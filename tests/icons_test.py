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
            return ('{"truncated":false,"tree":[{"path":"svg/plex.svg","sha":"%s"},'
                    '{"path":"svg/newone.svg","sha":"%s"}]}'
                    % (upstream_sha, upstream_sha)).encode()
        raise OSError("download refused")  # every file download fails

    reporting._get = fake_get
    reporting.start()
    reporting._thread.join(10)
    last = reporting.state()
    check("all-failed run reports 0 new", last.get("new") == 0, last)
    check("all-failed run reports 0 changed", last.get("changed") == 0, last)
    check("all-failed run reports the failures", last.get("failed") == 2, last)
    check("all-failed run records what it attempted", last.get("attempted") == 2, last)
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
    check("happy path counts the new file", last.get("new") == 1, last)
    check("happy path counts the changed file", last.get("changed") == 1, last)
    check("happy path reports no failures", last.get("failed") == 0, last)
    check("both files landed in the overlay",
          sorted(os.listdir(fresh)) == ["newone.svg", "plex.svg"], os.listdir(fresh))

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
