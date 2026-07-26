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

result = subprocess.run(
    [sys.executable, "localize.py", "firehome.html", "index.html"],
    cwd=stage, capture_output=True, text=True,
)
if result.returncode != 0:
    print(result.stdout + result.stderr)
    sys.exit("localize.py failed with only the files the Dockerfile copies")
print("ok    localize.py runs with only those files")
PY

python3 - "$ROOT" "$WORK/stage/index.html" <<'PY'
import json, re, sys
root, built = sys.argv[1], sys.argv[2]
html = open(built, encoding="utf-8").read()

tpl = json.loads(re.search(r'<script type="__bundler/template">(.*?)</script>',
                           html, re.S).group(1))
print("ok    template is still valid JSON (%d chars)" % len(tpl))

shim = open(root + "/storage-shim.js", encoding="utf-8").read()
got = re.search(r"<script>\n(.*?)\n</script>", tpl, re.S).group(1)
assert got == shim, "shim did not survive escaping intact"
print("ok    storage-shim.js round-trips byte-identically")

assert html.count("</script>") == 5, "injection broke out of the template tag"
print("ok    outer script tags intact")

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
# The one deliberate survivor is the URL field's format hint, not dashboard data.
assert tpl.count("example.com") == 1, "unexpected example.com references"
assert 'placeholder="URL' in tpl, "the URL field's example hint went missing"
assert ".app-grid .add-app:only-child{opacity:1!important}" in tpl, "empty-state CSS missing"
print("ok    placeholder apps and bookmarks emptied")

for host in ("cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"):
    assert host not in html, "%s still referenced" % host
print("ok    no remaining external hosts")
PY

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
