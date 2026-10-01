"""预冷温度与回读模型。

温度从环境温度按冷速线性下降并夹在目标温度上，供稳定窗口判断使用。模型是
显式推进的，验证环境不需要真实等待制冷机组。

模型同时代表温度回读通道：

* 正常情况下每次 :meth:`read` 降温一个步长并返回读数；
* 枪口在流量切换或停顿时会回温，:meth:`arm_rewarm` 可预先安排"进入目标带
  并稳温若干秒之后"的若干拍按冷速四分之一幅度回温，用来验证回温超带后的
  作废/延长判定；
* 回读通道可能丢点，:meth:`arm_drop_readback` 安排的拍次 :meth:`read`
  返回 ``None``，判定侧据此按部分失败处理（不能判合格）。

扰动按"相对锚点的稳温秒数"调度：锚点从首次进带开始；一旦回温出带或回读
丢点，锚点重置到"重新进带"那一刻。因此每个扰动都是"上一次扰动后重新稳住
到对应秒数"才投出，不会在出带恢复途中被提前吞掉，且与推进步长无关。
"""

from __future__ import annotations

from collections import deque

from ..config import CoolLimits

AMBIENT_C = 25.0
# 回温取冷速的四分之一：枪口回温是温和越带，而不是制冷彻底中断的整冷速反冲。
WARM_RATE_FRACTION = 0.25

_COOL = "cool"
_WARM = "warm"
_DROP = "drop"


class TemperatureModel:
    """加注枪口温度与回读模型。"""

    def __init__(self, limits: CoolLimits, start_c: float = AMBIENT_C) -> None:
        self._limits = limits
        self._value_c = float(start_c)
        self._band_seen = False
        # 自锚点（首次进带 / 上次扰动 / 重新进带）以来在带内累计的秒数。
        self._anchored_s = 0.0
        # 待投扰动：(相对锚点延迟秒数, 动作)，按延迟升序。
        self._armed: deque[tuple[float, str]] = deque()

    @property
    def value_c(self) -> float:
        return self._value_c

    def disturbances_pending(self) -> int:
        """尚未投出的扰动拍数。"""

        return len(self._armed)

    def _arm(self, kind: str, ticks: int, after_s: float) -> None:
        if ticks < 1:
            raise ValueError("扰动拍数必须为正")
        if after_s < 0.0:
            raise ValueError("触发时刻不能为负")
        # 每个扰动共用同一"相对锚点延迟"：锚点在每次投出后重置，因此多个
        # 扰动各自在"上一次扰动后重新稳住 after_s 秒"的那一拍依次投出。
        for _ in range(ticks):
            self._armed.append((after_s, kind))

    def arm_rewarm(self, ticks: int = 1, *, after_s: float) -> None:
        """安排进带稳温 ``after_s`` 秒后的若干拍按冷速四分之一幅度回温。"""

        self._arm(_WARM, ticks, after_s)

    def arm_drop_readback(self, ticks: int = 1, *, after_s: float) -> None:
        """安排进带稳温 ``after_s`` 秒后的若干拍回读丢点。"""

        self._arm(_DROP, ticks, after_s)

    def read(self, dt: float) -> float | None:
        """推进一个回读步长，返回本拍温度；安排了丢点时返回 ``None``。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        rate = self._limits.cool_rate_c_per_s

        # 扰动只在"上一拍已稳温到延迟时刻"之后才投出：进带那一拍哪怕一次
        # 性稳满整个大步长，也先让判定在该拍成立（合格），扰动落在其后的下
        # 一拍，正好复现"刚判合格、开阀瞬间就回温/丢点"。
        action = _COOL
        armed = self._armed
        if self._band_seen and armed and self._anchored_s >= armed[0][0]:
            _, action = armed.popleft()
            # 锚点移到本次扰动：后续扰动按"重新稳住后的秒数"计时。
            self._anchored_s = 0.0

        if action == _DROP:
            # 丢点：这一拍没有有效读数，温度不变；锚点计时作废。
            self._anchored_s = 0.0
            return None
        if action == _WARM:
            self._value_c = min(AMBIENT_C, self._value_c + WARM_RATE_FRACTION * rate * dt)
        else:
            self._value_c = max(self._limits.target_temp_c, self._value_c - rate * dt)
        if self._limits.reached(self._value_c):
            self._band_seen = True
            self._anchored_s += dt
        else:
            # 出带即重置锚点，与稳定窗口"中途回温超带要重来"保持一致。
            self._anchored_s = 0.0
            self._band_seen = False
        return self._value_c

    def reset(self) -> None:
        """把温度恢复到环境温度并清空待生效的扰动安排。"""

        self._value_c = AMBIENT_C
        self._band_seen = False
        self._anchored_s = 0.0
        self._armed.clear()
