from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PathConfig:
    algorithm_one_directory: Path
    candidate_merchants_path: Path
    output_directory: Path
    observation_pool_path: Path
    merchant_archive_path: Path
    community_archive_path: Path
    experiments_path: Path
    coordinates_path: Path | None


@dataclass(frozen=True)
class AssignmentConfig:
    min_observation_days: int
    min_unique_users: int
    top_k_neighbors: int
    anchor_vote_weight: float
    theta: float
    delta: float
    graph_weight: float
    geo_weight: float
    customer_weight: float
    minimum_sigma_meters: float
    community_assignment_distance_meters: float
    city_maximum_distance_meters: float


@dataclass(frozen=True)
class AppConfig:
    paths: PathConfig
    assignment: AssignmentConfig


@dataclass(frozen=True)
class CandidateMerchant:
    city_code: str
    merchant_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    unique_user_count: int
    customer_ids: frozenset[str]
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class MerchantScores:
    graph_scores: dict[int, float]
    geo_scores: dict[int, float]
    customer_scores: dict[int, float]
    final_scores: dict[int, float]
    geo_available: bool
    graph_best_community_id: int | None
    geo_best_community_id: int | None


@dataclass(frozen=True)
class DecisionResult:
    city_code: str
    merchant_id: str
    decision: str
    assigned_community_id: int | None
    top_community_id: int | None
    second_community_id: int | None
    top_score: float
    second_score: float
    score_margin: float
    graph_score: float
    geo_score: float
    customer_score: float
    reason: str
    observation_days: int
    unique_user_count: int


@dataclass(frozen=True)
class RunSummary:
    output_directory: str
    candidate_count: int
    assigned_count: int
    observation_count: int
    manual_review_count: int
    duration_seconds: float
