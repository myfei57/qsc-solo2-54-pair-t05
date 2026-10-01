"""预冷稳定窗口与合格判定。

只把温度降到目标带不算合格：枪口会回温，"刚判合格、开阀瞬间又不合格"必须
被挡下。因此判定规则是：

1. 温度进入目标带后，必须在**固定时长**稳定窗口内连续稳住
   （``precool_hold_s``），中途回温超带或回读丢点都会打断连续窗口；
2. 合格不是一次性锁存：稳满之后只要回读仍有效且仍在带内才算合格；一旦
   回温超带，合格立即撤销，必须重新稳住才能放行；
3. 回温超带后是"整段作废重来"还是"延长稳定窗口再看"，由枪型策略决定
   （:class:`~hydrorefuel.config.GunProfile`）：

   * ``restart``：已稳住时长清零、延长次数清零，重新进带重新稳满整窗；
   * ``extend``：保留已稳住时长，把窗口再延长 ``rewarm_extend_s`` 秒
     继续观察；延长次数累计到 ``rewarm_extend_max`` 后仍未稳满，即整轮
     失败（``failed``），必须重新发起预冷；

4. 温度回读丢点属于**部分失败**：这一拍不能推进稳定窗口，已稳住时长作废，
   且绝不能在此拍判合格；需要后续回读恢复并重新稳满窗口才算；
5. 回读时序必须单调递增，乱序回读直接报错——稳定窗口有时序，不能乱。

每次 :meth:`Precooler.tick` 产出唯一的 :class:`PrecoolVerdict`，控制台
展示与审计落盘都从它取数，判定依据保持一致。
"""

from __future__ import annotations

from ..config import CoolLimits, GunProfile
from .verdict import (
    COOLING,
    EVENT_ENTER_BAND,
    EVENT_EXTENSIONS_EXHAUSTED,
    EVENT_READY,
    EVENT_REWARM_EXTEND,
    EVENT_REWARM_RESTART,
    EVENT_REWARM_REVOKE_READY,
    EVENT_SAMPLE_LOST,
    EVENT_STARTED,
    FAILED,
    FAIL_EXTENSIONS_EXHAUSTED,
    HOLDING,
    IDLE,
    READY,
    PrecoolVerdict,
)


