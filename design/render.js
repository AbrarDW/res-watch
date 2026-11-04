/* Render the design reference images from design/mockup.html.
 *   node design/render.js            (needs Playwright: npm i -D playwright, or the copy in tests/e2e)
 * Writes design/ref-*.png. The mockup uses sample data; real screenshots of the running
 * app live in docs/screenshots/ (see docs/screenshots/README.md).
 */
'use strict';
const path = require('path');
let chromium;
try { ({ chromium } = require('playwright')); } catch (e) { ({ chromium } = require(path.join(__dirname, '../tests/e2e/node_modules/playwright'))); }

const base = 'file://' + path.join(__dirname, 'mockup.html');
const shots = [
  ['ref-overview', '?tab=overview', 1440, 900],
  ['ref-cpu-bars', '?tab=cpu&view=bars', 1440, 900],
  ['ref-cpu-heatmap', '?tab=cpu&view=heatmap', 1440, 900],
  ['ref-cpu-3d', '?tab=cpu&view=3d', 1440, 900],
  ['ref-gpu', '?tab=gpu', 1440, 900],
  ['ref-overview-dark', '?tab=overview&theme=dark', 1440, 900],
  ['ref-mobile', '?tab=overview&mview=list', 390, 844],
  ['ref-empty', '?empty=1', 1440, 900],
  ['ref-apps', '?tab=overview&side=apps', 1440, 900],
  ['ref-filters', '?tab=overview&side=apps&filters=1', 1440, 900],
  ['ref-tree', '?tab=cpu&view=bars&side=tree', 1440, 900],
  ['ref-app-details', '?tab=details&side=procs', 1440, 900],
];

(async () => {
  const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  let failed = 0;
  for (const [name, q, w, h] of shots) {
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2 });
    const errs = [];
    page.on('console', m => m.type() === 'error' && errs.push(m.text()));
    page.on('pageerror', e => errs.push(String(e)));
    await page.goto(base + q);
    await page.waitForTimeout(name.includes('3d') ? 2500 : 800);
    await page.screenshot({ path: path.join(__dirname, name + '.png'), fullPage: w < 500 });
    console.log(name.padEnd(18), errs.length ? 'ERR ' + errs.join(' | ') : 'ok');
    if (errs.length) failed++;
    await page.close();
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
})();
