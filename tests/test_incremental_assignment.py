from datetime import datetime

import pandas as pd

from incremental_assignment.decision import decide_candidate
from incremental_assignment.models import AssignmentConfig, CandidateMerchant
from incremental_assignment.scoring import calculate_scores


def _config() -> AssignmentConfig:
    return AssignmentConfig(
        min_observation_days=28,
        min_unique_users=5,
        top_k_neighbors=15,
        anchor_vote_weight=1.5,
        theta=0.55,
        delta=0.10,
        graph_weight=0.6,
        geo_weight=0.3,
        customer_weight=0.1,
        minimum_sigma_meters=100.0,
        community_assignment_distance_meters=3000.0,
        city_maximum_distance_meters=50000.0,
    )


def _candidate(
    merchant_id: str,
    latitude: float | None,
    longitude: float | None,
) -> CandidateMerchant:
    return CandidateMerchant(
        city_code="shanghai",
        merchant_id=merchant_id,
        first_seen_at=datetime.fromisoformat("2026-01-01T00:00:00"),
        last_seen_at=datetime.fromisoformat("2026-02-01T00:00:00"),
        unique_user_count=8,
        customer_ids=frozenset({"u1", "u2", "u3"}),
        latitude=latitude,
        longitude=longitude,
    )


def test_graph_score_uses_anchor_weighted_votes() -> None:
    merchant_archive = pd.DataFrame(
        [
            {
                "city_code": "shanghai",
                "merchant_id": "a",
                "community_id": 1,
                "is_anchor": 1,
                "assignment_confidence": 1.0,
                "assignment_source": "algorithm_one",
                "latitude": "",
                "longitude": "",
                "customer_ids": "u1|u2",
            },
            {
                "city_code": "shanghai",
                "merchant_id": "b",
                "community_id": 2,
                "is_anchor": 0,
                "assignment_confidence": 1.0,
                "assignment_source": "algorithm_one",
                "latitude": "",
                "longitude": "",
                "customer_ids": "u3",
            },
        ]
    )
    community_archive = pd.DataFrame(
        [
            {
                "city_code": "shanghai",
                "community_id": 1,
                "merchant_count": 1,
                "anchor_count": 1,
                "anchor_merchants": "a",
                "customer_ids": "u1|u2",
                "centroid_latitude": "",
                "centroid_longitude": "",
                "sigma_meters": 100.0,
            },
            {
                "city_code": "shanghai",
                "community_id": 2,
                "merchant_count": 1,
                "anchor_count": 0,
                "anchor_merchants": "",
                "customer_ids": "u3",
                "centroid_latitude": "",
                "centroid_longitude": "",
                "sigma_meters": 100.0,
            },
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "city_code": "shanghai",
                "merchant_a": "x",
                "merchant_b": "a",
                "weight": 1.0,
                "sppmi": 1.0,
            },
            {
                "city_code": "shanghai",
                "merchant_a": "x",
                "merchant_b": "b",
                "weight": 1.0,
                "sppmi": 1.0,
            },
        ]
    )

    scores = calculate_scores(
        _candidate("x", None, None),
        edges,
        merchant_archive,
        community_archive,
        _config(),
    )

    assert round(scores.graph_scores[1], 6) == 0.6
    assert round(scores.graph_scores[2], 6) == 0.4
    assert scores.geo_available is False
    assert scores.final_scores[1] > scores.final_scores[2]


def test_geo_graph_conflict_goes_to_manual_review() -> None:
    merchant_archive = pd.DataFrame(
        [
            {
                "city_code": "shanghai",
                "merchant_id": "graph-anchor",
                "community_id": 1,
                "is_anchor": 1,
                "assignment_confidence": 1.0,
                "assignment_source": "algorithm_one",
                "latitude": 31.0,
                "longitude": 121.0,
                "customer_ids": "u1",
            },
            {
                "city_code": "shanghai",
                "merchant_id": "geo-anchor",
                "community_id": 2,
                "is_anchor": 1,
                "assignment_confidence": 1.0,
                "assignment_source": "algorithm_one",
                "latitude": 31.2,
                "longitude": 121.2,
                "customer_ids": "u2",
            },
        ]
    )
    community_archive = pd.DataFrame(
        [
            {
                "city_code": "shanghai",
                "community_id": 1,
                "merchant_count": 1,
                "anchor_count": 1,
                "anchor_merchants": "graph-anchor",
                "customer_ids": "u1",
                "centroid_latitude": 31.0,
                "centroid_longitude": 121.0,
                "sigma_meters": 100.0,
            },
            {
                "city_code": "shanghai",
                "community_id": 2,
                "merchant_count": 1,
                "anchor_count": 1,
                "anchor_merchants": "geo-anchor",
                "customer_ids": "u2",
                "centroid_latitude": 31.2,
                "centroid_longitude": 121.2,
                "sigma_meters": 100.0,
            },
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "city_code": "shanghai",
                "merchant_a": "x",
                "merchant_b": "graph-anchor",
                "weight": 5.0,
                "sppmi": 5.0,
            }
        ]
    )
    scores = calculate_scores(
        _candidate("x", 31.2, 121.2),
        edges,
        merchant_archive,
        community_archive,
        _config(),
    )

    decision = decide_candidate(_candidate("x", 31.2, 121.2), scores, _config())

    assert decision.decision == "manual_review"
    assert decision.reason == "图分最高商圈与地理分最高商圈不同"
