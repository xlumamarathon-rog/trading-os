"""Fix-everything campaign (2026-08-14) — regression pins for audit BUG-2/3/4/5/7.

  BUG-2  time stop counts COMPLETED reference bars: four sub-bar calls with
         bar_closed only on the last = ONE bar of no-progress, and progress
         anywhere inside the bar clears the whole bar
  BUG-3  a stop gapped through books the realistic fill (window open), never
         the stop level
  BUG-4  cash_r_gross is qty-weighted across partials + final leg
  BUG-5  partials fill at the ladder price when the range contains it, at the
         close only when the bar gapped past
  BUG-7  stop species split; capture_pct gated at mfe_r >= 0.5, giveback in R
"""
import pytest

from src.exits.exit_manager import ExitManager

TREND = {"trend_state": "STRONG_TREND", "vol_regime": "NORMAL"}
CFG = {"breakeven_at_r": 1.0,
       "partials": [{"at_r": 1.0, "pct": 33}, {"at_r": 2.0, "pct": 33}],
       "k_sl_initial": {"india": 2.0, "mt5_forex": 2.0, "mt5_crypto": 3.0},
       "k_trail_by_regime": {"STRONG_TREND": 3.0, "WEAK_TREND": 2.0,
                             "RANGE": 1.25, "SHOCK": 0.75},
       "min_ratchet_step_atr": 0.25,
       "max_bars_no_progress": {"india": 3, "mt5_forex": 3, "mt5_crypto": 3},
       "event_tighten_minutes": 30,
       "crypto_weekend_policy": "tighten"}


class SpyStops:
    def __init__(self):
        self.exits = []

    async def place_stop(self, *a, **k):
        return "stop-1"

    async def modify_stop(self, *a, **k):
        pass

    async def replace_stop(self, *a, **k):
        return "stop-2"

    async def cancel_stop(self, *a, **k):
        pass

    async def exit_market(self, sym, qty, leg, direction=None):
        self.exits.append((sym, qty))


def mk():
    return ExitManager(dict(CFG), SpyStops())


async def attach(mgr, entry=100.0, qty=90):
    return await mgr.attach(symbol="X", direction="buy", entry=entry,
                            qty=qty, atr=1.5, leg="india")   # R = 3.0


# ------------------------------------------------------------- BUG-2

class TestTimeStopUnit:
    async def test_subbar_calls_do_not_tick_the_counter(self):
        mgr = mk()
        pos = await attach(mgr)
        # 2 stagnant daily bars (high == entry == extreme: no progress),
        # each split into 4 sub-bar calls — only the CLOSED bar counts
        for _day in range(2):
            for sub in range(4):
                await mgr.on_bar("X", 100.0, 99.8, 99.9, TREND,
                                 bar_closed=(sub == 3))
        assert pos.bars_no_progress == 2          # TWO bars, not eight
        assert pos.state != "EXITED"
        for sub in range(4):
            await mgr.on_bar("X", 100.0, 99.8, 99.9, TREND,
                             bar_closed=(sub == 3))
        assert pos.state == "EXITED"              # third completed bar fires
        assert pos.telemetry.exit_reason == "time_stop_no_progress"

    async def test_progress_anywhere_in_bar_clears_the_whole_bar(self):
        mgr = mk()
        pos = await attach(mgr)
        # progress in sub-bar 1, stagnation after — the BAR had progress
        await mgr.on_bar("X", 100.9, 100.0, 100.5, TREND, bar_closed=False)
        for sub in range(3):
            await mgr.on_bar("X", 100.2, 99.8, 100.0, TREND,
                             bar_closed=(sub == 2))
        assert pos.bars_no_progress == 0

    async def test_default_bar_closed_true_for_bar_per_call_users(self):
        mgr = mk()
        pos = await attach(mgr)
        for _ in range(3):
            await mgr.on_bar("X", 100.0, 99.8, 99.9, TREND)
        assert pos.state == "EXITED"              # legacy unit tests semantics


# ------------------------------------------------------------- BUG-3

