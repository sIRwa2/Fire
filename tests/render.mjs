// Render the deployed page in real WebKit (Safari's engine) and Chromium and
// assert it actually looks right. This is the only check that can catch a CSS
// rule that parses but never applies, or a selector that stops matching once the
// dashboard has content in it.
//
// Optional — it needs a couple of hundred MB of browsers, so tests/run.sh does
// not depend on it:
//
//     npm install playwright
//     npx playwright install --with-deps webkit chromium
//     node tests/render.mjs http://localhost:8080
//
// Non-destructive: the populated case is seeded into localStorage in a throwaway
// browser profile, never into the config service.
import { webkit, chromium } from 'playwright';

const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '');

const SEED = JSON.stringify({
  apps: [
    { id: 'plex', icon: 'plex', title: 'Plex', desc: 'plex.lan', url: 'http://plex.lan' },
    { id: 'proxmox', icon: 'proxmox', title: 'Proxmox', desc: 'pve.lan', url: 'http://pve.lan' },
  ],
  groups: [{ id: 'g1', name: 'Dev', bookmarks: [{ id: 'gitea', icon: 'gitea', title: 'Gitea', url: 'http://gitea.lan' }] }],
  appsTitle: 'Applications',
  bookmarksTitle: 'Bookmarks',
});

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  cond ? pass++ : fail++;
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${cond ? '' : '  <- ' + extra}`);
};

for (const [engine, launcher] of [['webkit', webkit], ['chromium', chromium]]) {
  const browser = await launcher.launch();

  for (const populated of [false, true]) {
    const label = `${engine}/${populated ? 'populated' : 'empty'}`;
    const page = await browser.newPage({ viewport: { width: 1504, height: 900 } });
    const external = [], broken = [];
    page.on('request', (r) => {
      const host = new URL(r.url()).host;
      if (host && host !== new URL(base).host) external.push(r.url());
    });
    page.on('response', (r) => {
      // The dc-runtime's un-hydrated {{ }} placeholders are absorbed by nginx.
      if (r.status() >= 400) broken.push(`${r.status()} ${decodeURIComponent(r.url())}`);
    });
    // Serve the config from the test rather than the real store: deterministic
    // regardless of what the dashboard currently holds, and it never reads or
    // writes the deployed config. Seeding localStorage would not work — with the
    // service reachable the shim treats the server as authoritative and ignores
    // it.
    //
    // The shim's first read is a synchronous XHR, which WebKit does not put
    // through route interception, so that one call is stubbed in-page. Everything
    // else on the XHR proxies to the real object, so the runtime is unaffected.
    const doc = JSON.stringify({
      rev: 1, updated: null, data: populated ? { v1: JSON.parse(SEED) } : {},
    });
    await page.addInitScript((body) => {
      const Real = window.XMLHttpRequest;
      window.XMLHttpRequest = function () {
        const real = new Real();
        let fake = false;
        return new Proxy(real, {
          get(t, k) {
            if (fake && k === 'status') return 200;
            if (fake && k === 'responseText') return body;
            if (k === 'open') {
              return (m, u, a) => {
                fake = m === 'GET' && String(u).includes('/api/config');
                if (!fake) t.open(m, u, a);
              };
            }
            if (k === 'send') return (b) => { if (!fake) t.send(b); };
            const v = t[k];
            return typeof v === 'function' ? v.bind(t) : v;
          },
        });
      };
    }, doc);
    await page.route('**/api/config', (route) => {
      const body = route.request().method() === 'GET'
        ? { rev: 1, updated: null, data: populated ? { v1: JSON.parse(SEED) } : {} }
        : { rev: 1, updated: null, changed: false };
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });
    });
    await page.goto(base + '/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);

    const s = await page.evaluate(() => {
      const cs = (sel, p) => {
        const el = document.querySelector(sel);
        return el ? getComputedStyle(el)[p] : null;
      };
      const wrap = document.querySelector('.page-wrap');
      const plus = document.querySelector('.add-app span');
      return {
        rendered: !!document.querySelector('.app-grid'),
        rawTemplate: /\{\{/.test(document.body.innerText),
        apps: document.querySelectorAll('.app-grid a[data-drag-id]').length,
        wrapWidth: wrap && wrap.getBoundingClientRect().width,
        wrapMargin: parseFloat(cs('.page-wrap', 'marginLeft')),
        boxSizing: cs('.page-wrap', 'boxSizing'),
        headerMargin: cs('.page-wrap > header', 'marginTop'),
        addBorder: cs('.add-app', 'borderTopColor'),
        addOutline: cs('.add-app', 'outlineStyle'),
        plusBg: plus && getComputedStyle(plus).backgroundColor,
        addOpacity: cs('.add-app', 'opacity'),
        sheets: [...document.querySelectorAll('style')].length,
        attached: [...document.querySelectorAll('style')].filter((x) => x.sheet).length,
      };
    });

    const transparent = (c) => c === 'rgba(0, 0, 0, 0)' || c === 'transparent';

    check(`${label}: renders`, s.rendered);
    check(`${label}: no un-hydrated {{ }} left visible`, !s.rawTemplate);
    check(`${label}: every <style> attached`, s.sheets > 0 && s.sheets === s.attached,
      `${s.attached}/${s.sheets}`);
    check(`${label}: wrapper is border-box`, s.boxSizing === 'border-box', s.boxSizing);
    check(`${label}: wrapper is 90% wide`, Math.abs(s.wrapWidth - 1504 * 0.9) < 2, s.wrapWidth);
    check(`${label}: margin is 5% of the viewport`, Math.abs(s.wrapMargin - 1504 * 0.05) < 2,
      s.wrapMargin);
    check(`${label}: header dropped 50px`, s.headerMargin === '50px', s.headerMargin);
    check(`${label}: add button has no border`, transparent(s.addBorder), s.addBorder);
    check(`${label}: add button has no focus ring at rest`, s.addOutline === 'none', s.addOutline);
    check(`${label}: + glyph has no background`, transparent(s.plusBg), s.plusBg);

    // The focus ring is a separate property from the border and survived the
    // border fix. It has to stay gone through a real click and after a keyboard
    // press has put the browser into keyboard modality — that is what makes
    // :focus-visible match again and brought the ring back.
    await page.click('.add-app');
    await page.waitForTimeout(300);
    const ringOnClick = await page.evaluate(
      () => getComputedStyle(document.querySelector('.add-app')).outlineStyle
    );
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    const ringAfterKey = await page.evaluate(
      () => getComputedStyle(document.querySelector('.add-app')).outlineStyle
    );
    check(`${label}: no focus ring after a mouse click`, ringOnClick === 'none', ringOnClick);
    check(`${label}: no focus ring after a key press`, ringAfterKey === 'none', ringAfterKey);

    // Opening the add dialog must focus the URL field. This is the half of the
    // clipboard feature that works without a secure context, and it is what makes
    // pasting a single keystroke on a plain-http LAN deployment.
    await page.click('.add-app');
    await page.waitForTimeout(500);
    const focused = await page.evaluate(() => document.activeElement?.dataset?.field);
    check(`${label}: add dialog focuses the URL field`, focused === 'url', focused);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);

    // The header's lock and settings buttons, flat in both lock states. The lock
    // border used to be a locked-state signal, so assert the accent plate that
    // now carries that job on its own is still there.
    const hdr = () => page.evaluate(() =>
      [...document.querySelectorAll('.hdr-actions button')].map((el) => {
        const c = getComputedStyle(el);
        return { title: el.title, border: c.borderTopColor, outline: c.outlineStyle,
                 bg: c.backgroundColor };
      }));
    const unlocked = await hdr();
    check(`${label}: two header buttons found`, unlocked.length === 2, unlocked.length);
    check(`${label}: header buttons have no border`,
      unlocked.every((b) => transparent(b.border)), JSON.stringify(unlocked.map((b) => b.border)));
    check(`${label}: header buttons have no focus ring`,
      unlocked.every((b) => b.outline === 'none'), JSON.stringify(unlocked.map((b) => b.outline)));
    // Transparent rather than a copy of the page colour, so it keeps following
    // the theme instead of going stale against it.
    check(`${label}: header buttons show the page background`,
      unlocked.every((b) => transparent(b.bg)), JSON.stringify(unlocked.map((b) => b.bg)));

    await page.click('.hdr-actions button[title="Lock layout"]');
    await page.waitForTimeout(400);
    const locked = await hdr();
    const lockBtn = locked.find((b) => /locked/i.test(b.title));
    check(`${label}: lock button stays borderless when locked`,
      !!lockBtn && transparent(lockBtn.border), lockBtn && lockBtn.border);
    check(`${label}: locked state still marked by the accent plate`,
      !!lockBtn && lockBtn.bg === 'rgb(148, 204, 138)', lockBtn && lockBtn.bg);
    check(`${label}: no external requests`, external.length === 0, external[0]);
    check(`${label}: no failed requests`, broken.length === 0, broken[0]);
    if (populated) {
      check(`${label}: seed applied`, s.apps === 2, s.apps);
      check(`${label}: add button hidden until hover`, s.addOpacity === '0', s.addOpacity);
      await page.hover('.app-grid');
      await page.waitForTimeout(400);
      const revealed = await page.evaluate(
        () => getComputedStyle(document.querySelector('.add-app')).opacity
      );
      check(`${label}: hover reveals the add button`, revealed === '1', revealed);
    } else {
      check(`${label}: add button visible when empty`, s.addOpacity === '1', s.addOpacity);
    }
    await page.close();
  }
  await browser.close();
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
