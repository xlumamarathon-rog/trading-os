# OPERATOR.md — Running Trading OS day to day

The manual for the human on the other end of the kill switch. Every command,
phrase, and threshold here matches the code (`config/master.yaml`, `src/`).
Keep this open next to the cockpit.

> **The one rule that overrides all others:** if you are unsure, **KILL first, diagnose second.**
> A flat book costs you opportunity; a runaway book costs you capital. The kill switch is
> reversible in 30 seconds. A bad hour is not.

---

## 0. The vocabulary you must know cold

| Term | Meaning | Where |
|---|---|---|
| **HALTED** | No new orders; existing stops still rest at the broker | kill switch flag / sentinel file |
| **PAUSED (entries)** | New entries blocked, but positions still managed & stops still trail | `PAUSE_ENTRIES` (anomaly guard or manual) |
| **SAFE-START** | A fresh LIVE process boots PAUSED — it will not trade until you Resume | `src/runtime.py` |
| **Kill phrase** | `KILL ALL POSITIONS` (typed to confirm a kill) | cockpit / `POST /control/kill` |
| **Unlock phrase** | your `KILL_SWITCH_UNLOCK_PHRASE` from `.env` | `POST /control/unlock` |
| **Ack phrase** | `I ACCEPT LIVE TRADING RISK` (gate only) | `gate_state.json` |

---

## 1. Morning checklist (before the open — ~10 minutes)

Run top-to-bottom. **Do not clear any position for the day until all green.**

1. **Health** — cockpit footer shows `live`, not `gateway unreachable`. Every worker chip green:
   `var_worker · exit_manager · anomaly_guard · news_poll · reconciler · tick_feed`.
   A red chip = that worker is down → §6 *Worker down*.
2. **Not halted / not paused** — no red HALTED banner; entries not still paused from yesterday.
   (Live boots PAUSED by design — you Resume it deliberately in step 7, not by reflex.)
3. **Overnight reconciliation CLEAN** — last night's EOD report (`data/.../report_<date>.md`)
   shows `EOD reconciliation | CLEAN`. A MISMATCH = §6 *Reconciliation mismatch* **before trading**.
4. **Every open position has a stop** — positions table: no blank Stop cell.
   The reconciler alarms on naked positions, but eyeball it too.
5. **VaR gauge** not already amber/red — if VaR is near the 2% limit at the open, the day starts
   defensive; size will be throttled automatically, don't override.
6. **Broker + data feed live** — MT5 VPS `/health` shows `terminal_connected: true`;
   OpenAlgo dashboard logged in; ping to broker normal.
7. **Resume entries** (live only) — once 1–6 are green, click *Resume entries* in the cockpit
   (`POST /control/resume_entries`). This is the deliberate "I have eyes on it" action.
   During the first 5 live days the 1% ramp cap is in force — expected, leave it.

If any step is red and you can't fix it in minutes: **stay PAUSED**. A day not traded is free.

---

## 2. Reading the cockpit (what each panel is telling you)

- **Equity / Day P&L / costs** — day P&L red and approaching −3% of equity → the auto-kill is near
  (`auto_trigger_daily_loss_pct: 0.03`). Don't wait for it; if it's not a clean drawdown, kill manually.
- **VaR gauge** — green <60%, amber 60–90%, red >90% of the 2% limit. Red means the next
  entries will be heavily throttled or rejected. That's the system working, not a bug.
- **Positions · exit states** — the story of each trade:
  - `RISK_ON` (amber) = full initial risk, hasn't reached +1R yet. This is where losers get cut.
  - `BREAKEVEN` (blue) = past +1R, stop at entry, first partial booked — the trade is now free.
  - `TRAILING` (green) = past +2R, chandelier trailing the runner.
  - `R now` negative and `state` still RISK_ON on many rows = a broad adverse move → watch VaR.
- **Dealer gamma (GEX) map** — `amplify` (red) = dealers short gamma, moves get exaggerated;
  treat shock signals as more real. `dampen` (green) = moves get muted, especially near big strikes.
