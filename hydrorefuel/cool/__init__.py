"""预冷组件。

高压加注前必须把氢气冷到目标温度并稳住：枪口会回温，判合格到开阀之间
温度可能漂出允许带，因此 :class:`Precooler` 按固定时长稳定窗口判定合格，
回温超限按枪型处置（整段作废或延长窗口），回读丢点一律作废当前窗口。
:class:`TemperatureModel` 按冷速降温、按回温速率升温，编排据此决定是否
允许开加注阀。
"""

from __future__ import annotations

from .precool import Precooler
from .snapshot import CoolSnapshot
from .temperature import TemperatureModel

__all__ = ["Precooler", "TemperatureModel", "CoolSnapshot"]
