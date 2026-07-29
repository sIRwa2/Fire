#!/usr/bin/env python3
"""Offline checks for the site-icon fetcher.

No network: the transport is stubbed so the parts worth pinning are covered —
which <link rel> wins, that relative hrefs resolve, that only real images are
stored, and that non-http(s) URLs are refused before anything is fetched.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import favicon as favicon_mod  # noqa: E402

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("ok    %s" % name)
    else:
        failed += 1
        print("FAIL  %s%s" % (name, "  <- " + str(extra) if extra else ""))


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
ICO = b"\x00\x00\x01\x00" + b"\x00" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 30
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'

check("sniffs png", favicon_mod._sniff(PNG) == ".png")
check("sniffs ico", favicon_mod._sniff(ICO) == ".ico")
check("sniffs webp", favicon_mod._sniff(WEBP) == ".webp")
check("sniffs svg", favicon_mod._sniff(SVG) == ".svg")
check("rejects html", favicon_mod._sniff(b"<!DOCTYPE html><html>") is None)
check("rejects a RIFF that is not webp",
      favicon_mod._sniff(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 20) is None)

work = tempfile.mkdtemp()
try:
    store = favicon_mod.FaviconStore(os.path.join(work, "fav"), log=lambda m: None)

    PAGE = b"""<html><head>
      <link rel="shortcut icon" href="/small.ico">
      <link rel="icon" sizes="32x32" href="/mid.png">
      <link rel="apple-touch-icon" sizes="180x180" href="https://cdn.example/big.png">
      <link rel="stylesheet" href="/not-an-icon.css">
    </head><body></body></html>"""

    fetched = []

    def stub(url, limit, truncate=False):
        fetched.append(url)
        if url == "http://site.test/":
            return url, PAGE
        if url.endswith("big.png"):
            return url, PNG
        raise OSError("not reachable")

    store._get = stub
    cands = store.candidates("http://site.test/")
    check("apple-touch-icon outranks the rest",
          cands[0] == "https://cdn.example/big.png", cands[:2])
    check("sizes order the remaining rel=icon entries",
          cands[1].endswith("/mid.png") and cands[2].endswith("/small.ico"), cands)
    check("relative hrefs resolve against the page",
          "http://site.test/mid.png" in cands, cands)
    check("a stylesheet link is ignored", not any("css" in c for c in cands), cands)
    # Declared icons come first, then the conventional guesses, /favicon.ico
    # leading those. It is no longer last outright — further well-known paths
    # follow it — but it must always be present and rank below anything declared.
    check("/favicon.ico follows the declared icons",
          cands.index("http://site.test/favicon.ico") == 3, cands)
    check("the conventional guesses are all appended",
          [c.rsplit("/", 1)[-1] for c in cands[3:]]
          == [p.lstrip("/") for p in favicon_mod.WELL_KNOWN], cands[3:])

    name, source = store.fetch("http://site.test/")
    check("stores the best candidate", name and name.endswith(".png"), name)
    check("reports where it came from", source == "https://cdn.example/big.png", source)
    check("the file landed", os.path.isfile(os.path.join(store.dir, name)))
    check("stored world-readable for nginx",
          os.stat(os.path.join(store.dir, name)).st_mode & 0o044 != 0)

    # A site with no usable image must not leave anything behind.
    empty = favicon_mod.FaviconStore(os.path.join(work, "fav2"), log=lambda m: None)
    empty._get = lambda url, limit, truncate=False: (url, b"<!DOCTYPE html><html></html>")
    name2, _ = empty.fetch("http://nothing.test/")
    check("no icon found means nothing stored", name2 is None, name2)

    # Re-fetching the same site replaces rather than accumulates.
    store._get = stub
    before = sorted(os.listdir(store.dir))
    store.fetch("http://site.test/")
    check("re-fetching does not accumulate copies",
          sorted(os.listdir(store.dir)) == before, os.listdir(store.dir))

    # A page over the size cap must still be parsed, not discarded. This is the
    # motogp.com case: 541 KB of HTML with </head> at byte 7036, so the icon was
    # in hand and thrown away, falling through to a /favicon.ico that 404s.
    big = favicon_mod.FaviconStore(os.path.join(work, "fav3"), log=lambda m: None)
    HUGE = (b"<html><head><link rel='apple-touch-icon' sizes='180x180' href='/big.png'>"
            b"</head><body>" + b"x" * (favicon_mod.PAGE_BYTES * 2) + b"</body></html>")

    def big_get(url, limit, truncate=False):
        if url.endswith(".png"):
            return url, PNG
        if len(HUGE) > limit and not truncate:
            raise ValueError("larger than %d bytes" % limit)
        return url, HUGE[:limit]

    big._get = big_get
    cands = big.candidates("http://huge.test/page")
    check("an oversized page is still parsed for icons",
          cands and cands[0] == "http://huge.test/big.png", cands[:2])
    name3, _ = big.fetch("http://huge.test/page")
    check("and its icon is stored", name3 and name3.endswith(".png"), name3)

    # Conventional paths are tried when the page declares nothing.
    bare = favicon_mod.FaviconStore(os.path.join(work, "fav4"), log=lambda m: None)
    bare._get = lambda url, limit, truncate=False: (
        (url, PNG) if url.endswith("/apple-touch-icon.png")
        else (url, b"<html><head></head></html>") if url.endswith("/page")
        else (_ for _ in ()).throw(OSError("404")))
    guesses = bare.candidates("http://bare.test/page")
    check("well-known paths are tried in order",
          [g.rsplit("/", 1)[-1] for g in guesses][:2] == ["favicon.ico", "apple-touch-icon.png"],
          guesses)
    name4, src4 = bare.fetch("http://bare.test/page")
    check("an undeclared apple-touch-icon is found",
          name4 and src4.endswith("/apple-touch-icon.png"), (name4, src4))

    for bad in ("file:///etc/passwd", "ftp://host/x", "not-a-url", "javascript:alert(1)"):
        try:
            store.fetch(bad)
            check("refuses %s" % bad, False, "no exception")
        except ValueError:
            check("refuses %s" % bad, True)
finally:
    shutil.rmtree(work, ignore_errors=True)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