- **Workers** — any red chip is an incident, even if P&L looks fine (a dead `exit_manager` means
  stops aren't trailing).
- **Event feed** — red bars are anomaly/alert events; skim them every check-in.
- **Live-mode gate** — the 5 clauses; irrelevant once live, but confirms you're in the mode you think.

Refresh cadence: glance at session open, mid-session, and 30 min before close. You do **not**
need to watch tick-by-tick — the machine does that; you watch for *state* going wrong.

---

## 3. Alert responses (Telegram / cockpit event feed)

| Alert | What it means | Your move |
|---|---|---|
| `anomaly_guard <SYM>: velocity/spread/volume trigger — entries paused` | A shock hit; entries auto-paused for the cooloff (15 min) | **Nothing required** — this is the guard doing its job. Confirm positions' stops are resting. Investigate the cause; don't Resume early unless it was a false alarm. |
| `auto: daily loss … breached` / `auto: VaR … breached` | The **auto-kill already fired** — book is flat, system HALTED | Go to §4. Do NOT unlock until you understand the loss. |
| `WORKER DOWN (max restarts): <name>` | Supervisor gave up after 5 restarts | §6 *Worker down* — this is a real incident |
| `EOD … recon=MISMATCH` | Internal book ≠ broker book | §6 *Reconciliation mismatch* — resolve before next session |
| Telegram silent when you expected an alert | Alert channel may be down (it fails safe — trading continues) | Check `heartbeat:*` in Redis / cockpit; alerts are best-effort, the trade path is not affected |

**Alert hygiene:** an alert you ignore twice is an alert you've mentally muted. If something fires
repeatedly and benignly, that's a config-tuning task (raise the threshold via the approval flow),
not something to learn to ignore.

---

## 4. Kill / Unlock / Resume — the three controls

### KILL (stop everything)
- **Cockpit:** big red button → type `KILL ALL POSITIONS` → reason → CONFIRM.
- **API:** `POST /control/kill {"confirm":"KILL ALL POSITIONS","reason":"…"}` (operator token).
- **Effect:** `TRADING_HALTED` set (Redis + local sentinel), every open order cancelled, every
  position closed at market on all legs, all actions audited, alert fired.
- **When:** any doubt, any anomaly you don't understand, any suspected bad data, before any manual
  DB/config surgery, or on the operator's gut. Overuse is cheap; underuse is not.

### UNLOCK (clear a halt) — deliberate, never reflexive
- **Cockpit:** unlock panel → enter your `KILL_SWITCH_UNLOCK_PHRASE`.
- **API:** `POST /control/unlock {"confirm":"<your phrase>"}`.
- **Pre-conditions before you unlock — ALL of them:**
  1. You know *why* it halted (read the audit rows / alert).
  2. The cause is resolved or understood as benign.
  3. Reconciliation is clean (broker book == what you expect).
  4. Redis is reachable (unlock fails-closed if not — that's intentional).
- Unlocking clears the halt but entries may still be paused — you then Resume separately.

### RESUME ENTRIES (release SAFE-START / an anomaly pause)
- **Cockpit:** *Resume entries* → **API:** `POST /control/resume_entries`.
- Releases the entry pause; positions were being managed the whole time regardless.
- After an anomaly pause, prefer to let the cooloff expire naturally; only Resume early if you've
  confirmed the trigger was a false alarm.

---

## 5. Weekly rhythm — the approval reviews

The system proposes; **you ratify**. Nothing changes behaviour without your click (spec §12.4).

**Weekly (pick a fixed slot, e.g. Saturday):**
1. **Approvals inbox** — each pending rule/model carries its evidence (holdout p-value, Brier
   delta, after-cost P&L delta). Approve only if:
   - the evidence bar is met (it wouldn't be surfaced otherwise), AND
   - you understand *why* the change helps in words, not just numbers.
   When in doubt, leave it pending — an unapproved rule simply doesn't run.
2. **Rule auditor flags** — any active rule underperforming its shadow simulation after 90 days
   is flagged for deactivation. Same discipline to turn OFF as ON: read it, then decide.
3. **Model calibration** — if the news-reaction model self-demoted to abstain-mode (Brier drift),
   it's telling you it's confused. Don't force it back; investigate the regime change first.
4. **Cost reconciliation** — spot-check that charged costs match the broker's contract notes;
   drift here means the cost model needs a config update (feeds MODULE 40).

**Never** approve a batch of changes you haven't individually read because the inbox looks long.
The gate exists precisely for the tired-Friday-afternoon version of you.

---

## 6. Incident playbooks

### 🔴 Auto-kill fired (daily loss / VaR breach)
1. Breathe — the book is already flat, capital is protected.
2. Read the audit rows + alert: which trigger, what was open, what the P&L was.
3. Was it a clean market drawdown (strategy behaved, market moved) or a malfunction
   (bad data, a rule misfiring, an execution error)?
   - **Clean drawdown:** this is the system respecting your daily loss limit. Do not unlock and
     "make it back" today — that's how limits get blown. Resume next session.
   - **Malfunction:** keep it HALTED, fix the cause, run `pytest` + `go_live_check.py`, only then unlock.

### 🔴 Worker down (supervisor gave up)
1. `exit_manager` or `anomaly_guard` down = **safety-critical** → **KILL immediately**, then fix.
   (Stops still rest at the broker, but nothing is trailing or watching for shocks.)
2. `var_worker` down = the router will fail-closed on VaR reads (rejects entries) — safe, but fix it.
3. `tick_feed` / `news_poll` down = stale data → pause entries, investigate the feed/VPS.
4. Check the process, the VPS, and upstream (broker WS, OpenAlgo). Restart the service
   (`systemctl restart trading-os`). Confirm the heartbeat returns before resuming.

### 🔴 Reconciliation mismatch (internal book ≠ broker)
1. **Do not trade** on a book you don't trust. Pause entries at minimum; KILL if the mismatch is large.
2. Open the EOD report — which class: missing-at-broker, missing-internally, qty, or price?
   - **Missing at broker** (we think we have a fill they don't) — likely an UNKNOWN-state order that
     wasn't reconciled; check the order state machine / broker order book.
   - **Missing internally** (they have a fill we don't) — a manual trade on the broker, or a dropped ack.
   - **Qty/price** — partial-fill accounting or a slippage-beyond-tolerance fill.
3. Reconcile against the **broker's** book as truth. Correct internal state, re-run reconciliation
   to CLEAN. The clean-day streak resets — that's correct, evidence must be earned.

### 🔴 Broker / data-feed outage mid-session
1. Router already fails-closed (no data ⇒ no new orders). Your exposure is the open book.
2. Your **broker-resident stops still protect you** even if your VPS or feed is down — this is
   the whole point of resting stops at the broker.
3. If the outage is prolonged and you can reach the broker terminal directly, manage/flatten there;
   then reconcile the manual actions when the system returns.

### 🔴 Suspected bad market data (prices look wrong)
1. KILL — do not let the sizer/exits act on prices you don't trust.
2. Cross-check the symbol on an independent source (broker terminal, exchange site).
3. The tick feed already drops non-finite/≤0 ticks, and exits skip corrupt bars — but a *plausible
   but wrong* price (e.g. a stale quote) can still mislead. Trust your eyes over the feed here.

### 🔴 "It's doing something I don't understand"
KILL. Then read the audit log — every order and control action is there, hash-chained and in order.
The audit log is the source of truth for what actually happened; reconstruct from it, not from memory.

---

## 7. End of day
- The EOD worker runs automatically: reconcile → daily report → gate advance → Telegram summary.
- Read the one-line summary. CLEAN + expected P&L = nothing to do.
- Weekly: back up `data/*.jsonl` (audit, ledger, gate) — the audit chain is your legal record
  (5-year SEBI retention).

## 8. What you must NEVER do
- Never edit `gate_state.json`, the audit log, or `config/master.yaml` risk limits on a whim to
  "let a trade through." Those frictions are the system protecting you from yourself.
- Never unlock to chase a loss back the same session.
- Never approve rules/models in bulk without reading each.
- Never disable the anomaly guard or widen a stop manually — the code won't let you widen a stop,
  and there's a reason.
- Never run `--mode live` around the gate — `go_live_check.py` is the only sanctioned path.

---

*Escalation: for a reproduced software bug, capture the audit rows + timestamps and open an issue;
keep the system HALTED until resolved. For broker/exchange/regulatory matters, that's the broker's
support and your compliance advisor — not something to code around.*
