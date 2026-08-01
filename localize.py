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

import base64
import json
import os
import re
import sys

ICON_CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/"
ICON_LOCAL = "/icons/"
SHIM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage-shim.js")
SWIPE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "header-swipe.js")

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

# The header sits 100px from the top of the page. That was originally one 50px
# margin on the header plus .page-wrap's own 50px padding, back when the header
# was the wrapper's first child.
#
# The search bar is now the first child, so that margin became the gap between
# the bar and the header — 50px of dead space directly under the bar. Two facts
# decide how to close it:
#
#   * The bar's margin-bottom and the header's margin-top are adjacent siblings,
#     so they COLLAPSE: the gap is the larger of the two, not the sum. Lowering
#     one alone does nothing while the other is bigger, which is why both are
#     set to HEADER_GAP.
#   * The header is in normal flow, so shrinking its margin lifts everything
#     below it. The wrapper's padding absorbs exactly what the margin gives up,
#     so the header — and the apps and bookmarks under it — do not move at all.
#
# Hence WRAP_PAD = 50 + (50 - HEADER_GAP): change HEADER_GAP alone and the rest
# of the page stays where it is. !important on the padding because .page-wrap
# carries it in an inline style attribute.
HEADER_GAP = 8
WRAP_PAD = 100 - HEADER_GAP
HEADER_CSS = (
    ".page-wrap > header{margin-top:%dpx!important}" % HEADER_GAP
    + ".page-wrap{padding-top:%dpx!important}" % WRAP_PAD
)

# ── Search the web from the top of the page ─────────────────────────────────
# A <form> with no action: submitting is handled by runSearch, which assigns
# location. See SEARCH_ENGINE_PATCHES for why the native GET had to go — in
# short, the CSP's form-action cannot name an engine the user has not typed yet.
#
# type="search" rather than type="text" is the point of using the native
# control — it brings the clear (×) button, the search keyboard on mobile, and
# Escape-to-clear, none of which would be worth reimplementing.
#
# A page cannot read the browser's configured default search engine — there is
# no API for it, deliberately, since it would be a fingerprinting vector — so
# "native" can only mean the native input control, not the native engine. The
# engine is therefore chosen in Settings; DuckDuckGo is the default because it
# needs no account and honours the ?q= form without a referrer.
SEARCH_ACTION = "https://duckduckgo.com/"

# Submitting is a JS location assignment rather than the form's own GET. The
# form's action would be constrained by the CSP's form-action, which cannot
# name a URL the user has not typed yet — so a custom engine would be blocked
# with no navigation and no error, which is exactly how the search bar failed
# the first time. A top-level location assignment is not covered by
# form-action, so any engine works and the directive stays at 'none'.
#
# The cost is that the bar needs JS. Nothing is lost: the whole dashboard is a
# JS-rendered bundle, so a no-JS page has no search box to submit anyway.
#
# %s marks where the query goes. Presets all happen to be ?q=, but the custom
# field accepts any shape, so substitution is used rather than appending.
SEARCH_ENGINES = (
    ("duckduckgo", "DuckDuckGo", "https://duckduckgo.com/?q=%s"),
    ("google", "Google", "https://www.google.com/search?q=%s"),
    ("bing", "Bing", "https://www.bing.com/search?q=%s"),
    ("startpage", "Startpage", "https://www.startpage.com/sp/search?query=%s"),
    ("searxng", "SearXNG (self-hosted)", ""),
    ("whoogle", "Whoogle (self-hosted)", ""),
    ("custom", "Custom…", ""),
)

# The engines whose URL the user supplies. Selecting one reveals the URL field,
# and until it holds a %s the bar has nowhere to send the query.
SEARCH_CUSTOM_KEYS = ("searxng", "whoogle", "custom")

# Suggestions are off by default. They cannot be fetched from the browser — no
# engine sends Access-Control-Allow-Origin and connect-src is 'self' — so they
# come from the api's /suggest, which fetches upstream server-side. That means
# every keystroke leaves the network, which is a real change for a dashboard
# that otherwise talks to nothing, so it is opt-in and the panel says as much.
#
# Only the four preset engines have a suggestion endpoint. A self-hosted engine
# needs its own JSON API enabled and answers in its own shape, so the toggle is
# hidden rather than offered and quietly broken.
SEARCH_SUGGEST_KEYS = ("duckduckgo", "google", "bing", "startpage")
SUGGEST_DEBOUNCE_MS = 160
SUGGEST_MAX = 8

# The × for the native clear button, as a mask so the colour comes from the rule
# above and can differ on hover. Inline rather than a custom property: --accent
# is published on document.body at runtime by applyTheme, and there is no static
# :root block here to hang another one on.
CLEAR_X = (
    "url('data:image/svg+xml;utf8,"
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="black" stroke-width="3" stroke-linecap="round">'
    '<path d="M5 5l14 14M19 5L5 19"/></svg>\')'
)
SEARCH_CSS = (
    # opacity, never display or height: the row keeps its box whether or not the
    # bar is showing, so revealing it cannot move the date or the greeting. That
    # is the whole reason this is not a collapse.
    # margin-bottom matches HEADER_GAP deliberately: it collapses against the
    # header's margin-top, so a larger value here would reopen the gap that
    # HEADER_CSS just closed, whatever the header's own margin says.
    ".hdr-search{margin:0 0 " + str(HEADER_GAP) + "px;opacity:0;"
    "transition:opacity .18s ease}"
    # Revealed only by its own strip. Hovering the header block below (the date
    # and the greeting) used to reveal it too, via
    # .page-wrap:has(> header:hover) — but that is a second, disjoint trigger
    # zone 50px further down the page, so the bar appeared when the pointer was
    # nowhere near it. The strip is the one area adjacent to the bar, and it
    # occupies the bar's full reserved height, so it is a 40px-tall target
    # spanning the content column rather than a thin line.
    ".hdr-search:hover{opacity:1}"
    # Kept up while the field has focus, or typing would blur it away the moment
    # the pointer left. Its own rule, not appended to the one above: a selector
    # list is dropped whole if any part fails to parse, and :focus-within is the
    # newer of the two.
    ".hdr-search:focus-within{opacity:1}"
    # Full width of the wrapper, so it reads as a bar rather than a stray field.
    ".hdr-search input{width:100%;box-sizing:border-box;padding:11px 14px;"
    "border-radius:9px;border:1px solid rgba(255,255,255,.1);"
    "background:rgba(255,255,255,.04);color:#f2f4f6;font-family:inherit;"
    "font-size:14px;outline:none;transition:border-color .18s ease,"
    "background .18s ease}"
    ".hdr-search input::placeholder{color:#7f8792}"
    ".hdr-search input:focus{border-color:var(--accent);"
    "background:rgba(255,255,255,.07)}"
    # WebKit hides the native clear button until it is styled back in, and its
    # default is a dark glyph that vanishes against this background.
    ".hdr-search input::-webkit-search-cancel-button{-webkit-appearance:none;"
    "appearance:none;width:14px;height:14px;cursor:pointer;"
    "background:currentColor;color:#7f8792;"
    "-webkit-mask:" + CLEAR_X + " center/14px no-repeat;"
    "mask:" + CLEAR_X + " center/14px no-repeat}"
    ".hdr-search input::-webkit-search-cancel-button:hover{color:#f2f4f6}"
)

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

# Flat "add" buttons: no outline of either kind, no tinted square behind the +.
#
# Unconditional. This was first scoped to the empty dashboard on the theory that
# the dashed border reads as an "add slot" next to real cards, but that meant the
# styling silently reverted the moment an app was added. border-color rather than
# border:none so the box keeps its exact size, and the label span has no
# background of its own so applying that to every span is safe.
#
# Two separate things draw an edge on these buttons:
#
#   border  — the dashed 1px accent outline in their inline style.
#   outline — the browser's focus ring. A different property, which the page only
#             resets for input, so the buttons keep theirs.
#
# The focus ring is why an outline survived the border fix: clicking a button
# focuses it, and it stays focused behind the dialog and after it closes.
#
# Suppressing it only for pointer focus (:focus:not(:focus-visible)) is the
# accessible version, and it is not enough here — typing in the dialog puts the
# browser in keyboard modality, so when focus returns to the button it matches
# :focus-visible and the ring reappears. Removing it outright is the only thing
# that matches "no outline" for real. That does cost keyboard users their focus
# indicator on these two buttons; the page already makes the same trade for every
# input. For the accessible variant instead:
#
#   .add-app:focus:not(:focus-visible),.new-cat:focus:not(:focus-visible)
#   {outline:none!important}
ADD_BUTTON_CSS = (
    ".add-app,.new-cat{border-color:transparent!important;outline:none!important}"
    ".add-app span,.new-cat span{background:none!important}"
)

# Grey labels on the three "add" affordances. #9aa3ad is the page's existing
# muted-text colour rather than a new one.
#
# Only the words: each button is a + glyph span followed by a label span, and the
# glyph keeps the accent. add-app and new-cat carry the colour inline on the label
# span, so :last-child targets it. The bookmark button instead sets the colour on
# the button and lets both spans inherit, so that one is greyed at the button and
# the glyph is put back — otherwise the + would grey too. .bm-cat > button is
# unambiguous: the delete and edit affordances beside it are spans.
ADD_LABEL_COLOUR = "#9aa3ad"
ADD_LABEL_CSS = (
    ".add-app span:last-child,"
    ".new-cat span:last-child,"
    ".bm-cat > button{color:" + ADD_LABEL_COLOUR + "!important}"
    ".bm-cat > button > span:first-child{color:var(--accent)!important}"
)

# Padding and spacing for the app cards.
#
# The cards are flush: the inline style declares padding:12px 14px and then
# overrides all four sides with padding-*:0px in the same attribute, so the
# longhands win and the hover highlight — style-hover="background:
# rgba(255,255,255,.045)", an 8px-radius plate — is painted tight against the
# icon and the text with no room around it.
#
# So give the highlight room, and take the space back out of the margin so the
# rows do not spread: this grows the plate from 34px to 52px tall while the row
# pitch only goes from 62px to 66px. The 14px bottom margin that used to do the
# separating is no longer needed — the padding does it, and doubling up would
# push the pitch to 80px.
#
# The horizontal padding is cancelled by an equal negative margin. Without that,
# every icon and title shifts right by CARD_PADDING's 12px and stops lining up
# with the section heading and the bookmark rows below; with it the content stays
# exactly where it is and only the highlight grows outwards. The bleed is half
# the grid's 24px column gap, so neighbouring cards end up touching rather than
# overlapping — and .page-wrap's own padding absorbs it at both edges, on a phone
# too, so nothing escapes the wrapper.
#
# Applied to the add button as well as the cards. Grid items stretch to the row
# height by default, so giving only the cards a margin would leave the button's
# hover area taller than theirs in a shared row.
CARD_PADDING = "9px 12px"
CARD_SIDE_BLEED = "12px"
CARD_MARGIN_CSS = (
    ".app-grid > a[data-drag-id],.app-grid > .add-app"
    "{padding:" + CARD_PADDING + "!important;"
    "margin-bottom:0!important;"
    "margin-left:-" + CARD_SIDE_BLEED + "!important;"
    "margin-right:-" + CARD_SIDE_BLEED + "!important}"
)

# The header's lock and settings buttons. .hdr-actions holds exactly those two —
# the sc-if pair inside the lock button only swaps the padlock glyph — so this
# matches them and nothing else. Targeted that way rather than by the .scp0 /
# .scp1 classes devtools shows, which are generated in render order and move.
#
# The lock button's border is one of its locked-state signals
# (lockBorder: s.locked ? 'var(--accent)' : 'rgba(255,255,255,.1)'), but far from
# the only one: when locked, lockBg becomes var(--accent) and lockColor flips to
# a dark glyph, the icon swaps to a closed padlock and the tooltip changes. A
# solid accent plate reads more clearly than the 1px border ever did.
#
# outline goes too, for the same reason as the add buttons: these get clicked, and
# the focus ring would sit on them afterwards.
#
# The backgrounds become `transparent` rather than a copy of the page colour, so
# they show whatever is behind them and keep following it when the theme changes.
# There is no --bg custom property to reference: applyTheme() assigns
# document.body.style.background directly and only publishes --accent.
#
# The locked state is the exception. It is the one place the background is load
# bearing — with the border gone, lockBg's accent plate is the whole signal — so
# it is restored explicitly for the locked button. That is keyed on the title,
# since the locked and unlocked buttons are otherwise identical in the markup;
# LOCK_TITLE_PREFIX is asserted so a re-export that rewords it fails here rather
# than silently flattening the indicator.
#
# The settings button's hover tint survives on purpose: its generated
# .scpN:hover rule is (0,2,0) against this rule's (0,1,1), so it still wins and
# the button keeps showing that it is interactive.
LOCK_TITLE_PREFIX = "Layout locked"
HDR_BUTTON_CSS = (
    ".hdr-actions button{"
    "border-color:transparent!important;"
    "outline:none!important;"
    "background:transparent!important}"
    '.hdr-actions button[title^="' + LOCK_TITLE_PREFIX + '"]'
    "{background:var(--accent)!important}"
)

# The dashed outline was the only thing marking the buttons out, so with it gone
# they need to be reachable. Kept to the empty dashboard: once apps exist,
# .add-app is a grid item sharing a row with real cards, and a top margin would
# knock it out of alignment with them.
EMPTY_STATE_CSS = (
    ".app-grid .add-app:only-child,"
    ".bm-grid > div:only-child .new-cat{"
    "opacity:1!important;"
    "margin-top:" + EMPTY_ADD_BUTTON_OFFSET + "!important}"
)

