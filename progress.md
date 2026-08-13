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

## Wave 14 — COVID-CRASH STRESS CERTIFICATION (final)

| Evidence | Value |
|---|---|
| Data | Real Yahoo Finance daily OHLC, **Oct 2019 → Jun 2020** — through Black Thursday (BTC **−37.17% in one day**, 12 Mar 2020) |
| The real market | max drawdowns: BTC **51.86%** · RELIANCE **45.09%** · EURUSD 6.5%; equal-weight buy&hold +14.85% (after riding ~50% drawdowns) |
| **The system (full stack, real costs, GAP-AWARE stop fills)** | **+10.09% return · MAX DRAWDOWN 6.65%** — ⅔ of buy&hold's return at **~1/7th of its drawdown** |
| Execution detail | 46 entries, 110 fills, **17 stop-hits filled at gapped prices (not triggers)**, 27 time-stops, ₹4,365 real costs (crypto spreads dominate, as they should) |
| Fidelity upgrade shipped | Paper broker stop fills are now **gap-aware**: price gapping through a trigger fills at the gapped price — real-market stop semantics (unit-tested) |
| Integrity | 274 days replayed · reconciliation CLEAN · audit chain intact · live gate blocks only on human items |
| Artifacts | `data/real_covid/` (real crash data) · `data/covid_replay/results.json` · `covid_certification.png` · replay script now parametric (`paper_replay_real.py <data_dir> <out_dir>`) |

## Wave 13 — ADVERSARIAL HARDENING (prior)

**New robustness suite** (`tests/robustness/`): NaN/inf injection, poison ticks, corrupt bars, 800-case sizer fuzz, 300-sequence order-state-machine invariant fuzz, money-conservation invariant, concurrent kill/router load, math edge locks.

**5 REAL BUGS found and fixed (each now regression-locked):**
1. Position sizer accepted NaN/inf → would have sent a NaN-quantity order to a broker → hard non-finite guard, named zero-reason
2. **VaR-headroom multiplier could BOOST size 51× on a negative/corrupt VaR value** → clamped to [0,1] + hard cap re-asserted after every multiplier (defense-in-depth)
3. Cost model silently computed NaN charges → loud ValueError
4. Exit engine processed corrupt bars (NaN / high<low) → skipped+logged, stops never move on garbage
5. Tick feed forwarded poison ticks (0/negative/NaN) into guard baselines → counted+skipped, spine survives

**Verified after fixes:** 267 tests green · both simulations reproduce bit-for-bit identical results (pure hardening, zero behavior change) · money-module coverage: sizer 100%, exit engine 98%, OSM 95%, router 94%.

## Wave 12 — LIVE-TRADING READINESS (prior)

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

---

## 2026-08-10 — Cockpit v2: sessions, pages, brokers (branch feat/cockpit-v2)

Operator video review found the paper runtime entering india-leg trades at
21:00 IST (NSE closed) and a single-page UI with no broker/portfolio/PnL
surfaces. Root cause: config trading_hours had ZERO consumers and the
router's session_open_fn hook (M21) was never wired.

- **M58 market_clock.py** — per-leg session calendar (NSE 09:15–15:30 IST
  Mon–Fri minus 16 config-listed 2026 holidays; FX 24/5 UTC week; crypto
  24/7). Wired as build_runtime's DEFAULT session_open_fn (explicit arg still
  wins; replay scripts assemble their own stack — certified numbers proven
  IDENTICAL). Entries only; exits/stops/kill never gated.
- **M59 gateway v2** — /clock /portfolio /history (filterable screener)
  /brokers (env-var booleans only) + operator-audited /brokers/test,
  /brokers/save. Save path refuses gate keys at TWO layers (gateway +
  provider allowlist): the UI structurally cannot weaken the live gate.
- **M60 broker_settings.py** — OpenAlgo provider switch (dhan|shoonya|
  fyers|zerodha) + MT5 exec URL to a gitignored overlay
  (config/brokers_local.yaml). Credentials stay env-vars; values never
  transit the gateway.
- **M61 cockpit SPA v2** — CRM shell, 7 hash-routed pages (dashboard,
  portfolio, pnl, history, markets, ops, settings), session-aware demo feed
  (closed markets freeze + badge CLOSED), go-live checklist, runbook.
- Tests: 440 passed / 1 skipped (+33); UI harness 45 checks (+22, incl.
  independent NSE-session recomputation); gateway probe 30/30;
  research_replay tsmom/india JSON-identical to main; go_live_check still
  exits 1 with exactly the 3 human items.

