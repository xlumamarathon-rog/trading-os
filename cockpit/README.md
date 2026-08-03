# Cockpit (MODULE 45) — web + Windows + macOS (Apple Silicon)

**Status: scaffold.** The SPA is built outside the server repo pipeline (Node toolchain).
Everything it needs on the backend is DONE and tested: `src/ops/cockpit_gateway.py` (M44).

## Stack (decided in spec §M45)
- **React + TypeScript + Vite**, charts via **TradingView Lightweight Charts**
- Same build ships as **web PWA** and inside **Tauri 2** shells (Windows + macOS/Metal)
- GPU: rendering only (WebGL/WebGPU — automatic). Zero order logic client-side (spec §12.11).

## Build
```bash
npm create vite@latest cockpit-app -- --template react-ts
cd cockpit-app && npm i lightweight-charts
# web:    npm run build   → deploy dist/ behind TLS, point at gateway URL
# native: npm i -D @tauri-apps/cli && npx tauri init && npx tauri build   (run on Win/mac)
```

## Gateway API contract (Bearer token; roles: viewer | operator)
| Endpoint | Role | Purpose |
|---|---|---|
| GET /state | viewer | positions, P&L, VaR, regime, halted flag |
| GET /approvals | viewer | pending rule/model approvals |
| POST /control/kill | operator | body `{confirm:"KILL ALL POSITIONS", reason}` |
| POST /control/unlock | operator | body `{confirm:"<unlock phrase>"}` |
| POST /control/pause_entries | operator | manual entry pause |
| POST /control/approve/{id} | operator | approve rule/model promotion |

Panels to build: kill-switch (type-phrase confirm), positions + exit-state machine view,
P&L/VaR gauges, GEX regime, approvals inbox, ledger calibration explorer, anomaly timeline.
