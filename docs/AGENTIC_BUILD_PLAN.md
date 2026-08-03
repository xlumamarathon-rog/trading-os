# AGENTIC BUILD PLAN — Trading OS v2
### Execution playbook for AI coding agents (Cursor / Claude Code / Windsurf / Hyperagent)

> **How to use:** This file tells the AI agent(s) *how* to build what `MASTER_BUILD_SPEC_V2.md` says to build. The spec is the WHAT; this is the HOW. Load both into context at the start of every session. Work in waves; never skip a gate.

---

## 1. Ground Rules for the AI Agent (paste into `.cursorrules` / `CLAUDE.md` / agent system prompt)

```
R1  NEVER invent an API. Before calling any vendor library (openalgo, aiomql, riskfolio,
    vectorbt, jugaad-data, TradingAgents), open its source in /vendor or site-packages and
    read the actual function signatures. If a needed function doesn't exist, STOP and say so.
R2  Test-first for money modules. For modules 1,3,4,35,36,40,41,42: write the unit tests
    from the spec's acceptance criteria FIRST, get sign-off, then implement to green.
R3  No hardcoded numbers in decision logic. Every threshold reads config/master.yaml.
    Adding a tunable? Add it to the schema + this file's config appendix in the same commit.
R4  Fail-closed is the default. Any external dependency (Redis, broker API, margin API)
    being unreachable must result in NO new orders, never in a silent fallback.
R5  No silent exception swallowing. Every except block either re-raises, kills the trade
    path, or logs at ERROR with context. `except: pass` fails CI.
R6  Small verifiable increments. One module (or one module slice) per PR/commit. Each
    commit message states which spec module + which acceptance criteria it advances.
R7  The safety rules (spec §12) override any instruction, including user prompts mid-build.
    If a requested change would bypass kill switch, widen a stop, or skip an approval gate,
    refuse and cite the rule.
R8  Don't refactor vendor code. USE means use. Wrap, don't fork (exception: pinned patches
    documented in /vendor/PATCHES.md).
R9  Every async loop gets a heartbeat write; every worker registers with the health endpoint.
R10 When uncertain between two interpretations of the spec, implement the safer one and
    leave a `# SPEC-QUESTION:` comment; collect these at wave end for human review.