---

## 2026-08-10 (later) — Cockpit v2.1: trade controls, research lab, runnable paper server

Operator feedback: kill switch too slow under stress (typed phrase), no
per-trade close, no manual ticket, no in-product backtesting — and the
deeper find: `--mode paper` exits immediately; the repo never had a
runnable paper process (why the operator lived in the demo app).

- **ExitManager.manual_exit** — public single-position close via the real
  exit path (cancel resting stop -> market-out; fail-loud on unknowns).
- **M62 quote_feed.py** — credential-free feed replaying bundled REAL OHLC
  tick-by-tick inside true bar ranges; session-aware (closed legs freeze).
- **M63 research_lab.py** — backtests from the cockpit on the certified
  research_replay harness; allowlisted strategy×dataset, single-flight.
- **M64 gateway** — /candles, /control/close_position (per-symbol typed
  confirm), /control/order (full router path), /research/run|runs; ALL
  injected providers now sync/async-tolerant (_maybe_await — the Aug-6
  seam class struck again, caught by the new assembly test).
- **scripts/run_paper.py** — THE missing product entrypoint: one command
  assembles feed + runtime + gateway + cockpit at /ui. Smoke-verified
  live: order placed, filled, closed, audited via HTTP.
- **Kill UX** — modal + arm-delay (two deliberate clicks, no typing);
  friction stays on the risk-increasing side (unlock phrase untouched).
  Gateway API contract unchanged.
- Cockpit: per-row CLOSE with armed confirm, manual trade ticket
  (mandatory stop, router rejections shown verbatim), Research page.
- Tests: 455 passed / 1 skipped (+15); UI harness 62 checks (+17);
  replay JSON-identical; go_live_check still exits 1 with 3 human items.

---

## 2026-08-11 — MODULE 65: auto-trading sleeves (branch feat/cockpit-v2)

The signal engine now trades the paper feed autonomously — through the
identical router door as manual tickets (kill switch, anomaly, session
clock, guards, sizer, margin). The engine adds ZERO order logic; it only
decides WHEN to knock.

- **src/ops/strategy_engine.py** — on every completed feed bar, enabled
  sleeves evaluate their registered signal on the same real daily series
  the backtests certified, with the replay's own real_regime/atr14
  reproduced verbatim. One position per symbol (ExitManager owns it until
  exit); exact sleeve attribution for the P&L ledger; a throwing sleeve is
  disabled + reported, never retried silently.
- **Safe boot:** every sleeve starts DISABLED. Enabling = operator act via
  POST /strategies/toggle with typed "ENABLE <sleeve>" (audited); disable
  is the airbag — instant, no phrase.
- **quote_feed** — completed_count + bars_window (wrapping real-bar
  history in the exact signal-contract shape).
- **Cockpit Strategies page** — sleeve table (status/entries/rejections/
  open/closed/win-rate/realized-R/last-signal), armed enable confirm,
  instant disable, live summary cards.
- **Proof:** mirror test — assembly boots, tsmom enabled via endpoint, one
  real bar closes, engine does EXACTLY what SIGNALS["tsmom"] says on that
  data. Live smoke: 3 sleeves enabled over HTTP -> 6 auto-entries, 2 open
  crypto positions managed by ExitManager, india entries session-refused
  at 21:00 IST, full entry->exit->ledger lifecycle observed.
- Tests: 468 passed / 1 skipped (+13); UI harness 73 checks (+11); replay
  JSON-identical; all sleeves-off boot verified.

---

## 2026-08-11 (night) — retire the cockpit-next demo app

The Next.js demo cockpit is gone. It existed as a design-review variant,
but it was what the operator ended up running as "the system" — a fake
random-walk feed with no session awareness (the source of the phantom
night-trading report). The real cockpit is the zero-build SPA served by
the gateway (/ui via scripts/run_paper.py); its ?demo=1 mode remains the
harness/design path and is honestly labeled.

The one load-bearing coupling was ported, not dropped: the snapshot ⇄ UI
field canary read cockpit-next/lib/types.ts. The contract now lives in
cockpit/web/state_contract.json owned by the real SPA, and the canary got
STRONGER — two-sided: SnapshotBuilder must emit every contract field AND
app.js must consume everything not marked ui_optional. Fixing the
consumption side surfaced a real gap: the mode badge was hardcoded PAPER;
it now follows state.mode (a live runtime shows a red LIVE badge).

