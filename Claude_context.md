# Trading OS — Working Context (handoff for Claude Code)

> Portable context for picking up the **trading-os** project in a fresh Claude Code / agent session.
> Written 2026-08-08. Reflects `main` @ `66033e2`. Everything below is fact from the repo + the work log; verify against the repo before acting.

---

## 1. What this project is

A complete, tested **algorithmic trading system** (Python, event-driven) with a hard, code-enforced **evidence gate** in front of live trading. It paper-trades today; it will only go live once three human items are satisfied.

- **Repo:** https://github.com/xlumamarathon-rog/trading-os (owner `xlumamarathon-rog`, default branch `main`).
- **Runtime philosophy: LEAN.** Production runtime ships on **three deps** (`pydantic`, `pyyaml`, `httpx`). Heavy libs (pandas/numpy/scipy, ml, fastapi) are test/optional/later-wave only. Preserve this — do not add runtime deps casually.
- **Language/tooling:** Python ≥3.11 (sandbox runs 3.9 fine for tests), `pytest` + `pytest-asyncio` (`asyncio_mode=auto`), `pythonpath=["."]`.
- **Key docs in-repo:** `README.md` (story), `DEPLOY.md` (VPS runbook), `OPERATOR.md` (daily manual), `progress.md` (build ledger), `docs/MASTER_BUILD_SPEC_V2.md` (spec; §12 = safety philosophy).

### Test baseline
`python -m pytest tests/ -q` → **401 passed, 7 skipped** on `main` @ `66033e2`.
The 6–7 skips are expected: vendor drift-canaries skip unless `scripts/clone_vendors.sh` was run; the empyrical/pandas_ta cross-checks skip if those libs aren't installed.

---

## 2. The live gate (NEVER bypass, fake, or weaken)

`python -m src.app --mode live` raises `LiveGateError` until every clause passes. `scripts/go_live_check.py` is the operator pre-flight. Three human items remain — all currently **UNCHECKED**:

1. **SEBI Feb-2025 algo registration** — exchange Algo IDs via broker + black-box/Research-Analyst determination with a qualified professional (circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013). Recorded as `sebi_checks_passed: true` in `gate_state.json` **on the VPS** (not committed).
2. **Broker static IP** — whitelisted with broker, then `broker.india.static_ip_confirmed: true` in `config/master.yaml` (this one is repo-verifiable).
3. **Risk acknowledgement** — `human_ack` in `gate_state.json` set to exactly `I ACCEPT LIVE TRADING RISK`.

**Hard rule for any agent working here:** never modify `gate_state.json`, the live-gate code (`src/app.py::assert_live_allowed`), or the flag `broker.india.static_ip_confirmed` in `config/master.yaml` as part of research/feature work. The frictions ARE the product (spec §12). Security changes may only *strengthen* the gate, never weaken it.

### Launch sequence (once all three are done)
`go_live_check.py` → `--mode live` (boots SAFE-STARTED, entries paused) → kill-switch drill → Resume entries in cockpit → first 5 days at the 1% ramp cap.

---

## 3. Architecture cheat-sheet

