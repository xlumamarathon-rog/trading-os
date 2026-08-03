"""Vendor integration tests + DRIFT CANARIES.

Two layers:
1. Adapter tests (always run): our adapters against fixtures/mock transports.
2. Canary tests (run when vendor/ is cloned; CI clones nightly): scan ACTUAL
   vendor source and fail if the APIs we wired against have drifted upstream.
"""
import json
from pathlib import Path

import httpx
import pytest

from src.intel.mirofish_adapter import MiroFishAdapter
from src.intel.trading_agents_adapter import TradingAgentsAdapter, rating_to_signal
from src.ml.edt_loader import load_evaluate_news

VENDOR = Path("vendor")
needs_vendor = pytest.mark.skipif(not VENDOR.exists(), reason="vendor/ not cloned")


# ---------------- adapter tests (always run) ----------------

def test_rating_map_covers_all_five_grades():
    assert rating_to_signal("Buy") == ("BUY", 0.80)
    assert rating_to_signal("Overweight") == ("BUY", 0.65)
    assert rating_to_signal("Hold") == ("HOLD", 0.50)
    assert rating_to_signal("Underweight") == ("SELL", 0.65)
    assert rating_to_signal("Sell") == ("SELL", 0.80)
    with pytest.raises(ValueError):
        rating_to_signal("Moon")


async def test_trading_agents_adapter_with_injected_graph():
    class FakeGraph:
        def propagate(self, company_name, trade_date, asset_type="stock"):
            return ({"final_trade_decision": "..."}, "Overweight")

    adapter = TradingAgentsAdapter(graph=FakeGraph())
    signal = await adapter.compute_signal("RELIANCE", "2026-08-04")
    assert signal == {"ticker": "RELIANCE", "direction": "BUY", "confidence": 0.65,
                      "rating": "Overweight", "source": "trading_agents"}


async def test_mirofish_adapter_full_cycle_against_verified_routes():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/simulation/create":
            return httpx.Response(200, json={"simulation_id": "S1"})
        if request.url.path == "/simulation/prepare":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/report/generate":
            return httpx.Response(200, json={"report_id": "R1"})
        if request.url.path == "/report/generate/status":
            return httpx.Response(200, json={"status": "completed"})
        if request.url.path == "/report/R1":
            return httpx.Response(200, json={"sentiment": -0.6, "panic_level": 0.8,
                                             "mechanical_flag": True, "summary": "fear"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                               base_url="http://mirofish")
    adapter = MiroFishAdapter("http://mirofish", client=client, poll_seconds=0.0)
    crowd = await adapter.reconstruct_crowd_reaction("RBI surprise hike")
    assert crowd["mechanical_flag"] is True and crowd["panic_level"] == 0.8
    # exact verified route sequence was exercised
    paths = [p for _, p in calls]
    assert paths == ["/simulation/create", "/simulation/prepare", "/report/generate",
                     "/report/generate/status", "/report/R1"]


def test_edt_loader_verified_fields_and_lookahead_guard(tmp_path):
    data = [
        {"title": "Acme beats earnings", "text": "...", "pub_time": "2020-01-01 09:00:00",
         "labels": {"ticker": "ACME", "start_time": "2020-01-01 09:30:00"}},
        {"title": "No labels row", "text": "...", "pub_time": "2020-01-02 09:00:00",
         "labels": {}},
        {"title": "Lookahead defect", "text": "...", "pub_time": "2020-01-03 09:00:00",
         "labels": {"ticker": "X", "start_time": "2020-01-02 08:00:00"}},
        {"bad": "row"},
    ]
    p = tmp_path / "evaluate_news.json"
    p.write_text(json.dumps(data))
    rows, rep = load_evaluate_news(p)
    assert rep.total == 4 and rep.usable == 1
    assert rep.skipped_no_labels == 1 and rep.skipped_bad_time == 1
    assert rep.skipped_malformed == 1
    assert rows[0]["ticker"] == "ACME"


# ---------------- drift canaries (vendor source scans) ----------------

@needs_vendor
def test_canary_openalgo_placeorder_schema():
    src = (VENDOR / "openalgo/restx_api/schemas.py").read_text()
    for fields in ("apikey", "strategy", "exchange", "symbol", "action", "quantity"):
        assert fields in src, f"OpenAlgo schema drift: {fields} missing"
    routes = (VENDOR / "openalgo/restx_api/__init__.py").read_text()
    for path in ("placeorder", "modifyorder", "cancelorder", "tradebook", "positionbook"):
        assert path in routes, f"OpenAlgo route drift: {path} missing"
    assert 'url_prefix="/api/v1"' in routes


@needs_vendor
def test_canary_tradingagents_propagate_and_ratings():
    graph = (VENDOR / "TradingAgents/tradingagents/graph/trading_graph.py").read_text()
    assert "class TradingAgentsGraph" in graph
    assert "def propagate(self, company_name, trade_date" in graph
    sig = (VENDOR / "TradingAgents/tradingagents/graph/signal_processing.py").read_text()
    assert "def process_signal(self" in sig
    for grade in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
        assert grade in sig, f"TradingAgents rating scale drift: {grade} missing"


@needs_vendor
def test_canary_aiomql_bot_and_order():
    bot = (VENDOR / "aiomql/src/aiomql/lib/bot.py").read_text()
    assert "class Bot" in bot and "async def initialize" in bot
    order = (VENDOR / "aiomql/src/aiomql/lib/order.py").read_text()
    assert "class Order" in order and "async def send" in order
    assert "def send_order(" in order


@needs_vendor
def test_canary_mirofish_backend_routes():
    sim = (VENDOR / "MiroFish/backend/app/api/simulation.py").read_text()
    assert "'/create'" in sim and "'/prepare'" in sim
    rep = (VENDOR / "MiroFish/backend/app/api/report.py").read_text()
    assert "'/generate'" in rep and "'/generate/status'" in rep


@needs_vendor
def test_canary_tradetheevent_dataset_contract():
    bt = (VENDOR / "TradeTheEvent/run_backtest.py").read_text()
    for f in ("item['title']", "item['pub_time']", "item['labels']"):
        assert f in bt, f"EDT format drift: {f} missing from run_backtest.py"


@needs_vendor
def test_canary_all_seven_vendors_present():
    expected = {"openalgo", "TradingAgents", "aiomql", "MiroFish",
                "ai-berkshire", "TradeTheEvent", "machine-learning-for-trading"}
    present = {p.name for p in VENDOR.iterdir() if p.is_dir()}
    missing = expected - present
    assert not missing, f"vendors not cloned: {missing} (run scripts/clone_vendors.sh)"
