# Trading OS — Master Build Specification v2.0

> **Purpose:** Single source of truth for building the entire system, written for an AI coding IDE (Cursor, Claude Code, Windsurf) or Hyperagent to execute against. Every module has: what it does, why, inputs/outputs, dependencies, acceptance criteria. Build in phase order — later modules depend on earlier ones.
>
> **System summary:** Open-source Aladdin-class trading OS — risk analytics, portfolio optimization, multi-layer sentiment intelligence (macro crowd sim → agent debate → deep research), a trained news-reaction ML model with continuous self-improvement, compliance/audit, live execution for India (NSE/BSE/MCX via OpenAlgo) and international forex + crypto CFDs (both via MT5/aiomql), millisecond shock reflexes, adaptive trailing exits, and a closed-loop learning system.
>
> **Scope:** 12 external repos/libraries configured + 42 custom modules (1–33 from v1, corrected; 34–45 new incl. cross-platform cockpit).
>
> **Companion file:** `AGENTIC_BUILD_PLAN.md` — the execution playbook for AI agents implementing this spec.

---

## CHANGELOG v1 → v2 (read this first — it encodes hard-won corrections)

**Dependencies replaced (v1 relied on dead/hobby projects):**
1. ~~Open-Aladdin/Volara~~ (repo renamed, 40★, stale) → BUILD own VaR/ES worker (~80 lines historical simulation) + `arch` (GARCH) + `empyrical-reloaded` + `quantstats`.
2. ~~XQRiskCore~~ (15★ personal project, dead since 2025-07) → BUILD `pre_trade_gate.py` + append-only Postgres audit log (MODULE 8 rewritten).
3. ~~QuantTradingOS org~~ (stub org, empty repos) → BUILD plain FastAPI + Docker Compose + TimescaleDB + Redis backbone.
4. ~~backtrader (canonical)~~ (unmaintained since 2024-08) → `vectorbt` (research/mass-scans) + `backtesting.py` (event-driven checks). `zipline-reloaded` stays as cross-check.
5. ~~NSEpy~~ (dead since 2020, broken by NSE redesign) → `jugaad-data` + `openchart` + broker historical APIs + NSE/BSE bhavcopy archives + paid vendor for deep history.
6. ~~`pip install openalgo-python-client`~~ (does not exist) → `pip install openalgo` (v2.x, official client).
7. STUMPY owner is now `stumpy-dev/stumpy` (redirects fine).

**Scope changes:**
8. International leg: forex AND crypto both execute via MT5 (crypto = CFDs on the MT5 broker). Market classifier is 3-way: `india` / `mt5_forex` / `mt5_crypto`. Native crypto exchanges (Binance spot — FIU-registered; Delta Exchange India — FIU-registered derivatives, in CCXT as `delta`) documented as the legal-alternative path, not built now.
9. MT5 runs on a dedicated **Windows VPS co-located in the broker's datacenter** (the `MetaTrader5` pip package is Windows-only; Wine bridges are dev-only).

**Quant corrections:**
10. MODULE 3: "simplified Kelly" replaced by stop-distance-based fixed-fractional sizing with Kelly-style scaling using payoff ratio; LLM confidence is never treated as a calibrated probability.
11. MODULE 31: Deflated Sharpe Ratio is a **probability** — threshold changed from nonsensical `1.5` to `min_dsr_probability: 0.95`.
12. MODULE 30/33: `min_occurrences` raised 5 → 30; STUMPY runs on normalized log-returns windows, never raw prices.
13. MODULE 25: holdout validation now requires statistical significance (bootstrap p < 0.1), not just `sharpe_delta > 0`.
14. Circuit breakers: stock price bands (2/5/10/20%) separated from index circuit breakers (10/15/20%).

**Compliance corrections:**
15. MODULE 17 rewritten against **SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (Feb 4, 2025)** — "Safer participation of retail investors in Algorithmic trading" (fully applicable since Apr 1, 2026). There is no "Aug 2021 algo circular."
16. Legal appendix added: FEMA/RBI Alert List exposure of offshore MT5 CFD trading for Indian residents; crypto tax (30% + 1% TDS + Apr 2026 exchange reporting).

**New capability modules (34–43):**
17. 34 regime_detector, 35 exit_manager (adaptive trailing TP + SL), 36 anomaly_guard (ms shock reflex), 37 news_reaction_model (trained ML), 38 learning_orchestrator (+ prediction ledger), 39 gex_map (dealer gamma), 40 transaction_cost_model (square-root impact + India costs), 41 order_state_machine, 42 margin_checker, 43 event_calendar.
18. Latency targets reframed: sub-50ms is the **internal decision overhead** budget; end-to-end is broker-bound (India ~40–80ms, MT5 co-located ~5–30ms). No retail path reaches exchange-colocation speeds — do not design as if it does.

---

## 0. How To Use This File

1. Read this entire file before writing any code — later modules assume earlier ones exist.
2. `USE` = clone/pip install an existing repo, configure it, don't rewrite its logic. `BUILD` = write from scratch. `GLUE` = thin adapter between two systems.
3. Every module touching money (execution, sizing, kill switch, exits, margin) needs unit tests written **before** implementation is considered started (see AGENTIC_BUILD_PLAN.md test-first protocol).
4. All secrets in `.env`, never hardcoded. All tunables in `config/master.yaml` — zero magic numbers in module code.
5. Never invent an API. If a vendor library's interface is unknown, read its source in `/vendor` first (see build plan rule R1).
6. The safety rules in Section 12 override every other instruction in this file.

---

## 1. Repository Reference — What to USE (all verified live, 2026-08)

