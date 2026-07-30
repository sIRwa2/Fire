/* Slide the greeting left to reveal the header's settings and lock buttons.
 *
 * On a phone the two buttons sit permanently beside the greeting, because they
 * are hover-revealed on desktop and TOUCH_CSS has to force them visible where
 * there is no hover. That leaves controls on screen that are used once a week,
 * next to the one line of text the header exists for. This turns them into a
 * swipe: the greeting takes the full width, the buttons wait just off the right
 * edge, and a leftward drag brings them in.
 *
 * The CSS is gated on data-hdr-swipe="1", which this script sets. If it fails to
 * load or throws, the attribute is never set and the header keeps today's
 * always-visible layout — the buttons must never become unreachable, since the
 * lock button is how you get out of a locked dashboard.
 *
 * localize.py injects this into the bundle's <head> at image-build time.
 */
(function () {
  'use strict';

  // Below this the gesture has no committed direction yet, so a slightly
  // diagonal swipe is still allowed to become a vertical scroll.
  var AXIS_THRESHOLD = 8;
  // Used until the buttons have been measured — only for the first paint, where
  // it just has to be wide enough to park them off the edge.
  var FALLBACK_REVEAL = 120;
  var GAP = 12;

  var root = document.documentElement;
  var slide = 0;      // px the greeting is currently pushed left
  var reveal = FALLBACK_REVEAL;
  var startX = 0, startY = 0, base = 0, axis = null, tracking = false;

  function measure() {
    var el = document.querySelector('.hdr-row .hdr-actions');
    var w = el ? el.offsetWidth : 0;
    reveal = w ? w + GAP : FALLBACK_REVEAL;
    root.style.setProperty('--hdr-reveal', reveal + 'px');
  }

  function set(px) {
    slide = px < 0 ? 0 : (px > reveal ? reveal : px);
    root.style.setProperty('--hdr-slide', slide + 'px');
    // A separate unitless 0..1 so the hint chevron can fade with the drag:
    // CSS calc() cannot divide a length by a length.
    root.style.setProperty('--hdr-progress', reveal ? (slide / reveal).toFixed(3) : '0');
  }

  function close() { if (slide) set(0); }

  function inHeader(target) {
    return !!(target && target.closest && target.closest('.hdr-row'));
  }

  document.addEventListener('touchstart', function (e) {
    var t = e.touches[0];
    if (!t) return;
    if (!inHeader(e.target)) { close(); return; }
    // Touches that start on a button are left alone, so a tap still activates it.
    if (e.target.closest('.hdr-actions')) return;
    measure();
    startX = t.clientX; startY = t.clientY;
    base = slide; axis = null; tracking = true;
  }, { passive: true });

  document.addEventListener('touchmove', function (e) {
    if (!tracking) return;
    var t = e.touches[0];
    if (!t) return;
    var dx = t.clientX - startX, dy = t.clientY - startY;
    if (!axis) {
      if (Math.abs(dx) < AXIS_THRESHOLD && Math.abs(dy) < AXIS_THRESHOLD) return;
      axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
      // Vertical wins: hand the gesture back to the page scroll and stay out of
      // it. touch-action:pan-y means the browser never waited on us for that.
      if (axis !== 'x') { tracking = false; return; }
      root.setAttribute('data-hdr-drag', '1');
    }
    // Dragging left is a negative dx, and pushes the greeting further left.
    set(base - dx);
  }, { passive: true });

  function release() {
    if (!tracking) return;
    tracking = false;
    root.removeAttribute('data-hdr-drag');
    // Past halfway it opens, otherwise it springs back — so a flick that starts
    // from open and barely moves does not toggle by accident.
    set(slide > reveal / 2 ? reveal : 0);
  }
  document.addEventListener('touchend', release, { passive: true });
  document.addEventListener('touchcancel', release, { passive: true });

  // Using one of the buttons closes the header again; whatever it opened is now
  // the thing on screen.
  document.addEventListener('click', function (e) {
    if (e.target && e.target.closest && e.target.closest('.hdr-row .hdr-actions button')) close();
  }, true);

  // A rotation changes nothing about the button widths, but re-measuring is
  // cheap and keeps an open header pinned to the right edge.
  window.addEventListener('resize', function () {
    measure();
    if (slide) set(reveal);
  });

  root.setAttribute('data-hdr-swipe', '1');
  // The header is rendered by the app, so it does not exist yet at any document
  // event this script can hook. Keep looking until it appears, then stop. Until
  // then the CSS fallback parks the buttons off the edge, which is wider than
  // they are — and the first drag re-measures anyway, so this only makes the
  // resting position exact.
  var tries = 0;
  (function seek() {
    measure();
    if (!document.querySelector('.hdr-row .hdr-actions') && tries++ < 40) {
      setTimeout(seek, 100);
    }
  })();
})();
