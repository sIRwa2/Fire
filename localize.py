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

# Bottom margin under the app cards.
#
# They sit closer together than they look: the inline style declares
# padding:12px 14px and then overrides all four sides with padding-*:0px, so a
# card is flush and the only vertical separation is the grid's 14px row gap
# against a 24px column gap. 14px of margin brings the vertical rhythm (14 + 14)
# in line with the horizontal one.
#
# Applied to the add button as well as the cards. Grid items stretch to the row
# height by default, so giving only the cards a margin would leave the button's
# hover area taller than theirs in a shared row.
CARD_BOTTOM_MARGIN = "14px"
CARD_MARGIN_CSS = (
    ".app-grid > a[data-drag-id],.app-grid > .add-app"
    "{margin-bottom:" + CARD_BOTTOM_MARGIN + "!important}"
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
            "resolve a fetched site icon",
            "    return '/icons/' + name",
            "    if (name.slice(0, 4) === 'fav:') return '/favicons/' + name.slice(4);\n"
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
      .then(r => (r.ok ? r.json() : null))
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
      .catch(() => {});
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


def apply_js_patches(body: str) -> str:
    """Apply the behavioural patches, asserting each target appears exactly once."""
    for name, old, new in (JS_PATCHES + ICON_UPDATE_PATCHES
                           + CLIPBOARD_PATCHES + PARITY_PATCHES
                           + CATEGORY_DRAG_PATCHES + LINK_TARGET_PATCHES
                           + FAVICON_PATCHES):
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
        + "<style>" + PAGE_WRAP_CSS + HEADER_CSS + ADD_BUTTON_CSS + ADD_LABEL_CSS
        + CARD_MARGIN_CSS
        + HDR_BUTTON_CSS + EMPTY_STATE_CSS + HOVER_FALLBACK_CSS
        + ICON_TILE_CSS + CATEGORY_GRIP_CSS + TOUCH_CSS + "</style>"
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
