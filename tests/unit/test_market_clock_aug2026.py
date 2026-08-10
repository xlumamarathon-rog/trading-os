"""MODULE 58 market clock — the session calendar that ends phantom
night-trading on the india leg (operator video finding, 2026-08-10)."""
import datetime as dt

import pytest
import yaml

from src.ops.market_clock import MarketClock

UTC = dt.timezone.utc

HOURS = {
    "india": {"open": "09:15", "close": "15:30", "timezone": "Asia/Kolkata",
              "weekdays": [0, 1, 2, 3, 4],
              "holidays": ["2026-11-10", "2026-01-26"]},
    "forex": {"week_open_dow": 0, "week_open_utc": "00:00",
              "week_close_dow": 4, "week_close_utc": "21:00"},
}


def utc(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=UTC)


class TestIndiaSession:
    def setup_method(self):
        self.clock = MarketClock(HOURS)

    def test_the_video_moment_2101_ist_is_closed(self):
        # Mon 2026-08-10 21:01 IST == 15:31 UTC — the exact operator report
        assert self.clock.is_open("india", utc(2026, 8, 10, 15, 31)) is False

    def test_mid_session_open(self):
        assert self.clock.is_open("india", utc(2026, 8, 11, 5, 0)) is True  # 10:30 IST

    @pytest.mark.parametrize("h,m,expected", [
        (3, 44, False),   # 09:14 IST — one minute early
        (3, 45, True),    # 09:15 IST — open
        (9, 59, True),    # 15:29 IST — last minute
        (10, 0, False),   # 15:30 IST — close is exclusive
    ])
    def test_session_boundaries_exact(self, h, m, expected):
        assert self.clock.is_open("india", utc(2026, 8, 11, h, m)) is expected

    def test_weekend_closed(self):
        assert self.clock.is_open("india", utc(2026, 8, 15, 5, 0)) is False  # Sat
        assert self.clock.is_open("india", utc(2026, 8, 16, 5, 0)) is False  # Sun

    def test_holiday_closed_even_midweek(self):
        # Diwali-Balipratipada, Tuesday 2026-11-10
        assert self.clock.is_open("india", utc(2026, 11, 10, 5, 0)) is False

    def test_next_open_skips_weekend(self):
        # Friday 16:00 IST -> next open Monday 09:15 IST
        nxt = self.clock._india_next_open(utc(2026, 8, 14, 10, 31))
        assert nxt.isoformat().startswith("2026-08-17T03:45")

    def test_next_open_skips_holiday(self):
        # Mon 2026-11-09 evening -> Tue is Diwali holiday -> Wed 11th
        nxt = self.clock._india_next_open(utc(2026, 11, 9, 12, 0))
        assert nxt.isoformat().startswith("2026-11-11T03:45")

    def test_bad_holiday_date_fails_closed_at_construction(self):
        with pytest.raises(ValueError, match="bad holiday date"):
            MarketClock({"india": {"holidays": ["not-a-date"]}})


class TestForexAndCrypto:
    def setup_method(self):
        self.clock = MarketClock(HOURS)

    def test_fx_open_midweek_and_at_video_moment(self):
        assert self.clock.is_open("mt5_forex", utc(2026, 8, 10, 15, 31)) is True

    def test_fx_weekend_closed_friday_cutoff_exact(self):
        assert self.clock.is_open("mt5_forex", utc(2026, 8, 14, 20, 59)) is True
        assert self.clock.is_open("mt5_forex", utc(2026, 8, 14, 21, 0)) is False
        assert self.clock.is_open("mt5_forex", utc(2026, 8, 15, 12, 0)) is False

    def test_crypto_always_open(self):
        assert self.clock.is_open("mt5_crypto", utc(2026, 8, 15, 3, 0)) is True

    def test_unknown_leg_fails_open_by_design(self):
        assert self.clock.is_open("weird_new_leg", utc(2026, 8, 15, 3, 0)) is True


class TestRouterContractAndStatus:
    def setup_method(self):
        self.clock = MarketClock(HOURS)

    async def test_session_open_fn_is_the_router_precheck_contract(self):
        # async, takes leg, returns bool — exactly what OrderRouter expects
        assert await self.clock.session_open_fn("mt5_crypto") is True

    def test_status_payload_shape(self):
        st = self.clock.status(utc(2026, 8, 10, 15, 31))
        legs = st["legs"]
        assert set(legs) == {"india", "mt5_forex", "mt5_crypto"}
        assert legs["india"]["open"] is False
        assert legs["india"]["next_open_utc"].startswith("2026-08-11T03:45")
        assert legs["mt5_crypto"]["open"] is True

    def test_defaults_from_master_yaml_load(self):
        cfg = yaml.safe_load(open("config/master.yaml"))
        clock = MarketClock(cfg["trading_hours"])
        # 16 NSE weekday closures shipped for 2026
        assert len(clock.india_holidays) == 16
        assert dt.date(2026, 10, 20) in clock.india_holidays  # Dussehra
