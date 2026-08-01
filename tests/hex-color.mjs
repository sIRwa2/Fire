// The hex field beside each colour picker in Settings — the one that replaced
// the word "Custom". Needs playwright, like tests/render.mjs:
//
//     node tests/hex-color.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit } from 'playwright';

let doc = { v1: { apps: [], groups: [], appsTitle: 'Applications', bookmarksTitle: 'Bookmarks' } };
const b = await webkit.launch();
const p = await b.newPage({ viewport: { width: 1000, height: 1000 } });
const errs = [], warns = [];
p.on('pageerror', e => errs.push(e.message));
// Resource 404s are the deployment's business — /api/icons and the {{ }} probe
// both answer only through nginx — so they are filtered out here. What is left
// is what this test cares about: React complaining about the controlled input.
p.on('console', m => /warn|error/i.test(m.type())
  && !/Failed to load resource|resource failed to load/.test(m.text())
  && warns.push(m.type() + ': ' + m.text().slice(0, 160)));
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
await p.addInitScript(d => { window.__stub = d; }, JSON.stringify({ rev: 1, data: doc }));
await p.goto(base + '/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1300);

await p.hover('.hdr-row'); await p.waitForTimeout(200);
await p.click('.hdr-actions button[title="Settings"]'); await p.waitForTimeout(600);

let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const hex = (k) => 'input[type="text"][data-k="' + k + '"]';
const state = () => p.evaluate(() => ({
  accent: getComputedStyle(document.body).getPropertyValue('--accent').trim(),
  bodyBg: getComputedStyle(document.body).backgroundColor,
  accentField: document.querySelector('input[type="text"][data-k="accent"]')?.value,
  bgField: document.querySelector('input[type="text"][data-k="bg"]')?.value,
  picker: document.querySelector('input[type="color"][data-k="accent"]')?.value,
  custom: /Custom/.test(document.querySelector('input[type="color"][data-k="accent"]')?.parentElement?.textContent || ''),
}));

const a = await state();
console.log('on open:', JSON.stringify(a));
check('both hex fields rendered', a.accentField !== undefined && a.bgField !== undefined, JSON.stringify(a));
check('they show the colours in use', /^#[0-9a-f]{6}$/.test(a.accentField) && /^#[0-9a-f]{6}$/.test(a.bgField), a.accentField + ' ' + a.bgField);
check('the "Custom" label is gone', !a.custom, a.custom);

// A hex pasted in whole applies, reaches the picker, and is persisted.
await p.fill(hex('accent'), '#8ab8d9');
await p.waitForTimeout(500);
let s = await state();
check('a pasted accent applies', s.accent === '#8ab8d9', s.accent);
check('...and the native picker follows it', s.picker === '#8ab8d9', s.picker);
check('...and it is persisted', doc.theme && doc.theme.accent === '#8ab8d9', JSON.stringify(doc.theme));

// Typed one character at a time: every intermediate value is half a hex, and a
// field that rejected those would be impossible to type into.
await p.fill(hex('bg'), '');
await p.locator(hex('bg')).pressSequentially('1e2430', { delay: 40 });
await p.waitForTimeout(500);
s = await state();
check('a hex can be typed a character at a time', s.bgField === '1e2430', s.bgField);
check('...and the background applies without the #', s.bodyBg === 'rgb(30, 36, 48)', s.bodyBg);

// Three-digit shorthand expands, and case does not matter.
await p.fill(hex('accent'), '#0F8');
await p.waitForTimeout(500);
s = await state();
check('shorthand expands to six digits', s.accent === '#00ff88', s.accent);

// Nonsense is ignored while typing, and dropped on blur rather than left to sit
// in the field as if it had been accepted.
await p.fill(hex('accent'), 'nope!!');
await p.waitForTimeout(400);
s = await state();
check('junk does not change the colour', s.accent === '#00ff88', s.accent);
check('...and stays visible while the field has focus', s.accentField === 'nope!!', s.accentField);
await p.locator(hex('accent')).blur();
await p.waitForTimeout(400);
s = await state();
check('...and is discarded on blur', s.accentField === '#00ff88', s.accentField);

// A swatch click has to be reflected back into the field.
await p.evaluate(() => document.querySelector('span[data-k="accent"][data-color]').click());
await p.waitForTimeout(500);
s = await state();
const swatch = await p.evaluate(() => document.querySelector('span[data-k="accent"][data-color]').dataset.color);
check('a swatch click updates the field', s.accentField === swatch && s.accent === swatch, s.accentField + ' vs ' + swatch);

// And the typed colour survives a reload.
await p.addInitScript(d => { window.__stub = d; }, JSON.stringify({ rev: 1, data: doc }));
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
await p.hover('.hdr-row'); await p.waitForTimeout(200);
await p.click('.hdr-actions button[title="Settings"]'); await p.waitForTimeout(600);
s = await state();
check('the background survives a reload', s.bgField === '#1e2430', s.bgField);

console.log('page errors:', errs.length ? errs.slice(0, 2) : 'none');
console.log('console warnings:', warns.length ? warns.slice(0, 3) : 'none');
check('no page errors', errs.length === 0, errs.slice(0, 2));
check('no console warnings', warns.length === 0, warns.slice(0, 3));
console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
