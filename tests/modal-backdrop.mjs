// Drag-select inside the add-entry modal must not close it when the mouse is
// released over the backdrop. Needs playwright, like tests/render.mjs:
//
//     node tests/modal-backdrop.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit } from 'playwright';

const doc = { v1: {
  apps: [{ id: 'plex', icon: 'plex', title: 'plex', desc: 'plex.lan', url: 'http://plex.lan' }],
  groups: [], appsTitle: 'Applications', bookmarksTitle: 'Bookmarks',
} };

const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '') + '/';
let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const browser = await webkit.launch();
const page = await browser.newPage();
page.on('pageerror', e => { fail++; console.log('PAGEERROR ' + e.message); });
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
await page.goto(base, { waitUntil: 'networkidle' });
await page.waitForTimeout(1300);

const modal = page.locator('[data-screen-label="Add entry modal"]');
const open = () => modal.count().then(n => n > 0);

await page.locator('.add-app').click();
await page.waitForTimeout(300);
check('modal opens', await open(), 'add-app click did not open it');

const box = await modal.boundingBox();
if (!box) { console.log('FAIL no modal box'); process.exit(1); }

// Press inside the modal, release out on the backdrop — a text drag that
// overshoots. The click lands on the backdrop, but the modal must survive.
await page.mouse.move(box.x + 40, box.y + box.height / 2);
await page.mouse.down();
await page.mouse.move(box.x - 120, box.y + box.height / 2, { steps: 8 });
await page.mouse.up();
await page.waitForTimeout(250);
check('drag from inside to backdrop keeps the modal open', await open(), 'modal closed');

// The reverse of the same gesture: press on the backdrop, release inside.
await page.mouse.move(box.x - 120, box.y + box.height / 2);
await page.mouse.down();
await page.mouse.move(box.x + 40, box.y + box.height / 2, { steps: 8 });
await page.mouse.up();
await page.waitForTimeout(250);
check('drag from backdrop into the modal keeps it open', await open(), 'modal closed');

// A plain click on the backdrop still closes it.
await page.mouse.click(box.x - 120, box.y + box.height / 2);
await page.waitForTimeout(250);
check('backdrop click closes the modal', !(await open()), 'modal stayed open');

await browser.close();
console.log(fail ? '\n' + fail + ' failed' : '\nall good');
process.exit(fail ? 1 : 0);
