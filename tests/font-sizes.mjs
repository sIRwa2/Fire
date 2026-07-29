// Does each font-size control move only its own target? Needs playwright, like
// tests/render.mjs:
//
//     node tests/font-sizes.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit } from 'playwright';

let doc = {
  v1: {
    apps: [{ id: 'plex', icon: 'plex', title: 'Plex', desc: 'plex.lan', url: 'http://plex.lan' }],
    groups: [{ id: 'g1', name: 'Dev', bookmarks: [{ id: 'gitea', icon: 'gitea', title: 'Gitea', url: 'http://gitea.lan' }] }],
    appsTitle: 'Applications', bookmarksTitle: 'Bookmarks',
  },
};
const b = await webkit.launch();
const p = await b.newPage({ viewport: { width: 1200, height: 1100 } });
const errs = [];
p.on('pageerror', e => errs.push(e.message));
await p.route('**/api/config', (route) => {
  const req = route.request();
  if (req.method() !== 'GET') {
    const body = JSON.parse(req.postData() || '{}');
    for (const [k, v] of Object.entries(body.data || {})) v === null ? delete doc[k] : (doc[k] = v);
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"rev":1}' });
  }
  return route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ rev: 1, updated: null, data: doc }) });
});
await p.addInitScript(() => {
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
const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '');
const sync = () => p.addInitScript(d => { window.__stub = d; }, JSON.stringify({ rev: 1, data: doc }));
await sync();
await p.goto(base + '/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1300);

const sizes = () => p.evaluate(() => {
  const fs = (sel) => {
    const e = document.querySelector(sel);
    return e ? Math.round(parseFloat(getComputedStyle(e).fontSize) * 10) / 10 : null;
  };
  return {
    appsHeading: fs('[data-k="appsTitle"]'),
    bmHeading: fs('[data-k="bookmarksTitle"]'),
    appTitle: fs('.app-grid a[data-drag-id] > span:not([data-nodrag]) > span:first-child'),
    appDesc: fs('.app-grid a[data-drag-id] > span:not([data-nodrag]) > span:last-child'),
    catName: fs('.bm-cat > div:first-child > span:first-child'),
    bmTitle: fs('.bm-cat a[data-drag-id]'),
    editIcon: fs('.app-grid a[data-drag-id] > span[data-nodrag] > span:first-child'),
    greeting: fs('header h1') ?? fs('header div + div'),
  };
});

const openSettings = async () => {
  await p.hover('.hdr-row'); await p.waitForTimeout(200);
  await p.click('.hdr-actions button[title="Settings"]'); await p.waitForTimeout(600);
};
const setSize = async (key, value) => {
  await openSettings();
  const input = p.locator(`input[data-k="${key}"]`);
  await input.fill(String(value));
  await input.dispatchEvent('change');
  await p.waitForTimeout(400);
  await p.keyboard.press('Escape');
  await p.waitForTimeout(350);
};

let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const base0 = await sizes();
console.log('defaults:', JSON.stringify(base0));
check('stock sizes are unchanged', base0.appsHeading === 19 && base0.appTitle === 14.5
  && base0.appDesc === 11.2 && base0.catName === 15 && base0.bmTitle === 14.5, JSON.stringify(base0));
check('five inputs are present', (await p.evaluate(async () => {
  document.querySelector('.hdr-actions button[title="Settings"]').click();
  return new Promise(r => setTimeout(() => r(document.querySelectorAll('input[data-k^="fs"]').length), 500));
})) === 5);
await p.keyboard.press('Escape'); await p.waitForTimeout(300);

// Each control must move only its own target.
const cases = [
  ['fsSection', 26, ['appsHeading', 'bmHeading']],
  ['fsAppTitle', 20, ['appTitle']],
  ['fsAppDesc', 9, ['appDesc']],
  ['fsCatName', 22, ['catName']],
  ['fsBmTitle', 18, ['bmTitle']],
];
for (const [key, value, targets] of cases) {
  const before = await sizes();
  await setSize(key, value);
  const after = await sizes();
  check(`${key} resizes ${targets.join(' + ')}`, targets.every(t => after[t] === value),
    targets.map(t => `${t}=${after[t]}`).join(' '));
  const others = Object.keys(after).filter(k => !targets.includes(k) && k !== 'greeting');
  const moved = others.filter(k => after[k] !== before[k]);
  check(`${key} touches nothing else`, moved.length === 0, moved.map(k => `${k} ${before[k]}->${after[k]}`).join(', '));
}

const final = await sizes();
check('the edit overlay icon was never resized', final.editIcon === base0.editIcon,
  `${base0.editIcon} -> ${final.editIcon}`);
check('all five are persisted', ['fsSection', 'fsAppTitle', 'fsAppDesc', 'fsCatName', 'fsBmTitle']
  .every(k => typeof doc.theme?.[k] === 'number'), JSON.stringify(doc.theme));

await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
const reloaded = await sizes();
check('sizes survive a reload', reloaded.appsHeading === 26 && reloaded.appTitle === 20
  && reloaded.appDesc === 9 && reloaded.catName === 22 && reloaded.bmTitle === 18,
  JSON.stringify(reloaded));

// Out-of-range input must be ignored, not applied.
await setSize('fsSection', 200);
check('an out-of-range size is rejected', (await sizes()).appsHeading === 26, (await sizes()).appsHeading);

console.log('page errors:', errs.length ? errs.slice(0, 2) : 'none');
console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
