# Execution Forensics Audit — August 2026

Branch: `research/worldbest-aug2026`. Question audited: **is the engine taking
trades the wrong way — mangling entries/exits — and what execution gaps exist
vs documented best practice?** Method: an instrumented, observer-only re-run of
the certified harness proven **bit-identical** to the certified artifacts
(results/exits/equity/audit rows all matching for oops/tsmom/crsi india_wide),
plus per-trade traces reconciled to raw bars, plus three parallel evidence
reviews (mean-reversion canon, trend canon, volume/candles/trendlines
literature). Reproduction scripts referenced at the end. Repo was not modified
by the audit itself.

## Executive verdict

1. **Entries are clean.** All 19 signals evaluated over 49,400 guarded calls
   with a proxy that raises on any read of `bars[i:]` — zero violations. Fill
   indexing verified trade-by-trade (decision on bars[:i], fill at
   bars[i].open + adverse slippage). Direction/stop-side integrity: 0/344
   mismatches.
2. **Cash accounting is honest and pessimistic.** Broker fills gap-throughs at
   the gapped open (worse), charges adverse slippage on every fill, conserves
   quantity exactly (0 mismatches in 325 closed trades). EOD reconciliation
   CLEAN is meaningful.
3. **But seven defects sit between the strategy and the numbers** — two
   lookahead/unit bugs that shape every stop and most exits, and five
   telemetry/path defects that make the reported R-tape systematically rosier
   than the cash. None of them touch the live gate; all of them touch what we
   *believe* about the sleeves.
4. **The user's named suspects — trendlines, volume, candlestick patterns —
   are NOT the gap.** The systematic literature grades them the three
   weakest-evidenced families in technical analysis (details §5). The real
   gaps are post-entry trade management (vs both canons) and sizing/cost
   structure.

## §1 Confirmed defects (ranked by decision impact)

### BUG-1 — Lookahead in ATR + regime that set every stop, trail width, and R denominator
`scripts/research_replay.py:291-292`: `atr14(bars, i)` sums TR over
`[i-13, i]` — **includes the fill bar's own H/L/C**, unknowable at the open —
and `real_regime(bars, i)` computes its SHOCK test from bar i's own range
(trend components are clean). Consumers: the initial stop (`:317`), the
sizer's `risk_per_unit`, `pos.atr` (frozen for the life of the trade), and the
trail-multiplier regime each bar (`:342`).
- Stop distances off by mean |3.74|%, max **+23.1%** vs honestly-computable ATR.
- 12/12 SHOCK (0.75×ATR) trail ratchets in oops india_wide occurred on days
  whose lag-1 regime was NOT SHOCK; 6 trades worth **+11.89R of the run's
  +26.71R** exited at levels a lag-1 engine never reaches.
- End-to-end A/B with lagged inputs (identical RNG, signals): oops
  **Sharpe 3.02 → 1.04, MDD 0.88% → 3.84%** (return went UP, 4.30% → 7.67% —
  the lookahead inflates the risk-adjusted headline, not the return).
- Sleeves that read `regime` in the signal (**tsmom_f, donchian, improved*,
  accurate***) have this lookahead reaching the entry decision itself.
- Fix: `atr14(bars, i-1)`, `real_regime(bars, i-1)`; refresh `pos.atr` per
  completed bar, lookahead-free.

### BUG-2 — Time-stop unit is the caller's sub-bar, not a daily bar
`exit_manager.py` counts one "bar" per `on_bar()` call; the replay harnesses
call it 4× per daily bar, so `max_bars_no_progress: {india: 20}` behaves as
**5 trading days**; the crsi research override "5" was **1.25 days** (22 of 35
time stops fired one bar after entry). Worse: callers disagree —
`run_paper.py` calls `on_bar` per TICK, so the paper/live runtime interprets
the same 20 as 20 ticks. This exit fires on **53% / 58% / 95%** of closed
trades (oops/tsmom/crsi india_wide). At the config's face value (80 sub-bars ≡
20 days) oops' exit mix flips 72 time/65 stop → 2/103 and headline
4.30%/0.88%/3.02 → 3.48%/1.86%/2.26 — sleeve rankings depend on a parameter
whose unit is undefined.
- Fix: count completed reference bars (pass a bar-closed flag) or express the
  knob in minutes; make all callers agree; regression-test the unit.

