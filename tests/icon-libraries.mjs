// Does the Icons section of the settings panel really drive the library API?
// Needs playwright, like tests/render.mjs:
//
//     node tests/icon-libraries.mjs http://localhost:8080
//
// Non-destructive: every /api/icons call is stubbed per-page, so the running
// service is only asked for the page itself. The api side of the same feature is
// covered offline by tests/icons_test.py; this covers the wiring between them —
// that the rows come from the service, that each control reaches the right
// endpoint with the right library, and that the panel believes what comes back.
import { webkit } from 'playwright';

const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '');

// The state the stub pretends to hold, shaped like IconStore.status().
const LIBS = [
  { key: 'selfhst', label: 'selfhst', repo: 'selfhst/icons', home: '', baked: false,
    note: '7000+ service logos. CC-BY-4.0.', count: 0, enabled: true },
  { key: 'homarr', label: 'Homarr dashboard-icons', repo: 'homarr-labs/dashboard-icons',
    home: '', baked: true, note: 'Ships with the app. Apache-2.0.', count: 3233, enabled: true },
  { key: 'mdi', label: 'Material Design Icons', repo: 'Templarian/MaterialDesign',
    home: '', baked: false, note: 'Generic UI glyphs, not service logos.', count: 0, enabled: true },
];
let libs = LIBS.map(l => ({ ...l }));
let icons = ['plex', 'proxmox', 'gitea'];
const posts = [];

const status = () => ({
  running: false, phase: 'idle', installed: icons.length, writable: true,
  last: {}, libraries: libs,
});

const b = await webkit.launch();
const p = await b.newPage({ viewport: { width: 1100, height: 1100 } });
const errs = [];
p.on('pageerror', e => errs.push(e.message));

await p.route('**/api/icons**', async (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const json = (body) => route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(body) });
  if (req.method() === 'POST') {
    const body = JSON.parse(req.postData() || '{}');
    posts.push({ path: url.pathname, body });
    if (url.pathname.endsWith('/config')) {
      if (Array.isArray(body.preference)) {
        libs = body.preference.map(k => libs.find(l => l.key === k)).filter(Boolean);
      }
      if (body.enabled) {
        for (const [k, v] of Object.entries(body.enabled)) {
          const lib = libs.find(l => l.key === k);
          if (lib) lib.enabled = !!v;
        }
        // The picker only offers the enabled sets, so an install whose only
        // populated library is off really does report nothing.
        icons = libs.some(l => l.enabled && l.count) ? ['plex', 'proxmox', 'gitea'] : [];
      }
      return json({ preference: libs.map(l => l.key),
        enabled: Object.fromEntries(libs.map(l => [l.key, l.enabled])) });
    }
    return json(status());
  }
  if (url.pathname.endsWith('/status')) return json(status());
  return json({ icons, count: icons.length, status: status() });
});

// The dashboard's own config is left alone; it only has to render enough of the
// page for the settings panel to open.
await p.route('**/api/config', (route) => route.fulfill({
  status: 200, contentType: 'application/json',
  body: JSON.stringify({ rev: 1, updated: null, data: { v1: { apps: [], groups: [],
    appsTitle: 'Applications', bookmarksTitle: 'Bookmarks' } } }),
}));

await p.goto(base + '/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
await p.hover('.hdr-row');
await p.waitForTimeout(200);
await p.click('.hdr-actions button[title="Settings"]');
await p.waitForTimeout(700);

let fail = 0;
const check = (n, cond, extra) => {
  cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra));
};

const rows = () => p.evaluate(() => {
  const out = [];
  for (const el of document.querySelectorAll('[data-key]')) {
    const key = el.dataset.key;
    let row = out.find(r => r.key === key);
    if (!row) out.push(row = { key, tags: [], text: '' });
    row.tags.push(el.tagName);
    if (el.tagName === 'BUTTON') row.button = el.textContent.trim();
  }
  return out;
});
const summary = () => p.evaluate(() => {
  const el = [...document.querySelectorAll('div, span')]
    .find(e => e.children.length === 0 && /icons installed/.test(e.textContent));
  return el ? el.textContent.trim() : null;
});

const first = await rows();
console.log('rows:', JSON.stringify(first));
check('one row per library, in the order the service sent',
      first.map(r => r.key).join() === 'selfhst,homarr,mdi', first.map(r => r.key));
check('each row carries a promote, an update and a toggle',
      first.every(r => r.tags.length === 3 && r.tags.filter(t => t === 'BUTTON').length === 1),
      JSON.stringify(first.map(r => r.tags)));
check('each row offers an update', first.every(r => r.button === 'Update'),
      JSON.stringify(first.map(r => r.button)));
check('the summary counts what the service reported',
      /^3 icons installed/.test(await summary()), await summary());

// Promoting sends the whole order, so the service never has to reconstruct the
// move, and the rows come back in the new order.
await p.click('span[data-key="homarr"]');
await p.waitForTimeout(900);
const promoted = posts.filter(x => x.path.endsWith('/config')).pop();
check('promoting posts the full preference order',
      JSON.stringify(promoted && promoted.body.preference) ===
        JSON.stringify(['homarr', 'selfhst', 'mdi']), JSON.stringify(promoted));
check('the rows reorder to match',
      (await rows()).map(r => r.key).join() === 'homarr,selfhst,mdi',
      (await rows()).map(r => r.key));

// The first row's promote is a no-op rather than a request.
const before = posts.length;
await p.click('span[data-key="homarr"]');
await p.waitForTimeout(600);
check('promoting the top row sends nothing', posts.length === before, posts.length - before);

// The toggle is the last control on the row.
await p.evaluate(() => {
  const spans = document.querySelectorAll('span[data-key="homarr"]');
  spans[spans.length - 1].click();
});
await p.waitForTimeout(900);
const toggled = posts.filter(x => x.path.endsWith('/config')).pop();
check('the toggle posts just that library\'s flag',
      JSON.stringify(toggled && toggled.body) === '{"enabled":{"homarr":false}}',
      JSON.stringify(toggled));
// Turning off the only populated set really does leave nothing to offer, and the
// panel has to believe it rather than treating an empty list as a failure.
check('an empty icon list is believed, not ignored',
      /^0 icons installed/.test(await summary()), await summary());

// And an update names its own library rather than letting the service choose.
await p.click('button[data-key="mdi"]');
await p.waitForTimeout(900);
const update = posts.filter(x => x.path.endsWith('/update')).pop();
check('update posts the row\'s library',
      JSON.stringify(update && update.body) === '{"library":"mdi"}', JSON.stringify(update));

console.log('page errors:', errs.length ? errs.slice(0, 2) : 'none');
console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
