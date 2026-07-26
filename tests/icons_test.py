#!/usr/bin/env python3
"""Offline checks for the icon updater.

Nothing here touches the network: the parts worth pinning are the blob-sha
format the whole diff depends on, the name validation that keeps third-party
strings from escaping the overlay directory, and the baked/overlay precedence.
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

work = tempfile.mkdtemp()
try:
    baked = os.path.join(work, "baked")
    overlay = os.path.join(work, "overlay")
    os.makedirs(baked)
    os.makedirs(overlay)
    open(os.path.join(baked, "plex.svg"), "wb").write(b"<svg>baked</svg>")
    open(os.path.join(baked, "shared.svg"), "wb").write(b"<svg>old</svg>")
    open(os.path.join(overlay, "shared.svg"), "wb").write(b"<svg>new</svg>")
    open(os.path.join(overlay, "brand-new.svg"), "wb").write(b"<svg>new</svg>")
    open(os.path.join(baked, "notes.txt"), "wb").write(b"ignore me")

    store = icons_mod.IconStore(overlay, baked, os.path.join(work, "state.json"),
                                log=lambda m: None)

    check("names unions both directories and strips .svg",
          store.names() == ["brand-new", "plex", "shared"], store.names())
    check("non-svg files are ignored", "notes" not in store.names())

    shas = store.local_shas()
    check("overlay shadows baked for the same name",
          shas["shared.svg"] == icons_mod.blob_sha(b"<svg>new</svg>"))
    check("baked-only files are still seen",
          shas["plex.svg"] == icons_mod.blob_sha(b"<svg>baked</svg>"))

    # A missing baked mount must degrade, not crash.
    gone = icons_mod.IconStore(overlay, os.path.join(work, "absent"),
                               os.path.join(work, "s2.json"), log=lambda m: None)
    check("missing baked directory degrades quietly",
          gone.names() == ["brand-new", "shared"], gone.names())

    check("state is empty before any run", store.state() == {}, store.state())
    check("status reports the installed count",
          store.status()["installed"] == 3, store.status())

    # Reporting must describe what happened, not what was attempted. A run whose
    # downloads all fail used to report them as "updated", because new/changed
    # were taken from the size of the todo list before anything was fetched.
    fresh = os.path.join(work, "overlay2")
    os.makedirs(fresh)
    reporting = icons_mod.IconStore(fresh, baked, os.path.join(work, "s3.json"),
                                    log=lambda m: None)
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
    reporting.start()
    reporting._thread.join(10)
    last = reporting.state()
    check("all-failed run reports 0 new", last.get("new") == 0, last)
    check("all-failed run reports 0 changed", last.get("changed") == 0, last)
    check("all-failed run reports the failures", last.get("failed") == 4, last)
    check("all-failed run records what it attempted", last.get("attempted") == 4, last)
    # One download per name: the duplicate webp/png copies of plex are not fetched,
    # and non-format directories and nested paths are ignored.
    check("prefers one format per name",
          last.get("formats") == {"svg": 2, "webp": 1, "png": 1}, last.get("formats"))
    check("nothing was written on failure", os.listdir(fresh) == [], os.listdir(fresh))

    # And the happy path still counts new vs changed correctly.
    def ok_get(url, timeout=None, limit=None):
        if "commits" in url or "trees" in url:
            return fake_get(url)
        return b"<svg>upstream</svg>"

    reporting._get = ok_get
    reporting.start()
    reporting._thread.join(10)
    last = reporting.state()
    check("happy path counts the new files", last.get("new") == 3, last)
    check("happy path counts the changed file", last.get("changed") == 1, last)
    check("happy path reports no failures", last.get("failed") == 0, last)
    check("every chosen format landed in the overlay",
          sorted(os.listdir(fresh)) == ["newone.svg", "plex.svg", "pngonly.png",
                                        "rasteronly.webp"], os.listdir(fresh))
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
    modes = sorted(oct(os.stat(os.path.join(fresh, n)).st_mode & 0o777)
                   for n in os.listdir(fresh))
    check("downloaded icons are world-readable", set(modes) == {"0o644"}, modes)

    # And files left at 0600 by an older version are repaired in place, so an
    # existing volume recovers without being recreated.
    stale = os.path.join(fresh, "plex.svg")
    os.chmod(stale, 0o600)
    fixed = icons_mod.IconStore(fresh, baked, os.path.join(work, "s6.json"),
                               log=lambda m: None).ensure_readable()
    check("a 0600 icon is repaired", fixed == 1, fixed)
    check("the repaired icon is readable",
          os.stat(stale).st_mode & 0o044 != 0, oct(os.stat(stale).st_mode & 0o777))

    # An unusable overlay must fail the run once with the cause, not once per
    # file. Simulated with a path whose parent is a file, so it fails for every
    # user including root.
    blocker = os.path.join(work, "not-a-dir")
    open(blocker, "wb").write(b"x")
    stuck = icons_mod.IconStore(os.path.join(blocker, "icons"), baked,
                               os.path.join(work, "s5.json"), log=lambda m: None)
    check("an unusable overlay reports not writable", stuck.writable() is False)
    check("a usable overlay reports writable", store.writable() is True)
    calls = []
    stuck._get = lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
        AssertionError("should not have reached the network"))
    stuck.start()
    stuck._thread.join(10)
    err = stuck.state().get("error", "")
    check("unusable overlay fails with the cause", "cannot write to" in err, err)
    check("unusable overlay names the fix", "docker volume rm" in err, err)
    check("unusable overlay never hits the network", calls == [], calls)

    try:
        store._write("../escape.svg", b"x")
        check("_write refuses a traversing name", False, "no exception raised")
    except ValueError:
        check("_write refuses a traversing name", True)
    check("nothing was written outside the overlay",
          not os.path.exists(os.path.join(work, "escape.svg")))
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