### BUG-3 — Stop exits booked in telemetry at the stop price while the broker fills the gap
`exit_manager.py` books `_exit(pos, pos.stop, "stop_hit")`; the broker fills
at the gapped price + slippage. Every stop exit's telemetry R is optimistic:
mean **+0.097R × 65** (oops), **+0.114R × 79** (tsmom); worst single trade
−1.000R reported vs **−1.885R** actual (ICICIBANK short, 2026-04-08 gap open).
Cash is correct; the tape is not.

### BUG-4 — `trades_r` / `win_rate_pct` / `avg_realized_r` are not the P&L distribution
realized_r = final-exit-of-remainder vs initial risk; excludes partials,
costs, and BUG-3 slippage. Portfolio effect on india_wide:
| | oops | tsmom |
|---|---|---|
| Σ reported R | +26.71R | +4.93R |
| Σ after-cost cash R | **+13.95R** | **−8.66R** (sign flip) |
| win rate reported vs cash | 48.2% vs 49.6% | 43.6% vs 47.3% |
13 partial-then-breakeven **winners** (cash positive) are recorded as 0.000R
non-wins. **Anything consuming `trades_r` inherited the optimism — including
the M66 risk optimizer (empirical Kelly, bootstrap drawdown) and the M69
challenge math.** Those numbers must be re-derived after the telemetry fix.

### BUG-5 — Partials fill at the sub-bar close, never worse than their trigger
73/73 oops partials overshot the +1R/+2R ladder (mean +0.194R, max +1.468R) =
**+4.05R** of cash optimism (tsmom +3.25R). Fix: fill at the ladder price when
the sub-bar range contains it.

### BUG-6 — Synthetic intrabar path visits the favourable extreme first on adverse-close bars
`way = [o,l,h,c] if c >= o else [o,h,l,c]` — on stop-out days the favourable
extreme is visited before the stop (45:6 oops, 56:8 tsmom), so partials/
ratchets bank at prices a pessimistic path never reaches. Fix for realism
runs: adverse-first ordering for open positions, or report both bounds.

### BUG-7 — `mfe_captured_pct` is unbounded and its aggregate is meaningless
Ratio realized_r/mfe_r with an arbitrarily small denominator (observed
−36,728%); the 0.0 branch conflates full-R losers with flat trades; the mean
(−166.9) is reported while the median is 0.0. Replace with giveback
(mfe_r − realized_r) in R + capture ratio gated at mfe_r ≥ 0.5R.

Minor: `never_widen_stop: true` in master.yaml is never read (invariant is
hardcoded — fail-safe, but the flag is decorative).

## §2 Verified clean (do not "fix")
Signals lookahead-free (49,400 guarded calls) · fill at next open + adverse
slippage · direction/stop-side integrity 344/344 · same-bar stop-first with
early return · entry-bar stop-outs taken (2/2) · gap-aware pessimistic broker
fills · exact quantity conservation · R vs initial risk after trailing ·
never-widen enforced in code.

## §3 Trade management vs the canon (evidence-graded)

### Mean reversion (Connors/Alvarez lineage)
- **Stops hurt MR, monotonically.** Connors STSTW ch.6 (236,237 trades,
  1995-2007): no stop = 69.8% win/0.58%; 1% stop = 26.9%/0.19%; 3% = 47.3%;
  5% = 57.5%. **NSE replication** (1,640 NSE stocks 2004-2017): no stop =
  71.3% win/1.34%/29.1 CAR; 1% stop = 23.5% win/7.2 CAR; 3% = 42.4% win with
  WORSE drawdown. Our regime-tightened trail (0.75-1.25×ATR ≈ 1.9-3% on NSE
  names) sits exactly in that kill zone — **our observed 30-42% win rates
  match the canon's predicted band for 1-3% stops.** Kaminski & Lo (JFM 2014):
  stopping premium is negative under mean reversion by construction.
