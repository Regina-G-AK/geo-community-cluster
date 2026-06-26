from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional, Tuple

import networkx as nx
import pandas as pd

from business_district.config import CooccurrenceConfig, GraphConfig
from business_district.transactions import CARD, MERCHANT, TIMESTAMP

MerchantPair = Tuple[str, str]
EdgeCandidate = Tuple[float, Optional[float], int]


@dataclass(frozen=True)
class PairStatistics:
    strengths: dict[MerchantPair, float]
    supports: dict[MerchantPair, int]
    merchant_visit_counts: dict[str, int]


def build_pair_statistics(
    visits: pd.DataFrame,
    config: CooccurrenceConfig,
) -> PairStatistics:
    window = pd.Timedelta(minutes=config.window_minutes)
    strength_by_pair: dict[MerchantPair, float] = defaultdict(float)
    support_by_pair: dict[MerchantPair, int] = defaultdict(int)
    merchant_visit_counts = Counter(visits[MERCHANT].astype(str))

    for _, group in visits.groupby(CARD, sort=False):
        ordered = group.sort_values(TIMESTAMP)
        rows = list(ordered[[MERCHANT, TIMESTAMP]].itertuples(index=False, name=None))
        user_pair_max: dict[MerchantPair, float] = {}

        for left_index, (left_merchant, left_timestamp) in enumerate(rows[:-1]):
            for right_merchant, right_timestamp in rows[left_index + 1 :]:
                delta = pd.Timestamp(right_timestamp) - pd.Timestamp(left_timestamp)
                if delta > window:
                    break
                if left_merchant == right_merchant:
                    continue
                pair = tuple(sorted((str(left_merchant), str(right_merchant))))
                minutes = delta.total_seconds() / 60.0
                weight = math.exp(-minutes / config.decay_tau_minutes)
                user_pair_max[pair] = max(user_pair_max.get(pair, 0.0), weight)

        for pair, weight in user_pair_max.items():
            strength_by_pair[pair] += weight
            support_by_pair[pair] += 1

    return PairStatistics(
        strengths=dict(strength_by_pair),
        supports=dict(support_by_pair),
        merchant_visit_counts=dict(merchant_visit_counts),
    )


def _calculate_sppmi_candidates(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
    graph_config: GraphConfig,
) -> dict[MerchantPair, tuple[float, float, int]]:
    eligible = {
        pair: strength
        for pair, strength in statistics.strengths.items()
        if statistics.supports[pair] >= cooccurrence_config.minimum_unique_users
    }
    if not eligible:
        return {}

    marginals: dict[str, float] = defaultdict(float)
    for (left, right), strength in eligible.items():
        marginals[left] += strength
        marginals[right] += strength

    total_strength = sum(eligible.values())
    smoothed_total = sum(
        strength ** graph_config.context_smoothing_alpha
        for strength in marginals.values()
    ) / 2.0
    candidates: dict[MerchantPair, tuple[float, float, int]] = {}

    for pair, strength in eligible.items():
        left, right = pair
        left_context_pmi = math.log(
            strength
            * smoothed_total
            / (
                marginals[left]
                * marginals[right] ** graph_config.context_smoothing_alpha
            )
        )
        right_context_pmi = math.log(
            strength
            * smoothed_total
            / (
                marginals[right]
                * marginals[left] ** graph_config.context_smoothing_alpha
            )
        )
        pmi = (left_context_pmi + right_context_pmi) / 2.0
        sppmi = max(pmi - math.log(graph_config.sppmi_shift), 0.0)

        expected = marginals[left] * marginals[right] / total_strength
        z_score = (
            (strength - expected) / math.sqrt(expected)
            if expected > 0
            else 0.0
        )
        if sppmi > 0 and z_score >= graph_config.minimum_z_score:
            candidates[pair] = (
                sppmi,
                z_score,
                statistics.supports[pair],
            )
    return candidates


def _calculate_transaction_count_candidates(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
) -> dict[MerchantPair, EdgeCandidate]:
    return {
        pair: (float(strength), None, int(statistics.supports[pair]))
        for pair, strength in statistics.strengths.items()
        if statistics.supports[pair] >= cooccurrence_config.minimum_unique_users
    }


def calculate_edge_candidates(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
    graph_config: GraphConfig,
) -> dict[MerchantPair, EdgeCandidate]:
    if graph_config.edge_weight_method == "sppmi":
        return _calculate_sppmi_candidates(
            statistics,
            cooccurrence_config,
            graph_config,
        )
    if graph_config.edge_weight_method == "transaction_count":
        return _calculate_transaction_count_candidates(
            statistics,
            cooccurrence_config,
        )
    raise ValueError(
        f"不支持的商户边权重计算方式: {graph_config.edge_weight_method}"
    )


def _normalize_community_weights(
    weights_by_merchant: dict[str, dict[int, float]],
) -> dict[str, dict[int, float]]:
    shares_by_merchant: dict[str, dict[int, float]] = {}
    for merchant_id, weights in weights_by_merchant.items():
        total_weight = sum(weights.values())
        if total_weight <= 0.0:
            continue
        shares_by_merchant[merchant_id] = {
            community_id: weight / total_weight
            for community_id, weight in weights.items()
            if weight > 0.0
        }
    return shares_by_merchant


def calculate_candidate_community_weight_shares(
    candidates: dict[MerchantPair, EdgeCandidate],
    partition: dict[str, int],
) -> dict[str, dict[int, float]]:
    weights_by_merchant: dict[str, dict[int, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for (left, right), (weight, _, _) in candidates.items():
        if left in partition and right in partition:
            weights_by_merchant[left][partition[right]] += weight
            weights_by_merchant[right][partition[left]] += weight
    return _normalize_community_weights(weights_by_merchant)


def build_sparse_graph(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
    graph_config: GraphConfig,
) -> nx.Graph:
    candidates = calculate_edge_candidates(
        statistics,
        cooccurrence_config,
        graph_config,
    )
    neighbors: dict[
        str,
        list[tuple[str, float, float | None, int]],
    ] = defaultdict(list)
    for (left, right), (weight, z_score, support) in candidates.items():
        neighbors[left].append((right, weight, z_score, support))
        neighbors[right].append((left, weight, z_score, support))

    top_neighbors: dict[
        str,
        dict[str, tuple[float, float | None, int]],
    ] = {}
    for merchant_id, merchant_neighbors in neighbors.items():
        ordered = sorted(
            merchant_neighbors,
            key=lambda item: (-item[1], -item[3], item[0]),
        )[: graph_config.top_k_neighbors]
        top_neighbors[merchant_id] = {
            neighbor: (weight, z_score, support)
            for neighbor, weight, z_score, support in ordered
        }

    graph = nx.Graph()
    graph.add_nodes_from(sorted(statistics.merchant_visit_counts))
    for merchant_id, merchant_neighbors in top_neighbors.items():
        for neighbor, (weight, z_score, support) in merchant_neighbors.items():
            reverse = top_neighbors.get(neighbor, {})
            if merchant_id not in reverse or graph.has_edge(merchant_id, neighbor):
                continue
            edge_attributes: dict[str, float | int] = {
                "weight": float(weight),
                "support": int(support),
            }
            if z_score is not None:
                edge_attributes["sppmi"] = float(weight)
                edge_attributes["z_score"] = float(z_score)
            graph.add_edge(merchant_id, neighbor, **edge_attributes)
    return graph
