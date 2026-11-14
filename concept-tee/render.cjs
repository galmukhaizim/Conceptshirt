// Render an SVG file to a square PNG with the pre-installed headless Chromium.
// usage: node render.cjs <in.svg> <out.png> [size]
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const [svgPath, pngPath, sizeArg] = process.argv.slice(2);
  const size = parseInt(sizeArg || '2048', 10);
  const svg = fs.readFileSync(svgPath, 'utf8');
  const html = `<!doctype html><html><head><meta charset="utf-8">
    <style>html,body{margin:0;padding:0;background:#000;width:${size}px;height:${size}px;overflow:hidden}
    svg{display:block;width:${size}px;height:${size}px}</style></head><body>${svg}</body></html>`;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await page.setContent(html, { waitUntil: 'load' });
  await page.screenshot({ path: pngPath, clip: { x: 0, y: 0, width: size, height: size } });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