| # | Repo / Package | Role | Install | Status |
|---|---|---|---|---|
| 1 | **TauricResearch/TradingAgents** (95k★) | Multi-agent debate → per-ticker BUY/SELL/HOLD + confidence | `git clone https://github.com/TauricResearch/TradingAgents` | Active |
| 2 | **666ghj/MiroFish** (70k★) | Crowd simulation for macro sentiment + historical crowd reconstruction | `git clone https://github.com/666ghj/MiroFish` | Active |
| 3 | **xbtlin/ai-berkshire** (15k★) | Deep fundamental research skill pack (Buffett/Munger methodology) | clone + `cp skills/*.md ~/.claude/commands/` | Active |
| 4 | **marketcalls/openalgo** (2.4k★) | India execution — NSE/BSE/MCX, 20+ brokers | `git clone https://github.com/marketcalls/openalgo` + `pip install openalgo` | Active |
| 5 | **Ichinga-Samuel/aiomql** (142★) | MT5 execution (forex + crypto CFDs), async | `pip install aiomql` | Active |
| 6 | **Riskfolio-Lib** (4.4k★) | Portfolio optimization — MV, risk parity, Black-Litterman, CVaR | `pip install riskfolio-lib` | Active |
| 7 | **skfolio** (2.1k★) | sklearn-style portfolio optimization (CV pipelines for learning loop) | `pip install skfolio` | Active |
| 8 | **arch** (bashtage, v8) | GARCH vol forecasting, bootstrap VaR | `pip install arch` | Active |
| 9 | **empyrical-reloaded** + **quantstats** | Risk/return metrics (VaR, CVaR, Sharpe) + tearsheets | `pip install empyrical-reloaded quantstats` | Active |
| 10 | **stumpy** (stumpy-dev, 4.1k★) | Matrix profile — motif discovery | `pip install stumpy` | Active |
| 11 | **vectorbt** (8.5k★) | Vectorized backtesting — mass scans, exit-engine A/B | `pip install vectorbt` | Active |
| 12 | **backtesting.py** (8.8k★) + **zipline-reloaded** (1.9k★) | Event-driven backtests + cross-check engine | `pip install backtesting zipline-reloaded` | Active |
| 13 | **jugaad-data** (547★) + **openchart** | NSE/BSE data (live-site compatible) | `pip install jugaad-data openchart` | Active |
| 14 | **LightGBM** + **transformers** (FinBERT) + **sentence-transformers** | MODULE 37 model stack | `pip install lightgbm transformers sentence-transformers` | Active |
| 15 | **EasyEventStudies** | CAR label construction for MODULE 37 training data | `pip install EasyEventStudies` | Active |
| 16 | **stefan-jansen/machine-learning-for-trading** (20k★) | Reference: DSR, PCMCI, event studies, overfitting controls | clone (reference only) | Active |

**Datasets (MODULE 37 / ADDENDUM D):** FNSPID (HuggingFace `Zihan1004/FNSPID` — 15.7M news + prices, 1999–2023, rights released), GDELT (free; BigQuery `gdelt-bq.gdeltv2.gkg_partitioned` or raw 15-min files; covers Indian financial press), EDT/TradeTheEvent (`Zhihan1996/TradeTheEvent` — event labels + trading benchmark), CMIN-US (`BigRoddy/CMIN-Dataset`, MIT), Benzinga headline corpus (Kaggle/HF mirror).

