"""MODULE 6 — India exchange risk rules (spec §Phase 2, v2-corrected).

Stock PRICE BANDS (2/5/10/20% daily) are separate from INDEX CIRCUIT BREAKERS
(10/15/20% -> market-wide halts). Also: F&O lot validation and MWPL ban list.
All band assignments injectable; defaults read from config-provided maps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

DEFAULT_BAND_TIERS = (0.02, 0.05, 0.10, 0.20)   # legal stock price-band tiers
INDEX_CIRCUIT_LEVELS = (0.10, 0.15, 0.20)        # market-wide halt stages


@dataclass
class BandCheck:
    ok: bool
    reason: str
    band_pct: Optional[float] = None
    move_pct: Optional[float] = None


class IndiaRiskConfig:
    def __init__(self, band_map: dict = None, lot_sizes: dict = None,
                 mwpl_banned: Iterable[str] = ()) -> None:
        self.band_map = band_map or {}
        self.lot_sizes = lot_sizes or {}
        self.mwpl_banned = set(mwpl_banned)

    def get_band(self, symbol: str) -> float:
        band = self.band_map.get(symbol, DEFAULT_BAND_TIERS[-1])
        if band not in DEFAULT_BAND_TIERS:
            raise ValueError(f"illegal band {band} for {symbol}")
        return band

    def check_price_band(self, symbol: str, proposed_price: float, last_close: float) -> BandCheck:
        if last_close <= 0 or proposed_price <= 0:
            return BandCheck(False, "invalid_prices")
        if symbol in self.mwpl_banned:
            return BandCheck(False, "mwpl_ban_list")
        band = self.get_band(symbol)
        move = abs(proposed_price - last_close) / last_close
        if move >= band:
            return BandCheck(False, "would_breach_price_band", band, move)
        return BandCheck(True, "ok", band, move)

    def validate_lot(self, symbol: str, qty: float) -> bool:
        lot = self.lot_sizes.get(symbol)
        if lot is None:
            return True  # cash equity — no lot constraint
        return qty > 0 and int(qty) % int(lot) == 0

    @staticmethod
    def index_circuit_stage(index_move_pct: float) -> int:
        """0 = trading normal; 1/2/3 = halt stages at 10/15/20%."""
        move = abs(index_move_pct)
        stage = 0
        for i, level in enumerate(INDEX_CIRCUIT_LEVELS, start=1):
            if move >= level:
                stage = i
        return stage