class Precooler:
    """预冷稳定窗口状态机。"""

    def __init__(self, limits: CoolLimits, gun: GunProfile) -> None:
        self._limits = limits
        self._gun = gun
        self._reset_state()

    def _reset_state(self) -> None:
        self._state = IDLE
        self._held_s = 0.0
        self._window_s = self._limits.precool_hold_s
        self._restarts = 0
        self._extensions = 0
        self._lost = 0
        self._failure_reason: str | None = None
        self._last_ts: float | None = None
        self._last_temperature_c: float | None = None
        self._verdict = self._verdict_for(sample_ok=True, event=None)

    @property
    def window_s(self) -> float:
        return self._window_s

    @property
    def verdict(self) -> PrecoolVerdict:
        return self._verdict

    def start(self, now: float = 0.0) -> PrecoolVerdict:
        """发起一轮新的预冷，所有稳定窗口计数归零。"""

        self._reset_state()
        self._last_ts = float(now)
        self._state = COOLING
        self._verdict = self._verdict_for(sample_ok=True, event=EVENT_STARTED)
        return self._verdict

    def tick(
        self,
        dt: float,
        temperature_c: float | None,
        ts: float,
    ) -> PrecoolVerdict:
        """按一拍温度回读推进稳定窗口，返回本拍合格判定。

        ``temperature_c`` 传 ``None`` 表示这一拍温度回读丢点；``ts`` 为回读
        时刻，必须相对上一拍严格递增（稳定窗口有时序，不能乱）。
        """

        if self._state == IDLE:
            raise ValueError("预冷尚未启动")
        if dt <= 0:
            raise ValueError("步长必须为正")
        if self._last_ts is not None and float(ts) <= self._last_ts:
            raise ValueError("温度回读时序必须严格递增，不能乱序补点")
        self._last_ts = float(ts)

        if temperature_c is None:
            # 部分失败：丢点这拍不推进窗口、不作废整轮，但连续稳定被打断，
            # 已稳住时长清零，并且这一拍绝不可能合格。
            self._lost += 1
            self._held_s = 0.0
            if self._state == READY:
                # 已合格后丢点同样撤销合格：没有有效回读就不能放行。
                self._state = HOLDING
            # COOLING/HOLDING/FAILED 原样保留，等回读恢复后继续/重稳。
            self._verdict = self._verdict_for(sample_ok=False, event=EVENT_SAMPLE_LOST)
            return self._verdict

        self._last_temperature_c = float(temperature_c)
        in_band = self._limits.reached(float(temperature_c))

        if self._state == FAILED:
            # 失败是终态，只能靠 start() 重开一轮；后续回读不再改变判定。
            self._verdict = self._verdict_for(sample_ok=True, event=None)
            return self._verdict

        if in_band:
            self._on_in_band(dt)
        else:
            self._on_rewarm()

        return self._verdict

    def _on_in_band(self, dt: float) -> None:
        event: str | None = None
        if self._state in (COOLING,):
            self._state = HOLDING
            event = EVENT_ENTER_BAND
        self._held_s += dt
        if self._state != READY and self._held_s >= self._window_s:
            self._state = READY
            event = EVENT_READY
        self._verdict = self._verdict_for(sample_ok=True, event=event)

    def _on_rewarm(self) -> None:
        # 还没进过带（仍在往目标带降温）时的出带是正常降温，不算回温：
        # 回温只可能发生在已经稳住/合格之后。
        if self._state == COOLING:
            self._verdict = self._verdict_for(sample_ok=True, event=None)
            return
        was_ready = self._state == READY

        if self._gun.rewarm_policy == "restart":
            # 整段作废重来：稳定窗口与延长计数全部清零，重新进带重新稳满。
            self._held_s = 0.0
            self._extensions = 0
            self._window_s = self._limits.precool_hold_s
            self._restarts += 1
            self._state = COOLING
            event = EVENT_REWARM_REVOKE_READY if was_ready else EVENT_REWARM_RESTART
            self._verdict = self._verdict_for(sample_ok=True, event=event)
            return

        # extend：保留已稳住时长，把稳定窗口再延长一段继续看。
        if self._extensions >= self._gun.rewarm_extend_max:
            self._state = FAILED
            self._failure_reason = FAIL_EXTENSIONS_EXHAUSTED
            self._held_s = 0.0
            self._verdict = self._verdict_for(sample_ok=True, event=EVENT_EXTENSIONS_EXHAUSTED)
            return
        self._extensions += 1
        self._window_s += self._gun.rewarm_extend_s
        self._state = HOLDING
        # 已合格后回温：合格被撤销（作废/延长的动作由计数器与窗口体现）。
        event = EVENT_REWARM_REVOKE_READY if was_ready else EVENT_REWARM_EXTEND
        self._verdict = self._verdict_for(sample_ok=True, event=event)

    def _verdict_for(self, *, sample_ok: bool, event: str | None) -> PrecoolVerdict:
        in_band = (
            sample_ok
            and self._last_temperature_c is not None
            and bool(self._limits.reached(self._last_temperature_c))
        )
        ready = sample_ok and self._state == READY and in_band
        return PrecoolVerdict(
            state=self._state,
            in_band=in_band,
            sample_ok=sample_ok,
            ready=ready,
            temperature_c=None if not sample_ok else self._last_temperature_c,
            target_c=self._limits.target_temp_c,
            band_c=self._limits.temp_band_c,
            held_s=self._held_s,
            window_s=self._window_s,
            rewarm_policy=self._gun.rewarm_policy,
            rewarm_restarts=self._restarts,
            rewarm_extensions=self._extensions,
            extend_s=self._gun.rewarm_extend_s,
            extend_max=self._gun.rewarm_extend_max,
            lost_samples=self._lost,
            failure_reason=self._failure_reason,
            event=event,
        )

    def is_failed(self) -> bool:
        """本轮预冷是否已终态失败（需重新发起）。"""

        return self._state == FAILED

    def reset(self) -> bool:
        """复位预冷状态，返回之前是否已合格。"""

        was_ready = self._verdict.ready
        self._reset_state()
        return was_ready
