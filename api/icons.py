#!/usr/bin/env python3
"""Icon set updates from github.com/homarr-labs/dashboard-icons.

The diff is exact and cheap. GitHub's tree API returns every path with its git
blob sha in one request, and a blob sha is just

    sha1("blob <bytelen>\\0" + bytes)

so the same value can be computed for the local copies and compared without
downloading anything. Only genuinely new or changed files are fetched. Verified
against all 3232 upstream icons: the computed shas matched GitHub's exactly.

Two directories are involved:

  baked   read-only, bind-mounted from the repo's svg/. Ships with the image, so
          the dashboard has a full icon set with no network access ever.
  overlay writable, on the config volume. Updates land here and nginx serves it
          in front of the baked set, so an update never mutates the image and
          removing the volume reverts to the shipped set.

Tracks main rather than a pinned commit, and only ever runs when asked — there is
no background polling, so the container makes no outbound request during normal
page loads.
"""

import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = "homarr-labs/dashboard-icons"
BRANCH = "main"
TREE_URL = "https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (REPO, BRANCH)
COMMIT_URL = "https://api.github.com/repos/%s/commits/%s" % (REPO, BRANCH)
RAW_URL = "https://raw.githubusercontent.com/%s/%s/" % (REPO, BRANCH)

# Preference order. svg is wanted wherever it exists — it scales and is tiny —
# but 690 of the ~3900 icons upstream have no svg at all, so those fall back to a
# raster. webp before png because it is markedly smaller: taking webp where it
# exists makes the svg-less set 28 MB instead of 263 MB for every variant.
FORMATS = ("svg", "webp", "png")

# Names come from a third party and are used to build file paths, so they are
# restricted to a flat, boring shape: no separators, no leading dot, no traversal.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:svg|webp|png)$")

# Raised past 512 KB because one real icon (napcat.webp, 661 KB) exceeded it.
MAX_ICON_BYTES = 1024 * 1024
MAX_DOWNLOADS = 6000        # backstop against a malformed tree response
HTTP_TIMEOUT = 30
# The first run after adding raster fallbacks fetches ~690 files. Sequentially
# that is about five minutes of a progress counter crawling, which reads as a
# hang; eight at a time brings it under a minute and is undemanding of the CDN.
DOWNLOAD_WORKERS = 4
# Transient failures are normal at this fan-out — the first parallel run lost 192
# of 690 files to "Temporary failure in name resolution", not to any HTTP error,
# because the threads outpaced the DNS resolver. Retrying with backoff turns that
# into a delay rather than 192 silently missing icons.
RETRIES = 4
RETRY_BACKOFF = 0.6
USER_AGENT = "firehome-icon-updater"


