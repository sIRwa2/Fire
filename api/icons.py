#!/usr/bin/env python3
"""Icon set updates, from any of the libraries in LIBRARIES.

The diff is exact and cheap. GitHub's tree API returns every path with its git
blob sha in one request, and a blob sha is just

    sha1("blob <bytelen>\\0" + bytes)

so the same value can be computed for the local copies and compared without
downloading anything. Only genuinely new or changed files are fetched. Verified
against all 3232 upstream icons: the computed shas matched GitHub's exactly.

Several libraries can be installed at once, and they overlap: only 1354 of
selfhst's 7084 names also exist in homarr's 3294, so 1940 icons are homarr-only
and 5730 selfhst-only. Picking one set would throw away thousands of icons, so
they layer instead, and a preference order decides who wins a shared name.

Directories, in the order names() resolves them:

  overlay  writable, on the config volume, one subdirectory per library. Updates
           land here, so an update never mutates the image and removing the
           volume reverts to the shipped set.
  baked    read-only, bind-mounted from the repo. Ships with the image, so the
           dashboard has a full icon set with no network access ever.

Both roots also hold icons directly, with no library subdirectory: homarr was the
only library when the image was first built, so the baked set ships flat and
every download from before libraries existed landed in the overlay root. Those
files are homarr's — see FLAT_LIBRARY — which is what lets its count be right and
its update diff work with no migration and no re-download.

Within each, libraries are consulted in preference order. `enabled` hides a
library from the picker without deleting it, so an app already using one of its
icons keeps rendering — see IconStore.names.

Tracks each library's default branch rather than a pinned commit, and only ever
runs when asked: there is no background polling, so the container makes no
outbound request during normal page loads.
"""

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request

# The libraries on offer, in default preference order: earlier wins a shared
# name. All three happen to lay their files out as <format>/<name>.<ext> at depth
# one, which is what the tree walk below relies on.
#
# `baked` marks the set that ships in the image, which is homarr's alone — it is
# what the image was first built around, and it sits flat in the icon root rather
# than in a subdirectory of its own (see FLAT_LIBRARY). Baking a second set would
# mean another 35 MB of svg in the repo and the image for something most installs
# will not switch to, so the others are downloaded on request instead. Nothing in
# the code cares which is which: `baked` is only reported to the settings panel.
#
# selfhst is preferred over homarr by default despite not shipping: it is the
# larger and more actively maintained set, so once it is downloaded it should win
# a shared name. Until then it contributes nothing and homarr answers everything,
# which is exactly what a fresh install wants.
LIBRARIES = (
    {
        "key": "selfhst",
        "label": "selfhst",
        "repo": "selfhst/icons",
        "branch": "main",
        "baked": False,
        "home": "https://github.com/selfhst/icons",
        "note": "7000+ service logos. CC-BY-4.0.",
    },
    {
        "key": "homarr",
        "label": "Homarr dashboard-icons",
        "repo": "homarr-labs/dashboard-icons",
        "branch": "main",
        "baked": True,
        "home": "https://github.com/homarr-labs/dashboard-icons",
        "note": "Ships with the app. Apache-2.0.",
    },
    {
        "key": "mdi",
        "label": "Material Design Icons",
        "repo": "Templarian/MaterialDesign",
        "branch": "master",
        "baked": False,
        "home": "https://pictogrammers.com/library/mdi/",
        # 7447 generic UI glyphs with almost no service logos: 130 names shared
        # with homarr, and no jellyfin, proxmox or gitea. It supplements a service
        # set rather than replacing one, so few installs will want it. Notes are
        # kept to one short line: the panel gives each one a single row.
        "note": "Generic UI glyphs, not service logos.",
    },
)

LIBRARY_KEYS = tuple(lib["key"] for lib in LIBRARIES)
# A key becomes a directory name under the overlay, so it may not be a path.
KEY_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# The flat directory nginx serves first, holding the overrides. Not a library key,
# so it can never collide with one.
RESOLVED = "resolved"

# The library the two roots hold. The image shipped homarr's icons flat, and until
# libraries existed every download landed flat in the overlay too, so these files
# are homarr's rather than unattributable.
FLAT_LIBRARY = "homarr"


def library(key):
    for lib in LIBRARIES:
        if lib["key"] == key:
            return lib
    raise KeyError(key)


def _tree_url(lib):
    return "https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (
        lib["repo"], lib["branch"])


def _commit_url(lib):
    return "https://api.github.com/repos/%s/commits/%s" % (lib["repo"], lib["branch"])


