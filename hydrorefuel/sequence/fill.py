"""加注顺序：队列确认 → 枪落座 → 预冷 → 开加注阀 → 阶梯流量核对 → 隔离停机。

加注是连带的硬顺序：确认超时不许落座，枪没落座咬合不许开阀，预冷没保持够
时长不许开阀，每一阶梯的累计质量必须落在公差内才允许推进下一阶梯；结束时
按"隔离 → 停机 → 枪回位"收尾。
"""

from __future__ import annotations

from ..cool.precool import Precooler
from ..cool.temperature import TemperatureModel
from ..cool.verdict import EVENT_STARTED
from ..disp.machine import Dispenser
from ..disp.nozzle import Nozzle
from ..errors import HydroError
from ..flow.meter import MassFlowMeter
from ..flow.step import FlowStep
from ..queue.confirm import Confirmation


def fill_vehicle(
    dispenser: Dispenser,
    nozzle: Nozzle,
    precooler: Precooler,
    temperature: TemperatureModel,
    meter: MassFlowMeter,
    step: FlowStep,
    confirm: Confirmation,
    station: str,
    vehicle: str = "vehicle01",
    rate_kg_per_min: float = 3.0,
    steps: int = 3,
    now: float = 0.0,
    dt: float = 36.0,
    max_steps: int = 6000,
    rewarm_ticks: int = 0,
    drop_ticks: int = 0,
    disturb_after_s: float | None = None,
) -> dict:
    """执行一次加注，返回带步骤与违约项的报告。

    ``rewarm_ticks`` / ``drop_ticks`` 在枪口温度首次进入目标带并稳温
    ``disturb_after_s`` 秒（默认取稳定窗口时长，即刚判合格之后）之后，分别
    注入若干拍回温、回读丢点，用于现场/验证环境核对回温作废（或延长窗口）
    与部分失败判定。
    """

    log: list[dict] = []
    violations: list[str] = []

    confirm.open(vehicle, now)
    accepted = confirm.confirm(vehicle, now + 0.5)
    log.append({"component": "queue", "action": "confirm", "detail": {"vehicle": vehicle, "accepted": accepted}})
    if not accepted:
        violations.append("queue_confirm_rejected")
        return {"station": station, "vehicle": vehicle, "ok": False, "steps": log, "violations": violations}

    try:
        dispenser.reset()
        if nozzle.is_seated():
            nozzle.unseat()
        seated = nozzle.seat(nozzle.seat_travel_mm + 5.0)
        log.append(
            {
                "component": "disp",
                "action": "seat_nozzle",
                "detail": {"seated": seated, "latched": nozzle.is_latched(), "travel_mm": nozzle.travel_mm()},
            }
        )
        if not seated:
            violations.append("nozzle_not_seated")
            return {"station": station, "vehicle": vehicle, "ok": False, "steps": log, "violations": violations}

        temperature.reset()
        trigger_s = precooler.window_s if disturb_after_s is None else disturb_after_s
        # 回温与丢点交替安排：锚点在每次扰动后重置，每个扰动都在"重新稳住
        # trigger_s 秒后"投出，保证多扰动各占一拍、顺序可控。
        for index in range(max(rewarm_ticks, drop_ticks)):
            if index < rewarm_ticks:
                temperature.arm_rewarm(1, after_s=trigger_s)
            if index < drop_ticks:
                temperature.arm_drop_readback(1, after_s=trigger_s)
        verdict = precooler.start(now)
        elapsed = 0.0
        for _ in range(max_steps):
            elapsed += dt
            reading = temperature.read(dt)
            verdict = precooler.tick(dt, reading, ts=now + elapsed)
            # 回温作废/延长、合格撤销、回读丢点都在发生的那一拍单独留痕，
            # detail 即当拍判定，与最终放行依据同源。
            if verdict.event is not None and verdict.event != EVENT_STARTED:
                log.append({"component": "cool", "action": verdict.event, "detail": verdict.as_dict()})
            # 失败即停；否则要"合格且已安排的回温/丢点扰动都投出"才算完，
            # 这样合格之后那拍的回温、丢点才真正会发生并被重新判定。
            if precooler.is_failed() or (verdict.ready and temperature.disturbances_pending() == 0):
                break
        # 预冷汇总这一步的 detail 就是最终判定对象本身：控制台与审计看到的
        # 字段与"是否放行"的依据完全一致，谁都不另算一遍。
        log.append({"component": "cool", "action": "precool", "detail": verdict.as_dict()})
        if precooler.is_failed():
            violations.append(f"precool_{verdict.failure_reason or 'failed'}")
            nozzle.unseat()
            return {"station": station, "vehicle": vehicle, "ok": False, "steps": log, "violations": violations}
        if not verdict.ready:
            violations.append("precool_not_ready")
            nozzle.unseat()
            return {"station": station, "vehicle": vehicle, "ok": False, "steps": log, "violations": violations}
        if not verdict.sample_ok or not verdict.in_band:
            # 防线：判定对象说还没稳住/回读不全时，绝不允许开阀。
            violations.append("precool_verdict_not_ready")
            nozzle.unseat()
            return {"station": station, "vehicle": vehicle, "ok": False, "steps": log, "violations": violations}

        dispenser.open_fill_valve()
        log.append({"component": "disp", "action": "open_fill_valve", "detail": {"valve_open": dispenser.fill_valve_open()}})

        for index in range(steps):
            before = meter.total_kg()
            meter.commit(rate_kg_per_min, dt)
            step.add(meter.total_kg() - before)
            within = step.within()
            log.append(
                {
                    "component": "flow",
                    "action": "step_check",
                    "detail": {
                        "index": index + 1,
                        "mass_kg": round(step.current_kg(), 3),
                        "target_kg": round(step.target_kg, 3),
                        "within": within,
                    },
                }
            )
            if not within:
                violations.append(f"flow_step_{index + 1}_mismatch")
                break
            step.advance()

        if violations:
            # 阶梯核对未过：立即隔离停机并回枪，避免管路带压留在现场。
            dispenser.isolate()
            dispenser.stop()
            nozzle.unseat()
            log.append(
                {
                    "component": "disp",
                    "action": "abort_fill",
                    "detail": {"valve_open": dispenser.fill_valve_open(), "seated": nozzle.is_seated()},
                }
            )
        else:
            log.append(
                {
                    "component": "disp",
                    "action": "fill_complete",
                    "detail": {
                        "valve_open": dispenser.fill_valve_open(),
                        "total_kg": round(meter.total_kg(), 3),
                    },
                }
            )
    except HydroError as error:
        payload = error.as_record()
        violations.append(f"{payload['component']}:{payload['type']}")
        log.append({"component": payload["component"], "action": "rejected", "detail": {"message": payload["message"]}})
        if dispenser.fill_valve_open():
            dispenser.isolate()
            dispenser.stop()
        if nozzle.is_seated():
            nozzle.unseat()

    return {"station": station, "vehicle": vehicle, "ok": not violations, "steps": log, "violations": violations}
