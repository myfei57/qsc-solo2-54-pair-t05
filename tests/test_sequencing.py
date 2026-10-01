"""端到端顺序编排。"""

from __future__ import annotations

from support import load_station


def test_full_chain_runs_in_order(platform):
    platform.start()
    load_station(platform)
    fill = platform.fill(vehicle="vehicle01", rate_kg_per_min=3.0, steps=3)
    assert fill["ok"], fill["violations"]
    stop = platform.stop_fill()
    assert stop["ok"], stop["violations"]
    assert [step["action"] for step in stop["steps"]] == ["isolate", "stop", "unseat_nozzle"]
    cycle = platform.cycle()
    assert cycle["cycle"] == 1
    assert platform.dispenser.state == "stopped"
    assert not platform.nozzle.is_seated()


def test_fill_leaves_dispenser_filling_until_stop(platform):
    platform.start()
    load_station(platform)
    platform.fill(steps=1)
    assert platform.dispenser.is_filling()
    assert platform.nozzle.is_seated()
    platform.stop_fill()
    assert platform.dispenser.state == "stopped"


def test_flow_step_mismatch_aborts_fill(platform):
    platform.start()
    load_station(platform)
    fill = platform.fill(steps=1, rate_kg_per_min=3.0, dt=30.0)
    assert not fill["ok"]
    assert "flow_step_1_mismatch" in fill["violations"]
    assert platform.dispenser.fill_valve_open() is False
    assert not platform.nozzle.is_seated()


def test_precool_happens_before_fill_valve(platform):
    platform.start()
    load_station(platform)
    report = platform.fill(steps=1)
    actions = [step["action"] for step in report["steps"]]
    assert actions.index("precool") < actions.index("open_fill_valve")


def _precool_detail(report):
    return next(step for step in report["steps"] if step["action"] == "precool")["detail"]


def test_rewarm_after_entry_restarts_window_and_eventually_fills(platform):
    platform.start()
    load_station(platform)
    # 小步长才能区分"进带→稳住→合格后回温"的时序；速率按 5 秒命中 1.8kg。
    report = platform.fill(steps=1, dt=30.0, rate_kg_per_min=3.6, rewarm_ticks=1)
    # restart 枪型：回温超带整段作废，重新稳住后仍然合格，加注照常完成。
    assert report["ok"], report["violations"]
    actions = [step["action"] for step in report["steps"]]
    assert "precool_rewarm_revoke_ready" in actions
    assert actions.index("precool_rewarm_revoke_ready") < actions.index("open_fill_valve")
    detail = _precool_detail(report)
    assert detail["ready"] is True
    assert detail["sample_ok"] is True
    assert detail["in_band"] is True
    assert detail["rewarm_restarts"] == 1


def test_lost_readback_blocks_valve_then_recovers(platform):
    platform.start()
    load_station(platform)
    report = platform.fill(steps=1, dt=30.0, rate_kg_per_min=3.6, drop_ticks=1)
    # 回读丢点是部分失败：这一轮在丢点后重新稳住，最终仍可成功加注。
    assert report["ok"], report["violations"]
    actions = [step["action"] for step in report["steps"]]
    assert "precool_sample_lost" in actions
    assert actions.index("precool_sample_lost") < actions.index("open_fill_valve")
    detail = _precool_detail(report)
    assert detail["ready"] is True
    assert detail["lost_samples"] == 1
    # 丢点那一拍绝不能判合格：所有丢点事件的 ready 都必须是 False。
    for step in report["steps"]:
        if step["action"] == "precool_sample_lost":
            assert step["detail"]["ready"] is False
            assert step["detail"]["sample_ok"] is False


def test_ready_is_revoked_on_rewarm_and_valve_not_opened_until_restable(platform):
    platform.start()
    load_station(platform)
    report = platform.fill(steps=1, dt=30.0, rate_kg_per_min=3.6, rewarm_ticks=1)
    assert report["ok"], report["violations"]
    actions = [step["action"] for step in report["steps"]]
    revoke = [i for i, action in enumerate(actions) if action == "precool_rewarm_revoke_ready"]
    assert revoke, actions
    ready_indexes = [i for i, action in enumerate(actions) if action == "precool_ready"]
    assert ready_indexes and ready_indexes[-1] > revoke[-1]
    # 开阀只能发生在最后一次重新判合格之后。
    assert actions.index("open_fill_valve") > ready_indexes[-1]


def test_extend_gun_extends_window_and_recovers(extend_gun_platform):
    platform = extend_gun_platform
    platform.start()
    load_station(platform)
    # extend 枪型：合格后回温只延长窗口，保留已稳住时长，随后仍能合格放行。
    report = platform.fill(steps=1, dt=30.0, rate_kg_per_min=3.6, rewarm_ticks=1)
    assert report["ok"], report["violations"]
    detail = _precool_detail(report)
    assert detail["ready"] is True
    assert detail["rewarm_policy"] == "extend"
    assert detail["rewarm_extensions"] == 1
    assert detail["window_s"] == 20.0 + 10.0


def test_extend_gun_fails_and_blocks_valve_when_budget_exhausted(extend_gun_platform):
    platform = extend_gun_platform
    platform.start()
    load_station(platform)
    # 该枪型最多延长一次；合格后连续两次回温，第二次耗尽预算，整轮失败。
    report = platform.fill(steps=1, dt=30.0, rate_kg_per_min=3.6, rewarm_ticks=2)
    assert not report["ok"]
    assert "precool_rewarm_extensions_exhausted" in report["violations"]
    actions = [step["action"] for step in report["steps"]]
    assert "precool_extensions_exhausted" in actions
    # 失败绝不允许开阀。
    assert "open_fill_valve" not in actions
    assert platform.dispenser.fill_valve_open() is False
    assert not platform.nozzle.is_seated()
    # 终态失败体现在快照与就绪判据上。
    assert platform.cool_snapshot()["state"] == "failed"
    assert platform.readiness()["checks"]["precool_not_failed"] is False
