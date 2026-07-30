// Are the themes in a working dropdown? Needs playwright, like tests/render.mjs:
//
//     node tests/theme-select.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit } from 'playwright';

let doc = { v1: { apps: [], groups: [], appsTitle: 'Applications', bookmarksTitle: 'Bookmarks' } };
const b = await webkit.launch();
const p = await b.newPage({ viewport: { width: 1000, height: 1000 } });
const errs = [], warns = [];
p.on('pageerror', e => errs.push(e.message));
p.on('console', m => /warn|error/i.test(m.type()) && warns.push(m.type() + ': ' + m.text().slice(0, 120)));
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

const openSettings = async () => {
  await p.hover('.hdr-row'); await p.waitForTimeout(200);
  await p.click('.hdr-actions button[title="Settings"]'); await p.waitForTimeout(600);
};
await openSettings();

const sel = 'select[data-k="theme"]';
const info = () => p.evaluate(() => {
  const s = document.querySelector('select[data-k="theme"]');
  return s ? {
    options: s.options.length,
    first: s.options[0]?.textContent,
    names: [...s.options].slice(1, 4).map(o => o.textContent),
    value: s.value,
    selectedText: s.options[s.selectedIndex]?.textContent,
    gridButtonsGone: !document.querySelector('button[data-i]'),
  } : null;
});

let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const a = await info();
console.log('select:', JSON.stringify(a));
check('the select rendered', !!a, a);
check('sc-for produced the options', a && a.options === 19, a && a.options);
check('Custom is the first entry', a && a.first === 'Custom', a && a.first);
check('theme names are the options', a && a.names.length === 3 && a.names.every(Boolean), a && a.names);
check('the old grid buttons are gone', a && a.gridButtonsGone, a && a.gridButtonsGone);
check('starts on Custom (default colours match no preset)', a && a.value === '', a && a.value);

// Pick a theme by its label and confirm it applies and persists.
const target = (await p.evaluate(() => [...document.querySelector('select[data-k="theme"]').options]
  .map(o => o.textContent).slice(1)))[1];
await p.selectOption(sel, { label: target });
await p.waitForTimeout(600);
const applied = await p.evaluate(() => ({
  bodyBg: getComputedStyle(document.body).backgroundColor,
  accent: getComputedStyle(document.body).getPropertyValue('--accent').trim(),
  value: document.querySelector('select[data-k="theme"]').value,
  label: document.querySelector('select[data-k="theme"]').options[document.querySelector('select[data-k="theme"]').selectedIndex].textContent,
}));
console.log('after choosing', JSON.stringify(target), '->', JSON.stringify(applied));
check('choosing applies the theme', applied.label === target && applied.value !== '', JSON.stringify(applied));
check('the theme is persisted', doc.theme && doc.theme.bg && doc.theme.accent, JSON.stringify(doc.theme));

// It must come back selected after a reload, not reset to Custom.
await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
await openSettings();
const after = await info();
check('the choice survives a reload', after.selectedText === target, JSON.stringify(after));

// Touching a colour picker should fall back to Custom.
await p.evaluate(() => {
  const input = document.querySelector('input[type="color"][data-k="accent"]');
  input.value = '#ff00aa';
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
await p.waitForTimeout(600);
const custom = await info();
check('a hand-picked colour reads as Custom', custom.value === '', JSON.stringify(custom));

console.log('page errors:', errs.length ? errs.slice(0, 2) : 'none');
console.log('console warnings:', warns.length ? warns.slice(0, 3) : 'none');
console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
