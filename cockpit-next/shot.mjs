import { chromium } from 'playwright';
const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1360, height: 2100 } });
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });
await page.waitForTimeout(4500); // let polling fetch + charts render
// verify real dashboard content is present (not just the shell)
const markers = await page.evaluate(() => ({
  equityCurve: !!document.body.innerText.match(/Equity curve/i),
  gex: !!document.body.innerText.match(/Dealer gamma/i),
  gate: !!document.body.innerText.match(/Live-mode gate/i),
  positions: !!document.body.innerText.match(/Open positions/i),
  canvases: document.querySelectorAll('canvas').length,
  equityText: (document.body.innerText.match(/₹[\d,]+/) || ['none'])[0],
}));
console.log('MARKERS:', JSON.stringify(markers));
console.log('CONSOLE_ERRORS:', errors.length, JSON.stringify(errors.slice(0,5)));
await page.screenshot({ path: '/tmp/cockpit_full.png', fullPage: true });
await browser.close();
