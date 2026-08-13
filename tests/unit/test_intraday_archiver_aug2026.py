"""Intraday archiver + MT5 history probe (Aug 2026). Invariants:

  - window chunking respects the verified Yahoo walls (1m: last ~30d in
    <=7d chunks; 5m/15m: single <=60d window) — NO test touches the network
  - merge is dedup-safe and resumable: overlapping re-runs add nothing,
    fresher bars win on timestamp collision, output stays sorted
  - a killed run never corrupts the archive (atomic replace)
  - manifest reports bars/sessions honestly from the stored data
  - history-probe bisection finds the exact availability boundary in
    O(log n) probes, sync and async twins agreeing
  - the bridge endpoint serves history_depth auth-gated and 503s when the
    terminal is down; the probe client writes per-symbol coverage
"""
import datetime as dt
import json

import httpx
import pytest

from mt5_service.app import create_mt5_service
from mt5_service.history_probe import (day_bounds_epoch, earliest_available,
                                       earliest_available_async)
from scripts.intraday_archiver import (INTERVALS, archive_symbol,
                                       chunk_windows, manifest_entry,
                                       merge_bars, parse_chart)

NOW = 1_786_600_000                            # fixed epoch


# ------------------------------------------------------------- chunking

class TestChunking:
    def test_1m_inside_30d_wall_in_7d_chunks(self):
        wins = chunk_windows(NOW, "1m")
        assert wins[0][0] == NOW - 29 * 86_400          # never past the wall
        assert wins[-1][1] == NOW
        assert all(p2 - p1 <= 7 * 86_400 for p1, p2 in wins)
        # contiguous, no holes: a lost chunk would silently lose bars
        assert all(wins[i][1] == wins[i + 1][0] for i in range(len(wins) - 1))

    def test_5m_single_window_inside_60d(self):
        wins = chunk_windows(NOW, "5m")
        assert len(wins) == 1
        assert wins[0] == (NOW - 59 * 86_400, NOW)

    def test_all_intervals_declared(self):
        assert set(INTERVALS) == {"1m", "5m", "15m"}


# ------------------------------------------------------------- parsing

def payload(ts_bars):
    return {"chart": {"result": [{
        "timestamp": [t for t, *_ in ts_bars],
        "indicators": {"quote": [{
            "open": [b[1] for b in ts_bars], "high": [b[2] for b in ts_bars],
            "low": [b[3] for b in ts_bars], "close": [b[4] for b in ts_bars],
            "volume": [b[5] for b in ts_bars]}]}}]}}


class TestParsing:
    def test_parse_skips_null_rows_and_keeps_volume(self):
        bars = parse_chart(payload([
            (NOW, 10.0, 10.5, 9.9, 10.2, 5000),
            (NOW + 60, None, 10.6, 10.0, 10.3, 4000),      # null open: skip
            (NOW + 120, 10.3, 10.7, 10.1, 10.6, None),     # null vol -> 0
        ]))
        assert [b[0] for b in bars] == [NOW, NOW + 120]
        assert bars[0][5] == 5000 and bars[1][5] == 0


# ------------------------------------------------------------- merging

class TestMerge:
    def test_overlap_dedup_and_sorted(self):
        a = [[NOW, 1, 1, 1, 1, 10], [NOW + 60, 2, 2, 2, 2, 20]]
        b = [[NOW + 60, 2, 2, 2, 2.5, 25], [NOW + 120, 3, 3, 3, 3, 30]]
        m = merge_bars(a, b)
        assert [x[0] for x in m] == [NOW, NOW + 60, NOW + 120]
        assert m[1][4] == 2.5                    # fresher bar wins collision

    def test_rerun_adds_nothing(self):
        a = [[NOW, 1, 1, 1, 1, 10]]
        assert merge_bars(a, a) == a


# ------------------------------------------------------------- archive io

