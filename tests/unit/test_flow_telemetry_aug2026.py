"""MODULE 70 — order-flow PROXY telemetry. Invariants:

  - sign attribution: quote test first (ltp>=ask buy / ltp<=bid sell),
    tick test fallback, 0 when undecidable
  - dVOL comes from the cumulative counter, never negative, and the
    counter resets safely across the session-day boundary
  - the N-minute proxy-delta buckets track the day's max |delta| — the
    honest cousin of the reel's "biggest delta of the day" ledger
  - L1 imbalance only when depth sizes exist
  - every snapshot is appended to JSONL (recorder contract: records only)
  - feed integration: Mt5QuoteFeed and OpenAlgoQuoteFeed hand their raw
    snapshots to an attached recorder; without one, behavior is unchanged
"""
import json

import httpx

from src.ops.flow_telemetry import FlowTelemetry
from src.ops.live_feeds import Mt5QuoteFeed, OpenAlgoQuoteFeed

T0 = 1_786_600_000.0                      # fixed epoch inside one UTC day


def mk(tmp_path, bucket=300):
    return FlowTelemetry(tmp_path / "flow.jsonl", bucket_seconds=bucket)


class TestSignAttribution:
    def test_quote_test_buy_sell(self, tmp_path):
        ft = mk(tmp_path)
        r = ft.on_snapshot("X", ts=T0, ltp=101.0, bid=100.0, ask=101.0,
                           cum_volume=1000)
        assert r["sign"] == 1                       # at the ask = buyer
        r = ft.on_snapshot("X", ts=T0 + 1, ltp=100.0, bid=100.0, ask=101.0,
                           cum_volume=1500)
        assert r["sign"] == -1                      # at the bid = seller
        assert r["d_vol"] == 500.0

    def test_tick_test_fallback_no_quotes(self, tmp_path):
        ft = mk(tmp_path)
        a = ft.on_snapshot("X", ts=T0, ltp=100.0, cum_volume=10)
        assert a["sign"] == 0                       # first print: undecidable
        b = ft.on_snapshot("X", ts=T0 + 1, ltp=100.5, cum_volume=30)
        assert b["sign"] == 1 and b["d_vol"] == 20.0
        c = ft.on_snapshot("X", ts=T0 + 2, ltp=100.1, cum_volume=45)
        assert c["sign"] == -1

    def test_inside_spread_uses_tick_test(self, tmp_path):
        ft = mk(tmp_path)
        ft.on_snapshot("X", ts=T0, ltp=100.2, bid=100.0, ask=101.0)
        r = ft.on_snapshot("X", ts=T0 + 1, ltp=100.6, bid=100.0, ask=101.0)
        assert r["sign"] == 1                       # inside spread, uptick


class TestVolumeCounter:
    def test_dvol_never_negative_on_counter_reset(self, tmp_path):
        ft = mk(tmp_path)
        ft.on_snapshot("X", ts=T0, ltp=1.0, cum_volume=5000)
        r = ft.on_snapshot("X", ts=T0 + 1, ltp=1.0, cum_volume=100)  # reset
        assert r["d_vol"] == 0.0

    def test_day_rollover_resets_state(self, tmp_path):
        ft = mk(tmp_path)
        ft.on_snapshot("X", ts=T0, ltp=1.0, bid=0.9, ask=1.0, cum_volume=100)
        next_day = T0 + 86_400
        r = ft.on_snapshot("X", ts=next_day, ltp=1.1, bid=1.0, ask=1.1,
                           cum_volume=50)
        assert r["signed_flow_day"] == 0.0          # fresh counter, and the
        assert r["d_vol"] == 0.0                    # first print seeds prev_cum


class TestBucketLedger:
    def test_day_max_abs_bucket_delta(self, tmp_path):
        ft = mk(tmp_path, bucket=60)
        ft.on_snapshot("X", ts=T0, ltp=2.0, bid=1.9, ask=2.0, cum_volume=0)
        ft.on_snapshot("X", ts=T0 + 10, ltp=2.0, bid=1.9, ask=2.0,
                       cum_volume=70_000)           # +70k in bucket 1
        st = ft.day_state("X")
        assert st["day_max_abs_bucket_delta"] == 70_000.0
        # next bucket: smaller delta must NOT lower the day max
        ft.on_snapshot("X", ts=T0 + 70, ltp=1.9, bid=1.9, ask=2.0,
                       cum_volume=75_000)
        st = ft.day_state("X")
        assert st["bucket_delta"] == -5_000.0
        assert st["day_max_abs_bucket_delta"] == 70_000.0

    def test_l1_imbalance_only_with_depth(self, tmp_path):
        ft = mk(tmp_path)
        r = ft.on_snapshot("X", ts=T0, ltp=1.0, bid=0.9, ask=1.0)
        assert r["l1_imbalance"] is None
        r = ft.on_snapshot("X", ts=T0 + 1, ltp=1.0, bid=0.9, ask=1.0,
                           bid_qty=300, ask_qty=100)
        assert r["l1_imbalance"] == 0.5             # (300-100)/400


