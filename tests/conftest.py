"""测试夹具：所有用例都从干净存储和可推进时钟起步。"""

from __future__ import annotations

import sys
from pathlib import Path

import dataclasses

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hydrorefuel.app import Platform, build_platform  # noqa: E402
from hydrorefuel.clock import ManualClock  # noqa: E402
from hydrorefuel.config import default_config  # noqa: E402


@pytest.fixture()
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture()
def platform(tmp_path, clock) -> Platform:
    return build_platform(default_config(), clock=clock, storage_root=str(tmp_path / "state"))


@pytest.fixture()
def extend_gun_platform(tmp_path, clock) -> Platform:
    """把加注机枪型切成 extend 策略（窗口最多延长一次，再回温即终态失败）。"""

    from hydrorefuel.config import DispenserLimits, GunPolicies, GunProfile

    cfg = default_config()
    disp = dataclasses.replace(cfg.dispenser, gun_type="TK35")
    guns = GunPolicies(
        {
            "TK70": cfg.guns.get("TK70"),
            "TK35": GunProfile("TK35", "extend", 10.0, 1),
        }
    )
    cfg = dataclasses.replace(cfg, dispenser=disp, guns=guns)
    return build_platform(cfg, clock=clock, storage_root=str(tmp_path / "state"))