- **Entry signals:** `src/strategies/signals.py` — contract `signal(bars, i, regime) -> "buy"|"sell"|None`; decisions use `bars[:i]` only (no lookahead), entries execute at `bars[i]["open"]`. Registry `SIGNALS` + `get_signal(name)`. Signals: baseline, tsmom, tsmom_f, donchian, rsi2, improved, improved2, improved3, accurate, accurate_ls. (RSI here is **Cutler/SMA-based** by design.)
- **Order path:** `src/core/order_router.py` (single door: kill-switch → anomaly pause → SEBI algo-id tag → parallel prechecks → portfolio guard → sizing). `guard_stack.py` composes budget/session/heat guards. `kill_switch.py` (fail-closed, dual flag Redis+sentinel, constant-time unlock). `position_sizer.py`, `margin_checker.py`.
- **Exits:** `src/exits/exit_manager.py` (state machine RISK_ON→BREAKEVEN→TRAILING→EXITED; chandelier ATR trail; partials; profit-lock; snapshot/restore). Adapters in `src/exits/adapters/` (composite routes india/mt5 legs).
- **Runtime assembly:** `src/runtime.py::build_runtime(cfg, mode=...)` wires the whole graph for paper|live (live = gate + SAFE-START + ramp cap). Now also composes the portfolio guard stack.
- **Intel:** `src/intel/` — regime_detector, anomaly_guard, event_calendar, news/sentiment adapters, **technical_analysis.py (NEW)**, **fundamental_analysis.py (NEW)**.
- **Ops:** `src/ops/` — cockpit_gateway (token RBAC viewer/operator), paper_server/broker, persistence (hash-chained audit), session_guard, shadow_runner, **metrics.py (NEW)**.
- **Research harness:** `scripts/research_replay.py <strategy> <data_dir> <out_dir> [exit_overrides_json]` — replays real OHLC through the FULL stack (router→paper broker w/ real cost schedules→exit manager→kill-switch→gate), reconciliation-CLEAN, writes `results.json`. Env knobs: `STARTING_CASH`, `REPORT_FROM`, `GIVEBACK_PCT`, `DAY_PROFIT_BANK`, `DAY_LOSS_STOP`, `BUDGET`, `MAX_HEAT_PCT`, `LONG_ONLY`, `RISK_PCT`, `MARTIN_ADR_MIN`.
- **Data:** `data/market_{india,forex,crypto}_6m/` (real OHLC, Feb 5–Aug 5 2026, with `symbols.json`+`meta.json`); stress sets `data/real_covid/`, `data/scenario_gfc_2008/`, `data/scenario_flash_crash_2012/`. **Bars are OHLC-only (no volume).**
- **Cockpit UI:** `cockpit/web/` = zero-build static SPA (index.html/app.js/style.css) served by the gateway `/ui`; `?demo=1` runs on mock data. `cockpit-next/` = Next.js variant. UI-flow test: `node cockpit/web/ui_flow_test.mjs` (23 checks).

---

## 4. Work done in this thread (chronological, with commit SHAs on `main` unless noted)

Baseline at thread start: `9c2eac8` (prior ShadowRunner async-guard fix).

