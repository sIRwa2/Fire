#!/usr/bin/env python3
"""firehome config store — a single JSON document over HTTP.

  GET    /config   -> {"rev": N, "updated": "<iso>", "data": {...}}
  PUT    /config   <- {"data": {...}}   merge; a null value drops that key
  POST   /config   same as PUT (navigator.sendBeacon can only POST)
  DELETE /config   wipe — "start clean"
  GET    /icons          -> {"icons": [...], "count": N, "status": {...}}
  POST   /favicon        <- {"url": "..."}  fetch and cache that site's best icon
  POST   /icons/update   start an update from GitHub; 409 while one is running
  GET    /icons/status   progress and the last result
  GET    /healthz

Deliberately stdlib-only: the whole job is to hold a few kilobytes of dashboard
config for a LAN homepage, and a dependency-free service is one less thing to
keep patched. Writes are atomic (temp file + os.replace + fsync) and the
previous document is snapshotted into history/ first, so a bad write or an
accidental reset is recoverable.

Last write wins. Two browsers editing at once is not coordinated — the second
save overwrites the first, and the loser can be recovered from history/.
"""

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import favicon as favicon_mod
import icons as icons_mod

DATA_DIR = os.environ.get("FIREHOME_DATA", "/data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
HISTORY_KEEP = int(os.environ.get("FIREHOME_HISTORY_KEEP", "20"))
PORT = int(os.environ.get("FIREHOME_API_PORT", "8080"))
MAX_BODY = 1 << 20  # 1 MiB; the real document is a few KB

EMPTY = {"rev": 0, "updated": None, "data": {}}

# Writable overlay on the volume, plus the read-only set bind-mounted from the
# repo's svg/. See icons.py for why there are two.
ICONS_DIR = os.path.join(DATA_DIR, "icons")
BAKED_ICONS = os.environ.get("FIREHOME_BAKED_ICONS", "/srv/icons-baked")
ICONS_STATE = os.path.join(DATA_DIR, "icons-state.json")
FAVICON_DIR = os.path.join(DATA_DIR, "favicons")

_lock = threading.Lock()
_icons = None  # set in main(), so the paths are configured in one place
_favicons = None


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _read_locked():
    """Return the stored document, or EMPTY when there is nothing stored yet.

    A corrupt file raises rather than being treated as empty: silently serving
    defaults would look like data loss and the next save would make it real.
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return dict(EMPTY)
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), dict):
        raise ValueError("stored config is not a valid document")
    doc.setdefault("rev", 0)
    doc.setdefault("updated", None)
    return doc


def _snapshot_locked(reason):
    """Copy the current document into history/ before it is replaced."""
    if not os.path.exists(CONFIG_PATH):
        return
    os.makedirs(HISTORY_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = os.path.join(HISTORY_DIR, "config-%s-%s.json" % (stamp, reason))
    n = 1
    while os.path.exists(dest):  # same-second writes
        dest = os.path.join(HISTORY_DIR, "config-%s-%s.%d.json" % (stamp, reason, n))
        n += 1
    with open(CONFIG_PATH, "rb") as src, open(dest, "wb") as out:
        out.write(src.read())

    keep = sorted(f for f in os.listdir(HISTORY_DIR) if f.endswith(".json"))
    for stale in keep[:-HISTORY_KEEP] if HISTORY_KEEP > 0 else keep:
        try:
            os.unlink(os.path.join(HISTORY_DIR, stale))
        except OSError:
            pass


def _write_locked(doc):
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dirfd = os.open(DATA_DIR, os.O_RDONLY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)


def merge(patch):
    with _lock:
        doc = _read_locked()
        data = dict(doc["data"])
        for key, value in patch.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        if data == doc["data"]:
            return doc, False
        _snapshot_locked("pre-save")
        doc = {
            "rev": int(doc["rev"]) + 1,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": data,
        }
        _write_locked(doc)
        return doc, True


def wipe():
    with _lock:
        existed = os.path.exists(CONFIG_PATH)
        if existed:
            _snapshot_locked("pre-reset")
            os.unlink(CONFIG_PATH)
        return existed


class Handler(BaseHTTPRequestHandler):
    server_version = "firehome-config"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        log("%s %s" % (self.address_string(), fmt % a))

    # ── helpers ─────────────────────────────────────────────────────────
    def _send(self, code, payload=None, ctype="application/json"):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _fail(self, code, message):
        self._send(code, {"error": message})

    def _body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            raise ValueError("Content-Length required")
        try:
            length = int(length)
        except ValueError:
            raise ValueError("bad Content-Length")
        if length < 0 or length > MAX_BODY:
            raise ValueError("body must be 0..%d bytes" % MAX_BODY)
        raw = self.rfile.read(length)
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("body must be a JSON object")
        patch = doc.get("data", doc)
        if not isinstance(patch, dict):
            raise ValueError('"data" must be a JSON object')
        return patch

    # ── routes ──────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path == "/healthz":
            return self._send(200, {"ok": True})
        if self.path == "/icons":
            names = _icons.names()
            return self._send(200, {"icons": names, "count": len(names),
                                    "status": _icons.status()})
        if self.path == "/icons/status":
            return self._send(200, _icons.status())
        if self.path != "/config":
            return self._fail(404, "not found")
        try:
            return self._send(200, _read_locked())
        except Exception as exc:
            log("read failed: %r" % (exc,))
            return self._fail(500, "stored config unreadable: %s" % exc)

    do_HEAD = do_GET

    def _save(self):
        if self.path == "/favicon":
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._fail(400, str(exc))
            url = body.get("url")
            if not isinstance(url, str) or not url.strip():
                return self._fail(400, '"url" is required')
            try:
                name, source = _favicons.fetch(url.strip())
            except ValueError as exc:
                return self._fail(400, str(exc))
            except Exception as exc:
                log("favicon: %r" % (exc,))
                return self._fail(502, "could not fetch an icon: %s" % exc)
            if not name:
                return self._fail(404, "no usable icon found")
            return self._send(200, {"icon": "fav:" + name, "source": source})
        if self.path == "/icons/update":
            if not _icons.start():
                return self._fail(409, "an update is already running")
            log("icons: update requested by %s" % self.address_string())
            return self._send(200, _icons.status())
        if self.path != "/config":
            return self._fail(404, "not found")
        try:
            patch = self._body()
        except ValueError as exc:
            return self._fail(400, str(exc))
        except json.JSONDecodeError as exc:
            return self._fail(400, "invalid JSON: %s" % exc)
        try:
            doc, changed = merge(patch)
        except Exception as exc:
            log("write failed: %r" % (exc,))
            return self._fail(500, "could not save: %s" % exc)
        return self._send(200, {"rev": doc["rev"], "updated": doc["updated"],
                                "changed": changed})

    do_PUT = _save
    do_POST = _save

    def do_DELETE(self):
        if self.path != "/config":
            return self._fail(404, "not found")
        try:
            existed = wipe()
        except Exception as exc:
            log("reset failed: %r" % (exc,))
            return self._fail(500, "could not reset: %s" % exc)
        log("config reset (existed=%s)" % existed)
        return self._send(200, {"reset": True, "existed": existed})


def main():
    global _icons, _favicons
    os.makedirs(DATA_DIR, exist_ok=True)
    _icons = icons_mod.IconStore(ICONS_DIR, BAKED_ICONS, ICONS_STATE, log=log)
    _favicons = favicon_mod.FaviconStore(FAVICON_DIR, log=log)
    log("icons: overlay=%s baked=%s (%d available)"
        % (ICONS_DIR, BAKED_ICONS, len(_icons.names())))
    # Repairs icons written before they were made world-readable, so an existing
    # volume recovers on restart instead of needing to be recreated.
    _icons.ensure_readable()
    server = ThreadingHTTPServer(("", PORT), Handler)
    server.daemon_threads = True
    log("firehome-config listening on :%d, data=%s" % (PORT, DATA_DIR))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
