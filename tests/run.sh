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

# Sources of every COPY in the first build stage, i.e. exactly the files
# localize.py can see inside the container.
stage1 = re.split(r"^FROM ", dockerfile, flags=re.M)[1]
sources = []
for line in stage1.splitlines():
    match = re.match(r"COPY\s+(?!--from)(.*)$", line.strip())
    if match:
        sources += match.group(1).split()[:-1]  # last token is the destination
assert sources, "no COPY sources found in the first build stage"
print("ok    build stage copies: %s" % " ".join(sources))

os.makedirs(stage)
for src in sources:
    full = os.path.join(root, src)
    assert os.path.exists(full), "Dockerfile COPYs %s, which does not exist" % src
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
