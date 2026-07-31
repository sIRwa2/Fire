#!/bin/sh
# Post-deploy checks for the running stack. Run on the docker box after a pull:
#
#     ./verify.sh                       # against http://localhost:8080
#     BASE=http://192.168.0.108:8080 ./verify.sh
#
# Everything here is non-destructive: the config round-trip writes under a
# scratch key and removes it again, leaving your dashboard config untouched.
#
# Two things it cannot check, because they need a human:
#   * that the dashboard actually renders and its icons appear
#   * that config follows you to a second browser or device
# For those, open the page, confirm no amber banner, run fireHomeStorage.dump()
# in the console, change something, reload, then load it on your phone.

BASE=${BASE:-http://localhost:8080}
COMPOSE=${COMPOSE:-docker compose}
pass=0
fail=0

ok()   { pass=$((pass + 1)); printf 'ok    %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf 'FAIL  %s\n' "$1"; [ -n "$2" ] && printf '        %s\n' "$2"; }
have() { command -v "$1" >/dev/null 2>&1; }

# check <name> <expected> <actual>
check() {
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2], got [$3]"; fi
}

code() { curl -s -o /dev/null -w '%{http_code}' "$BASE$1"; }

# Occurrences of a pattern in a file inside a running container.
# grep -c prints its count but exits 1 when that count is zero, so the pipeline
# swallows the status — a `|| echo 0` fallback would append a second line and
# turn a legitimate 0 into "0\n0". Empty output means the exec itself failed.
count_in() {
    $COMPOSE exec -T "$1" grep -c "$2" "$3" 2>/dev/null | tr -d '\r' | head -1
}

echo "== containers =="
if have docker; then
    for svc in firehome api; do
        cid=$($COMPOSE ps -q "$svc" 2>/dev/null)
        if [ -z "$cid" ]; then
            bad "$svc container is running"
            continue
        fi
        state=$(docker inspect -f '{{.State.Status}}' "$cid")
        check "$svc container is running" running "$state"
    done
    api_cid=$($COMPOSE ps -q api 2>/dev/null)
    if [ -n "$api_cid" ]; then
        health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$api_cid")
        check "api reports healthy" healthy "$health"
    fi
    echo
    echo "== image contents =="
    check "nginx config is valid" "0" \
        "$($COMPOSE exec -T firehome nginx -t >/dev/null 2>&1; echo $?)"
    check "storage shim is in the served page" "1" \
        "$(count_in firehome fireHomeStorage /usr/share/nginx/html/index.html)"
    check "no CDN reference in the served page" "0" \
        "$(count_in firehome jsdelivr /usr/share/nginx/html/index.html)"
    check "page-wrap layout CSS is in the served page" "1" \
        "$(count_in firehome 'width:90%!important' /usr/share/nginx/html/index.html)"
    icons=$($COMPOSE exec -T firehome sh -c 'ls /usr/share/nginx/html/icons | wc -l' 2>/dev/null | tr -d ' \r')
    if [ "$icons" -gt 3000 ] 2>/dev/null; then
        ok "icon set present ($icons files)"
    else
        bad "icon set present" "found [$icons], expected >3000"
    fi
else
    echo "(docker not on PATH — skipping container and image checks)"
fi

echo
echo "== http =="
check "page responds"                200 "$(code /)"
check "icon resolves locally"        200 "$(code /icons/plex.svg)"
check "unknown icon 404s"            404 "$(code /icons/definitely-not-real.svg)"
check "favicon.svg served"           200 "$(code /favicon.svg)"
check "favicon.ico served"           200 "$(code /favicon.ico)"
check "apple-touch-icon served"      200 "$(code /apple-touch-icon.png)"
check "healthz"                      200 "$(code /healthz)"
# The dc-runtime's un-hydrated placeholders; should be absorbed, not 404.
check "template placeholders absorbed" 200 "$(code '/%7B%7B%20app.iconSrc%20%7D%7D')"

ctype=$(curl -s -o /dev/null -w '%{content_type}' "$BASE/favicon.svg")
check "favicon.svg content type" "image/svg+xml" "$ctype"

# The links sit inside the bundle's JSON-escaped template, so match on the
# hrefs rather than on attribute syntax that carries backslashes.
page=$(curl -s "$BASE/")
if printf '%s' "$page" | grep -qF '/favicon.svg' &&
   printf '%s' "$page" | grep -qF '/apple-touch-icon.png'; then
    ok "favicon links are in the page"
else
    bad "favicon links are in the page"
fi
if printf '%s' "$page" | grep -qF '<title>Flame++<'; then
    ok "page title is set"
else
    bad "page title is set"
fi

echo
echo "== config store =="
KEY=__verify_probe
initial=$(curl -s "$BASE/api/config")
case "$initial" in
    *'"rev"'*) ok "GET /api/config responds" ;;
    *) bad "GET /api/config responds" "got [$initial]" ;;
