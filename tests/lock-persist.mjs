// Does the lock survive a reload, and does a clean browser profile pick it up?
// Needs playwright, like tests/render.mjs:
//
//     node tests/lock-persist.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page and never written to the service.
import { webkit } from 'playwright';

// Stateful stub so a reload sees what was actually saved.
let doc = {
  v1: {
    apps: [{ id: 'plex', icon: 'plex', title: 'Plex', desc: 'plex.lan', url: 'http://plex.lan' }],
    groups: [{ id: 'g1', name: 'Dev', bookmarks: [{ id: 'gitea', icon: 'gitea', title: 'Gitea', url: 'http://gitea.lan' }] }],
    appsTitle: 'Applications', bookmarksTitle: 'Bookmarks',
  },
};

const b = await webkit.launch();
const p = await b.newPage({ viewport: { width: 1300, height: 900 } });
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

const read = () => p.evaluate(() => ({
  flag: document.querySelector('[data-locked]')?.dataset.locked,
  lockTitle: document.querySelector('.hdr-actions button:nth-of-type(2)')?.title,
  addApp: getComputedStyle(document.querySelector('.add-app')).pointerEvents,
}));
const clickLock = async () => {
  await p.hover('.hdr-row'); await p.waitForTimeout(200);
  await p.locator('.hdr-actions button').nth(1).click(); await p.waitForTimeout(450);
};

let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const before = await read();
check('starts unlocked', before.flag === '0' && /Lock layout/i.test(before.lockTitle), JSON.stringify(before));

await clickLock();
const locked = await read();
check('locks', locked.flag === '1' && /locked/i.test(locked.lockTitle), JSON.stringify(locked));
check('lock is written to the config', doc.theme && doc.theme.locked === true, JSON.stringify(doc.theme));

await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
const afterReload = await read();
check('still locked after a reload', afterReload.flag === '1' && /locked/i.test(afterReload.lockTitle),
  JSON.stringify(afterReload));
check('and the controls are still inert', afterReload.addApp === 'none', afterReload.addApp);

await clickLock();
const unlocked = await read();
check('unlocks again', unlocked.flag === '0', JSON.stringify(unlocked));
check('unlock is written too', doc.theme.locked === false, JSON.stringify(doc.theme));

await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
const finalState = await read();
check('stays unlocked after a reload', finalState.flag === '0' && finalState.addApp === 'auto',
  JSON.stringify(finalState));

// A fresh browser profile must pick up the stored lock, not just this tab.
await p.evaluate(() => localStorage.clear());
doc.theme = { ...doc.theme, locked: true };
await sync();
await p.reload({ waitUntil: 'networkidle' });
await p.waitForTimeout(1300);
const fresh = await read();
check('a clean profile honours the stored lock', fresh.flag === '1', JSON.stringify(fresh));

console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
