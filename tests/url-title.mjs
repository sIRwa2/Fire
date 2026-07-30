// Does pasting or typing a URL in the add dialog actually fill the Title field,
// in a real browser, and is the URL field still typeable afterwards? The unit
// test (tests/suggest-title.test.mjs) covers the guessing rules against the
// shipped handlers; this covers the dialog they run in — a controlled input with
// a handler that rewrites state is exactly where a field goes untypeable.
//
//     node tests/url-title.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit } from 'playwright';

const DIALOG = '[data-screen-label="Add entry modal"]';
const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '') + '/';
let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const browser = await webkit.launch();
let doc = {
  v1: { apps: [], groups: [{ id: 'g1', name: 'Dev', bookmarks: [] }],
        appsTitle: 'Applications', bookmarksTitle: 'Bookmarks' },
};

const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
page.on('pageerror', e => { fail++; console.log('PAGEERROR ' + e.message); });
await page.route('**/api/config', (route) => {
  const req = route.request();
  if (req.method() !== 'GET') {
    const body = JSON.parse(req.postData() || '{}');
    for (const [k, v] of Object.entries(body.data || {})) v === null ? delete doc[k] : (doc[k] = v);
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"rev":1}' });
  }
  return route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ rev: 1, updated: null, data: doc }) });
});
// The icon-less save path asks the server for a site icon; keep it local.
await page.route('**/api/favicon', route => route.fulfill({ status: 200,
  contentType: 'application/json', body: JSON.stringify({ icon: null, source: null }) }));
await page.addInitScript(() => {
  const Real = window.XMLHttpRequest;
  window.XMLHttpRequest = function () {
    const real = new Real(); let fake = false;
    return new Proxy(real, { get(t, k) {
      if (fake && k === 'status') return 200;
      if (fake && k === 'responseText') return window.__stub;
      if (k === 'open') return (m, u, a) => { fake = m === 'GET' && String(u).includes('/api/config'); if (!fake) t.open(m, u, a); };
      if (k === 'send') return (x) => { if (!fake) t.send(x); };
      const v = t[k]; return typeof v === 'function' ? v.bind(t) : v; } });
  };
});
await page.addInitScript(d => { window.__stub = d; }, JSON.stringify({ rev: 1, data: doc }));
await page.goto(base, { waitUntil: 'networkidle' });
await page.waitForTimeout(1300);

const fields = () => page.evaluate((d) => Object.fromEntries(
  [...document.querySelectorAll(d + ' input')].filter(i => i.dataset.field)
    .map(i => [i.dataset.field, i.value])), DIALOG);
const openAdd = async () => { await page.click('.add-app'); await page.waitForTimeout(400); };
const closeDialog = async () => { await page.keyboard.press('Escape'); await page.waitForTimeout(300); };

// 1. Typed character by character, the way a person does it.
await openAdd();
await page.click(`${DIALOG} input[data-field="url"]`);
await page.keyboard.type('http://gitea.lan', { delay: 15 });
await page.waitForTimeout(300);
let f = await fields();
check('typing a URL fills the Title', f.title === 'Gitea', JSON.stringify(f));
check('the URL field kept every keystroke', f.url === 'http://gitea.lan', JSON.stringify(f));

// 2. The URL field must still be editable after the handler rewrote state.
await page.keyboard.type(':3000', { delay: 15 });
await page.waitForTimeout(250);
f = await fields();
check('the URL field is still typeable', f.url === 'http://gitea.lan:3000', JSON.stringify(f));

// 3. A guess is a suggestion: typing a Title takes it over for good.
await page.click(`${DIALOG} input[data-field="title"]`, { clickCount: 3 });
await page.keyboard.type('My Git', { delay: 15 });
await page.click(`${DIALOG} input[data-field="url"]`, { clickCount: 3 });
await page.keyboard.type('http://grafana.lan', { delay: 15 });
await page.waitForTimeout(300);
f = await fields();
check('a typed Title is not overwritten by a new URL', f.title === 'My Git', JSON.stringify(f));

// 4. Save it, and check what actually landed in the config.
await page.locator(`${DIALOG} button`).last().click();
await page.waitForTimeout(700);
check('the entry saved with the typed title',
  doc.v1.apps.length === 1 && doc.v1.apps[0].title === 'My Git',
  JSON.stringify(doc.v1.apps));

