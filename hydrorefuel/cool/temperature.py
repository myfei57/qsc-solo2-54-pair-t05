"""预冷温度模型。

温度从环境温度按冷速线性下降并夹在目标温度上，供预冷稳定窗口判定使用；
停止制冷时枪口按回温速率向环境温度回升。模型是显式推进的，验证环境不
需要真实等待制冷机组。回读丢点通过 :meth:`TemperatureModel.drop_reads`
注入，用来验证"丢点即部分失败"的判定路径。
"""

from __future__ import annotations

from ..config import CoolLimits

AMBIENT_C = 25.0


class TemperatureModel:
    """加注枪口温度模型。"""

    def __init__(self, limits: CoolLimits, start_c: float = AMBIENT_C) -> None:
        self._limits = limits
        self._value_c = float(start_c)
        self._pending_drops = 0

    @property
    def limits(self) -> CoolLimits:
        return self._limits

    @property
    def value_c(self) -> float:
        return self._value_c

    def cool(self, dt: float) -> float:
        """按冷速降温一个步长，返回降温后的温度。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        self._value_c = max(self._limits.target_temp_c, self._value_c - self._limits.cool_rate_c_per_s * dt)
        return self._value_c

    def rewarm(self, dt: float) -> float:
        """按回温速率升温一个步长（向环境温度回升），返回升温后的温度。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        self._value_c = min(AMBIENT_C, self._value_c + self._limits.rewarm_rate_c_per_s * dt)
        return self._value_c

    def drop_reads(self, count: int) -> None:
        """注入接下来 count 次回读丢点（验证部分失败路径用）。"""

        if count < 1:
            raise ValueError("丢点次数必须为正")
        self._pending_drops += int(count)

    def read(self) -> float | None:
        """回读当前温度；丢点注入生效期间返回 None。"""

        if self._pending_drops > 0:
            self._pending_drops -= 1
            return None
        return self._value_c

    def reached(self) -> bool:
        """温度是否已落到加注允许带。"""

        return self._limits.reached(self._value_c)

    def reset(self) -> None:
        """把温度恢复到环境温度，并清掉未消耗的丢点注入。"""

        self._value_c = AMBIENT_C
        self._pending_drops = 0
