// Does the greeting slide left to reveal the header buttons on a phone, and do
// all three gates hold? Needs playwright, like tests/render.mjs:
//
//     node tests/header-swipe.mjs http://localhost:8080
//
// Non-destructive: the config is stubbed per-page, never written to the service.
import { webkit, devices } from 'playwright';
const doc = { v1:{ apps:[{id:'plex',icon:'plex',title:'Plex',desc:'plex.lan',url:'http://plex.lan'}],
  groups:[{id:'g1',name:'Dev',bookmarks:[{id:'gitea',icon:'gitea',title:'gitea',url:'http://g.lan'}]}],
  appsTitle:'Applications', bookmarksTitle:'Bookmarks' } };
const base = (process.argv[2] || 'http://localhost:8080').replace(/\/$/, '') + '/';
const b = await webkit.launch();
let fail = 0;
const check = (n, c, x) => { c ? console.log('ok   ' + n) : (fail++, console.log('FAIL ' + n + '  <- ' + x)); };

const open = async (opts) => {
  const p = await b.newPage(opts);
  p.on('pageerror', e => { fail++; console.log('PAGEERROR ' + e.message); });
  await p.route('**/api/config', r => r.fulfill({ status:200, contentType:'application/json', body: JSON.stringify({rev:1,data:doc}) }));
  await p.addInitScript(() => { const R=window.XMLHttpRequest; window.XMLHttpRequest=function(){
    const r=new R(); let f=false; return new Proxy(r,{get(t,k){
      if(f&&k==='status')return 200; if(f&&k==='responseText')return window.__s;
      if(k==='open')return(m,u,a)=>{f=m==='GET'&&String(u).includes('/api/config'); if(!f)t.open(m,u,a);};
      if(k==='send')return(x)=>{if(!f)t.send(x);};
      const v=t[k]; return typeof v==='function'?v.bind(t):v;}});};});
  await p.addInitScript(d=>{window.__s=d;}, JSON.stringify({rev:1,data:doc}));
  await p.goto(base, { waitUntil:'networkidle' });
  await p.waitForTimeout(1300);
  return p;
};

// Synthesised touch drag; playwright's touchscreen only taps.
const drag = (p, x0, y0, dx, dy = 0, steps = 10) => p.evaluate(async ([x0,y0,dx,dy,steps]) => {
  const el = document.elementFromPoint(x0, y0);
  // WebKit (like Safari) has no Touch constructor; createTouch/createTouchList
  // is the only way to synthesise one.
  const t = (x, y) => document.createTouch(window, el, 1, x, y, x, y);
  const list = (...ts) => document.createTouchList(...ts);
  const fire = (type, x, y) => {
    const cur = type === 'touchend' ? list() : list(t(x, y));
    el.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true,
      touches: cur, targetTouches: cur, changedTouches: list(t(x, y)) }));
  };
  fire('touchstart', x0, y0);
  for (let i = 1; i <= steps; i++) {
    fire('touchmove', x0 + dx*i/steps, y0 + dy*i/steps);
    await new Promise(r => requestAnimationFrame(r));
  }
  fire('touchend', x0 + dx, y0 + dy);
}, [x0,y0,dx,dy,steps]);

const p_wait = (p) => p.waitForTimeout(400);
const look = (p) => p.evaluate(() => {
  const r = document.documentElement, a = document.querySelector('.hdr-actions');
  const ar = a.getBoundingClientRect(), hr = document.querySelector('.hdr-row').getBoundingClientRect();
  return {
    enabled: r.getAttribute('data-hdr-swipe'),
    slide: r.style.getPropertyValue('--hdr-slide'),
    reveal: r.style.getPropertyValue('--hdr-reveal'),
    // Visible = the buttons are inside the clipped header box.
    visible: ar.right <= hr.right + 1 && ar.left >= hr.left - 1,
    offRight: Math.round(ar.left - hr.right),
    h1Shift: getComputedStyle(document.querySelector('.hdr-row h1')).transform,
    clipped: getComputedStyle(document.querySelector('.hdr-row')).overflow,
  };
});

console.log('== phone ==');
const p = await open({ ...devices['iPhone 13'] });
let s = await look(p);
console.log(JSON.stringify(s));
check('script enabled the CSS', s.enabled === '1', s.enabled);
check('buttons start parked off the right edge', !s.visible && s.offRight >= 0, JSON.stringify(s));
check('header clips them', s.clipped === 'hidden', s.clipped);
check('reveal distance measured, not the fallback', s.reveal === '90px', s.reveal);

