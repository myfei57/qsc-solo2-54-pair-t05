"""预冷合格判定对象。

合格判定、控制台展示与审计落盘必须用同一份依据，因此 :class:`Precooler`
每次回读推进后只产出一个 :class:`PrecoolVerdict`：当前状态机状态、温度是否
在目标带、回读是否丢点、已稳住时长与稳定窗口、回温作废/延长/丢点的次数、
失败原因以及"这一拍触发了什么事件"都在里面。控制台快照和审计事件都从它
取数，谁也不另算一遍，避免"判合格"和"落审计"口径不一致。
"""

from __future__ import annotations

from dataclasses import dataclass

# 状态机状态。
IDLE = "idle"          # 尚未开始本轮预冷
COOLING = "cooling"    # 还在往目标带降温，未开始稳定窗口
HOLDING = "holding"    # 已进入目标带，正在累计连续稳定时长
READY = "ready"        # 已稳满窗口、回读有效且仍在带内，允许放行
FAILED = "failed"      # 本枪型策略下无法再救（延长次数耗尽），整轮失败

# 失败原因（None 表示当前未失败）。
FAIL_EXTENSIONS_EXHAUSTED = "rewarm_extensions_exhausted"

# 单拍事件：只在状态/判定发生变化的那一拍产生，供审计逐拍留痕。
EVENT_STARTED = "precool_started"
EVENT_ENTER_BAND = "precool_enter_band"
EVENT_READY = "precool_ready"
EVENT_REWARM_RESTART = "precool_rewarm_restart"
EVENT_REWARM_EXTEND = "precool_rewarm_extend"
EVENT_REWARM_REVOKE_READY = "precool_rewarm_revoke_ready"
EVENT_SAMPLE_LOST = "precool_sample_lost"
EVENT_EXTENSIONS_EXHAUSTED = "precool_extensions_exhausted"


@dataclass(frozen=True)
class PrecoolVerdict:
    """一拍温度回读后的预冷合格判定。"""

    state: str
    in_band: bool
    sample_ok: bool
    ready: bool
    temperature_c: float | None
    target_c: float
    band_c: float
    held_s: float
    window_s: float
    rewarm_policy: str
    rewarm_restarts: int
    rewarm_extensions: int
    extend_s: float
    extend_max: int
    lost_samples: int
    failure_reason: str | None
    event: str | None

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "in_band": self.in_band,
            "sample_ok": self.sample_ok,
            "ready": self.ready,
            "temperature_c": None if self.temperature_c is None else round(self.temperature_c, 2),
            "target_c": round(self.target_c, 2),
            "band_c": round(self.band_c, 2),
            "held_s": round(self.held_s, 2),
            "window_s": round(self.window_s, 2),
            "rewarm_policy": self.rewarm_policy,
            "rewarm_restarts": self.rewarm_restarts,
            "rewarm_extensions": self.rewarm_extensions,
            "extend_s": round(self.extend_s, 2),
            "extend_max": self.extend_max,
            "lost_samples": self.lost_samples,
            "failure_reason": self.failure_reason,
            "event": self.event,
        }
