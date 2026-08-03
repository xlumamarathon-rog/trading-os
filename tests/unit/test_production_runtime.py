"""Wave 9 runtime tests — WorkerSupervisor (restart-on-crash, bounded, shutdown),
LIVE gate (evidence-or-refuse), durable audit (fsync+tamper), alerts (fail-safe),
paper report / gate progression."""
import asyncio
import json

import httpx
import pytest

from src.app import (
    LIVE_ACK_PHRASE,
    LiveGateError,
    WorkerSupervisor,
    assert_live_allowed,
)
from src.core.config_loader import load_config
from src.ops.alerts import AlertFanout, TelegramAlerter
from src.ops.paper_report import advance_gate, generate_daily_report
from src.ops.persistence import ChainTamperedError, JsonlAuditLog, JsonlKVStore
from tests.fixtures.fakes import FakeRedis

CFG = load_config("config/master.yaml")


# ---------------- WorkerSupervisor ----------------

async def test_supervisor_restarts_crashed_worker_bounded():
    crashes = {"n": 0}

    async def flaky():
        crashes["n"] += 1
        if crashes["n"] <= 2:
            raise RuntimeError(f"boom {crashes['n']}")
        while True:
            await asyncio.sleep(0.01)

    sup = WorkerSupervisor(redis=FakeRedis(), max_restarts=5)
    sup.add("flaky", flaky)
    runner = asyncio.create_task(sup.run(monitor_interval=0.01))
    await asyncio.sleep(0.3)
    await sup.shutdown()
    runner.cancel()
    restarts = [e for e in sup.events if e.get("event") == "restarted"]
    assert len(restarts) == 2 and crashes["n"] == 3          # crashed twice, then stable


async def test_supervisor_gives_up_after_max_and_alerts():
    alerts = []

    async def always_dead():
        raise RuntimeError("permanently broken")

    async def alert(msg):
        alerts.append(msg)

    sup = WorkerSupervisor(redis=FakeRedis(), alert_fn=alert, max_restarts=2)
    sup.add("dead", always_dead)
    runner = asyncio.create_task(sup.run(monitor_interval=0.01))
    await asyncio.sleep(0.3)
    await sup.shutdown()
    runner.cancel()
    assert any(e.get("event") == "gave_up" for e in sup.events)
    assert alerts and "WORKER DOWN" in alerts[0]


async def test_supervisor_writes_heartbeats():
    redis = FakeRedis()

    async def steady():
        while True:
            await asyncio.sleep(0.01)

    sup = WorkerSupervisor(redis=redis)
    sup.add("steady", steady)
    runner = asyncio.create_task(sup.run(monitor_interval=0.01))
    await asyncio.sleep(0.1)
    await sup.shutdown()
    runner.cancel()
    assert "heartbeat:steady" in redis.store               # R9


# ---------------- LIVE gate ----------------

def gate_file(tmp_path, **over):
    gate = {"paper_days_completed": 20, "clean_reconciliation_streak": 6,
            "sebi_checks_passed": True, "human_ack": LIVE_ACK_PHRASE}
    gate.update(over)
    p = tmp_path / "gate_state.json"
    p.write_text(json.dumps(gate))
    return p


def test_live_blocked_without_gate_file(tmp_path):
    with pytest.raises(LiveGateError, match="run paper mode first"):
        assert_live_allowed(CFG, tmp_path / "missing.json")


@pytest.mark.parametrize("mutation,needle", [
    ({"paper_days_completed": 5}, "paper_days_completed"),
    ({"clean_reconciliation_streak": 2}, "5 consecutive clean"),
    ({"sebi_checks_passed": False}, "SEBI"),
    ({"human_ack": "yes"}, "human_ack"),
])
def test_live_blocked_on_each_missing_evidence(tmp_path, mutation, needle):
    path = gate_file(tmp_path, **mutation)
    with pytest.raises(LiveGateError, match=needle):
        assert_live_allowed(CFG, path)


def test_live_blocked_without_static_ip_even_with_perfect_gate(tmp_path):
    """config still says static_ip_confirmed: false ⇒ live refuses."""
    path = gate_file(tmp_path)
    with pytest.raises(LiveGateError, match="static_ip_confirmed"):
        assert_live_allowed(CFG, path)


# ---------------- durable audit ----------------

def test_jsonl_audit_survives_reload_and_detects_tamper(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = JsonlAuditLog(path)
    for i in range(5):
        log.append({"type": "t", "i": i})
    assert log.verify_chain()

    reloaded = JsonlAuditLog(path)                          # crash-restart simulation
    assert len(reloaded.rows) == 5 and reloaded.verify_chain()
    reloaded.append({"type": "t", "i": 5})                  # chain continues across restart
    assert JsonlAuditLog(path).verify_chain()

    lines = path.read_text().splitlines()
    row = json.loads(lines[2])
    row["i"] = 999                                          # tamper on disk
    lines[2] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ChainTamperedError):
        JsonlAuditLog(path)


def test_kv_store_roundtrip(tmp_path):
    store = JsonlKVStore(tmp_path / "ledger.jsonl")
    store.append({"a": 1})
    store.append({"b": 2})
    assert store.load_all() == [{"a": 1}, {"b": 2}]


# ---------------- alerts ----------------

async def test_telegram_alert_sends_and_fails_safe():
    sent = []

    def ok_transport():
        async def handler(request: httpx.Request):
            sent.append(json.loads(request.content.decode()))
            return httpx.Response(200, json={"ok": True})

        return httpx.MockTransport(handler)

    def down_transport():
        async def handler(request):
            raise httpx.ConnectError("telegram down")

        return httpx.MockTransport(handler)

    good = TelegramAlerter("TOKEN", "CHAT", transport=ok_transport())
    assert await good.send("kill switch fired") is True
    assert sent[0]["chat_id"] == "CHAT"

    bad = TelegramAlerter("TOKEN", "CHAT", transport=down_transport())
    assert await bad.send("x") is False                     # never raises (R5)
    assert bad.failures

    fan = AlertFanout([good, bad])
    assert await fan.send("both channels") == 1


# ---------------- paper report + gate progression ----------------

def test_gate_progression_and_streak_reset(tmp_path):
    path = tmp_path / "gate.json"
    for _ in range(3):
        gate = advance_gate(path, reconciliation_clean=True)
    assert gate["paper_days_completed"] == 3 and gate["clean_reconciliation_streak"] == 3
    gate = advance_gate(path, reconciliation_clean=False)   # dirty day
    assert gate["paper_days_completed"] == 3
    assert gate["clean_reconciliation_streak"] == 0         # streak resets — strict
    gate = advance_gate(path, reconciliation_clean=True, sebi_checks_passed=True)
    assert gate["clean_reconciliation_streak"] == 1 and gate["sebi_checks_passed"]


def test_daily_report_contains_evidence_fields():
    state = {"cash": 990_000.0, "equity": 1_002_000.0, "total_costs": 512.3,
             "positions": [], "resting": []}
    fills = [{"action": "BUY", "qty": 15, "symbol": "RELIANCE", "price": 2501.2}]
    report = generate_daily_report("2026-08-04", state, fills, True, 12)
    assert "CLEAN" in report and "RELIANCE" in report and "512.3" in report
    dirty = generate_daily_report("2026-08-05", state, fills, False, 12)
    assert "does NOT count" in dirty
