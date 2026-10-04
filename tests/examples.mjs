// Does the "Add examples" button in Settings put the demo dashboard back, and
// only ever add? Needs playwright, like tests/render.mjs:
//
//     node tests/examples.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, so the running service is
// only asked for the page itself. The stub is stateful, so "did it save" is
// answered by what the page wrote rather than by what it shows.
import { webkit } from 'playwright';

const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '');
const shot = process.argv[3];   // optional: a path to screenshot the panel to

let doc = { v1: { apps: [], groups: [], appsTitle: 'Applications', bookmarksTitle: 'Bookmarks' } };
const puts = [];
const dialogs = [];
let answer = true;   // what the next confirm() gets

const b = await webkit.launch();
const p = await b.newPage({ viewport: { width: 1100, height: 1100 } });
const errs = [];
p.on('pageerror', e => errs.push(e.message));
p.on('dialog', d => { dialogs.push({ type: d.type(), text: d.message() });
  d.type() === 'confirm' ? (answer ? d.accept() : d.dismiss()) : d.accept(); });

await p.route('**/api/config', (route) => {
  const req = route.request();
  if (req.method() !== 'GET') {
    const body = JSON.parse(req.postData() || '{}');
    for (const [k, v] of Object.entries(body.data || {})) v === null ? delete doc[k] : (doc[k] = v);
    puts.push(JSON.parse(JSON.stringify(doc.v1)));
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"rev":1}' });
  }
  return route.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ rev: 1, updated: null, data: doc }) });
});
// The shim's first read is a synchronous XHR, which WebKit does not put through
// route interception, so that one call is answered in-page from __stub; see
// render.mjs. Writes go through fetch and reach the route above.
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
const sync = () => p.addInitScript(d => { window.__stub = d; }, JSON.stringify({ rev: 1, data: doc }));

let fail = 0;
const check = (n, cond, extra) => {
  cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra));
};

const load = async () => {
  await sync();
  await p.goto(base + '/', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1300);
};
const openSettings = async () => {
  await p.hover('.hdr-row');
  await p.waitForTimeout(200);
  await p.click('.hdr-actions button[title="Settings"]');
  await p.waitForTimeout(700);
};
const onPage = () => p.evaluate(() => ({
  apps: document.querySelectorAll('.app-grid a[data-drag-id]').length,
  cats: document.querySelectorAll('.bm-cat').length,
  panel: !!document.querySelector('button[title^="12 apps"]'),
}));
const examples = () => p.locator('button', { hasText: 'Add examples' });
const lastPut = () => puts[puts.length - 1];
const ids = (v1) => [...v1.apps.map(a => a.id), ...v1.groups.map(g => g.id),
  ...v1.groups.flatMap(g => g.bookmarks.map(b => b.id))];

// ── an empty dashboard: no question asked ──────────────────────────────────
await load();
check('starts empty', JSON.stringify(await onPage()) === '{"apps":0,"cats":0,"panel":false}',
      JSON.stringify(await onPage()));
await openSettings();
const row = await p.evaluate(() => {
  const add = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Add examples');
  const reset = [...document.querySelectorAll('button')].find(b => /^Reset apps/.test(b.textContent));
  const done = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Done');
  const r = (el) => el && el.getBoundingClientRect();
  return { add: r(add), reset: r(reset), done: r(done) };
});
check('the button sits next to Reset', row.add && row.reset
      && Math.abs(row.add.top - row.reset.top) < 2 && row.add.right < row.reset.left,
      JSON.stringify(row));
check('and Done stays to the right', row.done && row.done.left > row.reset.right, JSON.stringify(row));
if (shot) await p.screenshot({ path: shot });

await examples().click();
await p.waitForTimeout(900);
check('an empty dashboard asks nothing', dialogs.length === 0, JSON.stringify(dialogs));
check('the panel closes', !(await onPage()).panel, JSON.stringify(await onPage()));
check('12 apps and 4 groups are saved',
      lastPut() && lastPut().apps.length === 12 && lastPut().groups.length === 4,
      JSON.stringify(lastPut() && [lastPut().apps.length, lastPut().groups.length]));
check('and rendered', (await onPage()).apps === 12 && (await onPage()).cats === 4,
      JSON.stringify(await onPage()));
check('the titles are left alone',
      lastPut().appsTitle === 'Applications' && lastPut().bookmarksTitle === 'Bookmarks',
      JSON.stringify([lastPut().appsTitle, lastPut().bookmarksTitle]));
const all = ids(lastPut());
check('every id is unique', new Set(all).size === all.length, all.length - new Set(all).size + ' dupes');
check('the ids are minted, not the icon names', !all.includes('plex') && !all.includes('g1'),
      all.slice(0, 3));
check('the demo apps point at example.com',
      lastPut().apps.every(a => /\.example\.com$/.test(new URL(a.url).host)),
      JSON.stringify(lastPut().apps.map(a => a.url).slice(0, 3)));

// ── everything present: it says so and writes nothing ──────────────────────
const before = puts.length;
await openSettings();
await examples().click();
await p.waitForTimeout(700);
check('a full dashboard gets an alert, not a confirm',
      dialogs.length === 1 && dialogs[0].type === 'alert' && /already/.test(dialogs[0].text),
      JSON.stringify(dialogs));
check('and nothing is written', puts.length === before, puts.length - before);
check('the panel stays open', (await onPage()).panel, JSON.stringify(await onPage()));

// ── a partial dashboard: asks, then tops up without touching the rest ──────
dialogs.length = 0;
doc.v1.apps = doc.v1.apps.slice(0, 5).concat([{ id: 'mine', icon: 'gitea', title: 'Gitea', url: 'http://gitea.lan' }]);
doc.v1.groups = doc.v1.groups.slice(0, 1).concat([{ id: 'g-mine', name: 'Home', bookmarks: [] }]);
await load();
check('reloaded with 6 apps and 2 groups', (await onPage()).apps === 6 && (await onPage()).cats === 2,
      JSON.stringify(await onPage()));
await openSettings();
answer = false;
await examples().click();
await p.waitForTimeout(700);
check('a populated dashboard asks first, with the counts it would add',
      dialogs.length === 1 && dialogs[0].type === 'confirm' && /7 example apps and 3 bookmark groups/.test(dialogs[0].text),
      JSON.stringify(dialogs));
check('declining writes nothing', lastPut().apps.length !== 13 && (await onPage()).apps === 6,
      JSON.stringify(await onPage()));
answer = true;
await examples().click();
await p.waitForTimeout(900);
const topped = lastPut();
check('accepting adds only what was missing', topped.apps.length === 13 && topped.groups.length === 5,
      JSON.stringify([topped.apps.length, topped.groups.length]));
check('the user\'s own entries survive, in place',
      topped.apps[5].id === 'mine' && topped.groups[1].id === 'g-mine',
      JSON.stringify([topped.apps[5].id, topped.groups[1].id]));
check('the kept examples are not duplicated',
      new Set(topped.apps.map(a => a.url)).size === 13
      && new Set(topped.groups.map(g => g.name)).size === 5, 'duplicate url or group name');

// ── Reset still empties ────────────────────────────────────────────────────
dialogs.length = 0;
await openSettings();
await p.locator('button', { hasText: 'Reset apps' }).click();
await p.waitForTimeout(900);
check('Reset still asks and still empties',
      dialogs.length === 1 && dialogs[0].type === 'confirm'
      && lastPut().apps.length === 0 && lastPut().groups.length === 0,
      JSON.stringify([dialogs, lastPut().apps.length]));

console.log('page errors:', errs.length ? errs.slice(0, 2) : 'none');
console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
