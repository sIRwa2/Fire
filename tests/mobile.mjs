// Does the page adapt below the breakpoint, and do the three mobile layouts do
// what they say? Needs playwright, like tests/render.mjs:
//
//     node tests/mobile.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit, devices } from 'playwright';

const APPS = ['plex', 'proxmox', 'gitea', 'grafana', 'nginx', 'portainer'];
const build = (mobileLayout) => ({
  v1: {
    apps: APPS.map(n => ({ id: n, icon: n, title: n, desc: n + '.lan', url: 'http://' + n + '.lan' })),
    groups: [
      { id: 'g1', name: 'Dev', bookmarks: ['gitea', 'grafana'].map(n => ({ id: n, icon: n, title: n, url: 'http://' + n + '.lan' })) },
      { id: 'g2', name: 'Media', bookmarks: [{ id: 'plex', icon: 'plex', title: 'plex', url: 'http://plex.lan' }] },
    ],
    appsTitle: 'Applications', bookmarksTitle: 'Bookmarks',
  },
  ...(mobileLayout ? { theme: { mobileLayout } } : {}),
});

const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '');
let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const browser = await webkit.launch();
const open = async (doc, opts) => {
  const page = await browser.newPage(opts);
  await page.route('**/api/config', r => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ rev: 1, data: doc }) }));
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
  await page.goto(base + '/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1300);
  return page;
};
const probe = (page) => page.evaluate(() => {
  const shown = (sel) => { const e = document.querySelector(sel); return !!e && getComputedStyle(e).display !== 'none'; };
  const cols = (sel) => getComputedStyle(document.querySelector(sel)).gridTemplateColumns.split(' ').length;
  return {
    mode: document.querySelector('[data-mobile]')?.dataset.mobile,
    appCols: cols('.app-grid'),
    bmCols: cols('.bm-grid'),
    text: shown('.app-grid a[data-drag-id] > span:not([data-nodrag])'),
    icon: shown('.app-grid a[data-drag-id] > img'),
    overlay: shown('.app-grid a[data-drag-id] > span[data-nodrag]'),
    bmIcon: shown('.bm-cat a[data-drag-id] img'),
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  };
});

for (const [mode, expect] of [
  ['both', { appCols: 1, text: true, icon: true }],
  ['icons', { appCols: 4, text: false, icon: true }],
  ['text', { appCols: 1, text: true, icon: false }],
]) {
  const page = await open(build(mode), devices['iPhone 13']);
  const r = await probe(page);
  check(`${mode}: applied`, r.mode === mode, r.mode);
  check(`${mode}: app columns`, r.appCols === expect.appCols, r.appCols);
  check(`${mode}: text ${expect.text ? 'shown' : 'hidden'}`, r.text === expect.text, r.text);
  check(`${mode}: icon ${expect.icon ? 'shown' : 'hidden'}`, r.icon === expect.icon, r.icon);
  check(`${mode}: two categories per row`, r.bmCols === 2, r.bmCols);
  check(`${mode}: no horizontal overflow`, !r.overflow, r.overflow);
  if (mode === 'icons') {
    // The overlay is forced visible on touch and would cover a 58px icon.
    check('icons: the edit overlay does not cover the icon', !r.overlay, r.overlay);
  }
  if (mode === 'text') {
    check('text: bookmark icons hidden too', !r.bmIcon, r.bmIcon);
  }
  await page.close();
}

// Desktop must be untouched, even with a mobile mode stored.
const desk = await open(build('icons'), { viewport: { width: 1400, height: 900 } });
const d = await probe(desk);
check('desktop keeps its 4 app columns', d.appCols === 4, d.appCols);
check('desktop keeps its 5 category columns', d.bmCols === 5, d.bmCols);
check('desktop still shows the text', d.text, d.text);
await desk.close();

// The stock layout is icon and text.
const dflt = await open(build(null), devices['iPhone 13']);
check('defaults to icon and text', (await probe(dflt)).mode === 'both');
await dflt.close();

// A long category name must not push the grip and DELETE out of its column. Two
// columns on a phone is ~163px, so the header row has to wrap rather than
// overflow — it used to put DELETE 46px off-screen. Checked on desktop too,
// where five columns are narrower still and only the page margin hid it.
const long = build('both');
long.v1.groups[0].name = 'Infrastructure & Monitoring';
for (const [label, opts] of [
  ['phone', devices['iPhone 13']],
  ['desktop', { viewport: { width: 1400, height: 900 } }],
]) {
  const page = await open(long, opts);
  const r = await page.evaluate(() => ({
    escaping: [...document.querySelectorAll('.bm-cat')].flatMap((c) => {
      const right = c.getBoundingClientRect().right;
      return [...c.querySelector('div').children]
        .filter(s => s.getBoundingClientRect().right > right + 0.5)
        .map(s => (s.className || 'name').toString().split(' ')[0]);
    }),
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }));
  check(`${label}: a long category name keeps its controls in the column`,
    r.escaping.length === 0, r.escaping.join(','));
  check(`${label}: a long category name causes no horizontal overflow`, !r.overflow, r.overflow);
  await page.close();
}

console.log(fail ? `\n${fail} failed` : '\nall good');
await browser.close();
process.exit(fail ? 1 : 0);
