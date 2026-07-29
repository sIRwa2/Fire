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

### The two "add" buttons

`ADD_BUTTON_CSS` clears the tinted square behind the `+` and **both** things that
draw an edge, unconditionally:

- **`border`** — the dashed 1px accent outline in the inline style. Cleared with
  `border-color: transparent` rather than `border: none`, so the box keeps its
  exact size.
- **`outline`** — the browser's focus ring. A different property, which the page
  only resets for `input`, so these buttons kept theirs. This is why an outline
  survived the border fix: clicking a button focuses it, and it stays focused
  behind the dialog and after it closes.

Suppressing the ring for pointer focus only
(`:focus:not(:focus-visible)`) is the accessible version and is not enough here —
typing in the dialog puts the browser in keyboard modality, so when focus returns
to the button it matches `:focus-visible` and the ring reappears. It is removed
outright instead, which costs keyboard users their focus indicator on these two
buttons; the page already makes the same trade for every `input`. The accessible
variant is in the comment above `ADD_BUTTON_CSS`.

Scoping this to `:only-child` was the original mistake: it meant the styling
reverted the moment an app was added.

`EMPTY_STATE_CSS` keeps only the visibility and the `EMPTY_ADD_BUTTON_OFFSET`
(24px) drop scoped to the empty dashboard: once apps exist, `.add-app` shares a
grid row with real cards and a top margin would knock it out of alignment.
`sc-for` and `sc-if` compile to React Fragments, which render no DOM node, so an
empty list leaves the button as the only child — that is what makes `:only-child`
identify the empty case exactly.

### The header lock and settings buttons

`HDR_BUTTON_CSS` clears their border and focus ring via `.hdr-actions button`,
which holds exactly those two — the `sc-if` pair inside the lock button only swaps
the padlock glyph. Targeted that way rather than by the `.scp0` / `.scp1` classes
devtools shows, which are generated in render order and move.

The lock button's border was one of its locked-state signals
(`lockBorder: s.locked ? 'var(--accent)' : 'rgba(255,255,255,.1)'`) but far from
the only one: when locked, `lockBg` becomes `var(--accent)` and `lockColor` flips
to a dark glyph, the icon swaps to a closed padlock, and the tooltip changes. A
solid accent plate reads more clearly than the 1px border did.

Their backgrounds are set to `transparent` rather than to a copy of the page
colour, so they show whatever is behind them and keep following it when the theme
changes. There is no `--bg` custom property to reference — `applyTheme()` assigns
`document.body.style.background` directly and only publishes `--accent`.

The locked state is the exception, and it is the one place the background is load
bearing: with the border gone, `lockBg`'s accent plate is the whole signal. It is
restored explicitly for the locked button, keyed on the title, since locked and
unlocked are otherwise identical in the markup. Because that restores
`var(--accent)` rather than a literal colour, it tracks the theme too — accent
green by default, amber under Ayu. `localize.py` asserts the title still starts
with `Layout locked`, so a re-export that rewords it fails the build instead of
quietly flattening the indicator.

The settings button's hover tint survives on purpose: its generated
`.scpN:hover` rule is (0,2,0) against this rule's (0,1,1), so it still wins and
the button keeps showing that it is interactive.

`HOVER_FALLBACK_CSS` makes the reveal survive an old browser. The page writes

```css
.app-grid:hover .add-app,.add-app:focus-visible{opacity:1}
```

and a selector list is discarded *whole* if any part fails to parse. Safari only
learned `:focus-visible` in 15.4, so on anything older both of these rules are
dropped and the two buttons can never be revealed — while `.hdr-actions` and
`.cat-del`, whose rules use no `:focus-visible`, keep working. Restating the hover
half on its own makes the reveal independent of that support, and an
`@supports not selector(:focus-visible)` block stops relying on hover at all
where it is genuinely missing.

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

