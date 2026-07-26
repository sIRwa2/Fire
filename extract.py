#!/usr/bin/env python3
"""Unpack the self-extracting bundle into a static site at build time.

firehome.html ships as a loader plus a base64+gzip manifest: on every page load
the browser decodes ~630 KB across 43 resources, mints a blob URL for each,
substitutes those into the 120 KB template, parses it, swaps documentElement and
re-runs every script. That is the "Unpacking…" message, none of it is cacheable,
and it happens again on every refresh.

Doing the same work here leaves a plain HTML document plus real asset files, so
the browser parses it once like any other page and caches the fonts and
JavaScript normally.

It also fixes a WebKit problem. The loader builds the document with DOMParser and
then adopts it via documentElement.replaceWith(); WebKit does not reliably attach
the stylesheet of a <style> element adopted from another document. Inline styles
— which is most of this page — survive, but the page's <style> block does not,
and that block holds every :hover rule as well as everything localize.py injects.
A natively parsed document has no such problem.

Usage: extract.py <patched-bundle.html> <out-dir>
"""

import json
import os
import re
import sys
import gzip
import base64

MIME_EXT = {
    "text/javascript": ".js",
    "font/woff2": ".woff2",
    "image/svg+xml": ".svg",
}

ASSET_DIR = "assets"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# The 24 icons bundled into the manifest are not shipped: svg/ already serves all
# 3222 at /icons/, and Component.iconUrl falls through to that path when
# __resources has no entry for a name.
#
# The other ext_resources ids — the React and react-dom CDN URLs — must be kept.
# The runtime resolves them through window.__resources:
#
#     function cdnScriptFor(url, sri) {
#       const v = window.__resources ? window.__resources[url] : void 0;
#       return typeof v === "string" && v ? { src: v } : { src: url, integrity: sri };
#     }
#
# so dropping them would make it inject a <script> pointing at unpkg.com.
SKIPPED_ID_PREFIX = "ic_"


def island(html, name, required=True):
    match = re.search(
        r'<script type="__bundler/%s">(.*?)</script>' % name, html, re.S
    )
    if not match:
        if required:
            raise ValueError("bundle has no %s island" % name)
        return None
    return json.loads(match.group(1))


def decode(entry):
    raw = base64.b64decode(entry["data"])
    return gzip.decompress(raw) if entry.get("compressed") else raw


def main(src, out_dir):
    html = open(src, encoding="utf-8").read()

    manifest = island(html, "manifest")
    ext_resources = island(html, "ext_resources", required=False) or []
    page_order = island(html, "page_order", required=False) or []
    template = island(html, "template")

    # Nested page bundles ride as about:blank#<uuid> frame markers that only the
    # in-browser loader can mint. Refuse rather than emit a page with dead frames.
    if page_order:
        raise ValueError(
            "bundle contains %d nested page(s); this extractor handles only "
            "single-page bundles" % len(page_order)
        )

    kept_ids = {
        e["id"]: e["uuid"]
        for e in ext_resources
        if not e["id"].startswith(SKIPPED_ID_PREFIX)
    }

    # A uuid needs a file if the template points at it, or if it backs an
    # ext_resource we are keeping.
    needed = {u for u in manifest if u in template} | set(kept_ids.values())

    # Everything dropped must be genuinely unreferenced.
    for uuid in set(manifest) - needed:
        if uuid in template:
            raise ValueError("dropping %s but the template references it" % uuid)

    os.makedirs(os.path.join(out_dir, ASSET_DIR), exist_ok=True)
    paths = {}
    written = 0
    for uuid in sorted(needed):
        entry = manifest[uuid]
        ext = MIME_EXT.get(entry["mime"])
        if ext is None:
            raise ValueError("no extension mapped for mime %r" % entry["mime"])
        name = uuid + ext
        with open(os.path.join(out_dir, ASSET_DIR, name), "wb") as fh:
            fh.write(decode(entry))
        paths[uuid] = "/%s/%s" % (ASSET_DIR, name)
        written += 1

    for uuid, path in paths.items():
        template = template.replace(uuid, path)

    # The loader strips these because blob URLs from an opaque origin fail a
    # CORS-mode SRI check. Same-origin paths would be fine, but the hashes
    # describe the CDN copies and the bytes are ours now, so drop them anyway.
    template = re.sub(r'\s+integrity="[^"]*"', "", template, flags=re.I)
    template = re.sub(r'\s+crossorigin="[^"]*"', "", template, flags=re.I)

    resource_map = {rid: paths[uuid] for rid, uuid in kept_ids.items()}
    script = (
        "<script>window.__resources = %s;</script>"
        % json.dumps(resource_map).replace("</", "<\\/")
    )

    # After <head> so the doctype stays first and quirks mode is avoided.
    head = re.search(r"<head[^>]*>", template, re.I)
    if not head:
        raise ValueError("template has no <head>")
    cut = head.end()
    template = template[:cut] + script + template[cut:]

    # Any manifest uuid still in the text means a reference we failed to rewrite.
    # The asset paths embed the uuid themselves, so blank those out before
    # looking, or every rewritten reference would read as a miss.
    probe = template
    for path in paths.values():
        probe = probe.replace(path, "")
    for uuid in UUID_RE.findall(probe):
        if uuid in manifest:
            raise ValueError("unresolved reference to %s remains" % uuid)

    index = os.path.join(out_dir, "index.html")
    with open(index, "w", encoding="utf-8") as fh:
        fh.write(template)

    print(
        "extract: %s (%.1f KB) + %d assets, from a %.1f KB bundle"
        % (
            index,
            len(template.encode("utf-8")) / 1024,
            written,
            os.path.getsize(src) / 1024,
        )
    )
    print(
        "extract: dropped %d bundled icon(s); /icons/ serves them"
        % (len(manifest) - written)
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: extract.py <bundle.html> <out-dir>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