# The page reveals these two buttons with
#
#     .app-grid:hover .add-app,.add-app:focus-visible{opacity:1}
#
# and a selector list is discarded whole if any part of it fails to parse. Safari
# only learned :focus-visible in 15.4, so on anything older both of these rules
# are dropped and the buttons can never be revealed — while .hdr-actions and
# .cat-del, whose rules use no :focus-visible, keep working. Restating the hover
# half on its own makes the reveal independent of that support. Higher
# specificity than the opacity:0 base rule, so no !important needed.
HOVER_FALLBACK_CSS = (
    ".app-grid:hover .add-app{opacity:1}"
    ".bm-grid:hover .new-cat{opacity:1}"
    # Belt and braces: where :focus-visible is genuinely unsupported, stop
    # relying on hover at all rather than leaving the controls unreachable.
    "@supports not selector(:focus-visible){"
    ".app-grid .add-app,.bm-grid .new-cat{opacity:1!important}}"
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
        # 690 upstream icons have no svg and arrive as webp or png, named with
        # their extension. iconUrl has to pass that through rather than appending
        # .svg — the extension is what gives the response its content type.
        "iconUrl passes a raster extension through",
        "    return '/icons/' + name + '.svg';",
        "    return '/icons/' + name"
        " + (/\\\\.(svg|webp|png)$/i.test(name) ? '' : '.svg');",
    ),
    (
        "pickIcon suggests a title",
        "  pickIcon = (e) => this.setState({ modal: { ...this.state.modal,"
        " icon: e.currentTarget.dataset.icon } });",
        "  pickIcon = (e) => {\\n"
        "    const icon = e.currentTarget.dataset.icon;\\n"
        "    const suggestion = icon.replace(/\\\\.(svg|webp|png)$/i, '')\\n"
        "      .replace(/-(light|dark)$/, '').split(/[-_]+/)\\n"
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
    # ...except the chosen one. Flattening the tiles took the selected state with
    # it: ic.border is already var(--accent) for the selected tile, but the rule
    # above is !important and overrode it, so nothing in the grid showed which
    # icon was attached. That mattered little while every icon was a deliberate
    # click; now that a pasted URL attaches one, it is the only way to see what
    # you are about to store.
    #
    # [data-sel="1"] is (0,2,1) against the flattening rule's (0,1,1), so it wins
    # on specificity and does not depend on source order. The border is already
    # in the markup at 1px solid — only its colour is restored, so nothing moves.
    'span[data-icon][data-sel="1"]{border-color:var(--accent)!important}'
    # The dialog's preview tile gets the same outline, so the icon field and the
    # grid tile read as the same state rather than two unrelated boxes.
    '[data-icon-field="1"]{border-color:var(--accent)!important}'
)


# ── The settings panel's dropdowns ──────────────────────────────────────────
# Shared by all three (theme, mobile layout, search engine) because the reason
# the background is opaque is easy to undo by accident.
#
# An open dropdown is drawn by the browser, not by the page, and it takes its
# surface from this declaration. Every other field in the panel is
# rgba(255,255,255,.05), and a select styled that way looks right closed and is
# unreadable open: the translucent 5% composites over the UA's own popup surface,
# which is white, and the option text inherits the panel's near-white #eceef1. So
# all three dropdowns were white boxes with white text in them.
#
# #333840 is that exact 5% white resolved against the panel's #282d36 — a literal
# in the bundle, used for the panel, the modal and the context menu, not something
# the theme drives — so the closed control is unchanged to the pixel.
#
# color-scheme:dark stays as well. It is what Safari honours, where an <option>
# cannot be styled at all, and it is what darkens the dropdown arrow.
SELECT_STYLE = (
    "width:100%;background:#333840;border:1px solid rgba(255,255,255,.15);"
    "border-radius:7px;padding:10px 12px;font-size:14px;color:#eceef1;"
    "font-family:inherit;color-scheme:dark;cursor:pointer"
)

# The rows inside an open dropdown. Chrome and Firefox paint each <option> with
# its own background rather than the select's, so the surface above is not enough
# on its own — without this the rows stay transparent over whatever the UA drew.
# data-k is on every select in the panel and nowhere else, which keeps this off
# any <select> a future patch adds elsewhere.
SELECT_CSS = "select[data-k] option{background-color:#333840;color:#eceef1}"

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
    ".app-grid .add-app,.hdr-row .hdr-actions,.bm-grid .new-cat,"".bm-cat .cat-del,.bm-cat .cat-grip,"
    # The search bar is hover-revealed too, so without this it is invisible and
    # unreachable on a phone rather than merely hidden.
    ".hdr-search,"
    '[data-nodrag="1"]{opacity:1!important}'
    "}"
)


def tpl(text: str) -> str:
    """Real source text -> how it appears inside the template's JSON string.

    Lets the patches below be written as ordinary JS and HTML instead of by hand
    with \\n and \\" escapes, which is where this went wrong the first time.
    Order matters: backslashes, then quotes, then newlines, and </ last because
    its replacement introduces a backslash of its own.
    """
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("</", "<\\u002F")
    )


# ── Icon set updates ────────────────────────────────────────────────────────
# The picker's list is baked into the page as __icons_data, separately from the
# files in svg/ — and it already disagrees with them: 3222 names against 3232
# files, so ten icons on disk (claude-ai-dark, aws-dynamodb, …) cannot be
# selected at all. Downloading new icons would not surface them either.
#
# So the list is taken from GET /api/icons, which enumerates the real
# directories, keeping the baked list as the fallback for when the service is
# unreachable. That fixes the existing mismatch and is what lets an update show
# up without rebuilding the image.
#
# refreshIcons() is called outside the existing try/catch in componentDidMount so
# a failure there cannot skip it, and it swallows its own errors: a dashboard that
# still renders with the baked list beats one that breaks because the service is
# down. A reply that arrives is taken as it stands, empty list included — turning
# every library off is a thing the panel now lets you do, and it has to be
# believed rather than mistaken for a failure. The fallback is for no reply at
# all, which the rejection and the catch already cover.
#
# The buttons only ever run on click — no polling — so the container makes no
# outbound request during a normal page load. See api/icons.py.
#
# Several icon libraries can be installed at once and they overlap, so the panel
# lists them rather than offering one "check for updates". Each row updates,
# enables or promotes one library; the order decides who wins a name more than one
# provides, which is why promoting is a control at all. The api rebuilds the
# directory nginx serves before it answers, so the page only has to reload the
# list afterwards — see IconStore.resolve.
ICON_UPDATE_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "load the icon list from the service",
            "      if (te) this.setState({ themes: JSON.parse(te.textContent) });",
            "      if (te) this.setState({ themes: JSON.parse(te.textContent) });\n"
            "    } catch (e) {}\n"
            "    this.refreshIcons();\n"
            "    try {",
        ),
        (
            "icon-update state",
            "      themePos: { x: 0, y: 0 }\n    };",
            "      themePos: { x: 0, y: 0 },\n"
            "      iconCount: 0,\n"
            "      iconMsg: '',\n"
            "      iconBusy: false,\n"
            "      iconLibs: [],\n"
            "      iconBusyKey: ''\n    };",
        ),
        (
            "icon-update handlers",
            "  resetDefaults = () => {",
            """  refreshIcons = () => fetch('/api/icons')
    .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
    .then(d => {
      if (Array.isArray(d.icons)) {
        this.setState({ icons: d.icons, iconCount: d.count });
      }
      const st = d.status || {};
      // Already in preference order, so the rows render as they arrive.
      if (Array.isArray(st.libraries)) this.setState({ iconLibs: st.libraries });
      if (st.writable === false && !this.state.iconMsg) {
        this.setState({ iconMsg: 'icon storage is not writable' });
      }
      return d;
    })
    .catch(() => null);
  // One update at a time — the api answers 409 for a second one — so the whole
  // section goes busy and the row being worked on says so.
  updateIcons = (e) => {
    if (this.state.iconBusy) return;
    const key = (e && e.currentTarget && e.currentTarget.dataset.key) || '';
    const lib = (this.state.iconLibs || []).find(l => l.key === key);
    this.setState({ iconBusy: true, iconBusyKey: key,
      iconMsg: 'checking ' + (lib ? lib.label : 'for updates') + '...' });
    const done = (msg) => this.setState({ iconBusy: false, iconBusyKey: '', iconMsg: msg });
    const poll = () => fetch('/api/icons/status')
      .then(r => r.json())
      .then(st => {
        if (st.running) {
          this.setState({ iconMsg: st.phase || 'working...' });
          setTimeout(poll, 700);
          return;
        }
        const last = st.last || {};
        this.refreshIcons().then(() => done(
          last.error ? 'failed: ' + last.error
            : (last.new || last.changed || last.failed)
              ? (last.new || 0) + ' new, ' + (last.changed || 0) + ' updated'
                + (last.failed ? ', ' + last.failed + ' failed' : '')
              : last.attempted ? 'nothing applied' : 'already up to date'
        ));
      })
      .catch(e => done('failed: ' + e.message));
    fetch('/api/icons/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ library: key })
    })
      .then(r => r.ok ? poll() : r.json().then(d => done('failed: ' + (d.error || r.status))))
      .catch(e => done('failed: ' + e.message));
  };
  // The api validates the keys and rebuilds what nginx serves, so the reply is
  // enough to trust; reloading the list is what makes the change visible in the
  // picker, since disabling a library removes its names from it.
  iconConfig = (body) => {
    this.setState({ iconMsg: 'saving...' });
    return fetch('/api/icons/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
      .then(r => r.ok ? r.json()
        : r.json().then(d => Promise.reject(new Error(d.error || 'HTTP ' + r.status))))
      .then(() => this.refreshIcons())
      .then(() => this.setState({ iconMsg: '' }))
      .catch(e => this.setState({ iconMsg: 'failed: ' + e.message }));
  };
  // Promotes one library a single place. The whole order is sent, not the move,
  // so the api never has to reconstruct what the page was looking at.
  moveIconLib = (e) => {
    const key = e.currentTarget.dataset.key;
    const order = (this.state.iconLibs || []).map(l => l.key);
    const at = order.indexOf(key);
    if (at < 1) return;
    order.splice(at - 1, 0, order.splice(at, 1)[0]);
    this.iconConfig({ preference: order });
  };
  toggleIconLib = (e) => {
    const key = e.currentTarget.dataset.key;
    const lib = (this.state.iconLibs || []).find(l => l.key === key);
    if (!lib) return;
    this.iconConfig({ enabled: { [key]: !lib.enabled } });
  };
  resetDefaults = () => {""",
        ),
        (
            "icon-update props",
            "      pickIcon: this.pickIcon,",
            "      pickIcon: this.pickIcon,\n"
            "      updateIcons: this.updateIcons,\n"
            "      moveIconLib: this.moveIconLib,\n"
            "      toggleIconLib: this.toggleIconLib,\n"
            "      iconSummary: (s.iconCount || (s.icons || []).length || 0)"
            " + ' icons installed' + (s.iconMsg ? ' - ' + s.iconMsg : ''),\n"
            # Per-row styles rather than one shared set: each row's toggle and
            # buttons reflect that library's own state.
            "      iconLibs: (s.iconLibs || []).map((l, i) => ({\n"
            "        key: l.key, label: l.label,\n"
            "        detail: (l.count || 0).toLocaleString() + ' icons'"
            " + (l.note ? ' - ' + l.note : ''),\n"
            "        upStyle: 'flex:none;width:22px;text-align:center;font-size:13px;"
            "user-select:none;color:' + (i === 0 ? '#4c545c' : '#9aa3ad')"
            " + ';cursor:' + (i === 0 ? 'default' : 'pointer'),\n"
            "        btnLabel: s.iconBusyKey === l.key ? '...' : 'Update',\n"
            "        btnStyle: 'flex:none;background:none;"
            "border:1px solid rgba(255,255,255,.18);border-radius:7px;"
            "padding:5px 11px;font-size:12px;font-weight:600;color:#dfe3e8;"
            "font-family:inherit;cursor:pointer;opacity:'"
            " + (s.iconBusy && s.iconBusyKey !== l.key ? '.45' : '1'),\n"
            "        track: 'position:relative;width:38px;height:20px;flex:none;"
            "border-radius:20px;cursor:pointer;transition:background .16s ease;"
            "background:' + (l.enabled ? 'var(--accent)' : 'rgba(255,255,255,.16)'),\n"
            "        knob: 'position:absolute;top:2px;left:'"
            " + (l.enabled ? '20px' : '2px')"
            " + ';width:16px;height:16px;border-radius:50%;background:#fff;"
            "transition:left .16s ease'\n"
            "      })),",
        ),
        (
            "icon-update settings row",
            '        <div style="display:flex;align-items:center;'
            "justify-content:space-between;margin-top:22px;padding-top:18px;"
            'border-top:1px solid rgba(255,255,255,.08)">\n'
            '          <button sc-camel-on-click="{{ resetDefaults }}"',
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            'text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px">Icons</div>\n'
            '        <div style="font-size:12.5px;color:#9aa3ad">'
            "{{ iconSummary }}</div>\n"
            '        <div style="font-size:11.5px;color:#6d757e;margin-top:7px">'
            "The sets overlap. Where more than one has an icon of the same name"
            " the one nearest the top wins, and ↑ moves a set up. Turning one"
            " off hides it from the picker; apps already using its icons keep"
            " them. A set showing no icons has not been downloaded yet — Update"
            " fetches it, which takes a few minutes.</div>\n"
            '        <sc-for list="{{ iconLibs }}" as="lib" hint-placeholder-count="3">\n'
            '          <div style="display:flex;align-items:center;gap:10px;'
            'margin-top:12px">\n'
            '            <span data-key="{{ lib.key }}"'
            ' sc-camel-on-click="{{ moveIconLib }}" style="{{ lib.upStyle }}"'
            ' title="Prefer this set">↑</span>\n'
            '            <span style="flex:1;min-width:0">\n'
            '              <span style="display:block;font-size:13.5px;color:#dfe3e8;'
            'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
            "{{ lib.label }}</span>\n"
            '              <span style="display:block;font-size:11.5px;color:#6d757e;'
            'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
            "{{ lib.detail }}</span>\n"
            "            </span>\n"
            '            <button data-key="{{ lib.key }}"'
            ' sc-camel-on-click="{{ updateIcons }}" style="{{ lib.btnStyle }}"'
            ' style-hover="background:rgba(255,255,255,.08)">'
            "{{ lib.btnLabel }}</button>\n"
            '            <span data-key="{{ lib.key }}"'
            ' sc-camel-on-click="{{ toggleIconLib }}" style="{{ lib.track }}">'
            '<span style="{{ lib.knob }}"></span></span>\n'
            "          </div>\n"
            "        </sc-for>\n"
            '        <div style="display:flex;align-items:center;'
            "justify-content:space-between;margin-top:22px;padding-top:18px;"
            'border-top:1px solid rgba(255,255,255,.08)">\n'
            '          <button sc-camel-on-click="{{ resetDefaults }}"',
        ),
    )
)


