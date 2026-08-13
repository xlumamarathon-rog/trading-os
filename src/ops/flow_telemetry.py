"""MODULE 70 — Order-Flow Proxy Telemetry (Aug 2026).

RECORDS, NEVER TRADES. This module exists because of the Aug-2026 orderflow
research verdict (ledger + docs/EXECUTION_AUDIT_AUG2026.md context):

  - Signed order flow moving price is real, peer-reviewed science (Cont,
    Kukanov & Stoikov 2014: OFI explains ~65% of contemporaneous mid moves;
    trade imbalance alone ~32%). The FOOTPRINT PATTERN layer sold on social
    media (delta divergence, absorption, stacked imbalances) has NO published
    test anywhere.
  - On this stack a true footprint is impossible: NSE retail feeds are
    1-second SNAPSHOTS with cumulative volume (no trade tape, no aggressor
    flag — NSE Level 1/2/3 spec has no trade messages), and MT5 spot FX is
    OTC (no executed-deal data at all; tick_volume counts quote updates).
  - What IS honest: a 1-second-resolution order-flow PROXY, recorded live.
    Per snapshot: dVOL = delta of cumulative volume, signed by the quote
    test (ltp at/above ask = buyer-aggressed, at/below bid = seller-
    aggressed, else tick test), bucketed into N-minute proxy-delta bars,
    plus L1 book imbalance when depth quantities are available.
  - Every depth/quote stream here is live-only with zero history at any
    price tier — a year of self-recorded snapshots cannot be bought later.
    Recording is therefore the single highest-leverage act; analysis comes
    after data exists.

Named hypotheses this dataset will eventually adjudicate (pre-registered in
the ledger, NOT tradeable until then):
  H-GAP:  closing-window signed flow -> overnight gap (grounded in NY Fed
          SR 917: overnight drift tied to imbalance at the prior close).
  H-EXEC: entry-time book imbalance -> short-horizon adverse selection on
          our own fills (execution timing, not signal).

The proxy's attribution error is bounded but unquantifiable (many exchange
trades collapse into one snapshot) — so records carry the raw inputs too,
and nothing downstream may call this "footprint" or "delta" without the
word PROXY. No trading code may import this module's output.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

UTC = dt.timezone.utc


class FlowTelemetry:
    """Per-symbol signed-flow proxy state + append-only JSONL recorder."""

    def __init__(self, out_path: str | Path, bucket_seconds: int = 300) -> None:
        self.out = Path(out_path)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.bucket_seconds = int(bucket_seconds)
        self._st: dict[str, dict] = {}

    # ------------------------------------------------------------- intake

    def on_snapshot(self, symbol: str, *, ts: float, ltp: Optional[float],
                    bid: Optional[float] = None, ask: Optional[float] = None,
                    bid_qty: Optional[float] = None,
                    ask_qty: Optional[float] = None,
                    cum_volume: Optional[float] = None) -> Optional[dict]:
        """Ingest one quote/depth snapshot; returns the record written."""
        if ltp is None:
            return None
        day = dt.datetime.fromtimestamp(ts, UTC).date().isoformat()
        st = self._st.get(symbol)
        if st is None or st["day"] != day:                 # new session-day
            st = {"day": day, "prev_ltp": None, "prev_cum": None,
                  "signed_flow": 0.0, "bucket": None, "bucket_delta": 0.0,
                  "day_max_abs_bucket_delta": 0.0}
            self._st[symbol] = st

        # ---- volume delta from the cumulative counter (NSE TTQ style)
        d_vol = 0.0
        if cum_volume is not None:
            if st["prev_cum"] is not None and cum_volume >= st["prev_cum"]:
                d_vol = float(cum_volume - st["prev_cum"])
            st["prev_cum"] = float(cum_volume)

        # ---- aggressor sign: quote test first, tick test as fallback
        sign = 0
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            if ltp >= ask:
                sign = 1
            elif ltp <= bid:
                sign = -1
        if sign == 0 and st["prev_ltp"] is not None:
            if ltp > st["prev_ltp"]:
                sign = 1
            elif ltp < st["prev_ltp"]:
                sign = -1
        st["prev_ltp"] = float(ltp)

        signed = sign * d_vol
        st["signed_flow"] += signed

        # ---- N-minute proxy-delta buckets (the honest cousin of the
        #      reel's per-candle delta ledger)
        bucket = int(ts // self.bucket_seconds) * self.bucket_seconds
        if st["bucket"] != bucket:
            st["bucket"] = bucket
            st["bucket_delta"] = 0.0
        st["bucket_delta"] += signed
        st["day_max_abs_bucket_delta"] = max(st["day_max_abs_bucket_delta"],
                                             abs(st["bucket_delta"]))

        # ---- L1 book imbalance when depth sizes exist
        imb = None
        if bid_qty is not None and ask_qty is not None and (bid_qty + ask_qty) > 0:
            imb = (bid_qty - ask_qty) / (bid_qty + ask_qty)

        rec = {"ts": round(ts, 3), "symbol": symbol, "ltp": ltp,
               "bid": bid, "ask": ask, "d_vol": d_vol, "sign": sign,
               "signed_flow_day": round(st["signed_flow"], 2),
               "bucket": bucket, "bucket_delta": round(st["bucket_delta"], 2),
               "day_max_abs_bucket_delta": round(st["day_max_abs_bucket_delta"], 2),
               "l1_imbalance": round(imb, 4) if imb is not None else None}
        with self.out.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    # ------------------------------------------------------------- reads

    def day_state(self, symbol: str) -> Optional[dict]:
        st = self._st.get(symbol)
        if st is None:
            return None
        return {"day": st["day"], "signed_flow": round(st["signed_flow"], 2),
                "bucket_delta": round(st["bucket_delta"], 2),
                "day_max_abs_bucket_delta": round(st["day_max_abs_bucket_delta"], 2)}

    def status(self) -> dict:
        return {"kind": "flow_telemetry_proxy", "symbols": sorted(self._st),
                "bucket_seconds": self.bucket_seconds, "out": str(self.out),
                "note": "PROXY telemetry — records only, never trades"}