const hdr = await p.evaluate(() => { const r = document.querySelector('.hdr-row').getBoundingClientRect();
  return { x: Math.round(r.left + 40), y: Math.round(r.top + r.height/2) }; });

await drag(p, hdr.x, hdr.y, -120);
await p.waitForTimeout(400);
s = await look(p);
console.log('after swipe left :', JSON.stringify(s));
check('swiping left reveals the buttons', s.visible, JSON.stringify(s));
check('the greeting moved with it', s.h1Shift.includes('-90') || /matrix.*-9\d/.test(s.h1Shift), s.h1Shift);
check('buttons are tappable once revealed',
  await p.evaluate(() => { const b = document.querySelector('.hdr-actions button[title="Settings"]');
    const r = b.getBoundingClientRect(); const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
    return !!hit && !!hit.closest('.hdr-actions'); }), 'covered');

await drag(p, hdr.x, hdr.y, 120);
await p.waitForTimeout(400);
s = await look(p);
check('swiping back right hides them again', !s.visible, JSON.stringify(s));

// A short drag must spring back rather than latch open.
await drag(p, hdr.x, hdr.y, -25);
await p.waitForTimeout(400);
check('a short drag springs back', !(await look(p)).visible, JSON.stringify(await look(p)));

// A vertical swipe must scroll the page, not open the header.
await drag(p, hdr.x, hdr.y, -6, -140);
await p.waitForTimeout(400);
check('a vertical swipe does not open it', !(await look(p)).visible, JSON.stringify(await look(p)));

// Tapping a header button closes it again.
await drag(p, hdr.x, hdr.y, -120); await p.waitForTimeout(400);
await p.click('.hdr-actions button[title="Settings"]'); await p.waitForTimeout(500);
const panelOpen = await p.evaluate(() => [...document.querySelectorAll('div')].some(d =>
  /Mobile layout/.test(d.textContent) && getComputedStyle(d).position === 'fixed'));
check('the revealed settings button still opens the panel', panelOpen, panelOpen);
await p.keyboard.press('Escape'); await p.waitForTimeout(400);
check('using a button closes the header', !(await look(p)).visible, JSON.stringify(await look(p)));

// Tapping elsewhere closes it.
await drag(p, hdr.x, hdr.y, -120); await p.waitForTimeout(400);
await drag(p, 100, 600, 0, 0, 1); await p.waitForTimeout(400);
check('tapping outside closes it', !(await look(p)).visible, JSON.stringify(await look(p)));
await p.close();

console.log('\n== locked ==');
const lk = await open({ ...devices['iPhone 13'] });
await drag(lk, hdr.x, hdr.y, -120); await p_wait(lk);
await lk.click('.hdr-actions button[title="Lock layout"]'); await p_wait(lk);
check('the lock button works from the revealed header',
  await lk.evaluate(() => document.querySelector('[data-locked]')?.dataset.locked === '1'),
  await lk.evaluate(() => document.querySelector('[data-locked]')?.dataset.locked));
await drag(lk, hdr.x, hdr.y, -120); await p_wait(lk);
const ls = await look(lk);
check('a locked dashboard can still be swiped open to unlock', ls.visible, JSON.stringify(ls));
check('the lock button is reachable while locked',
  await lk.evaluate(() => { const b = document.querySelector('.hdr-actions button[title^="Layout locked"]');
    const r = b.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!hit && !!hit.closest('.hdr-actions') && getComputedStyle(b).pointerEvents !== 'none'; }),
  'unreachable');
await lk.close();

console.log('\n== narrow desktop window (no touch) ==');
const d = await open({ viewport: { width: 420, height: 900 } });
const ds = await look(d);
console.log(JSON.stringify(ds));
check('a mouse-driven narrow window keeps the buttons in place', ds.visible, JSON.stringify(ds));
await d.close();

console.log('\n== desktop ==');
const w = await open({ viewport: { width: 1400, height: 900 } });
const ws = await look(w);
check('desktop untouched', ws.visible && ws.clipped === 'visible', JSON.stringify(ws));
await w.close();

console.log(fail ? `\n${fail} failed` : '\nall good');
await b.close();
process.exit(fail ? 1 : 0);