# ── Pasting a URL into the add dialog ───────────────────────────────────────
# Focusing the URL field is the part that actually helps: nothing in the modal
# was focused before, so adding an app meant clicking into a field first. With it
# focused, pasting is a single Cmd/Ctrl+V, needs no permission, and works over
# plain HTTP.
#
# Reading the clipboard automatically is attempted too, but it will not fire on a
# LAN deployment. navigator.clipboard is gated on a secure context, and measured
# in both engines: at http://127.0.0.1 it exists (readText still throws
# NotAllowedError without permission), while at http://<lan-ip> isSecureContext is
# false and navigator.clipboard is undefined outright. So this is dead code over
# http:// and starts working by itself behind HTTPS.
#
# Even where it exists, calling readText() raises a permission prompt in Chrome
# and a paste confirmation in Safari — on every click of Add application, which
# would be worse than pressing Cmd+V. So the permission is queried first and the
# clipboard is only read when it has *already* been granted; the prompt is never
# triggered. permissions.query itself throws for an unknown name in Safari, which
# the catch treats as "don't read".
CLIPBOARD_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "focus the URL field when the dialog opens",
            '<input value="{{ fUrl }}" data-field="url"',
            '<input ref="{{ focusRef }}" value="{{ fUrl }}" data-field="url"',
        ),
        (
            "offer the clipboard URL when adding an app",
            "  openAddApp = () => this.setState({ modal: { kind: 'app', editId: null,"
            " groupId: null, icon: '', title: '', desc: '', url: '', q: '' } });",
            """  openAddApp = () => {
    this.setState({ modal: { kind: 'app', editId: null, groupId: null, icon: '', title: '', desc: '', url: '', q: '' } });
    this.maybePasteUrl();
  };
  maybePasteUrl = async () => {
    try {
      if (!navigator.clipboard || !navigator.clipboard.readText) return;
      // Only read when already granted, so this never raises a prompt.
      const perm = await navigator.permissions.query({ name: 'clipboard-read' });
      if (perm.state !== 'granted') return;
      const text = ((await navigator.clipboard.readText()) || '').trim();
      if (text.length > 2048 || !/^https?:\\/\\/[^\\s]+$/i.test(text)) return;
      this.setState(s => (s.modal && !s.modal.url)
        ? { modal: { ...s.modal, url: text } } : null);
    } catch (e) {
      // No clipboard, no permissions API, or refused. The field is focused, so
      // pasting by hand is one keystroke.
    }
  };""",
        ),
    )
)


# ── Guessing the Title from the URL ─────────────────────────────────────────
# Picking an icon already suggests a Title (JS_PATCHES). This adds the other
# direction, which is the one that fires first in practice: you paste a URL, and
# the name is right there in it.
#
# It reuses that feature's machinery rather than inventing a second one — the
# same titleAuto flag, so the two suggestions cooperate: whichever happens later
# refines the title, and the moment the user types in the Title field it is theirs
# and neither touches it again. Editing an existing entry is safe for free, since
# its title is non-empty and titleAuto is unset.
#
# What gets guessed, and why the host's first label:
#
#   http://plex.lan                      -> Plex
#   https://www.motogp.com/en/videopass  -> Motogp     (leading www. dropped)
#   https://jellyfin.example.com         -> Jellyfin
#   http://home-assistant.lan:8123       -> Home Assistant
#   http://192.168.0.108:3002/gitea      -> Gitea      (IP: first path segment)
#   http://192.168.0.108:8080            -> ''         (nothing to go on)
#
# An IP address says nothing about what is running behind it, so the first path
# segment stands in — which is exactly the shape a reverse-proxied LAN service
# takes, and this dashboard is mostly LAN services.
#
# The suggestion waits for something host-shaped (a scheme, a dot or a colon).
# Without that gate, typing a URL by hand would put G, Gi, Git into the Title
# field on the way to Gitea. Clearing the URL clears a title that came from it,
# so the dialog does not keep a guess for a URL that is no longer there.
#
# The same label also fills the icon search, so the grid is already showing the
# icons worth choosing from. That is done by writing m.q — the search box the user
# can see and clear — rather than by teaching the grid's title fallback to treat a
# URL-derived title differently:
#
#   * the box shows "gitea", which is why the grid is filtered, and clearing it
#     brings everything back;
#   * picking an icon then leaves the grid exactly as it is. Filtering off the
#     title instead would unfilter the moment titleAuto flipped to the icon's own
#     suggestion, so the grid would jump under the finger that just clicked it —
#     the glitch the title fallback already avoids for icon picks;
#   * the grid's own expression needs no change at all.
#
# qAuto mirrors titleAuto: a search this dialog filled in, which a later URL
# replaces and a typed search takes over for good.
#
# The search is only filled when the label matches an icon that exists. Filtering
# to a label nothing matches would leave the picker empty, which is worse than
# showing everything — so "my-nas" leaves the grid alone.
URL_TITLE_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "the URL suggests a title too",
            """  fieldChange = (e) => {
    const f = e.currentTarget.dataset.field, v = e.currentTarget.value;
    this.setState(s => ({ modal: { ...s.modal, [f]: v, ...(f === 'title' ? { titleAuto: false } : {}) } }));
  };""",
            """  fieldChange = (e) => {
    const f = e.currentTarget.dataset.field, v = e.currentTarget.value;
    this.setState(s => {
      const m = { ...s.modal, [f]: v,
        ...(f === 'title' ? { titleAuto: false } : {}),
        ...(f === 'q' ? { qAuto: false } : {}) };
      return { modal: f === 'url' ? this.suggestFromUrl(m, v) : m };
    });
  };
  suggestFromUrl = (m, raw) => {
    const url = (raw || '').trim();
    // Wait for something host-shaped, or typing a URL by hand puts G, Gi, Git
    // into the Title on the way to Gitea.
    const ready = /^[a-z]+:\\/\\//i.test(url) || /[.:]/.test(url);
    const label = ready ? this.labelFromUrl(url) : '';
    const pool = label ? (this.state.icons || []).filter(n => n.includes(label)) : [];
    if (!m.title.trim() || m.titleAuto) {
      if (!url) { if (m.titleAuto) { m.title = ''; m.titleAuto = false; } }
      else if (label) { m.title = this.titleFromUrl(url); m.titleAuto = true; }
    }
    if (!(m.q || '').trim() || m.qAuto) {
      // Filtering to a label no icon matches would empty the picker, which is
      // worse than showing everything.
      if (!pool.length) { if (m.qAuto) { m.q = ''; m.qAuto = false; } }
      else { m.q = label; m.qAuto = true; }
    }
    if (!m.icon || m.iconAuto) {
      // Exact name only, never the first match in the grid: the grid matches on
      // substrings, so "plex" leads with perplexity-dark and "nginx" with
      // nginx-proxy-manager. An icon attached by mistake is worse than none,
      // because Enter stores it without another look — and no match just leaves
      // the field empty, which is what the site-icon fetch on save is for.
      const exact = pool.find(n => n.replace(/\\.(svg|webp|png)$/i, '') === label);
      if (exact) { m.icon = exact; m.iconAuto = true; }
      else if (m.iconAuto) { m.icon = ''; m.iconAuto = false; }
    }
    return m;
  };
  labelFromUrl = (raw) => {
    let url = (raw || '').trim();
    if (!url) return '';
    // Same normalisation saveModal uses, so a scheme-less paste still parses.
    if (!/^[a-z]+:\\/\\//i.test(url)) url = 'https://' + url;
    let u;
    try { u = new URL(url); } catch (e) { return ''; }
    const host = u.hostname.replace(/^www\\./i, '');
    const isIp = /^\\[/.test(host) || /^\\d+(\\.\\d+){3}$/.test(host);
    let label = isIp ? '' : host.split('.')[0];
    if (!label) label = (u.pathname || '').split('/').filter(Boolean)[0] || '';
    // Only a path segment can carry an extension; host labels hold no dots here.
    label = label.replace(/\\.[a-z0-9]{1,5}$/i, '');
    if (!label || /^\\d+$/.test(label)) return '';
    return label.toLowerCase();
  };
  titleFromUrl = (raw) => this.labelFromUrl(raw).split(/[-_+.\\s]+/).filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');""",
        ),
        (
            # The grid matches substrings and was then left in alphabetical order,
            # so the icon you actually asked for could sit well down the list:
            # "reddit" led with libreddit-light and libreddit, "plex" with five
            # perplexity variants, "nginx" with nginx-proxy-manager. The right
            # icon was attached — the outline showed it — but the grid still read
            # as if it favoured the other one.
            #
            # Rank instead of reorder-by-name: exact match, then names starting
            # with the query, then the rest. Array#sort is stable, so each band
            # keeps its alphabetical order. Only the filtered copy is sorted;
            # s.icons itself is never touched.
            "rank the icon matches instead of leaving them alphabetical",
            "      const pool = q ? s.icons.filter(n => n.includes(q)) : s.icons;",
            "      const stem = (n) => n.replace(/\\.(svg|webp|png)$/i, '');\n"
            "      const rank = (n) => stem(n) === q ? 0 : stem(n).startsWith(q) ? 1 : 2;\n"
            "      const pool = q"
            " ? s.icons.filter(n => n.includes(q)).sort((a, b) => rank(a) - rank(b))"
            " : s.icons;",
        ),
        (
            # Nothing in the markup distinguished the selected tile, so the CSS
            # had nothing to hook. ic.border already knows which one it is.
            "mark the selected tile so it can be outlined",
            "        bg: name === m.icon ? 'rgba(148,204,138,.18)' :"
            " 'rgba(255,255,255,.04)',",
            "        sel: name === m.icon ? '1' : '0',\n"
            "        bg: name === m.icon ? 'rgba(148,204,138,.18)' :"
            " 'rgba(255,255,255,.04)',",
        ),
        (
            "carry that marker on the tile",
            '<span data-icon="{{ ic.name }}" sc-camel-on-click="{{ pickIcon }}"',
            '<span data-icon="{{ ic.name }}" data-sel="{{ ic.sel }}"'
            ' sc-camel-on-click="{{ pickIcon }}"',
        ),
        (
            "the same marker for the dialog's icon field",
            "      hasIcon: !!(m && m.icon),",
            "      hasIcon: !!(m && m.icon),\n"
            "      iconSel: (m && m.icon) ? '1' : '0',",
        ),
        (
            "carry it on the icon field",
            '<span style="width:44px;height:44px;flex:none;display:grid;'
            "place-items:center;border-radius:9px;background:rgba(255,255,255,.05);"
            'border:1px solid rgba(255,255,255,.1)">',
            '<span data-icon-field="{{ iconSel }}" style="width:44px;height:44px;'
            "flex:none;display:grid;place-items:center;border-radius:9px;"
            "background:rgba(255,255,255,.05);"
            'border:1px solid rgba(255,255,255,.1)">',
        ),
        (
            # Anchored on what JS_PATCHES leaves behind. A click is a decision:
            # from here on the URL may still refine the title, but never the icon.
            "a hand-picked icon is not a suggestion",
            "      return { modal: { ...m, icon, title: suggest ? suggestion : m.title,"
            " titleAuto: suggest } };",
            "      return { modal: { ...m, icon, title: suggest ? suggestion : m.title,"
            " titleAuto: suggest, iconAuto: false } };",
        ),
        (
            "a clipboard URL suggests a title as well",
            """      this.setState(s => (s.modal && !s.modal.url)
        ? { modal: { ...s.modal, url: text } } : null);""",
            """      this.setState(s => (s.modal && !s.modal.url)
        ? { modal: this.suggestFromUrl({ ...s.modal, url: text }, text) } : null);""",
        ),
    )
)


# ── The category delete is just the bin ─────────────────────────────────────
# It read "🗑 DELETE" in 10px uppercase next to the category name. Two categories
# per row on a phone leaves ~163px for name + grip + DELETE, which is what pushed
# the header into wrapping in the first place; the word was the widest part of it.
#
# The bin alone says it, and the control keeps its accessible name — title="Delete
# category" was already there, so nothing is lost to a screen reader or to a
# hover. The padding was sized around the text (2px 7px 2px 5px, lopsided to sit
# beside the word); with the word gone it is symmetric, and a touch larger than
# the old height so the tap target does not shrink with the label.
CAT_DELETE_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "drop the DELETE label, keep the bin",
            "</svg>Delete",
            "</svg>",
        ),
        (
            "centre the bin in what the label used to occupy",
            "padding:2px 7px 2px 5px;border-radius:5px;font-size:10px",
            "padding:3px 5px;border-radius:5px;font-size:10px",
        ),
    )
)


# ── A search bar above the date ─────────────────────────────────────────────
# Sits inside the header's accent-topped block, above the date line, so the
# whole top of the page stays one unit.
#
# No component state and no handler: the form's own GET submit navigates, which
# is why this needs nothing from renderVals(). name="q" is what the action
# expects, and autocomplete="off" keeps the browser's form history out of the
# way of the engine's own suggestions.
#
# The template is compiled, so {{ }} would be read as a placeholder and a
# sc-camel-* attribute as a binding; this is deliberately plain HTML with
# neither.
SEARCH_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "search bar above the header",
            '<header data-screen-label="Header">',
            '<form class="hdr-search" role="search" '
            'sc-camel-on-submit="{{ runSearch }}">'
            '<input type="search" name="q" autocomplete="off" '
            'aria-label="Search the web" placeholder="{{ searchHint }}">'
            "</form>"
            '<header data-screen-label="Header">',
        ),
    )
)


