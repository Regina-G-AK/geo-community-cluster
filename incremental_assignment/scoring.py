from __future__ import annotations

import math

import pandas as pd

from incremental_assignment.archives import _parse_pipe_set
from incremental_assignment.models import AssignmentConfig, CandidateMerchant, MerchantScores


def _haversine_meters(
    left_latitude: float,
    left_longitude: float,
    right_latitude: float,
    right_longitude: float,
) -> float:
    radius = 6371000.0
    left_lat = math.radians(left_latitude)
    right_lat = math.radians(right_latitude)
    delta_lat = math.radians(right_latitude - left_latitude)
    delta_lon = math.radians(right_longitude - left_longitude)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(left_lat)
        * math.cos(right_lat)
        * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * radius * math.atan2(math.sqrt(value), math.sqrt(1.0 - value))


def _community_ids(merchant_archive: pd.DataFrame) -> list[int]:
    active = merchant_archive.loc[merchant_archive["community_id"].astype(int) >= 0]
    return sorted(active["community_id"].astype(int).unique().tolist())


def _edge_weight(row: dict[str, object]) -> float:
    sppmi = row["sppmi"]
    if not pd.isna(sppmi) and str(sppmi).strip():
        return float(sppmi)
    return float(row["weight"])


def calculate_graph_scores(
    candidate: CandidateMerchant,
    edges: pd.DataFrame,
    merchant_archive: pd.DataFrame,
    config: AssignmentConfig,
) -> dict[int, float]:
    labels = {
        str(row["merchant_id"]): int(row["community_id"])
        for row in merchant_archive.to_dict("records")
        if int(row["community_id"]) >= 0 and str(row["city_code"]) == candidate.city_code
    }
    anchors = {
        str(row["merchant_id"])
        for row in merchant_archive.to_dict("records")
        if int(row["is_anchor"]) == 1 and str(row["city_code"]) == candidate.city_code
    }
    relevant = edges.loc[
        (edges["city_code"].astype(str) == candidate.city_code)
        & (
            (edges["merchant_a"].astype(str) == candidate.merchant_id)
            | (edges["merchant_b"].astype(str) == candidate.merchant_id)
        )
    ]
    neighbors: list[tuple[str, float]] = []
    for row in relevant.to_dict("records"):
        left = str(row["merchant_a"])
        right = str(row["merchant_b"])
        neighbor = right if left == candidate.merchant_id else left
        if neighbor not in labels:
            continue
        weight = _edge_weight(row)
        if weight <= 0:
            continue
        vote_weight = weight * (config.anchor_vote_weight if neighbor in anchors else 1.0)
        neighbors.append((neighbor, vote_weight))
    neighbors.sort(key=lambda item: (-item[1], item[0]))
    selected = neighbors[: config.top_k_neighbors]
    scores = {community_id: 0.0 for community_id in _community_ids(merchant_archive)}
    denominator = sum(weight for _, weight in selected)
    if denominator <= 0:
        return scores
    for neighbor, weight in selected:
        scores[labels[neighbor]] += weight / denominator
    return scores


def _valid_coordinate(value: object) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip() != ""


def calculate_geo_scores(
    candidate: CandidateMerchant,
    merchant_archive: pd.DataFrame,
    community_archive: pd.DataFrame,
) -> tuple[dict[int, float], bool]:
    scores = {community_id: 0.0 for community_id in _community_ids(merchant_archive)}
    if candidate.latitude is None or candidate.longitude is None:
        return scores, False
    available = False
    anchor_rows = merchant_archive.loc[
        (merchant_archive["city_code"].astype(str) == candidate.city_code)
        & (merchant_archive["is_anchor"].astype(int) == 1)
        & (merchant_archive["community_id"].astype(int) >= 0)
    ]
    sigma_by_community = {
        int(row["community_id"]): float(row["sigma_meters"])
        for row in community_archive.to_dict("records")
        if str(row["city_code"]) == candidate.city_code and not pd.isna(row["sigma_meters"])
    }
    for community_id, group in anchor_rows.groupby("community_id", sort=True):
        distances = [
            _haversine_meters(
                candidate.latitude,
                candidate.longitude,
                float(row["latitude"]),
                float(row["longitude"]),
            )
            for row in group.to_dict("records")
            if _valid_coordinate(row["latitude"]) and _valid_coordinate(row["longitude"])
        ]
        if not distances:
            continue
        sigma = sigma_by_community.get(int(community_id))
        if sigma is None or sigma <= 0:
            continue
        nearest_distance = min(distances)
        scores[int(community_id)] = math.exp(-(nearest_distance**2) / (2.0 * sigma**2))
        available = True
    return scores, available


def calculate_customer_scores(
    candidate: CandidateMerchant,
    community_archive: pd.DataFrame,
    merchant_archive: pd.DataFrame,
) -> dict[int, float]:
    scores = {community_id: 0.0 for community_id in _community_ids(merchant_archive)}
    candidate_customers = set(candidate.customer_ids)
    for row in community_archive.to_dict("records"):
        if str(row["city_code"]) != candidate.city_code:
            continue
        community_id = int(row["community_id"])
        customers = _parse_pipe_set(row["customer_ids"])
        union = candidate_customers.union(customers)
        if not union:
            scores[community_id] = 0.0
        else:
            scores[community_id] = len(candidate_customers.intersection(customers)) / len(union)
    return scores


def _best_community(scores: dict[int, float]) -> tuple[int | None, float]:
    if not scores:
        return None, 0.0
    community_id, score = max(scores.items(), key=lambda item: (item[1], -item[0]))
    return community_id, score


def _fusion_weights(
    config: AssignmentConfig,
    geo_available: bool,
) -> tuple[float, float, float]:
    if geo_available:
        return config.graph_weight, config.geo_weight, config.customer_weight
    active_total = config.graph_weight + config.customer_weight
    return config.graph_weight / active_total, 0.0, config.customer_weight / active_total


def calculate_scores(
    candidate: CandidateMerchant,
    edges: pd.DataFrame,
    merchant_archive: pd.DataFrame,
    community_archive: pd.DataFrame,
    config: AssignmentConfig,
) -> MerchantScores:
    graph_scores = calculate_graph_scores(candidate, edges, merchant_archive, config)
    geo_scores, geo_available = calculate_geo_scores(
        candidate,
        merchant_archive,
        community_archive,
    )
    customer_scores = calculate_customer_scores(candidate, community_archive, merchant_archive)
    graph_weight, geo_weight, customer_weight = _fusion_weights(config, geo_available)
    community_ids = sorted(set(graph_scores).union(geo_scores).union(customer_scores))
    final_scores = {
        community_id: (
            graph_scores.get(community_id, 0.0) * graph_weight
            + geo_scores.get(community_id, 0.0) * geo_weight
            + customer_scores.get(community_id, 0.0) * customer_weight
        )
        for community_id in community_ids
    }
    graph_best, graph_best_score = _best_community(graph_scores)
    geo_best, geo_best_score = _best_community(geo_scores)
    return MerchantScores(
        graph_scores=graph_scores,
        geo_scores=geo_scores,
        customer_scores=customer_scores,
        final_scores=final_scores,
        geo_available=geo_available,
        graph_best_community_id=graph_best if graph_best_score > 0 else None,
        geo_best_community_id=geo_best if geo_best_score > 0 else None,
    )

