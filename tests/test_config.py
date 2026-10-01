"""配置解析与校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hydrorefuel.config import (
    CompressorLimits,
    CoolLimits,
    FlowLimits,
    StationConfig,
    config_to_mapping,
    default_config,
    load_config,
)
from hydrorefuel.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]

_COOL_BASE = {
    "target_temp_c": -40.0,
    "temp_band_c": 3.0,
    "cool_rate_c_per_s": 0.6,
    "precool_hold_s": 20.0,
    "rewarm_rate_c_per_s": 0.4,
    "rewarm_extend_s": 10.0,
}


def test_repository_config_matches_defaults():
    loaded = load_config(str(ROOT / "config" / "hydrorefuel.json"))
    assert loaded == default_config()


def test_config_round_trips_through_mapping():
    cfg = default_config()
    assert StationConfig.from_mapping(config_to_mapping(cfg)) == cfg


def test_missing_limits_section_is_rejected():
    with pytest.raises(ConfigError):
        StationConfig.from_mapping({"station": {"name": "x", "storage_root": "y"}})


def test_invalid_limits_are_rejected():
    with pytest.raises(ConfigError):
        CompressorLimits.from_mapping(
            {
                "inlet_min_mpa": 10.0,
                "outlet_target_mpa": 5.0,
                "ramp_mpa_per_s": 1.0,
                "surge_band_mpa": 1.0,
                "min_flow_kg_per_min": 1.0,
            }
        )
    with pytest.raises(ConfigError):
        FlowLimits.from_mapping(
            {
                "step_target_kg": 1.0,
                "step_tolerance_kg": 2.0,
                "max_rate_kg_per_min": 3.0,
                "min_rate_kg_per_min": 0.5,
            }
        )


def test_missing_file_is_rejected():
    with pytest.raises(ConfigError):
        load_config(str(ROOT / "config" / "missing.json"))


def test_cool_gun_policies_are_validated():
    with pytest.raises(ConfigError):
        CoolLimits.from_mapping(_COOL_BASE)
    with pytest.raises(ConfigError):
        CoolLimits.from_mapping({**_COOL_BASE, "gun_policies": {}})
    with pytest.raises(ConfigError):
        CoolLimits.from_mapping({**_COOL_BASE, "gun_policies": {"h70": "ignore"}})
    limits = CoolLimits.from_mapping({**_COOL_BASE, "gun_policies": {"h35": "restart", "h70": "extend"}})
    assert limits.policy_for("h35") == "restart"
    assert limits.policy_for("h70") == "extend"
    with pytest.raises(ConfigError):
        limits.policy_for("h999")


def test_dispenser_gun_type_must_have_a_policy():
    raw = config_to_mapping(default_config())
    raw["limits"]["disp"]["gun_type"] = "h999"
    with pytest.raises(ConfigError):
        StationConfig.from_mapping(raw)