# ── Enter commits the add/edit dialog ───────────────────────────────────────
# Paste a URL, get a Title guessed from it, and the dialog is already complete —
# so reaching for the mouse to press Add is the only thing left, and Enter should
# do it. The page already treats Enter that way for the new-category input
# (newGroupKey), so this is the same idea in the one dialog that lacked it.
#
# Hung off the existing window keydown handler rather than the inputs: the dialog
# has four fields plus an icon search, and Enter should work from all of them
# without five identical handlers. That handler already owns Escape for the same
# reason.
#
# Three guards, in order of how easy each is to get wrong:
#
#   * this.state.modal — without it, Enter anywhere on the page (or in the
#     settings panel) would try to save.
#   * not on a button — Enter on a focused Cancel fires that button's own click,
#     and saving as well would override the cancel.
#   * isComposing — Enter also confirms an IME candidate, which is not a submit.
#
# saveModal needs no extra validation: its own guard is character for character
# what saveDisabled uses for the button, so Enter cannot save what Add refuses.
ENTER_SAVE_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "Enter saves the add/edit dialog",
            "    this._onKey = (e) => { if (e.key === 'Escape')"
            " this.setState({ modal: null, themeOpen: false, ctx: null }); };",
            """    this._onKey = (e) => {
      if (e.key === 'Escape') this.setState({ modal: null, themeOpen: false, ctx: null });
      if (e.key === 'Enter' && this.state.modal && !e.isComposing
          && !(e.target && e.target.closest && e.target.closest('button'))) {
        e.preventDefault();
        this.saveModal();
      }
    };""",
        ),
    )
)


# ── Closing the add/edit dialog by clicking away ────────────────────────────
# The export closes the dialog from a click handler on the dimmed backdrop that
# fires whenever e.target === e.currentTarget — the backdrop itself was clicked,
# rather than something inside the dialog.
#
# The catch is what a click event's target is when press and release happen on
# different elements: the browser dispatches it on the nearest common ancestor of
# the two. Select the URL text, overshoot the dialog's edge, release over the
# backdrop, and that ancestor *is* the backdrop — so a selection drag was
# indistinguishable from a click outside, and the dialog vanished mid-edit.
#
# So the close does not ride on click at all. pointerdown records whether the
# press landed on the backdrop and pointerup closes only if the release landed
# there too, which also rules out the mirror gesture (press outside, release
# inside). A tap or click wholly on the backdrop still closes it.
#
# pressedBackdrop is a plain instance field rather than state: nothing renders
# from it, and a setState here would rebuild the dialog between press and
# release.
MODAL_BACKDROP_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "close the dialog on a press and release that both land outside",
            "  backdropClick = (e) => { if (e.target === e.currentTarget)"
            " this.setState({ modal: null }); };",
            "  backdropDown = (e) => { this.pressedBackdrop ="
            " e.target === e.currentTarget; };\n"
            "  backdropUp = (e) => { if (e.target === e.currentTarget"
            " && this.pressedBackdrop) this.setState({ modal: null }); };",
        ),
        (
            "the backdrop listens for the two halves of the gesture",
            '<div sc-camel-on-click="{{ backdropClick }}"'
            ' style="position:fixed;inset:0;',
            '<div sc-camel-on-pointer-down="{{ backdropDown }}"'
            ' sc-camel-on-pointer-up="{{ backdropUp }}"'
            ' style="position:fixed;inset:0;',
        ),
        (
            "expose both halves instead of the click handler",
            "      backdropClick: this.backdropClick,",
            "      backdropDown: this.backdropDown,\n"
            "      backdropUp: this.backdropUp,",
        ),
    )
)


# ── Bookmarks and apps as the same kind of thing ────────────────────────────
# Items drag between the two lists, and the drop handler already treats them as
# one shape: moving a bookmark into apps synthesises a desc from the URL host,
# and moving an app into a group keeps its desc untouched. So a bookmark can
# already carry a description — the dialog just hid the field, and there was no
# way to edit a bookmark at all. Both were asymmetries in the UI rather than in
# the data.
#
# Four changes: show Description for bookmarks, persist it, give bookmarks the
# edit affordance apps have, and offer the clipboard URL from the bookmark dialog
# too.
#
# saveModal stores the trimmed description as-is for a bookmark rather than
# copying the app branch's `|| url` fallback. A bookmark card does not display a
# description, so defaulting it to the URL would only surface later as a worse
# label after a drag into apps — the drop handler's host fallback is the better
# one, and leaving the field empty keeps it reachable.
PARITY_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "show the description field for bookmarks",
            "      modalShowDesc: !!m && m.kind === 'app',",
            "      modalShowDesc: !!m,",
        ),
        (
            "persist a bookmark's description",
            "      const base = { icon: m.icon || 'homepage', title: m.title.trim(), url };",
            "      const base = { icon: m.icon || 'homepage', title: m.title.trim(),"
            " desc: m.desc.trim(), url };",
        ),
        (
            "editing a bookmark",
            "  deleteBookmark = (e) => {",
            """  editBookmark = (e) => {
    e.preventDefault(); e.stopPropagation();
    const { gid, id } = e.currentTarget.dataset;
    const group = this.state.groups.find(g => g.id === gid);
    const bm = group && group.bookmarks.find(b => b.id === id);
    if (bm) this.setState({ modal: { kind: 'bm', editId: bm.id, groupId: gid, icon: bm.icon, title: bm.title, desc: bm.desc || '', url: bm.url, q: '' } });
  };
  deleteBookmark = (e) => {""",
        ),
        (
            "expose the bookmark edit handler",
            "      deleteBookmark: this.deleteBookmark,",
            "      deleteBookmark: this.deleteBookmark,\n"
            "      editBookmark: this.editBookmark,",
        ),
        (
            "an edit button on each bookmark",
            '<span data-nodrag="1" data-gid="{{ group.id }}" data-id="{{ bm.id }}"'
            ' sc-camel-on-click="{{ deleteBookmark }}" title="Remove"',
            '<span data-nodrag="1" data-gid="{{ group.id }}" data-id="{{ bm.id }}"'
            ' sc-camel-on-click="{{ editBookmark }}" title="Edit"'
            ' style="width:20px;height:20px;display:grid;place-items:center;'
            "border-radius:5px;font-size:10px;color:#9aa3ad;cursor:pointer;"
            'opacity:0;margin-left:4px"'
            ' style-hover="opacity:1;background:rgba(255,255,255,.1);color:#eceef1"'
            ">✎</span>"
            '<span data-nodrag="1" data-gid="{{ group.id }}" data-id="{{ bm.id }}"'
            ' sc-camel-on-click="{{ deleteBookmark }}" title="Remove"',
        ),
        (
            "offer the clipboard URL when adding a bookmark",
            "    this.setState({ modal: { kind: 'bm', editId: null, groupId, icon: '',"
            " title: '', desc: '', url: '', q: '' } });",
            "    this.setState({ modal: { kind: 'bm', editId: null, groupId, icon: '',"
            " title: '', desc: '', url: '', q: '' } });\n"
            "    this.maybePasteUrl();",
        ),
    )
)


# ── Drag-sortable bookmark categories ───────────────────────────────────────
# The existing drag system moves items between containers: hitTest finds the
# [data-container] under the pointer and the insertion index among its
# [data-drag-id] children, and moveLive splices the item across state. Categories
# were not part of it — only the bookmarks inside them.
#
# Making .bm-grid a container and each .bm-cat draggable creates two collisions,
# because a category is a draggable that *contains* draggables and a container:
#
#   * hitTest takes the first [data-container] whose rect holds the pointer, in
#     document order. .bm-grid encloses every category list, so dragging a
#     bookmark would resolve to .bm-grid and drop into the wrong place.
#   * .bm-grid's [data-drag-id] children are both the categories and every
#     bookmark nested inside them, so the insertion index would be computed
#     against a mixed list.
#
# Both are fixed by making the drag typed: items carry data-drag-kind, containers
# declare data-accepts (defaulting to "item", so the app grid and the category
# lists need no markup change), and hitTest filters both by the kind being
# dragged. A category can then only land among categories and a bookmark only
# among bookmarks.
#
# The handle is separate from the moved element, which the system did not support:
# dragPointerDown used e.currentTarget as both. Resolving the moved node with
# closest('[data-drag-id]') keeps apps and bookmarks identical — currentTarget is
# already the draggable, so closest returns itself — while letting a grip inside
# the header move the whole category. That matters because the category name is
# contentEditable: without a dedicated handle, click-dragging to select the text
# would drag the column instead.
#
# The grip lives inside the same sc-if editable as the delete affordance, so it
# is absent exactly when dragging is disabled by the lock.
CATEGORY_DRAG_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "resolve the dragged node from its handle",
            "    const el = e.currentTarget;\n    const r = el.getBoundingClientRect();",
            "    const el = e.currentTarget.closest('[data-drag-id]') || e.currentTarget;\n"
            "    const r = el.getBoundingClientRect();",
        ),
        (
            "record what kind of thing is being dragged",
            "      id: el.dataset.dragId, el,",
            "      id: el.dataset.dragId, kind: el.dataset.dragKind || 'item', el,",
        ),
        (
            "only consider containers that accept this kind",
            "    const conts = [...document.querySelectorAll('[data-container]')];",
            "    const kind = this._press.kind || 'item';\n"
            "    const conts = [...document.querySelectorAll('[data-container]')]\n"
            "      .filter(c => (c.dataset.accepts || 'item') === kind);",
        ),
        (
            "index against same-kind siblings only",
            "    const items = [...cont.querySelectorAll('[data-drag-id]')]"
            ".filter(n => n.dataset.dragId !== this._press.id);",
            "    const items = [...cont.querySelectorAll('[data-drag-id]')]\n"
            "      .filter(n => (n.dataset.dragKind || 'item') === kind"
            " && n.dataset.dragId !== this._press.id);",
        ),
        (
            "reorder categories",
            "  moveLive(id, toCid, toIndex) {\n    let item = null;",
            """  moveLive(id, toCid, toIndex) {
    if (this._press && this._press.kind === 'group') {
      const moving = this.state.groups.find(g => g.id === id);
      if (!moving) return;
      const groups = this.state.groups.filter(g => g.id !== id);
      groups.splice(toIndex, 0, moving);
      const reordered = { apps: this.state.apps, groups };
      if (this.sig(reordered) === this.sig(this.state)) return;
      this.flipMove(reordered);
      return;
    }
    let item = null;""",
        ),
        (
            "the category grid becomes a container",
            '<div class="bm-grid" style="display:grid;',
            '<div class="bm-grid" data-container="groups" data-layout="grid"'
            ' data-accepts="group" style="display:grid;',
        ),
        (
            "each category becomes draggable",
            '<div class="bm-cat" style="display:flex;',
            '<div class="bm-cat" data-drag-id="{{ group.id }}" data-drag-kind="group"'
            ' style="display:flex;',
        ),
        (
            "a grip to drag the category by",
            '<sc-if value="{{ editable }}" hint-placeholder-val="{{ true }}">\n'
            '                <span class="cat-del"',
            '<sc-if value="{{ editable }}" hint-placeholder-val="{{ true }}">\n'
            '                <span class="cat-grip" sc-camel-on-pointer-down='
            '"{{ dragPointerDown }}" title="Drag to reorder" style="display:flex;'
            "align-items:center;padding:2px 4px;border-radius:5px;font-size:11px;"
            'line-height:1;color:#6d757e">⠿</span>\n'
            '                <span class="cat-del"',
        ),
    )
)

# The grip follows the delete affordance's hover-reveal. touch-action:none stops a
# touch drag being taken over by the page scroll.
CATEGORY_GRIP_CSS = (
    ".bm-cat .cat-grip{opacity:0;transition:opacity .18s ease;"
    "cursor:grab;touch-action:none}"
    ".bm-cat:hover .cat-grip{opacity:1}"
    ".bm-cat .cat-grip:active{cursor:grabbing}"
    # The header is a flex row of name + grip + DELETE, and every child defaults
    # to min-width:auto — so the name refuses to shrink past its longest word and
    # pushes the controls out of the column. A category called "Infrastructure &
    # Monitoring" put DELETE 46px off-screen on a phone; on a 1400px desktop the
    # controls escaped the column too and were only hidden by the page margin.
    # So let the row wrap: the name keeps the full column width and the controls
    # drop to a second line only when they no longer fit beside it. Short names
    # ("DEV", "MEDIA") still sit on one line exactly as before. Squeezing the name
    # instead — min-width:0 with the controls pinned — left "Productivity" broken
    # across three lines inside 61px, which is worse than a wrapped row.
    # overflow-wrap stays for the one case wrapping cannot help: a single word
    # longer than the column.
    ".bm-cat > div:first-child{flex-wrap:wrap}"
    ".bm-cat > div:first-child > span:first-child"
    "{min-width:0;overflow-wrap:anywhere}"
    ".bm-cat .cat-grip,.bm-cat .cat-del{flex:0 0 auto}"
)


