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

# Every path a volume is mounted at inside the api must exist in the api image.
# Where it does not, Docker creates the mountpoint and the fresh volume comes out
# root-owned, which the unprivileged service cannot write to — the failure is a
# silent EACCES per operation, and it has already happened twice (/data/icons,
# then /data/favicons). Checked structurally so a third volume cannot repeat it.
compose = open(os.path.join(root, "docker-compose.yml"), encoding="utf-8").read()
api_block = re.search(r"^  api:\n(.*?)(?=^  \S|\Z)", compose, re.S | re.M).group(1)
# Only the volumes: list — security_opt entries like no-new-privileges:true look
# just like a mount to a naive split on ":".
vol_block = re.search(r"^    volumes:\n(.*?)(?=^    \S|\Z)", api_block, re.S | re.M).group(1)
mounted = set()
for line in vol_block.splitlines():
    m = re.match(r"\s*-\s+([^:\s]+):([^:\s]+)", line)
    if m and not m.group(1).startswith("."):      # named volumes, not bind mounts
        mounted.add(m.group(2))
api_dockerfile = open(os.path.join(root, "api", "Dockerfile"), encoding="utf-8").read()
made = set()
for m in re.finditer(r"mkdir\s+-p\s+([^\\\n&]+)", api_dockerfile):
    for path in m.group(1).split():
        # mkdir -p creates the ancestors too, so /data/icons covers /data.
        parts = path.rstrip("/").split("/")
        for i in range(2, len(parts) + 1):
            made.add("/".join(parts[:i]))
for target in sorted(mounted):
    assert target.rstrip("/") in made, (
        "docker-compose mounts a volume at %s but the api image never creates it, "
        "so the volume will be root-owned and unwritable" % target
    )
print("ok    every api volume mountpoint exists in the image (%s)" % ", ".join(sorted(mounted)))

# The search bar navigates with location.assign, not a form submit, so
# form-action can stay fully closed. If it is ever relaxed to carry a search
# origin again, the bar has silently regressed to a native submit — which
# cannot reach a user-supplied engine, and fails with no error when it tries.
nginx = open(os.path.join(root, "nginx.conf"), encoding="utf-8").read()
csp = re.search(r'add_header Content-Security-Policy "([^"]*)"', nginx).group(1)
form_action = re.search(r"form-action ([^;\"]*)", csp).group(1).strip()
assert form_action == "'none'", (
    "form-action is %s, not 'none'; the search bar navigates with "
    "location.assign and needs no form-action allowance" % form_action
)
print("ok    CSP form-action stays 'none' (search navigates via JS)")

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

swipe = open(root + "/header-swipe.js", encoding="utf-8").read()
assert swipe in html, "header-swipe.js is not present verbatim"
print("ok    header-swipe.js embedded intact")

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

# The search form submits by itself, so the parts that make that work — the GET
# action, name="q" and type="search" — are the whole feature. type="text" would
# still submit but drop the native clear button, which is the point of it.
search = re.search(r'<form class="hdr-search"[^>]*>(.*?)</form>', tpl, re.S)
assert search, "search form is gone"
form = re.search(r'<form class="hdr-search"([^>]*)>', tpl).group(1)
# No action/method: a native submit would bypass the chosen engine entirely, and
# is what the CSP's form-action 'none' blocks with no error.
for gone in ("action=", "method="):
    assert gone not in form, (
        "the search form carries %s, so it would submit natively, ignore the "
        "engine setting and be blocked by form-action: %s" % (gone, form.strip())
    )
assert "runSearch" in form, "the search form has no submit handler"
assert 'type="search"' in search.group(1), "search input is no longer type=search"
assert 'name="q"' in search.group(1), "search input lost name=q; the engine gets no query"
assert 'aria-label=' in search.group(1), "search input has no accessible name"
# It sits immediately above <header>, inside .page-wrap — that sibling
# relationship is what the reveal selector keys on, so a move breaks the hover.
assert tpl.index('class="hdr-search"') > tpl.index('class="page-wrap"'), \
    "search bar left page-wrap; the reveal selector would never match"
