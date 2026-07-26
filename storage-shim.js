/* Redirect the dashboard's localStorage config to the server.
 *
 * The bundled app reads its state synchronously in the component constructor
 * (`JSON.parse(localStorage.getItem('fireHome.v1'))`) and writes it back with
 * setItem on every change. Rather than rewrite the app, this shim intercepts
 * those two keys and backs them with GET/PUT /api/config, so config follows the
 * user to any browser on the LAN instead of living in one profile.
 *
 * localize.py injects this into the bundle's <head> at image-build time, ahead
 * of the app runtime.
 */
(function () {
  'use strict';

  var API = '/api/config';
  var PREFIX = 'fireHome.';
  var KEYS = ['fireHome.v1', 'fireHome.theme'];
  var DEBOUNCE_MS = 400;
  var RETRY_MS = 2000;

  var mem = Object.create(null); // managed key -> JSON string; absent = unset
  var degraded = false;         // true => fall through to real localStorage

  var ls = null;
  try { ls = window.localStorage; } catch (e) { /* storage blocked */ }

  // ── status banner ───────────────────────────────────────────────────────
  var bannerEl = null;
  function banner(msg) {
    if (!bannerEl) {
      bannerEl = document.createElement('div');
      bannerEl.id = '__firehome_storage_msg';
      bannerEl.style.cssText =
        'position:fixed;bottom:12px;left:12px;z-index:99998;max-width:min(520px,90vw);' +
        'font:12px/1.5 ui-monospace,monospace;background:#2a2415;color:#e8c477;' +
        'border:1px solid #5c4a2b;border-radius:8px;padding:8px 12px;white-space:pre-wrap';
    }
    bannerEl.textContent = msg;
    var parent = document.body || document.documentElement;
    if (bannerEl.parentNode !== parent) parent.appendChild(bannerEl);
  }
  function clearBanner() {
    if (bannerEl && bannerEl.parentNode) bannerEl.parentNode.removeChild(bannerEl);
  }

  // ── initial load ────────────────────────────────────────────────────────
  // Synchronous by design. The app reads localStorage while its own <script>
  // evaluates, so the server state has to be in hand before that happens; an
  // async load would mean patching the bundle's mount sequence instead. It is
  // one small request to a service on the same host.
  try {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', API, false);
    xhr.send(null);
    if (xhr.status !== 200) throw new Error('HTTP ' + xhr.status);
    var data = (JSON.parse(xhr.responseText) || {}).data || {};
    for (var i = 0; i < KEYS.length; i++) {
      var short = KEYS[i].slice(PREFIX.length);
      if (data[short] !== null && data[short] !== undefined) {
        mem[KEYS[i]] = JSON.stringify(data[short]);
      }
    }
  } catch (err) {
    // Usable but browser-local. Server state stays authoritative, so these
    // edits are lost on the next successful load — hence the visible warning.
    degraded = true;
    banner(
      'firehome: server storage unreachable (' + (err && err.message) + ').\n' +
      'Changes are saved in this browser only and will be replaced by the ' +
      'server copy on reload.'
    );
  }

  // ── writes ──────────────────────────────────────────────────────────────
  var timer = null, inflight = false, dirty = false, retried = false;

  function schedule(delay) {
    dirty = true;
    if (timer) clearTimeout(timer);
    timer = setTimeout(flush, delay === undefined ? DEBOUNCE_MS : delay);
  }

  function snapshot() {
    var out = {};
    for (var i = 0; i < KEYS.length; i++) {
      var k = KEYS[i], short = k.slice(PREFIX.length);
      // null tells the server to drop the key, so a removeItem propagates.
      out[short] = (k in mem) ? JSON.parse(mem[k]) : null;
    }
    return { data: out };
  }

  function flush() {
    timer = null;
    if (inflight || !dirty) return;
    dirty = false;
    var body;
    try { body = JSON.stringify(snapshot()); } catch (e) { return; }
    inflight = true;
    fetch(API, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: body
    })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        retried = false;
        clearBanner();
      })
      .catch(function (e) {
        dirty = true;
        banner('firehome: could not save to server (' + (e && e.message) + ').');
      })
      .finally(function () {
        inflight = false;
        // One retry per failure run; after that we wait for the next edit so a
        // down server doesn't turn into an endless request loop.
        if (dirty && !timer && !retried) { retried = true; schedule(RETRY_MS); }
      });
  }

  // Covers closing the tab inside the debounce window.
  window.addEventListener('pagehide', function () {
    if (!dirty || degraded || !navigator.sendBeacon) return;
    try {
      navigator.sendBeacon(
        API,
        new Blob([JSON.stringify(snapshot())], { type: 'application/json' })
      );
      dirty = false;
    } catch (e) { /* best effort */ }
  });

  // ── interception ────────────────────────────────────────────────────────
  // Storage.prototype is patched rather than the localStorage instance because
  // prototype methods are reliably writable; the `this === ls` guard keeps
  // sessionStorage and any other Storage object on the original path.
  var origGet = Storage.prototype.getItem;
  var origSet = Storage.prototype.setItem;
  var origRemove = Storage.prototype.removeItem;

  function managed(self, key) {
    return self === ls && KEYS.indexOf(String(key)) >= 0;
  }

  Storage.prototype.getItem = function (key) {
    if (managed(this, key) && !degraded) {
      return (String(key) in mem) ? mem[String(key)] : null;
    }
    return origGet.apply(this, arguments);
  };

  Storage.prototype.setItem = function (key, value) {
    if (managed(this, key) && !degraded) {
      mem[String(key)] = String(value);
      schedule();
      return undefined;
    }
    return origSet.apply(this, arguments);
  };

  Storage.prototype.removeItem = function (key) {
    if (managed(this, key) && !degraded) {
      delete mem[String(key)];
      schedule();
      return undefined;
    }
    return origRemove.apply(this, arguments);
  };

  // ── console escape hatch ────────────────────────────────────────────────
  // The app's own "Reset to defaults" button only clears apps and bookmarks.
  // This wipes the whole server-side config, theme included.
  window.fireHomeStorage = {
    api: API,
    degraded: function () { return degraded; },
    dump: function () { return snapshot().data; },
    reset: function () {
      return fetch(API, { method: 'DELETE' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        for (var i = 0; i < KEYS.length; i++) delete mem[KEYS[i]];
        dirty = false;
        if (timer) { clearTimeout(timer); timer = null; }
        location.reload();
      });
    }
  };
})();
