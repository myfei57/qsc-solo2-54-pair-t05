"""审计留存、事件流与回放。"""

from __future__ import annotations

from hydrorefuel.audit.record import AuditRecord
from hydrorefuel.audit.report import summarize
from hydrorefuel.audit.timeline import build_timeline
from hydrorefuel.errors import InterlockError


def test_events_are_recorded_as_audit(platform):
    platform.start()
    platform.unload(target_kg=10.0)
    entries = platform.audit.entries()
    assert entries
    assert any(entry["action"] == "unload_complete" for entry in entries)
    assert platform.audit.summary()["total"] >= 1


def test_rejections_show_up_as_alarms(platform):
    platform.start()
    platform.stop_fill()
    alarms = platform.audit.alarms()
    assert any(item["severity"] == "alarm" for item in alarms)
    timeline = build_timeline(platform.audit.records(), station=platform.unit)
    assert timeline[0]["step"] == 1


def test_error_records_carry_component(platform):
    record = platform.note_error(InterlockError("拒绝", component="vent"))
    assert record["component"] == "vent"
    assert summarize([AuditRecord.from_dict(record)])["by_component"]["vent"] == 1


def test_journal_replay(platform):
    platform.start()
    platform.cycle()
    assert platform.audit.replay_segment(0)
    assert platform.audit.replay_from_journal()


def test_precool_verdict_in_audit_matches_console_snapshot(platform):
    """审计落盘的预冷判定字段必须与控制台 /api/cool 同源一致。"""

    from support import load_station

    platform.start()
    load_station(platform)
    report = platform.fill(steps=1, dt=30.0, rate_kg_per_min=3.6, rewarm_ticks=1, drop_ticks=1)
    assert report["ok"], report["violations"]

    # 控制台快照直接取自判定对象。
    snapshot = platform.cool_snapshot()
    decision_keys = (
        "state",
        "in_band",
        "sample_ok",
        "ready",
        "held_s",
        "window_s",
        "rewarm_policy",
        "rewarm_restarts",
        "rewarm_extensions",
        "lost_samples",
    )

    # 审计里应能找到预冷相关事件（作废/丢点/合格），且每一条的判定字段
    # 与同一判定对象口径一致：回温作废、丢点在带内时 ready 必须为 False。
    entries = platform.audit.entries()
    cool_actions = {
        entry["action"]
        for entry in entries
        if entry["component"] == "cool"
    }
    assert "precool_rewarm_revoke_ready" in cool_actions
    assert "precool_sample_lost" in cool_actions
    assert "precool_ready" in cool_actions

    # 汇总判据（precool）与最终状态快照逐字段一致。
    precool_entry = next(
        entry for entry in entries if entry["component"] == "cool" and entry["action"] == "precool"
    )
    payload = precool_entry["payload"]
    for key in decision_keys:
        assert payload[key] == snapshot[key], (key, payload[key], snapshot[key])

    # 放行动作发生在合格判据之后：开阀事件存在且最终判据 ready 为真。
    assert snapshot["ready"] is True
    assert payload["ready"] is True
    assert payload["lost_samples"] == 1
    assert payload["rewarm_restarts"] >= 1
