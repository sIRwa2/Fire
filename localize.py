#!/usr/bin/env python3
"""Strip firehome.html's remaining internet dependencies.

The exported bundle is nearly self-contained: React, the dc-runtime, the Inter
woff2 faces and 24 icons all ride inside the manifest as gzipped base64. Two
things still reach out to the internet at runtime:

  1. Component.iconUrl() falls back to cdn.jsdelivr.net for any icon that is
     not one of the 24 bundled ones — i.e. every icon the user picks in the UI.
  2. Two <link rel=preconnect> hints open DNS+TLS connections to Google's font
     hosts, even though the fonts themselves are inlined.

Both are rewritten/removed here so the container never talks to the internet.
Icons are served from /icons/ by nginx out of the local svg/ directory.

It also injects storage-shim.js, which moves the dashboard's config off
localStorage and onto the config service. See that file for why.

Each edit asserts its expected hit count: if a future re-export of firehome.html
changes these strings, the build fails loudly instead of silently shipping a
page that phones home.
"""

import json
import os
import re
import sys

ICON_CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/"
ICON_LOCAL = "/icons/"
SHIM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage-shim.js")

TEMPLATE_RE = re.compile(
    r'(<script type="__bundler/template">)(.*?)(</script>)', re.S
)

# The template ships as a JSON string inside <script type="__bundler/template">,
# so its markup is backslash-escaped in the raw file: \" for quotes, \n for
# newlines. Patching the raw text (rather than re-serializing the JSON) keeps
# the bundler's own </ escaping of nested </script> intact.
PRECONNECT_RE = re.compile(r'<link rel=\\"preconnect\\"[^>]*?>(?:\\n)?')


def inject_shim(html: str) -> str:
    """Insert storage-shim.js into the inner template's <head>.

    The template lives as a JSON string inside <script type="__bundler/template">,
    so the injected markup has to carry that string's escaping: JSON escapes for
    quotes/newlines/backslashes, plus the bundler's own <\\u002F encoding of "</"
    so a nested </script> cannot terminate the outer tag early.
    """
    shim = open(SHIM_PATH, encoding="utf-8").read()
    tag = "<script>\n" + shim + "\n</script>"
    escaped = json.dumps(tag)[1:-1].replace("</", "<\\u002F")

    match = TEMPLATE_RE.search(html)
    if not match:
        raise ValueError("no __bundler/template section found")
    open_tag, body, close_tag = match.groups()

    n = body.count("<head>")
    if n != 1:
        raise ValueError("expected 1 <head> in the template, found %d" % n)
    body = body.replace("<head>", "<head>" + escaped, 1)

    return html[: match.start()] + open_tag + body + close_tag + html[match.end():]


def main(src: str, dst: str) -> int:
    html = open(src, encoding="utf-8").read()

    n = html.count(ICON_CDN)
    if n != 1:
        print(f"localize: expected 1 icon-CDN reference, found {n}", file=sys.stderr)
        return 1
    html = html.replace(ICON_CDN, ICON_LOCAL)

    html, n = PRECONNECT_RE.subn("", html)
    if n != 2:
        print(f"localize: expected 2 preconnect hints, found {n}", file=sys.stderr)
        return 1

    # Belt and braces: nothing outside the manifest's own bundled-resource keys
    # (which are lookup ids, never fetched) may reference a remote host. The
    # user's own service links live in localStorage, not here; the *.example.com
    # placeholders in defaultApps() are meant to be edited in the UI.
    leaks = [
        h
        for h in ("cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com")
        if h in html
    ]
    if leaks:
        print(f"localize: unexpected remaining references: {leaks}", file=sys.stderr)
        return 1

    try:
        html = inject_shim(html)
    except ValueError as exc:
        print(f"localize: {exc}", file=sys.stderr)
        return 1

    open(dst, "w", encoding="utf-8").write(html)
    print(f"localize: wrote {dst} ({len(html)} chars)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: localize.py <src.html> <dst.html>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