# ── Link target as a user setting ───────────────────────────────────────────
# The page already decides this, but from an authoring prop:
#
#     linkTarget: (this.props.openInNewTab ?? true) ? '_blank' : '_self'
#
# props come from the x-dc script's data-props, so it was baked in and not
# something the dashboard's user could change. This moves it into the theme
# object, which is what the settings panel already writes to and what the config
# service already persists — so the choice follows you to any browser.
#
# Stored as sameTab, defaulting from the prop with ?? rather than ||: once the
# user has chosen, `false` is a real value that must not fall through to the
# default. Until they touch it, the prop still decides, so nothing changes for an
# existing dashboard.
#
# The row, the toggle and its two style strings mirror "Show bookmark icons"
# exactly, including the polarity — a positive label with the switch on when the
# behaviour is enabled.
LINK_TARGET_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "remember the link target in the theme",
            "      hideBmIcons: !!t.hideBmIcons\n    };",
            "      hideBmIcons: !!t.hideBmIcons,\n"
            "      sameTab: t.sameTab ?? !(this.props.openInNewTab ?? true)\n    };",
        ),
        (
            "a toggle for the link target",
            "  toggleBmIcons = () => this.setTheme({ hideBmIcons: !this.theme().hideBmIcons });",
            "  toggleBmIcons = () => this.setTheme({ hideBmIcons: !this.theme().hideBmIcons });\n"
            "  toggleSameTab = () => this.setTheme({ sameTab: !this.theme().sameTab });",
        ),
        (
            "honour the setting when opening a link",
            "      linkTarget: (this.props.openInNewTab ?? true) ? '_blank' : '_self',",
            "      linkTarget: th.sameTab ? '_self' : '_blank',",
        ),
        (
            "expose the link-target toggle",
            "      toggleBmIcons: this.toggleBmIcons,",
            "      toggleBmIcons: this.toggleBmIcons,\n"
            "      toggleSameTab: this.toggleSameTab,\n"
            "      newTabKnob: 'position:absolute;top:2px;left:'"
            " + (th.sameTab ? '2px' : '20px')"
            " + ';width:16px;height:16px;border-radius:50%;background:#fff;"
            "transition:left .16s ease',\n"
            "      newTabTrack: 'position:relative;width:38px;height:20px;flex:none;"
            "border-radius:20px;cursor:pointer;transition:background .16s ease;"
            "background:' + (th.sameTab ? 'rgba(255,255,255,.16)' : 'var(--accent)'),",
        ),
        (
            "a settings row for the link target",
            '          <span style="font-size:13.5px;color:#dfe3e8">Show bookmark icons</span>\n'
            '          <span sc-camel-on-click="{{ toggleBmIcons }}"'
            ' style="{{ bmIconsTrack }}"><span style="{{ bmIconsKnob }}"></span></span>\n'
            "        </div>",
            '          <span style="font-size:13.5px;color:#dfe3e8">Show bookmark icons</span>\n'
            '          <span sc-camel-on-click="{{ toggleBmIcons }}"'
            ' style="{{ bmIconsTrack }}"><span style="{{ bmIconsKnob }}"></span></span>\n'
            "        </div>\n"
            '        <div style="display:flex;align-items:center;'
            "justify-content:space-between;margin-top:22px;padding-top:18px;"
            'border-top:1px solid rgba(255,255,255,.08)">\n'
            '          <span style="font-size:13.5px;color:#dfe3e8">'
            "Open links in a new tab</span>\n"
            '          <span sc-camel-on-click="{{ toggleSameTab }}"'
            ' style="{{ newTabTrack }}"><span style="{{ newTabKnob }}"></span></span>\n'
            "        </div>",
        ),
    )
)


# ── The placeholder icon that never existed ─────────────────────────────────
# saveModal stores `icon: m.icon || 'homepage'` when nothing was picked, but there
# is no homepage.svg — not in svg/, not among the 24 icons the bundle baked in, and
# not even in the picker's own list. It is a phantom, so every icon-less entry
# requested /icons/homepage.svg and got a 404 and a broken image. That predates the
# containerisation: the original CDN fallback would have 404'd too.
#
# The sentinel is kept rather than renamed, because existing configs already store
# it — mapping it here repairs them instead of leaving them broken. It resolves to
# an inline data: URI, so it cannot 404, needs no network, and is covered by the
# CSP's img-src data:.
#
# It is only ever on screen briefly: the site-icon lookup replaces it, and that now
# always returns something. But "briefly" is not "never" when the service is
# unreachable.
PLACEHOLDER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#3a414d"/>'
    '<circle cx="32" cy="32" r="15" fill="none" stroke="#9aa3ad" stroke-width="3"/>'
    '<path d="M17 32h30M32 17c7 8 7 22 0 30M32 17c-7 8-7 22 0 30" fill="none"'
    ' stroke="#9aa3ad" stroke-width="3"/></svg>'
)
PLACEHOLDER_URI = "data:image/svg+xml;base64," + base64.b64encode(
    PLACEHOLDER_SVG.encode("utf-8")
).decode("ascii")

# ── Falling back to the site's own icon ─────────────────────────────────────
# Saving without picking an icon used to store 'homepage', a generic glyph. Now
# the service is asked for the site's own icon and the entry is updated when it
# arrives, so the save is never blocked on a slow or unreachable host.
#
# The fetch has to happen server-side: the page's CSP is
# img-src 'self' data: blob:, so a cross-origin <img> is refused outright.
# Widening that to img-src * would end the no-external-requests property and leak
# the dashboard's contents to every host in it. See api/favicon.py.
#
# 'homepage' stays as the immediate placeholder and is only replaced if it is
# still there when the answer comes back, so a choice made in the meantime wins.
FAVICON_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "resolve a fetched site icon, and the phantom placeholder",
            "    return '/icons/' + name",
            "    if (name.slice(0, 4) === 'fav:') return '/favicons/' + name.slice(4);\n"
            "    if (name === 'homepage') return '" + PLACEHOLDER_URI + "';\n"
            "    return '/icons/' + name",
        ),
        (
            "ask for the site's icon when none was picked",
            "  saveModal = () => {\n    const m = this.state.modal;",
            """  siteIcon = (id, url, groupId) => {
    fetch('/api/favicon', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    })
      .then(r => (r.ok ? r.json() : r.json().catch(() => ({})).then(e => {
        console.warn('[firehome] no site icon for ' + url + ':', e.error || r.status);
        return null;
      })))
      .then(d => {
        if (!d || !d.icon) return;
        const keep = (e) => (e.id === id && e.icon === 'homepage' ? { ...e, icon: d.icon } : e);
        if (groupId) {
          this.persist({ groups: this.state.groups.map(g => g.id === groupId
            ? { ...g, bookmarks: g.bookmarks.map(keep) } : g) });
        } else {
          this.persist({ apps: this.state.apps.map(keep) });
        }
      })
      .catch(e => console.warn('[firehome] site icon lookup failed:', e.message));
  };
  saveModal = () => {
    const m = this.state.modal;""",
        ),
        (
            "kick off the app icon fetch after saving",
            "      this.persist({ apps, modal: null });",
            "      this.persist({ apps, modal: null });\n"
            "      if (!m.icon) this.siteIcon(entry.id, url, null);",
        ),
        (
            "kick off the bookmark icon fetch after saving",
            "      this.persist({ groups, modal: null });",
            "      this.persist({ groups, modal: null });\n"
            "      if (!m.icon) {\n"
            "        const saved = (groups.find(g => g.id === m.groupId) || { bookmarks: [] })\n"
            "          .bookmarks.slice(-1)[0];\n"
            "        this.siteIcon(m.editId || (saved && saved.id), url, m.groupId);\n"
            "      }",
        ),
    )
)


# ── Whether the lock actually hides the edit controls ───────────────────────
# The lock barely locked anything. It stops dragging and makes the titles
# non-editable, and only the category delete sits inside sc-if editable — the add
# buttons, the card edit/delete overlay and the bookmark delete all still appeared
# on hover *and stayed clickable*, so an app could be deleted while "locked".
#
# Locked now hides them, which is what the lock reads as, and the setting restores
# the old behaviour for anyone who wants the controls regardless.
#
# The label says "hover controls" rather than "edit controls" on purpose. The
# category delete and the drag grip live inside sc-if editable and are *removed
# from the DOM* when locked, which this setting does not and should not undo —
# turning them back on would mean re-enabling contentEditable titles with them,
# since both hang off the same `editable` flag.
#
# Done in CSS off a data-locked attribute on the root rather than by wrapping more
# markup in sc-if, so it is one rule and one prop. pointer-events matters as much
# as opacity: these are hover-revealed by opacity alone, and an invisible button is
# still perfectly clickable.
#
# Scoped to .app-grid and .bm-cat, not [data-nodrag] at large: the settings
# panel's own close button is also data-nodrag="1" and sits inside the root, so a
# blanket rule would leave the panel impossible to close while locked. .hdr-actions
# is left alone for the same reason — the lock button itself lives there.
# Each selector carries its grid so it reaches (0,3,0), matching
# EMPTY_STATE_CSS's `.app-grid .add-app:only-child`. Without that qualifier this
# lost the tie on an empty dashboard and the add button stayed visible while
# locked. Equal specificity is then broken by order, which is why LOCKED_CSS is
# concatenated last.
LOCKED_CSS = (
    '[data-locked="1"] .app-grid .add-app,'
    '[data-locked="1"] .bm-grid .new-cat,'
    '[data-locked="1"] .bm-cat > button,'
    '[data-locked="1"] .app-grid [data-nodrag="1"],'
    '[data-locked="1"] .bm-cat [data-nodrag="1"]'
    "{opacity:0!important;pointer-events:none!important}"
)

LOCKED_HOVER_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "remember whether the lock hides the edit controls",
            "      sameTab: t.sameTab ?? !(this.props.openInNewTab ?? true)\n    };",
            "      sameTab: t.sameTab ?? !(this.props.openInNewTab ?? true),\n"
            "      lockedHover: !!t.lockedHover\n    };",
        ),
        (
            "a toggle for it",
            "  toggleSameTab = () => this.setTheme({ sameTab: !this.theme().sameTab });",
            "  toggleSameTab = () => this.setTheme({ sameTab: !this.theme().sameTab });\n"
            "  toggleLockedHover = () =>"
            " this.setTheme({ lockedHover: !this.theme().lockedHover });",
        ),
        (
            "flag the locked state on the root",
            '<div ref="{{ rootRef }}" style="min-height:100vh;',
            '<div ref="{{ rootRef }}" data-locked="{{ lockedFlag }}"'
            ' style="min-height:100vh;',
        ),
        (
            "expose the locked flag and its toggle",
            "      toggleSameTab: this.toggleSameTab,",
            "      toggleSameTab: this.toggleSameTab,\n"
            "      lockedFlag: (s.locked && !th.lockedHover) ? '1' : '0',\n"
            "      toggleLockedHover: this.toggleLockedHover,\n"
            "      lockedHoverKnob: 'position:absolute;top:2px;left:'"
            " + (th.lockedHover ? '20px' : '2px')"
            " + ';width:16px;height:16px;border-radius:50%;background:#fff;"
            "transition:left .16s ease',\n"
            "      lockedHoverTrack: 'position:relative;width:38px;height:20px;"
            "flex:none;border-radius:20px;cursor:pointer;"
            "transition:background .16s ease;background:'"
            " + (th.lockedHover ? 'var(--accent)' : 'rgba(255,255,255,.16)'),",
        ),
        (
            "a settings row for it",
            '          <span sc-camel-on-click="{{ toggleSameTab }}"'
            ' style="{{ newTabTrack }}"><span style="{{ newTabKnob }}"></span></span>\n'
            "        </div>",
            '          <span sc-camel-on-click="{{ toggleSameTab }}"'
            ' style="{{ newTabTrack }}"><span style="{{ newTabKnob }}"></span></span>\n'
            "        </div>\n"
            '        <div style="display:flex;align-items:center;'
            "justify-content:space-between;margin-top:22px;padding-top:18px;"
            'border-top:1px solid rgba(255,255,255,.08)">\n'
            '          <span style="font-size:13.5px;color:#dfe3e8">'
            "Show hover controls when locked</span>\n"
            '          <span sc-camel-on-click="{{ toggleLockedHover }}"'
            ' style="{{ lockedHoverTrack }}">'
            '<span style="{{ lockedHoverKnob }}"></span></span>\n'
            "        </div>",
        ),
    )
)


# ── Remembering the lock ────────────────────────────────────────────────────
# `locked` lived only in component state, so it reset to unlocked on every reload
# — which makes it a per-visit gesture rather than a setting, and undoes itself
# the moment you refresh.
#
# It rides on the theme object, alongside the other preferences the settings panel
# writes, so the config service persists it and it follows you to any browser. The
# initial read happens after this.state.theme is assigned in the constructor and
# before the first render, so nothing flashes unlocked on the way in.
#
# setTheme is what writes and persists, so toggleLock has to call it rather than
# only setState — the two are kept in step deliberately: everything else reads
# this.state.locked, and rewriting all of those to read the theme would be a much
# wider change for no gain.
LOCK_PERSIST_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "restore the lock on load",
            "    this.state.themeOpen = false;",
            "    this.state.themeOpen = false;\n"
            "    this.state.locked = !!this.state.theme.locked;",
        ),
        (
            "persist the lock when toggled",
            "  toggleLock = () => this.setState(s => ({ locked: !s.locked }));",
            "  toggleLock = () => {\n"
            "    const locked = !this.state.locked;\n"
            "    this.setState({ locked });\n"
            "    this.setTheme({ locked });\n"
            "  };",
        ),
    )
)


# ── Themes in a dropdown ────────────────────────────────────────────────────
# The 18 built-in themes were a two-column grid of buttons — nine rows, most of
# the settings panel. A select is one row.
#
# The select is controlled, so it needs to know which preset is active, and the
# theme is stored as bg/fg/accent rather than as a name or index. themeIndex finds
# the matching preset using the same comparison the grid used for its highlight,
# and yields '' when the colours match none of them — which happens as soon as the
# colour pickers above are touched. That case gets an explicit "Custom" option
# instead of a blank select, and choosing it is a no-op.
#
# The trade-off is losing the colour swatch each button carried; the panel's own
# accent and background pickers sit directly above, so the preview is still there.
# Option lists are drawn by the OS, so colour-scheme:dark is set to stop the popup
# rendering light against this panel.
THEME_SELECT_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "which preset is currently active",
            "      themeList: (s.themes || []).map((t, i) => ({",
            "      themeIndex: (() => {\n"
            "        const i = (s.themes || []).findIndex("
            "t => t.bg === th.bg && t.accent === th.accent);\n"
            "        return i < 0 ? '' : String(i);\n"
            "      })(),\n"
            "      themeList: (s.themes || []).map((t, i) => ({",
        ),
        (
            "choosing a theme from the dropdown",
            "  pickTheme = (e) => {",
            "  chooseTheme = (e) => {\n"
            "    const raw = e.currentTarget.value;\n"
            "    if (raw === '') return;\n"
            "    const t = (this.state.themes || [])[+raw];\n"
            "    if (t) this.setTheme({ bg: t.bg, fg: t.fg, accent: t.accent });\n"
            "  };\n"
            "  pickTheme = (e) => {",
        ),
        (
            "expose the dropdown handler",
            "      pickTheme: this.pickTheme,",
            "      pickTheme: this.pickTheme,\n"
            "      chooseTheme: this.chooseTheme,",
        ),
        (
            "replace the theme grid with a select",
            '        <div style="display:grid;grid-template-columns:'
            'repeat(2,minmax(0,1fr));gap:9px">\n'
            '          <sc-for list="{{ themeList }}" as="t"'
            ' hint-placeholder-count="8">\n'
            '            <button data-i="{{ t.i }}"'
            ' sc-camel-on-click="{{ pickTheme }}" style="{{ t.style }}">\n'
            '              <span style="{{ t.dotStyle }}"></span>\n'
            '              <span style="white-space:nowrap;overflow:hidden;'
            'text-overflow:ellipsis">{{ t.name }}</span>\n'
            "            </button>\n"
            "          </sc-for>\n"
            "        </div>",
            '        <select value="{{ themeIndex }}" data-k="theme"'
            ' sc-camel-on-change="{{ chooseTheme }}" style="'
            + SELECT_STYLE + '">\n'
            '          <option value="">Custom</option>\n'
            '          <sc-for list="{{ themeList }}" as="t"'
            ' hint-placeholder-count="8">\n'
            '            <option value="{{ t.i }}">{{ t.name }}</option>\n'
            "          </sc-for>\n"
            "        </select>",
        ),
    )
)


