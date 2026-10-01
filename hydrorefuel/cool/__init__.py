"""预冷组件。

高压加注前必须把氢气冷到目标温度并在**固定时长**稳定窗口内连续稳住，否则
枪口温升会触发跳枪。:class:`TemperatureModel` 按冷速降温并可注入回温/回读
丢点，:class:`Precooler` 依据枪型策略（整段作废重来或延长窗口）产出唯一的
:class:`PrecoolVerdict`：合格不是一次性锁存，判合格后回温超带或回读丢点都
会撤销合格，重新稳住才放行；控制台展示与审计落盘都从同一份判定取数。
"""

from __future__ import annotations

from .precool import Precooler
from .snapshot import CoolSnapshot
from .temperature import TemperatureModel
from .verdict import PrecoolVerdict

__all__ = ["Precooler", "TemperatureModel", "CoolSnapshot", "PrecoolVerdict"]
