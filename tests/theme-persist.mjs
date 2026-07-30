// Do the theme and the colour pickers actually persist? Needs playwright, like
// tests/render.mjs:
//
//     node tests/theme-persist.mjs http://localhost:8080
//
// Colours and presets go through setTheme, which writes fireHome.theme and so
// travels to the config service. The lock did not — it lived only in component
// state — which is why it was the one thing that reset on reload. This exists so
// that distinction stays covered rather than remembered.
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit } from 'playwright';

// Stateful stub standing in for the config service, so "did it actually save"
// is answerable rather than assumed.
let doc = { v1: { apps: [{ id: 'plex', icon: 'plex', title: 'Plex', desc: 'plex.lan', url: 'http://plex.lan' }],
  groups: [{ id: 'g1', name: 'Dev', bookmarks: [{ id: 'gitea', icon: 'gitea', title: 'Gitea', url: 'http://gitea.lan' }] }],
  appsTitle: 'Applications', bookmarksTitle: 'Bookmarks' } };
const puts = [];

const b = await webkit.launch();
const p = await b.newPage({ viewport: { width: 1100, height: 1000 } });
p.on('pageerror', e => console.log('PAGEERROR', e.message));
await p.route('**/api/config', (route) => {
  const req = route.request();
  if (req.method() !== 'GET') {
    const body = JSON.parse(req.postData() || '{}');
    puts.push(JSON.stringify(body.data?.theme ?? null));
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

const look = () => p.evaluate(() => ({
  accent: getComputedStyle(document.body).getPropertyValue('--accent').trim(),
  bg: getComputedStyle(document.body).backgroundColor,
  banner: !!document.getElementById('__firehome_storage_msg'),
}));
const openSettings = async () => {
  await p.hover('.hdr-row'); await p.waitForTimeout(200);
  await p.click('.hdr-actions button[title="Settings"]'); await p.waitForTimeout(600);
};

let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

console.log('initial:', JSON.stringify(await look()));
check('no degraded banner', !(await look()).banner);

// 1. The accent colour picker.
await openSettings();
await p.evaluate(() => {
  const i = document.querySelector('input[type="color"][data-k="accent"]');
  i.value = '#ff2288';
  i.dispatchEvent(new Event('input', { bubbles: true }));
});
await p.waitForTimeout(700);
await p.keyboard.press('Escape'); await p.waitForTimeout(300);
const afterAccent = await look();
check('accent applies immediately', afterAccent.accent === '#ff2288', afterAccent.accent);
check('accent reaches the config service', doc.theme && doc.theme.accent === '#ff2288', JSON.stringify(doc.theme));

// 2. The background colour picker.
await openSettings();
await p.evaluate(() => {
  const i = document.querySelector('input[type="color"][data-k="bg"]');
  i.value = '#101820';
  i.dispatchEvent(new Event('input', { bubbles: true }));
});
await p.waitForTimeout(700);
await p.keyboard.press('Escape'); await p.waitForTimeout(300);
check('background reaches the config service', doc.theme && doc.theme.bg === '#101820', JSON.stringify(doc.theme));

// 3. Reload: both must come back.
await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
const reloaded = await look();
console.log('after reload:', JSON.stringify(reloaded));
check('accent survives a reload', reloaded.accent === '#ff2288', reloaded.accent);
check('background survives a reload', reloaded.bg === 'rgb(16, 24, 32)', reloaded.bg);

// 4. A preset theme, then a reload.
await openSettings();
const label = (await p.evaluate(() => [...document.querySelector('select[data-k="theme"]').options].map(o => o.textContent)))[3];
await p.selectOption('select[data-k="theme"]', { label });
await p.waitForTimeout(700);
await p.keyboard.press('Escape'); await p.waitForTimeout(300);
const themed = await look();
check(`choosing "${label}" applies it`, themed.accent !== '#ff2288', themed.accent);
check('the preset reaches the config service', doc.theme.accent === themed.accent, JSON.stringify(doc.theme));
await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
const afterTheme = await look();
check('the preset survives a reload', afterTheme.accent === themed.accent && afterTheme.bg === themed.bg,
  JSON.stringify(afterTheme) + ' vs ' + JSON.stringify(themed));

// 5. A clean browser profile must inherit it, since this is server-side.
await p.evaluate(() => localStorage.clear());
await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
const fresh = await look();
check('a cleared profile still gets the theme', fresh.accent === themed.accent, JSON.stringify(fresh));

console.log('\ntheme writes seen by the service:', puts.length);
console.log('final stored theme:', JSON.stringify(doc.theme));
console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
