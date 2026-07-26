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

Three levels, smallest first:

```sh
# 1. apps and bookmarks only — the app's own "Reset to defaults" button.
#    Now writes through to the server, so it applies everywhere.

# 2. everything including theme, from the browser console:
fireHomeStorage.reset()

# 3. everything, from the shell — snapshots to history/ first:
curl -X DELETE http://<host-lan-ip>:8080/api/config
```

To also discard the history snapshots and start from bare metal:

```sh
docker compose down -v && docker compose up -d --build
```

Useful from the console: `fireHomeStorage.dump()` shows what would be saved,
`fireHomeStorage.degraded()` reports whether the server is reachable.

## What "local only" means here

Three independent layers:

1. **No outbound requests.** The exported bundle already inlines React, the
   dc-runtime, the Inter fonts and 24 icons. `localize.py` removes what was
   left: the `cdn.jsdelivr.net` icon fallback (now `/icons/`, served from the
   local `svg/` set — all 3222 icons the picker offers are present) and two
   Google Fonts `preconnect` hints. The build fails if either string moves, so
   a future re-export cannot silently reintroduce a CDN dependency.
2. **A Content-Security-Policy with no allowlisted hosts** (`nginx.conf`), so
   the page cannot fetch from the internet regardless. Clicking a tile is a
   top-level navigation and still works normally.
3. **nginx rejects any client outside RFC1918 / loopback.** This is a backstop
   for the compose port binding — a router port-forward still won't expose it.

Note that the config API is unauthenticated: anyone who can reach the page can
read and overwrite the dashboard config. That matches a LAN homepage, but it is
the reason layer 3 exists.

## Updating the page

Re-export `firehome.html`, then:

```sh
./tests/run.sh                  # needs python3 + node; no Docker required
docker compose up -d --build
```

`tests/run.sh` checks that `localize.py` still finds its patch targets and that
the shim still satisfies the contract the dashboard relies on — the things a
re-export can quietly break. `index.html` is served with
`Cache-Control: no-cache`, so a reload picks it up; icons are cached for a year
since they're addressed by name.
