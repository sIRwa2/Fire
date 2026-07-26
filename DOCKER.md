# Running firehome in Docker (LAN only)

```sh
docker compose up -d --build
```

Then open `http://<host-lan-ip>:8080/`.

Two containers: `firehome` (nginx, serves the page and the icons) and `api` (the
config store). Only `firehome` publishes a port; `api` is reachable solely
through `/api/` on the compose network.

Override the bind address or port without editing the compose file:

```sh
# only this machine
FIREHOME_BIND=127.0.0.1 docker compose up -d
# pin to one LAN interface, on port 80
FIREHOME_BIND=192.168.1.50 FIREHOME_PORT=80 docker compose up -d
```

## Server-side config

Config lives in the `firehome-data` volume as `/data/config.json`, so it follows
you to any browser on the LAN instead of living in one browser profile.

The bundled app reads and writes its state synchronously through
`localStorage`, so rather than rewriting it, `storage-shim.js` intercepts the
two keys it uses (`fireHome.v1`, `fireHome.theme`) and backs them with the
config service. `localize.py` injects the shim at image-build time.

```
GET    /api/config   -> {"rev": N, "updated": "<iso>", "data": {...}}
PUT    /api/config   <- {"data": {...}}   merge; a null value drops that key
DELETE /api/config   wipe
```

Worth knowing:

- **Last write wins.** Two browsers editing at the same time aren't
  coordinated; the second save overwrites the first.
- **Every save snapshots the previous document** into `/data/history/`, keeping
  the last 20 (`FIREHOME_HISTORY_KEEP`). That covers an accidental reset or a
  clobbered edit.
- **If the config service is unreachable when the page loads**, a warning banner
  appears and edits fall back to that browser's own `localStorage`. They are
  *not* pushed later — the server copy wins on the next successful load.
- **A corrupt `config.json` returns 500 rather than looking empty**, so it
  presents as a visible error instead of silent data loss. Recover from
  `history/`.
- Anything configured before this change (in a browser's `localStorage`, or from
  opening `firehome.html` as a `file://` URL) does not migrate. Read it out with
  `JSON.parse(localStorage['fireHome.v1'])` in devtools on the old origin and
  PUT it if you want to keep it.

## Starting clean

`localize.py` rewrites the bundle's `defaultApps()` and `defaultGroups()` to
return `[]`. Without that, none of the resets below produce an empty dashboard:
they clear the stored config, the page falls back to the 12 `*.example.com` demo
apps and 4 bookmark groups, and `componentDidMount()` calls `saveLS()` on mount
— so the next page load persists the demo data again and undoes the reset.

Emptying the defaults changes only the fallback. Placeholders already written
into the stored config stay until it is wiped, so the first time you need both a
rebuild and a reset.

Three levels, smallest first:

```sh
# 1. apps and bookmarks only — the app's own "Reset to defaults" button.
#    Now empties rather than restoring demo data, and writes through to the
#    server so it applies everywhere.

# 2. everything including theme, from the browser console:
fireHomeStorage.reset()

# 3. everything, from the shell — snapshots to history/ first:
curl -X DELETE http://<host-lan-ip>:8080/api/config
```

Both "add" buttons are hover-revealed (`opacity: 0` until their grid is
hovered), which would leave a cleared dashboard looking like an empty page.
`EMPTY_STATE_CSS` handles their empty-state appearance: forced visible, dashed
outline cleared, the tinted square behind the `+` removed, and dropped
`EMPTY_ADD_BUTTON_OFFSET` (24px) below the heading. The outline goes via
`border-color: transparent` rather than `border: none` so the box keeps its exact
size.

All of it is scoped to `:only-child`, i.e. the empty dashboard. Once apps exist,
`.add-app` is a grid item sitting alongside real cards, where the dashed outline
reads as an "add slot" and a top margin would knock it out of alignment with its
row. Drop the `:only-child` qualifiers to make it unconditional. `sc-for` and
`sc-if` compile to React Fragments, which render no DOM node, so an empty list
leaves the button as the only child — that is what makes `:only-child` identify
the empty case exactly.

The add/edit dialog's URL field is reduced to a bare `URL` placeholder. The
dialog has no labels — every field relies on its placeholder — so removing the
attribute outright would leave an unlabelled box.

To also discard the history snapshots and start from bare metal:

```sh
docker compose down -v && docker compose up -d --build
```

Useful from the console: `fireHomeStorage.dump()` shows what would be saved,
`fireHomeStorage.degraded()` reports whether the server is reachable.

## Verifying a deployment

```sh
./verify.sh                             # against http://localhost:8080
BASE=http://192.168.0.108:8080 ./verify.sh
```

Checks container and health state, `nginx -t`, that the shim and icon set made
it into the image, every served route, and a config write that round-trips
through the proxy and survives an `api` restart. Non-destructive: the write uses
a scratch key and removes it afterwards. Docker-dependent checks are skipped
when `docker` isn't on `PATH`.

Two things it can't check, because they need eyes:

- that the dashboard renders and its icons appear
- that config follows you to a second browser or device

For those: open the page, confirm no amber banner, change something, reload,
then load the same URL on your phone.

## Self-containment

