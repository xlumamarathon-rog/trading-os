# DECISIONS.md — judgment calls made during the agentic build (R10 ledger)

1. **VaR quantile convention**: ceil(alpha*n) order statistic so an exact 5% tail
   lands ON the tail (caught by test, Wave 3).
2. **Percentile mid-rank for ties**: flat vol history sits at 0.5, preventing
   false SHOCK classification (caught by test, Wave 3).
3. **GARCH**: `arch` used when installed; EWMA (lam=0.94) is the tested in-repo
   fallback so a missing wheel can never silently remove vol forecasting.
4. **Kill switch fail-closed scope**: Redis errors AND missing VaR cache AND
   margin-API loss AND crashing injected pre-checks all reject orders. No
   fail-open path exists; config schema only accepts `halt`.
5. **Retry semantics**: UNKNOWN state is never retryable; only broker-confirmed
   absence (FAILED_NOT_PLACED) allows a retry, always under a NEW client id.
6. **Exit stops**: distance = max(k_sl*ATR, structure); hard-% cap deferred to
   position sizer (which already caps notional + gap risk) — noted as
   SPEC-QUESTION, resolved as "sizer owns capital caps, exit owns price stops".
7. **Anomaly baselines**: primed externally in Wave 2/3; EWMA self-priming is
   deploy work wired to M34 outputs on the VPS.
8. **DSR**: implemented as probability (Bailey & LdP), Acklam ppf; trial variance
   is an explicit parameter — scans must pass their real trial counts.
9. **Regime filter**: regimes with <3 occurrences inside covered data count as
   FAILED evidence; eras before data coverage are excluded from the denominator.
10. **M37 fusion**: action enum contains no resume/override action — Tier-0
    supremacy is enforced by construction, not by convention.
11. **Cockpit**: backend gateway fully tested; SPA is a scaffold (Node/Tauri
    toolchain is deploy-side). Client carries zero order logic (spec §12.11).
12. **Sandbox constraints recorded**: Python 3.9 compatible (target 3.11),
    docker compose validated on VPS, real broker contract notes pending for
    the ≤1% cost-model validation, 2-week paper gates run on the VPS.
