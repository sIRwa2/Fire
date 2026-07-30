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
    ".app-grid .add-app,.hdr-row .hdr-actions,.bm-grid .new-cat,"".bm-cat .cat-del,.bm-cat .cat-grip,"
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
# down.
#
# The button only ever runs on click — no polling — so the container makes no
# outbound request during a normal page load. See api/icons.py.
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
            "      iconBusy: false\n    };",
        ),
        (
            "icon-update handlers",
            "  resetDefaults = () => {",
            """  refreshIcons = () => fetch('/api/icons')
    .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
    .then(d => {
      if (Array.isArray(d.icons) && d.icons.length) {
        this.setState({ icons: d.icons, iconCount: d.count });
      }
      if (d.status && d.status.writable === false && !this.state.iconMsg) {
        this.setState({ iconMsg: 'icon storage is not writable' });
      }
      return d;
    })
    .catch(() => null);
  updateIcons = () => {
    if (this.state.iconBusy) return;
    this.setState({ iconBusy: true, iconMsg: 'checking...' });
    const done = (msg) => this.setState({ iconBusy: false, iconMsg: msg });
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
    fetch('/api/icons/update', { method: 'POST' })
      .then(r => r.ok ? poll() : r.json().then(d => done('failed: ' + (d.error || r.status))))
      .catch(e => done('failed: ' + e.message));
  };
  resetDefaults = () => {""",
        ),
        (
            "icon-update props",
            "      pickIcon: this.pickIcon,",
            "      pickIcon: this.pickIcon,\n"
            "      updateIcons: this.updateIcons,\n"
            "      iconSummary: (s.iconCount || (s.icons || []).length || 0)"
            " + ' icons installed' + (s.iconMsg ? ' - ' + s.iconMsg : ''),\n"
            "      iconBtnLabel: s.iconBusy ? 'Checking...' : 'Check for updates',",
        ),
        (
            "icon-update settings row",
            '        <div style="display:flex;align-items:center;'
            "justify-content:space-between;margin-top:22px;padding-top:18px;"
            'border-top:1px solid rgba(255,255,255,.08)">\n'
            '          <button sc-camel-on-click="{{ resetDefaults }}"',
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            'text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px">Icons</div>\n'
            '        <div style="display:flex;align-items:center;'
            'justify-content:space-between;gap:12px">\n'
            '          <span style="font-size:12.5px;color:#9aa3ad">'
            "{{ iconSummary }}</span>\n"
            '          <button sc-camel-on-click="{{ updateIcons }}" style="flex:none;'
            "background:none;border:1px solid rgba(255,255,255,.18);border-radius:7px;"
            "padding:7px 14px;font-size:12.5px;font-weight:600;color:#dfe3e8;"
            'cursor:pointer;font-family:inherit" '
            'style-hover="background:rgba(255,255,255,.08)">{{ iconBtnLabel }}</button>\n'
            "        </div>\n"
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
    if (!m.title.trim() || m.titleAuto) {
      if (!url) { if (m.titleAuto) { m.title = ''; m.titleAuto = false; } }
      else if (label) { m.title = this.titleFromUrl(url); m.titleAuto = true; }
    }
    if (!(m.q || '').trim() || m.qAuto) {
      // Filtering to a label no icon matches would empty the picker, which is
      // worse than showing everything.
      const hit = !!label && (this.state.icons || []).some(n => n.includes(label));
      if (!hit) { if (m.qAuto) { m.q = ''; m.qAuto = false; } }
      else { m.q = label; m.qAuto = true; }
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
            "a clipboard URL suggests a title as well",
            """      this.setState(s => (s.modal && !s.modal.url)
        ? { modal: { ...s.modal, url: text } } : null);""",
            """      this.setState(s => (s.modal && !s.modal.url)
        ? { modal: this.suggestFromUrl({ ...s.modal, url: text }, text) } : null);""",
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
            ' sc-camel-on-change="{{ chooseTheme }}" style="width:100%;'
            "background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);"
            "border-radius:7px;padding:10px 12px;font-size:14px;color:#eceef1;"
            'font-family:inherit;color-scheme:dark;cursor:pointer">\n'
            '          <option value="">Custom</option>\n'
            '          <sc-for list="{{ themeList }}" as="t"'
            ' hint-placeholder-count="8">\n'
            '            <option value="{{ t.i }}">{{ t.name }}</option>\n'
            "          </sc-for>\n"
            "        </select>",
        ),
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
            ' sc-camel-on-change="{{ chooseMobile }}" style="width:100%;'
            "background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.15);"
            "border-radius:7px;padding:10px 12px;font-size:14px;color:#eceef1;"
            'font-family:inherit;color-scheme:dark;cursor:pointer">\n'
            + _mobile_options()
            + "\n        </select>\n"
            '        <div style="font-size:11.5px;color:#6d757e;margin-top:7px">'
            "Applies on screens under " + MOBILE_BREAKPOINT + ".</div>\n"
            '        <div style="font-size:12px;font-weight:700;letter-spacing:1px;'
            'text-transform:uppercase;color:#9aa3ad;margin:22px 0 11px">Font sizes</div>',
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
                           # Last: its anchors are what JS_PATCHES and
                           # CLIPBOARD_PATCHES leave behind.
                           + URL_TITLE_PATCHES + ENTER_SAVE_PATCHES):
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
        + "<style>" + PAGE_WRAP_CSS + HEADER_CSS + ADD_BUTTON_CSS + ADD_LABEL_CSS
        + CARD_MARGIN_CSS
        + HDR_BUTTON_CSS + EMPTY_STATE_CSS + HOVER_FALLBACK_CSS
        + ICON_TILE_CSS + CATEGORY_GRIP_CSS + FONT_SIZE_CSS + TOUCH_CSS
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