esac

curl -s -X PUT "$BASE/api/config" -H 'Content-Type: application/json' \
     -d "{\"data\":{\"$KEY\":{\"probe\":true}}}" >/dev/null
if curl -s "$BASE/api/config" | grep -q '"probe"'; then
    ok "write round-trips through the proxy"
else
    bad "write round-trips through the proxy"
fi

if have docker; then
    $COMPOSE restart api >/dev/null 2>&1
    i=0
    while [ $i -lt 60 ]; do
        [ "$(code /api/config)" = "200" ] && break
        i=$((i + 1)); sleep 0.5
    done
    if curl -s "$BASE/api/config" | grep -q '"probe"'; then
        ok "config survives an api restart (volume persists)"
    else
        bad "config survives an api restart (volume persists)"
    fi
fi

# Clean up the probe key without touching real config.
curl -s -X PUT "$BASE/api/config" -H 'Content-Type: application/json' \
     -d "{\"data\":{\"$KEY\":null}}" >/dev/null
if curl -s "$BASE/api/config" | grep -q '"probe"'; then
    bad "probe key removed"
else
    ok "probe key removed"
fi

echo
echo "== icon set =="
icons=$(curl -s "$BASE/api/icons")
# The first "count" in the reply, not the last: the nested status now carries one
# per icon library, and sed's greedy .* would take mdi's 0 as the installed total.
count=$(printf '%s' "$icons" | grep -o '"count": *[0-9]*' | head -1 | grep -o '[0-9]*')
if [ "${count:-0}" -gt 3000 ] 2>/dev/null; then
    ok "icon list served from the api ($count)"
else
    bad "icon list served from the api" "count=[$count]"
fi
# The overlay sits in front of the baked set; either source satisfies this.
check "an icon resolves through the overlay" 200 "$(code /icons/plex.svg)"
# 690 upstream icons have no svg and arrive as webp, so nginx has to serve that
# extension with the right type — the page loads them through <img>.
raster=$(printf '%s' "$icons" | tr ',' '\n' | grep -o '"[^"]*\.webp"' | head -1 | tr -d '"')
if [ -n "$raster" ]; then
    check "a webp icon resolves" 200 "$(code "/icons/$raster")"
    check "a webp icon has the right content type" "image/webp" \
        "$(curl -s -o /dev/null -w '%{content_type}' "$BASE/icons/$raster")"
else
    echo "note  no webp icons installed yet — run Check for updates to fetch them"
fi
# nginx decodes %XX and collapses .. before matching a location — which is why
# the {{ }} rule catches %7B%7B — so /icons/..%2Findex.html simply becomes
# /index.html and serves the homepage. That is correct, and asserting 404 for it
# was wrong. What has to hold is narrower and more useful: nothing outside the
# document root is reachable, and the icons route cannot be talked into serving
# something that is not an icon. Status codes vary (400 above the root, 404
# otherwise), so the property under test is simply "never 200".
# The escapes must be percent-encoded and deep enough to actually reach /.
# curl collapses a plain ../ in the path before sending, so '/icons/../../etc/passwd'
# leaves the client as '/etc/passwd' and tests nothing; %2F survives to the server.
# And a two-level escape cannot climb out of the document root, so it passes
# whatever the server does. Both mistakes were in the first version of this check,
# which is why it is written out at length: against a deliberately naive server
# these probes return 200 and the shallow ones do not.
esc='..%2F..%2F..%2F..%2F..%2F..%2F..%2F..%2F'
for probe in "/icons/${esc}etc%2Fpasswd" "/icons/${esc}etc%2Fhostname" \
             "/icons/..%2F..%2Fnginx.conf"; do
    got=$(code "$probe")
    if [ "$got" = "200" ]; then
        bad "path escape refused: $probe" "returned 200"
    else
        ok "path escape refused: $probe ($got)"
    fi
