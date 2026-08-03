"""MODULE 42 tests — insufficient margin, API-down fail-closed, lot validation, MT5 floor."""
from src.core.config_loader import load_config
from src.core.margin_checker import MarginChecker
from tests.fixtures.fakes import MockMarginAPI

RISK = load_config("config/master.yaml").risk_limits


async def test_india_pass_with_buffer():
    api = MockMarginAPI(available=200_000, required=100_000)
    mc = MarginChecker(RISK, india_api=api)
    d = await mc.check_india("RELIANCE", 100, 2500.0)
    assert d.ok and d.reason == "ok"
    assert d.required == 100_000 * (1 + RISK.margin_buffer_india)


async def test_india_insufficient_within_buffer_zone():
    # available covers required but NOT required*(1+buffer) → reject
    api = MockMarginAPI(available=102_000, required=100_000)
    mc = MarginChecker(RISK, india_api=api)
    d = await mc.check_india("RELIANCE", 100, 2500.0)
    assert not d.ok and d.reason == "insufficient_margin"


async def test_india_api_down_fail_closed():
    api = MockMarginAPI(fail=True)
    mc = MarginChecker(RISK, india_api=api)
    d = await mc.check_india("RELIANCE", 100, 2500.0)
    assert not d.ok and "fail_closed" in d.reason


async def test_india_no_api_fail_closed():
    mc = MarginChecker(RISK, india_api=None)
    d = await mc.check_india("RELIANCE", 100, 2500.0)
    assert not d.ok and "fail_closed" in d.reason


async def test_fo_lot_size_validation():
    api = MockMarginAPI()
    mc = MarginChecker(RISK, india_api=api)
    bad = await mc.check_india("NIFTY24AUGFUT", qty=70, price=25000.0, lot_size=75)
    ok = await mc.check_india("NIFTY24AUGFUT", qty=75, price=25000.0, lot_size=75)
    assert not bad.ok and bad.reason == "lot_size_mismatch"
    assert ok.ok


async def test_invalid_qty():
    mc = MarginChecker(RISK, india_api=MockMarginAPI())
    d = await mc.check_india("RELIANCE", 0, 2500.0)
    assert not d.ok and d.reason == "invalid_qty_or_price"


async def test_mt5_free_margin_floor():
    # equity 100k, floor 30% ⇒ post-trade free must be >= 30k
    ok_api = MockMarginAPI(free=50_000, required=15_000, equity_value=100_000)
    bad_api = MockMarginAPI(free=50_000, required=25_000, equity_value=100_000)
    mc_ok = MarginChecker(RISK, mt5_api=ok_api)
    mc_bad = MarginChecker(RISK, mt5_api=bad_api)
    assert (await mc_ok.check_mt5("BTCUSD", 1.0)).ok            # (50k-15k)/100k = 35%
    d = await mc_bad.check_mt5("BTCUSD", 1.0)                    # (50k-25k)/100k = 25%
    assert not d.ok and d.reason == "free_margin_below_floor"


async def test_mt5_api_down_fail_closed():
    mc = MarginChecker(RISK, mt5_api=MockMarginAPI(fail=True))
    d = await mc.check_mt5("EURUSD", 1.0)
    assert not d.ok and "fail_closed" in d.reason
