// Drive storage-shim.js against the real config service using minimal browser
// stubs, to check the contract the dashboard actually relies on.
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const BASE = process.env.FIREHOME_TEST_BASE || 'http://127.0.0.1:8097';
const ROOT = new URL('..', import.meta.url).pathname;
const shimSrc = fs.readFileSync(ROOT + 'storage-shim.js', 'utf8');

function sync(method, path, body) {
  const args = ['-s', '-X', method, BASE + path, '-w', '\n%{http_code}'];
  if (body) args.push('-H', 'Content-Type: application/json', '-d', body);
  const out = execFileSync('curl', args, { encoding: 'utf8' }).split('\n');
  return { status: Number(out.pop()), text: out.join('\n') };
}

// ── stubs ────────────────────────────────────────────────────────────────
class Storage {
  constructor() { this._m = new Map(); }
  getItem(k) { return this._m.has(k) ? this._m.get(k) : null; }
  setItem(k, v) { this._m.set(k, String(v)); }
  removeItem(k) { this._m.delete(k); }
}
class XMLHttpRequest {
  open(m, u) { this._m = m; this._u = u; }
  send() { const r = sync(this._m, this._u.replace(/^\/api/, '')); this.status = r.status; this.responseText = r.text; }
}
const listeners = {};
const win = {
  localStorage: new Storage(),
  addEventListener: (t, f) => { (listeners[t] ||= []).push(f); },
  XMLHttpRequest, Storage,
  fetch: (u, o) => fetch(BASE + u.replace(/^\/api/, ''), o),
  navigator: {}, location: null, // set per-window below (reload must flag the right window)
  document: {
    createElement: () => ({ style: {}, set textContent(v) { this._t = v; }, get textContent() { return this._t; } }),
    documentElement: { appendChild(c) { this._c = c; }, _c: null },
    body: null,
  },
  setTimeout, clearTimeout, Blob: class {}, JSON, Object, String, Error, Promise,
};
win.window = win;
const mkLocation = (w) => ({ reload() { w.__reloaded = true; } });
win.location = mkLocation(win);

// ── run ──────────────────────────────────────────────────────────────────
const run = (w) => new Function('window', 'localStorage', 'Storage', 'document',
  'XMLHttpRequest', 'fetch', 'navigator', 'location', 'setTimeout', 'clearTimeout', 'Blob',
  `"use strict";${shimSrc}`)(
  w, w.localStorage, w.Storage, w.document, w.XMLHttpRequest, w.fetch, w.navigator,
  w.location, w.setTimeout, w.clearTimeout, w.Blob);

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  (cond ? pass++ : fail++);
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${cond ? '' : '  <- ' + extra}`);
};

// 1. clean server -> app sees no saved state and falls back to its defaults
sync('DELETE', '/config');
run(win);
check('clean server: getItem returns null', win.localStorage.getItem('fireHome.v1') === null);
check('clean server: not degraded', win.fireHomeStorage.degraded() === false);

// 2. a write reaches the server after the debounce
win.localStorage.setItem('fireHome.v1', JSON.stringify({ apps: [{ id: 'plex' }] }));
win.localStorage.setItem('fireHome.theme', JSON.stringify({ accent: '#94cc8a' }));
await sleep(900);
const stored = JSON.parse(sync('GET', '/config').text);
check('setItem persists v1 server-side', stored.data.v1?.apps?.[0]?.id === 'plex', JSON.stringify(stored.data));
check('setItem persists theme server-side', stored.data.theme?.accent === '#94cc8a', JSON.stringify(stored.data));
check('two writes coalesce into one revision', stored.rev === 1, 'rev=' + stored.rev);
check('nothing leaked into real localStorage', win.localStorage._m.size === 0, [...win.localStorage._m.keys()].join());

// 3. a fresh page load sees the server state
const win2 = { ...win, localStorage: new Storage(), window: null, __reloaded: false };
win2.window = win2;
win2.location = mkLocation(win2);
run(win2);
check('reload reads server state', JSON.parse(win2.localStorage.getItem('fireHome.v1')).apps[0].id === 'plex');
check('reload reads server theme', JSON.parse(win2.localStorage.getItem('fireHome.theme')).accent === '#94cc8a');

// 4. unmanaged keys are untouched
win2.localStorage.setItem('somethingElse', 'x');
check('unmanaged key uses real storage', win2.localStorage.getItem('somethingElse') === 'x');

// 5. removeItem propagates as a delete
win2.localStorage.removeItem('fireHome.theme');
await sleep(900);
check('removeItem drops the key server-side', !('theme' in JSON.parse(sync('GET', '/config').text).data));

// 6. console reset wipes everything and reloads
await win2.fireHomeStorage.reset();
const afterReset = JSON.parse(sync('GET', '/config').text);
check('reset() empties the server document', Object.keys(afterReset.data).length === 0, JSON.stringify(afterReset));
check('reset() reloads the page', win2.__reloaded === true);

// 6b. A value that is not JSON must not silently drop the pending change. The app
// always writes JSON, but these keys are reachable from a console, and the bug
// pinned here was a lost write with no warning: flush() cleared its dirty flag
// before serialising, so a snapshot that threw discarded the edit for good.
const winJunk = { ...win, localStorage: new Storage(), window: null };
winJunk.window = winJunk;
winJunk.location = mkLocation(winJunk);
sync('DELETE', '/config');
sync('PUT', '/config', JSON.stringify({ data: { v1: { apps: [{ id: 'keep' }] } } }));
run(winJunk);
const beforeJunk = JSON.parse(sync('GET', '/config').text).data;
winJunk.localStorage.setItem('fireHome.v1', '{{{ not json');
winJunk.localStorage.setItem('fireHome.theme', JSON.stringify({ accent: '#ffffff' }));
await sleep(900);
// This one holds either way — a snapshot that throws sends nothing at all, so the
// store is never clobbered. It is the invariant, not the regression pin.
check('a non-JSON value does not wipe the stored config',
  JSON.stringify(JSON.parse(sync('GET', '/config').text).data) === JSON.stringify(beforeJunk),
  sync('GET', '/config').text);
// This is the pin. With the old ordering `dirty` was cleared before serialising,
// so the shim went quiet permanently: every later flush threw on the same junk and
// nothing was ever sent or reported.
check('the failure is surfaced, not swallowed',
  /could not prepare the save/.test(winJunk.document.documentElement._c?.textContent || ''),
  winJunk.document.documentElement._c?.textContent);

// 7. server down at load -> degraded, falls through to real localStorage
const winDown = { ...win, localStorage: new Storage(), window: null };
winDown.window = winDown;
winDown.location = mkLocation(winDown);
winDown.XMLHttpRequest = class { open() {} send() { this.status = 0; this.responseText = ''; } };
run(winDown);
check('server down: degraded mode', winDown.fireHomeStorage.degraded() === true);
winDown.localStorage.setItem('fireHome.v1', '{"apps":[]}');
check('server down: writes fall through to localStorage',
  winDown.localStorage._m.get('fireHome.v1') === '{"apps":[]}');
check('server down: warning banner shown',
  /server storage unreachable/.test(winDown.document.documentElement._c?.textContent || ''),
  JSON.stringify(winDown.document.documentElement._c?.textContent));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