def _headers():
    h = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("FIREHOME_GITHUB_TOKEN", "").strip()
    if token:
        # Only raises the 60/hour unauthenticated rate limit; never required.
        h["Authorization"] = "Bearer " + token
    return h


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class IconStore:
    def __init__(self, overlay_dir, baked_dir, state_path, log=print):
        self.overlay = overlay_dir
        self.baked = baked_dir
        self.state_path = state_path
        self.log = log
        self._lock = threading.Lock()
        self._thread = None
        self._progress = {"running": False, "phase": "idle"}

    # ── inventory ───────────────────────────────────────────────────────────
    def _scan(self, directory):
        try:
            names = os.listdir(directory)
        except OSError:
            return []
        return [n for n in names if NAME_RE.match(n)]

    def names(self):
        """What the dashboard can offer, overlay included.

        An svg is named bare ("plex") so existing config keeps working and the
        page's iconUrl can append .svg as it always has. A raster keeps its
        extension ("adblock.webp"), which iconUrl passes through untouched — the
        extension has to survive into the URL so the response carries the right
        content type.
        """
        out = {}
        for name in set(self._scan(self.baked)) | set(self._scan(self.overlay)):
            stem, _, ext = name.rpartition(".")
            # svg wins if both exist, matching the download preference.
            if ext == "svg" or stem not in out:
                out[stem] = stem if ext == "svg" else name
        return sorted(out.values())

    def local_shas(self):
        """filename -> blob sha, overlay shadowing baked."""
        out = {}
        for directory in (self.baked, self.overlay):
            for name in self._scan(directory):
                try:
                    with open(os.path.join(directory, name), "rb") as fh:
                        out[name] = blob_sha(fh.read())
                except OSError:
                    pass
        return out

    # ── persisted state ─────────────────────────────────────────────────────
    def state(self):
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        directory = os.path.dirname(self.state_path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".icons-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.state_path)

    def status(self):
        out = dict(self._progress)
        out["installed"] = len(self.names())
        out["last"] = self.state()
        return out

    # ── update ──────────────────────────────────────────────────────────────
    def start(self):
        """Kick off an update. False if one is already running."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._progress = {"running": True, "phase": "contacting github"}
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True

    def _get(self, url, timeout=HTTP_TIMEOUT, limit=None):
        """Fetch a URL, retrying transient failures with a backoff.

        Retries name-resolution and connection errors, timeouts, 429s and 5xxs.
        A 404 or a body over the cap is final — repeating those just wastes time.
        """
        last = None
        for attempt in range(RETRIES):
            try:
                req = urllib.request.Request(url, headers=_headers())
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if limit is None:
                        return resp.read()
                    data = resp.read(limit + 1)
                    if len(data) > limit:
                        raise ValueError("response larger than %d bytes" % limit)
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            if attempt < RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
        raise last

    def _run(self):
        started = time.time()
        result = {"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        try:
            commit = json.loads(self._get(COMMIT_URL).decode("utf-8"))
            result["commit"] = commit.get("sha", "")[:12]
            result["commit_date"] = (
                commit.get("commit", {}).get("committer", {}).get("date")
            )

            self._progress["phase"] = "reading the icon index"
            tree = json.loads(self._get(TREE_URL).decode("utf-8"))
            if tree.get("truncated"):
                raise ValueError("GitHub truncated the tree listing")

            # One entry per icon: the best available format, so a name with an
            # svg is never also fetched as a raster.
            best = {}
            for entry in tree.get("tree", []):
                path = entry.get("path", "")
                if entry.get("type") != "blob" or path.count("/") != 1:
                    continue
                directory, _, name = path.partition("/")
                if directory not in FORMATS or not NAME_RE.match(name):
                    continue
                stem, _, ext = name.rpartition(".")
                if ext != directory:          # png/foo.svg and friends
                    continue
                rank = FORMATS.index(ext)
                if stem not in best or rank < best[stem][0]:
                    best[stem] = (rank, name, path, entry.get("sha", ""))
            upstream = {name: (path, sha) for _, name, path, sha in best.values()}
            if not upstream:
                raise ValueError("no icons found in the upstream tree")
            result["formats"] = {
                fmt: sum(1 for n in upstream if n.endswith("." + fmt))
                for fmt in FORMATS
            }

            local = self.local_shas()
            todo = [n for n, (_, sha) in upstream.items() if local.get(n) != sha]
            result["upstream"] = len(upstream)
            result["attempted"] = len(todo)

            if len(todo) > MAX_DOWNLOADS:
                raise ValueError(
                    "refusing to download %d files (limit %d)" % (len(todo), MAX_DOWNLOADS)
                )

            os.makedirs(self.overlay, exist_ok=True)
            # Counted as each file lands, not up front from the size of `todo`:
            # these numbers are reported to the user as what happened, and a run
            # whose downloads all failed must not claim it updated anything.
            added = changed = done = 0
            failed = []
            counters = threading.Lock()
            total = len(todo)

            def fetch(name):
                path, sha = upstream[name]
                data = self._get(RAW_URL + path, limit=MAX_ICON_BYTES)
                if blob_sha(data) != sha:
                    raise ValueError("checksum mismatch")
                self._write(name, data)

            with concurrent.futures.ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
                futures = {pool.submit(fetch, n): n for n in sorted(todo)}
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    with counters:
                        done += 1
                        try:
                            future.result()
                            if name in local:
                                changed += 1
                            else:
                                added += 1
                        except Exception as exc:
                            failed.append(name)
                            self.log("icons: %s failed: %r" % (name, exc))
                        self._progress["phase"] = "downloading %d/%d" % (done, total)

            result["new"] = added
            result["changed"] = changed
            result["failed"] = len(failed)
            result["failed_names"] = sorted(failed)[:20]
            result["seconds"] = round(time.time() - started, 1)
            result["error"] = None
            self.log(
                "icons: %s — %d new, %d changed, %d failed of %d attempted in %.1fs"
                % (result.get("commit", "?"), added, changed, len(failed),
                   len(todo), result["seconds"])
            )
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
            self.log("icons: update failed: %r" % (exc,))

        try:
            self._save_state(result)
        except OSError as exc:
            self.log("icons: could not persist state: %r" % (exc,))
        self._progress = {"running": False, "phase": "idle"}

    def _write(self, name, data):
        if not NAME_RE.match(name):
            raise ValueError("rejected name %r" % name)
        fd, tmp = tempfile.mkstemp(dir=self.overlay, prefix=".dl-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, os.path.join(self.overlay, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