The exported bundle already inlines React, the dc-runtime, the Inter fonts and
24 icons. `localize.py` removes what was left: the `cdn.jsdelivr.net` icon
fallback (now `/icons/`, served from the local `svg/` set — all 3222 icons the
picker offers are present) and two Google Fonts `preconnect` hints. The build
fails if either string moves, so a re-export can't silently reintroduce a CDN
dependency. `nginx.conf` also sets a CSP with no allowlisted hosts, and limits
clients to RFC1918 / loopback.

The config API is unauthenticated — anyone who can reach the page can read and
overwrite the dashboard config.

## Page margin

`localize.py` adds `width: 90%` and `box-sizing: border-box` to `.page-wrap`,
which reproduces Flame's container:

```css
.Container { width: 100%; padding: 20px; margin: 0 auto; }
@media (min-width: 769px)  { .Container { width: 90%; } }
@media (min-width: 1201px) { .Container { padding: 50px 250px; } }
```

Flame never declares a margin. `width: 90%` with `margin: 0 auto` makes the auto
margins split the leftover 10%, so each side is **5% of the viewport** — 75.2px
at a 1504px window, which is where a measured `75.203px` comes from. A fixed
`75px` would only match at that one width.

The exported template already had the `margin: 0 auto` and the `50px 250px`
padding but not the width, and with `width: auto` the auto margins resolve to 0
— hence no margin at all. `box-sizing` is needed because Flame gets border-box
from a global `*` reset this template has no equivalent of; under content-box,
`width: 90%` plus 500px of horizontal padding would overflow the viewport.

For a fixed margin instead, replace `PAGE_WRAP_CSS` in `localize.py` with
`.page-wrap{margin:75px auto!important}` — keep the `auto`, or the block stops
centring.

`HEADER_CSS` in the same file drops the header 50px via
`.page-wrap > header{margin-top:50px}`. The header is the wrapper's first child
and the wrapper has `padding-top: 50px`, which is what stops that margin
collapsing through the parent — so the header lands 100px from the top. Since
the header is in normal flow, the content below moves down with it.

## Icon picker

Picking an icon suggests a Title. The names are kebab-case slugs, so
`home-assistant` suggests `Home Assistant`, and a trailing `-light`/`-dark`
variant suffix is dropped so `vim-dark` suggests `Vim`.

It stays a suggestion. A title you typed is never overwritten; `titleAuto` tracks
whether the current title came from a previous pick, so trying different icons
keeps updating it, and editing the field by hand ends that.

The grid falls back to filtering by the title when the search box is empty. Left
alone, an autofilled title would make the grid collapse to icons matching the
name at the moment of the click, which reads as a glitch — so the fallback is
skipped for a title that was itself autofilled. Typing a title first still
pre-filters the grid, and editing an existing app is unaffected.

The tiles have their background and border removed. **They are targeted by
`span[data-icon]`, not by the `.scpx` class you see in devtools.** That class is
generated by the runtime's `createPseudoSheet`:

```js
const cls = "scp" + (n++).toString(36);
```

so the suffix is just a base36 counter in first-render order and moves whenever
the markup changes — `.scpx` is not a stable hook. Those generated rules are also
rewritten to `!important`, so overriding them needs `!important` *and* higher
specificity: `span[data-icon]:hover` is (0,2,1) against `.scpx:hover` at (0,2,0).

Because `ic.bg`/`ic.border` were what marked the selected icon in the accent
colour, the grid no longer shows which icon is chosen. The 44px preview beside
the search box still does.

## Favicon

`tools/make-favicon.py` generates `favicon.svg`, `favicon.png`,
`apple-touch-icon.png` and `favicon.ico` from one set of path data, so the
vector and the rasters can't drift. Stdlib only — it flattens the beziers and
rasterizes them itself, no Pillow or cairo needed.

```sh
python3 tools/make-favicon.py   # after editing the design constants
```

Colours and geometry are constants at the top of that file. The outputs are
committed, so the Docker build doesn't run the generator.

`localize.py` injects the `<link>` tags and a `<title>`, neither of which the
exported template carries — without them the tab shows a blank icon and the
bare URL, and the browser probes `/favicon.ico` on every load.

## A note on the console

You may see requests for `%7B%7B%20app.iconSrc%20%7D%7D` and similar. Those are
`{{ app.iconSrc }}`: the dc-runtime's markup sits in the document as real HTML,
so the browser eagerly fetches placeholder `src` attributes while parsing,
before the runtime replaces that subtree with the rendered output. Harmless —
the icons you actually see are the post-render ones. `nginx.conf` answers these
with a 1x1 transparent GIF so they stop showing up as 404s.

## Updating the page

Re-export `firehome.html`, then:

```sh
./tests/run.sh                  # needs python3 + node; no Docker required
docker compose up -d --build
./verify.sh
```

`tests/run.sh` checks what a re-export can quietly break: that `localize.py`
still finds its patch targets, that it runs with only the files the Dockerfile
copies, and that the shim still satisfies the contract the dashboard relies on.
`index.html` is served with `Cache-Control: no-cache`, so a reload picks it up;
icons are cached for a year since they're addressed by name.
