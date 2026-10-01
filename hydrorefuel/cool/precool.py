"""预冷合格判定。

仅把温度降到目标带不算合格：枪口会回温，判合格到开阀之间温度可能又漂出
允许带。判定规则在这里定死：

* 稳定窗口按**固定时长**判定（``precool_hold_s``），窗口内温度必须全程位于
  允许带（``target_temp_c ± temp_band_c``）；不采用波动带判据——允许带就是
  唯一的温度界限，判定与审计才有同一个锚点。
* 回温超限（温度高于带上沿）按**枪型**处置：``restart`` 整段作废重新稳；
  ``extend`` 保留已稳时长、窗口要求延长 ``rewarm_extend_s`` 后再看。
  过冷超限（低于带下沿）一律整段作废，不适用延长政策。
* 已合格后再超限，合格一律作废，必须重新稳满一个完整窗口才放行。
* 温度回读丢点属于部分失败：窗口性能否合格取决于证据链是否完整，丢点即
  作废当前窗口（已合格的同样作废），只有无丢点的窗口才能判合格。
* 状态只按 ``idle → cooling → stabilizing → ready`` 顺序推进，任何回退都
  回到 ``cooling`` 重新进带；新一轮判定必须先 ``reset`` 再 ``start``。

判定依据由 :meth:`Precooler.verdict` 统一给出，控制台快照与审计都取它，
不会出现"判的一套、记的另一套"。
"""

from __future__ import annotations

from ..config import POLICY_EXTEND, POLICY_RESTART, CoolLimits

IDLE = "idle"
COOLING = "cooling"
STABILIZING = "stabilizing"
READY = "ready"