# ── Typing a hex colour ─────────────────────────────────────────────────────
# Accent and Background each offered five swatches and an <input type="color">
# labelled "Custom". The native picker is the wrong tool for a colour you already
# know: it opens an OS dialog and has no way to accept #1e2430 as text. The label
# is replaced by the field it was describing.
#
# The field is controlled, but on a *draft* rather than on the theme: it holds
# whatever has been typed, and the theme is only written once that text parses.
# Validating against the theme value directly is the trap the font size rows hit
# from the other side — a controlled input whose handler ignores anything not yet
# a full hex re-renders the old value on every rejected keystroke, so "#1e2430"
# can never be typed one character at a time. The draft is dropped on blur, which
# is also what discards a half-typed or nonsense value and snaps the field back to
# the colour actually in use.
#
# on-change, not on-input: React's onChange *is* the input event, so it fires per
# keystroke and keeps the input on React's supported controlled path.
#
# Both #abc and #aabbcc are accepted, with or without the #, in either case.
# Shorthand is expanded and the result lowercased because the value is handed
# straight to <input type="color">, which silently resets to #000000 for anything
# that is not exactly #rrggbb.
#
# The rows can now wrap: five swatches, the picker and a 92px field exceed the
# panel's width on a phone, and wrapping beats the horizontal scroll the panel
# would otherwise get.
HEX_FIELD_STYLE = (
    "width:92px;flex:none;background:rgba(255,255,255,.05);"
    "border:1px solid rgba(255,255,255,.15);border-radius:7px;padding:6px 8px;"
    "font-size:13px;color:#eceef1;font-family:inherit;color-scheme:dark"
)

HEX_ROW_STYLE = (
    "display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:24px"
)


def _hex_row(key, label):
    """The swatch row for one colour, with "Custom" traded for a hex field.

    Anchored on the whole row so the two rows — identical but for their swatch
    list and colour key — each match exactly once.
    """
    swatches = (
        '          <sc-for list="{{ ' + key + 'Swatches }}" as="sw"'
        ' hint-placeholder-count="5">\n'
        '            <span data-k="{{ sw.k }}" data-color="{{ sw.color }}"'
        ' sc-camel-on-click="{{ pickSwatch }}" style="{{ sw.style }}"></span>\n'
        "          </sc-for>\n"
    )
    picker = (
        '<input type="color" value="{{ ' + key + 'Val }}" data-k="' + key + '"'
        ' sc-camel-on-input="{{ colorChange }}" style="width:30px;height:30px;'
        "padding:0;border:none;background:none;border-radius:6px;cursor:pointer"
    )
    old = (
        '        <div style="display:flex;align-items:center;gap:12px;'
        'margin-bottom:24px">\n'
        + swatches
        + '          <label style="display:flex;align-items:center;gap:7px;'
        'margin-left:4px;font-size:12.5px;color:#9aa3ad;cursor:pointer">\n'
        "            " + picker + '">Custom\n'
        "          </label>\n"
        "        </div>"
    )
    new = (
        '        <div style="' + HEX_ROW_STYLE + '">\n'
        + swatches
        + "          " + picker + ';margin-left:4px">\n'
        '          <input type="text" value="{{ ' + key + 'Hex }}"'
        ' data-k="' + key + '" sc-camel-on-change="{{ hexColor }}"'
        ' sc-camel-on-blur="{{ hexBlur }}" sc-camel-max-length="7"'
        ' sc-camel-spell-check="{{ false }}" sc-camel-auto-complete="off"'
        ' sc-camel-auto-capitalize="off" placeholder="#rrggbb"'
        ' aria-label="' + label + ' as a hex value"'
        ' style="' + HEX_FIELD_STYLE + '">\n'
        "        </div>"
    )
    return old, new


HEX_COLOR_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "what each hex field shows",
            "      accentVal: th.accent,\n"
            "      bgVal: th.bg,",
            "      accentVal: th.accent,\n"
            "      bgVal: th.bg,\n"
            "      accentHex: Component.hexShown(s, th, 'accent'),\n"
            "      bgHex: Component.hexShown(s, th, 'bg'),",
        ),
        (
            "parse and apply a typed hex",
            "  colorChange = (e) => this.setTheme("
            "{ [e.currentTarget.dataset.k]: e.currentTarget.value });",
            """  colorChange = (e) => this.setTheme({ [e.currentTarget.dataset.k]: e.currentTarget.value });
  static hexShown(s, th, k) {
    const draft = s.hexDraft || {};
    return draft[k] == null ? th[k] : draft[k];
  }
  static parseHex(raw) {
    const s = String(raw).trim().replace(/^#/, '').toLowerCase();
    if (/^[0-9a-f]{3}$/.test(s)) return '#' + s[0] + s[0] + s[1] + s[1] + s[2] + s[2];
    return /^[0-9a-f]{6}$/.test(s) ? '#' + s : null;
  }
  setHexDraft = (k, v) => this.setState({ hexDraft: { ...this.state.hexDraft, [k]: v } });
  hexColor = (e) => {
    const k = e.currentTarget.dataset.k, raw = e.currentTarget.value;
    this.setHexDraft(k, raw);
    const parsed = Component.parseHex(raw);
    if (parsed) this.setTheme({ [k]: parsed });
  };
  hexBlur = (e) => this.setHexDraft(e.currentTarget.dataset.k, null);""",
        ),
        (
            "expose the hex handlers",
            "      colorChange: this.colorChange,",
            "      colorChange: this.colorChange,\n"
            "      hexColor: this.hexColor,\n"
            "      hexBlur: this.hexBlur,",
        ),
        ("the accent hex field",) + _hex_row("accent", "Accent color"),
        ("the background hex field",) + _hex_row("bg", "Background color"),
    )
)


# ── Font sizes ──────────────────────────────────────────────────────────────
# Five sizes, covering every piece of dashboard text except the date and greeting.
# The two section headings share one control: they are the same element (an h2 with
# data-k) at the same visual level, so splitting them would be two settings for one
# decision.
#
# Sizes live in inline styles, so the rules need !important. The values are
# per-user, and the stylesheet is static at build time, so they travel as CSS
# custom properties that applyTheme publishes on the body — the same route --accent
# already takes. The var() fallbacks are the stock values, so the page still looks
# right if a property is ever missing.
#
# The selectors are structural, since none of these elements carries a class:
#   * both headings via data-k, which is stable and explicit.
#   * the card's title and description are the two spans in its inner column. The
#     :not([data-nodrag]) matters — the edit/delete overlay is another direct span
#     child of the anchor, and its icons would otherwise be resized too.
#   * a category name is the first span of the header row; the grip and delete are
#     later siblings.
#   * a bookmark title has no size of its own, inheriting 14.5px from its anchor,
#     so the anchor is what gets sized.
FONT_SIZES = (
    ("fsSection", "--fs-section", "19px", "Section headings"),
    ("fsAppTitle", "--fs-app-title", "14.5px", "Application name"),
    ("fsAppDesc", "--fs-app-desc", "11.2px", "Application description"),
    ("fsCatName", "--fs-cat-name", "15px", "Bookmark category"),
    ("fsBmTitle", "--fs-bm-title", "14.5px", "Bookmark name"),
)

FONT_SIZE_CSS = (
    '[data-k="appsTitle"],[data-k="bookmarksTitle"]'
    "{font-size:var(--fs-section,19px)!important}"
    ".app-grid a[data-drag-id] > span:not([data-nodrag]) > span:first-child"
    "{font-size:var(--fs-app-title,14.5px)!important}"
    ".app-grid a[data-drag-id] > span:not([data-nodrag]) > span:last-child"
    "{font-size:var(--fs-app-desc,11.2px)!important}"
    ".bm-cat > div:first-child > span:first-child"
    "{font-size:var(--fs-cat-name,15px)!important}"
    ".bm-cat a[data-drag-id]{font-size:var(--fs-bm-title,14.5px)!important}"
)


def _font_size_rows():
    """One label-and-number-input row per size, in the panel's input style.

    defaultValue, not value. A controlled input plus a handler that refuses
    out-of-range numbers means React re-renders the old value on every keystroke,
    so the field cannot be typed into at all — clearing it snaps back, and "2"
    then "4" never becomes 24. Only the spinner arrows worked, 0.5 at a time.
    Uncontrolled lets typing through and still commits each valid value on change.
    The panel is rebuilt from the theme whenever it opens, so the field is always
    correct on the way in.
    """
    out = []
    for key, _prop, _default, label in FONT_SIZES:
        out.append(
            '        <div style="display:flex;align-items:center;'
            'justify-content:space-between;gap:12px;margin-bottom:9px">\n'
            '          <span style="font-size:13px;color:#9aa3ad">' + label + "</span>\n"
            '          <input type="number" data-k="' + key + '"'
            ' sc-camel-default-value="{{ ' + key + ' }}"'
            ' min="8" max="40" step="0.5" sc-camel-on-change="{{ fontSize }}"'
            ' style="width:74px;flex:none;background:rgba(255,255,255,.05);'
            "border:1px solid rgba(255,255,255,.15);border-radius:7px;padding:6px 8px;"
            'font-size:13px;color:#eceef1;font-family:inherit;color-scheme:dark">\n'
            "        </div>"
        )
    return "\n".join(out)


FONT_SIZE_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "font sizes on the theme",
            "      lockedHover: !!t.lockedHover\n    };",
            "      lockedHover: !!t.lockedHover,\n"
            + "".join(
                "      %s: +t.%s || %s,\n" % (key, key, default[:-2])
                for key, _prop, default, _label in FONT_SIZES
            ).rstrip(",\n")
            + "\n    };",
        ),
        (
            "publish the sizes as custom properties",
            "    document.body.style.setProperty('--accent', t.accent);",
            "    document.body.style.setProperty('--accent', t.accent);\n"
            + "".join(
                "    document.body.style.setProperty('%s', t.%s + 'px');\n"
                % (prop, key)
                for key, prop, _default, _label in FONT_SIZES
            ).rstrip("\n"),
        ),
        (
            "a handler for the size inputs",
            "  toggleLockedHover = ()",
            "  fontSize = (e) => {\n"
            "    const key = e.currentTarget.dataset.k;\n"
            "    const size = parseFloat(e.currentTarget.value);\n"
            "    if (!isFinite(size) || size < 8 || size > 40) return;\n"
            "    this.setTheme({ [key]: size });\n"
            "  };\n"
            "  toggleLockedHover = ()",
        ),
        (
            "expose the sizes and the handler",
            "      toggleLockedHover: this.toggleLockedHover,",
            "      toggleLockedHover: this.toggleLockedHover,\n"
            "      fontSize: this.fontSize,\n"
            + "".join(
                "      %s: th.%s,\n" % (key, key)
                for key, _prop, _default, _label in FONT_SIZES
            ).rstrip("\n"),
        ),
        (
            "a font size section in the panel",
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            'text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px">Color theme</div>',
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px\">Font sizes</div>\n"
            + _font_size_rows()
            + "\n"
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            'text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px">Color theme</div>',
        ),
    )
)


# ── Mobile layout ───────────────────────────────────────────────────────────
# The page was not responsive at all: both grids carry hardcoded column counts,
# so on a 390px phone the applications sat in 4 columns of 69px — every title
# truncated to "P.." — and the categories in 5 columns of 50px, where the names
# overlapped the delete buttons. That is fixed for everyone below the breakpoint,
# independently of the setting.
#
# "Mobile" is decided by viewport width, not by sniffing the user agent: a narrow
# desktop window gets the same treatment, which is what makes it responsive rather
# than device-specific.
#
# The setting governs the application grid, which is the part with three sensible
# presentations. Categories are always two per row, since five columns of anything
# is wrong on a phone. Two columns leaves ~163px per category, which is what
# exposed the header overflow that CATEGORY_GRIP_CSS now fixes.
MOBILE_BREAKPOINT = "768px"
# Categories per row on a phone. Five was the desktop count and unusable there.
MOBILE_CATEGORY_COLUMNS = "2"
MOBILE_LAYOUTS = (
    ("both", "Icon and text"),
    ("icons", "Icons only"),
    ("text", "Text only"),
)

MOBILE_CSS = (
    "@media (max-width:" + MOBILE_BREAKPOINT + "){"
    # Baseline, whatever the mode. Two categories per row rather than five, and a
    # greeting that does not wrap into three lines and shove the header buttons
    # around.
    ".bm-grid{grid-template-columns:repeat(" + MOBILE_CATEGORY_COLUMNS
    + ",minmax(0,1fr))!important}"
    ".hdr-row h1{font-size:34px!important}"
    # Icon and text, one per row.
    '[data-mobile="both"] .app-grid,'
    '[data-mobile="text"] .app-grid{grid-template-columns:1fr!important}'
    # Icons only, packed into as many columns as fit.
    '[data-mobile="icons"] .app-grid'
    "{grid-template-columns:repeat(auto-fill,minmax(58px,1fr))!important}"
    '[data-mobile="icons"] .app-grid a[data-drag-id] > span:not([data-nodrag]),'
    '[data-mobile="icons"] .add-app span:last-child{display:none!important}'
    '[data-mobile="icons"] .app-grid a[data-drag-id],'
    '[data-mobile="icons"] .add-app{justify-content:center!important}'
    # The per-card edit/delete overlay is forced visible on touch, and at 58px it
    # covers the whole icon — obscuring the one thing this mode exists to show.
    # Icons-only is a launcher view; switch to Icon and text to edit an entry.
    '[data-mobile="icons"] .app-grid a[data-drag-id] > span[data-nodrag]'
    "{display:none!important}"
    # Text only — the app icon and the bookmark icons go.
    '[data-mobile="text"] .app-grid a[data-drag-id] > img,'
    '[data-mobile="text"] .bm-cat a[data-drag-id] img{display:none!important}'
    "}"
)