class TestGapAwareStopFill:
    async def test_gap_through_books_the_open_not_the_stop(self):
        mgr = mk()
        pos = await attach(mgr)                   # stop 94, R=3
        await mgr.on_bar("X", 92.5, 91.0, 91.5, TREND,
                         bar_closed=True, open_px=92.0)   # gapped to 92
        assert pos.state == "EXITED"
        assert pos.telemetry.exit_reason == "stop_initial"
        assert pos.telemetry.exit_price == pytest.approx(92.0)
        assert pos.telemetry.realized_r < -1.0    # worse than -1R, honestly

    async def test_normal_stop_hit_books_the_stop(self):
        mgr = mk()
        pos = await attach(mgr)                   # stop 97 (entry 100 - 2×1.5)
        await mgr.on_bar("X", 98.5, 96.5, 96.9, TREND,
                         bar_closed=True, open_px=98.0)   # traded THROUGH 97
        assert pos.telemetry.exit_price == pytest.approx(97.0)
        assert pos.telemetry.realized_r == pytest.approx(-1.0)


# ------------------------------------------------------------- BUG-5

class TestLadderFills:
    async def test_partial_fills_at_ladder_when_range_contains_it(self):
        mgr = mk()
        pos = await attach(mgr)                   # ladder1 = 103
        await mgr.on_bar("X", 104.5, 101.0, 104.2, TREND, bar_closed=True)
        assert pos.exit_legs[0][1] == pytest.approx(103.0)   # AT the ladder

    async def test_partial_fills_at_close_when_gapped_past(self):
        mgr = mk()
        pos = await attach(mgr)
        await mgr.on_bar("X", 105.5, 103.6, 104.8, TREND,
                         bar_closed=True, open_px=103.8)     # low > ladder
        assert pos.exit_legs[0][1] == pytest.approx(104.8)   # close stands in


# ------------------------------------------------------------- BUG-4

class TestCashRGross:
    async def test_qty_weighted_across_partials_and_final(self):
        mgr = mk()
        pos = await attach(mgr, qty=90)           # entry 100, R=3
        # +1R bar: partial 29 (33% of 90 lot-floored) at ladder 103, BE stop
        await mgr.on_bar("X", 103.4, 101.0, 103.2, TREND, bar_closed=True)
        # +2R bar: partial 29 at ladder 106
        await mgr.on_bar("X", 106.4, 104.0, 106.2, TREND, bar_closed=True)
        # remainder stopped at the trailed level
        await mgr.on_bar("X", 105.0, pos.stop - 0.2, pos.stop - 0.1, TREND,
                         bar_closed=True, open_px=105.0)
        t = pos.telemetry
        legs = pos.exit_legs + [(pos.remaining_qty, t.exit_price)]
        expected = sum(q * (p - 100.0) for q, p in legs) / (3.0 * 90)
        assert t.cash_r_gross == pytest.approx(expected)
        assert t.cash_r_gross > t.realized_r      # partials banked above exit


# ------------------------------------------------------------- BUG-7

class TestTelemetryHonesty:
    async def test_capture_gated_below_half_r(self):
        mgr = mk()
        pos = await attach(mgr)
        # tiny MFE (+0.1R), then straight to the initial stop
        await mgr.on_bar("X", 100.3, 99.9, 100.1, TREND, bar_closed=True)
        await mgr.on_bar("X", 95.0, 93.9, 94.1, TREND, bar_closed=True,
                         open_px=95.0)
        t = pos.telemetry
        assert t.exit_reason == "stop_initial"
        assert t.capture_pct is None              # no -3676% nonsense
        assert t.giveback_r == pytest.approx(t.mfe_r - t.realized_r)

    async def test_stop_species(self):
        mgr = mk()
        pos = await attach(mgr)
        assert mgr._classify_stop(pos) == "stop_initial"
        pos.stop = pos.entry
        assert mgr._classify_stop(pos) == "stop_breakeven"
        pos.stop = pos.entry + 1.0
        assert mgr._classify_stop(pos) == "stop_trail"