def _raw_url(lib):
    return "https://raw.githubusercontent.com/%s/%s/" % (lib["repo"], lib["branch"])

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
# nginx serves these files from a different user (its worker is uid 101; this
# service runs as 10001), so they have to be world-readable. tempfile.mkstemp
# creates 0600 by design and os.replace preserves it, which left every downloaded
# icon unreadable to nginx — a 403 and a broken thumbnail for each one.
ICON_MODE = 0o644
# Backstop against a malformed tree response. Has to clear the largest library's
# first run, which downloads all of it: selfhst is 7528 names and mdi 7447, so
# 6000 — sized when homarr's ~3900 was the only set — refused them outright.
MAX_DOWNLOADS = 12000
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
        # One file holds the settings and every library's last run, so the update
        # thread and a settings POST read-modify-write the same document. Without
        # this, a preference change landing between the run's read and its write
        # is lost, and the panel renders the order snapping back.
        self._state_lock = threading.Lock()
        self._thread = None
        self._progress = {"running": False, "phase": "idle"}

    # ── layout ──────────────────────────────────────────────────────────────
    # Each library owns a subdirectory under the overlay and under the baked
    # root, and FLAT_LIBRARY owns the two roots as well.
    def overlay_dir(self, key):
        """Where an update writes. Always the subdirectory, never a root: the
        baked root is read-only, and the overlay root is left as it was found so
        that downgrading to a version without libraries still finds its icons."""
        return os.path.join(self.overlay, key)

    def baked_dir(self, key):
        return os.path.join(self.baked, key)

    def dirs_for(self, key):
        """Every directory holding this library's icons, most specific first.

        One list, so precedence is stated once instead of at each reader: the
        writable copy beats the baked one, and a library subdirectory beats the
        flat root it shares with older installs.
        """
        out = [self.overlay_dir(key)]
        if key == FLAT_LIBRARY:
            out.append(self.overlay)
        out.append(self.baked_dir(key))
        if key == FLAT_LIBRARY:
            out.append(self.baked)
        return out

    def served_dirs(self):
        """Directories nginx serves on its own, after looking in resolved/.

        Only the two roots: they hold one library's icons (FLAT_LIBRARY) in a
        fixed overlay-then-baked order, which can never contradict the user's
        preference. A per-library baked subdirectory would need a try_files line
        of its own, in an order fixed at build time — so those are resolved here
        instead, where the preference is known. See nginx.conf.
        """
        return (self.overlay, self.baked)

    def preference(self):
        """Library keys in preference order, unknown keys dropped."""
        saved = self.state().get("preference") or []
        order = [k for k in saved if k in LIBRARY_KEYS]
        order += [k for k in LIBRARY_KEYS if k not in order]
        return order

    def enabled(self):
        """Which libraries the picker may offer. Absent means enabled."""
        flags = self.state().get("enabled") or {}
        return {k: bool(flags.get(k, True)) for k in LIBRARY_KEYS}

    # ── inventory ───────────────────────────────────────────────────────────
    def _scan(self, directory):
        try:
            names = os.listdir(directory)
        except OSError:
            return []
        return [n for n in names if NAME_RE.match(n)]

    def names(self):
        """What the picker may offer, in resolution order.

        An svg is named bare ("plex") so existing config keeps working and the
        page's iconUrl can append .svg as it always has. A raster keeps its
        extension ("adblock.webp"), which iconUrl passes through untouched — the
        extension has to survive into the URL so the response carries the right
        content type.

        A disabled library is skipped here but still served by nginx, so an app
        already pointing at one of its icons keeps rendering — the toggle hides a
        set from search rather than breaking what already uses it.
        """
        on = self.enabled()
        out = {}
        seen = set()
        for key in self.preference():
            if not on.get(key, True):
                continue
            for directory in self.dirs_for(key):
                seen.update(self._scan(directory))
        for name in seen:
            stem, _, ext = name.rpartition(".")
            # svg wins if both exist, matching the download preference.
            if ext == "svg" or stem not in out:
                out[stem] = stem if ext == "svg" else name
        return sorted(out.values())

    def resolved_dir(self):
        """The directory nginx looks in first, holding the overrides.

        nginx resolves /icons/plex.svg by bare name and its config is static, so
        it cannot consult a preference order that lives in a JSON file. Rather
        than teach it to, whichever library wins a name is linked here whenever
        the order or the installed sets change, and nginx checks this directory
        before its own fallbacks.

        Only the overrides, not every icon: a winner already sitting in a
        directory nginx serves by itself needs no entry, and leaving it out is
        what keeps this a handful of hardlinks. Linking the whole baked set here
        would copy rather than link — the image's icons are a bind mount, so a
        hardlink to them crosses devices — which would put 48 MB in the volume
        and rewrite it on every start, since a copy is never samefile() with its
        source. See served_dirs.
        """
        return os.path.join(self.overlay, RESOLVED)

    def resolve(self):
        """Rebuild the resolved directory from the current preference order.

        Only the enabled libraries can win a shared name, so disabling one stops
        it overriding the others. A disabled library still gets a second pass for
        names nothing enabled provides at all: an app already pointing at one of
        its icons keeps rendering, which is what makes the toggle "hide from the
        picker" rather than "break what already uses it". Compare names(), which
        offers only the enabled ones.

        Returns how many entries it wrote.
        """
        target = self.resolved_dir()
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            self.log("icons: cannot create %s: %r" % (target, exc))
            return 0

        on = self.enabled()
        # Later layers must not overwrite earlier ones, so walk in preference
        # order and keep the first hit for each name. Enabled libraries first,
        # then the disabled ones, which can therefore only fill a gap.
        winner = {}
        order = self.preference()
        for key in [k for k in order if on.get(k, True)] + \
                   [k for k in order if not on.get(k, True)]:
            for directory in self.dirs_for(key):
                for name in self._scan(directory):
                    winner.setdefault(name, os.path.join(directory, name))
        # A winner nginx would find anyway, at a place no preference can
        # contradict, is left where it is.
        served = self.served_dirs()
        winner = {name: source for name, source in winner.items()
                  if os.path.dirname(source) not in served}

        existing = set(self._scan(target))
        linked = 0
        for name, source in winner.items():
            dest = os.path.join(target, name)
            try:
                if name in existing and os.path.samefile(source, dest):
                    continue          # already the right file, nothing to do
            except OSError:
                pass
            tmp = dest + ".tmp"
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                try:
                    os.link(source, tmp)
                except OSError:
                    shutil.copyfile(source, tmp)
                    os.chmod(tmp, ICON_MODE)
                os.replace(tmp, dest)
                linked += 1
            except OSError as exc:
                self.log("icons: could not resolve %s: %r" % (name, exc))
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        # Names nothing overrides any more: either no library provides them, or
        # the one that won now sits somewhere nginx reads by itself. Left behind,
        # they would shadow that fallback forever.
        for name in existing - set(winner):
            try:
                os.unlink(os.path.join(target, name))
            except OSError:
                pass
        return linked

    def counts(self):
        """Per-library icon counts, for the settings panel."""
        out = {}
        for key in LIBRARY_KEYS:
            names = set()
            for directory in self.dirs_for(key):
                names.update(self._scan(directory))
            out[key] = len(names)
        return out

    def local_shas(self, key):
        """filename -> blob sha for one library, the most specific copy winning.

        Per-library so a name present in two libraries is not mistaken for an
        already-current copy of the one being updated: the shas would differ and
        every shared icon would be re-downloaded on every run.
        """
        out = {}
        # Least specific first, so a nearer copy overwrites what a further one
        # reported — the same precedence dirs_for states, applied backwards.
        for directory in reversed(self.dirs_for(key)):
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

    def ensure_readable(self):
        """Make sure everything in the overlay is readable by nginx.

        Repairs files written before ICON_MODE existed, which were 0600 and so
        served as 403s. Runs at startup and after an update rather than requiring
        the volume to be recreated. Returns how many needed fixing.
        """
        fixed = 0
        # The overlay root as well as each library's subdirectory: the root is
        # where pre-library installs put their downloads.
        roots = [self.overlay, self.resolved_dir()]
        roots += [self.overlay_dir(k) for k in LIBRARY_KEYS]
        for root in roots:
            for name in self._scan(root):
                path = os.path.join(root, name)
                try:
                    mode = os.stat(path).st_mode & 0o777
                    if not mode & 0o044:
                        os.chmod(path, ICON_MODE)
                        fixed += 1
                except OSError:
                    pass
        if fixed:
            self.log("icons: made %d file(s) readable to nginx" % fixed)
        return fixed

    def writable(self):
        """Whether the overlay can actually be written to.

        Checked up front because the alternative is what shipped first: a
        root-owned volume produced 690 identical EACCES failures and a "690
        failed" summary, with the cause only visible in the container log.
        """
        try:
            os.makedirs(self.overlay, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.overlay, prefix=".probe-")
            os.close(fd)
            os.unlink(tmp)
            return True
        except OSError:
            return False

    def status(self):
        out = dict(self._progress)
        out["installed"] = len(self.names())
        out["writable"] = self.writable()
        out["last"] = self.state()
        counts = self.counts()
        on = self.enabled()
        # Ordered by preference, so the panel can render the list as-is and the
        # order it shows is the order that resolves a shared name.
        out["libraries"] = [
            {
                "key": lib["key"], "label": lib["label"], "repo": lib["repo"],
                "home": lib["home"], "note": lib["note"], "baked": lib["baked"],
                "count": counts.get(lib["key"], 0),
                "enabled": on.get(lib["key"], True),
            }
            for lib in (library(k) for k in self.preference())
        ]
        return out

    def set_config(self, preference=None, enabled=None):
        """Persist a new preference order and/or enabled flags.

        Validated here rather than at the route: an unknown key would become a
        directory name under the overlay.
        """
        with self._state_lock:
            state = self.state()
            if preference is not None:
                order = [k for k in preference if k in LIBRARY_KEYS]
                # Any key the caller omitted keeps its relative position at the
                # end, so a stale client cannot silently drop a library.
                order += [k for k in LIBRARY_KEYS if k not in order]
                state["preference"] = order
            if enabled is not None:
                flags = state.get("enabled") or {}
                for key, value in enabled.items():
                    if key in LIBRARY_KEYS:
                        flags[key] = bool(value)
                state["enabled"] = flags
            self._save_state(state)
        # The order decides who wins a shared name, so the served directory has
        # to be rebuilt before the change is visible on the page.
        self.resolve()
        return {"preference": self.preference(), "enabled": self.enabled()}

    # ── update ──────────────────────────────────────────────────────────────
    def start(self, key):
        """Kick off an update for one library. False if one is already running."""
        if key not in LIBRARY_KEYS:
            raise ValueError("unknown icon library %r" % key)
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._progress = {"running": True, "phase": "contacting github",
                              "library": key}
            self._thread = threading.Thread(target=self._run, args=(key,),
                                            daemon=True)
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

    def _run(self, key):
        lib = library(key)
        started = time.time()
        result = {"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "library": key, "repo": lib["repo"]}
        try:
            # Fail once, with the cause, rather than once per file.
            if not self.writable():
                raise PermissionError(
                    "cannot write to %s. A named volume mounted where the image "
                    "has no directory is created root-owned, which this "
                    "unprivileged service cannot write to. Recreate it: "
                    "docker compose down && docker volume rm "
                    "<project>_firehome-icons && docker compose up -d --build"
                    % self.overlay
                )

            commit = json.loads(self._get(_commit_url(lib)).decode("utf-8"))
            result["commit"] = commit.get("sha", "")[:12]
            result["commit_date"] = (
                commit.get("commit", {}).get("committer", {}).get("date")
            )

            self._progress["phase"] = "reading the icon index"
            tree = json.loads(self._get(_tree_url(lib)).decode("utf-8"))
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

            local = self.local_shas(key)
            todo = [n for n, (_, sha) in upstream.items() if local.get(n) != sha]
            result["upstream"] = len(upstream)
            result["attempted"] = len(todo)

            if len(todo) > MAX_DOWNLOADS:
                raise ValueError(
                    "refusing to download %d files (limit %d)" % (len(todo), MAX_DOWNLOADS)
                )

            os.makedirs(self.overlay_dir(key), exist_ok=True)
            # Counted as each file lands, not up front from the size of `todo`:
            # these numbers are reported to the user as what happened, and a run
            # whose downloads all failed must not claim it updated anything.
            added = changed = done = 0
            failed = []
            counters = threading.Lock()
            total = len(todo)

            raw = _raw_url(lib)

            def fetch(name):
                path, sha = upstream[name]
                data = self._get(raw + path, limit=MAX_ICON_BYTES)
                if blob_sha(data) != sha:
                    raise ValueError("checksum mismatch")
                self._write(key, name, data)

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

        self.ensure_readable()
        # Newly downloaded files only become visible once they are linked into
        # the directory nginx serves.
        try:
            self.resolve()
        except OSError as exc:
            self.log("icons: could not rebuild the resolved set: %r" % (exc,))
        try:
            # Merged, not overwritten: the same file holds the preference order
            # and the enabled flags, and each library's own last-run record. Under
            # the lock, so a settings POST arriving now is not read-modify-written
            # away by this.
            with self._state_lock:
                state = self.state()
                runs = state.get("runs") or {}
                runs[key] = result
                state["runs"] = runs
                # Kept at the top level too, so a client reading the old shape
                # still sees the most recent run rather than nothing.
                state.update({k: v for k, v in result.items()
                              if k not in ("preference", "enabled", "runs")})
                self._save_state(state)
        except OSError as exc:
            self.log("icons: could not persist state: %r" % (exc,))
        self._progress = {"running": False, "phase": "idle"}

    def _write(self, key, name, data):
        if not NAME_RE.match(name):
            raise ValueError("rejected name %r" % name)
        if not KEY_RE.match(key):
            raise ValueError("rejected library key %r" % key)
        target = self.overlay_dir(key)
        os.makedirs(target, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target, prefix=".dl-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            # Before the rename, so the file is never visible at 0600.
            os.chmod(tmp, ICON_MODE)
            os.replace(tmp, os.path.join(target, name))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