# ── Swipe the greeting to reveal the header buttons ─────────────────────────
# The settings and lock buttons are hover-revealed on desktop, and TOUCH_CSS has
# to force them visible where there is no hover — so on a phone they sit
# permanently beside the greeting. This parks them just off the right edge and
# brings them in on a leftward drag, driven by header-swipe.js.
#
# Gated three ways, because an unreachable lock button means a dashboard nobody
# can unlock:
#
#   * the breakpoint, so a wide window is untouched;
#   * (hover:none), so a narrow *desktop* window keeps the hover reveal — there
#     is no way to swipe with a mouse, and this is the only reason the media
#     query is not simply MOBILE_CSS's;
#   * [data-hdr-swipe="1"], which the script sets on <html> once it is running.
#     No script, no change in behaviour.
#
# The greeting and the buttons both move off one variable: --hdr-slide, 0 to
# --hdr-reveal, written by the script during the drag. The transition is
# suppressed while a finger is down (data-hdr-drag) so the movement tracks the
# finger rather than lagging a fifth of a second behind it.
HEADER_SWIPE_CSS = (
    "@media (max-width:" + MOBILE_BREAKPOINT + ") and (hover:none){"
    # overflow:hidden is what actually hides the parked buttons. The settings
    # panel is a fixed overlay elsewhere in the DOM, so it is not clipped by this.
    # pan-y keeps vertical scrolling native while horizontal drags come to us.
    '[data-hdr-swipe="1"] .hdr-row'
    "{position:relative;overflow:hidden;touch-action:pan-y}"
    '[data-hdr-swipe="1"] .hdr-row h1'
    "{flex:1 1 auto;min-width:0;transform:translateX(calc(0px - var(--hdr-slide,0px)))}"
    # position and padding are inline on the element, so they need !important.
    '[data-hdr-swipe="1"] .hdr-row .hdr-actions'
    "{position:absolute!important;right:0;top:0;height:100%;padding-bottom:0!important;"
    "transform:translateX(calc(var(--hdr-reveal,120px) - var(--hdr-slide,0px)))}"
    '[data-hdr-swipe="1"]:not([data-hdr-drag="1"]) .hdr-row h1,'
    '[data-hdr-swipe="1"]:not([data-hdr-drag="1"]) .hdr-row .hdr-actions'
    "{transition:transform .22s ease}"
    # Something has to say the gesture exists. A chevron at the edge, fading out
    # as the drag progresses — off --hdr-progress rather than --hdr-slide because
    # calc() cannot divide a length by a length.
    '[data-hdr-swipe="1"] .hdr-row::after'
    '{content:"‹";position:absolute;right:2px;top:50%;transform:translateY(-50%);'
    "pointer-events:none;font-size:22px;line-height:1;color:#6b7480;"
    "opacity:calc(0.5 - var(--hdr-progress,0) * 0.5)}"
    "}"
)


def _mobile_options():
    return "\n".join(
        '            <option value="%s">%s</option>' % (value, label)
        for value, label in MOBILE_LAYOUTS
    )


MOBILE_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "remember the mobile layout",
            "      lockedHover: !!t.lockedHover,",
            "      lockedHover: !!t.lockedHover,\n"
            "      mobileLayout: t.mobileLayout || 'both',",
        ),
        (
            "flag the mobile layout on the root",
            '<div ref="{{ rootRef }}" data-locked="{{ lockedFlag }}"',
            '<div ref="{{ rootRef }}" data-locked="{{ lockedFlag }}"'
            ' data-mobile="{{ mobileLayout }}"',
        ),
        (
            "choosing a mobile layout",
            "  toggleLockedHover = ()",
            "  chooseMobile = (e) =>"
            " this.setTheme({ mobileLayout: e.currentTarget.value });\n"
            "  toggleLockedHover = ()",
        ),
        (
            "expose the mobile layout and its handler",
            "      toggleLockedHover: this.toggleLockedHover,",
            "      toggleLockedHover: this.toggleLockedHover,\n"
            "      mobileLayout: th.mobileLayout,\n"
            "      chooseMobile: this.chooseMobile,",
        ),
        (
            "a mobile layout row in the panel",
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            'text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px">Font sizes</div>',
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px\">"
            "Mobile layout</div>\n"
            '        <select value="{{ mobileLayout }}" data-k="mobileLayout"'
            ' sc-camel-on-change="{{ chooseMobile }}" style="'
            + SELECT_STYLE + '">\n'
            + _mobile_options()
            + "\n        </select>\n"
            '        <div style="font-size:11.5px;color:#6d757e;margin-top:7px">'
            "Applies on screens under " + MOBILE_BREAKPOINT + ".</div>\n"
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            'text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px">Font sizes</div>',
        ),
    )
)


# ── Keyboard shortcuts ──────────────────────────────────────────────────────
# l locks, a adds an application, s opens Settings, Space focuses the search bar.
#
# Hung off the existing window keydown handler (the one that owns Escape and
# Enter) rather than a second listener, so there is one place where a key can be
# swallowed.
#
# Bare letters are only safe if they cannot fire while the user is typing, and
# this page has more typing surfaces than it looks: the two section headings are
# contentEditable when unlocked, the add/edit dialog has four fields plus an
# icon search, Settings has number inputs, selects and the custom search URL,
# and the search bar itself. _typing() covers all of them by shape rather than
# by name — input/textarea/select or anything contentEditable — because the next
# field someone adds will not be on a list here.
#
# The other guards:
#
#   * modifiers are excluded, so Ctrl-L (address bar) and Cmd-A still work;
#   * isComposing, because a letter mid-IME-composition is not a shortcut;
#   * a dialog being open suppresses everything, since Escape and Enter own the
#     keyboard at that point;
#   * `a` respects the lock. LOCKED_CSS gives the add button
#     pointer-events:none, so opening the dialog by keyboard would be reaching
#     past a control the lock has deliberately disabled.
#
# Space is the awkward one: it is the native activation key for a focused
# button, so it is only claimed when focus is not on one. Otherwise tabbing to
# the lock button and pressing Space would focus the search bar instead of
# toggling the lock. preventDefault stops the page scrolling.
KEYBINDS = (
    ("L", "Lock or unlock the layout"),
    ("A", "Add an application"),
    ("S", "Open settings"),
    ("Space", "Focus the search bar"),
)


def _keybind_rows():
    """The shortcut list for the settings panel."""
    return "\n".join(
        '          <div style="display:flex;align-items:center;gap:10px;'
        'margin-top:7px">\n'
        '            <kbd style="min-width:20px;padding:2px 7px;border-radius:5px;'
        "border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.06);"
        "font-family:inherit;font-size:11.5px;font-weight:700;color:#dfe3e8;"
        'text-align:center">%s</kbd>\n'
        '            <span style="font-size:12.5px;color:#9aa3ad">%s</span>\n'
        "          </div>" % (key, label)
        for key, label in KEYBINDS
    )


KEYBIND_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "list the shortcuts in the panel",
            '        <div style="display:flex;align-items:center;'
            "justify-content:space-between;margin-top:22px;padding-top:18px;"
            'border-top:1px solid rgba(255,255,255,.08)">\n'
            '          <button sc-camel-on-click="{{ resetDefaults }}"',
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 4px\">"
            "Keyboard shortcuts</div>\n"
            + _keybind_rows()
            + "\n"
            '        <div style="font-size:11.5px;color:#6d757e;margin-top:9px">'
            "Ignored while typing, and with a modifier held.</div>\n"
            '        <div style="display:flex;align-items:center;'
            "justify-content:space-between;margin-top:22px;padding-top:18px;"
            'border-top:1px solid rgba(255,255,255,.08)">\n'
            '          <button sc-camel-on-click="{{ resetDefaults }}"',
        ),
        (
            "shortcuts on the window key handler",
            """    this._onKey = (e) => {
      if (e.key === 'Escape') this.setState({ modal: null, themeOpen: false, ctx: null });""",
            """    this._typing = (el) => {
      if (!el) return false;
      if (el.isContentEditable) return true;
      const tag = el.tagName;
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
    };
    this._onKey = (e) => {
      if (e.key === 'Escape') this.setState({ modal: null, themeOpen: false, ctx: null });
      if (!e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.isComposing
          && !this.state.modal && !this._typing(e.target)) {
        const k = e.key.toLowerCase();
        if (k === 'l') { e.preventDefault(); this.toggleLock(); }
        if (k === 's') { e.preventDefault(); this.setState({ themeOpen: true, themePos: { x: 0, y: 0 } }); }
        // The lock disables the add button, so the shortcut must not bypass it.
        if (k === 'a' && !this.state.locked) { e.preventDefault(); this.openAddApp(); }
        // Space activates a focused button natively; only claim it otherwise.
        if (e.key === ' ' && !(e.target && e.target.closest && e.target.closest('button'))) {
          const box = this.rootRef.current
            && this.rootRef.current.querySelector('.hdr-search input[name=q]');
          if (box) { e.preventDefault(); box.focus(); }
        }
      }""",
        ),
    )
)


def _engine_options():
    return "\n".join(
        '            <option value="%s">%s</option>' % (key, label)
        for key, label, _url in SEARCH_ENGINES
    )


# ── Choosing the search engine ──────────────────────────────────────────────
# The picker writes searchEngine (a key) and searchUrl (the custom template) to
# the theme, the same store the rest of the settings use, so the choice
# survives a reload and rides along with the existing persistence.
#
# runSearch resolves the key to a URL template, substitutes the query and
# assigns location. Three things it has to get right:
#
#   * preventDefault always, even when there is nowhere to send the query —
#     otherwise the form falls back to its native GET, which reloads the
#     dashboard with ?q=... and loses what was typed.
#   * encodeURIComponent, then + for spaces: encodeURIComponent gives %20,
#     which every engine accepts, but + is what a native form would have sent
#     and what the engines' own boxes produce, so the resulting URL looks
#     ordinary rather than machine-made.
#   * a blank or %s-less custom URL is a no-op rather than a navigation to
#     nowhere. The settings panel says so, and the placeholder says so, so the
#     failure is visible before Enter is ever pressed.
SEARCH_ENGINE_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "remember the engine and any custom URL",
            "      mobileLayout: t.mobileLayout || 'both',",
            "      mobileLayout: t.mobileLayout || 'both',\n"
            "      searchEngine: t.searchEngine || '%s',\n"
            "      searchUrl: t.searchUrl || ''," % SEARCH_ENGINES[0][0],
        ),
        (
            "resolve the engine, and run the search",
            "  chooseMobile = (e) =>",
            "  static SEARCH_ENGINES() {\n"
            "    return "
            + json.dumps(
                [{"key": k, "label": l, "url": u} for k, l, u in SEARCH_ENGINES]
            )
            + ";\n"
            "  }\n"
            "  searchTemplate() {\n"
            "    const th = this.theme();\n"
            "    const e = Component.SEARCH_ENGINES()"
            ".find(x => x.key === th.searchEngine);\n"
            "    const url = (e && e.url) || th.searchUrl || '';\n"
            "    return url.indexOf('%s') >= 0 ? url : '';\n"
            "  }\n"
            "  chooseEngine = (e) =>"
            " this.setTheme({ searchEngine: e.currentTarget.value });\n"
            "  searchUrlChange = (e) =>"
            " this.setTheme({ searchUrl: e.currentTarget.value.trim() });\n"
            "  runSearch = (e) => {\n"
            "    e.preventDefault();\n"
            "    const tpl = this.searchTemplate();\n"
            "    const input = e.currentTarget.querySelector('input[name=q]');\n"
            "    const q = input ? input.value.trim() : '';\n"
            "    if (!tpl || !q) return;\n"
            "    const enc = encodeURIComponent(q).replace(/%20/g, '+');\n"
            "    window.location.assign(tpl.replace('%s', enc));\n"
            "  };\n"
            "  chooseMobile = (e) =>",
        ),
        (
            "expose the engine, its handlers and the hint",
            "      mobileLayout: th.mobileLayout,",
            "      mobileLayout: th.mobileLayout,\n"
            "      searchEngine: th.searchEngine,\n"
            "      searchUrl: th.searchUrl,\n"
            "      chooseEngine: this.chooseEngine,\n"
            "      searchUrlChange: this.searchUrlChange,\n"
            "      runSearch: this.runSearch,\n"
            "      customEngine: " + json.dumps(list(SEARCH_CUSTOM_KEYS))
            + ".indexOf(th.searchEngine) >= 0,\n"
            "      searchHint: this.searchTemplate() ? 'Search the web…'"
            " : 'Set a search URL in Settings…',",
        ),
        (
            "an engine row in the panel",
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px\">"
            "Mobile layout</div>",
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px\">"
            "Search engine</div>\n"
            '        <select value="{{ searchEngine }}" data-k="searchEngine"'
            ' sc-camel-on-change="{{ chooseEngine }}" style="'
            + SELECT_STYLE + '">\n'
            + _engine_options()
            + "\n        </select>\n"
            '        <sc-if value="{{ customEngine }}"'
            ' hint-placeholder-val="{{ false }}">\n'
            '          <input type="url" value="{{ searchUrl }}"'
            ' sc-camel-on-change="{{ searchUrlChange }}"'
            ' placeholder="https://searx.lan/search?q=%s"'
            ' style="width:100%;box-sizing:border-box;margin-top:9px;'
            "background:rgba(255,255,255,.05);"
            "border:1px solid rgba(255,255,255,.15);border-radius:7px;"
            "padding:10px 12px;font-size:13.5px;color:#eceef1;"
            'font-family:inherit;outline:none">\n'
            '          <div style="font-size:11.5px;color:#6d757e;margin-top:7px">'
            "%s is where the query goes. Without it the search bar stays"
            " inactive.</div>\n"
            "        </sc-if>\n"
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px\">"
            "Mobile layout</div>",
        ),
    )
)