`tests/render.mjs` covers what `verify.sh` cannot: whether the page actually
*looks* right. It drives the deployed URL in real WebKit — the engine Safari uses
— and in Chromium, asserting the computed layout, that every `<style>` attached,
that the add buttons have no outline in both the empty and populated states, that
hover reveals them, and that nothing is requested off-host. Optional, since it
needs a few hundred MB of browsers:

```sh
npm install playwright
npx playwright install --with-deps webkit chromium
node tests/render.mjs http://192.168.0.108:8080
```

It is non-destructive: the config is stubbed per-page rather than written, so the
deployed dashboard is neither read nor modified. Seeding `localStorage` would not
work — with the service reachable the shim treats the server as authoritative —
and the shim's first read is a synchronous XHR, which WebKit does not put through
route interception, so that one call is stubbed in-page.

Still needs eyes: that config follows you to a second physical device.

## Self-containment

The exported bundle already inlines React, the dc-runtime, the Inter fonts and
24 icons. `localize.py` removes what was left: the `cdn.jsdelivr.net` icon
fallback (now `/icons/`, served from the local `svg/` set — all 3222 icons the
picker offers are present) and two Google Fonts `preconnect` hints. The build
fails if either string moves, so a re-export can't silently reintroduce a CDN
dependency. `nginx.conf` also sets a CSP with no allowlisted hosts, and limits
clients to RFC1918 / loopback.