assert tpl.index('class="hdr-search"') < tpl.index("<header"), "search bar is not above the header"
assert tpl.index('class="hdr-search"') < tpl.index("{{ dateStr }}"), "search bar is not above the date"
assert ".hdr-search input{" in tpl, "search bar CSS missing"

# Hidden until hovered, but the box has to stay: display:none or a height
# collapse here would move the date and greeting on every hover.
assert ".hdr-search{margin:0 0 14px;opacity:0" in tpl, \
    "search bar is not hidden by opacity; the reveal would shift the header"
assert "display:none" not in tpl.split(".hdr-search{")[1][:200], \
    "search bar hidden with display:none, which does not reserve its height"
assert ".hdr-search:hover{opacity:1}" in tpl, \
    "the search bar's own strip no longer reveals it"

# The engine picker: every preset offered, the query placeholder substituted
# rather than appended, and the custom URL field gated on a custom engine.
localize_py = open(os.path.join(root, "localize.py"), encoding="utf-8").read()
engines = re.findall(r'^SEARCH_ENGINES = \((.*?)^\)', localize_py, re.S | re.M)[0]
keys = re.findall(r'\("([a-z]+)",', engines)
assert len(keys) >= 4, "expected at least four search engines, found %s" % keys
for key in keys:
    assert '<option value="%s">' % key in tpl, "engine %s is not offered in the panel" % key
assert 'sc-camel-on-change="{{ chooseEngine }}"' in tpl, "the engine select has no handler"
# A preset whose URL is blank must be one of the user-supplied ones, or picking
# it would leave the bar dead with no field to fix it in.
supplied = re.search(r"^SEARCH_CUSTOM_KEYS = \(([^)]*)\)", localize_py, re.M).group(1)
for key, url in re.findall(r'\("([a-z]+)", "[^"]*", "([^"]*)"\)', engines):
    if not url:
        assert '"%s"' % key in supplied, (
            "engine %s has no URL and is not in SEARCH_CUSTOM_KEYS, so choosing "
            "it would leave the search bar inert with nowhere to set a URL" % key
        )
assert "searchUrlChange" in tpl, "the custom search URL field has no handler"
assert "%s is where the query goes" in tpl, "the custom URL field lost its hint"
# The handler must block the native submit unconditionally: without it the form
# falls back to a GET at the dashboard's own URL and the query is lost.
assert "e.preventDefault()" in tpl.split("runSearch = (e) =>")[1][:120], \
    "runSearch does not preventDefault first; a native submit would reload the page"
assert "window.location.assign" in tpl, "runSearch does not navigate"
print("ok    %d search engines offered, custom URL gated and handled" % len(keys))
assert ".hdr-search:focus-within{opacity:1}" in tpl, \
    "search bar would fade out while being typed in"
# The strip is the only trigger: revealing from the header block as well put a
# second, disjoint hover zone 50px below the bar.
assert ":has(> header:hover)" not in tpl, \
    "the header block is a second hover zone; the reveal should be the strip alone"
# A touch device has no hover, so the bar must be exempted or it is invisible
# and unreachable on a phone.
touch = re.search(r"@media \(hover:none\)\{(.*?)\}", tpl, re.S).group(1)
assert ".hdr-search," in touch, "search bar is not forced visible on touch"
assert ".hdr-search input::-webkit-search-cancel-button{" in tpl, \
    "the native clear button is unstyled; WebKit hides it by default"
print("ok    search bar above the header, hover-revealed without shifting it")

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
# Grey labels on the three add affordances, with the + glyphs left on the accent.
assert ".add-app span:last-child," in tpl and "#9aa3ad!important}" in tpl, \
    "add-label colour CSS missing"
assert ".bm-cat > button > span:first-child{color:var(--accent)!important}" in tpl, \
    "the bookmark + glyph would grey along with its button"
