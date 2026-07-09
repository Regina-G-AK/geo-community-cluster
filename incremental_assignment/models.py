from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssignmentConfig:
    top_k_neighbors: int
    theta: float
    delta: float
    graph_weight: float
    geo_weight: float
    community_assignment_distance_meters: float
    city_maximum_distance_meters: float
