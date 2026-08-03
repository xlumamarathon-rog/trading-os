"""MODULE 18 tests — written from spec §3 acceptance criteria."""
import os

import pytest
from pydantic import ValidationError

from src.core.config_loader import load_config

REPO_YAML = "config/master.yaml"


def test_loads_repo_master_yaml():
    cfg = load_config(REPO_YAML)
    assert 0 < cfg.risk_limits.max_position_pct < 1
    assert cfg.kill_switch.redis_unreachable_behavior == "halt"
    assert cfg.execution_costs.india.brokerage_flat >= 0


def test_unresolved_env_recorded_not_silently_defaulted():
    for name in ("INDIA_BROKER_API_KEY", "MT5_LOGIN"):
        os.environ.pop(name, None)
    cfg = load_config(REPO_YAML)
    assert "INDIA_BROKER_API_KEY" in cfg.unresolved_env
    assert "MT5_LOGIN" in cfg.unresolved_env


def test_env_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SECRET_XYZ", "s3cret")
    y = tmp_path / "m.yaml"
    y.write_text(
        """
risk_limits: {max_var_daily: 0.02, max_position_pct: 0.05, max_risk_per_trade_pct: 0.01,
              max_daily_loss_pct: 0.03, max_sector_exposure_pct: 0.2, gap_assumption_atr: 3.0,
              kelly_cap: 0.5, margin_buffer_india: 0.05, mt5_min_free_margin_pct: 0.3}
kill_switch: {auto_trigger_daily_loss_pct: 0.03, auto_trigger_var_breach: true,
              redis_unreachable_behavior: halt, unlock_phrase: "${TEST_SECRET_XYZ}"}
execution_costs:
  india: {brokerage_flat: 20, stt_delivery_pct: 0.001, stt_intraday_sell_pct: 0.00025,
          exchange_txn_pct: 0.0000345, stamp_duty_pct: 0.00015, gst_pct: 0.18}
  impact_model: {y_coefficient: 0.7}
"""
    )
    cfg = load_config(y)
    assert cfg.kill_switch.unlock_phrase == "s3cret"
    assert cfg.unresolved_env == []


def test_invalid_position_pct_rejected(tmp_path):
    y = tmp_path / "bad.yaml"
    y.write_text(
        """
risk_limits: {max_var_daily: 0.02, max_position_pct: 1.5, max_risk_per_trade_pct: 0.01,
              max_daily_loss_pct: 0.03, max_sector_exposure_pct: 0.2, gap_assumption_atr: 3.0,
              kelly_cap: 0.5, margin_buffer_india: 0.05, mt5_min_free_margin_pct: 0.3}
kill_switch: {auto_trigger_daily_loss_pct: 0.03, auto_trigger_var_breach: true,
              redis_unreachable_behavior: halt}
execution_costs:
  india: {brokerage_flat: 20, stt_delivery_pct: 0.001, stt_intraday_sell_pct: 0.00025,
          exchange_txn_pct: 0.0000345, stamp_duty_pct: 0.00015, gst_pct: 0.18}
  impact_model: {y_coefficient: 0.7}
"""
    )
    with pytest.raises(ValidationError):
        load_config(y)


def test_fail_open_config_is_impossible(tmp_path):
    """Spec §12.1 — only 'halt' is a legal redis_unreachable_behavior."""
    y = tmp_path / "open.yaml"
    y.write_text(
        """
risk_limits: {max_var_daily: 0.02, max_position_pct: 0.05, max_risk_per_trade_pct: 0.01,
              max_daily_loss_pct: 0.03, max_sector_exposure_pct: 0.2, gap_assumption_atr: 3.0,
              kelly_cap: 0.5, margin_buffer_india: 0.05, mt5_min_free_margin_pct: 0.3}
kill_switch: {auto_trigger_daily_loss_pct: 0.03, auto_trigger_var_breach: true,
              redis_unreachable_behavior: ignore}
execution_costs:
  india: {brokerage_flat: 20, stt_delivery_pct: 0.001, stt_intraday_sell_pct: 0.00025,
          exchange_txn_pct: 0.0000345, stamp_duty_pct: 0.00015, gst_pct: 0.18}
  impact_model: {y_coefficient: 0.7}
"""
    )
    with pytest.raises(ValidationError):
        load_config(y)