print("ok    add-button labels greyed, + glyphs left on the accent")

# Drag-sortable categories. The typing is what keeps a bookmark from being
# captured by .bm-grid, which encloses every category list.
for needed in ('data-container="groups"', 'data-accepts="group"',
               'data-drag-kind="group"', 'class="cat-grip"',
               "(c.dataset.accepts || 'item') === kind",
               "(n.dataset.dragKind || 'item') === kind",
               "this._press.kind === 'group'",
               "e.currentTarget.closest('[data-drag-id]')"):
    assert needed in tpl, "category drag piece missing: %s" % needed
# The grip has to be inside the editable block, since dragging is lock-gated.
grip_at = tpl.index('class="cat-grip"')
assert tpl.rindex('{{ editable }}', 0, grip_at) > tpl.rindex('</sc-if>', 0, grip_at), \
    "the drag grip is outside the editable block"
print("ok    categories drag-sortable, drag typed by kind")

# The cards are padded so the hover highlight has room, with the horizontal
# padding cancelled by an equal negative margin so the content does not shift.
card = re.search(r"\.app-grid > a\[data-drag-id\],\.app-grid > \.add-app\{([^}]*)\}", tpl)
assert card, "the app card rule is missing"
decls = dict(d.split(":", 1) for d in card.group(1).replace("!important", "").split(";") if d)
assert decls.get("padding") == "9px 12px", "card padding missing: %s" % decls
# Equal and opposite, or every icon and title drifts right of the section heading.
pad_x = decls["padding"].split()[1]
assert decls.get("margin-left") == "-" + pad_x and decls.get("margin-right") == "-" + pad_x, \
    "the side bleed does not cancel the horizontal padding: %s" % decls
# The bleed is half the grid's 24px column gap, so neighbours touch but never
# overlap; any more and their hit areas would collide.
assert int(pad_x.rstrip("px")) * 2 <= 24, "the side bleed would overlap the next card"
assert decls.get("margin-bottom") == "0", \
    "a bottom margin on top of the padding would push the row pitch to 80px"
print("ok    app cards padded, bleed cancels the shift")

# The link target moves from an authoring prop to a persisted user setting. The
# ?? matters: once chosen, sameTab:false must not fall back to the prop default.
assert "sameTab: t.sameTab ?? !(this.props.openInNewTab ?? true)" in tpl, \
    "link-target default no longer layers over the prop"
assert "linkTarget: th.sameTab ? '_self' : '_blank'," in tpl, \
    "linkTarget does not read the setting"
assert "toggleSameTab = () => this.setTheme({ sameTab: !this.theme().sameTab });" in tpl
assert "Open links in a new tab" in tpl and "{{ newTabTrack }}" in tpl, \
    "the settings row is missing"
# It has to live in the theme, which is what the config service persists.
assert "sameTab" in re.search(r"  theme\(\) \{.*?\n  \}", tpl, re.S).group(0), \
    "sameTab is not part of theme(), so it would not persist"
print("ok    link target is a persisted setting")

# Falling back to the site's own icon. The fetch is server-side because the CSP
# refuses a cross-origin <img>, so the page only ever references /favicons/.
assert "if (name.slice(0, 4) === 'fav:') return '/favicons/' + name.slice(4);" in tpl, \
    "iconUrl cannot resolve a fetched site icon"
assert "siteIcon = (id, url, groupId)" in tpl, "the site-icon fetch is missing"
assert "if (!m.icon) this.siteIcon(entry.id, url, null);" in tpl, \
    "apps do not request a site icon"
assert "this.siteIcon(m.editId || (saved && saved.id), url, m.groupId);" in tpl, \
    "bookmarks do not request a site icon"
# The placeholder must only be replaced if it is still the placeholder.
assert "e.icon === 'homepage'" in tpl, \
    "a site icon could overwrite an icon chosen in the meantime"
print("ok    site icon fallback wired")

