"""配置解析与校验。"""

from __future__ import annotations

from pathlib import Path

import pytest

from hydrorefuel.config import (
    CompressorLimits,
    FlowLimits,
    GunPolicies,
    GunProfile,
    StationConfig,
    config_to_mapping,
    default_config,
    load_config,
)
from hydrorefuel.errors import ConfigError

ROOT = Path(__file__).resolve().parents[1]


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


def test_stability_window_mode_is_pinned_to_fixed_duration():
    base = config_to_mapping(default_config())
    base["limits"]["cool"]["stability_window_mode"] = "volatility_band"
    with pytest.raises(ConfigError):
        StationConfig.from_mapping(base)
    # 默认缺省即固定时长。
    mapping = config_to_mapping(default_config())
    del mapping["limits"]["cool"]["stability_window_mode"]
    cfg = StationConfig.from_mapping(mapping)
    assert cfg.cool.stability_window_mode == "fixed_duration"


def test_gun_profile_policy_must_be_known():
    with pytest.raises(ConfigError):
        GunProfile.from_mapping("TK99", {"rewarm_policy": "guess"})
    with pytest.raises(ConfigError):
        GunProfile.from_mapping("TK99", {})
    # restart 枪型不需要延长参数。
    restart = GunProfile.from_mapping("TK70", {"rewarm_policy": "restart"})
    assert restart.rewarm_extend_max == 0
    # extend 枪型必须给出正的延长时长与次数。
    with pytest.raises(ConfigError):
        GunProfile.from_mapping("TK35", {"rewarm_policy": "extend"})
    extend = GunProfile.from_mapping(
        "TK35", {"rewarm_policy": "extend", "rewarm_extend_s": 5.0, "rewarm_extend_max": 2}
    )
    assert extend.rewarm_extend_s == 5.0
    with pytest.raises(ConfigError):
        GunPolicies.from_mapping({})


def test_dispenser_gun_type_must_have_a_policy():
    mapping = config_to_mapping(default_config())
    mapping["limits"]["disp"]["gun_type"] = "TK99"
    with pytest.raises(ConfigError):
        StationConfig.from_mapping(mapping)
    # 缺 gun_type 由 disp 段直接拦截。
    from hydrorefuel.config import DispenserLimits

    disp_mapping = config_to_mapping(default_config())["limits"]["disp"]
    del disp_mapping["gun_type"]
    with pytest.raises(ConfigError):
        DispenserLimits.from_mapping(disp_mapping)
