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
  groups: [
    { id: 'g1', name: 'Dev', bookmarks: [{ id: 'gitea', icon: 'gitea', title: 'Gitea', url: 'http://gitea.lan' }] },
    { id: 'g2', name: 'Ops', bookmarks: [{ id: 'grafana', icon: 'grafana', title: 'Grafana', url: 'http://grafana.lan' }] },
  ],
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
    const external = [], broken = [], placeholders = [];
    // Set only while the search-submit check below is deliberately firing one
    // outbound navigation. Everything else must still count as a leak, so this
    // is a narrow window rather than a permanent exemption for the engine.
    let expectSearchNavigation = false;
    page.on('request', (r) => {
      const host = new URL(r.url()).host;
      if (host && host !== new URL(base).host && !expectSearchNavigation) external.push(r.url());
    });
    page.on('response', (r) => {
      if (r.status() < 400) return;
      const url = decodeURIComponent(r.url());
      // The dc-runtime's un-hydrated {{ }} placeholders are fetched by the browser
      // while parsing and absorbed by nginx's empty_gif rule. Bucketed separately
      // so a plain static server reports the missing rule rather than looking like
      // a broken page.
      (url.includes('{{') ? placeholders : broken).push(`${r.status()} ${url}`);
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
        searchWidth: (() => {
          const i = document.querySelector('.hdr-search input');
          return i && i.getBoundingClientRect().width;
        })(),
        searchType: document.querySelector('.hdr-search input')?.type,
        // The header is the neighbour the bar is meant to align with, so it is
        // the honest width to compare against — .page-wrap has 250px of
        // horizontal padding, so wrapWidth would never match.
        headerWidth: document.querySelector('.page-wrap > header')
          ?.getBoundingClientRect().width,
        // Above the header, and a direct child of .page-wrap — the sibling
        // relationship the reveal selector keys on. Measured rather than
        // assumed from source order.
        searchAboveHeader: (() => {
          const i = document.querySelector('.hdr-search');
          const h = document.querySelector('.page-wrap > header');
          return !!(i && h && i.getBoundingClientRect().bottom <= h.getBoundingClientRect().top);
        })(),
        searchIsWrapChild: !!document.querySelector('.page-wrap > .hdr-search'),
        // Hidden at rest, but still occupying its box: a zero height here would
        // mean the reveal moves everything below it.
        searchOpacity: cs('.hdr-search', 'opacity'),
        searchBoxHeight: Math.round(
          document.querySelector('.hdr-search')?.getBoundingClientRect().height || 0),
        addBorder: cs('.add-app', 'borderTopColor'),
        addOutline: cs('.add-app', 'outlineStyle'),
        plusBg: plus && getComputedStyle(plus).backgroundColor,
        addOpacity: cs('.add-app', 'opacity'),
        cardPadding: cs('.app-grid > a[data-drag-id]', 'padding'),
        cardMarginX: cs('.app-grid > a[data-drag-id]', 'marginLeft'),
        appTarget: document.querySelector('.app-grid a[data-drag-id]')?.target,
        bmTarget: document.querySelector('.bm-cat a[data-drag-id]')?.target,
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
    // type is read off the live element: a browser that does not know
    // type="search" reflects "text", which is exactly the case where the
    // native clear button is missing and the styling above is inert.
    check(`${label}: search input is a native search field`, s.searchType === 'search',
      s.searchType);
    check(`${label}: search bar is a direct child of the wrapper`, s.searchIsWrapChild);
    check(`${label}: search bar is above the header`, s.searchAboveHeader);
    check(`${label}: search bar spans the content column`,
      s.searchWidth && s.headerWidth && Math.abs(s.searchWidth - s.headerWidth) < 2,
      `${s.searchWidth} vs ${s.headerWidth}`);
    check(`${label}: search bar is hidden at rest`, s.searchOpacity === '0', s.searchOpacity);
    check(`${label}: hidden search bar still reserves its height`, s.searchBoxHeight > 20,
      s.searchBoxHeight);

    // The header block is a separate trigger zone ~50px below the bar, so
    // revealing from it meant the bar appeared with the pointer nowhere near
    // it. Checked first, while the pointer is still parked away from both.
    await page.hover('.page-wrap > header');
    await page.waitForTimeout(300);
    const onHeader = await page.evaluate(() =>
      getComputedStyle(document.querySelector('.hdr-search')).opacity);
    check(`${label}: hovering the header does not reveal the search bar`,
      onHeader === '0', onHeader);

    // The point of hiding by opacity: hovering must reveal the bar without
    // moving anything. Measured against the greeting, since that is what a
    // collapse would visibly shove around.
    const beforeHover = await page.evaluate(() =>
      Math.round(document.querySelector('.hdr-row h1').getBoundingClientRect().top));
    await page.hover('.hdr-search');
    await page.waitForTimeout(300);
    const afterHover = await page.evaluate(() => ({
      opacity: getComputedStyle(document.querySelector('.hdr-search')).opacity,
      greetingTop: Math.round(document.querySelector('.hdr-row h1').getBoundingClientRect().top),
    }));
    check(`${label}: hovering the bar's own strip reveals it`,
      afterHover.opacity === '1', afterHover.opacity);
    check(`${label}: revealing the bar does not move the greeting`,
      afterHover.greetingTop === beforeHover, `${beforeHover} -> ${afterHover.greetingTop}`);

    // Typing must not depend on the pointer staying put.
    await page.focus('.hdr-search input');
    await page.mouse.move(1400, 850);
    await page.waitForTimeout(300);
    const searchFocusOpacity = await page.evaluate(() =>
      getComputedStyle(document.querySelector('.hdr-search')).opacity);
    check(`${label}: focused search bar stays visible off-hover`,
      searchFocusOpacity === '1', searchFocusOpacity);

    // Enter must actually navigate, under the deployment's real CSP. This is
    // the only check that exercises it: the search used to submit the form
    // natively, which form-action 'none' blocks with no navigation and no
    // visible error, so the bar looked fine and did nothing. It now navigates
    // with location.assign, which form-action does not govern.
    // Requests are aborted rather than followed, so no traffic leaves the box.
    expectSearchNavigation = true;
    let submitted = null;
    const catchNav = (r) => { submitted = r.request().url(); r.abort(); };
    for (const host of ['**duckduckgo.com**', '**google.com**', '**searx.lan**']) {
      await page.route(host, catchNav);
    }
    await page.type('.hdr-search input', 'proxmox docs');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(1200);
    check(`${label}: Enter searches with the default engine`,
      submitted === 'https://duckduckgo.com/?q=proxmox+docs', submitted || 'no navigation');

    // Switching the engine in Settings must change where Enter goes, and a
    // custom URL must work even though the CSP can never have named it.
    for (const [key, url, expected] of [
      ['google', null, 'https://www.google.com/search?q=proxmox+docs'],
      ['custom', 'https://searx.lan/search?q=%s', 'https://searx.lan/search?q=proxmox+docs'],
    ]) {
      await page.goto(base, { waitUntil: 'networkidle' });
      await page.hover('.page-wrap > header');
      await page.waitForTimeout(200);
      await page.click('button[title="Settings"]');
      await page.waitForTimeout(400);
      await page.selectOption('select[data-k="searchEngine"]', key);
      await page.waitForTimeout(250);
      if (url) {
        await page.fill('input[type="url"]', url);
        await page.dispatchEvent('input[type="url"]', 'change');
        await page.waitForTimeout(250);
      }
      await page.keyboard.press('Escape');
      await page.waitForTimeout(250);
      submitted = null;
      await page.hover('.hdr-search');
      await page.waitForTimeout(150);
      await page.fill('.hdr-search input', 'proxmox docs');
      await page.keyboard.press('Enter');
      await page.waitForTimeout(1200);
      check(`${label}: Enter searches with the ${key} engine`,
        submitted === expected, submitted || 'no navigation');
    }

    for (const host of ['**duckduckgo.com**', '**google.com**', '**searx.lan**']) {
      await page.unroute(host, catchNav);
    }
    // Back to defaults for the checks below, which assume a stock dashboard.
    // The aborted navigation leaves the tab on an error page, where
    // localStorage is inaccessible — so return to the origin before clearing.
    // The theme is stored under fireHome.theme: with no config service behind
    // the shim it degrades to real localStorage, so clearing the engine keys
    // there is what resets the picker.
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.evaluate(() => {
      const raw = localStorage.getItem('fireHome.theme');
      if (!raw) return;
      try {
        const t = JSON.parse(raw);
        delete t.searchEngine;
        delete t.searchUrl;
        localStorage.setItem('fireHome.theme', JSON.stringify(t));
      } catch { localStorage.removeItem('fireHome.theme'); }
    });
    await page.goto(base, { waitUntil: 'networkidle' });
    expectSearchNavigation = false;
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
    // Unlock again: everything below is gated on editable, including the
    // category drag grip, which is absent while the layout is locked.
    await page.click('.hdr-actions button[title^="Layout locked"]');
    await page.waitForTimeout(400);
    check(`${label}: no external requests`, external.length === 0, external[0]);
    check(`${label}: no failed requests`, broken.length === 0, broken[0]);
    check(`${label}: {{ }} placeholders absorbed (needs nginx's empty_gif rule)`,
      placeholders.length === 0, placeholders[0]);
    if (populated) {
      // The cards' inline padding is overridden to 0 on all sides, which left the
      // hover highlight flush against the content; CARD_MARGIN_CSS puts it back,
      // with an equal negative side margin so nothing shifts. Measured here in a
      // real browser because both halves have to survive the cascade together.
      check(`${label}: cards are padded`, s.cardPadding === '9px 12px', s.cardPadding);
      check(`${label}: the side bleed cancels the padding`,
        s.cardMarginX === '-12px', s.cardMarginX);
      // Apps and bookmarks must agree on the link target, whichever it is.
      check(`${label}: link target is consistent and set`,
        s.appTarget === s.bmTarget && ['_blank', '_self'].includes(s.appTarget),
        `${s.appTarget} / ${s.bmTarget}`);

      // Items drag between the two lists, so the dialogs must offer the same
      // fields — the bookmark one used to hide Description, and bookmarks could
      // not be edited at all.
      const dialogFields = async (open) => {
        await open();
        await page.waitForTimeout(400);
        const r = await page.evaluate(() => ({
          fields: [...document.querySelectorAll('[data-screen-label="Add entry modal"] input')]
            .map((i) => i.dataset.field).filter(Boolean).join(','),
          focused: document.activeElement?.dataset?.field,
        }));
        await page.keyboard.press('Escape');
        await page.waitForTimeout(250);
        return r;
      };
      const appDlg = await dialogFields(() => page.click('.add-app'));
      const bmDlg = await dialogFields(() => page.click('.bm-cat button'));
      check(`${label}: both dialogs offer the same fields`,
        appDlg.fields === bmDlg.fields && appDlg.fields.includes('desc'),
        `${appDlg.fields} vs ${bmDlg.fields}`);
      check(`${label}: both dialogs focus the URL field`,
        appDlg.focused === 'url' && bmDlg.focused === 'url',
        `${appDlg.focused} / ${bmDlg.focused}`);
      // The words are grey; the + glyphs keep the accent.
      const labelColours = await page.evaluate(() => {
        const c = (sel) => { const el = document.querySelector(sel); return el ? getComputedStyle(el).color : null; };
        return {
          app: c('.add-app span:last-child'), appPlus: c('.add-app span:first-child'),
          cat: c('.new-cat span:last-child'),
          bm: c('.bm-cat > button span:last-child'), bmPlus: c('.bm-cat > button span:first-child'),
        };
      });
      const GREY = 'rgb(154, 163, 173)', ACCENT = 'rgb(148, 204, 138)';
      check(`${label}: add labels are grey`,
        [labelColours.app, labelColours.cat, labelColours.bm].every((v) => v === GREY),
        JSON.stringify(labelColours));
      check(`${label}: + glyphs keep the accent`,
        labelColours.appPlus === ACCENT && labelColours.bmPlus === ACCENT,
        `${labelColours.appPlus} / ${labelColours.bmPlus}`);

      // Categories are drag-sortable. The risk in making .bm-grid a container is
      // that it encloses every category list, so this also confirms a bookmark
      // still lands in its own list rather than being captured by the outer one.
      const catOrder = () => page.evaluate(() => [...document.querySelectorAll('.bm-cat')]
        .map((c) => c.querySelector('span')?.textContent?.trim()));
      const before = await catOrder();
      const grip = page.locator('.bm-cat').first().locator('.cat-grip');
      const gripBox = await grip.boundingBox();
      const lastBox = await page.locator('.bm-cat').last().boundingBox();
      if (gripBox && lastBox) {
        await page.mouse.move(gripBox.x + gripBox.width / 2, gripBox.y + gripBox.height / 2);
        await page.mouse.down();
        for (let i = 1; i <= 12; i++) {
          await page.mouse.move(
            gripBox.x + (lastBox.x + lastBox.width * 0.6 - gripBox.x) * i / 12,
            gripBox.y + (lastBox.y - gripBox.y) * i / 12, { steps: 1 });
        }
        await page.waitForTimeout(200);
        await page.mouse.up();
        await page.waitForTimeout(500);
      }
      const after = await catOrder();
      check(`${label}: categories are drag-sortable`,
        before.length === 2 && after.join() === before.slice().reverse().join(),
        `${before} -> ${after}`);
      const bmIntact = await page.evaluate(() => [...document.querySelectorAll('.bm-cat')]
        .every((c) => c.querySelectorAll('a[data-drag-id]').length === 1));
      check(`${label}: dragging a category keeps its bookmarks`, bmIntact);

      const canEdit = await page.evaluate(
        () => !!document.querySelector('.bm-cat span[title="Edit"]'));
      check(`${label}: bookmarks have an edit button`, canEdit);

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