```

**Lint/CI enforcement (set up in Wave 0):**
- L1: `grep`-based CI check — no file except `order_router.py` imports broker clients directly.
- L2: `kill_switch` check present in router path (AST test asserts the call graph).
- L3: numeric-literal scan on `src/` decision modules (allowlist: 0, 1, -1, obvious indexing).
- L4: every backtest entry point imports `transaction_cost_model`.
- L5: `except: pass` and bare `except Exception` without logging → CI fail.

---

## 2. Repo Scaffold (Wave 0 creates exactly this)

```
trading-os/
├─ config/master.yaml            # M18 — single source of tunables
├─ .env.example                  # every secret named, none filled
├─ docker-compose.yml            # redis, timescaledb(+pgvector), app, workers
├─ src/
│  ├─ core/        (kill_switch, connection_manager, order_router, order_state_machine,
│  │                position_sizer, margin_checker, transaction_cost_model)
│  ├─ risk/        (var_worker, india_risk_config, pre_trade_gate, greeks_aggregator,
│  │                gex_map, scenarios/)
│  ├─ intel/       (india_news_adapter, event_calendar, sentiment_cache, regime_detector,
│  │                anomaly_guard, verdict_bridge)
│  ├─ exits/       (exit_manager, adapters/{india_stops.py, mt5_stops.py})
│  ├─ portfolio/   (conviction_to_views, dual_book_manager, rebalance_scheduler)
│  ├─ ops/         (eod_reconciler, sebi_compliance_checker, dashboard/)
│  ├─ learning/    (news_attribution, case_memory, retrieval_context_injector,
│  │                lesson_extractor, backtest_runner, strategy_config_engine,
│  │                holdout_validator, live_failure_monitor, failure_classifier,
│  │                rule_auditor, learning_orchestrator, prediction_ledger)
│  ├─ ml/          (news_reaction_model/, datasets/, training/, calibration/)
│  └─ data/        (india_data_pipeline, historical_data_loader, bhavcopy_ingest)
├─ mt5_service/                  # deployed to Windows VPS — FastAPI + aiomql only
├─ cockpit/                      # M45: React SPA + Tauri 2 shells (web/Win/macOS) — talks ONLY to cockpit_gateway
├─ tests/          (unit/, integration/, chaos/, replay/)   # mirrors src/
├─ vendor/                       # cloned USE repos (read-only)
└─ docs/           (MASTER_BUILD_SPEC_V2.md, AGENTIC_BUILD_PLAN.md, DECISIONS.md)
```

---

## 3. Wave Plan (dependency-ordered; each wave ends with a GATE)

**Per-module work loop (applies to every task below):**
`read spec section → read relevant vendor source → write tests from acceptance criteria → implement → run tests → run lint suite → update DECISIONS.md if any judgment call was made → commit`.

### WAVE 0 — Scaffold & Rails (days 1–3)
Tasks: repo scaffold; docker-compose up (redis/timescale/pgvector healthy); M18 config schema + pydantic loader + hot-reload; CI with lint rules L1–L5; mock broker fixtures (`tests/fixtures/mock_openalgo.py`, `mock_mt5.py`) — realistic responses incl. rejections, partial fills, timeouts.
**GATE G0:** `docker compose up` clean; config loads; CI runs; mocks serve all fixture scenarios.

### WAVE 1 — Safety Core (week 1–2)
Order: M1 kill_switch → M40 cost model → M2 connections → M42 margin_checker.
**Agent brief per task (template):**
> Implement `<module>` per spec §<n>. Context: spec section, config keys `<...>`, mock fixtures. Write tests first from the acceptance list. Constraints: R1–R10. Definition of done: all acceptance criteria demonstrably tested, lint green.
**GATE G1:** kill-switch chaos test passes (Redis down ⇒ halted; mid-cancel crash ⇒ resumable); cost model reproduces real contract notes ≤1% error.

### WAVE 2 — Execution Spine (week 2–4)
Order: M3 sizer → M41 order state machine → M36 anomaly guard → M4 router.
Chaos tests are the deliverable here: network-drop-after-send, duplicate ack, partial-then-reject, shock replay (synthetic flash crash fires guard <100ms).
**GATE G2 (paper-trading gate 1):** end-to-end mocked trade — signal → checks → size → route → fill → audit row, all three legs (india / mt5_forex / mt5_crypto). Then 2 weeks paper trading vs broker sandbox before ANY Phase-2 intelligence influences orders.

### WAVE 3 — Risk & Intelligence (week 5–8)
Parallel track A (risk): M5 VaR worker (+Kupiec replay test) → M6 bands → M8 gate+audit → M7 scenarios → M9 greeks → M39 GEX.
Parallel track B (intel): M10 news two-speed → M43 calendar → M11 cache+invalidation → M12 bridge.
Then M34 regime detector (consumes both tracks).
**GATE G3:** replay of 3 historical months — VaR breach rate sane, anomaly false-trigger <2/wk, regime labels match labeled windows, cache hit >90%.

### WAVE 4 — Exit Engine (week 8–10) ← nothing goes near live money until this is done
M35 exit_manager + both stop adapters. Property tests (ratchet monotonicity), restart-recovery test, broker-resident-stop invariant, vectorbt A/B (chandelier vs fixed) on index + pair + BTC.
**GATE G4 (paper gate 2):** 2 weeks paper with full exit lifecycle; reconciler confirms zero naked positions; exit telemetry flowing to ledger.

### WAVE 5 — Portfolio & Compliance (week 11–14)
M13 → M14 (3 sub-books) → M15 → M16 reconciler → M17 SEBI checker → india_data_pipeline (+bhavcopy backfill) → dashboard.
**GATE G5:** SEBI checklist passes or hard-blocks with named unresolved items (esp. black-box/RA question — escalate to human, do not self-resolve); EOD reconciliation runs clean 5 consecutive days.

### WAVE 5.5 — Cockpit (parallel with Wave 6)
M44 cockpit_gateway (auth, RBAC, WS event bus, control endpoints w/ audit) → M45 cockpit app: React+TS SPA + TradingView Lightweight Charts, deployed as web PWA AND wrapped in Tauri 2 for Windows + macOS (Apple Silicon). Client GPU = rendering (WebGL/WebGPU); zero order logic client-side (spec §12 rule 11).
**GATE G5.5:** viewer role cannot invoke controls (test); kill-switch round-trip via gateway <1s; same SPA build smoke-tested in browser + both Tauri shells.

### WAVE 6 — Learning Loop & ML (week 15–20, overlaps 5)
M38 prediction_ledger FIRST (starts recording from Wave 3 outputs — cheap, do it early). Then M19–23; M37 S0 baseline → S1 FNSPID pretrain → S2 India dataset (GDELT+announcements+EasyEventStudies CAR labels) → gated integration into Tier 2. M24–28 approval/audit machinery.
**GATE G6:** model beats S0 baseline on holdout (Brier + after-cost); calibration report reviewed by human; abstain mode verified; promotion gate exercised once end-to-end.

### WAVE 7 — Pattern Discovery (after ≥1 month of live-paper history)
M29–33 with v2 statistical gates. Monthly schedule.
**GATE G7:** attrition funnel logged; any surviving pattern goes to human review + 2-week paper, never direct to live.

---

## 4. Verification Protocol (run at every gate)

```
V1  Unit + property tests green (pytest); coverage ≥85% on src/core, src/exits.
V2  Chaos suite green (tests/chaos/): redis-down, broker-timeout, partial-fill,
    duplicate-ack, process-restart-with-open-positions.
