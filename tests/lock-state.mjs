// Does the lock actually hide the hover-revealed edit controls, and does the
// setting bring them back? Needs playwright, like tests/render.mjs:
//
//     node tests/lock-state.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit } from 'playwright';

// Stateful config stub. Seeding localStorage does not work once the service is
// reachable — the shim treats the server as authoritative — and the shim's first
// read is a synchronous XHR that WebKit will not route-intercept, so that one call
// is stubbed in-page too.
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
await p.addInitScript(d => { window.__stub = d; }, JSON.stringify({ rev: 1, data: doc }));
await p.goto((process.argv[2] || 'http://localhost:8080').replace(/\/$/, '') + '/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1400);

const state = async (label) => {
  await p.hover('.app-grid');
  await p.waitForTimeout(350);
  const r = await p.evaluate(() => {
    const g = (sel) => {
      const e = document.querySelector(sel);
      if (!e) return 'absent';
      const c = getComputedStyle(e);
      return `${Math.round(parseFloat(c.opacity))}/${c.pointerEvents}`;
    };
    return {
      flag: document.querySelector('[data-locked]')?.dataset.locked,
      apps: document.querySelectorAll('.app-grid a[data-drag-id]').length,
      addApp: g('.add-app'),
      cardCtl: g('.app-grid [data-nodrag="1"]'),
      addBm: g('.bm-cat > button'),
      catDel: g('.bm-cat .cat-del'),
      settings: g('.hdr-actions button[title="Settings"]'),
      lockBtn: g('.hdr-actions button:nth-of-type(2)'),
    };
  });
  console.log(label.padEnd(26), JSON.stringify(r));
  return r;
};

const clickLock = async () => {
  await p.hover('.hdr-row'); await p.waitForTimeout(200);
  await p.locator('.hdr-actions button').nth(1).click();
  await p.waitForTimeout(450);
};
const setToggle = async () => {
  await p.hover('.hdr-row'); await p.waitForTimeout(200);
  await p.click('.hdr-actions button[title="Settings"]'); await p.waitForTimeout(600);
  await p.locator('span:has-text("Show hover controls when locked")').first()
    .locator('xpath=following-sibling::span[1]').click();
  await p.waitForTimeout(400);
  await p.keyboard.press('Escape'); await p.waitForTimeout(400);
};

const a = await state('unlocked');
await clickLock();
const bb = await state('locked, setting off');
await setToggle();
const c = await state('locked, setting on');
await clickLock();
const d = await state('unlocked, setting on');
console.log('theme persisted:', JSON.stringify(doc.theme));

let fail = 0;
const check = (n, cond, extra) => { if (!cond) { fail++; console.log('FAIL ' + n + '  <- ' + extra); } else console.log('ok   ' + n); };
console.log('');
check('cards actually rendered', a.apps === 1, a.apps);
check('unlocked: controls reachable', a.addApp === '1/auto' && a.cardCtl.endsWith('/auto'), `${a.addApp} ${a.cardCtl}`);
check('locked: add button hidden and inert', bb.addApp === '0/none', bb.addApp);
check('locked: card controls hidden and inert', bb.cardCtl === '0/none', bb.cardCtl);
check('locked: add bookmark hidden and inert', bb.addBm === '0/none', bb.addBm);
check('locked: settings still usable', bb.settings.endsWith('/auto'), bb.settings);
check('locked: lock button still usable', bb.lockBtn.endsWith('/auto'), bb.lockBtn);
// The card overlay is revealed by hovering its own card, not the grid, so being
// reachable (pointer-events) is the invariant here rather than opacity.
check('setting on: controls reachable while locked',
  c.addApp === '1/auto' && c.cardCtl.endsWith('/auto') && c.addBm.endsWith('/auto'),
  `${c.addApp} ${c.cardCtl} ${c.addBm}`);
check('unlocking again is unaffected', d.addApp === '1/auto', d.addApp);
check('the choice is persisted', doc.theme && doc.theme.lockedHover === true, doc.theme);
console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