# ── Search suggestions ──────────────────────────────────────────────────────
# Typed queries go to /api/suggest, which fetches the engine's own endpoint
# server-side; see api/suggest.py for why a direct browser fetch is impossible.
#
# The parts that matter:
#
#   * Debounced, and every in-flight request is superseded. A stale response
#     arriving after a newer one would otherwise repopulate the list with
#     suggestions for a prefix the user has already typed past — so responses
#     carry the query they were for and are dropped unless it still matches.
#   * The highlighted index is reset on every fresh input, or the selection
#     would point into a list that has since changed under it.
#   * Enter with something highlighted searches that suggestion; Enter with
#     nothing highlighted searches what was typed. Escape closes the list
#     first and only clears the field on a second press, which is what the
#     native search control already does.
#   * Pointerdown on a row, not click: click fires after the input's blur,
#     which closes the list and would leave the click landing on nothing.
SUGGEST_CSS = (
    ".hdr-sugg{position:absolute;left:0;right:0;top:calc(100% + 6px);z-index:60;"
    "margin:0;padding:5px;list-style:none;border-radius:9px;"
    "border:1px solid rgba(255,255,255,.12);background:#2a2f38;"
    "box-shadow:0 12px 34px rgba(0,0,0,.45)}"
    ".hdr-sugg li{padding:8px 11px;border-radius:6px;font-size:13.5px;"
    "color:#dfe3e8;cursor:pointer;white-space:nowrap;overflow:hidden;"
    "text-overflow:ellipsis}"
    '.hdr-sugg li[data-on="1"]{background:rgba(255,255,255,.09);color:#f2f4f6}'
    # The form is the positioning context for the dropdown, and it must not clip
    # it — .hdr-search itself carries the opacity transition, so the list is a
    # child of the form rather than a sibling.
    ".hdr-search form,.hdr-search{position:relative}"
)

SUGGEST_PATCHES = tuple(
    (name, tpl(old), tpl(new))
    for name, old, new in (
        (
            "remember whether suggestions are on",
            "      searchUrl: t.searchUrl || '',",
            "      searchUrl: t.searchUrl || '',\n"
            "      suggest: !!t.suggest,",
        ),
        (
            "suggestion state",
            "  chooseEngine = (e) =>",
            "  toggleSuggest = () => this.setTheme({ suggest: !this.theme().suggest });\n"
            "  suggestAvailable() {\n"
            "    return " + json.dumps(list(SEARCH_SUGGEST_KEYS))
            + ".indexOf(this.theme().searchEngine) >= 0;\n"
            "  }\n"
            "  closeSuggest = () => {\n"
            "    if (this._suggTimer) clearTimeout(this._suggTimer);\n"
            "    this._suggTimer = null;\n"
            "    if (this.state.suggItems && this.state.suggItems.length)\n"
            "      this.setState({ suggItems: [], suggAt: -1 });\n"
            "    else if (this.state.suggAt !== -1) this.setState({ suggAt: -1 });\n"
            "  };\n"
            "  askSuggest = (q) => {\n"
            "    if (!q) return this.closeSuggest();\n"
            "    this._suggFor = q;\n"
            "    fetch('/api/suggest', {\n"
            "      method: 'POST',\n"
            "      headers: { 'Content-Type': 'application/json' },\n"
            "      body: JSON.stringify({ engine: this.theme().searchEngine, q: q })\n"
            "    }).then(r => r.ok ? r.json() : null).then(d => {\n"
            "      // Dropped unless this is still the query being typed, so a\n"
            "      // slow response cannot overwrite a newer one.\n"
            "      if (!d || this._suggFor !== q) return;\n"
            "      const items = (d.suggestions || []).slice(0, "
            + str(SUGGEST_MAX) + ");\n"
            "      this.setState({ suggItems: items, suggAt: -1 });\n"
            "    }).catch(() => {});\n"
            "  };\n"
            "  searchInput = (e) => {\n"
            "    const q = e.currentTarget.value.trim();\n"
            "    if (!this.theme().suggest || !this.suggestAvailable()) return;\n"
            "    if (this._suggTimer) clearTimeout(this._suggTimer);\n"
            "    this._suggTimer = setTimeout(() => this.askSuggest(q), "
            + str(SUGGEST_DEBOUNCE_MS) + ");\n"
            "  };\n"
            "  searchKey = (e) => {\n"
            "    const items = this.state.suggItems || [];\n"
            "    if (e.key === 'Escape') {\n"
            "      // Close the list first; a second Escape is the native clear.\n"
            "      if (items.length) { e.preventDefault(); this.closeSuggest(); }\n"
            "      return;\n"
            "    }\n"
            "    if (!items.length) return;\n"
            "    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {\n"
            "      e.preventDefault();\n"
            "      const at = this.state.suggAt;\n"
            "      const step = e.key === 'ArrowDown' ? 1 : -1;\n"
            "      const next = at + step < 0 ? items.length - 1\n"
            "        : (at + step) % items.length;\n"
            "      this.setState({ suggAt: next });\n"
            "    }\n"
            "  };\n"
            "  pickSuggest = (e) => {\n"
            "    // pointerdown, not click: click lands after blur has closed the list.\n"
            "    e.preventDefault();\n"
            "    const text = e.currentTarget.dataset.sugg || '';\n"
            "    const input = this.rootRef.current\n"
            "      && this.rootRef.current.querySelector('.hdr-search input[name=q]');\n"
            "    if (input) input.value = text;\n"
            "    this.closeSuggest();\n"
            "    this.goSearch(text);\n"
            "  };\n"
            "  chooseEngine = (e) =>",
        ),
        (
            "run the highlighted suggestion, or what was typed",
            "  runSearch = (e) => {\n"
            "    e.preventDefault();\n"
            "    const tpl = this.searchTemplate();\n"
            "    const input = e.currentTarget.querySelector('input[name=q]');\n"
            "    const q = input ? input.value.trim() : '';\n"
            "    if (!tpl || !q) return;\n"
            "    const enc = encodeURIComponent(q).replace(/%20/g, '+');\n"
            "    window.location.assign(tpl.replace('%s', enc));\n"
            "  };",
            "  goSearch = (q) => {\n"
            "    const tpl = this.searchTemplate();\n"
            "    if (!tpl || !q) return;\n"
            "    const enc = encodeURIComponent(q).replace(/%20/g, '+');\n"
            "    window.location.assign(tpl.replace('%s', enc));\n"
            "  };\n"
            "  runSearch = (e) => {\n"
            "    e.preventDefault();\n"
            "    const input = e.currentTarget.querySelector('input[name=q]');\n"
            "    const items = this.state.suggItems || [];\n"
            "    const at = this.state.suggAt;\n"
            "    // A highlighted suggestion wins over the raw text.\n"
            "    const q = (at >= 0 && items[at]) ? items[at]\n"
            "      : (input ? input.value.trim() : '');\n"
            "    this.closeSuggest();\n"
            "    this.goSearch(q);\n"
            "  };",
        ),
        (
            "expose the suggestion list and its handlers",
            "      runSearch: this.runSearch,",
            "      runSearch: this.runSearch,\n"
            "      toggleSuggest: this.toggleSuggest,\n"
            "      searchInput: this.searchInput,\n"
            "      searchKey: this.searchKey,\n"
            "      closeSuggest: this.closeSuggest,\n"
            "      pickSuggest: this.pickSuggest,\n"
            "      suggestOn: th.suggest,\n"
            "      suggestShown: !!(s.suggItems && s.suggItems.length),\n"
            "      suggestAvailable: this.suggestAvailable(),\n"
            "      suggestList: (s.suggItems || []).map((text, i) => ({\n"
            "        text, on: i === s.suggAt ? '1' : '0'\n"
            "      })),\n"
            "      suggestTrack: 'position:relative;width:38px;height:20px;flex:none;"
            "border-radius:20px;cursor:pointer;transition:background .16s ease;"
            "background:' + (th.suggest ? 'var(--accent)' : 'rgba(255,255,255,.16)'),\n"
            "      suggestKnob: 'position:absolute;top:2px;left:'"
            " + (th.suggest ? '20px' : '2px')"
            " + ';width:16px;height:16px;border-radius:50%;background:#fff;"
            "transition:left .16s ease',",
        ),
        (
            "wire the input and hang the list off the form",
            '<input type="search" name="q" autocomplete="off" '
            'aria-label="Search the web" placeholder="{{ searchHint }}">'
            "</form>",
            '<input type="search" name="q" autocomplete="off" '
            'aria-label="Search the web" placeholder="{{ searchHint }}" '
            'sc-camel-on-input="{{ searchInput }}" '
            'sc-camel-on-key-down="{{ searchKey }}" '
            'sc-camel-on-blur="{{ closeSuggest }}">'
            '<sc-if value="{{ suggestShown }}" hint-placeholder-val="{{ false }}">'
            '<ul class="hdr-sugg">'
            '<sc-for list="{{ suggestList }}" as="sg" hint-placeholder-count="4">'
            '<li data-sugg="{{ sg.text }}" data-on="{{ sg.on }}" '
            'sc-camel-on-pointer-down="{{ pickSuggest }}">{{ sg.text }}</li>'
            "</sc-for></ul></sc-if>"
            "</form>",
        ),
        (
            "a suggestions toggle in the panel",
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px\">"
            "Mobile layout</div>",
            '        <sc-if value="{{ suggestAvailable }}"'
            ' hint-placeholder-val="{{ true }}">\n'
            '          <div style="display:flex;align-items:center;'
            'justify-content:space-between;margin-top:14px">\n'
            '            <span style="font-size:13.5px;color:#dfe3e8">'
            "Search suggestions</span>\n"
            '            <span sc-camel-on-click="{{ toggleSuggest }}"'
            ' style="{{ suggestTrack }}">'
            '<span style="{{ suggestKnob }}"></span></span>\n'
            "          </div>\n"
            '          <div style="font-size:11.5px;color:#6d757e;margin-top:7px">'
            "Sends what you type to the engine, through this server, as you"
            " type it.</div>\n"
            "        </sc-if>\n"
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            "text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px\">"
            "Mobile layout</div>",
        ),
    )
)


def apply_js_patches(body: str) -> str:
    """Apply the behavioural patches, asserting each target appears exactly once."""
    for name, old, new in (JS_PATCHES + ICON_UPDATE_PATCHES
                           + CLIPBOARD_PATCHES + PARITY_PATCHES
                           + CATEGORY_DRAG_PATCHES + LINK_TARGET_PATCHES
                           + FAVICON_PATCHES + LOCKED_HOVER_PATCHES
                           + LOCK_PERSIST_PATCHES + THEME_SELECT_PATCHES
                           + FONT_SIZE_PATCHES + MOBILE_PATCHES
                           # After MOBILE_PATCHES: its anchors are the theme key
                           # and settings row that patch adds.
                           + SEARCH_ENGINE_PATCHES
                           # Before SUGGEST_PATCHES: it inserts the form whose
                           # input SUGGEST_PATCHES then wires up. Its own anchor
                           # is the untouched <header>, so it does not need to
                           # wait for anything.
                           + SEARCH_PATCHES
                           # After SEARCH_ENGINE_PATCHES and SEARCH_PATCHES: its
                           # anchors are the runSearch body, the settings rows
                           # and the search input those two leave behind.
                           + SUGGEST_PATCHES
                           # Last: its anchors are what JS_PATCHES and
                           # CLIPBOARD_PATCHES leave behind.
                           # Anywhere: both anchor on export text nothing else
                           # touches.
                           + MODAL_BACKDROP_PATCHES + HEX_COLOR_PATCHES
                           + URL_TITLE_PATCHES + ENTER_SAVE_PATCHES
                           # After ENTER_SAVE_PATCHES: it extends the handler
                           # body that patch rewrites.
                           + KEYBIND_PATCHES
                           + CAT_DELETE_PATCHES):
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
    swipe = open(SWIPE_PATH, encoding="utf-8").read()
    tag = (
        HEAD_MARKUP
        + "<style>" + PAGE_WRAP_CSS + HEADER_CSS + SEARCH_CSS + SUGGEST_CSS
        + ADD_BUTTON_CSS + ADD_LABEL_CSS
        + CARD_MARGIN_CSS
        + HDR_BUTTON_CSS + EMPTY_STATE_CSS + HOVER_FALLBACK_CSS
        + ICON_TILE_CSS + SELECT_CSS + CATEGORY_GRIP_CSS + FONT_SIZE_CSS + TOUCH_CSS
        + MOBILE_CSS + HEADER_SWIPE_CSS + LOCKED_CSS + "</style>"
        + "<script>\n" + shim + "\n</script>"
        + "<script>\n" + swipe + "\n</script>"
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

    # HDR_BUTTON_CSS relies on the two header buttons living in .hdr-actions.
    if 'class=\\"hdr-actions' not in body:
        raise ValueError("no hdr-actions container; HDR_BUTTON_CSS would be inert")

    # ...and on the locked button's title, which is how the accent plate is kept
    # for the one state where the background carries meaning.
    if "'%s" % LOCK_TITLE_PREFIX not in body:
        raise ValueError(
            "lock title no longer starts with %r; the locked-state background "
            "would be flattened along with the rest" % LOCK_TITLE_PREFIX
        )

    n = body.count("<head>")
    if n != 1:
        raise ValueError("expected 1 <head> in the template, found %d" % n)
    body = body.replace("<head>", "<head>" + escaped, 1)

    body = empty_defaults(body)
    body = apply_js_patches(body)

    # SEARCH_CSS is all .hdr-search descendants, so a form that failed to land
    # would leave an unstyled input sitting above the date. Checked after the
    # patches, since SEARCH_PATCHES is what inserts it.
    if 'class=\\"hdr-search\\"' not in body:
        raise ValueError("no hdr-search form; SEARCH_CSS would be inert")

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