1. **`0005223` — 5 integration "seam" bugs** (same class as 9c2eac8: modules green in isolation that can't talk when wired):
   - `profit_lock` shipped as a test in 9c2eac8 but its **implementation was never committed** → fresh clones were red. Implemented in ExitManager.
   - `guard_stack` called equity_fn/positions_fn synchronously though the natural source (balance_fn) is async → now sync/async-tolerant.
   - `build_runtime` never actually wired the guard stack (budget/session/heat absent from assembled runtime) → now composed.
   - `WorkerSupervisor` awaited `alert_fn` unconditionally (sync alerter lost WORKER DOWN) → `_maybe_await`.
   - `ExitManager.on_partial/on_exit` same intolerance → `_maybe_await`.
   - Also: vendor-canary skip could never fire (dir always exists) → keyed on real checkouts. +6 regression tests.
2. **Replay verification** (no commit): re-ran the certified 6-month per-market replays on `0005223` vs pre-fix `9c2eac8` — **byte-identical**; seam fixes change zero backtest behavior. `profit_lock` ships inert unless configured.
3. **`dab9f11` — 3 real security fixes**: cockpit `/ui/{asset}` path-traversal (prefix check → `Path.is_relative_to`); MT5 exec service had **zero auth** on order/close endpoints → optional `X-MT5-Auth` shared secret (constant-time; env `MT5_SERVICE_TOKEN`; documented in DEPLOY.md + .env.example); `/config` now defensively redacts secrets. +7 tests.
4. **`cb3819a` — hardened the 2 "accepted" items after deep re-review**: kill-switch unlock → `secrets.compare_digest` (constant-time; unlock is a genuine 2nd secret beyond the operator token; verified phrase does NOT leak to audit); `go_live_check` runs pytest with a mode-0700 `--basetemp` (neutralizes PYSEC-2026-1845 `/tmp/pytest-of` race). +9 tests.
5. **`f951291` — pytest-fix watch**: `scripts/check_pytest_fix.py` watches PyPI for a final pytest ≥9.0.3 (the PYSEC-2026-1845 fix), reports ACTION/OK/SKIP, wired into go_live_check as a soft NOTE (never PASS/FAIL). +19 tests. NOTE: on first live run it fired ACTION — pytest 9.1.1 IS on PyPI now (the sandbox's pip index had lagged at 8.4.2); the requirements pin bump is a pending real follow-up.
6. **Document analysis** (no code): analyzed 5 attached strategy docs (Martin Luke swing, 18-day MA, ICT SMT divergence, First Red Day, Quant-X). Verdict: mostly anecdotal/unverifiable; Quant-X is an architecture guide, not a strategy.
7. **Research branch `research/doc-strategies-aug2026`** (NOT on main; HEAD `b847f9c`): added `martin_luke` + `18ma` signals, backtested through the engine → **no positive edge** on this universe → **removed** them (restored signals.py to main baseline, deleted the test). Branch retained with add→remove history.
8. **`809a6c6` — empyrical-validated metrics**: compared our hand-rolled Sharpe/MaxDD vs `empyrical` (industry standard) on real curves → **identical to 0.0000**. Added `src/ops/metrics.py` (stdlib-only Sharpe/Sortino/Calmar/MDD/CAGR/vol, cross-validated vs empyrical in tests); research_replay now emits sortino/calmar/annual-vol additively (existing certified fields byte-identical). Kept our engine/RSI over backtesting.py/vectorbt/ta-lib (better or unsafe to swap). +8 tests. `empyrical-reloaded` added as OPTIONAL validation dep.
9. **`66033e2` — technical + fundamental analysis + cockpit UI** (from the OpenBB guide):
   - **Did NOT vendor OpenBB** — it's AGPL-3.0 (network copyleft, risky for a live/client-facing system) and heavy. Kept as an optional external tool.
   - `src/intel/technical_analysis.py` (MODULE 56): MACD, Bollinger, **Wilder RSI** (additive; distinct from the engine's Cutler RSI — existing signals untouched), Stochastic, ADX, OBV; provenance-tagged `Study`/`analyze`; no lookahead.
   - `src/intel/fundamental_analysis.py` (MODULE 57): ratios (P/E,P/B,ROE,ROA,D/E,current,margins,interest-cov,FCF-yield) + 0–100 health score + `FundamentalProvider` Protocol; fail-soft (missing→None+warnings).
   - Cockpit: new **Technicals + Fundamentals panel** (score rings, indicator chips, flags, bull/bear/neutral); read-only; existing 23/23 UI-flow test green. +17 tests.

---

## 5. Standing rules for agents on this repo

1. **Never touch the gate** (see §2). Research/features must not change `gate_state.json`, `assert_live_allowed`, or the static-IP flag.
2. **Real numbers only.** No estimated/hypothetical performance. Clone, run the real test suite as baseline, replay candidates through `research_replay.py` on the real data + stress sets. Every replay must reconcile CLEAN.
3. **Research stays on a branch**, never `main`, until it earns promotion via evidence (+ the repo's human-review/PR philosophy for anything changing trading behavior). Safety/security/additive-tooling fixes have gone straight to `main` with proof.
4. **Keep the runtime lean.** Validation/reference libs are test-only or optional (skip cleanly if absent), never runtime deps.
5. **Every change ships with regression tests**, and you must prove certified numbers are unchanged when touching shared code.
6. **Pushes** use the GitHub integration (`github__push_files` / `github__create_branch` / `github__delete_file`); after pushing, reset local to the remote and re-run the suite to verify from a clean state.

---

## 6. Open follow-ups (not yet done)

- **Bump pytest pin** to `>=9.1.1` (the PYSEC-2026-1845 fix is now published) after validating the suite on pytest 9; then retire the `--basetemp` mitigation. (Watch script already flags ACTION.)
- **Wire a live `/analysis` gateway endpoint** so the cockpit panel shows real technicals/fundamentals instead of demo fixtures.
- **Wire a real `FundamentalProvider`** (e.g. yfinance `.NS`/`.BO` for India) with cache + fail-soft + recorded-fixture tests (no live network in tests).
- Optional: delete the `research/doc-strategies-aug2026` branch entirely (no delete-branch tool was available in-session; do it in the GitHub UI) — its code was already reverted.
- Optional: evaluate `vectorbt` as a **research-only** fast-sweep tool (never the certified execution path).

---

## 7. Quick start for a fresh session

```bash
git clone https://github.com/xlumamarathon-rog/trading-os.git && cd trading-os
python3 -m pip install -r requirements.txt          # pydantic pyyaml httpx pytest pytest-asyncio (+fastapi for mt5 tests)
python3 -m pip install fastapi                       # needed for mt5_service + a few tests to collect
python3 -m pytest tests/ -q                          # expect ~401 passed, ~6-7 skipped
python3 scripts/go_live_check.py                     # operator pre-flight (exits 1: 3 human items pending)
python3 scripts/research_replay.py tsmom data/market_india_6m /tmp/out   # example real replay
```

Gate status as of this handoff: **SEBI ☐ · static IP ☐ · risk ack ☐** — live trading not yet earned.
