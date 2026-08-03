# Trading OS Cockpit — Next.js

Production monitoring & control UI (MODULE 45). Next.js 15 + React 19 + TypeScript,
charts via **lightweight-charts**. Talks ONLY to the M44 gateway; **zero order
logic client-side** (spec §12.11) — renders state, sends authenticated intents.

## Features
- Equity-curve area chart + per-symbol candlestick charts (India / forex / crypto legs)
- VaR-vs-limit gauge, dealer-gamma (GEX) heatmap visualizer
- Live exit-state machine per position (RISK_ON / BREAKEVEN / TRAILING / EXITED)
- Kill-switch panel (typed phrase confirm) + unlock; role-aware (viewer cannot control)
- Approvals inbox (rule/model human gate), worker-health chips, event feed
- Live-mode gate progress (the 5 clauses that must pass before live)

## Run
```bash
npm install
npm run build && npm start          # http://localhost:3000  (demo mode, mock data)
# against the real gateway:
NEXT_PUBLIC_GATEWAY_URL=https://gateway.internal npm start
```
Demo mode (no env var) serves mock data via /api/demo/* so the UI runs standalone.

## Test
```bash
npm run build && npm start &        # terminal A
node smoke.mjs                      # terminal B — routes + control contract + render
```
Deploy: `output: 'standalone'` → copy `.next/standalone` to the VPS behind TLS.