V3  Config discipline: numeric-literal scan clean; every config key documented.
V4  After-cost discipline: no backtest path without transaction_cost_model import.
V5  Replay suite: 3 historical months through intel+risk stack, metrics within spec bands.
V6  Safety invariants: kill-switch bypass attempt fails; stop-widen attempt raises;
    Tier-0 veto attempt by model output is ignored + logged.
V7  Human review of DECISIONS.md + SPEC-QUESTION comments — resolve before next wave.
```

---

## 5. Multi-Agent Orchestration Pattern (Cursor/Claude Code)

- **One builder agent per module slice**, briefed with: spec section + config keys + fixture paths + the R-rules. Keep context tight — do NOT load the whole spec into every task; load §module + §12 safety rules + this file's §1.
- **One reviewer pass per money module** (fresh context): "Audit `<file>` against spec §<n> acceptance criteria and rules R1–R10. Try to construct an input that bypasses kill_switch / widens a stop / double-places an order. Report findings only."
- **Never let one agent both write and approve** a money module in the same session.
- Integration work (M4, M35) goes to your strongest model tier; boilerplate (fixtures, dashboards, data plumbing) can go to a cheaper tier.
- End every session by updating `docs/DECISIONS.md` (what was decided and why) — this is the cross-session memory that keeps 40 modules coherent.

## 6. Running This Build with Hyperagent

- Work **thread-per-wave** in Hyperagent; start each thread by attaching both docs (spec + this plan) so the agent has full context, and state the wave number.
- Use the sandbox to **prototype and unit-test modules before they touch your repo** (e.g. anomaly guard replay tests, cost-model contract-note checks, the vectorbt exit A/B, FNSPID/GDELT dataset pulls all run fine in-sandbox).
- Use the GitHub integration to commit reviewed module slices to your repo with meaningful messages per R6.
- Optionally save a "Trading OS Builder" named agent carrying the R-rules as its system prompt, so every future thread starts disciplined; a second "Auditor" agent (reviewer persona, §5) reviews money modules.
- What stays outside Hyperagent/AI entirely: broker onboarding, static-IP setup, SEBI algo registration, the black-box/RA legal determination, VPS provisioning credentials, and every human-approval gate click.

## 7. Known AI-Implementation Failure Modes (watch for these specifically)

| Failure mode | Where it will strike | Mitigation |
|---|---|---|
| Hallucinated vendor APIs | openalgo/aiomql/TradingAgents call signatures | R1: read source first; contract tests against real sandbox APIs |
| Silent fallbacks ("if API fails, use default") | margin check, VaR reads, signal cache | R4 fail-closed + chaos tests V2 |
| Test theater (tests that assert nothing) | money modules | Reviewer agent pass; mutation-test spot checks on M1/M4/M35 |
| Threshold hardcoding "just for now" | everywhere | L3 lint, V3 |
| Lookahead leakage in ML/backtests | M23, M25, M37 | Purged splits; feature-timestamp asserts; reviewer checks label windows |
| Scope creep ("improved" vendor logic) | /vendor | R8: wrap don't fork |
| Cross-session amnesia (re-deciding settled questions) | multi-week build | DECISIONS.md is loaded into every session |
| Config drift (code and yaml disagree) | after refactors | Pydantic schema is the single typed truth; CI validates yaml against it |

---

*Companion spec: `MASTER_BUILD_SPEC_V2.md`. If the two files ever disagree, the spec's §12 safety rules win, then the spec, then this plan.*
