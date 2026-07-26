#!/bin/sh
# Verify the two build-time pieces without needing Docker. Worth running after
# every re-export of firehome.html.
#
#   1. localize.py still finds its patch targets, and the shim survives the
#      round-trip through the bundle's JSON/</script> escaping. This runs in a
#      directory holding ONLY the files the Dockerfile's first stage COPYs, so
#      a missing COPY fails here rather than during a build.
#   2. storage-shim.js honours the contract the dashboard depends on, driven
#      against a real instance of api/server.py.
#
# Needs python3 and node on the PATH.
set -e

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PORT=${FIREHOME_TEST_PORT:-8097}
trap 'kill $API 2>/dev/null || true; rm -rf "$WORK"' EXIT INT TERM

echo "== localize.py, against the Dockerfile's build-stage file set =="
python3 - "$ROOT" "$WORK/stage" <<'PY'
import os, re, shutil, subprocess, sys

root, stage = sys.argv[1], sys.argv[2]
dockerfile = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()

def copy_sources(block):
    """Local sources of every COPY in a build stage (skipping --from stages)."""
    out = []
    for line in block.splitlines():
        match = re.match(r"COPY\s+(?!--from)(.*)$", line.strip())
        if match:
            out += match.group(1).split()[:-1]  # last token is the destination
    return out


stages = re.split(r"^FROM ", dockerfile, flags=re.M)[1:]

# Every COPY in the file must reference something that exists, or the build
# dies late with a confusing error.
for src in {s for stage in stages for s in copy_sources(stage)}:
    assert os.path.exists(os.path.join(root, src)), \
        "Dockerfile COPYs %s, which does not exist" % src
print("ok    every COPY source exists")

# Only the first stage's files are visible to localize.py in the container.
sources = copy_sources(stages[0])
assert sources, "no COPY sources found in the first build stage"
print("ok    build stage copies: %s" % " ".join(sources))

os.makedirs(stage)
for src in sources:
    full = os.path.join(root, src)
    if os.path.isdir(full):
        shutil.copytree(full, os.path.join(stage, src))
    else:
        shutil.copy2(full, stage)

for step in (["localize.py", "firehome.html", "patched.html"],
             ["extract.py", "patched.html", "dist"]):
    result = subprocess.run([sys.executable] + step, cwd=stage,
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout + result.stderr)
        sys.exit("%s failed with only the files the Dockerfile copies" % step[0])
    print("ok    %s runs with only those files" % step[0])
PY

python3 - "$ROOT" "$WORK/stage/dist" <<'PY'
import json, re, sys
import os
root, dist = sys.argv[1], sys.argv[2]
tpl = html = open(os.path.join(dist, "index.html"), encoding="utf-8").read()

assert html.lstrip().startswith("<!DOCTYPE html>"), "extracted page lost its doctype"
assert "__bundler" not in html, "extracted page still carries bundler islands"
assert "Unpacking" not in html, "extracted page still carries the loader UI"
print("ok    extracted page is plain HTML (%.1f KB)" % (len(html) / 1024))

# Nothing may be left pointing at a blob-minted resource, and the React entries
# must be present or the runtime injects a script tag aimed at unpkg.com.
assets = sorted(os.listdir(os.path.join(dist, "assets")))
assert len(assets) >= 3, "expected the extracted JavaScript in assets/"
for name in assets:
    assert name in html or name.endswith(".woff2"), "orphan asset: %s" % name
resmap = re.search(r"window\.__resources = (\{.*?\});", html).group(1)
for url in ("react.production.min.js", "react-dom.production.min.js"):
    assert url in resmap, "__resources lost %s; runtime would fetch the CDN" % url
assert "/assets/" in resmap
print("ok    %d assets emitted, React mapped locally" % len(assets))

shim = open(root + "/storage-shim.js", encoding="utf-8").read()
assert shim in html, "storage-shim.js is not present verbatim"
print("ok    storage-shim.js embedded intact")

# The exported template carries no title or icon links, so a missing injection
# means a blank tab icon and a /favicon.ico probe on every load.
for needed in ('<title>firehome<', 'rel="icon" href="/favicon.svg"',
               'rel="apple-touch-icon"'):
    assert needed in tpl, "head injection missing: %s" % needed
print("ok    title and favicon links injected")

# The dynamic margin depends on width:90% reaching an element that still
# carries margin:0 auto — either half missing means no margin at all.
assert 'class="page-wrap"' in tpl, "page-wrap element is gone"
assert ".page-wrap{box-sizing:border-box!important;width:90%!important}" in tpl, \
    "page-wrap layout CSS missing"
wrap = re.search(r'class="page-wrap"[^>]*style="([^"]*)"', tpl).group(1)
assert "margin:0 auto" in wrap, "page-wrap lost margin:0 auto, so width:90% centres nothing"
print("ok    page-wrap dynamic margin wired up (%s + width:90%%)" % wrap)

# The header offset relies on being a child of .page-wrap, and on the parent
# keeping a top padding so the margin can't collapse through it.
assert ".page-wrap > header{margin-top:50px!important}" in tpl, "header offset CSS missing"
assert tpl.index("<header") > tpl.index('class="page-wrap"'), "header left page-wrap"
assert re.search(r"padding:\s*\d+px", wrap), "page-wrap lost its padding; header margin would collapse"
print("ok    header offset wired up")

# A clean slate has to be genuinely empty: wiping stored config falls back to
# these defaults, and componentDidMount persists them again on the next load.
for name in ("defaultApps", "defaultGroups"):
    body = re.search(r"static " + name + r"\(\) \{(.*?)\n  \}", tpl, re.S).group(1)
    assert body.strip() == "return [];", "%s still returns data: %s" % (name, body.strip()[:60])
