from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssignmentConfig:
    community_assignment_distance_meters: float