# The lock hides the hover-revealed controls unless the setting says otherwise.
assert 'data-locked="{{ lockedFlag }}"' in tpl, "the root carries no locked flag"
assert "lockedFlag: (s.locked && !th.lockedHover) ? '1' : '0'," in tpl, "locked flag logic missing"
assert "lockedHover: !!t.lockedHover" in tpl, "lockedHover is not part of theme(), so it would not persist"
assert "Show hover controls when locked" in tpl, "the settings row is missing"
# pointer-events matters as much as opacity: these are revealed by opacity alone,
# so an invisible button would still be clickable.
locked_rule = re.search(r'\[data-locked="1"\][^{]*\{([^}]*)\}', tpl).group(1)
assert "opacity:0!important" in locked_rule and "pointer-events:none!important" in locked_rule, locked_rule
# Each selector must carry its grid to reach (0,3,0); without that it loses to
# EMPTY_STATE_CSS's :only-child rule and the add button stays visible when locked.
assert '[data-locked="1"] .app-grid .add-app,' in tpl, "locked selector is too weak to win the tie"
# ...and equal specificity is broken by order, so this rule has to come last.
style = re.search(r"<style>(.*?)</style>", tpl, re.S).group(1)
assert style.rstrip().endswith("pointer-events:none!important}"), \
    "the locked rule is no longer last, so EMPTY_STATE_CSS may win the tie"
# The header must stay usable, or the layout could not be unlocked again.
assert ".hdr-actions" not in locked_rule, "the lock would disable its own button"
print("ok    lock hides the hover controls, with a setting to keep them")

# The lock is a stored preference, not a per-visit gesture. It lived only in
# component state, so it reset to unlocked on every reload.
assert "this.state.locked = !!this.state.theme.locked;" in tpl, \
    "the lock is not restored on load"
assert "this.setTheme({ locked });" in tpl, \
    "toggleLock does not persist, so the lock would reset on reload"
# It has to be read after this.state.theme is assigned, or it would always be false.
assert tpl.index("this.state.theme = theme || {};") < tpl.index("this.state.locked = !!"), \
    "the lock is restored before the theme is assigned"
print("ok    lock state is persisted")

# The 18 themes are a dropdown, not a nine-row grid of buttons.
assert '<select value="{{ themeIndex }}"' in tpl, "the theme select is missing"
assert '<option value="{{ t.i }}">{{ t.name }}</option>' in tpl, "the options are missing"
assert 'button data-i="{{ t.i }}"' not in tpl, "the old theme grid is still there"
# A controlled select needs to know the active preset, and the theme is stored as
# colours rather than an index — so it is matched, with '' meaning Custom.
assert "themeIndex: (() => {" in tpl and "return i < 0 ? '' : String(i);" in tpl, \
    "themeIndex does not resolve the active preset"
assert '<option value="">Custom</option>' in tpl, \
    "no Custom entry, so hand-picked colours would blank the select"
assert "chooseTheme = (e) => {" in tpl and "if (raw === '') return;" in tpl, \
    "choosing Custom should be a no-op"
# Every select needs its own data-k. The mobile-layout dropdown arrived second and
# silently stole querySelector('select') from the theme tests, which then asserted
# happily against the wrong element.
selects = re.findall(r"<select ([^>]*)>", tpl)
keys = [re.search(r'data-k="([^"]+)"', s) for s in selects]
assert all(keys), "a select carries no data-k, so tests cannot address it unambiguously"
assert len({k.group(1) for k in keys}) == len(keys), "two selects share a data-k"
print("ok    themes are a dropdown (%d selects, each addressable)" % len(selects))

# Five font sizes, covering everything but the date and greeting. They travel as
# custom properties because the values are per-user and this stylesheet is static.
for prop, default in (("--fs-section", "19px"), ("--fs-app-title", "14.5px"),
                      ("--fs-app-desc", "11.2px"), ("--fs-cat-name", "15px"),
                      ("--fs-bm-title", "14.5px")):
    assert "var(%s,%s)!important" % (prop, default) in tpl, \
        "no rule for %s, or its fallback drifted from the stock size" % prop
    assert "setProperty('%s'" % prop in tpl, "%s is never published by applyTheme" % prop
