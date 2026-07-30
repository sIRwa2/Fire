// The app cards' padding: does the hover highlight have room, without the rows
// spreading or the content drifting out of alignment? Needs playwright, like
// tests/render.mjs:
//
//     node tests/cards.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit, devices } from 'playwright';

const APPS = ['plex', 'proxmox', 'gitea', 'grafana', 'nginx', 'portainer'];
const build = (mobileLayout) => ({
  v1: {
    apps: APPS.map(n => ({ id: n, icon: n, title: n, desc: n + '.lan', url: 'http://' + n + '.lan' })),
    groups: [{ id: 'g1', name: 'Dev', bookmarks: [{ id: 'gitea', icon: 'gitea', title: 'gitea', url: 'http://gitea.lan' }] }],
    appsTitle: 'Applications', bookmarksTitle: 'Bookmarks',
  },
  ...(mobileLayout ? { theme: { mobileLayout } } : {}),
});

const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '') + '/';
let fail = 0;
const check = (n, cond, extra) => { cond ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + extra)); };

const browser = await webkit.launch();
const open = async (doc, opts) => {
  const page = await browser.newPage(opts);
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
  return page;
};

const probe = (page) => page.evaluate(() => {
  const cards = [...document.querySelectorAll('.app-grid a[data-drag-id]')];
  const r = cards.map(c => c.getBoundingClientRect());
  const add = document.querySelector('.add-app').getBoundingClientRect();
  const wrap = document.querySelector('.page-wrap').getBoundingClientRect();
  const heading = document.querySelector('h2').getBoundingClientRect();
  // Two cards in the same row must never overlap: the negative side margins bleed
  // 12px each way into a 24px column gap, so they may touch but not more.
  let overlaps = 0;
  for (let i = 0; i < r.length; i++) {
    for (let j = i + 1; j < r.length; j++) {
      if (Math.abs(r[i].top - r[j].top) < 2 &&
          r[i].right > r[j].left + 0.5 && r[j].right > r[i].left + 0.5) overlaps++;
    }
  }
  const icon = document.querySelector('.app-grid img');
  return {
    cardH: Math.round(r[0].height),
    cardW: Math.round(r[0].width),
    addH: Math.round(add.height),
    pitch: r.length > 4 ? Math.round(r[4].top - r[0].top) : null,
    padding: getComputedStyle(cards[0]).padding,
    iconVsHeading: icon ? Math.round(icon.getBoundingClientRect().left - heading.left) : null,
    insideWrap: r.every(x => x.left >= wrap.left - 0.5 && x.right <= wrap.right + 0.5) &&
      add.left >= wrap.left - 0.5 && add.right <= wrap.right + 0.5,
    overlaps,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  };
});

const desk = await open(build(null), { viewport: { width: 1400, height: 900 } });
const d = await probe(desk);
console.log('desktop:', JSON.stringify(d));
check('the card is padded', d.padding === '9px 12px', d.padding);
// 34px was the flush height, when the highlight was the content's own box.
check('the highlight is 52px tall, not 34px', d.cardH === 52, d.cardH);
check('the row pitch barely moved (was 62px)', d.pitch === 66, d.pitch);
// The side bleed is what keeps the content where it was; without it every icon
// and title shifts right by 12px and stops lining up with the section heading.
check('icons still align with the section heading', d.iconVsHeading === 0, d.iconVsHeading);
check('cards in a row do not overlap', d.overlaps === 0, d.overlaps);
check('nothing escapes the page wrapper', d.insideWrap, d.insideWrap);
check('no horizontal overflow', !d.overflow, d.overflow);
// The add button carries its own min-height:60px and box-sizing:border-box, so
// the padding sits inside it rather than adding to it.
check('the add button is not inflated by the padding', d.addH === 60, d.addH);
const hovered = await desk.evaluate(async () => {
  const a = [...document.querySelectorAll('.app-grid a[data-drag-id]')][2];
  const r = a.getBoundingClientRect();
  return { w: Math.round(r.width), h: Math.round(r.height) };
});
check('the highlight plate is wider than the grid column', hovered.w > 172, JSON.stringify(hovered));
await desk.close();

// A phone is where the bleed could escape: .page-wrap goes to width:100% there.
for (const mode of ['both', 'icons', 'text']) {
  const page = await open(build(mode), devices['iPhone 13']);
  const m = await probe(page);
  console.log(`phone/${mode}:`, JSON.stringify(m));
  check(`phone/${mode}: stays inside the wrapper`, m.insideWrap, m.insideWrap);
  check(`phone/${mode}: no horizontal overflow`, !m.overflow, m.overflow);
  check(`phone/${mode}: no overlapping hit areas`, m.overlaps === 0, m.overlaps);
  if (mode === 'both') {
    check('phone/both: icons still align with the heading', m.iconVsHeading === 0, m.iconVsHeading);
  }
  await page.close();
}

console.log(fail ? `\n${fail} failed` : '\nall good');
await browser.close();
process.exit(fail ? 1 : 0);