// 5. A paste arrives as one bulk value change rather than a keystroke run, which
//    is what fill() produces. (Assigning input.value by hand would go through
//    React's own value tracker and never reach onChange — a harness artefact, not
//    something a real paste does.)
await openAdd();
await page.locator(`${DIALOG} input[data-field="url"]`).fill('https://www.motogp.com/en/videopass');
await page.waitForTimeout(400);
f = await fields();
check('pasting a URL fills the Title', f.title === 'Motogp', JSON.stringify(f));
await page.locator(`${DIALOG} button`).last().click();
await page.waitForTimeout(700);
check('the pasted entry saved with the guessed title',
  doc.v1.apps.length === 2 && doc.v1.apps[1].title === 'Motogp',
  JSON.stringify(doc.v1.apps.map(a => a.title)));

// 6. The bookmark dialog is the same dialog, so it must behave the same way.
await page.hover('.bm-cat');
await page.waitForTimeout(250);
await page.locator('.bm-cat > button').click();
await page.waitForTimeout(400);
await page.click(`${DIALOG} input[data-field="url"]`);
await page.keyboard.type('http://plex.lan', { delay: 15 });
await page.waitForTimeout(300);
f = await fields();
check('the bookmark dialog guesses too', f.title === 'Plex', JSON.stringify(f));
await page.locator(`${DIALOG} button`).last().click();
await page.waitForTimeout(700);
check('the bookmark saved with the guessed title',
  doc.v1.groups[0].bookmarks.length === 1 && doc.v1.groups[0].bookmarks[0].title === 'Plex',
  JSON.stringify(doc.v1.groups[0].bookmarks));

// 7. The URL also pre-filters the icon grid, against the real icon set.
await openAdd();
const gridCount = () => page.locator(`${DIALOG} span[data-icon]`).count();
const iconNames = () => page.evaluate((d) => [...document.querySelectorAll(d + ' span[data-icon]')]
  .map(s => s.dataset.icon), DIALOG);
const unfiltered = await gridCount();
check('the grid starts unfiltered', unfiltered > 0, unfiltered);
await page.locator(`${DIALOG} input[data-field="url"]`).fill('http://gitea.lan');
await page.waitForTimeout(400);
const filtered = await iconNames();
check('pasting a URL filters the icon grid', filtered.length > 0 && filtered.length < unfiltered,
  `${filtered.length} of ${unfiltered}`);
check('every icon shown matches the host', filtered.every(n => n.includes('gitea')),
  filtered.slice(0, 5).join(','));
check('the search box shows what it filtered by',
  (await fields()).q === 'gitea', JSON.stringify(await fields()));