- **The "5-bar" time stop was a misread** — the 3-4-day figure comes from the
  n=49 SPX index strategy; the stock-universe no-stop mean hold is 7.74 bars.
  (And per BUG-2 our "5" was really ~1.25 days.) Alvarez: time stops help only
  as 7-10 bar backstops firing on ~10% of trades; ours fired on 95%.
- **Partials/breakeven/trailing:** Alvarez — scaling out = "large drop in CAR";
  profit targets "simply cut your profit"; trailing stops "greatly reduce
  returns and make drawdowns worse". No canon test of breakeven-at-+1R exists;
  all adjacent evidence negative. One exception in the record: 2×ATR10 stop
  paired with a FULL 3×ATR10 target, close-evaluated (+33% CAR) — nothing like
  our ladder.
- **Entry timing:** next-open is second-order (canon conflict on magnitude);
  the documented upgrade is a 1-day-only limit BELOW the signal close
  (Connors ch.8 ladder: −5% limit ≈ 3× expectancy; Alvarez 2021: +21% CAR).
- **Scope limit:** the no-stop conclusion is NOT licensed for forex/crypto
  (canon never tested there); honest NSE MR calibration: 62-70% win,
  0.3-1.0%/trade with signal-based exits.

### Trend following (Clenow/Carver/Turtles/AQR)
- **No signal-reversal exit path exists (0/188 tsmom exits)** — the exit the
  entire canon converges on ("a change of trend is the best stop loss" —
  Clare et al., JAM 2013; Davey's 567k-backtest study: stop-and-reverse beat
  all 14 alternative exits everywhere).
- **Trail widths 1.25/0.75×ATR are outside the canon's entire tested range**
  (Clenow 3×ATR(100) close-only; "Tuning the Turtle" 5; Wilcox&Crittenden 10,
  with 8-12 indistinguishable; Blox replication: 3×ATR = 100/100 tests losing,
  10×ATR = 100/100 winning). Clare et al.: tightening 12%→3% cost 7.1pp/yr
  and took Sharpe 0.54 → −0.11.
- **Partials 33%@+1R/33%@+2R cap tail capture at 34%** (−56% haircut on a
  +10R trade). <7% of trades drive ALL cumulative trend profit (Zarattini et
  al. 2025, 66k trades 1950-2024); every partial-taking combination reduced
  return in Reid's 1,432-trade grid. Canon winners average ~+12-17R in our
  units; our largest realized R ≈ +4.
- **Time stop:** absent from every canon trend system; Davey: 5-bar time exits
  much worse than 45-bar; our 20-30 "bars" (= 5-7.5 days after BUG-2) sits
  against a 63-day momentum signal — a 3-10× horizon mismatch.
- **Sizing:** the 5% notional cap bound **100% of 382 fills** → effective risk
  0.14-0.16% vs configured 1%. Carver's diversification-multiplier point: the
  identical mechanism turns "a 15% risk target into just 3% risk"; fix at
  portfolio level (vol targeting + diversification multiplier), never by
  raising per-trade risk while exits still truncate every tail.
  **Sequence: exits → partials → sizing.**
- Aligned & keep: next-open entry (canonical), 2×ATR initial stop (=Turtles),
  3.0×ATR strong-trend trail (=Clenow), never-widen.

## §4 Counterfactual — our entries, the legends' exits, same data
Long-only india_wide, no stops, honest ATR R units, no costs (analysis-only):
- **oops + Williams bailout exit (first profitable open): 94.3% win** — the
  legendary win rate reproduces on OUR entries — but earns +12.2R with worst
  MAE −5.76R (6 trades below −2R underwater). Engine cash on the same signal:
  +13.95R with the left tail capped at ~−1R. Win rate is an exit-style
  choice, not an edge measure; the engine's exits are competitive on money
  and dominant on tail risk.