README run instructions now point at run_paper.py instead of npm.

---

## 2026-08-12 — evaluated FinceptTerminal + WCC "top trader" strategies (no code ingested)

**FinceptTerminal (Fincept-Corporation, 22.6k stars) — evaluated, NOT adopted.**
Cloned to vendor/ (temporarily) and dissected. Verdict mirrors the OpenBB
decision, for stronger reasons:
1. AGPL-3.0 (network copyleft — the exact license this repo rejected for
   OpenBB). Code copying is off the table.
2. Its 407 "strategies" are QuantConnect LEAN's example/regression
   algorithms RELABELED with Fincept MIT+copyright headers inside an AGPL
   repo — three-layer license mess, and zero proprietary alpha.
3. Its "37 AI agents" are LLM persona prompts (Buffett/Graham/…/a
   RenTech cosplay). Opinion intel, not codifiable strategy; we already
   vendor the better originals (TradingAgents, ai-berkshire) with adapters.
4. Its india broker layer IMPORTS OPENALGO'S ADAPTERS — the same hub this
   repo already standardized on; crypto is a ccxt wrapper; "quant lab" is
   Microsoft Qlib wrappers. Nothing to gain that we don't have.
Useful as: independent confirmation that OpenAlgo-as-hub is the right india
broker architecture. Clone deleted; not added to clone_vendors.sh.

**WCC 2026 leaderboard "top traders" — researched, nothing ingestible.**
The viral board (Rosputnia 428.8%→604% YTD, Pham, Pomer, Cianni, Perdices)
is an interim, unaudited, best-of-multiple-accounts ranking on $10k
minimum accounts with no drawdown reported; the entry agreement lets
competitors run several accounts and rank only the best — contractual
survivorship. Of the five, only Rosputnia has a public identity, and her
"method" is indicator names without definitions. Verdict: zero codifiable
rules from the 2026 leaders. "Comment TRADE" reels reposting the board are
lead-capture funnels.

**What IS codifiable from WCC history (future research-branch candidates,
same workflow as the martin_luke/18ma evaluation):**
- Larry Williams volatility breakout: entry at Open ± k·(prev range),
  k ≈ 0.75–1.1 published; parameters must survive out-of-sample.
- Williams "Oops!": open gaps below prior low → buy stop at prior low
  (mirror short). Radge's replication: UNfiltered variant survived 17y
  out-of-sample; the book's day-filters did not — validate base rules only.
- Williams Greatest Swing Value: fully specified (BuySwing/SellSwing SZMA).
- Williams "bailout" exit: first profitable open.
- Unger's meta-layer (4× champion, published as process): many small
  uncorrelated systems, monthly performance-ranked rotation, Monte Carlo
  culling — portfolio architecture, not signals.
- Vince/optimal-f sizing lesson: define max loss per trade first; size
  well below optimal f. (Our half-Kelly cap + 1% risk already embodies it.)

---

## 2026-08-12 — MODULE 66: risk optimizer (the honest "advanced math for profit")

Operator asked to "use all the advanced math to maximize profits." The
mathematics' actual answer: profit maximization IS geometric-growth
maximization, g(f) = E[log(1+f·R)] peaks at the Kelly fraction and then
FALLS — risking past f* produces less growth with more violence. Built the
module that computes this from the system's own realized trades.

- **src/ops/risk_optimizer.py** — stdlib-only, deterministic: empirical
  Kelly via golden-section on the actual R distribution (no binomial
  approximation), Wilson CI on win rate, growth curve to 2×Kelly, seeded
  bootstrap Monte Carlo max-drawdown distribution + ruin probability.
  Descriptive analytics ONLY — changes no trading behavior. Refuses sizing
  claims under 10 trades; all-win samples return "Kelly undefined", never
  a lever-up licence; negative expectancy returns f*=0.
- **research_replay.py** — additive `trades_r` field (per-trade Rs behind
  the aggregates), 809a6c6-style: certified fields proven field-for-field
  IDENTICAL on tsmom/india.
- **Gateway /riskmath** (viewer+) fed by a lab run's trades_r or the live
  ledger; cockpit Research page renders the report.
- **§12.11 canary refined**: forbidden markers now target client-side
  COMPUTATION (Math.log/pow, kelly_fraction() etc.), since the SPA now
  legitimately DISPLAYS the server-computed kelly fields.

