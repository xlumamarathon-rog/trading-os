"""MODULE 1 tests — written from spec acceptance criteria BEFORE implementation (R2).

Acceptance: mocked-broker kill works; manual trigger; auto-triggers per config;
fail-closed on Redis loss; cannot be bypassed; mid-cancel failure continues.
"""
import pytest

from src.core.kill_switch import HALT_KEY, KillSwitch, TradingHaltedError
from tests.fixtures.fakes import BrokerDown, FailingRedis, FakeRedis, MockBroker


def make_switch(tmp_path, redis=None, brokers=None, alert_fn=None, audit_fn=None):
    return KillSwitch(
        redis=redis if redis is not None else FakeRedis(),
        brokers=brokers
        if brokers is not None
        else {
            "india": MockBroker(
                "india",
                orders=[{"id": "O1"}, {"id": "O2"}],
                positions=[{"id": "P1"}],
            ),
            "mt5": MockBroker("mt5", orders=[{"id": "M1"}], positions=[{"id": "MP1"}, {"id": "MP2"}]),
        },
        sentinel_path=tmp_path / "halt.sentinel",
        unlock_phrase="I UNDERSTAND RESUME TRADING",
        auto_trigger_daily_loss_pct=0.03,
        auto_trigger_var_breach=True,
        max_var_daily=0.02,
        alert_fn=alert_fn,
        audit_fn=audit_fn,
    )


async def test_not_halted_initially(tmp_path):
    ks = make_switch(tmp_path)
    assert await ks.is_halted() is False
    await ks.require_trading_allowed()  # must not raise


async def test_kill_all_cancels_and_closes_everything_both_legs(tmp_path):
    alerts = []
    ks = make_switch(tmp_path, alert_fn=lambda r: alerts.append(r))
    report = await ks.kill_all("manual test")

    assert await ks.is_halted() is True
    assert sorted(report.orders_cancelled) == ["india:O1", "india:O2", "mt5:M1"]
    assert sorted(report.positions_closed) == ["india:P1", "mt5:MP1", "mt5:MP2"]
    assert report.failures == []
    assert len(alerts) == 1 and alerts[0]["reason"] == "manual test"
    # brokers actually emptied
    assert ks.brokers["india"].orders == [] and ks.brokers["mt5"].positions == []


async def test_mid_cancel_failure_continues_with_rest(tmp_path):
    """Chaos: one cancel fails — every other order/position must still be handled."""
    brokers = {
        "india": MockBroker(
            "india",
            orders=[{"id": "O1"}, {"id": "O2"}, {"id": "O3"}],
            positions=[{"id": "P1"}],
            fail_cancel_ids={"O2"},
        ),
    }
    ks = make_switch(tmp_path, brokers=brokers)
    report = await ks.kill_all("chaos")
    assert "india:O1" in report.orders_cancelled and "india:O3" in report.orders_cancelled
    assert any("cancel_failed:O2" in f for f in report.failures)
    assert report.positions_closed == ["india:P1"]
    assert await ks.is_halted() is True


async def test_one_leg_down_other_leg_still_flattened(tmp_path):
    brokers = {
        "india": BrokerDown(),
        "mt5": MockBroker("mt5", orders=[{"id": "M1"}], positions=[{"id": "MP1"}]),
    }
    ks = make_switch(tmp_path, brokers=brokers)
    report = await ks.kill_all("india leg down")
    assert any("india:fetch_orders_failed" in f for f in report.failures)
    assert report.orders_cancelled == ["mt5:M1"]
    assert report.positions_closed == ["mt5:MP1"]


async def test_fail_closed_when_redis_unreachable(tmp_path):
    ks = make_switch(tmp_path, redis=FailingRedis())
    assert await ks.is_halted() is True  # spec §12.1
    with pytest.raises(TradingHaltedError):
        await ks.require_trading_allowed()


async def test_kill_all_with_redis_down_still_halts_via_sentinel(tmp_path):
    ks = make_switch(tmp_path, redis=FailingRedis())
    report = await ks.kill_all("redis down during kill")
    assert any("redis_set_failed" in f for f in report.failures)
    assert (tmp_path / "halt.sentinel").exists()
    assert await ks.is_halted() is True


async def test_sentinel_alone_keeps_system_halted(tmp_path):
    """Even if the Redis flag vanished, the sentinel file keeps us halted."""
    ks = make_switch(tmp_path)
    await ks.kill_all("x")
    await ks.redis.delete(HALT_KEY)  # simulate flag loss
    assert await ks.is_halted() is True


async def test_unlock_wrong_phrase_refused(tmp_path):
    ks = make_switch(tmp_path)
    await ks.kill_all("x")
    with pytest.raises(PermissionError):
        await ks.unlock("wrong phrase")
    assert await ks.is_halted() is True


async def test_unlock_correct_phrase_clears_both_flags(tmp_path):
    ks = make_switch(tmp_path)
    await ks.kill_all("x")
    await ks.unlock("I UNDERSTAND RESUME TRADING")
    assert await ks.is_halted() is False


async def test_unlock_refused_while_redis_down(tmp_path):
    ks = make_switch(tmp_path)
    await ks.kill_all("x")
    ks.redis = FailingRedis()
    with pytest.raises(RuntimeError):
        await ks.unlock("I UNDERSTAND RESUME TRADING")
    assert await ks.is_halted() is True


async def test_auto_trigger_daily_loss(tmp_path):
    ks = make_switch(tmp_path)
    assert await ks.check_auto_triggers(daily_pnl_pct=-0.01) is None
    report = await ks.check_auto_triggers(daily_pnl_pct=-0.031)
    assert report is not None and "daily loss" in report.reason
    assert await ks.is_halted() is True


async def test_auto_trigger_var_breach(tmp_path):
    ks = make_switch(tmp_path)
    assert await ks.check_auto_triggers(var_95=0.015) is None
    report = await ks.check_auto_triggers(var_95=0.025)
    assert report is not None and "VaR95" in report.reason
