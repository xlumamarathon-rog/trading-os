# DEPLOY.md — From this repo to real paper trading (production runbook)

> Order matters. Every step has a verification. Live mode is impossible until the
> paper evidence exists — `src/app.py` enforces it (`LiveGateError`), this is not advisory.

## Phase A — Infrastructure (day 1)

1. **Linux core VPS** — AWS Mumbai `ap-south-1`, t3.large+, Ubuntu 22.04, static Elastic IP.
   ```bash
   git clone https://github.com/xlumamarathon-rog/trading-os && cd trading-os
   cp .env.example .env        # fill EVERY value; never commit .env
   bash scripts/clone_vendors.sh
   pip install -r requirements.txt fastapi arch
   # heavier deploy libs (py3.11): pip install riskfolio-lib skfolio vectorbt stumpy \
   #   zipline-reloaded empyrical-reloaded quantstats jugaad-data openchart openalgo aiomql
   docker compose up -d        # redis + timescaledb(+pgvector)
   python -m pytest tests/ -q && python scripts/lint_rules.py   # must be green ON the VPS
   ```
   **Verify:** `docker compose ps` healthy; full suite green; `ping` NSE round-trip 5–15ms.

2. **Windows MT5 VPS** — provision in your broker's Equinix site (ask support: LD4/NY4/AMS).
   Install MT5 terminal, log in, then Python 3.11 + `pip install aiomql fastapi uvicorn`.
   Deploy `mt5_service/`; run `uvicorn mt5_service.app:app` behind the private network + TLS.
   **Verify:** `/health` from Linux core shows `terminal_connected: true`; ping-to-broker <2ms.

3. **OpenAlgo server** (Linux core, localhost): follow vendor/openalgo docs — connect YOUR
   broker (Dhan/Shoonya/Fyers/Zerodha), whitelist the static IP with the broker
   (mandatory since Apr 2025), set `broker.india.base_url` to the local OpenAlgo URL.
   **Verify:** OpenAlgo dashboard logs in to broker; `/api/v1/positionbook` responds.

## Phase B — Paper trading (weeks 1–2 minimum, THE gate)

4. Start paper mode (the paper server speaks the exact broker schemas — same code paths):
   ```bash
   python -m src.app --mode paper
   ```
   Point `connection_manager` base URLs at the paper server; feed it live ticks from the
   broker WebSocket (read-only market data is fine in paper mode).
5. Daily, automatically: EOD reconciliation runs; `paper_report` writes the day's evidence
   and advances `gate_state.json` — **a day only counts if reconciliation is CLEAN; a dirty
   day resets the 5-day streak.**
6. Watch in the cockpit/dashboard: fills with real slippage+costs, stops resting and
   ratcheting, anomaly events, worker heartbeats (a dead worker alerts via Telegram).

**Paper exit criteria (all enforced by the live gate):**
- `paper_days_completed >= 14`
- `clean_reconciliation_streak >= 5`
- SEBI Feb-2025 checks recorded passed (MODULE 17 — incl. exchange Algo ID registration
  and the black-box/RA determination made with a professional)
- `broker.india.static_ip_confirmed: true` in config
- `human_ack` in gate_state.json set to exactly: `I ACCEPT LIVE TRADING RISK`

## Phase C — Live (only after B; start at minimum size)

7. `python -m src.app --mode live` — starts ONLY if every gate above passes.
8. First live week: minimum lot sizes, kill-switch drill on day 1 (trigger + unlock via
   cockpit), verify contract notes against MODULE 40 within 1% (5 real notes).
9. Scale only after a clean live week AND the rule/model approval discipline (M24/M38)
   has been exercised at least once end-to-end.

## Operations reference

| Task | How |
|---|---|
| Emergency stop | Cockpit big red button (phrase: `KILL ALL POSITIONS`) / `POST /control/kill` / Telegram alert channel confirms |
| Unlock after halt | `POST /control/unlock` with your `KILL_SWITCH_UNLOCK_PHRASE` |
| Worker died | Supervisor restarts ≤5×, then alerts; check `heartbeat:*` keys + cockpit events |
| Audit integrity | `JsonlAuditLog(path).verify_chain()` — tamper ⇒ `ChainTamperedError` |
| Upstream API drift | CI + re-run `tests/unit/test_broker_payloads.py` after any OpenAlgo/aiomql update; re-verify against vendor/ source (R1) |
| Backups | Nightly: TimescaleDB dump + `data/*.jsonl` (audit, ledger, gate) to S3 |

## Legal checklist before Phase C (spec §13)
SEBI algo registration via broker (Algo IDs) · black-box/RA determination ·
5-year audit retention confirmed · FEMA exposure on offshore MT5 acknowledged (operator's decision).
