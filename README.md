# Trading OS

**An open-source, Aladdin-class trading system** — India equities (NSE/BSE/MCX via OpenAlgo) + international forex & crypto CFDs (via MT5) — built spec-first by AI agents under hard test gates, hardened by five layers of testing, and proven on real market data.

> ⚠️ **Live trading is gated, not enabled.** All code is complete and tested, but `--mode live` refuses to start until the evidence gate passes — including three items that must be human: SEBI Feb-2025 registration (+ black-box/RA determination), broker static-IP confirmation, and a typed risk-acknowledgement phrase. See [The road to live](#the-road-to-live).

---

## Status board

| | |
|---|---|
| Python tests | **267 passed / 0 failed** (unit · chaos · integration · vendor canaries · adversarial) |
| Next.js cockpit | build clean · smoke **12/12** incl. Playwright render (28 chart canvases, 0 console errors) |
| Money-module coverage | position sizer **100%** · exit engine **98%** · order state machine 95% · router 94% |
| Safety lint | 0 violations (broker-import allowlist · except-pass ban · AST kill-switch-first proof · after-cost-only backtests) |
| Modules | **45/45** code-complete + tested |
| Real-market validation | 244 real days replayed: market **−15.8%** vs system **−0.52%**, max DD **3.14%** — strategy lab best: bear **−0.14%** / COVID **+19.3%** ([details](#proven-on-real-market-data)) |

## What this system is

**Execution** — one order router (the single door to every broker) with fail-closed pre-checks: kill switch first (proven by AST inspection), anomaly pause, VaR cache, price bands, SEBI algo-ID tagging, margin, after-cost sizing. An explicit order state machine makes the double-order bug structurally impossible (timeout ⇒ UNKNOWN ⇒ reconcile against broker truth; retry only from confirmed-absent).

**Protection** — a millisecond anomaly guard (velocity/spread/volume shock detection, no model can veto it), broker-resident stops from the moment of fill, and an adaptive exit engine: chandelier trailing with regime-aware k (loose in trends, tight in shocks), breakeven at +1R, lot-floored partials as real orders, time stops, event/weekend tightening — with a code-enforced *never-widen* invariant.

**Risk** — historical-sim VaR/ES with Kupiec validation, GARCH vol (real `arch` integration), stress scenarios, dealer-gamma (GEX) mapping, hash-chained tamper-evident audit log, EOD reconciliation where a dirty day resets the evidence streak.

**Intelligence** — two-speed news ingestion with dissemination clustering, event-calendar lockouts, sentiment cache with event-driven invalidation, regime detection (vol percentile / ADX / Hurst), and adapters wired to the *verified* APIs of TradingAgents (5-grade rating scale), MiroFish (simulate→report cycle), and the EDT dataset — plus a news-reaction ML core (ATR-normalized labels, fade-vs-drift, abstain, fusion rules where a Tier-0 override cannot even be expressed).

**Learning** — an append-only prediction ledger (features frozen at decision time; a confidently-wrong model self-demotes on Brier drift), five-cause error autopsies where noise learns nothing, non-bypassable human approval on every rule/model change, bootstrap-significance holdout validation, and probability-scale Deflated Sharpe for pattern discovery.

**Operations** — WorkerSupervisor (bounded restart-on-crash, heartbeats), durable fsync'd persistence, Telegram alerting that can never break the trading path, a cockpit gateway with RBAC (viewer provably cannot control), and a full **Next.js cockpit** (candlesticks, equity curve, VaR gauge, GEX heatmap, kill-switch panel with typed confirmation, approvals inbox, live-gate progress).

## Proven on real market data

244 real market days (Dec 2025 → Aug 2026 — a genuine bear window) replayed tick-by-tick through the full stack with every day's true OHLC preserved and **real brokerage charged** (India: brokerage+STT+exchange+stamp+GST; MT5: spread+commission):

| | Real market | This system |
|---|---|---|
| BTC-USD | −31.7% (max DD **39.6%**) | — |
| RELIANCE.NS | −14.6% (max DD 20.6%) | — |
| Equal-weight buy & hold | **−15.78%** | — |
| **Strategy (after costs)** | — | **−0.52% · max DD 3.14%** |

8 broker-side stop-hits and 24 time-stops did the protecting. Reconciliation CLEAN, audit chain intact. (`scripts/paper_replay_real.py`, chart in `data/real_replay/`.) *This validates the machinery, not the deliberately-simple demo entry signal.*

### Strategy lab — Market vs Our System (Aug 2026)

`scripts/research_replay.py` replays candidate entry signals through the **same real stack** (real sizer, router, ExitManager trailing, kill-switch, real cost schedules) on two real windows. Five candidate families were benchmarked (baseline SMA20, TSMOM, Donchian, RSI-2, regime-filtered trend); the best combo is a regime-filtered trend entry — no fresh entries during a SHOCK vol regime, close > SMA20 **and** SMA50, 21-day momentum positive — with tighter trailing (2.0/1.5/1.0/0.5 ATR by regime) and a 50% partial at 1.5R:

| Window | Real market (equal-weight B&H) | Production baseline | Strategy-lab best |
|---|---|---|---|
| 2026 bear (Dec 2025 → Aug 2026, 244 days) | **−15.78%** | −0.64% · max DD 3.14% | **−0.14% · max DD 0.61%** |
| COVID crash (Oct 2019 → Jun 2020, 274 days) | +14.85% | +10.09% · max DD 6.65% | **+19.31% · max DD 7.3% · Sharpe 0.84** |

The trailing engine was stress-tested separately: tight trails alone lifted the COVID window from +9.7% to +15.2% with zero bear-window penalty; loose trails gave profits back (+6.8%). Every run: reconciliation CLEAN, audit chain intact, live gate stayed shut. Two hardening items surfaced (ExitManager crashes on an empty `partials` list; the paper wire stack only supports protective stops for long positions) — tracked as pre-live fixes, not gate items.

## The bug ledger — what five layers of testing each caught

Every layer found bugs the previous layer could not. All fixed, all regression-locked:

| Layer | Bugs caught |
|---|---|
| **Unit tests** (build-time) | VaR quantile convention off-by-one · percentile tie-handling false-SHOCK |
| **Vendor source audit (R1)** | OpenAlgo payload schema mismatch (`direction/qty` vs real `action/quantity/apikey/strategy`) — live orders would have been rejected |
| **Integration test** (full stack vs paper broker) | fractional partial quantities (NSE integer rule) · **stop-hit double-sell** |
| **Multi-day simulation** | crypto lots routed down the India integer-only stop path → CompositeStopAdapter |
| **Adversarial suite** | NaN/inf accepted by the sizer (NaN-quantity orders) · **corrupt negative VaR turned the headroom *reducer* into a 51× size *booster*** · silent NaN costs · corrupt bars moving stops · poison ticks poisoning anomaly baselines |

## The road to live

`--mode live` starts **safe-started** (entries paused until an operator clicks Resume in the cockpit) and ramped (1% max position size for the first 5 live days, code-enforced). It will not start at all until `scripts/go_live_check.py` is green — and that requires:

- ✅ *automated, already satisfied by replay evidence:* ≥14 paper days · ≥5-day clean reconciliation streak · tests · lint · audit chain
- 🧍 *human, by design:* **(1)** SEBI Feb-2025 algo registration + black-box/RA determination with a professional, **(2)** broker static IP whitelisted + config flag, **(3)** `human_ack` set to exactly `I ACCEPT LIVE TRADING RISK`

Deployment: `DEPLOY.md` (VPS runbook) · daily ops: cockpit + EOD worker · legal notes incl. FEMA exposure on offshore MT5: spec §13.

## Layout

```
config/master.yaml        every tunable (zero magic numbers in code — lint-enforced)
src/core/                 kill switch · router · order state machine · sizer · costs · paper broker
src/exits/                adaptive exit engine + india/mt5/composite stop adapters
src/intel/                anomaly guard · regime detector · news · calendar · tick feed · vendor adapters
src/risk/                 VaR worker · bands · scenarios · audit gate · greeks · GEX
src/learning/             ledger · autopsies · holdout · DSR · walk-forward · pattern miner
src/ml/                   news-reaction model core (labels · features · abstain · fusion)
src/ops/                  gateway · snapshot · EOD worker · persistence · alerts · reports
src/runtime.py            build_runtime(cfg, mode) — the paper|live assembly + safe-start + ramp
cockpit-next/             Next.js cockpit (charts + visualizers; smoke.mjs 12/12)
mt5_service/              Windows-VPS MT5 microservice (aiomql impl, verified API)
tests/                    267 tests: unit / integration / vendor canaries / robustness
scripts/                  simulations · real-market replay · go_live_check · lint rules
docs/                     MASTER_BUILD_SPEC_V2.md (the WHAT) · AGENTIC_BUILD_PLAN.md (the HOW) · DECISIONS.md
```

## Run it

```bash
pip install -r requirements.txt fastapi arch
python -m pytest tests/ -q && python scripts/lint_rules.py     # 267 green expected
python scripts/paper_simulation.py                             # 6-day drill session
python scripts/paper_replay_real.py                            # real-market replay
cd cockpit-next && npm install && npm run build && npm start   # cockpit at :3000 (demo data)
python scripts/go_live_check.py                                # your pre-flight
```

## How it was built

Spec-first (`docs/MASTER_BUILD_SPEC_V2.md`) by AI agents under the rules in `docs/AGENTIC_BUILD_PLAN.md`: tests written from acceptance criteria before implementation on every money module, vendor APIs read from source before wiring (R1 — and it caught a real schema mismatch), fail-closed everywhere by default, and safety rules that override every other instruction — including the prompts of the agents doing the building.

## Compliance notice

India algo trading is governed by SEBI circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013** (Feb 2025). MODULE 17 hard-blocks deployment until registration, per-order Algo IDs, static IP, OPS limits, and the black-box/Research-Analyst determination are recorded. Offshore MT5 CFD trading carries **FEMA exposure for Indian residents** — documented, not mitigated, in spec §13. Nothing in this repository is investment advice.
