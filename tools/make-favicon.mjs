// Rasterise favicon.svg into the raster favicon set.
//
//     npm install playwright && npx playwright install --with-deps chromium
//     node tools/make-favicon.mjs
//
// favicon.svg is the source of truth and is what modern browsers actually use;
// this only produces the fallbacks: favicon.png, apple-touch-icon.png, and
// favicon.ico for clients that probe /favicon.ico blindly. All four are committed,
// so the Docker build never runs this.
//
// It renders with a real browser rather than the hand-rolled rasteriser this
// replaced. That one drew geometry defined in its own source, where even-odd
// filling was fine because it controlled the shapes. The artwork is now an SVG
// with inner subpaths for the windows, and SVG's default fill rule is nonzero —
// getting that subtly wrong would silently fill the holes. A browser is the
// reference implementation, and it is already a dev dependency for
// tests/render.mjs.
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const root = path.join(import.meta.dirname, '..');
const svg = fs.readFileSync(path.join(root, 'favicon.svg'), 'utf8');

const PNG_SIZE = 32;
const APPLE_SIZE = 180;
const ICO_SIZES = [16, 32, 48];

const browser = await chromium.launch();
const page = await browser.newPage();

async function render(size) {
  await page.setViewportSize({ width: size, height: size });
  // The svg is inlined rather than loaded from disk so no file:// access is
  // needed, and margin:0 keeps the artwork flush to the viewport.
  await page.setContent(
    `<style>html,body{margin:0;padding:0;background:transparent}` +
      `svg{display:block;width:${size}px;height:${size}px}</style>${svg}`,
    { waitUntil: 'load' }
  );
  // omitBackground keeps the corners outside the circle transparent.
  return page.screenshot({ omitBackground: true });
}

function ico(entries) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);              // reserved
  header.writeUInt16LE(1, 2);              // type: icon
  header.writeUInt16LE(entries.length, 4);
  let offset = 6 + 16 * entries.length;
  const dir = [];
  for (const [size, png] of entries) {
    const e = Buffer.alloc(16);
    e.writeUInt8(size < 256 ? size : 0, 0); // width
    e.writeUInt8(size < 256 ? size : 0, 1); // height
    e.writeUInt8(0, 2);                     // palette
    e.writeUInt8(0, 3);                     // reserved
    e.writeUInt16LE(1, 4);                  // colour planes
    e.writeUInt16LE(32, 6);                 // bits per pixel
    e.writeUInt32LE(png.length, 8);
    e.writeUInt32LE(offset, 12);
    offset += png.length;
    dir.push(e);
  }
  return Buffer.concat([header, ...dir, ...entries.map(([, png]) => png)]);
}

const written = [];
const write = (name, data) => {
  fs.writeFileSync(path.join(root, name), data);
  written.push(`${name} (${data.length} bytes)`);
};

write('favicon.png', await render(PNG_SIZE));
write('apple-touch-icon.png', await render(APPLE_SIZE));
const icoParts = [];
for (const size of ICO_SIZES) icoParts.push([size, await render(size)]);
write('favicon.ico', ico(icoParts));

await browser.close();
for (const line of written) console.log('wrote ' + line);
console.log('favicon.svg is the source and was not modified.');
