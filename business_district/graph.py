from __future__ import annotations

import math
import multiprocessing
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


PairStatisticsTask = tuple[
    tuple[tuple[str, tuple[tuple[str, pd.Timestamp], ...]], ...],
    CooccurrenceConfig,
]


def _build_pair_statistics_chunk(task: PairStatisticsTask) -> PairStatistics:
    groups, config = task
    window = pd.Timedelta(minutes=config.window_minutes)
    strength_by_pair: dict[MerchantPair, float] = defaultdict(float)
    support_by_pair: dict[MerchantPair, int] = defaultdict(int)
    merchant_visit_counts: Counter[str] = Counter()

    for _, rows in groups:
        merchant_visit_counts.update(merchant_id for merchant_id, _ in rows)
        user_pair_max: dict[MerchantPair, float] = {}
        for left_index, (left_merchant, left_timestamp) in enumerate(rows[:-1]):
            for right_merchant, right_timestamp in rows[left_index + 1 :]:
                delta = right_timestamp - left_timestamp
                if delta > window:
                    break
                if left_merchant == right_merchant:
                    continue
                pair = tuple(sorted((left_merchant, right_merchant)))
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


def _partition_card_groups(
    visits: pd.DataFrame,
    process_count: int,
) -> tuple[tuple[tuple[str, tuple[tuple[str, pd.Timestamp], ...]], ...], ...]:
    groups = tuple(
        (
            str(card_id),
            tuple(
                (str(merchant_id), pd.Timestamp(timestamp))
                for merchant_id, timestamp in group.sort_values(TIMESTAMP)[
                    [MERCHANT, TIMESTAMP]
                ].itertuples(index=False, name=None)
            ),
        )
        for card_id, group in visits.groupby(CARD, sort=False)
    )
    chunk_count = min(len(groups), process_count * 4)
    if chunk_count == 0:
        return tuple()
    return tuple(groups[index::chunk_count] for index in range(chunk_count))


def _merge_pair_statistics_chunks(
    chunks: list[PairStatistics],
) -> PairStatistics:
    strengths: dict[MerchantPair, float] = defaultdict(float)
    supports: dict[MerchantPair, int] = defaultdict(int)
    visit_counts: Counter[str] = Counter()
    for chunk in chunks:
        for pair, strength in chunk.strengths.items():
            strengths[pair] += strength
        for pair, support in chunk.supports.items():
            supports[pair] += support
        visit_counts.update(chunk.merchant_visit_counts)
    return PairStatistics(dict(strengths), dict(supports), dict(visit_counts))


def build_pair_statistics(
    visits: pd.DataFrame,
    config: CooccurrenceConfig,
    process_count: int,
) -> PairStatistics:
    if process_count < 1:
        raise ValueError(f"进程数必须不小于 1: process_count={process_count}")
    partitions = _partition_card_groups(visits, process_count)
    tasks = [(partition, config) for partition in partitions]
    if process_count == 1 or len(tasks) <= 1:
        chunks = [_build_pair_statistics_chunk(task) for task in tasks]
    else:
        with multiprocessing.Pool(processes=process_count) as pool:
            chunks = pool.map(_build_pair_statistics_chunk, tasks)
    return _merge_pair_statistics_chunks(chunks)


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
        elif left in partition and right not in partition:
            weights_by_merchant[right][partition[left]] += weight
        elif right in partition and left not in partition:
            weights_by_merchant[left][partition[right]] += weight
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
