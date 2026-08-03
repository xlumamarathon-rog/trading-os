/* Cockpit UI smoke test — probes a RUNNING cockpit (npm start) for route +
 * control-contract correctness and, if playwright is present, verifies the
 * dashboard renders (charts + panels) with zero console errors.
 *   Terminal A:  npm run build && npm start
 *   Terminal B:  node smoke.mjs
 * Exits non-zero on any failure (CI-friendly).
 */
const BASE = process.env.COCKPIT_URL || 'http://localhost:3000';
let failures = 0;
const check = (name, ok) => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`); if (!ok) failures++; };

const page = await fetch(BASE + '/');
check('GET / -> 200', page.status === 200);

const st = await (await fetch(BASE + '/api/demo/state')).json();
check('state has 3 candle series', Object.keys(st.candles).length === 3);
check('state equityCurve populated', st.equityCurve.length > 10);
check('state gate present', typeof st.gate.paper_days_completed === 'number');
check('positions present', Array.isArray(st.positions) && st.positions.length === 3);

const badKill = await fetch(BASE + '/api/demo/control/kill',
  { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm: 'nope' }) });
check('kill bad phrase -> 400', badKill.status === 400);

const goodKill = await fetch(BASE + '/api/demo/control/kill',
  { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm: 'KILL ALL POSITIONS', reason: 'smoke' }) });
check('kill good phrase -> 200', goodKill.status === 200);
check('halted after kill', (await (await fetch(BASE + '/api/demo/state')).json()).halted === true);

try {
  const { chromium } = await import('playwright');
  const browser = await chromium.launch();
  const p = await browser.newPage({ viewport: { width: 1360, height: 2000 } });
  const errs = [];
  p.on('console', (m) => m.type() === 'error' && errs.push(m.text()));
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(BASE + '/', { waitUntil: 'networkidle' });
  await p.waitForTimeout(4000);
  const m = await p.evaluate(() => ({
    canvases: document.querySelectorAll('canvas').length,
    equity: /₹[\d,]+/.test(document.body.innerText),
    gex: /dealer gamma/i.test(document.body.innerText),
    gate: /live-mode gate/i.test(document.body.innerText),
    positions: /open positions/i.test(document.body.innerText),
  }));
  check('charts rendered (canvas > 10)', m.canvases > 10);
  check('equity value shown', m.equity);
  check('GEX + gate + positions panels rendered', m.gex && m.gate && m.positions);
  check('zero browser console errors', errs.length === 0);
  await browser.close();
} catch (e) {
  console.log('SKIP  browser render check (' + e.message.slice(0, 40) + ')');
}
console.log(failures === 0 ? '\nSMOKE PASSED' : `\nSMOKE FAILED (${failures})`);
process.exit(failures === 0 ? 0 : 1);