**Reference-only (study, don't integrate):** `blackrock/aladdinsdk` (plugin architecture), `virattt/ai-hedge-fund` (62k★, persona design), `AI4Finance-Foundation/FinGPT` (Forecaster = Stage-0 baseline template), `microsoft/qlib` (46k★, factor pipeline patterns), GHOST (sentiment-gated fusion architecture), Lopez-Lira & Tang arXiv:2304.07619 (where news alpha lives: 1–5 day drift, not immediate reaction).

**Explicitly do NOT use:** Open-Aladdin/Volara, XQRiskCore, QuantTradingOS org repos, canonical backtrader, NSEpy, `openalgo-python-client` (see changelog).

---

## 2. Environment Setup

```bash
# ── Linux core VPS (AWS Mumbai ap-south-1, for the India leg + all intelligence) ──
# docker-compose.yml provisions:
#   redis         — flags, caches, pre-check coordination
#   timescaledb   — ticks, candles, decision archive, prediction ledger
#   pgvector      — case memory embeddings (or Zep Cloud managed)
#   app           — FastAPI backbone (BUILD — Section 4)

python >= 3.11
pip install openalgo aiomql riskfolio-lib skfolio arch empyrical-reloaded quantstats \
            stumpy vectorbt backtesting zipline-reloaded jugaad-data openchart \
            lightgbm transformers sentence-transformers EasyEventStudies \
            fastapi uvicorn uvloop orjson redis asyncpg httpx pyyaml apscheduler

mkdir vendor && cd vendor
git clone https://github.com/TauricResearch/TradingAgents
git clone https://github.com/666ghj/MiroFish
git clone https://github.com/xbtlin/ai-berkshire
git clone https://github.com/marketcalls/openalgo
git clone https://github.com/stefan-jansen/machine-learning-for-trading
git clone https://github.com/Zhihan1996/TradeTheEvent

# ── Windows VPS (MT5 leg — co-located with broker's Equinix DC: LD4/NY4/AMS) ──
# MetaTrader5 pip package is WINDOWS-ONLY. Production = Windows Server VPS running:
#   - MT5 terminal + logged-in account
#   - Python 3.11 + aiomql + our mt5_exec_service (FastAPI microservice, MODULE 2/4 adapter)
# Linux core talks to it over private HTTPS/gRPC. Wine/docker MT5 bridges are dev-only.

# ── Broker prerequisites ──
# India: static IP whitelisted with broker (mandatory for API orders since Apr 2025),
#        algo registration per SEBI Feb-2025 framework (see MODULE 17).
# MT5:   ask broker which Equinix site hosts your account's server; provision VPS there.
```

---

## 3. Master Config Schema — `config/master.yaml` (MODULE 18 — build FIRST)

```yaml
broker:
  india:
    provider: dhan            # dhan (25 ops), shoonya (free), fyers, zerodha (10 ops, static-IP)
    api_key: ${INDIA_BROKER_API_KEY}
    api_secret: ${INDIA_BROKER_SECRET}
    static_ip_confirmed: false     # deployment gate — must be true before live
    max_orders_per_sec: 10
  mt5:
    login: ${MT5_LOGIN}
    password: ${MT5_PASSWORD}
    server: ${MT5_SERVER}
    exec_service_url: https://mt5-vps.internal:8443   # Windows VPS microservice
    symbol_classes:
      forex: [EURUSD, GBPUSD, USDJPY, XAUUSD]
      crypto_cfd: [BTCUSD, ETHUSD]

risk_limits:
  max_var_daily: 0.02
  max_position_pct: 0.05
  max_risk_per_trade_pct: 0.01     # NOW ACTUALLY USED by position sizer (v2 fix)
  max_daily_loss_pct: 0.03
  max_sector_exposure_pct: 0.20
  gap_assumption_atr: 3.0          # sizing assumes stop can gap through by 3×ATR

trading_hours:
  india: {open: "09:15", close: "15:30", timezone: "Asia/Kolkata"}
  forex_sessions:
    london: ["08:00", "16:00"]
    new_york: ["13:00", "21:00"]
    timezone: "UTC"
  crypto_cfd:
    mode: continuous               # ~24/7; broker maintenance windows below
    maintenance: [{day: "Sat", from: "00:00", to: "02:00", tz: "UTC"}]

execution_costs:                   # MODULE 40 (NEW)
  india:
    brokerage_flat: 20             # INR per order (discount broker)
    stt_delivery_pct: 0.001
    stt_intraday_sell_pct: 0.00025
    exchange_txn_pct: 0.0000345
    stamp_duty_pct: 0.00015
    gst_pct: 0.18
  impact_model: {y_coefficient: 0.7}   # ΔP ≈ Y·σ·√(Q/V)
  mt5: {spread_map: dynamic, swap_long: broker_api, swap_short: broker_api}

llm:
  provider: claude
  model_debate: claude-sonnet-4-6
  model_fast_classifier: claude-haiku   # Tier-2 severity, ~1s calls

cache_ttl:
  sentiment_signal: 14400
  var_snapshot: 300
  mirofish_macro: 43200

anomaly_guard:                     # MODULE 36 (NEW)
  velocity_sigma: {1s: 6, 5s: 5, 30s: 4}
  spread_blowout_mult: 3.0
  volume_spike_mult: 5.0
  index_1min_pct: 0.5
  on_shock: {pause_entries: true, derisk_pct: 50, tighten_trail: true}
  cooloff_minutes: 15

news:                              # MODULE 10 v2 (two-speed)
  hot_poll_seconds_held_symbols: 45
  cold_poll_minutes_watchlist: 15
  fast_classifier: {severity_pause_threshold: 7}
  dissemination_cluster_feature: true    # count sources per story, don't just dedupe

event_calendar:                    # MODULE 43 (NEW)
  sources: [rbi, fed, cpi_in, cpi_us, union_budget, earnings_held, fo_expiry]
  pre_event_lockout_min: 30
  post_event_resume_min: 15

regime_detector:                   # MODULE 34 (NEW)
  vol_lookback_days: 90
  adx_period: 14
  hurst_window: 200                # H>0.55 trend / H<0.45 mean-revert / else random
  gex_input: true                  # MODULE 39 feed

exit_manager:                      # MODULE 35 (NEW)
  breakeven_at_r: 1.0
  partials: [{at_r: 1.0, pct: 33}, {at_r: 2.0, pct: 33}]
  k_sl_initial: {india: 2.0, mt5_forex: 2.0, mt5_crypto: 3.0}
  k_trail_by_regime: {STRONG_TREND: 3.0, WEAK_TREND: 2.0, RANGE: 1.25, SHOCK: 0.75}
  min_ratchet_step_atr: 0.25
  max_bars_no_progress: {india: 20, mt5_forex: 30, mt5_crypto: 16}
  event_tighten_minutes: 30
  crypto_weekend_policy: tighten   # tighten | flatten | hold
  never_widen_stop: true           # code-enforced invariant

news_reaction_model:               # MODULE 37 (NEW)
  model_type: lightgbm
  encoder: [prosusai_finbert, distilroberta_fin]
  horizons: [5m, 1h, 1d, 5d]
  head_5m_role: risk_only          # Lopez-Lira: no retail entry alpha at 5m
  abstain_below_confidence: 0.6
  min_training_events: 20000
  vix_interaction: true            # Conrad 2025: vol regime suppresses news impact

learning_orchestrator:             # MODULE 38 (NEW)
  ledger: append_only
  recalibrate: nightly             # isotonic only — never weights
  autopsy: weekly
  retrain: monthly
  promotion_gate: {holdout_months: 6, must_beat: [brier, after_cost_pnl], human_approval: true}
  regime_stratified_training: true
  sample_half_life_years: 4
  self_demote_on_calibration_drift: true
  luck_vs_skill_autopsy: true

learning_loop:
  min_matching_cases: 3
  min_consistency_score: 0.7
  rule_audit_period_days: 90

pattern_discovery:
  min_regimes_passed: 5
  min_dsr_probability: 0.95        # v2 FIX: DSR is a probability, not a Sharpe
  min_occurrences: 30              # v2 FIX: was 5 — statistically meaningless
  scan_frequency: monthly

kill_switch:
  auto_trigger_daily_loss_pct: 0.03
  auto_trigger_var_breach: true
  redis_unreachable_behavior: halt   # v2 FIX: FAIL-CLOSED, explicit
```

**Acceptance criteria:** every module reads tunables from this file; a repo-wide grep for numeric literals in decision logic returns nothing (see build plan verification V3).

---

## 4. Architecture Overview

**Three market legs, one brain:**

```
                    ┌──────────── LINUX CORE VPS (Mumbai) ────────────┐
 news/data feeds →  │ Tier1 calendar → Tier2 fast news → Tier3 debate │
 broker WebSockets→ │ regime_detector · GEX map · VaR cache · signals │
                    │ order_router → pre_trade_gate → position_sizer  │
                    │ exit_manager · anomaly_guard · kill_switch      │
                    │ ledger · case_memory · learning_orchestrator    │
                    └───────┬──────────────────────────┬──────────────┘
                            │ localhost REST            │ private HTTPS/gRPC
                    ┌───────▼────────┐         ┌───────▼─────────────────┐
                    │ OpenAlgo       │         │ Windows VPS (Equinix)   │
                    │ → India broker │         │ MT5 terminal + aiomql   │
                    │ (NSE/BSE/MCX)  │         │ → forex + crypto CFDs   │
                    └────────────────┘         └─────────────────────────┘
```

**The four-tier reaction stack** (defense in depth — each tier is slower but smarter):
- **Tier 0 (ms):** MODULE 36 anomaly_guard — price/spread/volume shock detection from ticks; model-free; pauses entries, tightens exits. Cannot be vetoed by any model.
- **Tier 1 (pre-positioned):** MODULE 43 event_calendar — scheduled-event lockouts (most "sudden" crashes are scheduled).
- **Tier 2 (seconds):** two-speed news poll + Haiku severity + MODULE 37 reaction model inference → pause/tighten/opportunity + cache invalidation.
- **Tier 3 (minutes):** TradingAgents debate + MiroFish crowd read → judgment, re-entry posture.

**Execution hot path** (budget: <10ms internal): kill-switch check → parallel Redis pre-checks → sizing → route. LLMs and VaR math never run in the hot path — only cache reads.

---

## PHASE 1 — Foundation & Execution (Weeks 1–4)

Build order within phase: 18 (config) → 1 → 40 → 2 → 42 → 3 → 41 → 36 → 4.

### MODULE 18 — `config/master.yaml` + loader [BUILD FIRST]
Schema in Section 3. Loader validates schema on startup (pydantic), resolves `${ENV}` refs, hot-reloads on SIGHUP for non-safety keys only (risk_limits and kill_switch changes require restart — deliberate friction).

### MODULE 1 — `kill_switch.py` [BUILD — before anything else touches an order]
**What:** Emergency stop. Cancels all pending orders, closes all positions at market, halts new orders.
**v2 semantics (changed):** FAIL-CLOSED. `TRADING_HALTED` flag lives in Redis, but if Redis is unreachable the order router treats it as halted. The flag also mirrors to a local file on both VPSes as a dead-man fallback.
```
async def kill_all(reason):
    set Redis TRADING_HALTED=true  AND local sentinel file on both legs
    cancel all open orders (OpenAlgo + MT5 service), close all positions at market
    log every action to decision_archive (append-only)
    alert (Telegram) with reason + full action list
    flag clears ONLY via explicit manual unlock endpoint with confirmation phrase
```
**Acceptance:** unit-tested with mocked brokers; manual CLI/API trigger; auto-triggers (daily loss, VaR breach) tested against config; provably un-bypassable by order_router (router imports the check, no direct broker client access elsewhere — enforced by lint rule L2 in build plan); Redis-down simulation results in rejected orders.

### MODULE 40 — `transaction_cost_model.py` [BUILD — NEW]
**What:** Full cost of any hypothetical trade: brokerage + STT/stamp/exchange/GST (India) or spread+swap (MT5), plus **market impact** via the square-root law `ΔP ≈ Y·σ·√(Q/V)`.
**Why:** Backtests without India's STT and impact are fiction; the rebalancer and every backtest must price trades before making them.
**Consumers:** position_sizer (net-edge check), rebalance_scheduler (skip trades whose drift < cost), all backtest engines (Addendums), MODULE 38's after-cost promotion gate.
**Acceptance:** unit tests reproduce a broker contract note within 1% on 5 real historical trades; impact term validated against 3 published examples; every backtest run in the repo imports this module (verification V4).

### MODULE 2 — `connection_manager.py` [BUILD]
Warm singletons: httpx.AsyncClient → OpenAlgo (localhost, keep-alive), HTTPS/gRPC channel → MT5 exec service (Windows VPS). Startup latency self-test logs per-leg round-trip. uvloop installed as event loop policy; orjson for all serialization.
**Acceptance:** single instance app-wide; measured warm vs cold latency logged; MT5 channel auto-reconnects with exponential backoff and reports state to health endpoint.

### MODULE 42 — `margin_checker.py` [BUILD — NEW]
**What:** Pre-order funds/margin verification. India: SPAN + exposure margin for F&O (broker margin API), cash for equity. MT5: free-margin check with configurable buffer (default: order must leave ≥30% free margin).
**Why:** v1 had no funds check — first rejected order in production would have been this.
**Acceptance:** called inside MODULE 4 pre-checks; unit tests cover insufficient-margin rejection, margin-API-down (fail-closed: reject), and F&O lot validation.

### MODULE 3 — `position_sizer.py` [BUILD — v2 corrected math]
**What:** Size from **stop distance**, not vibes:
```
def calculate_position_size(entry, stop, balance, current_var, market):
    risk_per_unit = abs(entry - stop)                     # from exit_manager's initial stop calc
    risk_budget   = balance * cfg.max_risk_per_trade_pct  # e.g. 1%
    qty           = risk_budget / risk_per_unit
    # Kelly-style scaling ONLY if a calibrated edge estimate exists (MODULE 37/38 ledger):
    if calibrated_edge_available: qty *= clamp(kelly_fraction(p_win, payoff_ratio), 0, 0.5)  # half-Kelly cap
    qty = min(qty, balance * cfg.max_position_pct / entry)          # hard cap
    qty *= max(0, 1 - current_var / cfg.max_var_daily)              # VaR headroom
    qty *= gap_survival_factor(cfg.gap_assumption_atr)              # 3×ATR gap ≤ daily loss limit
    if net_edge_after_costs(qty, entry, market) <= 0: return 0      # MODULE 40 check
    return floor_to_lot(qty, market)
```
LLM confidence is context for the debate layer — it never enters this function as a probability.
**Acceptance:** property tests — output never exceeds max_position_pct; stop-at-entry edge case → 0; VaR-at-limit → 0; gap assumption honored; costs can zero a trade.

### MODULE 41 — `order_state_machine.py` [BUILD — NEW]
**What:** Every order lives in an explicit state machine: `CREATED → SENT → ACKED → PARTIAL → FILLED | REJECTED | CANCELLED | UNKNOWN`.
**The hard cases it owns:** timeout-after-send (state UNKNOWN → reconcile against broker order book before ANY retry), partial fills (exit_manager informed of actual filled qty), idempotency (client order IDs; a retry can never double-place), rejection taxonomy (margin/price-band/rms → routed to the right fix).
**Acceptance:** simulated chaos tests — network drop after send, duplicate ack, partial then reject — all converge to a consistent terminal state with correct position accounting. No order can be lost or double-counted (invariant test).

### MODULE 36 — `anomaly_guard.py` [BUILD — NEW, Phase 1 (it's the kill switch's fast reflex)]
**What:** Tick-stream shock detector per symbol + index. Triggers on: |1s/5s/30s return| > kσ for that window, spread > 3× rolling median, 30s volume > 5× time-of-day norm, index 1-min move > threshold.
**Actions (<100ms, all local):** set PAUSE_ENTRIES, cancel resting entry orders (never protective stops), flip exit_manager to SHOCK, invalidate sentiment cache for affected names, alert.
**Acceptance:** replayed synthetic shocks (flash crash, gap, breakout) all fire within 100ms in test harness; normal volatile days produce <2 false triggers/week in replay of 3 historical months; cannot be disabled by any model output (Tier-0 invariant test).

### MODULE 4 — `order_router.py` [BUILD — the single door]
**What:** Sole entry point for every trade. 3-way classify: `india` / `mt5_forex` / `mt5_crypto`.
```
async def route_order(req):
    if halted() or paused_entries(): return reject(...)     # fail-closed on Redis errors
    checks = await asyncio.gather(signal_valid, var_headroom, circuit_band_ok,
                                  margin_ok, compliance_tagged, session_open)
    if not all(checks): return reject(named_reason)
    size = position_sizer(...);  if size == 0: return reject("no_net_edge")
    osm  = order_state_machine.create(req, size, client_order_id=uuid)
    result = await (openalgo_leg if market=="india" else mt5_service_leg)(osm)
    audit_log.append(everything)                             # MODULE 8
    exit_manager.attach(result.position)                     # stops live within 2s
    return result
```
**Acceptance:** routes correctly by classification; kill-switch/pause un-bypassable; every outcome audited; mocked-executor tests for all three legs; SEBI algo ID present on every India order (MODULE 17 hook).

---

## PHASE 2 — Intelligence & Risk (Weeks 5–12)

### MODULE 5 — `var_worker.py` [BUILD — own math now]
Background loop (5 min, 24/7 because crypto CFDs): historical-simulation VaR/ES (95/99, 1-day) over combined books in INR base + GARCH(1,1) next-day σ forecast per symbol (`arch`). Writes `portfolio:var:*` and `vol_forecast:*` to Redis. Execution path reads cache only.
**Acceptance:** VaR backtested — ~5% of days should breach VaR95 over a 2-year replay (Kupiec test passes); cache never stale >5min while any market open.

### MODULE 6 — `india_risk_config.py` [BUILD — v2 corrected]
Stock **price bands** (2/5/10/20% daily) separated from **index circuit breakers** (10/15/20% → market-wide halts). F&O lot validation, MCX position limits, ban-list (MWPL) check.
**Acceptance:** price-band check in router pre-checks; MWPL ban-list refreshed daily; unit tests per band tier.

### MODULE 7 — `scenarios/*.json` + stress runner [BUILD]
Same seven scenarios as v1 (COVID-2020, IL&FS-2018, flash crash, RBI +100bps, GFC-2008, Russia-2022, yen-carry-2024) **plus** `crypto_winter_2022.json` and `crypto_weekend_gap.json` for the CFD book. Runner uses Riskfolio-Lib stress framework.

### MODULE 8 — `pre_trade_gate.py` + `audit_log` [BUILD — replaces XQRiskCore]
Append-only Postgres table (hash-chained rows for tamper evidence) + gate service: VaR headroom, sector exposure, rule-approval states. Every rejection logged with machine-readable reason.
**Acceptance:** audit rows immutable (no UPDATE grants); hash chain verifies; gate called from router only.

### MODULE 9 — `greeks_aggregator.py` [BUILD]
Portfolio Δ/Γ/Θ/V from OpenAlgo per-position Greeks. Feeds dashboard + MODULE 39.

### MODULE 10 — `india_news_adapter.py` [BUILD — v2 two-speed]
Sources: ET Markets RSS, NSE corporate announcements API, BSE announcements, SEBI circulars, RBI releases, Screener.in cross-ref. **Two-speed:** held symbols polled every 45s, watchlist every 15min. **Dissemination feature:** cluster same-story items, emit `cluster_size` as impact proxy (don't just dedupe). Output schema: `{source, headline, body, published_at, first_seen_at, tickers, cluster_size, url}`.
**Acceptance:** schema consumed by TradingAgents directly; `first_seen_at` recorded (feeds MODULE 37 timestamp integrity); rate-limit safe.

### MODULE 43 — `event_calendar.py` [BUILD — NEW]
Scheduled-event registry (RBI/Fed/CPI/budget/earnings-of-held/expiry). Publishes `event_risk:{symbol}` minutes-to-event. Router blocks new entries in affected names T-30→T+15; exit_manager tightens.
**Acceptance:** calendar auto-refreshes weekly; lockout enforced in router tests.

### MODULE 11 — `sentiment_cache.py` [BUILD — v2 event-driven invalidation]
As v1 (precompute loop, Redis, execution path never calls LLMs) **plus**: Tier-2 severity ≥ threshold invalidates affected keys immediately and triggers targeted refresh. TTL is fallback, not the only refresh path.
**Acceptance:** cache hit rate >90% market hours; forced-invalidation path tested; hot path never awaits an LLM (static check).

### MODULE 12 — `verdict_bridge.py` [GLUE]
ai-berkshire Pass verdict → watchlist insert (unchanged from v1).

### MODULE 34 — `regime_detector.py` [BUILD — NEW]
Per symbol + index, tick-driven for held names: vol percentile (90d) → LOW/NORMAL/HIGH/SHOCK; ADX+EMA alignment → STRONG_TREND/WEAK_TREND/RANGE; **Hurst exponent** (200-bar) → trend/mean-revert/random tag; session; event_risk (M43); GEX regime (M39); sentiment-flip flag. → Redis `regime:{symbol}`.
**Acceptance:** labeled-window tests (COVID→SHOCK, 2017→STRONG_TREND+LOW); consumers: sizer, exits, rebalancer, anomaly thresholds.

### MODULE 35 — `exit_manager.py` [BUILD — NEW — adaptive trailing TP + SL]
State machine per position (see config): initial stop = max(k_sl×ATR, structure, % cap) **resting at broker within 2s of fill**; +1R → breakeven + partial 1; +2R → partial 2 + chandelier trail (`highest_high − k_trail×ATR`) with k_trail from regime; tighten triggers (sentiment flip, event window, vol jump, crypto weekend); time stop; swap-aware exit for crypto CFDs.
**Per-leg adapters:** India = modify resting SL/SL-M (rate-limit-aware batching, min 0.25×ATR steps); MT5 = server-side `position_modify(sl=...)`.
**Invariants (code-enforced + property-tested):** stop only ever moves favorably; every position has a broker-resident stop (reconciler alarm otherwise); engine rebuilds state from broker + audit log after restart.
**Acceptance:** property tests on ratchet monotonicity; restart-recovery test; A/B vs fixed stops in vectorbt across an index, a pair, and BTC before live (target: MFE-captured % improves without win-rate collapse); exit telemetry (MFE %, slippage, exit reason) written to ledger.

### MODULE 39 — `gex_map.py` [BUILD — NEW]
Daily (and intraday refresh) dealer gamma exposure from NSE option chain: `GEX = Σ Γᵢ·OIᵢ·100·S²·(±)` per strike for NIFTY/BANKNIFTY + held F&O names. Outputs: net GEX regime (amplify vs dampen), largest gamma strikes (pin candidates).
**Consumers:** regime_detector (mechanical-move context), failure_classifier (`mechanical_move_misread` evidence), exit_manager (pin-aware expiry-day behavior).
**Acceptance:** validated against 5 known expiry-pinning days historically; runs after option-chain refresh; degrades gracefully if chain unavailable.

---

## PHASE 2.5 — Infrastructure

- **Linux core:** AWS Mumbai `ap-south-1` (t3.large minimum — TimescaleDB + workers + nightly jobs outgrow t3.medium), Docker Compose stack, static IP, NSE round-trip verified 5–15ms.
- **Windows MT5 VPS:** provisioned in the broker's Equinix site (ask broker: LD4/NY4/AMS), MT5 terminal + exec microservice, private link to core, ping-to-broker <2ms verified.
- **Monitoring:** healthchecks for every worker; dead-man alert if exit_manager or anomaly_guard heartbeat stops; daily backup of TimescaleDB + audit log to S3.

---

## PHASE 3 — Portfolio & OS Polish (Weeks 13–20)

### MODULE 13 — `conviction_to_views.py` [BUILD]
ai-berkshire Pass/Fail/Grey + conviction → Riskfolio-Lib/skfolio Black-Litterman views (unchanged logic from v1; add unit tests for all three verdicts).

### MODULE 14 — `dual_book_manager.py` [BUILD — v2: three sub-books]
INR book (India), USD book (mt5_forex), USD crypto-CFD sub-book (separate risk budget — crypto vol regime must not consume the forex budget). Common-currency normalization via live USDINR; cross-book correlation tracked; warning >0.7.

### MODULE 15 — `rebalance_scheduler.py` [BUILD]
Daily drift check per session windows; skips: illiquid names, near-expiry F&O, event-locked symbols (M43), trades whose drift < transaction cost (M40). Generates minimum-trade set.

### MODULE 16 — `eod_reconciler.py` [BUILD]
Internal audit vs broker tradebook/contract note diff, per leg (OpenAlgo tradebook; MT5 deal history). **v2 additions:** verifies every open position has a broker-resident stop (M35 invariant); verifies ledger settlement rows exist for the day's predictions.

### MODULE 17 — `sebi_compliance_checker.py` [BUILD — REWRITTEN against SEBI Feb-2025 circular]
Validates before any India strategy goes live, per **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (Feb 4, 2025; fully applicable Apr 1, 2026)**:
```
checks = {
  "algo_registered_with_exchange": each strategy has exchange-issued Algo ID via broker,
  "algo_id_tagged_on_orders":      router stamps Algo ID on every India order,
  "broker_api_only":               orders flow through the registered broker API (no direct exchange access),
  "static_ip_whitelisted":         config.broker.india.static_ip_confirmed == True,
  "ops_threshold_respected":       orders-per-second within the exchange-specified threshold
                                   for unregistered algos, or algo registered for higher OPS,
  "black_box_classification":      if strategy logic is non-disclosable (LLM/ML-driven signals),
                                   provider registration requirement (Research Analyst) is
                                   acknowledged and resolved — HARD BLOCK until resolved,
  "audit_trail_retention":         5-year retention on decision archive verified,
}
```
Document the circular number + clause in code comments next to each check. **The black-box question is a real legal decision point for this system — surface it, don't bury it.**

### MODULE — `india_data_pipeline.py` [BUILD — v2 sources]
Live: OpenAlgo WebSocket. Historical: broker candles (Dhan/Fyers/Zerodha), `jugaad-data` + `openchart` for NSE/BSE, bhavcopy archive ingestion (NSE→2000, BSE→2007) into TimescaleDB. F&O chain: NSE endpoint (feeds M39). Corporate actions: BSE API. Deep history (pre-2000 / minute-level): paid vendor slot (TrueData ~₹1.4–2.8k/mo per segment) behind the same normalizer.
**Acceptance:** one OHLCV schema out regardless of source; gaps documented, never silently interpolated.

### Dashboard [GLUE — Streamlit, cap 2 days — internal/dev tool; user-facing UI is MODULE 45]
Panels: P&L per book, VaR gauge, Greeks, GEX regime, today's signals + confidence, last 10 trades, open positions with stop levels + exit state, anomaly/event flags, model calibration sparkline.

### MODULE 44 — `cockpit_gateway.py` [BUILD — NEW]
**What:** The single authenticated door between client apps and the system. FastAPI additions: REST + **WebSocket event bus** streaming positions, P&L, VaR, signals, regime state, anomaly/event flags, exit-state changes, approvals queue, ledger/calibration snapshots. Control endpoints: kill-switch trigger + unlock (confirmation phrase), pause/resume entries, rule & model approvals, watchlist edits.
**Security:** OIDC or token auth + TLS; RBAC (`viewer` = read-only streams, `operator` = controls); every control action double-confirmed, rate-limited, and written to the audit log with actor identity. Clients NEVER talk to brokers or internal workers directly.
**Acceptance:** auth required on every route; viewer role provably cannot invoke controls; kill-switch via gateway is audit-logged with actor; WS reconnect-safe (resume from snapshot + deltas).

### MODULE 45 — `cockpit_app/` [BUILD — NEW — web + Windows + macOS Apple Silicon]
**What:** Cross-platform monitoring & control app. **Stack decision:** web-first **TypeScript SPA (React) + TradingView Lightweight Charts** + WebSocket streams from MODULE 44, wrapped in **Tauri 2** shells for native Windows and macOS (Apple-Silicon-native, Metal-accelerated system webview, small binaries). The identical codebase deploys as a browser web app (PWA). *Documented alternative:* Flutter (Impeller — Metal on M-chips, D3D on Windows, CanvasKit/WASM on web) if a Dart single codebase is preferred; chosen against because trading-grade charting ecosystems (TradingView Lightweight Charts, ECharts) are JS-native and the cockpit is chart-heavy.
**GPU usage:** client GPU is for **rendering** — candlestick/depth charts, GEX heatmaps, calibration plots via WebGL/WebGPU (automatic in Tauri webview and modern browsers). Optional future: ONNX Runtime Web (WebGPU) for local what-if inference on exported model snapshots — **live trading inference always stays server-side** (latency + version consistency). Heavy local research on M-chip/Windows GPUs = run the Python stack natively (LightGBM / PyTorch-MPS), outside this app's scope.
**Panels:** dashboard parity with the Streamlit tool + big-red kill switch (type-phrase confirm), approvals inbox (rules/model promotions), positions with live exit-state view, prediction-ledger & calibration explorer, anomaly/event timeline.
**Safety invariant (§12 rule 11):** the client contains ZERO order/exit logic — it renders state and sends authenticated control intents to MODULE 44 only. Losing the client changes nothing about system safety.
**Acceptance:** same SPA build passes smoke tests in browser + Tauri Windows + Tauri macOS; kill-switch round-trip <1s on LAN; WS survives sleep/wake and reconnects with correct state; viewer build cannot render control affordances.


---

## ADDENDUM A — Learning Loop (Modules 19–23)

### MODULE 19 — `news_attribution.py` [BUILD]
As v1 (T-4h→T+1h window, ranked candidates, explicit no-cause case) **plus v2 rule:** any lesson/rule derived from attribution may only use information available **before** the move for ex-ante features (T+1h window is for explanation, never for tradeable-rule conditions).

### MODULE 20 — `case_memory.py` [BUILD]
As v1 (pgvector/Zep, embeddings, news + crowd context required) **plus:** exit-quality fields (MFE captured, exit reason) and GEX regime at case time.

### MODULE 21 — `retrieval_context_injector.py` [GLUE]
As v1. A/B logging (with vs without precedent) is mandatory — MODULE 38's meta-loop consumes it.

### MODULE 22 — `lesson_extractor.py` [BUILD]
As v1, plus the five-cause error taxonomy tag (see MODULE 38) so lessons carry their evidence class.

### MODULE 23 — `backtest_runner.py` [BUILD — v2 engines]
Engines: `vectorbt` (mass scans, exit A/B) + `backtesting.py` (event-driven verification) + `zipline-reloaded` (cross-check). All runs price trades through MODULE 40 (no gross-P&L backtests exist in this repo — verification V4). Nightly: flag >2% moves → attribution → case memory. Holdout discipline: last 6 months withheld; retrieval-augmented agents must beat vanilla on holdout before trusting case-memory influence.

---

## ADDENDUM B — Strategy Control & Failure Diagnostics (Modules 24–28)

### MODULE 24 — `strategy_config_engine.py` [BUILD]
As v1: evidence bar (min cases, consistency) → holdout validation → **human approval mandatory** (non-bypassable default; the override itself requires a separate explicit config flag plus confirmation phrase).

### MODULE 25 — `holdout_validator.py` [BUILD — v2 statistics]
ON vs OFF over last 90 days of untouched data, **plus:** stationary-bootstrap significance test on the delta (rule passes only if p < 0.1 AND sharpe_delta > 0 AND max_dd not worse). Each candidate rule consumes the holdout at most once per quarter (leak control).

### MODULE 26 — `live_failure_monitor.py` [BUILD]
As v1: triggers on 3 consecutive losses / daily-loss breach / 2× historical drawdown → async diagnosis → proposal → human approval. Never blocks execution path.

### MODULE 27 — `failure_classifier.py` [BUILD]
Six categories as v1 (news_blindspot, regime_mismatch, mechanical_move_misread, correlation_breakdown, stale_signal_cache, overfit_historical_rule). **v2:** `mechanical_move_misread` now uses MODULE 39 GEX evidence (short-gamma amplification days) in addition to MiroFish flags.

### MODULE 28 — `rule_auditor.py` [BUILD]
Weekly shadow-simulation audit of every active rule after 90 days; flags net-negative rules for human review (same approval discipline to deactivate).

---

## ADDENDUM C — Auto Strategy Discovery (Modules 29–33)

### MODULE 29 — `historical_data_loader.py` [BUILD — v2 sources]
Coverage targets as v1 (Nifty 1996+, Sensex 1986+, S&P 1957+, EURUSD 1999+, gold 1975+, BTC 2014+ added) but sources corrected: bhavcopy archives + broker APIs + `jugaad-data` for India; yfinance/vendor for global; MT5 history for FX/crypto CFDs. Data-quality report per instrument (gaps documented).

### MODULE 30 — `regime_filter.py` [BUILD — v2 thresholds]
Seven regimes as v1. **v2:** a pattern needs ≥ `min_occurrences: 30` total and ≥3 occurrences inside a regime for that regime's win-rate to count; regimes with <3 occurrences are `None` (excluded from the 5-of-7 requirement denominator only if data genuinely doesn't cover the era).

### MODULE 31 — `deflated_sharpe.py` [BUILD — v2 corrected]
Bailey & López de Prado DSR — **outputs a probability**. Gate: `dsr >= 0.95`. Validated against ml4t reference tests. Penalty grows with num_trials per scan (log the trial count of every scan run).

### MODULE 32 — `walk_forward_validator.py` [BUILD]
As v1 (rolling window, majority-of-segments rule, ≥5 segments). Document window type (rolling, 20y default).

### MODULE 33 — `pattern_miner.py` [BUILD — orchestrator, v2 input fix]
Stage 1 runs STUMPY on **z-normalized log-return windows** (never raw prices). Stages: motif search → regime filter → news attribution (survivors only) → DSR ≥ 0.95 → walk-forward → human review via strategy_config_engine. Monthly schedule. Log attrition per stage.

---

## ADDENDUM D — ML Reaction Model & Self-Improvement (Modules 37–38) [NEW]

### MODULE 37 — `news_reaction_model.py` [BUILD, with sourced components]
**Task:** given news + market state at T, predict per horizon (5m/1h/1d/5d): direction P(up/down/none), magnitude (ATR buckets <0.5 / 0.5–1.5 / >1.5), fade-vs-drift persistence, calibrated confidence with abstain.
**Sourced components (verified 2026-08):** text encoders = ProsusAI/finbert + mrm8488/distilroberta-fin-news (frozen); US pretraining corpus = FNSPID (15.7M news, 1999–2023); India news history = GDELT GKG (BigQuery, Indian financial press since 2013) + NSE/BSE announcement archives (exact timestamps); labels = EasyEventStudies CAR[0,+1]/[0,+5] market-model abnormal returns; event taxonomy + benchmark = EDT/TradeTheEvent; baseline to beat = FinGPT-Forecaster-style prompted LLM (Stage 0).
**Model:** LightGBM multi-head on [frozen embeddings ⊕ engineered news features (source, category, surprise, cluster_size, novelty) ⊕ regime features (M34) ⊕ India-VIX interaction].
**Build stages:** S0 prompted baseline (week 1, benchmark logged) → S1 US pretrain on FNSPID, validate on EDT/CMIN → S2 India dataset build (GDELT + announcements + CAR labels; 4–8 weeks; target 200k+ event rows) + transfer → S3 (optional) encoder fine-tune / GHOST-style gated fusion, only if S2 shows signal.
**Design constraints (evidence-backed):** 5m head is risk-only (no retail entry alpha at that horizon — Lopez-Lira); expect AUC ~0.55–0.60 (Two Sigma post-mortem — that IS an edge at scale); vol-regime interaction mandatory (Conrad 2025).
**Training hygiene (acceptance-tested):** features strictly ≤T; unverifiable first-release timestamps → row excluded; overlapping-event windows down-weighted; "no-move" majority class calibrated; purged walk-forward splits with embargo; regime-stratified sampling with 4y half-life decay.
**Integration:** Tier-2 fusion — (severity high + BIG + DRIFT → pause, then informed entry) / (+ FADE → tighten into spike, book partials) / (+ NO-MOVE → false-alarm filter) / (abstain → plain severity rules). Predictions appended to TradingAgents prompt and to every ledger row. **Invariant: model output can never veto Tier 0/1 protective actions.**

### MODULE 38 — `learning_orchestrator.py` + `prediction_ledger` [BUILD]
**Prediction ledger (build with M37 S0, day one):** append-only TimescaleDB table — every prediction/signal/exit decision recorded at decision time with frozen feature vector, model version, confidence, action taken; outcomes settled later per horizon. Hindsight-proof by construction.
**Error autopsy (weekly):** each significant miss classified — bad_input (fix pipeline, don't touch model) / missing_feature (add feature) / regime_shift (reweight+retrain, widen abstain) / model_wrong (upweight row) / irreducible_noise (**learn nothing** — logged verdict). Wins audited too: skill vs luck; lucky wins logged as near-misses.
**Cadences:** per-prediction = ledger + drift monitors only; nightly = training-store append + isotonic recalibration (confidence only, never logic); weekly = autopsies + lesson extraction + rule flags; monthly = full retrain → champion/challenger (beat incumbent on trailing 6-month holdout: Brier + after-cost P&L) → human approval; quarterly = feature search, rule pruning, meta-audit.
**Guardrails:** regime-stratified training (never recent-only); own-trade contamination tagging; calibration-drift auto-demote to abstain-mode + alert; promotion requires human click (same gate as M24).
**Meta-loop:** quarterly self-audit — are promotions improving live results? are retrieved lessons helping (M21 A/B)? is abstain rate healthy? Gate tightens itself on decay.

---

## 11. Build Order Summary v2

```
Phase 1  (wk 1–4):   18 → 1 → 40 → 2 → 42 → 3 → 41 → 36 → 4          [+ unit tests before code]
Phase 2  (wk 5–12):  5, 6, 7, 8, 9, 10, 43, 11, 12, 34, 35, 39        [exit engine before any live paper P&L]
Phase 2.5:           Mumbai VPS + Windows MT5 VPS + monitoring
Phase 3  (wk 13–20): 13, 14, 15, 16, 17, india_data_pipeline, dashboard, 44 (gateway)
Phase 3.5:           45 (cockpit app: web PWA + Tauri Windows/macOS shells)
Addendum A:          19–23   (ledger from M38 starts recording at Phase 2 already)
Addendum B:          24–28
Addendum D:          37 (S0 in Phase 2; S1–S2 alongside Addendum A), 38
Addendum C:          29–33   (LAST — needs data + live paper history; most speculative)
```
**Total: 12 external repos/libs + 42 custom modules.**
Paper-trade gates: every execution path ≥2 weeks paper before live; every learned rule and model promotion ≥2 weeks shadow before influencing live orders.

---

## 12. Non-Negotiable Safety Rules v2 (override everything else)

1. `kill_switch.py` exists and is tested before any other execution code runs against real or paper capital. **Fail-closed everywhere:** Redis down = halted; margin API down = reject; unknown order state = no retry until reconciled.
2. Every open position has a **broker-resident stop** within 2s of fill. The exit engine may only tighten, never widen (code-enforced invariant).
3. Tier 0 (anomaly guard) and Tier 1 (event lockouts) **cannot be vetoed by any model, LLM, or learned rule.**
4. Human approval gates (rules, model promotions, rule deactivations) are default-ON and not silently bypassable; the override flag requires a separate explicit config change plus confirmation phrase.
5. Every module touching money has unit tests before it's marked complete; chaos tests for the order state machine.
6. Paper trade every new capability ≥2 weeks before live capital.
7. `sebi_compliance_checker.py` (Feb-2025 framework, incl. the black-box/RA determination) must pass before any India strategy goes live.
8. No hardcoded thresholds — everything tunable lives in `config/master.yaml`.
9. All backtests and promotion gates are **after-cost** (MODULE 40). Gross-P&L results are not results.
10. The prediction ledger is append-only and written at decision time — no retroactive edits, ever.
11. Client apps (MODULE 45) contain zero order/exit logic and never talk to brokers — all control intents flow through the authenticated cockpit gateway (MODULE 44) with audit + confirmation.

---

## 13. Legal & Compliance Appendix (read before going live)

**India algo trading:** governed by SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (Feb 4, 2025), effective phased Oct 1 2025 / Apr 1 2026. Retail algos: exchange registration via broker, Algo ID on every order, broker-API-only routing, static IP, OPS thresholds. **Black-box algos (logic not disclosable — plausibly including LLM/ML-driven signals) carry a Research Analyst registration requirement for the provider — resolve this question with a professional before live deployment. MODULE 17 hard-blocks until resolved.**

**Offshore MT5 (forex + crypto CFDs):** trading margined FX/CFDs with offshore brokers is **not permitted for Indian residents under FEMA**; RBI maintains an Alert List naming many MT5 brokers (incl. Exness, OctaFX); LRS remittances may not fund margined forex. This build implements the user's explicit decision to use MT5 — the legal exposure is acknowledged and owned by the operator, and this spec does not mitigate it. **Legal alternatives if ever wanted:** exchange-traded currency derivatives on NSE (INR pairs + select crosses); crypto derivatives on FIU-registered Delta Exchange India (full API, in CCXT as `delta`); spot crypto on FIU-registered exchanges (Binance registered Aug 2024; CoinDCX; taxes: 30% + cess on gains, 1% TDS, exchange reporting to ITD from Apr 1 2026, no loss offset).

**Data licensing:** FNSPID rights released (Jul 2025 note) — retain the announcement; EDT is research-use (Reuters-scraped) — use for benchmarking/pretraining research, not redistribution; GDELT open; exchange bhavcopies free; broker API data per broker T&Cs.

---

*End of MASTER_BUILD_SPEC v2.0 — companion execution playbook: `AGENTIC_BUILD_PLAN.md`.*
