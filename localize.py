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

It also injects, into the same <head>:

  * storage-shim.js, which moves the dashboard's config off localStorage and
    onto the config service. See that file for why.
  * the favicon links and a <title>. The exported template carries neither, so
    without these the tab shows a blank icon and the bare URL, and the browser
    probes /favicon.ico on every load.

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


HEAD_MARKUP = (
    "<title>firehome</title>"
    '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
    '<link rel="alternate icon" href="/favicon.png" type="image/png" sizes="32x32">'
    '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
)

# Flame — the design this dashboard follows — never declares a margin on its
# container. Layout.module.css is:
#
#     .Container { width: 100%; padding: 20px; margin: 0 auto; }
#     @media (min-width: 769px)  { .Container { width: 90%; } }
#     @media (min-width: 1201px) { .Container { padding: 50px 250px; } }
#
# width:90% plus margin:0 auto makes the auto margins split the leftover 10%,
# so each side is 5% of the viewport rather than a fixed number. That is where a
# measured 75.203px comes from: 5% of a 1504px window. Hardcoding 75px would
# only match at that one width.
#
# The exported template already has the margin:0 auto and the 50px 250px
# padding, but not the width — and with width:auto the auto margins resolve to
# 0, which is why there is no margin at all today.
#
# box-sizing comes along because Flame gets border-box from a global * reset
# that this template has no equivalent of. Under content-box, width:90% plus
# 500px of horizontal padding would overflow the viewport and add a scrollbar.
#
# !important because the element carries an inline style attribute; it has no
# width today, but a re-export that added one would silently win otherwise.
PAGE_WRAP_CSS = (
    ".page-wrap{box-sizing:border-box!important;width:90%!important}"
    "@media (max-width:768px){.page-wrap{width:100%!important}}"
)

# Drop the header 50px. It is the first child of .page-wrap, which has
# padding-top:50px, so the padding stops this margin collapsing through the
# parent and the header ends up 100px from the top.
#
# The header is in normal flow, so everything below it moves down by the same
# 50px. Shifting the header alone would mean taking it out of flow, which would
# leave a gap where it used to be.
HEADER_CSS = ".page-wrap > header{margin-top:50px!important}"

# The bundle ships a demo dashboard: 12 apps pointing at *.example.com and 4
# bookmark groups of public sites. Emptying the defaults is what makes a clean
# slate actually clean — wiping the stored config only falls back to these, and
# componentDidMount() calls saveLS() on mount, so the very next page load
# persists them again and undoes the reset.
EMPTIED_DEFAULTS = ("defaultApps", "defaultGroups")

# The URL field's format hint. The dialog has no labels — every field relies on
# its placeholder — so this keeps a bare "URL" rather than dropping the attribute
# and leaving an unlabelled box. Matches the sibling "Title" placeholder.
URL_HINT_OLD = 'placeholder=\\"URL — e.g. https://plex.example.com\\"'
URL_HINT_NEW = 'placeholder=\\"URL\\"'

# How far the two "add" buttons sit below their heading on an empty dashboard.
EMPTY_ADD_BUTTON_OFFSET = "24px"

# Presentation of the two "add" buttons on an empty dashboard.
#
# Everything here is scoped to :only-child — the empty case — deliberately. Once
# apps exist, .add-app is a grid item sitting alongside real cards, where the
# dashed outline reads as an "add slot" and a top margin would knock it out of
# alignment with its row. Drop the :only-child qualifiers to make it
# unconditional.
#
# sc-for and sc-if compile to React Fragments, which render no DOM node, so an
# empty list leaves the button as the only child — which is what makes
# :only-child identify the empty case exactly. The bookmarks button sits one
# level deeper, inside a <div>.
EMPTY_STATE_CSS = (
    # Both buttons are hover-revealed (opacity:0 until their grid is hovered),
    # so without this an emptied dashboard renders as a blank page.
    ".app-grid .add-app:only-child,"
    ".bm-grid > div:only-child .new-cat{"
    "opacity:1!important;"
    # border-color, not border:none, so the box keeps its exact size.
    "border-color:transparent!important;"
    "margin-top:" + EMPTY_ADD_BUTTON_OFFSET + "!important}"
    # The tinted square behind the + glyph. The label span alongside it has no
    # background of its own, so this is safe to apply to both.
    ".app-grid .add-app:only-child span,"
    ".bm-grid > div:only-child .new-cat span{background:none!important}"
)