assert tpl.count('sc-camel-on-change="{{ fontSize }}"') == 5, "expected five size inputs"
# The card's edit/delete overlay is another direct span child of the anchor, so
# without :not([data-nodrag]) its icons get resized along with the title.
assert ".app-grid a[data-drag-id] > span:not([data-nodrag]) > span:first-child" in tpl, \
    "the app-title selector would also catch the edit overlay"
# A bookmark title has no size of its own; the anchor is what carries it.
assert ".bm-cat a[data-drag-id]{font-size:var(--fs-bm-title" in tpl, \
    "bookmark titles are sized on the wrong element"
assert "if (!isFinite(size) || size < 8 || size > 40) return;" in tpl, \
    "an absurd size would be accepted"
print("ok    five font sizes wired to custom properties")

# Mobile. Both grids carried hardcoded column counts, so a phone got four 69px
# app columns and five 50px category columns.
mobile = re.search(r"@media \(max-width:768px\)\{(.*?)\}(?=\[data-locked|\Z)", tpl, re.S)
assert mobile, "no mobile media query"
block = mobile.group(1)
assert ".bm-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}" in block, \
    "categories do not sit two per row on a phone"
for mode in ("both", "icons", "text"):
    assert '[data-mobile="%s"]' % mode in block, "no rule for the %s layout" % mode
assert 'data-mobile="{{ mobileLayout }}"' in tpl, "the root carries no mobile flag"
assert "mobileLayout: t.mobileLayout || 'both'" in tpl, "mobile layout is not part of theme()"
# Icons-only must not leave the touch overlay covering the icon it exists to show.
assert '[data-mobile="icons"] .app-grid a[data-drag-id] > span[data-nodrag]{display:none!important}' in block, \
    "the edit overlay would cover a 58px icon in icons-only mode"
# Everything must sit inside the media query, or desktop changes too.
assert "@media" in tpl[:tpl.index('[data-mobile="both"]')], "mobile rules escaped the media query"
# Two columns is ~163px on a phone, narrow enough that a long category name
# pushed the grip and the delete control out of the column. The row has to wrap, and
# the name has to be allowed to break — outside the media query, because five
# desktop columns are narrower still.
assert ".bm-cat > div:first-child{flex-wrap:wrap}" in tpl, \
    "the category header cannot wrap, so a long name pushes its controls out"
assert ".bm-cat .cat-grip,.bm-cat .cat-del{flex:0 0 auto}" in tpl, \
    "the category controls can be squeezed"
assert "overflow-wrap:anywhere" in tpl, "a single long word can still overflow the column"
print("ok    responsive mobile layouts wired")

# The category delete is the bin alone — the widest part of that header row was
# the word DELETE. Losing the visible text is only safe because the control keeps
# an accessible name, so the title is asserted alongside.
cat_del = re.search(r'<span class="cat-del"(.*?)</span>', tpl, re.S).group(1)
assert 'title="Delete category"' in cat_del, \
    "the bin has no accessible name now that the label is gone"
assert "<svg" in cat_del, "the bin icon is gone"
assert "Delete<" not in tpl and ">Delete" not in tpl, "the DELETE label is still rendered"
print("ok    category delete is the bin icon alone")

# Swipe-to-reveal for the header buttons. Every part of this is a way for the
# lock button to become unreachable, so the gating is asserted, not assumed.
swipe_css = re.search(r"@media \(max-width:768px\) and \(hover:none\)\{(.*?)\}\s*(?=\[data-locked|@media|\Z)",
                      tpl, re.S)