class Precooler:
    """预冷稳定窗口与合格判定。"""

    def __init__(self, limits: CoolLimits) -> None:
        self._limits = limits
        self._state = IDLE
        self._gun_type = ""
        self._policy = ""
        self._held_s = 0.0
        self._required_s = limits.precool_hold_s
        self._gaps = 0
        self._excursions = 0
        self._revocations = 0
        self._events: list[dict] = []

    @property
    def state(self) -> str:
        return self._state

    @property
    def held_s(self) -> float:
        """当前窗口已稳定保持的时长。"""

        return self._held_s

    @property
    def required_s(self) -> float:
        """当前窗口要求稳满的时长（延长政策下会大于基准值）。"""

        return self._required_s

    @property
    def gaps(self) -> int:
        """本轮判定累计的回读丢点次数。"""

        return self._gaps

    @property
    def excursions(self) -> int:
        """本轮判定窗口内累计的超限次数。"""

        return self._excursions

    @property
    def revocations(self) -> int:
        """本轮判定合格被作废的次数。"""

        return self._revocations

    def start(self, gun_type: str) -> None:
        """按枪型启动一轮预冷判定；上一轮必须先 ``reset``。"""

        if self._state != IDLE:
            raise ValueError("预冷判定已在进行，必须先复位再启动")
        self._gun_type = gun_type
        self._policy = self._limits.policy_for(gun_type)
        self._state = COOLING
        self._held_s = 0.0
        self._required_s = self._limits.precool_hold_s
        self._gaps = 0
        self._excursions = 0
        self._revocations = 0
        self._events = []

    def tick(self, dt: float, temperature_c: float | None) -> bool:
        """推进一次判定，返回是否已经合格；``None`` 表示本次回读丢点。"""

        if self._state == IDLE:
            raise ValueError("预冷尚未启动")
        if dt <= 0:
            raise ValueError("步长必须为正")
        if temperature_c is None:
            self._on_gap()
            return False
        if self._limits.reached(temperature_c):
            self._on_in_band(dt)
        else:
            self._on_out_of_band(temperature_c)
        return self._state == READY

    def _on_gap(self) -> None:
        """回读丢点：证据断链，当前窗口（含已合格）一律作废。"""

        self._gaps += 1
        revoked = self._state == READY
        if revoked:
            self._revocations += 1
        held_before = self._held_s
        if self._state in (STABILIZING, READY):
            self._void_window()
        self._emit(
            "precool_readback_gap",
            "warning",
            revoked=revoked,
            gaps=self._gaps,
            held_s=round(held_before, 2),
        )

    def _on_in_band(self, dt: float) -> None:
        if self._state == COOLING:
            self._state = STABILIZING
            self._emit(
                "precool_stabilizing",
                "notice",
                held_s=round(self._held_s, 2),
                required_s=round(self._required_s, 2),
            )
        self._held_s += dt
        if self._state == STABILIZING and self._held_s >= self._required_s:
            self._state = READY
            self._emit(
                "precool_qualified",
                "notice",
                held_s=round(self._held_s, 2),
                required_s=round(self._required_s, 2),
            )

    def _on_out_of_band(self, temperature_c: float) -> None:
        if self._state == COOLING:
            # 尚未进带，属于正常降温过程，不计超限。
            return
        rewarmed = self._limits.rewarmed(temperature_c)
        overcooled = self._limits.overcooled(temperature_c)
        held_before = self._held_s
        if self._state == READY:
            # 已合格后超限：合格一律作废，重新稳满一个完整窗口。
            self._revocations += 1
            self._void_window()
            self._emit(
                "precool_revoked",
                "warning",
                reason="rewarm" if rewarmed else "overcool",
                temperature_c=round(temperature_c, 2),
                held_s=round(held_before, 2),
                revocations=self._revocations,
            )
            return
        self._excursions += 1
        if rewarmed and self._policy == POLICY_EXTEND:
            # 延长稳定窗口再看：保留已稳时长，窗口要求延长，回带后继续累计。
            self._required_s += self._limits.rewarm_extend_s
            self._state = COOLING
            self._emit(
                "precool_rewarm",
                "warning",
                applied=POLICY_EXTEND,
                held_s=round(self._held_s, 2),
                required_s=round(self._required_s, 2),
                temperature_c=round(temperature_c, 2),
                excursions=self._excursions,
            )
        else:
            # restart 政策，以及不适用延长政策的过冷超限：整段作废重来。
            self._void_window()
            self._emit(
                "precool_overcool" if overcooled else "precool_rewarm",
                "warning",
                applied=POLICY_RESTART,
                held_s=round(held_before, 2),
                required_s=round(self._required_s, 2),
                temperature_c=round(temperature_c, 2),
                excursions=self._excursions,
            )

    def _void_window(self) -> None:
        """作废当前窗口：保持时长与窗口要求归零，回到降温段重新进带。"""

        self._held_s = 0.0
        self._required_s = self._limits.precool_hold_s
        self._state = COOLING

    def _emit(self, action: str, severity: str, **detail: object) -> None:
        self._events.append(
            {
                "action": action,
                "severity": severity,
                "detail": {"gun_type": self._gun_type, "policy": self._policy, **detail},
            }
        )

    def is_ready(self) -> bool:
        """预冷是否已判合格（稳定窗口完整且无丢点）。"""

        return self._state == READY

    def verdict(self) -> dict:
        """返回当前合格判定的全部依据（控制台与审计共用同一份）。"""

        return {
            "state": self._state,
            "ready": self._state == READY,
            "gun_type": self._gun_type,
            "policy": self._policy,
            "held_s": round(self.held_s, 2),
            "required_s": round(self.required_s, 2),
            "remaining_s": round(max(0.0, self.required_s - self.held_s), 2),
            "gaps": self.gaps,
            "excursions": self.excursions,
            "revocations": self.revocations,
            "target_c": self._limits.target_temp_c,
            "band_c": self._limits.temp_band_c,
        }

    def drain_events(self) -> list[dict]:
        """取走自上次以来累积的判定事件，编排据此写审计。"""

        events = list(self._events)
        self._events.clear()
        return events

    def reset(self) -> bool:
        """复位预冷判定，返回之前是否已合格。"""

        was_ready = self._state == READY
        self._state = IDLE
        self._gun_type = ""
        self._policy = ""
        self._held_s = 0.0
        self._required_s = self._limits.precool_hold_s
        self._gaps = 0
        self._excursions = 0
        self._revocations = 0
        self._events = []
        return was_ready