The config API is unauthenticated — anyone who can reach the page can read and
overwrite the dashboard config. See
[Risks and trade-offs](README.md#risks-and-trade-offs) in the README for the full
list, including what the CSP does and does not buy you, and how config can be
lost.

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

## Touch devices

Every control for editing the dashboard is hover-revealed — four `:hover` rules
in the page's own stylesheet (`.add-app`, `.hdr-actions`, `.new-cat`, `.cat-del`)
plus two elements carrying an inline `opacity: 0` and a `style-hover` the runtime
turns into a generated `.scpN:hover` rule (the app card's edit/delete overlay and
the bookmark delete).

A touch device has no hover, so on an iPhone or iPad none of them could be
reached and the dashboard was effectively read-only. `TOUCH_CSS` forces them
visible under `@media (hover: none)`, which matches exactly those devices —
pointer machines keep the hover-reveal, and an iPad with a trackpad reports
`hover: hover` and is likewise unaffected. The trade-off on touch is that the
per-card edit/delete buttons are always visible, which is the only way to reach
them at all.

## Build-time extraction

`firehome.html` is a self-extracting bundle. Served as-is, it showed an
"Unpacking…" message on every load because the work was redone on every load:

| | |
| --- | --- |
| page on the wire | 791 KB |
| base64 manifest payload | 636 KB |
| decompressed to | 630 KB across 43 resources |
| — fonts | 16 woff2, 402 KB |
| — JavaScript | 3 files, 207 KB (React, react-dom, dc-runtime) |
| — bundled icons | 24 svg, 22 KB |
| template | 120 KB, with 43 uuid substitutions over it |

Each load base64-decoded and gunzipped all 43 resources, minted a blob URL for
each, string-replaced every uuid across the 120 KB template, parsed the result,
swapped `documentElement`, then re-created and re-ran every script. None of it
was cacheable, because the assets existed only as blob URLs minted inside that
page.

`extract.py` now does that work at build time, so the browser gets a plain
document: **128 KB of HTML plus 19 real asset files**, parsed once like any other
page, with the fonts and JavaScript cached for a year under `/assets/`. The 24
bundled icons are dropped entirely — `svg/` already serves all 3222 at `/icons/`.

The fonts turn out to be the biggest win. The default theme uses system fonts, so
the 402 KB of Inter woff2 is now fetched only if a theme actually asks for it,
where the bundle decoded all of it on every single load regardless.

Two things `extract.py` guards, because getting either wrong fails quietly:

- **The React entries in `window.__resources`.** The runtime resolves its own
  React and react-dom through that map and falls back to a CDN URL when an entry
  is missing:

  ```js
  function cdnScriptFor(url, sri) {
    const v = window.__resources ? window.__resources[url] : void 0;
    return typeof v === "string" && v ? { src: v } : { src: url, integrity: sri };
  }
  ```

  so dropping them would make the page inject a `<script>` pointing at
  unpkg.com.
- **Unresolved uuids.** Any manifest uuid still in the output means a reference
  that was not rewritten, and the extraction aborts. Nested page bundles are
  refused outright rather than emitted with dead frames.

The runtime needs no help with ordering: it boots off
`document.readyState !== "loading" ? boot() : addEventListener("DOMContentLoaded", boot)`,
so it waits for `<x-dc>` to exist even though its script is in `<head>`.

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

## Pasting a URL into the add dialog

Opening **Add application** focuses the URL field, so pasting is a single
Cmd/Ctrl+V. Nothing was focused before, which meant clicking into a field first.
This needs no permission and works over plain HTTP.

Reading the clipboard automatically is attempted as well, but **it will not fire
on a LAN deployment**. `navigator.clipboard` is gated on a secure context, and
measured in both engines:

| origin | `isSecureContext` | `navigator.clipboard` |
| --- | --- | --- |
| `http://127.0.0.1:8080` | true | present; `readText()` throws `NotAllowedError` |
| `http://192.168.0.108:8080` | **false** | **undefined** |

So over `http://` it is inert, and it starts working by itself if the dashboard
ever sits behind HTTPS.

Even where the API exists, calling `readText()` raises a permission prompt in
Chrome and a paste confirmation in Safari — on *every* click of Add application,
which would be worse than pressing Cmd+V. So the permission is queried first and
the clipboard is only read when it has already been granted; the prompt is never
triggered. Anything that is not an `http(s)://` URL under 2048 characters is
ignored, and an existing URL is never overwritten.

## Bookmarks and apps are the same kind of thing

Items drag between the two lists, and the drop handler already treated them as
one shape: moving a bookmark into apps synthesises a `desc` from the URL host,
while moving an app into a group keeps its `desc` untouched. So a bookmark could
already carry a description — the dialog just hid the field, and there was no way
to edit a bookmark at all. Both were asymmetries in the UI, not in the data.

The bookmark dialog now offers the same fields as the application one, bookmarks
have the edit affordance apps have, and the clipboard/focus behaviour applies to
both. The heading still says which you are creating.

`saveModal` stores a bookmark's trimmed description as-is rather than copying the
app branch's `|| url` fallback. A bookmark card does not display a description, so
defaulting it to the URL would only surface later as a worse label after a drag
into apps — the drop handler's host fallback is the better one, and leaving it
empty keeps that reachable.

## Drag-sortable categories

Bookmark categories reorder by dragging the grip beside their name. The grip is
hover-revealed and only exists while the layout is unlocked, matching the delete
affordance and the lock that already gated dragging.

Making this work meant typing the drag, because a category is a draggable that
both *contains* draggables and *is* a container. Two collisions came out of that:

- `hitTest` takes the first `[data-container]` whose rect holds the pointer, in
  document order. `.bm-grid` encloses every category list, so dragging a bookmark
  would have resolved to `.bm-grid` and dropped into the wrong place.
- `.bm-grid`'s `[data-drag-id]` descendants are the categories *and* every
  bookmark nested inside them, so the insertion index would have been computed
  against a mixed list.

So items carry `data-drag-kind` and containers declare `data-accepts`, defaulting
to `item` — the app grid and the category lists need no markup change — and
`hitTest` filters both by the kind being dragged. A category can only land among
categories, a bookmark only among bookmarks.

The handle is separate from the moved element, which the system did not support:
`dragPointerDown` used `e.currentTarget` as both. Resolving the moved node with
`closest('[data-drag-id]')` leaves apps and bookmarks identical — `currentTarget`
is already the draggable, so `closest` returns itself — while letting the grip
move the whole category. That separation is necessary rather than cosmetic: the
category name is `contentEditable`, so without a dedicated handle, click-dragging
to select the text would drag the column instead.

Ordering persists through the existing `saveLS()` on drop, since group order is
just the array order.

## Opening links in a new tab or the same one

**Settings → Open links in a new tab.** On by default, and the choice lives in the
theme object, so the config service persists it and it follows you to any browser.

The page already decided this, but from an authoring prop:

```js
linkTarget: (this.props.openInNewTab ?? true) ? '_blank' : '_self'
```

`props` come from the `x-dc` script's `data-props`, so it was baked into the export
and not reachable by the dashboard's user. It now reads a `sameTab` flag on the
theme, defaulting from that prop so an existing dashboard behaves exactly as
before until the toggle is touched.

The default uses `??` rather than `||` deliberately: once you have chosen "same
tab off", `false` is a real stored value and `||` would have discarded it and
fallen back to the prop. Verified by toggling both ways — the server holds
`{"sameTab": true}` then `{"sameTab": false}`, and both survive a reload.

Apps and bookmarks share the same `linkTarget`, so they cannot disagree.

## Falling back to the site's own icon

Saving an app or bookmark without picking an icon fetches the site's own, and the
entry updates when it arrives — the save is never blocked on a slow or
unreachable host, which keeps `homepage` showing in the meantime.

**It has to be fetched server-side.** The page's CSP is
`img-src 'self' data: blob:`, so a cross-origin `<img>` is refused outright —
confirmed in Chromium, which logs the violation. Widening it to `img-src *` would
end the guarantee that the page makes no external requests and would tell every
host in your dashboard who is looking at it. Fetching once through the api and
serving from our own origin keeps the CSP unchanged, and means the icon still
renders when the target is down.

**Some sites simply have no icon.** Anything that declares nothing, serves an
empty `/favicon.ico`, and 404s the conventional paths — synthtopia.com does all
three — gets a generated tile instead: a rounded square in a colour derived from
the hostname, with the site's initial. So a lookup never comes back empty, and an
entry always ends up with something of its own rather than the shared generic
glyph. An unreachable host gets one too.

The colour is hashed from the hostname rather than actually random, so a site
keeps the same tile across re-fetches, and `www.` is stripped first so
`www.example.com` and `example.com` agree. It is an SVG — a few hundred bytes,
scales to whatever size the page asks for, and needs no image library. The
baseline is placed by hand rather than with `dominant-baseline`, which older
WebKit ignores when an SVG is rendered as an image.

The sniffer still refuses to store a 404 page as an icon — synthtopia's is a 47 KB
lump of HTML — which is what makes the tile the fallback rather than that.

**"Or better" is the interesting half.** `/favicon.ico` is usually 16px, so the
page's own `<link rel>` tags are read first and scored — `apple-touch-icon`
highest, then `rel=icon` ordered by its `sizes` attribute — with `/favicon.ico`
kept as the last resort. In practice that is the difference between a 16px `.ico`
and gitea.com's 20 KB PNG, or Hacker News's SVG.

A page is parsed even when it exceeds the 256 KB read cap, because `<link rel>`
lives in the `<head>` at the very start. Treating the cap as fatal threw away
icons that were already in hand: motogp.com is 541 KB of HTML with `</head>` at
byte 7036, so its 180px `apple-touch-icon` was discarded in favour of a
`/favicon.ico` that 404s. An *image* is never truncated — half a file is not an
image.

Results are cached in the `firehome-favicons` volume, named by site, so
re-running replaces an entry rather than accumulating copies. The page refers to
them as `fav:<name>`, which `iconUrl` maps to `/favicons/<name>` — that keeps
them out of the icon picker, which enumerates the icon directories.

The placeholder is only replaced if it is *still* the placeholder, so an icon
chosen while the fetch was in flight wins.

This is the second thing in the stack that reaches the network, and it will
request whatever URL you save. See the README's risks section.

**If icons never appear, it is almost certainly the volume.** `firehome-favicons`
mounts at `/data/favicons`, and a named volume mounted where the image has no
directory is created root-owned — which the unprivileged service cannot write to,
so every fetch fails with `EACCES`. The api image creates it now, but a volume
made before that fix stays root-owned and has to be recreated. It only holds
re-fetchable icons, so nothing is lost:

```sh
docker compose down
docker volume rm "$(basename "$PWD")_firehome-favicons"
docker compose up -d --build
```

`POST /api/favicon` returns 503 with that command when it cannot write, and the
page logs the reason to the browser console rather than failing silently.

This is the same mistake `/data/icons` made earlier, so `tests/run.sh` now checks
every volume mountpoint in the compose file against the `mkdir` list in the api
image, instead of relying on the two being kept in step by hand.

## Icon updates

The dashboard can pull new and changed icons from
[homarr-labs/dashboard-icons](https://github.com/homarr-labs/dashboard-icons)
from the settings panel: **Settings → Icons → Check for updates**. It tracks
`main`, and only ever runs when you click — there is no background polling, so a
normal page load still makes no outbound request.

**The picker's list was the real obstacle.** It ships baked into the page as
`__icons_data`, separately from the files, and the two already disagreed: 3222
names against 3232 files, so ten icons on disk (`claude-ai-dark`,
`aws-dynamodb`, …) could not be selected at all. Downloading more would not have
surfaced them either. The list now comes from `GET /api/icons`, which enumerates
the real directories, with the baked list kept as the fallback for when the
service is unreachable — which both fixes that mismatch and is what lets an
update appear without rebuilding the image.

**690 icons upstream have no svg at all.** The repo carries `svg/` (3232),
`png/` (3913) and `webp/` (3905), and 690 names — `adblock`, `9anime`, `alarmpi`,
`adsb` … — exist only as rasters. They were simply unreachable before. The updater
now takes the best available format per name in the order svg → webp → png, so an
icon with an svg is never also fetched as a raster, and webp is preferred over png
because it makes that svg-less set **28 MB instead of 263 MB** for every variant.

An svg is offered under its bare name (`plex`), so existing config keeps working
and `iconUrl` appends `.svg` as it always has. A raster keeps its extension
(`adblock.webp`), which `iconUrl` passes straight through — the extension has to
survive into the URL for the response to carry the right content type. The title
suggestion strips it, so picking `adblock.webp` still suggests "Adblock".

There was also a stray `assetgrid.png` sitting in `svg/` all along, invisible
because the old pattern only matched `.svg`. It is offered now too.

**The first run after this fetches ~690 files.** Downloads run 4 at a time with a
retrying backoff, which takes well under a minute; subsequent checks are the usual
no-op. The concurrency and the retries are related: an early 8-worker run lost 192
of 690 files to `Temporary failure in name resolution` — the threads outpaced the
DNS resolver, not any HTTP limit — so transient errors, timeouts, 429s and 5xxs
are now retried with a backoff. Because the diff is by content, a run that ends
with failures is fully repaired by clicking again: it re-attempts exactly what is
missing.

**The diff is exact and cheap.** GitHub's tree API returns every path with its
git blob sha in one request, and a blob sha is just
`sha1("blob <bytelen>\0" + bytes)`, so the same value can be computed locally and
compared without downloading anything. Only genuinely new or changed files are
fetched, each one checksum-verified against the sha the tree reported before it is
written. Checked against all 3232 upstream icons: the computed shas matched
GitHub's exactly.

Two directories are involved:

| | |
| --- | --- |
| `svg/` → `/srv/icons-baked` | read-only bind mount of the repo. Ships with the image, so a full icon set works with no network access ever. |
| `firehome-icons` volume | writable overlay. Updates land here; nginx serves it *in front of* the baked set. |

So an update never mutates the image, and `docker compose down -v` reverts to the
shipped set.

**Volume ownership is load bearing here.** The api runs as uid 10001, and a named
volume mounted where the image has no directory is created *root-owned* — which
that user cannot write to, so every download fails `EACCES` while the config store,
on the correctly-initialised `/data` volume, carries on working. The api image
therefore creates `/data/icons` explicitly so a fresh volume inherits app
ownership. A volume created before that fix stays root-owned and has to be
recreated; it only holds re-downloadable icons, so nothing is lost:

```sh
docker compose down
docker volume rm "$(basename "$PWD")_firehome-icons"
docker compose up -d --build
```

`GET /api/icons` reports `writable`, `verify.sh` checks it, the settings row says
so before you click, and an update refuses up front with the cause rather than
failing once per file.

**File modes matter for the same reason.** nginx's worker is uid 101 and this
service is 10001, so downloaded icons have to be world-readable. `tempfile.mkstemp`
creates 0600 by design and `os.replace` preserves it, which made every icon a 403
and a broken thumbnail even though the download had succeeded. Files are now
chmodded to 0644 before the rename, and anything left at 0600 by an earlier
version is repaired in place at startup — so an existing volume recovers on
restart without being recreated. If the bind mount is missing the updater degrades rather than
crashing — it just sees no baked files and would fetch everything.

```
GET  /api/icons          -> {"icons": [...], "count": N, "status": {...}}
POST /api/icons/update   start an update; 409 while one is already running
GET  /api/icons/status   phase while running, plus the last result
```

**"already up to date"** means the last run finished with 0 new and 0 changed:
every icon in upstream's `svg/` has a byte-identical local copy. Note what it does
not say — it is an absolute comparison against upstream right now, not a delta
since the previous check, and it ignores local-only files, since the diff only
walks upstream entries and the updater never deletes anything. The counts describe
what was actually written, so a run whose downloads all failed reports
`0 new, 0 updated, N failed` rather than claiming success; `attempted` in the
state file records what it set out to do.

Worth knowing:

- **This is the one thing that reaches the internet.** The api container talks to
  `api.github.com` and `raw.githubusercontent.com`, on your click only. The page
  itself still makes no external request, so the CSP is unchanged.
- **Third-party SVGs.** They render through `<img>`, which never executes script,
  but the page CSP needs `script-src 'unsafe-inline'` for the dc-runtime, so a
  scripted SVG would run if someone opened `/icons/foo.svg` directly. That
  location now has its own far stricter policy
  (`default-src 'none'; sandbox`), and nginx only serves filenames matching the
  same flat shape the updater will write.
- **Names are validated before they touch the filesystem**, so a path in the tree
  response cannot escape the overlay directory.
- **Rate limit.** Unauthenticated GitHub API is 60 requests/hour per IP and a
  check costs two, so it is not a practical concern. Set
  `FIREHOME_GITHUB_TOKEN` for headroom if you want.

## Favicon

`favicon.svg` is the source of truth and is what modern browsers actually use.
`tools/make-favicon.mjs` rasterises the fallbacks from it — `favicon.png` (32),
`apple-touch-icon.png` (180) and `favicon.ico` (16/32/48) for clients that probe
`/favicon.ico` blindly:

```sh
node tools/make-favicon.mjs      # after replacing favicon.svg
```

All four are committed, so the Docker build never runs it. To change the icon,
replace `favicon.svg` and re-run.

It renders with a headless browser, which is already a dev dependency for
`tests/render.mjs`. That replaced a hand-rolled stdlib rasteriser, and the reason
is worth recording: that one drew geometry defined in its own source, where
even-odd filling was safe because it controlled the shapes. The artwork is now an
SVG with inner subpaths for the windows, and SVG's default fill rule is *nonzero*
— getting that subtly wrong would have silently filled the holes rather than
failing. A browser is the reference implementation.

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
