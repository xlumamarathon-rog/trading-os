"""MODULE 29 — Multi-source deep-history loader (v2 sources; gaps documented)."""
from __future__ import annotations

from src.data.india_data_pipeline import normalize_ohlcv

DATA_SOURCES = {
    "nifty50": {"start": "1996-01-01", "source": "bhavcopy_archive+vendor"},
    "sensex": {"start": "1986-01-01", "source": "bse_archive+vendor"},
    "nse_stocks": {"start": "2000-01-01", "source": "bhavcopy_archive"},
    "sp500": {"start": "1957-01-01", "source": "yfinance+vendor"},
    "eurusd": {"start": "1999-01-01", "source": "mt5_history"},
    "gold": {"start": "1975-01-01", "source": "vendor"},
    "btcusd": {"start": "2014-01-01", "source": "mt5_history+exchange_archive"},
}


async def load_full_history(instrument: str, fetch_fn, expected_step: float = 86400.0):
    """fetch_fn(source, start) -> raw rows. Output: (rows, DataQualityReport)."""
    if instrument not in DATA_SOURCES:
        raise KeyError(f"unknown instrument {instrument} — register it in DATA_SOURCES")
    src = DATA_SOURCES[instrument]
    raw = await fetch_fn(src["source"], src["start"])
    return normalize_ohlcv(instrument, raw, expected_step=expected_step)
