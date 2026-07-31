#!/usr/bin/env python3
"""Offline checks for the search-suggestion proxy.

Nothing here touches the network: urlopen is stubbed, so what is pinned is the
response parsing (both shapes the engines actually return), the engine
allowlist that keeps this from becoming an open proxy, and the rule that a
broken upstream degrades to "no suggestions" rather than an exception — the
search box has to keep working when the engine is unreachable, which on a
LAN-only box is the normal case.
"""

import io
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import suggest as suggest_mod  # noqa: E402

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("ok    %s" % name)
    else:
        failed += 1
        print("FAIL  %s%s" % (name, "  <- " + str(extra) if extra else ""))


class _Resp(io.BytesIO):
    """Minimal stand-in for what urlopen returns, usable as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def stub(payload, encoding="utf-8"):
    """Point urlopen at a fixed payload and record the URL it was given."""
    seen = {}

    def fake(req, timeout=None):
        seen["url"] = req.full_url if hasattr(req, "full_url") else str(req)
        seen["timeout"] = timeout
        if isinstance(payload, Exception):
            raise payload
        body = payload if isinstance(payload, bytes) else payload.encode(encoding)
        return _Resp(body)

    urllib.request.urlopen = fake
    return seen


_real_urlopen = urllib.request.urlopen

# ── parsing ─────────────────────────────────────────────────────────────────
# The OpenSearch array form, which is what every engine in ENGINES returns.
seen = stub('["prox",["proxmox","proxy","proximity"]]')
items, err = suggest_mod.fetch("duckduckgo", "prox")
check("the array form parses", items == ["proxmox", "proxy", "proximity"], items)
check("no error on a good response", err is None, err)
check("the query reaches the upstream URL", "prox" in seen["url"], seen["url"])
check("a timeout is set", seen["timeout"] == suggest_mod.TIMEOUT, seen["timeout"])

# Some engines answer with [text, type] pairs rather than bare strings.
stub('["prox",[["proxmox",0],["proxy",1]]]')
items, _ = suggest_mod.fetch("duckduckgo", "prox")
check("pair-shaped entries unwrap to text", items == ["proxmox", "proxy"], items)

# Duplicates would render as repeated rows in the dropdown.
stub('["a",["one","one","two"]]')
items, _ = suggest_mod.fetch("duckduckgo", "a")
check("duplicates are dropped", items == ["one", "two"], items)

# Blank and non-string entries must not reach the dropdown as empty rows.
stub('["a",["one","","  ",null,3,"two"]]')
items, _ = suggest_mod.fetch("duckduckgo", "a")
check("blank and non-string entries are skipped", items == ["one", "two"], items)

stub('["a",["s%d" % i for i in range(1)]]'.replace("%", "%%"))  # malformed JSON
items, err = suggest_mod.fetch("duckduckgo", "a")
check("malformed JSON yields nothing, not an exception", items == [], items)

for shape in ("{}", "[]", '["only"]', '["a","not-a-list"]', "null", '"text"'):
    stub(shape)
    items, _ = suggest_mod.fetch("duckduckgo", "a")
    check("unexpected shape %s yields nothing" % shape, items == [], items)

# Google answers in ISO-8859-1, so a non-UTF-8 body must still decode.
stub("[\"caf\xe9\",[\"caf\xe9 noir\"]]".encode("latin-1"))
items, err = suggest_mod.fetch("google", "cafe")
check("a latin-1 response still decodes", items == ["caf\xe9 noir"], (items, err))

# The dropdown is capped, so the response has to be too.
stub('["a",[%s]]' % ",".join('"s%d"' % i for i in range(50)))
items, _ = suggest_mod.fetch("duckduckgo", "a")
check("the list is capped at MAX_SUGGESTIONS",
      len(items) == suggest_mod.MAX_SUGGESTIONS, len(items))

# ── the engine allowlist ────────────────────────────────────────────────────
# The whole point of taking a key rather than a URL: there is no way to make
# this fetch an arbitrary host, so it cannot be turned into an open proxy.
called = {"n": 0}


def _boom(req, timeout=None):
    called["n"] += 1
    raise AssertionError("urlopen must not be called for an unknown engine")


urllib.request.urlopen = _boom
items, err = suggest_mod.fetch("nope", "prox")
check("an unknown engine is refused", err == "unknown engine", err)
check("...without any outbound request", called["n"] == 0, called["n"])
items, err = suggest_mod.fetch("https://evil.example/?q=", "prox")
check("a URL as the engine key is refused", err == "unknown engine", err)

# Every engine offered in the dashboard's picker must resolve to a template
# here, or the toggle would be shown for an engine that returns nothing.
for key in ("duckduckgo", "google", "bing", "startpage"):
    check("%s has a suggestion endpoint" % key, key in suggest_mod.ENGINES)
for template in suggest_mod.ENGINES.values():
    check("every template has a query slot: %s" % template, "%s" in template)
    check("every template is https: %s" % template, template.startswith("https://"))

# ── failure is not an exception ─────────────────────────────────────────────
stub(urllib.error.URLError("connection refused"))
items, err = suggest_mod.fetch("duckduckgo", "prox")
check("an unreachable engine returns no items", items == [], items)
check("...and says why", err and "unreachable" in err, err)

stub(OSError("reset by peer"))
items, err = suggest_mod.fetch("duckduckgo", "prox")
check("a socket error is caught too", items == [] and err, (items, err))

# An empty query is not worth a request.
urllib.request.urlopen = _boom
called["n"] = 0
items, err = suggest_mod.fetch("duckduckgo", "   ")
check("a blank query makes no request", items == [] and err is None and called["n"] == 0,
      (items, err, called["n"]))

# An over-long query is truncated rather than passed through, so a paste cannot
# push a huge string at the upstream.
seen = stub('["a",["x"]]')
suggest_mod.fetch("duckduckgo", "q" * (suggest_mod.MAX_QUERY + 500))
check("an over-long query is truncated",
      seen["url"].count("q") <= suggest_mod.MAX_QUERY + 20, len(seen["url"]))

# A query with URL metacharacters must be encoded, not injected into the
# template's own parameters.
seen = stub('["a",["x"]]')
suggest_mod.fetch("duckduckgo", "a&type=json#x /b")
for raw in ("&type=json", "#", " "):
    check("%r is encoded, not passed raw" % raw,
          raw not in seen["url"].split("?", 1)[1].split("&type=list")[0],
          seen["url"])

urllib.request.urlopen = _real_urlopen
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
