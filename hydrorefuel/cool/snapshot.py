"""预冷状态快照。

快照字段全部取自 :class:`~hydrorefuel.cool.verdict.PrecoolVerdict`，与审计
落盘同源，不做第二次判断。
"""

from __future__ import annotations

from dataclasses import dataclass

from .verdict import PrecoolVerdict


@dataclass(frozen=True)
class CoolSnapshot:
    """预冷某一时刻的合格判定快照。"""

    verdict: PrecoolVerdict

    @classmethod
    def from_verdict(cls, verdict: PrecoolVerdict) -> "CoolSnapshot":
        return cls(verdict=verdict)

    def as_dict(self) -> dict:
        return self.verdict.as_dict()