# Picking an icon suggests a Title.
#
# The icon names are kebab-case slugs, so "home-assistant" becomes
# "Home Assistant"; a trailing -light/-dark variant suffix is dropped so
# "vim-dark" suggests "Vim". It stays a suggestion: a title the user typed is
# never overwritten, and titleAuto tracks whether the current title came from a
# previous pick so trying different icons keeps updating it.
#
# fieldChange clears titleAuto because any manual edit to the title means the
# user has taken ownership of it.
#
# The icon grid falls back to filtering by the title when the search box is
# empty. Left alone, an autofilled title would make the grid collapse to icons
# matching the name at the moment of the click, which reads as a glitch — so the
# fallback is skipped for a title that was itself autofilled. Typing a title
# first still pre-filters the grid, and editing an existing app is unchanged.
JS_PATCHES = (
    (
        "pickIcon suggests a title",
        "  pickIcon = (e) => this.setState({ modal: { ...this.state.modal,"
        " icon: e.currentTarget.dataset.icon } });",
        "  pickIcon = (e) => {\\n"
        "    const icon = e.currentTarget.dataset.icon;\\n"
        "    const suggestion = icon.replace(/-(light|dark)$/, '').split(/[-_]+/)\\n"
        "      .filter(Boolean).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');\\n"
        "    this.setState(s => {\\n"
        "      const m = s.modal;\\n"
        "      const suggest = !m.title.trim() || m.titleAuto;\\n"
        "      return { modal: { ...m, icon, title: suggest ? suggestion : m.title,"
        " titleAuto: suggest } };\\n"
        "    });\\n"
        "  };",
    ),
    (
        "manual title edit releases the suggestion",
        "  fieldChange = (e) => {\\n"
        "    const f = e.currentTarget.dataset.field, v = e.currentTarget.value;\\n"
        "    this.setState({ modal: { ...this.state.modal, [f]: v } });\\n"
        "  };",
        "  fieldChange = (e) => {\\n"
        "    const f = e.currentTarget.dataset.field, v = e.currentTarget.value;\\n"
        "    this.setState(s => ({ modal: { ...s.modal, [f]: v,"
        " ...(f === 'title' ? { titleAuto: false } : {}) } }));\\n"
        "  };",
    ),
    (
        "an autofilled title does not re-filter the icon grid",
        "      const q = (m.q || m.title || '').trim().toLowerCase();",
        "      const q = (m.q || (m.titleAuto ? '' : m.title) || '').trim().toLowerCase();",
    ),
)

# The icon-picker tiles. Their background and border come from ic.bg / ic.border,
# which highlight the selected icon in the accent colour; the hover background
# comes from a runtime-generated class.
#
# That generated class is what shows up in devtools as .scpx — createPseudoSheet
# names them "scp" + counter.toString(36), so the suffix depends on the order
# pseudo-styles are first rendered and shifts whenever the markup changes. It is
# not a usable hook, hence the data-icon attribute, which is stable and specific
# to these tiles. The generated rules are also force-!important, so beating them
# needs both !important and higher specificity: span[data-icon]:hover is (0,2,1)
# against .scpx:hover at (0,2,0).
#
# Removing these means the grid no longer marks which icon is selected. The 44px
# preview beside the search box still shows the current pick.
ICON_TILE_CSS = (
    "span[data-icon],span[data-icon]:hover{"
    "background:none!important;border-color:transparent!important}"
)

