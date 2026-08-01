# firehome

A self-hosted homepage for a home network: your apps and bookmarks, a web search
bar, and config stored server-side so it follows you to any browser. The design
follows [Flame](https://github.com/pawelmalak/flame).

Everything is served from one container on your LAN. It reaches the internet only
when you ask it to — see [Risks and trade-offs](#risks-and-trade-offs).

## Icons: what to expect

**You get 3,233 icons in the box.** They install with the app, work offline, and
cover most self-hosted services. If that is fine, skip this section — there is
nothing to do.

Two more sets can be downloaded later, from the page itself:

| set | what it is | icons |
| --- | --- | --- |
| **Homarr dashboard-icons** | already installed, ships with the app | 3,233 |
| **selfhst** | more service logos, more up to date | 7,528 |
| **Material Design Icons** | plain UI symbols, not logos | 7,447 |

What you can do, in **Settings → Icons**:

- **Nothing.** The shipped set is complete and needs no network.
- **Add a set.** Click **Update** on its row. selfhst and MDI are ~7,500 files
  each, so give them a few minutes; the row shows progress.
- **Top up the shipped set.** **Update** on the homarr row adds ~780 more, the
  ones that exist only as webp or png upstream, taking it to 4,009.
- **Keep several.** Sets stack, they do not replace each other, so you end up with
  every icon from all of them.
- **Pick which one wins.** Two sets often have an icon of the same name. **↑**
  moves a set up the list; the top one wins.
- **Turn one off.** It vanishes from icon search. Apps already using its icons
  keep working.

Icons are only ever fetched on that click. Nothing is downloaded while you
install, or on a normal page load.

## Install

You need Docker with the Compose plugin, and nothing else.

```sh
git clone <this-repo> firehome
cd firehome
docker compose up -d --build
```

Open **`http://<host-ip>:8080/`**. That's it — the dashboard starts empty, and you
add apps and bookmarks from the page itself.

To listen somewhere other than every interface on port 8080:

```sh
FIREHOME_BIND=127.0.0.1 docker compose up -d --build            # this host only
FIREHOME_BIND=192.168.1.50 FIREHOME_PORT=80 docker compose up -d --build
```

<details>
<summary>What that command actually did</summary>

Built two images, created the `firehome-data` volume for your config, and started
the stack. The web container waits for the config store to report healthy before
nginx accepts requests, so the first page load is never a race.

`--build` is not optional, here or when updating. The storage shim, layout CSS,
search bar and favicon links are injected into the page at image-build time, so a
plain `docker compose up -d` would restart the old image without them.

</details>

## Update

```sh
git pull && docker compose up -d --build
```

Your apps, bookmarks and settings live in a Docker volume, not in the image, so
they survive rebuilds untouched.

## Uninstall

```sh
docker compose down          # stop, keep your config
docker compose down -v       # stop and delete config, history and icon updates
```

## Checking it works

Optional. `./verify.sh` covers the common case — containers up, page served,
config store readable and writable:

```sh
./verify.sh
BASE=http://192.168.0.108:8080 ./verify.sh    # from another machine
```

It is non-destructive: its config write uses a scratch key and removes it
afterwards. For the full test suite, see [Testing](#testing).

## Starting over

A fresh install is already empty — the upstream bundle's 12 `*.example.com` demo
apps and 4 bookmark groups are stripped at build time. To wipe a dashboard you
have been using:

| | |
| --- | --- |
| **Settings → Reset apps & bookmarks** | Empties the dashboard on every device. |
| `curl -X DELETE http://<host-ip>:8080/api/config` | Same, from a shell. Snapshots to `/data/history/` first. |
| `docker compose down -v` | Deletes the volumes outright, history and icon updates included. |

See [DOCKER.md](DOCKER.md#the-two-add-buttons) for the details of how the empty
state is drawn.

## Icon libraries

The short version is above, under [Icons: what to expect](#icons-what-to-expect).
The rest is why they stack rather than replace each other.

**The sets overlap, but not much.** Of selfhst's 7,528 names only 1,565 also exist
in homarr's 4,009 — 2,444 icons are homarr-only and 5,963 selfhst-only, 9,972
distinct names between them. Choosing one would throw thousands of icons away, so
all three can be installed at once and the order decides who wins a shared name.
Licences: homarr Apache-2.0, selfhst CC-BY-4.0, MDI Apache-2.0.

**690 icons exist only as webp or png** upstream and used to be unreachable —
searching for `adblock` or `9anime` finds them now.

Downloads land in a volume that sits in front of the set baked into the image, so
`docker compose down -v` reverts to the shipped icons and an update never touches
the image. Each set tracks its own default branch and only runs when you click.
Turning a set off hides it from icon search but keeps serving what it already
provides, which is why apps using its icons do not break.

See [DOCKER.md](DOCKER.md#icon-updates) for how nginx resolves a name it cannot
reason about.

## Settings

The settings panel (the gear in the header) holds the theme, font and colour
options that shipped with the page, plus:

| | |
| --- | --- |
| **Search engine** | DuckDuckGo, Google, Bing, Startpage, or a URL of your own — SearXNG, Whoogle, anything with a query parameter. `%s` marks where the query goes. |
| **Search suggestions** | Off by default. On, each keystroke is sent to the engine through this server. See [Risks](#third-party-content). |
| **Open links in a new tab** | On by default. Applies to both apps and bookmarks. |
| **Mobile layout** | Icon and text, icons only, or text only — on screens under 768px. |
| **Keyboard shortcuts** | Listed in the panel: `L` lock, `A` add application, `S` settings, `Space` focus the search bar. Ignored while typing. |
| **Font sizes** | Five controls: section headings, application name and description, bookmark category and name. Everything except the date and greeting. |
| **Show hover controls when locked** | Off by default, so locking hides the add/edit/delete affordances. See [DOCKER.md](DOCKER.md#what-the-lock-actually-locks). |
| *(the lock itself)* | The padlock in the header is remembered, so it survives a reload and applies on every browser. |
| *(automatic)* | Saving an app or bookmark without picking an icon fetches the site's own, falling back to a coloured tile with its initial. See [DOCKER.md](DOCKER.md#falling-back-to-the-sites-own-icon). |
| **Icons** | One row per icon library: update it, turn it off, or move it up so it wins a shared name. See [DOCKER.md](DOCKER.md#icon-updates). |

These are stored server-side with the rest of your config, so they follow you to
any browser.

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
- **SVGs are active content** (the rasters are not). They render through `<img>`,
  which never executes script, and `/icons/` is served under its own
  `default-src 'none'; sandbox` policy. A scripted icon still would not run, but
  the reason it cannot is a header, not the file being harmless.
- **The site-icon fetcher will request any URL you give it.** Saving an entry
  without an icon makes the container fetch that address server-side, and it
  cannot refuse private ranges — pointing at LAN services is the whole point. On
  an unauthenticated endpoint that is a usable internal probe: a caller learns
  whether a host answers and serves an image. It is bounded rather than blocked —
  http(s) only, 6s timeouts, 256 KB of HTML, 512 KB per icon, at most six
  candidates, and bytes are stored only if they parse as an image.
- **Search suggestions send every keystroke to the engine.** Off by default, and
  the only feature here that talks to a third party while you use the dashboard
  rather than while you configure it. With it on, each debounced keystroke goes
  to `/api/suggest`, which fetches DuckDuckGo, Google or Bing server-side — so
  the engine sees your server's address rather than your browser's, but it does
  see what you type, before you press Enter. The proxy takes an engine *key*,
  not a URL, so unlike the icon fetcher it cannot be pointed at an arbitrary
  host; queries are capped at 200 characters and responses at 64 KB. Self-hosted
  engines get no toggle, since a SearXNG instance needs its own JSON API and
  answers in a different shape.
- **Update runs are unauthenticated too**, so anyone on the LAN can make the
  container fetch from GitHub — and the first run pulls ~690 raster files, around
  28 MB. Concurrent runs are refused, downloads are capped at 1 MB per file and
  6000 files, and nothing outside a flat `[A-Za-z0-9._-]+\.(svg|webp|png)` shape
  is ever written.

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

## Testing

Not needed to run the dashboard. `./verify.sh` (above) is the one worth knowing.

```sh
./tests/run.sh          # build-time + api checks; python3 + node, no Docker needed
```

The rest drive a real browser and need playwright:

```sh
npm install playwright && npx playwright install --with-deps webkit chromium
node tests/render.mjs http://192.168.0.108:8080
```

`render.mjs` is the broad one — it confirms the page renders in WebKit and
Chromium, and covers layout, the search bar, the engine picker, suggestions and
the keyboard shortcuts. It stubs the config per-page, so the deployed dashboard is
neither read nor written. The others each isolate one behaviour:

| | |
| --- | --- |
| `lock-state.mjs`, `lock-persist.mjs` | What the lock hides, and that it survives a reload. |
| `theme-select.mjs`, `theme-persist.mjs`, `font-sizes.mjs` | The theme dropdown, the colour pickers, each size control. |
| `mobile.mjs`, `header-swipe.mjs` | The three mobile layouts, and swipe-to-reveal on a phone. |
| `cards.mjs`, `url-title.mjs` | App card padding and alignment; URL → Title in the real dialog. |
| `icon-libraries.mjs` | The Icons rows: update, promote and enable each reach the api with the right library. |
| `modal-backdrop.mjs` | The add-entry dialog closes on a backdrop click, but not on a drag that crosses its edge. |

Worth doing by hand either way: load the same URL on a second device and check
your config follows you.

## What's here

| Path | |
| --- | --- |
| `firehome.html` | The exported page bundle. Source of truth; re-export overwrites it. |
| `localize.py` | Build-time patches: removes the CDN/font dependencies, injects the storage shim, favicon links, title and layout CSS, empties the demo content. Fails loudly if a re-export moves what it patches. |
| `extract.py` | Unpacks the self-extracting bundle into plain HTML + `assets/`, so the browser never runs the loader. |
| `storage-shim.js` | Redirects the page's `localStorage` config to the config service. |
| `api/server.py` | The config store. Stdlib only. |
| `api/icons.py` | Icon libraries and their updates, diffed by git blob sha. |
| `nginx.conf`, `Dockerfile`, `docker-compose.yml` | Serving and orchestration. |
| `favicon.svg` | The site icon, and the source of truth for it. Replace it and re-run `tools/make-favicon.mjs` to regenerate the raster fallbacks. |
| `api/suggest.py` | Search-suggestion proxy. Fetches the engine server-side; off unless enabled. |
| `svg/` | The homarr-labs icon set, served at `/icons/` so nothing loads from a CDN. The other libraries are downloaded into a volume, not committed here. |
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

## Credits and licence

firehome is MIT licensed — see [LICENSE](LICENSE).

- The page design follows [Flame](https://github.com/pawelmalak/flame) by Paweł
  Malak, MIT licensed.
- The shipped icons are the [homarr-labs dashboard-icons](https://github.com/homarr-labs/dashboard-icons)
  set, Apache-2.0. The sets you can add from the settings panel are
  [selfhst/icons](https://github.com/selfhst/icons) (CC-BY-4.0) and
  [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache-2.0).
  The logos they depict remain the property of their owners and are included for
  identification only.
