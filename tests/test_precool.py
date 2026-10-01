"""预冷温度模型、稳定窗口与回温/丢点判定。"""

from __future__ import annotations

import pytest

from hydrorefuel.config import default_config
from hydrorefuel.cool.precool import COOLING, STABILIZING, Precooler
from hydrorefuel.cool.temperature import TemperatureModel
from hydrorefuel.errors import ConfigError

from support import load_station


def _started_precooler(gun_type: str) -> Precooler:
    precooler = Precooler(default_config().cool)
    precooler.start(gun_type)
    return precooler


def test_temperature_reaches_target():
    limits = default_config().cool
    model = TemperatureModel(limits)
    for _ in range(400):
        model.cool(1.0)
    assert model.reached()
    assert model.value_c == limits.target_temp_c


def test_temperature_rewarms_towards_ambient():
    limits = default_config().cool
    model = TemperatureModel(limits)
    for _ in range(400):
        model.cool(1.0)
    assert model.value_c == limits.target_temp_c
    model.rewarm(10.0)
    assert model.value_c == limits.target_temp_c + limits.rewarm_rate_c_per_s * 10.0
    for _ in range(1000):
        model.rewarm(1.0)
    assert model.value_c == 25.0


def test_readback_drops_are_injectable():
    limits = default_config().cool
    model = TemperatureModel(limits)
    model.drop_reads(2)
    assert model.read() is None
    assert model.read() is None
    assert model.read() == model.value_c
    model.drop_reads(1)
    model.reset()
    assert model.read() == 25.0


def test_precool_requires_hold_time():
    limits = default_config().cool
    model = TemperatureModel(limits)
    precooler = _started_precooler("h70")
    for _ in range(400):
        model.cool(1.0)
        if precooler.tick(1.0, model.value_c):
            break
    assert precooler.is_ready()
    assert precooler.held_s >= limits.precool_hold_s
    assert precooler.reset() is True


def test_hold_resets_when_temperature_leaves_band():
    limits = default_config().cool
    precooler = _started_precooler("h35")
    precooler.tick(5.0, limits.target_temp_c)
    assert precooler.held_s == 5.0
    precooler.tick(5.0, limits.target_temp_c + 10.0)
    assert precooler.held_s == 0.0


def test_restart_policy_voids_window_on_rewarm():
    limits = default_config().cool
    precooler = _started_precooler("h35")
    precooler.tick(10.0, limits.target_temp_c)
    assert precooler.state == STABILIZING
    assert precooler.tick(1.0, limits.target_temp_c + 10.0) is False
    assert precooler.state == COOLING
    assert precooler.held_s == 0.0
    events = precooler.drain_events()
    assert [event["action"] for event in events] == ["precool_stabilizing", "precool_rewarm"]
    assert events[1]["detail"]["policy"] == "restart"
    assert events[1]["detail"]["applied"] == "restart"
    assert events[1]["detail"]["held_s"] == 10.0
    # 整段作废：重新进带后必须重新稳满整个窗口。
    precooler.tick(10.0, limits.target_temp_c)
    assert precooler.held_s == 10.0
    assert precooler.is_ready() is False
    assert precooler.tick(10.0, limits.target_temp_c) is True


def test_extend_policy_preserves_hold_and_extends_window():
    limits = default_config().cool
    precooler = _started_precooler("h70")
    precooler.tick(15.0, limits.target_temp_c)
    assert precooler.tick(1.0, limits.target_temp_c + 10.0) is False
    assert precooler.held_s == 15.0
    assert precooler.required_s == limits.precool_hold_s + limits.rewarm_extend_s
    events = precooler.drain_events()
    assert events[-1]["action"] == "precool_rewarm"
    assert events[-1]["detail"]["policy"] == "extend"
    assert events[-1]["detail"]["applied"] == "extend"
    # 回带后继续累计，稳满延长后的窗口才合格。
    assert precooler.tick(14.0, limits.target_temp_c) is False
    assert precooler.tick(1.0, limits.target_temp_c) is True


def test_overcool_always_restarts_even_with_extend_policy():
    limits = default_config().cool
    precooler = _started_precooler("h70")
    precooler.tick(15.0, limits.target_temp_c)
    assert precooler.tick(1.0, limits.target_temp_c - 10.0) is False
    assert precooler.held_s == 0.0
    assert precooler.required_s == limits.precool_hold_s
    assert precooler.drain_events()[-1]["action"] == "precool_overcool"