# Every control for editing the dashboard is hover-revealed, by one of two
# mechanisms: four :hover rules in the page's own stylesheet (.add-app,
# .hdr-actions, .new-cat, .cat-del) and two elements that carry an inline
# opacity:0 plus a style-hover the runtime turns into a generated .scpN:hover
# rule (the app card's edit/delete overlay and the bookmark delete).
#
# A touch device has no hover, so on iPhone/iPad none of them can be reached at
# all — the dashboard becomes read-only. (hover: none) matches exactly those
# devices, so pointer machines keep the hover-reveal untouched; an iPad with a
# trackpad reports hover: hover and is likewise unaffected.
#
# !important is required for the two inline opacity:0 elements. The third
# data-nodrag element, the settings close button, has no opacity of its own, so
# including it here is a no-op. The trade-off on touch is that the per-card
# edit/delete buttons are always visible, which is the only way to reach them.
TOUCH_CSS = (
    "@media (hover:none){"
    ".app-grid .add-app,.hdr-row .hdr-actions,.bm-grid .new-cat,.bm-cat .cat-del,"
    '[data-nodrag="1"]{opacity:1!important}'
    "}"
)


def apply_js_patches(body: str) -> str:
    """Apply the behavioural patches, asserting each target appears exactly once."""
    for name, old, new in JS_PATCHES:
        n = body.count(old)
        if n != 1:
            raise ValueError("expected 1 site for %r, found %d" % (name, n))
        body = body.replace(old, new)
    return body


def empty_defaults(body: str) -> str:
    """Rewrite the placeholder default apps/bookmarks to empty lists.

    Matches from the method signature to the first closing brace at method
    indentation. A function replacement is used rather than a string so that the
    escaped \\n sequences in the template are not read as regex escapes.
    """
    for name in EMPTIED_DEFAULTS:
        pattern = re.compile(r"static " + name + r"\(\) \{.*?\\n  \}")
        replacement = "static %s() {\\n    return [];\\n  }" % name
        body, n = pattern.subn(lambda m: replacement, body)
        if n != 1:
            raise ValueError("expected 1 %s() definition, found %d" % (name, n))
    return body


def patch_template(html: str) -> str:
    """Rewrite the inner template: head injection plus emptied placeholder data.

    The template lives as a JSON string inside <script type="__bundler/template">,
    so the injected markup has to carry that string's escaping: JSON escapes for
    quotes/newlines/backslashes, plus the bundler's own <\\u002F encoding of "</"
    so a nested </script> cannot terminate the outer tag early.
    """
    shim = open(SHIM_PATH, encoding="utf-8").read()
    tag = (
        HEAD_MARKUP
        + "<style>" + PAGE_WRAP_CSS + HEADER_CSS + EMPTY_STATE_CSS
        + ICON_TILE_CSS + TOUCH_CSS + "</style>"
        + "<script>\n" + shim + "\n</script>"
    )
    escaped = json.dumps(tag)[1:-1].replace("</", "<\\u002F")

    match = TEMPLATE_RE.search(html)
    if not match:
        raise ValueError("no __bundler/template section found")
    open_tag, body, close_tag = match.groups()

    # The layout CSS above is inert if the class is renamed, so fail rather than
    # ship a page whose margin silently went missing.
    wrap_at = body.find('class=\\"page-wrap\\"')
    if wrap_at < 0:
        raise ValueError("no page-wrap element found; PAGE_WRAP_CSS would be inert")

    # HEADER_CSS selects .page-wrap > header, so a header that moved out of the
    # wrapper would leave the rule matching nothing.
    header_at = body.find("<header")
    if header_at < 0 or header_at < wrap_at:
        raise ValueError("no <header> inside page-wrap; HEADER_CSS would be inert")

    n = body.count("<head>")
    if n != 1:
        raise ValueError("expected 1 <head> in the template, found %d" % n)
    body = body.replace("<head>", "<head>" + escaped, 1)

    body = empty_defaults(body)
    body = apply_js_patches(body)

    n = body.count(URL_HINT_OLD)
    if n != 1:
        raise ValueError("expected 1 URL placeholder hint, found %d" % n)
    body = body.replace(URL_HINT_OLD, URL_HINT_NEW)

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
        html = patch_template(html)
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