for gone in ("bitwarden.example.com", "news.ycombinator.com",
             "store.steampowered.com", "Todoist"):
    assert gone not in tpl, "placeholder data still present: %s" % gone
assert tpl.count("example.com") == 0, "example.com is still referenced somewhere"
# The dialog has no labels, so the URL field must keep some placeholder.
assert 'placeholder="URL"' in tpl, "the URL field lost its placeholder entirely"
print("ok    placeholder apps, bookmarks and URL hint emptied")

# The flat add buttons must be unconditional: scoping them to :only-child meant
# the outline and tinted square came back the moment an app was added.
assert ".add-app,.new-cat{border-color:transparent!important;outline:none!important}" in tpl, \
    "add-button border and focus ring are not both cleared unconditionally"
assert ".add-app span,.new-cat span{background:none!important}" in tpl, \
    "the + glyph's background is not cleared unconditionally"
assert ":only-child" not in tpl.split(".add-app,.new-cat{")[0][-400:], \
    "outline removal looks re-scoped to the empty state"
# Only the visibility and the offset stay scoped to the empty dashboard.
assert "margin-top:24px!important" in tpl, "empty-state offset missing"
# The reveal must not depend on :focus-visible: the page pairs the two in one
# selector list, which older Safari drops whole.
assert ".app-grid:hover .add-app{opacity:1}" in tpl, "hover fallback missing"
assert "@supports not selector(:focus-visible)" in tpl, "focus-visible fallback missing"
print("ok    add-button styling unconditional, hover reveal made robust")

# The header's lock and settings buttons, reached through .hdr-actions rather
# than the render-order .scp0/.scp1 classes.
assert 'class=\\"hdr-actions' in tpl or 'class="hdr-actions' in tpl, \
    "hdr-actions container is gone"
for decl in ("border-color:transparent!important", "outline:none!important",
             "background:transparent!important"):
    assert ".hdr-actions button{" in tpl and decl in tpl, \
        "header button CSS missing: %s" % decl
# The locked button keeps its accent plate, keyed on the title. Both halves have
# to hold or the locked state ends up with no indicator at all, since its border
# is gone too.
assert '.hdr-actions button[title^="Layout locked"]{background:var(--accent)!important}' in tpl, \
    "locked-state background is flattened along with the rest"
assert "lockTitle: s.locked ? 'Layout locked" in tpl, \
    "lock title reworded; the locked-state selector would no longer match"
assert "lockBg: s.locked ? 'var(--accent)'" in tpl, \
    "lockBg no longer marks the locked state, which the border used to"
print("ok    header lock/settings buttons flattened, locked state preserved")

# The picker takes its list from the service so an update shows up without a
# rebuild, and so the baked list's 3222-vs-3232 mismatch stops mattering.
for needed in ("refreshIcons = () => fetch('/api/icons')", "this.refreshIcons();",
               "fetch('/api/icons/update', { method: 'POST' })",
               "updateIcons: this.updateIcons,", "{{ iconSummary }}",
               "{{ iconBtnLabel }}", ">Icons</div>"):
    assert needed in tpl, "icon-update patch missing: %s" % needed
# The baked list must survive as the fallback for when the service is unreachable.
assert 'id="__icons_data"' in tpl, "baked icon list is gone; nothing to fall back to"
print("ok    icon list served from the api, update button wired")

# The picker tiles are targeted by data-icon, not by the runtime-generated .scpx
# class, whose base36 suffix moves whenever render order changes.
assert 'data-icon="{{ ic.name }}"' in tpl, "icon tiles lost the data-icon hook"
assert "span[data-icon],span[data-icon]:hover{" in tpl, "icon tile CSS missing"
print("ok    icon tile styling injected")

# Touch devices have no hover, which would leave every editing control
# unreachable. Each hover-revealed hook must be covered by the (hover:none) rule.
touch = re.search(r"@media \(hover:none\)\{(.*?)\}\}", tpl, re.S).group(1)
for hook in (".add-app", ".hdr-actions", ".new-cat", ".cat-del", '[data-nodrag="1"]'):
    assert hook in touch, "hover-revealed control not covered on touch: %s" % hook
assert "opacity:1!important" in touch
# Sanity-check that these really are the hover-revealed set in the page itself.
for rule in (".app-grid:hover .add-app", ".hdr-row:hover .hdr-actions",
             ".bm-grid:hover .new-cat", ".bm-cat:hover .cat-del"):
    assert rule in tpl, "hover rule changed shape, touch fallback may be stale: %s" % rule
print("ok    touch fallback covers every hover-revealed control")

for host in ("cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"):
    assert host not in html, "%s still referenced" % host
print("ok    no remaining external hosts")
PY

echo
echo "== icon updater (offline) =="
python3 "$ROOT/tests/icons_test.py"

echo
echo "== icon -> title suggestion =="
node "$ROOT/tests/suggest-title.test.mjs" "$WORK/stage/dist/index.html"

echo
echo "== storage-shim.js =="
FIREHOME_DATA="$WORK/data" FIREHOME_API_PORT="$PORT" python3 "$ROOT/api/server.py" \
    >"$WORK/api.log" 2>&1 &
API=$!

i=0
while [ $i -lt 50 ]; do
    if curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then break; fi
    i=$((i + 1))
    sleep 0.1
done
if [ $i -eq 50 ]; then echo "config service failed to start:"; cat "$WORK/api.log"; exit 1; fi

FIREHOME_TEST_BASE="http://127.0.0.1:$PORT" node "$ROOT/tests/shim.test.mjs"