- **crsi + Connors MA5 exit: 50% win, −6.9R** — worse than our engine. The
  crsi entry has no edge on this data under either exit style. Kill stands.

## §5 The user's named suspects: trendlines, volume, candles
Evidence review verdicts (peer-reviewed sources in the thread report):
- **Volume → direction: LORE.** 468-estimate meta-analysis (IRFA 2021): the
  volume→return effect is negligible after publication-bias correction; where
  signed structure exists it points the OPPOSITE way from trading lore (high
  volume → more reversal). Volume IS valuable as hygiene: liquidity screens,
  stale-bar detection, eligibility gates. **The fetcher currently discards
  Yahoo's volume array — fix for hygiene, expect no alpha.**
- **Candlestick patterns: LORE** on the strongest tests (randomized-OHLC
  bootstrap on DJIA; Japan itself; 349-stock samples). The one buried real
  finding (JBF 2015): the EXIT rule, not the pattern, determines
  profitability — which points back at §3.
- **Trendlines: redundant.** Chang-Osler: the best chart pattern in FX is
  dominated by simple MA rules — which this system already runs. Adding
  discretionary trendlines imports bias, not edge.
- **What the evidence DOES support adding** (ranked): 52-week-high proximity
  ranking (George-Hwang 2004, ~45bp/mo, pure OHLC); vol-regime gating of MR
  sleeves (Nagel 2012: reversal pays only when liquidity is scarce);
  overnight-vs-intraday return split (validated US, must test NSE); exit
  redesign per §3; volume kept for hygiene.

## §6 Numbers previously reported that this audit supersedes or taints
- oops india_wide "Sharpe 3.02 / MDD 0.88%" — lookahead-inflated; honest
  re-run ≈ 1.04 / 3.84% (return honest, actually better without lookahead).
- All win-rate/avg-R tables computed from `trades_r` (worldbest campaign
  leaderboards incl.) — telemetry-basis; cash win rates differ by −1.4 to
  +3.7pp on audited runs; tsmom india_wide R-sum flips sign in cash.
- M66 kelly/growth/monte-carlo and M69 challenge math (53.2% pass) consumed
  `trades_r` — re-derive after telemetry fix. Return/equity/reconciliation
  numbers and the 2008/COVID stress conclusions stand (cash-basis, pessimistic).

## §7 Pre-registered fix experiments (proposed, not yet run)
Each on a research branch, each with the bar declared BEFORE running; tsmom
certified equity must be re-baselined explicitly (these fixes change certified
numbers by design — that is their point):
1. **FIX-LOOKAHEAD**: lag ATR/regime to bars[:i]. Bar: all suites green; new
   baseline published with honest Sharpe/MDD; no sleeve promoted/demoted on
   the diff alone.
2. **FIX-TIMESTOP-UNIT**: bar-closed flag; config in daily bars; all callers
   agree. Bar: exit-mix distributions re-published per sleeve.
3. **FIX-TELEMETRY**: exit price = broker fill; emit qty-weighted after-cost
   R (`trades_r_cash`); keep old field for continuity. Bar: Σtrades_r_cash
   reconciles to equity within costs on every run.
4. **EXIT-STYLE EXPERIMENT (the big one)**: per-style exit profiles —
   MR sleeves: no partials, no breakeven, wide/no stop with 7-10 day
   backstop + signal exit; TF sleeves: no partials, 3×ATR floor trail +
   signal-reversal exit. Bar: cash expectancy > current per style on
   india_wide + fx + crypto, stress replays intact, honest multiplicity note.
5. **SIZING (after 4)**: portfolio vol targeting + diversification multiplier
   vs notional cap. Bar: cost drag < 1/3 of gross expectancy (Carver's speed
   limit) and MC drawdown within mandate.

Audit reproduction: /tmp/audit_trace/ (instrumented_run.py — bit-identical
replica; guard_lookahead.py; nolookahead_run.py A/B; ts80 unit A/B; per-trade
trace.jsonl). Web-canon source reports live in the thread ledger.