const outlines = () => page.evaluate((d) => {
  const accent = getComputedStyle(document.body).getPropertyValue('--accent').trim();
  const rgb = (el) => el ? getComputedStyle(el).borderTopColor : null;
  const toHex = (c) => {
    const m = /rgba?\((\d+), ?(\d+), ?(\d+)/.exec(c || '');
    return m ? '#' + [1, 2, 3].map(i => (+m[i]).toString(16).padStart(2, '0')).join('') : c;
  };
  const tiles = [...document.querySelectorAll(d + ' span[data-icon]')];
  return {
    accent: accent.toLowerCase(),
    field: toHex(rgb(document.querySelector(d + ' [data-icon-field]'))),
    fieldFlag: document.querySelector(d + ' [data-icon-field]')?.dataset.iconField,
    outlined: tiles.filter(t => toHex(rgb(t)) === accent.toLowerCase()).map(t => t.dataset.icon),
  };
}, DIALOG);
// The icon is selected too, so Enter alone stores a complete entry. The dialog's
// preview tile is what shows which one — the grid's selected-tile highlight is
// suppressed by ICON_TILE_CSS, which flattens every tile.
const preview = () => page.evaluate((d) => {
  const img = document.querySelector(d + ' img');
  return img ? img.getAttribute('src') : null;
}, DIALOG);
check('the matching icon is selected', await preview() === '/icons/gitea.svg', await preview());

// Both the grid tile and the icon field are outlined, so it is visible which one
// is attached. The tiles are otherwise flattened, so an accent border is the
// whole signal.
let o = await outlines();
check('the chosen tile is outlined in the accent', o.outlined.join(',') === 'gitea', JSON.stringify(o));
check('the icon field is outlined too', o.field === o.accent, JSON.stringify(o));
check('exactly one tile is outlined', o.outlined.length === 1, JSON.stringify(o));

// The grid matches substrings, so left alphabetical the icon you asked for sinks:
// "plex" behind five perplexity variants, "reddit" behind two libreddit ones.
// Ranked, the exact match leads — and it is the one attached.
for (const [url, want, buriedBy] of [
  ['http://plex.lan', 'plex', 'perplexity'],
  ['https://www.reddit.com/', 'reddit', 'libreddit'],
  ['http://nginx.lan', 'nginx', 'nginx-proxy-manager'],
]) {
  await page.locator(`${DIALOG} input[data-field="url"]`).fill(url);
  await page.waitForTimeout(400);
  const shown = await iconNames();
  check(`${want} leads the grid, not ${buriedBy}`, shown[0] === want, shown.slice(0, 3).join(','));
  check(`and ${want} is the icon attached`,
    await preview() === `/icons/${want}.svg`, await preview());
  check(`and ${want} is the outlined tile`,
    (await outlines()).outlined.join(',') === want, JSON.stringify(await outlines()));
}
await page.locator(`${DIALOG} input[data-field="url"]`).fill('http://gitea.lan');
await page.waitForTimeout(400);

// Picking from the filtered grid must not make it jump back to everything.
await page.locator(`${DIALOG} span[data-icon]`).first().click();
await page.waitForTimeout(400);
check('picking an icon leaves the grid filtered',
  (await iconNames()).length === filtered.length, await gridCount());
check('the picked icon is selected',
  (await fields()).title.toLowerCase().includes('gitea'), JSON.stringify(await fields()));

// Clearing the search brings everything back.
await page.locator(`${DIALOG} input[data-field="q"]`).fill('');
await page.waitForTimeout(400);
check('clearing the search restores the full grid', await gridCount() === unfiltered,
  await gridCount());
await closeDialog();

// A URL nothing matches must leave the grid alone rather than empty it.
await openAdd();
await page.locator(`${DIALOG} input[data-field="url"]`).fill('http://zzzznotathing.lan');
await page.waitForTimeout(400);
check('an unmatched host leaves the grid full', await gridCount() === unfiltered, await gridCount());
check('but the title is still guessed',
  (await fields()).title === 'Zzzznotathing', JSON.stringify(await fields()));
// Nothing attached means nothing outlined — the icon field must not claim a
// selection the entry does not have.
o = await outlines();
check('no icon attached leaves the field un-outlined', o.fieldFlag === '0' && o.field !== o.accent,
  JSON.stringify(o));
check('and no tile is outlined', o.outlined.length === 0, JSON.stringify(o));

// Clicking a different icon moves the outline with it.
await page.locator(`${DIALOG} input[data-field="q"]`).fill('grafana');
await page.waitForTimeout(400);
await page.locator(`${DIALOG} span[data-icon="grafana"]`).click();
await page.waitForTimeout(400);
o = await outlines();
check('picking a tile moves the outline', o.outlined.join(',') === 'grafana', JSON.stringify(o));
check('and the icon field follows', o.field === o.accent, JSON.stringify(o));
await closeDialog();

// 8. Paste, Enter, done — the whole point: the entry lands with its icon, and no
//    site-icon fetch is needed because one was already chosen.
await openAdd();
await page.locator(`${DIALOG} input[data-field="url"]`).fill('http://grafana.lan');
await page.waitForTimeout(350);
check('the pasted URL guessed a title before Enter',
  (await fields()).title === 'Grafana', JSON.stringify(await fields()));
await page.keyboard.press('Enter');
await page.waitForTimeout(700);
check('Enter saved the entry',
  doc.v1.apps.length === 3 && doc.v1.apps[2].title === 'Grafana',
  JSON.stringify(doc.v1.apps.map(a => a.title)));
check('and it stored the auto-selected icon',
  doc.v1.apps[2].icon === 'grafana', JSON.stringify(doc.v1.apps[2]));
check('Enter closed the dialog', await page.locator(DIALOG).count() === 0,
  await page.locator(DIALOG).count());

// Enter must do nothing while the dialog is incomplete — the same condition that
// disables the Add button.
await openAdd();
await page.click(`${DIALOG} input[data-field="title"]`);
await page.keyboard.type('No URL yet', { delay: 15 });
await page.keyboard.press('Enter');
await page.waitForTimeout(500);
check('Enter does not save without a URL', doc.v1.apps.length === 3,
  JSON.stringify(doc.v1.apps.map(a => a.title)));
check('the dialog stays open', await page.locator(DIALOG).count() === 1,
  await page.locator(DIALOG).count());

// Enter from the icon search field commits too — all four fields plus the search.
await page.click(`${DIALOG} input[data-field="url"]`);
await page.keyboard.type('http://nginx.lan', { delay: 15 });
await page.click(`${DIALOG} input[data-field="q"]`);
await page.keyboard.press('Enter');
await page.waitForTimeout(700);
check('Enter works from the icon search field too',
  doc.v1.apps.length === 4 && doc.v1.apps[3].title === 'No URL yet',
  JSON.stringify(doc.v1.apps.map(a => a.title)));

// Enter on a focused Cancel must cancel, not save.
await openAdd();
await page.locator(`${DIALOG} input[data-field="url"]`).fill('http://should-not-save.lan');
await page.waitForTimeout(300);
await page.locator(`${DIALOG} button`, { hasText: 'Cancel' }).first().focus();
await page.keyboard.press('Enter');
await page.waitForTimeout(700);
check('Enter on Cancel does not save', doc.v1.apps.length === 4,
  JSON.stringify(doc.v1.apps.map(a => a.title)));
check('Enter on Cancel closed the dialog', await page.locator(DIALOG).count() === 0,
  await page.locator(DIALOG).count());

// With no dialog open, Enter must not even be swallowed: preventDefault on every
// Enter would break activating a focused app link from the keyboard.
check('Enter is left alone when no dialog is open', await page.evaluate(() => {
  const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
  document.body.dispatchEvent(ev);
  return !ev.defaultPrevented;
}), 'preventDefault fired with no modal open');

// And it stays inert outside the dialog: the settings panel has its own fields.
await page.hover('.hdr-row');
await page.waitForTimeout(200);
await page.click('.hdr-actions button[title="Settings"]');
await page.waitForTimeout(500);
await page.keyboard.press('Enter');
await page.waitForTimeout(400);
check('Enter in the settings panel saves nothing', doc.v1.apps.length === 4,
  JSON.stringify(doc.v1.apps.map(a => a.title)));
await closeDialog();

// 9. Editing an existing entry must not have its title rewritten under it.
await page.hover('.app-grid a[data-drag-id]');
await page.waitForTimeout(250);
await page.locator('.app-grid a[data-drag-id] span[data-nodrag] span').first().click();
await page.waitForTimeout(400);
await page.click(`${DIALOG} input[data-field="url"]`, { clickCount: 3 });
await page.keyboard.type('http://jellyfin.lan', { delay: 15 });
await page.waitForTimeout(300);
f = await fields();
check('editing an entry keeps its title', f.title === 'My Git', JSON.stringify(f));
await closeDialog();

await browser.close();

// 8. The clipboard prefill path (maybePasteUrl), which fires on dialog open and is
//    the case this feature is really for: URL already copied, dialog opened, both
//    fields filled in without touching anything. It needs a granted clipboard-read
//    permission, so it runs in Chromium — WebKit has no way to grant it headlessly,
//    and the handler deliberately returns early rather than raise a prompt.
const chromium = (await import('playwright')).chromium;
const cr = await chromium.launch();
const ctx = await cr.newContext({ permissions: ['clipboard-read', 'clipboard-write'],
  viewport: { width: 1400, height: 1000 } });
let doc2 = { v1: { apps: [], groups: [], appsTitle: 'Applications', bookmarksTitle: 'Bookmarks' } };
const cp = await ctx.newPage();
cp.on('pageerror', e => { fail++; console.log('PAGEERROR(chromium) ' + e.message); });
await cp.route('**/api/config', r => r.fulfill({ status: 200, contentType: 'application/json',
  body: JSON.stringify({ rev: 1, updated: null, data: doc2 }) }));
await cp.addInitScript(() => {
  const Real = window.XMLHttpRequest;
  window.XMLHttpRequest = function () {
    const real = new Real(); let fake = false;
    return new Proxy(real, { get(t, k) {
      if (fake && k === 'status') return 200;
      if (fake && k === 'responseText') return window.__stub;
      if (k === 'open') return (m, u, a) => { fake = m === 'GET' && String(u).includes('/api/config'); if (!fake) t.open(m, u, a); };
      if (k === 'send') return (x) => { if (!fake) t.send(x); };
      const v = t[k]; return typeof v === 'function' ? v.bind(t) : v; } });
  };
});
await cp.addInitScript(d => { window.__stub = d; }, JSON.stringify({ rev: 1, data: doc2 }));
await cp.goto(base, { waitUntil: 'networkidle' });
await cp.waitForTimeout(1300);
await cp.evaluate(() => navigator.clipboard.writeText('http://192.168.0.108:3002/gitea'));
await cp.click('.add-app');
await cp.waitForTimeout(700);
const pre = await cp.evaluate((d) => Object.fromEntries(
  [...document.querySelectorAll(d + ' input')].filter(i => i.dataset.field)
    .map(i => [i.dataset.field, i.value])), DIALOG);
console.log('clipboard prefill:', JSON.stringify(pre));
check('the clipboard URL is prefilled', pre.url === 'http://192.168.0.108:3002/gitea', JSON.stringify(pre));
check('and its Title is guessed from it', pre.title === 'Gitea', JSON.stringify(pre));
await cr.close();

console.log(fail ? `\n${fail} failed` : '\nall good');
process.exit(fail ? 1 : 0);