assert swipe_css, "no header-swipe media query"
sblock = swipe_css.group(1)
assert "(hover:none)" in tpl, "a narrow desktop window would lose the buttons to a gesture it cannot make"
for rule in ('[data-hdr-swipe="1"] .hdr-row{position:relative;overflow:hidden;touch-action:pan-y}',
             "transform:translateX(calc(var(--hdr-reveal,120px) - var(--hdr-slide,0px)))"):
    assert rule in sblock, "header swipe rule missing: %s" % rule
# Nothing may hide the buttons unless the script that reveals them is running.
for line in sblock.split("}"):
    if ".hdr-row" in line and "@media" not in line:
        assert '[data-hdr-swipe="1"]' in line, \
            "a header swipe rule is not gated on the script: %s" % line
assert "root.setAttribute('data-hdr-swipe', '1')" in html, \
    "the script never enables its own CSS"
assert "touch-action:pan-y" in sblock, "a vertical scroll would be swallowed by the header"
print("ok    header swipe-to-reveal gated on script, touch and width")

print("ok    header lock/settings buttons flattened, locked state preserved")

# The picker takes its list from the service so an update shows up without a
# rebuild, and so the baked list's 3222-vs-3232 mismatch stops mattering.
for needed in ("refreshIcons = () => fetch('/api/icons')", "this.refreshIcons();",
               "fetch('/api/icons/update', { method: 'POST' })",
               "updateIcons: this.updateIcons,", "{{ iconSummary }}",
               "{{ iconBtnLabel }}", ">Icons</div>"):
    assert needed in tpl, "icon-update patch missing: %s" % needed
# Rasters carry their extension in the name, so iconUrl must not append .svg to
# them, and the title suggestion must not turn adblock.webp into "Adblock.webp".
assert "/\\.(svg|webp|png)$/i.test(name) ? '' : '.svg'" in tpl, \
    "iconUrl would append .svg to a raster name"
assert "icon.replace(/\\.(svg|webp|png)$/i, '')" in tpl, \
    "the title suggestion would keep the file extension"
# The URL suggests a title too, sharing titleAuto with the icon suggestion so the
# two cooperate instead of fighting. Both entry points must reach the same helper.
assert "labelFromUrl = (raw) => {" in tpl, "no URL -> label helper"
assert "titleFromUrl = (raw) => this.labelFromUrl(raw)" in tpl, "no URL -> title helper"
# Both entry points — the URL field and the clipboard prefill — go through the
# same suggestion, so they cannot drift apart.
assert "modal: f === 'url' ? this.suggestFromUrl(m, v) : m" in tpl, \
    "the URL field does not suggest anything"
assert "modal: this.suggestFromUrl({ ...s.modal, url: text }, text) }" in tpl, \
    "a clipboard-prefilled URL does not suggest anything"
# Both stay suggestions: they only write when the field is empty or holds a value
# this dialog put there.
assert "if (!m.title.trim() || m.titleAuto) {" in tpl, \
    "a URL could overwrite a title the user typed"
assert "if (!(m.q || '').trim() || m.qAuto) {" in tpl, \
    "a URL could overwrite a search the user typed"
# Without the host-shaped gate, typing a URL dribbles G, Gi, Git into the field.
assert "/^[a-z]+:\\/\\//i.test(url) || /[.:]/.test(url)" in tpl, \
    "the URL guess would fire on every keystroke"
# Filtering to a label nothing matches would leave the icon picker empty.
assert "const pool = label ? (this.state.icons || []).filter(n => n.includes(label)) : [];" in tpl, \
    "the grid could be filtered down to nothing"
# The icon is auto-selected on an exact name only. The pool is substring-matched,
# so "plex" leads with perplexity-dark and "nginx" with nginx-proxy-manager —
# taking pool[0] would attach the wrong icon, and Enter would store it.
assert "const exact = pool.find(n => n.replace(/\\.(svg|webp|png)$/i, '') === label);" in tpl, \
    "the auto-selected icon is not restricted to an exact name"
