/* res-watch browser smoke test.
 *
 *   node smoke.cjs [base-url]          (default http://127.0.0.1:8000)
 *
 * Env: RW_USER / RW_PASS  -> log in first (server started with --auth on)
 *      RW_PIDS=1,2        -> PIDs of busy processes to select (optional)
 *      RW_SHOTS=dir       -> save a screenshot of every view there
 *      RW_NO_3D=1         -> skip the WebGL views (machines without GL)
 * Exits non-zero on any failed check or browser console error.
 */
'use strict';
const { chromium } = require('playwright');

const base = (process.argv[2] || 'http://127.0.0.1:8000').replace(/\/$/, '') + '/';
const pids = (process.env.RW_PIDS || '').split(',').filter(Boolean);
const shots = process.env.RW_SHOTS;
let failures = 0;
const check = (ok, msg) => { console.log(`${ok ? 'ok  ' : 'FAIL'}  ${msg}`); if (!ok) failures++; };

(async () => {
  const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  // In login mode the wrong-password attempt and the after-logout check return 401 on purpose;
  // Chrome logs those as resource errors, so they are not counted.
  const expected401 = t => process.env.RW_USER && t.includes('status of 401');
  page.on('console', m => { if (m.type() === 'error' && !expected401(m.text())) errors.push(m.text()); });
  const shot = async name => { if (shots) await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }); };
  const series = id => page.evaluate(i => {
    const el = document.getElementById(i);
    const c = el && window.echarts.getInstanceByDom(el);
    return c ? c.getOption().series.map(s => s.name) : null;
  }, id);

  await page.goto(base);
  if (process.env.RW_USER) {
    check(new URL(page.url()).pathname.endsWith('/login'), 'redirected to the login page');
    await page.fill('#u', process.env.RW_USER);
    await page.fill('#p', 'definitely-wrong');
    await page.click('#go');
    await page.waitForFunction(() => document.querySelector('#err').textContent.length > 0);
    check(true, 'wrong password is rejected');
    await page.fill('#p', process.env.RW_PASS);
    await page.click('#go');
    await page.waitForURL(u => !u.pathname.endsWith('/login'));
    check(await page.isVisible('#logout'), 'logged in, logout button visible');
  }
  await page.waitForSelector('#ptable tbody tr', { timeout: 15000 });
  check(true, 'process list loaded');

  // ---- select: the busy PIDs (if given) plus two apps
  if (pids.length) {
    await page.click('#sidemode button[data-mode=procs]');
    for (const p of pids) {
      await page.fill('#search', p);
      await page.waitForSelector(`#ptable tr[data-id="${p}"]`);
      await page.click(`#ptable tr[data-id="${p}"]`);
    }
    await page.fill('#search', '');
  }
  await page.click('#sidemode button[data-mode=apps]');
  await page.waitForTimeout(400);
  const apps = await page.$$eval('#ptable tr[data-kind=app]', r => r.map(x => x.dataset.id));
  for (const a of apps.slice(0, 2)) await page.click(`#ptable tr[data-kind=app][data-id="${a}"] td.l`);
  const nSel = await page.$$eval('.focusbar .chip', c => c.length);
  check(nSel === pids.length + Math.min(2, apps.length), `${nSel} items selected`);

  // ---- every tab / view
  await page.click('[data-tab=overview]');
  await page.waitForTimeout(3500);
  check((await series('c-cpu'))?.length === nSel, 'overview CPU chart has one series per selection');
  check(await page.$$eval('#statcards .card', c => c.length) === nSel, 'one stat card per selection');
  await shot('overview');

  await page.click('[data-tab=cpu]');
  await page.click('[data-cpuview=bars]');
  await page.waitForTimeout(1500);
  const bars = await series('c-core');
  check(bars && bars.length === nSel + 1 && bars[0] === 'whole system', 'CPU bars: whole system + one series per selection');
  check(await page.$$eval('#cores .core', c => c.length) > 0, 'core cards rendered');
  await shot('cpu-bars');

  await page.click('[data-cpuview=heatmap]');
  await page.waitForTimeout(1200);
  check(await page.$$eval('[id^=c-heat]', h => h.length) === nSel, 'one heatmap per selection');
  await shot('cpu-heatmap');

  if (!process.env.RW_NO_3D) {
    await page.click('[data-cpuview="3d"]');
    await page.waitForTimeout(2500);
    check((await series('c-core'))?.length === nSel, '3D chart has one stacked series per selection');
    await shot('cpu-3d');
  }

  await page.click('[data-tab=gpu]');
  await page.waitForTimeout(800);
  const gpuOk = await page.evaluate(() => fetch('api/meta').then(r => r.json()).then(m => m.gpu.available));
  check(gpuOk ? !!(await series('c-gutil')) : (await page.textContent('#main')).includes('GPU metrics unavailable'),
    gpuOk ? 'GPU charts rendered' : 'GPU tab explains metrics are unavailable');
  await shot('gpu');

  await page.click('[data-tab=details]');
  await page.waitForTimeout(1500);
  const sections = await page.$$eval('.dsec', s => s.length) + await page.$$eval('[id^=dpid] .card', s => s.length);
  check(sections === nSel, 'details: one section per selection');
  await shot('details');

  // ---- tree view + filters popover
  await page.click('#sidemode button[data-mode=tree]');
  await page.click('#fbtn');
  check(await page.isVisible('#popover'), 'filters popover opens');
  await page.click('[data-pop=done]');

  if (process.env.RW_USER) {
    await page.click('#logout');
    await page.waitForURL(u => u.pathname.endsWith('/login'));
    const st = await page.evaluate(() => fetch('api/processes').then(r => r.status));
    check(st === 401, 'after logout the API returns 401');
  }

  check(errors.length === 0, `no browser errors${errors.length ? ': ' + errors.join(' | ') : ''}`);
  await browser.close();
  console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
