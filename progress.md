# Trading OS — Build Progress Report

**Updated:** 2026-08-04 · **Session:** Build #3 (autonomous full run) · **Repo:** https://github.com/xlumamarathon-rog/trading-os

---

## Scoreboard — FULL MODULE SET BUILT + REAL UI

| Metric | Value |
|---|---|
| Python tests | **234 passed / 0 failed** (unit + chaos + end-to-end paper lifecycle + vendor canaries) |
| Next.js cockpit | `next build` clean (7 routes, types valid) · **smoke 12/12** incl. Playwright render (28 chart canvases, 0 console errors) |
| Lint | 0 hard violations (L1/L5) · L2 AST kill-switch proof ✅ · L4 after-cost proof ✅ |
| Modules with code + passing tests | **45 of 45** (M45 = full Next.js cockpit, verified rendering) |
| GitHub pushes | 15 (wave-by-wave, each after a green run) |

## Wave 12 — LIVE-TRADING READINESS (this session)

| Piece | Status |
|---|---|
| **Runtime assembly** (`src/runtime.py`) — ONE `build_runtime(cfg, mode)` constructs the whole graph for paper OR live; live passes the evidence gate first (no bypass) | ✅ tested |
| **SAFE-START** — a fresh live process boots with entries PAUSED; trades only after operator `POST /control/resume_entries` (new gateway endpoint, audited) | ✅ tested |
| **LIVE RAMP (code-enforced)** — first 5 live days capped at 1% position size (vs 5%); `live_days_completed` advances only on CLEAN recon days via the EOD worker | ✅ tested |
| **Tick feed worker** — injected stream → guard → exit sub-bars → snapshot candles, heartbeats (R9) | ✅ tested |
| **EOD worker** — reconcile → daily report → gate advance → alert, automated daily ritual | ✅ tested |
| **Snapshot ⇄ UI contract canary** — gateway snapshot builder field-for-field matched against `cockpit-next/lib/types.ts` (Python and TypeScript cannot drift silently) | ✅ tested |
| **`scripts/go_live_check.py`** — operator pre-flight: config/secrets/tests/lint/gate/audit-chain PASS-FAIL table; demonstrably green on all automated clauses, blocked only on the 3 human items | ✅ demonstrated |
| **deploy/** — systemd units for runtime + cockpit | ✅ |

**Definition of done for LIVE (final):** everything automated is green (244 paper days, streak 244, audit chain intact, 243 tests). Remaining are exactly the three items that MUST be human: (1) SEBI Feb-2025 registration + black-box/RA determination, (2) broker static IP + config flag, (3) the signed ack phrase. Plus deploy-side: VPS provisioning, broker API keys in .env, real-broker-feed verification per R1.

## Wave 11 — REAL-MARKET PAPER REPLAY (prior)

| Evidence | Value |
|---|---|
| Data | **Real Yahoo Finance daily OHLC**, Dec 2025 → Aug 2026: RELIANCE.NS (NSE stock), EURUSD (currency pair), BTC-USD — replayed tick-by-tick with every day's TRUE open/high/low/close preserved |
| The real market | equal-weight buy&hold **−15.78%**; real max drawdowns: BTC **39.6%**, RELIANCE **20.6%**, EURUSD 5.5% (a genuine bear window) |
| **The system (full stack, after real costs)** | **−0.52% return · 3.14% max drawdown** — 33 entries, 71 fills, 8 stop-hits, 24 time-stops |
| Real brokerage charged | ₹981 total — India schedule (brokerage+STT+exchange+stamp+GST) ₹672; MT5 CFD spread+commission for EURUSD/BTC (per-leg costs added to PaperBroker + tested) |
| Gate after replay | 244 real days, streak 244 → live gate now blocks **ONLY on the human items** (SEBI checks, static IP, human ack) — exactly as designed |
| Integrity | final reconciliation CLEAN · audit chain intact |
| Artifacts | `scripts/paper_replay_real.py`, `data/real_replay/results.json`, `equity_vs_market.png` |

## Wave 10 — REAL WEB UI (prior session)

| Piece | Status |
|---|---|
| **Next.js 15 + React 19 + TS cockpit** (`cockpit-next/`) — equity area chart, per-symbol candlesticks (3 legs), VaR gauge, dealer-gamma (GEX) heatmap, live exit-state positions table, kill-switch panel (typed-phrase confirm) + unlock, approvals inbox, worker-health chips, event feed, live-gate progress | ✅ built + rendered |
| **lightweight-charts** integration — 28 chart canvases render, zero console errors (Playwright-verified) | ✅ |
| **Demo mode** — `/api/demo/*` mock market so the UI runs standalone; real mode via `NEXT_PUBLIC_GATEWAY_URL` | ✅ |
| **§12.11 client-safety** — zero order logic client-side; renders state + sends authenticated intents only | ✅ canary |
| **smoke.mjs** — reusable UI test: routes + control contract + browser render, 12/12 | ✅ |
| Bugs found + fixed this session | test-regex case-sensitivity vs CSS uppercase (test bug, not app); broad `pkill` killing sandbox tooling (harness, not app). App itself: 0 runtime errors, 0 NaN over 60-request hammer. |
| Prior vanilla SPA (`cockpit/web/`) | retained — zero-build fallback served by the gateway at `/ui` |

## Wave 9 — PRODUCTION READINESS (this session)

| Piece | Status |
|---|---|
| **Paper broker engine** — fills w/ √-impact slippage + full India cost schedule, resting SL-M triggered by ticks, margin, books | ✅ tested |
| **Paper server** — PaperBroker behind the EXACT verified broker schemas (OpenAlgo + mt5_service) ⇒ paper mode is a base-URL swap, zero code-path changes | ✅ tested |
| **END-TO-END INTEGRATION TEST** — real router → paper fill (slippage+costs) → exit engine attaches real resting stop → trail ratchets INTO the broker → crash triggers broker-side stop → reconciliation CLEAN → daily report + gate advance | ✅ 3 tests |
| **Bugs the integration test caught** — fractional partial quantities (NSE rejects) and stop-hit double-sell — both fixed: lot-floored partials as real orders, stop re-placed for remainder, stop_hit never market-exits again | ✅ fixed + retested |
| **src/app.py runtime** — WorkerSupervisor (restart-on-crash ≤5, heartbeats R9, give-up alert, graceful shutdown) | ✅ tested |
| **LIVE GATE** — live mode raises LiveGateError unless: ≥14 paper days, ≥5 clean-reconciliation streak, SEBI passed, static IP confirmed, exact human ack phrase | ✅ tested (each clause) |
| **Durable persistence** — fsync'd hash-chained JSONL audit (reload-verified, tamper ⇒ error), ledger KV store | ✅ tested |
| **Alerting** — Telegram adapter (fail-safe, never breaks trading path) + fanout | ✅ tested |
| **Paper evidence loop** — daily report + gate_state.json progression (dirty day resets streak) | ✅ tested |
| **CI** — GitHub Actions: full suite + safety lint on every push | ✅ |
| **DEPLOY.md** — phase-gated runbook: infra → paper (evidence) → live (earned) | ✅ |

## Wave log (all pushed to GitHub `main`)

| Wave | Modules | Tests | Push |
|---|---|---|---|
| 0 Scaffold & rails | M18 config, fixtures, lint CI | 5 | `4e94846` |
| 1 Safety core | M1 kill switch, M40 costs, M2 connections, M42 margin | 35 | `4e94846` |
| 2 Execution spine | M3 sizer, M41 order state machine, M36 anomaly guard, M4 router | 48 | `4e94846`/`39837fb` |
| 3 Risk & intelligence | M5 VaR/Kupiec, M6 bands, M7 scenarios, M8 audit chain, M9 greeks, M39 GEX, M10 news, M43 calendar, M11 cache, M12 bridge, M34 regime | 35 | `1662485` |
| 4 Exit engine | M35 adaptive trailing TP/SL + india/mt5 stop adapters | 16 | `55f21b3` |
| 5 Portfolio & compliance | M13 views, M14 3-book, M15 rebalancer, M16 reconciler, M17 SEBI Feb-2025, data pipeline | 18 | `9b6aa94` |
| 6 Learning loop | M38a ledger, M19 attribution, M20 memory, M21 injector, M22 lessons, M23 after-cost backtests, M24 human gate, M25 bootstrap holdout, M26/27/28 | 22 | `7dfba3e` |
| 7 Discovery + ML | M29 loader, M30 regime filter, M31 DSR(probability), M32 walk-forward, M33 miner, M37 labels/fusion/abstain, M38b orchestrator | 19 | `d150a89` |
| 8 Gateway & service | M44 cockpit gateway (RBAC/audit/kill round-trip), mt5_service, M45 scaffold, dashboard, DECISIONS.md | 7 | (this push) |

## Safety properties now PROVEN by tests (not promised)

1. Fail-open configuration is unrepresentable; Redis/margin/VaR-cache loss ⇒ NO orders.
2. Kill switch: dual-flag, chaos-proven, phrase-locked; router's FIRST awaited call (AST).
3. Double-order structurally impossible (UNKNOWN never retryable; new client id always).
4. Size can never exceed caps (500-case property); gap-survival + after-cost gates.
5. Stops: broker-resident from attach, monotonic ratchet (property, long+short), no widen path.
6. Anomaly guard <100ms, zero false triggers on normal replay, no stop-cancel API surface.
7. Rules/models: human approval non-bypassable both directions; holdout consumed 1×/quarter; bootstrap significance; DSR is a probability with multiple-testing penalty verified.
8. Ledger: append-only, features frozen, confidently-wrong model self-demotes (Brier drift).
9. SEBI Feb-2025 gate blocks on every clause incl. the black-box/RA human determination.
10. Cockpit: viewer role provably cannot control; every control audited with actor; kill round-trip tested.

## ⚠️ Deploy-time work (code-complete ≠ live-ready — honest list)

1. **Vendor glue on the VPS** (R1: read source first): real OpenAlgo REST paths, aiomql wiring in mt5_service, TradingAgents/MiroFish/ai-berkshire invocation. Interfaces + mocks are final; the adapters' HTTP shapes need verification against live vendor versions.
2. **M37 training**: FNSPID download + LightGBM training + India dataset build (GDELT + announcements + EasyEventStudies CAR labels) — runs on the VPS/GPU box; all label/feature/fusion/abstain logic here is tested.
3. **M45 SPA**: build per cockpit/README.md (Node/Tauri toolchain, then Tauri shells on Win/mac).
4. **Infra**: Mumbai VPS + Windows Equinix VPS, docker compose up, static IP, broker onboarding, exchange algo registration (Algo IDs), 5 real contract notes for cost-model ≤1% validation.
5. **Gates that require wall-clock**: 2-week paper trading per leg (G2/G4), 5 clean EOD reconciliations (G5), 3-month replay calibrations (G3).
6. **Legal**: black-box/RA determination with a professional; FEMA exposure on offshore MT5 acknowledged in spec §13 — operator's decision.

## Next actions (in order)
1. Provision VPSes, `docker compose up`, wire real broker adapters (verify against vendor source per R1).
2. Backfill bhavcopy/broker history → replay suite → gate G3 calibration.
3. Start paper-trading clocks (G2 execution, G4 exits). 4. Build cockpit SPA. 5. M37 Stage 0 baseline then S1/S2 training. 6. SEBI registration + contract-note validation. 7. Only then: smallest live capital.
