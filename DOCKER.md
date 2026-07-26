# Running firehome in Docker (LAN only)

```sh
docker compose up -d --build
```

Then open `http://<host-lan-ip>:8080/`.

Override the bind address or port without editing the compose file:

```sh
# only this machine
FIREHOME_BIND=127.0.0.1 docker compose up -d
# pin to one LAN interface, on port 80
FIREHOME_BIND=192.168.1.50 FIREHOME_PORT=80 docker compose up -d
```

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

Nothing is written to disk by the app, so there are no volumes: your dashboard
config lives in the browser's `localStorage`.

## Consequences of localStorage config

- Config is per-origin. Anything you configured while opening `firehome.html`
  as a `file://` URL does not carry over, and `http://192.168.1.50:8080` and
  `http://fire.lan:8080` are two separate configs. Pick one URL and stick to it.
- Config is per-browser and per-device, and is not backed up by the container.
  There is no export/import UI, so moving it means copying the `fireHome.v1`
  and `fireHome.theme` `localStorage` keys via the browser devtools console.

## Updating the page

Re-export `firehome.html`, then `docker compose up -d --build`. `index.html` is
served with `Cache-Control: no-cache`, so a reload picks it up; icons are cached
for a year since they're addressed by name.
