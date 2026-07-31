#!/usr/bin/env python3
"""Offline checks for the icon updater.

Nothing here touches the network: the parts worth pinning are the blob-sha
format the whole diff depends on, the name validation that keeps third-party
strings from escaping the overlay directory, the baked/overlay precedence, and
the library layering — which of several sets wins a name both libraries provide.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import icons as icons_mod  # noqa: E402

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("ok    %s" % name)
    else:
        failed += 1
        print("FAIL  %s%s" % (name, "  <- " + str(extra) if extra else ""))


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


# The diff is only as good as this hash matching what GitHub reports. Git's empty
# blob is a published constant, so it pins the format against something external
# rather than against our own implementation.
check(
    "blob_sha matches git's empty-blob hash",
    icons_mod.blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    icons_mod.blob_sha(b""),
)
check(
    "blob_sha length-prefixes the content",
    icons_mod.blob_sha(b"hello") == "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0",
    icons_mod.blob_sha(b"hello"),
)

# Names build file paths, so anything with a separator or a leading dot must be
# refused outright.
for bad in ("../evil.svg", "a/b.svg", "/abs.svg", ".hidden.svg", "no-extension",
            "trailing.svg.exe", "", "..", "sub/../x.svg"):
    check("rejects name %r" % bad, not icons_mod.NAME_RE.match(bad))
for good in ("plex.svg", "home-assistant.svg", "1password-dark.svg", "a.b_c-d.svg"):
    check("accepts name %r" % good, bool(icons_mod.NAME_RE.match(good)))

# A library key becomes a directory name under the overlay, so it is held to the
# same standard as a filename.
for bad in ("../evil", "a/b", "/abs", "-lead", "Mixed", "", "."):
    check("rejects library key %r" % bad, not icons_mod.KEY_RE.match(bad))
check("every shipped library key is a safe directory name",
      all(icons_mod.KEY_RE.match(k) for k in icons_mod.LIBRARY_KEYS),
      icons_mod.LIBRARY_KEYS)
# resolved/ sits beside the library directories, so a library named "resolved"
# would have its files deleted as stale on the next resolve.
check("no library is named after the resolved directory",
      icons_mod.RESOLVED not in icons_mod.LIBRARY_KEYS)

FLAT = icons_mod.FLAT_LIBRARY
# Two libraries that own only a subdirectory, in preference order, for the tests
# where the roots would get in the way.
A, B = [k for k in icons_mod.LIBRARY_KEYS if k != FLAT][:2]

work = tempfile.mkdtemp()
try:
    # ── the flat roots ──────────────────────────────────────────────────────
    # The image ships one set flat, and installs from before libraries existed
    # have their downloads flat in the overlay too. Those files are FLAT_LIBRARY's
    # and have to keep working with no migration and no re-download.
    baked = os.path.join(work, "baked")
    overlay = os.path.join(work, "overlay")
    os.makedirs(baked)
    os.makedirs(overlay)
    write(os.path.join(baked, "plex.svg"), b"<svg>baked</svg>")
    write(os.path.join(baked, "shared.svg"), b"<svg>old</svg>")
    write(os.path.join(overlay, "shared.svg"), b"<svg>new</svg>")
    write(os.path.join(overlay, "brand-new.svg"), b"<svg>new</svg>")
    write(os.path.join(baked, "notes.txt"), b"ignore me")

    store = icons_mod.IconStore(overlay, baked, os.path.join(work, "state.json"),
                                log=lambda m: None)

    check("names unions both flat directories and strips .svg",
          store.names() == ["brand-new", "plex", "shared"], store.names())
    check("non-svg files are ignored", "notes" not in store.names())

    check("the flat sets are counted as the library that shipped them",
          store.counts()[FLAT] == 3, store.counts())
    check("another library does not claim the flat sets",
          store.counts()[A] == 0, store.counts())

    shas = store.local_shas(FLAT)
    check("the overlay root shadows the baked root",
          shas["shared.svg"] == icons_mod.blob_sha(b"<svg>new</svg>"), shas)
    check("a baked-only file is still seen",
          shas["plex.svg"] == icons_mod.blob_sha(b"<svg>baked</svg>"), shas)
    check("a flat-layout name is not attributed to another library",
          store.local_shas(A) == {}, store.local_shas(A))

    # nginx reads both roots by itself, in the same writable-then-baked order, so
    # nothing in them has to be linked into the overrides directory. Copying them
    # there would be 48 MB of duplicated baked icons in the real deployment, and
    # rewritten on every start, since a cross-device copy is never samefile().
    check("resolve overrides nothing when only the roots are populated",
          store.resolve() == 0)
    check("the overrides directory stays empty",
          store._scan(store.resolved_dir()) == [],
          store._scan(store.resolved_dir()))

    # A missing baked mount must degrade, not crash.
    gone = icons_mod.IconStore(overlay, os.path.join(work, "absent"),
                               os.path.join(work, "s2.json"), log=lambda m: None)
    check("missing baked directory degrades quietly",
          gone.names() == ["brand-new", "shared"], gone.names())

    check("state is empty before any run", store.state() == {}, store.state())
    check("status reports the installed count",
          store.status()["installed"] == 3, store.status())
    check("status lists every library",
          [lib["key"] for lib in store.status()["libraries"]] == store.preference(),
          store.status()["libraries"])

    # ── per-library layout ──────────────────────────────────────────────────
    lib_work = tempfile.mkdtemp(dir=work)
    lb = os.path.join(lib_work, "baked")
    lo = os.path.join(lib_work, "overlay")
    os.makedirs(lo)
    write(os.path.join(lb, A, "shared.svg"), b"<svg>first</svg>")
    write(os.path.join(lb, A, "first-only.svg"), b"<svg>first</svg>")
    write(os.path.join(lb, B, "shared.svg"), b"<svg>second</svg>")
    write(os.path.join(lb, B, "second-only.svg"), b"<svg>second</svg>")
    write(os.path.join(lo, A, "shared.svg"), b"<svg>first overlay</svg>")

    libs = icons_mod.IconStore(lo, lb, os.path.join(lib_work, "state.json"),
                               log=lambda m: None)
    resolved = libs.resolved_dir()

    check("preference defaults to the shipped order",
          libs.preference() == list(icons_mod.LIBRARY_KEYS), libs.preference())
    check("names unions the libraries",
          libs.names() == ["first-only", "second-only", "shared"], libs.names())
    check("counts are per library",
          libs.counts()[A] == 2 and libs.counts()[B] == 2, libs.counts())

    # A name only the baked root has: nginx reaches it by itself, so it must not
    # be linked into the overrides — see the roots section above.
    write(os.path.join(lb, "root-only.svg"), b"<svg>root</svg>")

    libs.resolve()
    check("resolve overrides every name a library subdirectory provides",
          sorted(os.listdir(resolved)) == ["first-only.svg", "second-only.svg",
                                           "shared.svg"], os.listdir(resolved))
    check("a name only the roots provide is left to nginx",
          "root-only.svg" not in os.listdir(resolved), os.listdir(resolved))
    check("a name only the roots provide is still offered",
          "root-only" in libs.names(), libs.names())
    check("the preferred library wins a shared name",
          read(os.path.join(resolved, "shared.svg")) == b"<svg>first overlay</svg>",
          read(os.path.join(resolved, "shared.svg")))
    # The duplicate should cost an inode, not the bytes — both files are on one
    # volume in the real deployment, so the link is expected to succeed.
    check("a resolved icon is hardlinked, not copied",
          os.stat(os.path.join(resolved, "first-only.svg")).st_nlink > 1,
          os.stat(os.path.join(resolved, "first-only.svg")).st_nlink)
    check("resolved icons are readable by nginx",
          os.stat(os.path.join(resolved, "shared.svg")).st_mode & 0o044 != 0)
    check("no .tmp files are left behind",
          not [n for n in os.listdir(resolved) if n.endswith(".tmp")],
          os.listdir(resolved))

    # Reordering has to change who wins, and has to take effect without waiting
    # for the next update: set_config rebuilds the resolved directory itself.
    libs.set_config(preference=[B])
    check("an omitted key keeps its place rather than being dropped",
          sorted(libs.preference()) == sorted(icons_mod.LIBRARY_KEYS)
          and libs.preference()[0] == B, libs.preference())
    check("reordering changes who wins a shared name",
          read(os.path.join(resolved, "shared.svg")) == b"<svg>second</svg>",
          read(os.path.join(resolved, "shared.svg")))

    # An unknown key would become a directory name under the overlay.
    libs.set_config(preference=["../evil", B])
    check("an unknown library key is dropped from the preference",
          "../evil" not in libs.preference(), libs.preference())
    check("nothing was created outside the overlay",
          not os.path.exists(os.path.join(lib_work, "evil")))

    # Disabling hides a set from the picker. It must not break an app already
    # pointing at one of its icons, so a name nothing else provides is still
    # served — that is the whole difference between this and deleting the set.
    libs.set_config(enabled={A: False})
    check("a disabled library is not offered by the picker",
          "first-only" not in libs.names(), libs.names())
    check("a disabled library cannot win a shared name",
          read(os.path.join(resolved, "shared.svg")) == b"<svg>second</svg>",
          read(os.path.join(resolved, "shared.svg")))
    check("a disabled library's unique icon is still served",
          os.path.exists(os.path.join(resolved, "first-only.svg")))
    check("status reports the disabled flag",
          [lib["enabled"] for lib in libs.status()["libraries"]
           if lib["key"] == A] == [False], libs.status()["libraries"])
    libs.set_config(enabled={A: True})

    # An override no library provides any more must not linger: nginx looks here
    # first, so it would shadow the fallbacks forever.
    os.unlink(os.path.join(lb, B, "second-only.svg"))
    libs.resolve()
    check("a removed icon is dropped from the resolved directory",
          not os.path.exists(os.path.join(resolved, "second-only.svg")),
          os.listdir(resolved))

    # ── the update run ──────────────────────────────────────────────────────
    # Reporting must describe what happened, not what was attempted. A run whose
    # downloads all fail used to report them as "updated", because new/changed
    # were taken from the size of the todo list before anything was fetched.
    fresh = os.path.join(work, "overlay2")
    os.makedirs(fresh)
    # Baked for this library with different content, so the upstream copy counts
    # as changed rather than new.
    write(os.path.join(lb, B, "plex.svg"), b"<svg>old</svg>")
    reporting = icons_mod.IconStore(fresh, lb, os.path.join(work, "s3.json"),
                                    log=lambda m: None)
    # The same file holds the settings and every library's last run, so a run has
    # to merge into it. Set something first, so there is something to lose.
    reporting.set_config(preference=[B])
    upstream_sha = icons_mod.blob_sha(b"<svg>upstream</svg>")

    def fake_get(url, timeout=None, limit=None):
        if "commits" in url:
            return b'{"sha":"deadbeefcafe","commit":{"committer":{"date":"2026-01-01T00:00:00Z"}}}'
        if "trees" in url:
            # Real tree entries carry type:blob, and the same icon appears in
            # several format directories — which is what the preference logic has
            # to collapse to one download per name.
            entries = [
                ("svg/plex.svg", upstream_sha),      # svg wins for plex
                ("webp/plex.webp", "ffff"),
                ("png/plex.png", "ffff"),
                ("svg/newone.svg", upstream_sha),
                ("webp/rasteronly.webp", upstream_sha),   # webp wins, no svg
                ("png/rasteronly.png", "ffff"),
                ("png/pngonly.png", upstream_sha),        # png is all there is
                ("meta/plex.json", "ffff"),               # other dirs ignored
                ("svg/nested/deep.svg", "ffff"),          # not flat, ignored
            ]
            return ('{"truncated":false,"tree":[%s]}' % ",".join(
                '{"path":"%s","sha":"%s","type":"blob"}' % e for e in entries
            )).encode()
        raise OSError("download refused")  # every file download fails

    reporting._get = fake_get
    # The key names a directory to download into, so it is rejected before any
    # thread starts rather than sanitised on the way to the filesystem.
    try:
        reporting.start("../evil")
        check("an unknown library is refused", False, "no exception raised")
    except ValueError:
        check("an unknown library is refused", True)

    reporting.start(B)
    reporting._thread.join(10)
    last = reporting.state()
    check("all-failed run reports 0 new", last.get("new") == 0, last)
    check("all-failed run reports 0 changed", last.get("changed") == 0, last)
    check("all-failed run reports the failures", last.get("failed") == 4, last)
    check("all-failed run records what it attempted", last.get("attempted") == 4, last)
    check("the run records which library it was for",
          last.get("library") == B, last)
    # One download per name: the duplicate webp/png copies of plex are not fetched,
    # and non-format directories and nested paths are ignored.
    check("prefers one format per name",
          last.get("formats") == {"svg": 2, "webp": 1, "png": 1}, last.get("formats"))
    check("nothing was written on failure",
          os.listdir(os.path.join(fresh, B)) == [],
          os.listdir(os.path.join(fresh, B)))

    # And the happy path still counts new vs changed correctly.
    def ok_get(url, timeout=None, limit=None):
        if "commits" in url or "trees" in url:
            return fake_get(url)
        return b"<svg>upstream</svg>"

    reporting._get = ok_get
    reporting.start(B)
    reporting._thread.join(10)
    last = reporting.state()
    # shared.svg is baked for this library, with different content, so it counts
    # as changed; the other three are new.
    check("happy path counts the new files", last.get("new") == 3, last)
    check("happy path counts the changed file", last.get("changed") == 1, last)
    check("happy path reports no failures", last.get("failed") == 0, last)
    check("every chosen format landed in the library's directory",
          sorted(os.listdir(os.path.join(fresh, B)))
          == ["newone.svg", "plex.svg", "pngonly.png", "rasteronly.webp"],
          os.listdir(os.path.join(fresh, B)))
    check("a run keeps the settings in the state file",
          (last.get("preference") or [None])[0] == B, last.get("preference"))
    check("a run is recorded under its own library",
          (last.get("runs") or {}).get(B, {}).get("new") == 3, last.get("runs"))
    # A download is invisible until it is linked into the served directory.
    check("a downloaded icon is resolved without a further request",
          os.path.exists(os.path.join(fresh, icons_mod.RESOLVED, "newone.svg")),
          os.listdir(os.path.join(fresh, icons_mod.RESOLVED)))

    # svg is offered bare so existing config keeps working; a raster keeps its
    # extension, which iconUrl passes straight into the URL.
    offered = icons_mod.IconStore(fresh, os.path.join(work, "absent"),
                                  os.path.join(work, "s4.json"),
                                  log=lambda m: None).names()
    check("svg icons are offered without an extension",
          "plex" in offered and "plex.svg" not in offered, offered)
    check("rasters are offered with their extension",
          "rasteronly.webp" in offered and "pngonly.png" in offered, offered)

    # nginx reads these as a different user, so every written file has to be
    # world-readable. mkstemp creates 0600 and os.replace keeps it, which is what
    # made every downloaded icon a 403.
    downloaded = os.path.join(fresh, B)
    modes = sorted(oct(os.stat(os.path.join(downloaded, n)).st_mode & 0o777)
                   for n in os.listdir(downloaded))
    check("downloaded icons are world-readable", set(modes) == {"0o644"}, modes)

    # And files left at 0600 by an older version are repaired in place, so an
    # existing volume recovers without being recreated.
    stale = os.path.join(downloaded, "plex.svg")
    os.chmod(stale, 0o600)
    fixed = icons_mod.IconStore(fresh, lb, os.path.join(work, "s6.json"),
                                log=lambda m: None).ensure_readable()
    check("a 0600 icon in a library directory is repaired", fixed >= 1, fixed)
    check("the repaired icon is readable",
          os.stat(stale).st_mode & 0o044 != 0, oct(os.stat(stale).st_mode & 0o777))

    # An unusable overlay must fail the run once with the cause, not once per
    # file. Simulated with a path whose parent is a file, so it fails for every
    # user including root.
    blocker = os.path.join(work, "not-a-dir")
    write(blocker, b"x")
    stuck = icons_mod.IconStore(os.path.join(blocker, "icons"), baked,
                                os.path.join(work, "s5.json"), log=lambda m: None)
    check("an unusable overlay reports not writable", stuck.writable() is False)
    check("a usable overlay reports writable", store.writable() is True)
    calls = []
    stuck._get = lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
        AssertionError("should not have reached the network"))
    stuck.start(A)
    stuck._thread.join(10)
    err = stuck.state().get("error", "")
    check("unusable overlay fails with the cause", "cannot write to" in err, err)
    check("unusable overlay names the fix", "docker volume rm" in err, err)
    check("unusable overlay never hits the network", calls == [], calls)

    try:
        store._write(A, "../escape.svg", b"x")
        check("_write refuses a traversing name", False, "no exception raised")
    except ValueError:
        check("_write refuses a traversing name", True)
    try:
        store._write("../escape", "x.svg", b"x")
        check("_write refuses a traversing library key", False, "no exception raised")
    except ValueError:
        check("_write refuses a traversing library key", True)
    check("nothing was written outside the overlay",
          not os.path.exists(os.path.join(work, "escape.svg"))
          and not os.path.exists(os.path.join(work, "escape")))
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