assert "pool[0]" not in tpl, "the first substring match is being attached as the icon"
assert "if (!m.icon || m.iconAuto) {" in tpl, \
    "a URL could replace an icon the user picked"
assert "titleAuto: suggest, iconAuto: false } };" in tpl, \
    "a hand-picked icon is not marked as deliberate, so a later URL would replace it"
# The grid filters off m.q, which is why picking an icon does not unfilter it.
assert "const q = (m.q || (m.titleAuto ? '' : m.title) || '').trim().toLowerCase();" in tpl, \
    "the grid's filter expression changed; the URL pre-filter rides on m.q"
# Matches are substrings, so left alphabetical the icon asked for sinks: reddit
# behind libreddit, plex behind five perplexity variants. Exact first, then
# prefix, then the rest — and only the filtered copy is sorted, never s.icons.
assert "const rank = (n) => stem(n) === q ? 0 : stem(n).startsWith(q) ? 1 : 2;" in tpl, \
    "icon matches are not ranked, so an exact match can sit below substring ones"
assert "s.icons.filter(n => n.includes(q)).sort((a, b) => rank(a) - rank(b))" in tpl, \
    "the ranked order is not applied to the grid"
assert "s.icons.sort" not in tpl, "sorting s.icons in place would mutate state"
# Enter commits the dialog, off the same window handler that owns Escape. Both
# guards are load-bearing: without the modal check, Enter is swallowed page-wide
# and a focused app link can no longer be activated from the keyboard; without the
# button check, Enter on a focused Cancel saves instead of cancelling.
assert "if (e.key === 'Enter' && this.state.modal && !e.isComposing" in tpl, \
    "Enter does not commit the dialog, or is not gated on a modal being open"
assert "!(e.target && e.target.closest && e.target.closest('button'))" in tpl, \
    "Enter on a focused Cancel button would save instead of cancelling"
# Enter must not be able to save what the Add button refuses, so the two
# conditions have to stay identical.
assert "saveDisabled = !m || !m.title.trim() || !m.url.trim();" in tpl and \
    "if (!m || !m.title.trim() || !m.url.trim()) return;" in tpl, \
    "saveModal's guard and saveDisabled have drifted apart"
# The baked list must survive as the fallback for when the service is unreachable.
assert 'id="__icons_data"' in tpl, "baked icon list is gone; nothing to fall back to"
print("ok    icon list served from the api, update button wired")

# The picker tiles are targeted by data-icon, not by the runtime-generated .scpx
# class, whose base36 suffix moves whenever render order changes.
assert 'data-icon="{{ ic.name }}"' in tpl, "icon tiles lost the data-icon hook"
assert "span[data-icon],span[data-icon]:hover{" in tpl, "icon tile CSS missing"
# Flattening the tiles also flattened the selected state, so the chosen icon is
# outlined back in — on the grid tile and on the dialog's icon field, which have
# to agree or they read as two unrelated boxes.
assert 'data-sel="{{ ic.sel }}"' in tpl, "the selected tile carries no marker"
assert "sel: name === m.icon ? '1' : '0'," in tpl, "nothing computes which tile is selected"
assert 'data-icon-field="{{ iconSel }}"' in tpl, "the icon field carries no marker"
assert "iconSel: (m && m.icon) ? '1' : '0'," in tpl, "nothing computes the icon field's state"
for rule in ('span[data-icon][data-sel="1"]{border-color:var(--accent)!important}',
             '[data-icon-field="1"]{border-color:var(--accent)!important}'):
    assert rule in tpl, "selected-icon outline missing: %s" % rule
# (0,2,1) beats the flattening rule's (0,1,1), so the outline does not depend on
# which order the two rules end up in.
assert tpl.index("span[data-icon],span[data-icon]:hover{") < \
    tpl.index('span[data-icon][data-sel="1"]'), "the outline should follow the flattening rule"
print("ok    icon tile styling injected, selection outlined")

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
echo "== site icon fetcher (offline) =="
python3 "$ROOT/tests/favicon_test.py"

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