done
check "the icons route will not serve a non-icon" 404 "$(code '/icons/index.html')"
if printf '%s' "$icons" | grep -q '"running"'; then
    ok "icon updater reports status"
else
    bad "icon updater reports status"
fi

# Several icon libraries can be installed at once, and the panel renders whatever
# the api lists — in preference order, since that is what decides who wins a name
# more than one provides.
status=$(curl -s "$BASE/api/icons/status")
keys=$(printf '%s' "$status" | grep -o '"key": *"[a-z0-9-]*"' | sed 's/.*"\([a-z0-9-]*\)"$/\1/')
nkeys=$(printf '%s\n' "$keys" | grep -c .)
if [ "${nkeys:-0}" -ge 3 ] 2>/dev/null; then
    ok "icon libraries listed ($(printf '%s' "$keys" | tr '\n' ' '))"
else
    bad "icon libraries listed" "found [$nkeys]: $keys"
fi
# The set baked into the image has to be attributed to the library that shipped
# it: it lives flat in the icon root rather than in a subdirectory, and if that
# attribution breaks, its count reads 0 and its next update re-downloads all of it.
shipped=$(printf '%s' "$status" | tr '{' '\n' | grep '"key": *"homarr"' |
    sed -n 's/.*"count": *\([0-9]*\).*/\1/p' | head -1)
if [ "${shipped:-0}" -gt 3000 ] 2>/dev/null; then
    ok "the shipped library reports its icons ($shipped)"
else
    bad "the shipped library reports its icons" "count=[$shipped]"
fi
# Re-posting the order that is already in force: proves the endpoint is reachable
# and validating, and changes nothing.
if [ -n "$keys" ]; then
    body=$(printf '%s' "$keys" | sed 's/.*/"&"/' | tr '\n' ',' | sed 's/,$//')
    # The key name is quoted too, so the array has to be isolated before the keys
    # are picked out of it.
    echoed=$(curl -s -X POST "$BASE/api/icons/config" \
        -H 'Content-Type: application/json' -d "{\"preference\":[$body]}" |
        tr -d '\n' | sed -n 's/.*"preference": *\[\([^]]*\)\].*/\1/p' |
        grep -o '[a-z0-9-]\{2,\}' | tr '\n' ' ' | sed 's/ *$//')
    check "the library order round-trips" "$(printf '%s' "$keys" | tr '\n' ' ')" "$echoed"
fi
# An unknown key must never become a directory under the overlay.
check "an unknown library is refused" 400 "$(curl -s -o /dev/null -w '%{http_code}' \
    -X POST "$BASE/api/icons/update" -H 'Content-Type: application/json' \
    -d '{"library":"../evil"}')"
# A named volume mounted where the image has no directory comes out root-owned,
# which the unprivileged api cannot write to — every download then fails EACCES.
# The site-icon fetcher shares the api's network path; a bad URL must be refused
# rather than attempted.
badfav=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/favicon" \
    -H 'Content-Type: application/json' -d '{"url":"file:///etc/passwd"}')
check "site-icon fetcher refuses a non-http url" 400 "$badfav"

if printf '%s' "$icons" | grep -q '"writable": *true'; then
    ok "icon storage is writable"
else
    bad "icon storage is writable" \
        "recreate the volume: docker compose down && docker volume rm \
\$(basename \$PWD)_firehome-icons && docker compose up -d --build"
fi

if have docker; then
    snaps=$($COMPOSE exec -T api sh -c 'ls /data/history 2>/dev/null | wc -l' 2>/dev/null | tr -d ' \r')
    if [ "$snaps" -gt 0 ] 2>/dev/null; then
        ok "history snapshots are being written ($snaps)"
    else
        bad "history snapshots are being written" "found [$snaps]"
    fi
fi

echo
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
