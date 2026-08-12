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
   **Set `MT5_SERVICE_TOKEN`** (same value on the Linux core and this service): the
   `/order` and `/position/*` endpoints place and close REAL orders, so they require
   the `X-MT5-Auth` header — TLS + private network is not the only line of defense.
   Without the token set the guard is a no-op (dev only); in production it is mandatory.
   **Verify:** `/health` from Linux core shows `terminal_connected: true`; ping-to-broker <2ms;
   an `/order` call WITHOUT the header returns 401.

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

7. **Pre-flight:** `python3 scripts/go_live_check.py` — one command, PASS/FAIL table over
   every automated clause (config, secrets, tests, lint, gate evidence, audit chain).
   It cannot be green until the three human items are done — by design.
8. `python -m src.app --mode live` — starts ONLY if the gate passes, and boots
   **SAFE-STARTED**: entries are PAUSED until an operator clicks *Resume entries* in the
   cockpit (`POST /control/resume_entries`, audited). A fresh live process never trades on its own.
9. **Live ramp (code-enforced):** for the first `live_ramp.days` (default 5) live days,
   position size is capped at `live_ramp.max_position_pct` (default 1% vs the normal 5%).
   The EOD worker advances `live_days_completed` only on CLEAN reconciliation days.
8. First live week: minimum lot sizes, kill-switch drill on day 1 (trigger + unlock via
   cockpit), verify contract notes against MODULE 40 within 1% (5 real notes).
12. Scale only after a clean live week AND the rule/model approval discipline (M24/M38)
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


---

## India real-time data feed — the daily runbook (MODULE 68, Aug 2026)

The engine reads india quotes from YOUR OpenAlgo hub (`FEED=openalgo` or
`FEED=live`): batched `POST /api/v1/multiquotes` every ~1.5s (hub rate
limit is 50/s — we use ~0.7/s), daily bars from `/api/v1/history`
(interval "D"). Fail-soft chain: hub → Yahoo → replay; the cockpit's
Settings page shows which layer is serving (a red DEGRADED chip means
fix the hub, the system is meanwhile running on Yahoo).

**Broker verdict for the data feed (researched 2026-08-12, ledger):**
Angel One = free real-time NSE websocket + the only DOCUMENTED headless
re-login (clientcode+PIN+TOTP POST — no browser). Dhan charges ₹499/mo
for data API; Zerodha ₹500/mo; both need daily browser logins. Fyers is
free but its unattended refresh flow is grey-zone. Upstox free (browser
OAuth daily; its 1-year Analytics Token is read-only and can't drive
OpenAlgo).

**What breaks every morning:** OpenAlgo expires ALL sessions at 03:00 IST
(SESSION_EXPIRY_TIME) and the broker token dies on its own schedule
(Upstox 03:30, Zerodha 06:00, Fyers EOD, Dhan +24h). From then until you
re-auth, /api/v1/quotes returns auth errors — the engine degrades to
Yahoo automatically and says so.

**Daily routine (~08:45 IST, before the 09:15 open):**
1. Open the OpenAlgo web UI → log in.
2. Connect broker (TOTP for angel/dhan; browser redirect for others).
3. Wait for the master-contract download to finish.
4. Health check: `curl -X POST localhost:5000/api/v1/quotes -H 'Content-Type: application/json' -d '{"apikey":"<key>","symbol":"RELIANCE","exchange":"NSE"}'`
5. Confirm the cockpit Settings page shows `openalgo_hub` (not DEGRADED).

Do NOT script browser logins or undocumented auth endpoints (ToS/2FA
risk). Angel's loginByPassword is the one legitimately automatable path
if unattended mornings become necessary.