class TestRecorderContract:
    def test_every_snapshot_appends_jsonl(self, tmp_path):
        ft = mk(tmp_path)
        for k in range(5):
            ft.on_snapshot("X", ts=T0 + k, ltp=1.0 + k * 0.01, cum_volume=k)
        lines = (tmp_path / "flow.jsonl").read_text().strip().splitlines()
        assert len(lines) == 5
        assert all("PROXY" not in ln for ln in lines)  # data rows, not prose
        rec = json.loads(lines[-1])
        assert set(rec) >= {"ts", "symbol", "ltp", "d_vol", "sign",
                            "signed_flow_day", "bucket_delta"}


# ---------------------------------------------------------------- feeds

def mt5_transport(vol_holder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/tick/"):
            vol_holder["v"] += 3
            return httpx.Response(200, json={
                "symbol": "EURUSD", "bid": 1.1000, "ask": 1.1002,
                "last": 1.1002, "volume": vol_holder["v"],
                "time": 1786600000})
        if request.url.path.startswith("/candles/"):
            return httpx.Response(200, json=[
                {"ts": 1786500000 + i * 86400, "o": 1.1, "h": 1.11,
                 "l": 1.09, "c": 1.1} for i in range(20)])
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_mt5_feed_hands_snapshots_to_recorder(tmp_path):
    holder = {"v": 100}
    feed = Mt5QuoteFeed(["EURUSD"], base_url="http://bridge",
                        client=httpx.AsyncClient(
                            transport=mt5_transport(holder),
                            base_url="http://bridge"),
                        min_gap_s=0.0)
    feed.flow = FlowTelemetry(tmp_path / "mt5_flow.jsonl")
    await feed.tick_once()
    await feed.tick_once()
    lines = (tmp_path / "mt5_flow.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[-1])
    assert rec["symbol"] == "EURUSD" and rec["bid"] == 1.1
    assert rec["d_vol"] == 3.0                      # cum counter delta
    assert rec["sign"] == 1                         # ltp == ask -> buyer


def openalgo_transport():
    ttq = {"v": 10_000}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/multiquotes"):
            ttq["v"] += 250
            return httpx.Response(200, json={
                "status": "success",
                "results": [{"symbol": "RELIANCE",
                             "data": {"ltp": 1310.5, "bid": 1310.0,
                                      "ask": 1310.5, "volume": ttq["v"]}}]})
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={
                "status": "success",
                "data": [{"timestamp": "2026-08-01", "open": 1, "high": 2,
                          "low": 1, "close": 1.5, "volume": 10}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


async def test_openalgo_feed_hands_snapshots_to_recorder(tmp_path):
    feed = OpenAlgoQuoteFeed(["RELIANCE"], base_url="http://hub",
                             apikey="k", min_gap_s=0.0,
                             client=httpx.AsyncClient(
                                 transport=openalgo_transport(),
                                 base_url="http://hub"))
    feed.flow = FlowTelemetry(tmp_path / "oa_flow.jsonl")
    await feed.tick_once()
    await feed.tick_once()
    lines = (tmp_path / "oa_flow.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[-1])
    assert rec["symbol"] == "RELIANCE"
    assert rec["d_vol"] == 250.0                    # TTQ delta per snapshot
    assert rec["sign"] == 1                         # ltp at the ask

async def test_feeds_without_recorder_are_unchanged(tmp_path):
    holder = {"v": 100}
    feed = Mt5QuoteFeed(["EURUSD"], base_url="http://bridge",
                        client=httpx.AsyncClient(
                            transport=mt5_transport(holder),
                            base_url="http://bridge"),
                        min_gap_s=0.0)
    out = await feed.tick_once()
    assert "EURUSD" in out and feed.flow is None