Real numbers (tsmom, india_6m, 38 closed trades): win 57.9% (CI 42–72%),
avg +0.20R → empirical Kelly f*=25.3%/trade. Configured risk 1% = 4% of
Kelly. Growth/trade: 0.0020 @1% · 0.0183 @½K · 0.0240 @K · **−0.0012 @2×K**
(the cliff). Bootstrap P95 max drawdown: 7.0% @1% vs **90.8% @Kelly with
85.7% probability of a 50% drawdown inside 50 trades**. The 1% cap +
half-Kelly ceiling aren't timidity — they're the right side of the curve
given 38-trade estimation error.

Tests: 479 passed / 1 skipped (+11); UI harness 76 (+3).

---

## 2026-08-12 — data-source verification: Yahoo chart API is the unified feed (measured live)

Operator asked for a TradingView-like single source for india + forex.
Verified empirically from a live NSE session (Wed 10:44 IST), not from
documentation:

- **query1.finance.yahoo.com/v8/finance/chart/** — the SAME endpoint
  scripts/fetch_market_data.py already uses for the bundled datasets —
  serves NSE equities (RELIANCE.NS 1,314.10), NIFTY (^NSEI), forex
  (EURUSD=X, USDINR=X), crypto (BTC-USD) and COMEX (GC=F) from one URL
  shape, no credentials, ~50ms.
- **Measured quote age during the live session: NSE 1–2s** (effectively
  real-time, not the assumed 15-min delay), USDINR 4s, crypto 4s, EUR/GBP
  ~42s, COMEX ~10min.
- Granularity/history (measured): 1m×7d, 5m×60d, 1h×730d; daily history
  deep (RELIANCE first-trade metadata 1996; chunked period1/period2
  fetching for full depth).
- Caveats (honest): unofficial API — no SLA, no published rate limits
  (community consensus: keep well under a few hundred req/hr/IP), ToS-grey
  for anything beyond personal use. Any live wiring must be fail-soft to
  the replay feed.
- Alternatives checked: Binance public API geo-blocked from the test DC
  (fine from India); nseindia.com/api bot-blocked (404) from DCs;
  TradingView has NO public data API (its chart library requires bringing
  your own feed; scraping libs violate ToS). Paid unified (TwelveData Pro
  etc.) only worth it at production scale — at which point the broker
  websockets (OpenAlgo + MT5) are better AND contractual.

Conclusion: Yahoo chart API = the credential-free unified source for the
paper cockpit's live feed (same pedigree as the bundled data); broker
feeds remain the production path. Feed interface (M62) already designed
for the swap.

---

## 2026-08-12 — feed doctrine + MT5 bridge market data (real-time for live)

Operator (correctly): delayed data is acceptable for paper, never for live.
Doctrine recorded: **trade on the feed you execute on.**

| mode | india leg | mt5 legs (forex/crypto CFD) |
|---|---|---|
| live | OpenAlgo broker websocket | MT5 terminal's own feed |
| paper | Yahoo (1-2s, verified) or replay | MT5 DEMO feed (real-time, free) or Yahoo |
| research | Yahoo / bundled | Yahoo / bundled |

MT5 is the CORRECT forex source for the mt5 legs: symbol_info_tick /
copy_rates_from_pos give the broker's real bid/ask INCLUDING their spread
(Yahoo's =X symbols are blended mids — charts yes, execution no), real-time
with any account including demo. Latency risk is bounded by design: the
strategies are daily-bar systems and protective stops are BROKER-RESIDENT
(spec §12) — a dead feed can cost entry slippage, never an unprotected book.

Shipped: mt5_service now serves GET /tick/{symbol} and
GET /candles/{symbol}?timeframe=&count= — same X-MT5-Auth posture as exec
endpoints, 503 fail-loud when the terminal is down, count capped at 1000.
aiomql impl verified against vendor source (core/meta_trader.py) per R1.
Tests: 481 passed / 1 skipped (+2).

---

## 2026-08-12 — MODULE 67: live quote feeds (FEED=yahoo|mt5|replay)

The paper cockpit now runs on REAL live prices. Two providers behind the
exact M62 interface (run_paper and the strategy engine cannot tell the
difference), selected by the FEED env var:

- **YahooQuoteFeed** — the ledger-verified chart endpoint. Budgeted
  round-robin (one HTTP call per min_gap_s, default 8s), OPEN symbols
  only; daily history (250 bars/symbol) fetched at boot so strategies
  have their full lookback; live ticks aggregate into 5m candles.
- **Mt5QuoteFeed** — the bridge's /tick and /candles; mids the broker's
  REAL bid/ask; X-MT5-Auth on every call; D1 bars from the terminal.
- **FeedMux** — per-leg doctrine composition: FEED=mt5 routes mt5 legs to
  the bridge and india to Yahoo.
- **Fail-soft contract**: provider errors strike; max_errors degrades the
  feed to its ReplayQuoteFeed fallback (status says DEGRADED_replay), and
  a healed provider un-degrades via periodic probe. A dead vendor can
  never freeze the cockpit.
- Session-aware at the feed: closed legs are never even POLLED (budget +
  correctness in one move).

Tests: recorded-fixture only (real Yahoo payloads captured live
2026-08-12 in-session; MT5 fixtures matching the shipped bridge schema)
via httpx.MockTransport — no network in tests. 488 passed / 1 skipped
(+7). Live smoke during NSE session: cockpit RELIANCE 1311.6 vs 1311.8
direct query seconds apart; TCS 2327.9; HDFCBANK 722.65; 250 daily bars
per symbol; /candles aggregating.

---

## 2026-08-12 — WCC-lineage research: vbo / oops / gsv (branch research/wcc-williams-aug2026)

Implemented Larry Williams' three PUBLISHED rules as contract-compatible
daily-bar adaptations (close-confirmation entries at next open; certified
ExitManager exits, NOT the bailout — the question tested: do these ENTRY
rules add edge inside OUR system). Unfiltered base rules only (Radge).
Promotion bar PRE-REGISTERED before results: CLEAN everywhere, ≥10 pooled
trades, positive primary-market return sum, empirical Kelly > 0, no
stress blowup.

18-run evidence grid (india/forex/crypto 6m + covid/gfc/flash), all 18
reconciled CLEAN, tsmom certified results re-proven IDENTICAL with the
new registry entries present:

| candidate | pooled n | win | avgR | Kelly f* | primary ret Σ | stress (covid/gfc/flash) |
|---|---|---|---|---|---|---|
| vbo  | 156 | 44.9% | 0.100 | 12.5% | +0.86% | −0.85 / −0.90 / +0.28 |
| oops |  67 | 43.3% | 0.173 | 20.3% | +1.61% | −0.64 / −0.18 / +0.53 |
| gsv  | 211 | 38.4% | 0.018 |  2.0% | +2.14% | −1.26 / −2.97 / −0.31 |

All three cleared the pre-registered bar → registered (sleeves boot
DISABLED; registry admission ≠ activation). Honest recommendation on top:
- **oops — the find.** India +1.59% BEATS tsmom's +1.15% on the same
  window; best pooled avgR (0.38 india); mean-reversion style diversifies
  a trend-heavy book (Unger's meta-lesson). Candidate to enable.
- **vbo — marginal.** Positive but thin; negative on both long stress
  sets; zero forex trades (k=1.0 never triggers on EURUSD dailies). Keep
  OFF pending multi-year walk-forward.
- **gsv — passed the letter of the bar, fails the spirit.** avgR 0.018 is
  statistically indistinguishable from zero (38.4% win over 211 trades);
  crypto carries the sum; worst stress profile in the grid (gfc −2.97%);
  configured 1% risk is already HALF its Kelly. Keep OFF; re-examine only
  after data/market_*_hist lands and walk_forward_validate can run.

---

## 2026-08-13 — MODULE 68: india real-time via the OpenAlgo hub (FEED=openalgo|live)

The "MT5 for India", researched from official docs + vendored source (R1),
then built:

- **Research verdict (docs.openalgo.in + broker docs, cited in-thread):**
  OpenAlgo serves REST market data (/api/v1/quotes, /multiquotes,
  /history, 50/s limit) and a ws proxy (:8765, LTP/Quote/Depth) over 36
  broker plugins. For the DATA feed: **Angel One = free real-time NSE ws
  + the only documented headless re-login**; Dhan (our config default)
  charges ₹499/mo for data; Zerodha ₹500/mo; Fyers free but grey-zone
  unattended auth; Upstox free w/ daily browser OAuth. Daily breakage is
  structural: OpenAlgo session expiry 03:00 IST + broker token expiry →
  DEPLOY.md morning runbook added.
- **OpenAlgoQuoteFeed** (src/ops/live_feeds.py): BATCHED — one
  multiquotes call covers the whole india universe per poll (~1.5s gap vs
  the 50/s hub limit); daily bars from /history interval "D" with live
  bar roll-on; session-aware (no polls when NSE closed); fail-soft chain
  openalgo → yahoo → replay with auto-recovery probing. Payload shapes
  from the vendored docs, fixture-tested (no live network).
- **run_paper**: FEED=openalgo (india on hub, rest on Yahoo) and
  FEED=live (india on hub, mt5 legs on the bridge) — the full doctrine.
- **Settings page**: india card now shows the live data-feed layer
  (openalgo_hub / yahoo_live / DEGRADED_replay); provider list gains
  angel + upstox with fee notes.
- Base-feed fix: fallback delegation is now awaitable-tolerant so
  three-deep chains (live→live→replay) work.
- Tests: 506 passed / 1 skipped (+4 openalgo fixtures incl. the full
  degrade-to-yahoo chain); UI harness 76.

---

## 2026-08-13 — MODULE 69: funded-account (prop-firm) mode + challenge math

Operator will trade forex via a funded account (FTMO-style evaluation).
Built the rule set as a guard layer and the "optimize to pass" math as
real math.

- **src/ops/prop_rules.py — PropGuard**: firm rules config-driven
  (prop_firm: block, DISABLED by default): daily-loss anchored to
  day-start equity at the FIRM's server reset hour (not IST), equity
  marks include floating, static or trailing max-DD, profit target, min
  trading days. THE invariant: our soft line (60% of each firm budget)
  refuses entries long before the firm's hard line; breach latches.
  Wired as a 4th optional guard-stack layer (additive, default None —
  legacy behavior proven intact).
- **challenge_monte_carlo / optimal_challenge_risk**: bootstrap the
  system's own R distribution through the firm's rules; sweep risk/trade
  for the pass-probability peak. A challenge is an asymmetric one-shot
  bet — its optimal aggression is a number.
- **Real numbers** (pooled mt5-leg book: tsmom+oops+vbo on forex+crypto,
  118 trades, 50% win, avgR +0.076; FTMO-style 10%/5%/10% phase-1):

  | risk/trade | P(pass) | P(bust) | median days |
  |---|---|---|---|
  | 0.5% | 0.3% | 0% | 56 (times out) |
  | 1.0% | 21.8% | 0% | 45 |
  | 2.0% | 61.8% | 5.9% | 29 |
  | **3.0%** | **72.6%** | 15.7% | 21 ← optimal |
  | 6.0% | 49.6% | 50.4% | 6 (coin flip) |

  Phase-2 (+5%) at 3%: 84.1% -> **P(both phases) = 61.1%** ≈ 1.6
  challenge fees per funded account. Challenge-optimal risk (3%) is 3x
  the wealth-optimal configured risk (1%) — correct per theory: the
  challenge downside is capped at the fee. After funding, drop back.
- **Gateway GET /prop** + run_paper wiring (PROP=1 or config): equity
  marked every loop, traded days counted on exits, challenge math served
  from the live mt5-leg ledger.
- Operational notes for the operator: (1) daily-bar sleeves HOLD
  overnight/weekends -> needs the firm's SWING account variant (regular
  accounts often ban weekend holds); (2) most firms allow EAs but ban
  latency arb/copy-trading — read the specific firm's ToS; (3) Oracle
  free-tier ARM (4 OCPU/24GB) is ample for the engine, but the MT5
  TERMINAL is Windows software — run it under Wine on the same box or a
  tiny Windows VPS; the engine talks to it over the bridge either way.
- Tests: 520 passed / 1 skipped (+14: firm-day rolls, soft-before-hard,
  breach latching, trailing vs static DD, MC determinism + interior
  peak, negative-edge-cannot-pass, guard-stack integration).

---

## 2026-08-13 — Funded Account cockpit page (M69 UI)

The prop-firm mode gets its own page (#/funded, nav "Funded"): exam state
(IN PROGRESS / PASSED / FAILED with breach reason), live gauges for the
daily-loss and max-DD budgets (used% vs firm cap, red at soft-stop),
profit-target progress bar with the pass equity, traded-days counter vs
minimum, the firm rulebook (incl. the 21:00 UTC firm-day reset and our
60% soft line), and the challenge-math table with the pass-optimal risk
row starred. Demo fixture carries the REAL M69 Monte Carlo numbers
(3%/trade -> 72.6% phase-1). UI harness: 85 checks (+9).
