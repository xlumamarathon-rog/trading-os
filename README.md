# Trading OS

Open-source **Aladdin-class trading system** — India (NSE/BSE/MCX via OpenAlgo) + international **forex & crypto CFDs via MT5** — built spec-first by AI agents with hard test gates.

> ⚠️ **Not live-trading ready.** All modules are code-complete and tested, but the wall-clock gates (2-week paper trading, EOD reconciliation streak), VPS deployment, vendor-API verification, ML training runs, and SEBI registration remain — see `progress.md` for the honest deploy list and `docs/MASTER_BUILD_SPEC_V2.md` §12 for the safety rules that gate live capital.

## Status

| | |
|---|---|
| Tests | **200 passed / 0 failed** |
| Waves | all 8 build waves complete ✅ |
| Modules | 44 / 45 code-complete + tested (M45 cockpit SPA = scaffold; backend gateway done) |
| Remaining | deploy-time work only — see `progress.md` (VPS infra, vendor glue verification, ML training runs, paper-trade gates, SEBI registration) |

**What's proven so far (by tests, not promises):**
- Kill switch is **fail-closed** — Redis down ⇒ halted; dual-flag sentinel survives flag loss; chaos-tested mid-cancel failures
- A fail-open configuration is **structurally impossible** (config schema rejects it)
- Position size can **never exceed the 5% cap** (500-case property test); 3×ATR gap-survival bound; after-cost gate
- The double-order bug is **structurally impossible** (retry only from confirmed-absent-at-broker)
- Kill-switch check is the **first awaited call** in the order router — proven by AST inspection (lint L2)
- Anomaly guard fires on a flash crash in **<100ms**, zero false triggers on 500-tick normal walk, and has **no API pathway that can touch protective stops**

## Layout

```
config/master.yaml    every tunable (zero magic numbers in code)
src/core/             kill_switch · order_router · order_state_machine · position_sizer
                      margin_checker · transaction_cost_model · connection_manager · config_loader
src/intel/            anomaly_guard (Tier-0 shock reflex)
tests/                88 unit + chaos tests (test-first, per module)
scripts/lint_rules.py CI rules L1/L3/L5 (broker-import allowlist, literal scan, except-pass ban)
docs/                 MASTER_BUILD_SPEC_V2.md (the WHAT) · AGENTIC_BUILD_PLAN.md (the HOW)
progress.md           per-module build report — what's done, tested, next
```

## Run the tests

```bash
pip install -r requirements.txt
python -m pytest tests/ --cov=src
python scripts/lint_rules.py
```

## How this is built

Every module: *read spec → write tests from acceptance criteria → implement to green → lint → commit*. Money modules are test-first (rule R2), fail-closed by default (R4), and the safety rules in spec §12 override everything — including the prompts of the AI agents building it.

## Compliance notice

India algo trading is governed by SEBI circular **SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013** (Feb 2025) — algo registration, per-order Algo IDs, static IP, and a black-box/Research-Analyst determination are required before live deployment (spec §13). Offshore MT5 CFD trading carries **FEMA exposure for Indian residents** — documented, not mitigated, in spec §13.
