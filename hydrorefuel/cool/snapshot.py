"""预冷状态快照。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoolSnapshot:
    """预冷某一时刻的状态与合格判定依据。"""

    state: str
    temperature_c: float
    target_c: float
    held_s: float
    required_s: float
    remaining_s: float
    ready: bool
    gun_type: str
    policy: str
    gaps: int
    excursions: int
    revocations: int

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "temperature_c": round(self.temperature_c, 2),
            "target_c": round(self.target_c, 2),
            "held_s": round(self.held_s, 2),
            "required_s": round(self.required_s, 2),
            "remaining_s": round(self.remaining_s, 2),
            "ready": self.ready,
            "gun_type": self.gun_type,
            "policy": self.policy,
            "gaps": self.gaps,
            "excursions": self.excursions,
            "revocations": self.revocations,
        }