def test_qualified_is_revoked_when_temperature_leaves_band():
    limits = default_config().cool
    precooler = _started_precooler("h70")
    precooler.tick(20.0, limits.target_temp_c)
    assert precooler.is_ready()
    precooler.drain_events()
    assert precooler.tick(1.0, limits.target_temp_c + 10.0) is False
    assert precooler.state == COOLING
    assert precooler.revocations == 1
    events = precooler.drain_events()
    assert events[-1]["action"] == "precool_revoked"
    assert events[-1]["detail"]["reason"] == "rewarm"
    # 延长政策也保不住已合格的超限：必须重新稳满一个完整窗口。
    assert precooler.tick(20.0, limits.target_temp_c) is True
    assert precooler.revocations == 1


def test_readback_gap_voids_window():
    limits = default_config().cool
    precooler = _started_precooler("h35")
    precooler.tick(10.0, limits.target_temp_c)
    assert precooler.tick(1.0, None) is False
    assert precooler.gaps == 1
    assert precooler.held_s == 0.0
    assert precooler.drain_events()[-1]["action"] == "precool_readback_gap"
    # 证据断链后必须重新稳满一个无丢点的窗口。
    assert precooler.tick(20.0, limits.target_temp_c) is True


def test_readback_gap_after_qualification_revokes():
    limits = default_config().cool
    precooler = _started_precooler("h35")
    precooler.tick(20.0, limits.target_temp_c)
    assert precooler.is_ready()
    assert precooler.tick(1.0, None) is False
    assert precooler.is_ready() is False
    assert precooler.revocations == 1


def test_precool_sequencing_is_enforced():
    limits = default_config().cool
    precooler = Precooler(limits)
    with pytest.raises(ValueError):
        precooler.tick(1.0, limits.target_temp_c)
    precooler.start("h35")
    with pytest.raises(ValueError):
        precooler.start("h35")
    precooler.reset()
    precooler.start("h35")
    with pytest.raises(ValueError):
        precooler.tick(0.0, limits.target_temp_c)


def test_unknown_gun_type_is_rejected():
    precooler = Precooler(default_config().cool)
    with pytest.raises(ConfigError):
        precooler.start("h999")


def test_verdict_carries_judgement_basis():
    limits = default_config().cool
    precooler = _started_precooler("h70")
    precooler.tick(10.0, limits.target_temp_c)
    verdict = precooler.verdict()
    assert verdict["state"] == STABILIZING
    assert verdict["ready"] is False
    assert verdict["gun_type"] == "h70"
    assert verdict["policy"] == "extend"
    assert verdict["held_s"] == 10.0
    assert verdict["required_s"] == limits.precool_hold_s
    assert verdict["remaining_s"] == limits.precool_hold_s - 10.0
    assert verdict["gaps"] == 0
    assert verdict["excursions"] == 0
    assert verdict["revocations"] == 0


def test_rewarm_after_qualification_blocks_release_and_audits(platform):
    platform.start()
    load_station(platform)
    fill = platform.fill(steps=1)
    assert fill["ok"], fill["violations"]
    assert platform.readiness()["checks"]["precool_ready"] is True
    disturb = platform.disturb_cool(rewarm_s=20.0)
    assert "precool_revoked" in disturb["events"]
    assert disturb["verdict"]["ready"] is False
    readiness = platform.readiness()
    assert readiness["checks"]["precool_ready"] is False
    assert readiness["precool"]["revocations"] == 1
    snapshot = platform.cool_snapshot()
    assert snapshot["ready"] is False
    assert snapshot["revocations"] == 1
    actions = [entry["action"] for entry in platform.audit.entries()]
    assert "precool_revoked" in actions
    # 重新稳住才算合格，才放行。
    refill = platform.fill(steps=1)
    assert refill["ok"], refill["violations"]
    assert platform.readiness()["checks"]["precool_ready"] is True


def test_readback_gap_blocks_qualification(platform):
    platform.start()
    load_station(platform)
    assert platform.fill(steps=1)["ok"]
    disturb = platform.disturb_cool(drop_reads=1)
    assert "precool_readback_gap" in disturb["events"]
    assert disturb["verdict"]["ready"] is False
    assert platform.readiness()["checks"]["precool_ready"] is False
    assert any(entry["action"] == "precool_readback_gap" for entry in platform.audit.alarms())


def test_audit_basis_matches_console_snapshot(platform):
    platform.start()
    load_station(platform)
    assert platform.fill(steps=1)["ok"]
    snapshot = platform.cool_snapshot()
    qualified = [entry for entry in platform.audit.entries() if entry["action"] == "precool_qualified"]
    assert qualified
    payload = qualified[0]["payload"]
    assert payload["held_s"] == snapshot["held_s"]
    assert payload["required_s"] == snapshot["required_s"]
    assert payload["gun_type"] == snapshot["gun_type"] == "h70"
    assert payload["policy"] == snapshot["policy"] == "extend"
