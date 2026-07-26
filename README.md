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
```

`verify.sh` is non-destructive — its config write uses a scratch key and removes
it afterwards. Neither script can confirm the page *renders*: for that, open it,
check no amber storage banner appears, change something, reload, and load the
same URL on a second device.

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

The "Add application" and "New category" buttons are hover-revealed, which would
leave an empty dashboard looking blank, so they're forced visible while their
grid is empty.

One placeholder is left deliberately: the add/edit dialog's URL field keeps its
`URL — e.g. https://plex.example.com` format hint, which is a form hint rather
than dashboard content.

## What's here

| Path | |
| --- | --- |
| `firehome.html` | The exported page bundle. Source of truth; re-export overwrites it. |
| `localize.py` | Build-time patches: removes the CDN/font dependencies, injects the storage shim, favicon links, title and layout CSS. Fails loudly if a re-export moves what it patches. |
| `storage-shim.js` | Redirects the page's `localStorage` config to the config service. |
| `api/server.py` | The config store. Stdlib only. |
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
