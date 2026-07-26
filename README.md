# firehome

A self-hosted homepage, served from a container with its config stored
server-side. The design follows [Flame](https://github.com/pawelmalak/flame).

## Update

After pulling changes on the docker box:

```sh
git pull && docker compose up -d --build && ./verify.sh
```

`--build` is not optional. The storage shim, favicon links and layout CSS are
injected into the page at image-build time, so a plain `docker compose up -d`
restarts the old image without them.

## First run

Same command. It builds both images, creates the `firehome-data` volume and
starts the stack; `firehome` waits for the config store to report healthy before
nginx comes up. Then open `http://<host-ip>:8080/`.

```sh
FIREHOME_BIND=127.0.0.1 docker compose up -d              # this host only
FIREHOME_BIND=192.168.1.50 FIREHOME_PORT=80 docker compose up -d
```

## Checking it works

```sh
./verify.sh                              # container + http + config-store checks
BASE=http://192.168.0.108:8080 ./verify.sh
./tests/run.sh                           # build-time checks; python3 + node, no Docker
node tests/render.mjs http://192.168.0.108:8080   # real WebKit + Chromium; needs playwright
```

`verify.sh` is non-destructive — its config write uses a scratch key and removes
it afterwards. `tests/render.mjs` is the one that confirms the page actually
*renders*, in Safari's own engine; it stubs the config per-page so the deployed
dashboard is neither read nor written. It needs playwright:

```sh
npm install playwright && npx playwright install --with-deps webkit chromium
```

Still worth doing by hand: load the same URL on a second device and check your
config follows you.

## Starting clean

A clean dashboard is genuinely empty. `localize.py` rewrites the bundle's
`defaultApps()` and `defaultGroups()` to return `[]`, dropping the 12
`*.example.com` demo apps and 4 bookmark groups it shipped with.

```sh
fireHomeStorage.reset()                              # browser console, incl. theme
curl -X DELETE http://<host-ip>:8080/api/config      # shell; snapshots first
docker compose down -v && docker compose up -d --build   # also drops history
```

**Rebuilding is not enough on its own.** Emptying the defaults only changes what
the page falls back to; placeholders already written into the stored config stay
until you wipe it. So the first time, rebuild *and* reset:

```sh
docker compose up -d --build && curl -X DELETE http://<host-ip>:8080/api/config
```

Then reload. The dashboard's own "Reset to defaults" button now also empties
rather than restoring demo data, and writes through to the server so it applies
on every device.

The "Add application" and "New category" buttons have no dashed outline and no
tinted square behind the `+`, in every state. Only their visibility and the
`EMPTY_ADD_BUTTON_OFFSET` (24px) drop are scoped to an empty grid — once apps
exist, `.add-app` shares a row with real cards and a top margin would break the
alignment.

They're otherwise hover-revealed, and that reveal is made robust against old
Safari: the page pairs `:hover` with `:focus-visible` in one selector list, which
Safari below 15.4 discards whole. See [DOCKER.md](DOCKER.md#the-two-add-buttons).

The add/edit dialog's URL field keeps a bare `URL` placeholder. The dialog has no
labels — every field relies on its placeholder — so dropping the attribute
outright would leave an unlabelled box.

## Updating the icon set

**Settings → Icons → Check for updates** pulls new and changed icons from
[homarr-labs/dashboard-icons](https://github.com/homarr-labs/dashboard-icons),
including the **690 icons that exist only as webp or png** upstream and so were
previously unreachable — searching for `adblock` or `9anime` now finds them.
Tracks `main`, runs only when you click, and lands in a volume that sits in front
of the set baked into the image — so `docker compose down -v` reverts to the
shipped icons. This is the only thing in the stack that reaches the internet, and
only on that click. See [DOCKER.md](DOCKER.md#icon-updates).

## Risks and trade-offs

Everything here assumes a trusted LAN and a dashboard whose contents are not
precious. Worth knowing before you rely on it, roughly in the order it could
actually bite you.

### Losing config

- **Nothing is authenticated.** Anyone who can reach the page can read, rewrite
  or wipe your dashboard, and can trigger icon downloads. `DELETE /api/config` is
  one unauthenticated request. There is no login, no token, no CSRF protection —
  a page on another site could POST to your dashboard if it knows the address.
  The nginx RFC1918 allowlist limits *where* from, which is not the same as who.
- **A stale browser tab can overwrite newer config.** Last write wins, with no
  version check: a tab left open since yesterday will happily save over today's
  edits made elsewhere. Both are recoverable from `/data/history/`, which keeps
  the last 20 documents (`FIREHOME_HISTORY_KEEP`) — but you have to notice.
- **Degraded mode loses edits silently-ish.** If the config service is
  unreachable when the page loads, an amber banner appears and edits go to that
  browser's own `localStorage`. They are *not* pushed later; the server copy wins
  on the next successful load. The banner is the only warning.
- **The config volume is the only copy.** No backup, and `docker compose down -v`
  destroys it along with the history. Copy `/data/config.json` somewhere if your
  layout took real effort.

### Third-party content

- **The icon updater tracks `main`, so upstream changes land without review.**
  Icons come from a repository you don't control; a compromised or careless
  upstream commit would be fetched on your next click. Transport is HTTPS with
  certificate validation and each file is checksum-verified against the sha the
  tree API reported — but that only proves you got what GitHub served, not that
  it is benign. Pinning a commit instead is a small change to `api/icons.py` if
  you later want a review step.
- **SVGs are active content.** They render through `<img>`, which never executes
  script, and `/icons/` is served under its own `default-src 'none'; sandbox`
  policy. A scripted icon still would not run, but the reason it cannot is a
  header, not the file being harmless.
- **Update runs are unauthenticated too**, so anyone on the LAN can make the
  container fetch from GitHub. Concurrent runs are refused, downloads are capped
  at 512 KB per file and 6000 files, and nothing outside a flat
  `[A-Za-z0-9._-]+\.svg` shape is ever written.

### The page itself

- **The CSP is weaker than it looks.** `script-src` needs both `'unsafe-inline'`
  and `'unsafe-eval'` because the dc-runtime evaluates the page's logic through
  `new Function()`. No external host is allowlisted, so the page cannot phone
  home, but inline-script protection is effectively off — meaning any injected
  markup would execute. The only thing that writes to the page is your own
  config, which is exactly what an unauthenticated API accepts from anyone.
- **Keyboard focus indication is gone on three buttons** — the two "add" buttons
  and the header pair. Removing the focus ring was deliberate and is called out
  in `localize.py`, with the accessible variant recorded there.

### Maintenance

- **The build patches the bundle by matching exact strings.** Re-exporting
  `firehome.html` can move any of them. Every patch asserts its hit count, so the
  build fails loudly rather than silently dropping a change — run
  `./tests/run.sh` after a re-export and fix what it reports.
- **The api container bind-mounts `./svg` from the repo**, so it expects to be
  started from a checkout. Without it the updater sees no baked icons and would
  try to fetch all 3232.

The api container runs unprivileged with `cap_drop: ALL`, `no-new-privileges`, a
read-only root filesystem and no published port — reachable only through nginx.
That limits what a bug in it can reach; it does not make the endpoints safe to
expose. Don't port-forward this.

## What's here

| Path | |
| --- | --- |
| `firehome.html` | The exported page bundle. Source of truth; re-export overwrites it. |
| `localize.py` | Build-time patches: removes the CDN/font dependencies, injects the storage shim, favicon links, title and layout CSS, empties the demo content. Fails loudly if a re-export moves what it patches. |
| `extract.py` | Unpacks the self-extracting bundle into plain HTML + `assets/`, so the browser never runs the loader. |
| `storage-shim.js` | Redirects the page's `localStorage` config to the config service. |
| `api/server.py` | The config store. Stdlib only. |
| `api/icons.py` | Icon updates from the upstream repo, diffed by git blob sha. |
| `nginx.conf`, `Dockerfile`, `docker-compose.yml` | Serving and orchestration. |
| `tools/make-favicon.py` | Generates the favicon set from one set of path data. |
| `svg/` | The homarr-labs icon set, served at `/icons/` so nothing loads from a CDN. |
| `iterm/` | iTerm colour schemes; the pool the bundle's 18 built-in themes were drawn from. Not used at runtime. |
| `verify.sh`, `tests/` | Post-deploy and build-time checks. |

## After re-exporting firehome.html

```sh
./tests/run.sh && docker compose up -d --build && ./verify.sh
```

`tests/run.sh` covers what a re-export can quietly break: that `localize.py`
still finds its patch targets, that it runs with only the files the Dockerfile
copies, and that the storage shim still satisfies the contract the page relies
on.

See [DOCKER.md](DOCKER.md) for how the storage, layout, favicon and
self-containment pieces actually work.
