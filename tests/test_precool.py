"""预冷温度回读、稳定窗口与合格判定。"""

from __future__ import annotations

import pytest

from hydrorefuel.config import GunProfile, default_config
from hydrorefuel.cool.precool import Precooler
from hydrorefuel.cool.temperature import TemperatureModel
from hydrorefuel.cool.verdict import (
    COOLING,
    HOLDING,
    READY,
    EVENT_READY,
    EVENT_REWARM_EXTEND,
    EVENT_REWARM_RESTART,
    EVENT_REWARM_REVOKE_READY,
    EVENT_SAMPLE_LOST,
    FAIL_EXTENSIONS_EXHAUSTED,
    FAILED,
)

COOL = default_config().cool
TARGET = COOL.target_temp_c
OUT_OF_BAND = TARGET + COOL.temp_band_c + 5.0  # 明确回温超带
HOLD = COOL.precool_hold_s

RESTART_GUN = GunProfile("TK70", "restart", 0.0, 0)


def _cool_to_ready(precooler: Precooler, *, step: float = 1.0, start_ts: float = 0.0) -> float:
    """喂目标温度把判定推进到合格，返回最后一拍时间戳。"""

    ts = start_ts
    ticks = int(HOLD // step)
    for _ in range(ticks):
        ts += step
        verdict = precooler.tick(step, TARGET, ts=ts)
    assert verdict.ready
    return ts


def test_temperature_read_reaches_target():
    model = TemperatureModel(COOL)
    last = None
    for _ in range(400):
        last = model.read(1.0)
    assert last is not None
    assert COOL.reached(last)
    assert last == TARGET


def test_precool_requires_full_fixed_window():
    precooler = Precooler(COOL, RESTART_GUN)
    precooler.start()
    # 差一拍没稳满也不能合格：稳定窗口是固定时长，模式定死。
    ts = 0.0
    for _ in range(int(HOLD) - 1):
        ts += 1.0
        verdict = precooler.tick(1.0, TARGET, ts=ts)
    assert not verdict.ready
    assert verdict.state == HOLDING
    verdict = precooler.tick(1.0, TARGET, ts=ts + 1.0)
    assert verdict.ready
    assert verdict.state == READY
    assert verdict.held_s >= HOLD
    assert precooler.reset() is True


def test_rewarm_restart_voids_the_window():
    precooler = Precooler(COOL, RESTART_GUN)
    precooler.start()
    precooler.tick(5.0, TARGET, ts=5.0)
    assert precooler.verdict.held_s == 5.0
    verdict = precooler.tick(5.0, OUT_OF_BAND, ts=10.0)
    # 整段作废：已稳住时长清零、窗口回到初始长度，重新稳住才算。
    assert precooler.verdict.held_s == 0.0
    assert verdict.window_s == HOLD
    assert verdict.rewarm_restarts == 1
    assert verdict.state == COOLING
    assert verdict.event == EVENT_REWARM_RESTART


def test_ready_is_revoked_when_rewarm_after_qualification():
    precooler = Precooler(COOL, RESTART_GUN)
    precooler.start()
    ts = _cool_to_ready(precooler)
    # 用户报的核心问题：判合格后回温，开阀瞬间就不合格——必须撤销合格。
    verdict = precooler.tick(1.0, OUT_OF_BAND, ts=ts + 1.0)
    assert not verdict.ready
    assert verdict.state == COOLING
    assert verdict.event == EVENT_REWARM_REVOKE_READY
    assert verdict.rewarm_restarts == 1
    # 重新稳住整窗才再次合格放行。
    ts = _cool_to_ready(precooler, start_ts=ts + 1.0)
    assert precooler.verdict.ready
    assert precooler.verdict.rewarm_restarts == 1


def test_extend_gun_keeps_hold_and_extends_window():
    extend_gun = GunProfile("TK35", "extend", 10.0, 2)
    precooler = Precooler(COOL, extend_gun)
    precooler.start()
    ts = _cool_to_ready(precooler)
    before_held = precooler.verdict.held_s
    verdict = precooler.tick(1.0, OUT_OF_BAND, ts=ts + 1.0)
    # 延长策略：保留已稳住时长，窗口再加 10 秒，合格撤销但不整段作废。
    assert verdict.event in (EVENT_REWARM_EXTEND, EVENT_REWARM_REVOKE_READY)
    assert not verdict.ready
    assert verdict.state == HOLDING
    assert precooler.verdict.held_s == before_held
    assert verdict.window_s == HOLD + 10.0
    assert verdict.rewarm_extensions == 1
    # 在延长后的窗口里继续稳住即可合格。
    for _ in range(10):
        ts += 1.0
        verdict = precooler.tick(1.0, TARGET, ts=ts + 1.0)
    assert verdict.ready
    assert verdict.window_s == HOLD + 10.0


def test_extend_budget_exhausted_fails_the_round():
    extend_gun = GunProfile("TK35", "extend", 10.0, 1)
    precooler = Precooler(COOL, extend_gun)
    precooler.start()
    ts = 0.0
    # 先进带稳住一拍，再连续两次回温超带：第一次延长，第二次耗尽即失败。
    ts += 1.0
    precooler.tick(1.0, TARGET, ts=ts)
    ts += 1.0
    precooler.tick(1.0, OUT_OF_BAND, ts=ts)
    ts += 1.0
    verdict = precooler.tick(1.0, OUT_OF_BAND, ts=ts)
    assert verdict.state == FAILED
    assert verdict.failure_reason == FAIL_EXTENSIONS_EXHAUSTED
    assert not verdict.ready
    assert precooler.is_failed()
    # 失败是终态：后续哪怕温度又正常，也不能自动恢复，必须 start() 重来。
    verdict = precooler.tick(1.0, TARGET, ts=ts + 1.0)
    assert verdict.state == FAILED
    precooler.start()
    ts = _cool_to_ready(precooler, start_ts=ts + 1.0)
    assert precooler.verdict.ready


def test_lost_readback_is_partial_failure_and_never_qualifies():
    precooler = Precooler(COOL, RESTART_GUN)
    precooler.start()
    ts = _cool_to_ready(precooler)
    # 已合格后丢点：没有有效回读就不能放行，合格撤销、稳定时长作废。
    verdict = precooler.tick(1.0, None, ts=ts + 1.0)
    assert verdict.event == EVENT_SAMPLE_LOST
    assert verdict.sample_ok is False
    assert verdict.ready is False
    assert verdict.in_band is False
    assert precooler.verdict.held_s == 0.0
    assert verdict.lost_samples == 1
    # 回读恢复后必须重新稳满整窗才算合格。
    ts += 1.0
    for _ in range(int(HOLD) - 1):
        ts += 1.0
        verdict = precooler.tick(1.0, TARGET, ts=ts)
    assert not verdict.ready
    ts += 1.0
    verdict = precooler.tick(1.0, TARGET, ts=ts)
    assert verdict.ready
    assert verdict.sample_ok is True
    assert verdict.lost_samples == 1


def test_lost_readback_during_window_does_not_qualify():
    precooler = Precooler(COOL, RESTART_GUN)
    precooler.start()
    ts = 0.0
    for _ in range(int(HOLD) - 1):
        ts += 1.0
        precooler.tick(1.0, TARGET, ts=ts)
    # 最后一拍回读丢点：哪怕时长凑够，也绝不能判合格。
    verdict = precooler.tick(1.0, None, ts=ts + 1.0)
    assert not verdict.ready
    assert precooler.verdict.held_s == 0.0


def test_timestamps_must_be_strictly_increasing():
    precooler = Precooler(COOL, RESTART_GUN)
    precooler.start(now=10.0)
    precooler.tick(1.0, TARGET, ts=11.0)
    with pytest.raises(ValueError):
        precooler.tick(1.0, TARGET, ts=11.0)  # 同一时刻重复回读
    with pytest.raises(ValueError):
        precooler.tick(1.0, TARGET, ts=10.5)  # 回读时序倒流


def test_tick_guards():
    precooler = Precooler(COOL, RESTART_GUN)
    with pytest.raises(ValueError):
        precooler.tick(1.0, TARGET, ts=1.0)  # 未启动
    precooler.start()
    with pytest.raises(ValueError):
        precooler.tick(0.0, TARGET, ts=1.0)  # 非正步长
    with pytest.raises(ValueError):
        precooler.tick(-1.0, TARGET, ts=1.0)


def test_verdict_dict_carries_decision_basis():
    precooler = Precooler(COOL, RESTART_GUN)
    precooler.start()
    _cool_to_ready(precooler)
    data = precooler.verdict.as_dict()
    for key in (
        "state",
        "in_band",
        "sample_ok",
        "ready",
        "temperature_c",
        "target_c",
        "band_c",
        "held_s",
        "window_s",
        "rewarm_policy",
        "rewarm_restarts",
        "rewarm_extensions",
        "extend_s",
        "extend_max",
        "lost_samples",
        "failure_reason",
        "event",
    ):
        assert key in data
    assert data["ready"] is True
    assert data["rewarm_policy"] == "restart"