class TestArchiveSymbol:
    def test_resumable_dedup_and_manifest(self, tmp_path):
        calls = []

        def fake_fetch(yh, interval, p1, p2):
            calls.append((yh, interval))
            return payload([(NOW - 86_400, 10, 11, 9, 10.5, 100),
                            (NOW - 86_340, 10.5, 11, 10, 10.8, 200)])

        e1 = archive_symbol("TESTSYM", "TEST.NS", "5m", tmp_path,
                            fetch=fake_fetch, now_epoch=NOW, pause_s=0)
        assert e1["bars"] == 2 and e1["added_last_run"] == 2
        # second run: same data -> nothing added, file intact
        e2 = archive_symbol("TESTSYM", "TEST.NS", "5m", tmp_path,
                            fetch=fake_fetch, now_epoch=NOW, pause_s=0)
        assert e2["bars"] == 2 and e2["added_last_run"] == 0
        stored = json.loads((tmp_path / "TESTSYM_5m.json").read_text())
        assert stored["yahoo"] == "TEST.NS" and len(stored["bars"]) == 2

    def test_window_failure_is_fail_soft(self, tmp_path):
        def flaky(yh, interval, p1, p2):
            raise RuntimeError("boom")

        e = archive_symbol("TESTSYM", "TEST.NS", "1m", tmp_path,
                           fetch=flaky, now_epoch=NOW, pause_s=0)
        assert e["bars"] == 0                    # empty but valid archive
        assert (tmp_path / "TESTSYM_1m.json").exists()

    def test_manifest_sessions_accounting(self):
        bars = [[NOW, 1, 1, 1, 1, 0], [NOW + 60, 1, 1, 1, 1, 0],
                [NOW + 86_400, 1, 1, 1, 1, 0]]
        m = manifest_entry("S", "1m", bars, added=3)
        assert m["sessions"] == 2 and m["bars"] == 3
        assert m["first_ts"] == NOW and m["last_ts"] == NOW + 86_400


# ------------------------------------------------------------- bisection

class TestHistoryProbe:
    BOUNDARY = dt.date(2024, 3, 15)

    def probe(self, day):
        self.calls += 1
        return day >= self.BOUNDARY

    def test_finds_exact_boundary_in_log_probes(self):
        self.calls = 0
        got = earliest_available(self.probe, hi=dt.date(2026, 8, 12))
        assert got == self.BOUNDARY
        assert self.calls <= 16                  # log2(~9700 days) + guards

    def test_no_data_returns_none(self):
        assert earliest_available(lambda d: False,
                                  hi=dt.date(2026, 8, 12)) is None

    def test_full_history_returns_floor(self):
        got = earliest_available(lambda d: True, hi=dt.date(2026, 8, 12))
        assert got == dt.date(2000, 1, 1)

    async def test_async_twin_agrees(self):
        async def probe(day):
            return day >= self.BOUNDARY

        got = await earliest_available_async(probe, hi=dt.date(2026, 8, 12))
        assert got == self.BOUNDARY

    def test_day_bounds_epoch(self):
        p1, p2 = day_bounds_epoch(dt.date(2026, 8, 13))
        assert p2 - p1 == 86_400
        assert dt.datetime.fromtimestamp(p1, dt.timezone.utc).hour == 0


# ------------------------------------------------------------- bridge

class FakeMt5:
    def __init__(self, connected=True):
        self._connected = connected

    async def is_connected(self):
        return self._connected

    async def history_depth(self, symbol):
        return {"symbol": symbol, "m1_first_date": "2019-06-03",
                "tick_first_date": "2025-11-01", "probed_at": "2026-08-13"}


async def test_bridge_history_depth_endpoint():
    from httpx import ASGITransport
    app = create_mt5_service(FakeMt5(), auth_token="S-1")
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://t") as c:
        r = await c.get("/history_depth/EURUSD")            # no auth
        assert r.status_code == 401
        r = await c.get("/history_depth/EURUSD",
                        headers={"X-MT5-Auth": "S-1"})
        assert r.status_code == 200
        assert r.json()["m1_first_date"] == "2019-06-03"


async def test_bridge_history_depth_503_when_down():
    from httpx import ASGITransport
    app = create_mt5_service(FakeMt5(connected=False))
    async with httpx.AsyncClient(transport=ASGITransport(app=app),
                                 base_url="http://t") as c:
        r = await c.get("/history_depth/EURUSD")
        assert r.status_code == 503


# ------------------------------------------------------------- probe client

def test_probe_client_writes_coverage(tmp_path):
    from scripts.probe_mt5_history import probe, summarize

    def handler(request: httpx.Request) -> httpx.Response:
        sym = request.url.path.rsplit("/", 1)[-1]
        if sym == "GBPUSD":
            return httpx.Response(503, json={"detail": "down"})
        return httpx.Response(200, json={
            "symbol": sym, "m1_first_date": "2020-01-02",
            "tick_first_date": "2026-01-05", "probed_at": "2026-08-13"})

    client = httpx.Client(transport=httpx.MockTransport(handler),
                          base_url="http://bridge",
                          headers={"X-MT5-Auth": "S"})
    cov = probe("http://bridge", "S", ["EURUSD", "GBPUSD"], client=client)
    assert cov["symbols"]["EURUSD"]["m1_first_date"] == "2020-01-02"
    assert "error" in cov["symbols"]["GBPUSD"]
    ok, fail = summarize(cov)
    assert (ok, fail) == (1, 1)
