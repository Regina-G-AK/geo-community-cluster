from __future__ import annotations

import math
import multiprocessing
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Counter as CounterType
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union

import networkx as nx
import pandas as pd

from business_district.config import CooccurrenceConfig, GraphConfig
from business_district.probes import print_probe
from business_district.transactions import CARD, MERCHANT, TIMESTAMP

MerchantPair = Tuple[str, str]
EdgeCandidate = Tuple[float, Optional[float], int]
CardVisitRows = Tuple[Tuple[str, pd.Timestamp], ...]
CardGroup = Tuple[str, CardVisitRows]
PAIR_GROUP_PROGRESS_VISIT_INTERVAL = 500000
PAIR_WORKER_PROGRESS_VISIT_INTERVAL = 100000
PAIR_WORKER_PROGRESS_COMPARISON_START = 1000000


@dataclass(frozen=True)
class PairStatistics:
    strengths: Dict[MerchantPair, float]
    supports: Dict[MerchantPair, int]
    merchant_visit_counts: Dict[str, int]


PairStatisticsTask = Tuple[
    int,
    Tuple[CardGroup, ...],
    CooccurrenceConfig,
]
PairStatisticsChunk = Tuple[int, PairStatistics]


def _build_pair_statistics_chunk(task: PairStatisticsTask) -> PairStatisticsChunk:
    chunk_index, groups, config = task
    started_at = time.monotonic()
    chunk_visit_count = sum(len(rows) for _, rows in groups)
    print_probe(
        "pair_statistics.worker_started",
        f"chunk_index={chunk_index}, card_count={len(groups)}, "
        f"visit_count={chunk_visit_count}",
    )
    window = pd.Timedelta(minutes=config.window_minutes)
    strength_by_pair: Dict[MerchantPair, float] = defaultdict(float)
    support_by_pair: Dict[MerchantPair, int] = defaultdict(int)
    merchant_visit_counts: CounterType[str] = Counter()
    processed_visit_count = 0
    candidate_comparison_count = 0
    user_pair_evidence_count = 0
    next_visit_probe_count = PAIR_WORKER_PROGRESS_VISIT_INTERVAL
    next_comparison_probe_count = PAIR_WORKER_PROGRESS_COMPARISON_START

    for card_index, (_, rows) in enumerate(groups):
        merchant_visit_counts.update(merchant_id for merchant_id, _ in rows)
        user_pair_max: Dict[MerchantPair, float] = {}
        for left_index, (left_merchant, left_timestamp) in enumerate(rows[:-1]):
            for right_merchant, right_timestamp in rows[left_index + 1 :]:
                candidate_comparison_count += 1
                delta = right_timestamp - left_timestamp
                if delta > window:
                    break
                if left_merchant == right_merchant:
                    continue
                pair = tuple(sorted((left_merchant, right_merchant)))
                minutes = delta.total_seconds() / 60.0
                weight = math.exp(-minutes / config.decay_tau_minutes)
                user_pair_max[pair] = max(user_pair_max.get(pair, 0.0), weight)
        user_pair_evidence_count += len(user_pair_max)
        for pair, weight in user_pair_max.items():
            strength_by_pair[pair] += weight
            support_by_pair[pair] += 1
        processed_visit_count += len(rows)
        visit_probe_reached = processed_visit_count >= next_visit_probe_count
        comparison_probe_reached = (
            candidate_comparison_count >= next_comparison_probe_count
        )
        if visit_probe_reached:
            while processed_visit_count >= next_visit_probe_count:
                next_visit_probe_count += PAIR_WORKER_PROGRESS_VISIT_INTERVAL
        if comparison_probe_reached:
            while candidate_comparison_count >= next_comparison_probe_count:
                next_comparison_probe_count *= 2
        if (
            visit_probe_reached
            or comparison_probe_reached
            or card_index + 1 == len(groups)
        ):
            print_probe(
                "pair_statistics.worker_progress",
                f"chunk_index={chunk_index}, "
                f"processed_card_count={card_index + 1}, "
                f"total_card_count={len(groups)}, "
                f"processed_visit_count={processed_visit_count}, "
                f"total_visit_count={chunk_visit_count}, "
                f"candidate_comparison_count={candidate_comparison_count}, "
                f"user_pair_evidence_count={user_pair_evidence_count}, "
                f"unique_pair_count={len(strength_by_pair)}, "
                f"elapsed_seconds={time.monotonic() - started_at:.2f}",
            )

    statistics = PairStatistics(
        dict(strength_by_pair),
        dict(support_by_pair),
        dict(merchant_visit_counts),
    )
    print_probe(
        "pair_statistics.worker_complete",
        f"chunk_index={chunk_index}, card_count={len(groups)}, "
        f"visit_count={chunk_visit_count}, "
        f"candidate_comparison_count={candidate_comparison_count}, "
        f"user_pair_evidence_count={user_pair_evidence_count}, "
        f"unique_pair_count={len(statistics.strengths)}, "
        f"elapsed_seconds={time.monotonic() - started_at:.2f}",
    )
    return chunk_index, statistics


def _partition_card_groups(
    visits: pd.DataFrame,
    process_count: int,
) -> Tuple[Tuple[CardGroup, ...], ...]:
    started_at = time.monotonic()
    print_probe(
        "pair_statistics.card_grouping_started",
        f"visit_count={len(visits)}, process_count={process_count}",
    )
    groups: List[CardGroup] = []
    grouped_visit_count = 0
    maximum_card_visit_count = 0
    next_visit_probe_count = PAIR_GROUP_PROGRESS_VISIT_INTERVAL
    for card_id, group in visits.groupby(CARD, sort=False):
        rows: CardVisitRows = tuple(
            (str(merchant_id), pd.Timestamp(timestamp))
            for merchant_id, timestamp in group.sort_values(TIMESTAMP)[
                [MERCHANT, TIMESTAMP]
            ].itertuples(index=False, name=None)
        )
        groups.append(
            (
                str(card_id),
                rows,
            )
        )
        grouped_visit_count += len(rows)
        maximum_card_visit_count = max(maximum_card_visit_count, len(rows))
        if grouped_visit_count >= next_visit_probe_count:
            print_probe(
                "pair_statistics.card_grouping_progress",
                f"card_count={len(groups)}, "
                f"grouped_visit_count={grouped_visit_count}, "
                f"total_visit_count={len(visits)}, "
                f"maximum_card_visit_count={maximum_card_visit_count}, "
                f"elapsed_seconds={time.monotonic() - started_at:.2f}",
            )
            while grouped_visit_count >= next_visit_probe_count:
                next_visit_probe_count += PAIR_GROUP_PROGRESS_VISIT_INTERVAL
    chunk_count = min(len(groups), process_count * 4)
    if chunk_count == 0:
        print_probe(
            "pair_statistics.card_grouping_complete",
            "card_count=0, grouped_visit_count=0, chunk_count=0",
        )
        return tuple()
    partitions = tuple(
        tuple(groups[index::chunk_count])
        for index in range(chunk_count)
    )
    print_probe(
        "pair_statistics.card_grouping_complete",
        f"card_count={len(groups)}, grouped_visit_count={grouped_visit_count}, "
        f"maximum_card_visit_count={maximum_card_visit_count}, "
        f"chunk_count={chunk_count}, "
        f"elapsed_seconds={time.monotonic() - started_at:.2f}",
    )
    return partitions


def _iter_pair_statistics_chunks(
    tasks: List[PairStatisticsTask],
    process_count: int,
) -> Iterator[PairStatisticsChunk]:
    if process_count == 1 or len(tasks) <= 1:
        for task in tasks:
            yield _build_pair_statistics_chunk(task)
        return

    print_probe(
        "pair_statistics.worker_pool_started",
        f"process_count={process_count}, chunk_count={len(tasks)}",
    )
    with multiprocessing.Pool(
        processes=process_count,
        maxtasksperchild=1,
    ) as pool:
        for chunk in pool.imap(_build_pair_statistics_chunk, tasks):
            yield chunk


def _accumulate_pair_statistics_chunks(
    chunks: Iterable[PairStatisticsChunk],
) -> Iterator[PairStatistics]:
    strengths: Dict[MerchantPair, float] = defaultdict(float)
    supports: Dict[MerchantPair, int] = defaultdict(int)
    visit_counts: CounterType[str] = Counter()
    has_chunk = False
    for chunk_index, chunk in chunks:
        has_chunk = True
        merge_started_at = time.monotonic()
        print_probe(
            "pair_statistics.chunk_merge_started",
            f"chunk_index={chunk_index}, "
            f"chunk_pair_count={len(chunk.strengths)}, "
            f"accumulated_pair_count={len(strengths)}",
        )
        for pair, strength in chunk.strengths.items():
            strengths[pair] += strength
        for pair, support in chunk.supports.items():
            supports[pair] += support
        visit_counts.update(chunk.merchant_visit_counts)
        print_probe(
            "pair_statistics.snapshot_copy_started",
            f"chunk_index={chunk_index}, "
            f"accumulated_pair_count={len(strengths)}",
        )
        snapshot = PairStatistics(
            dict(strengths),
            dict(supports),
            dict(visit_counts),
        )
        print_probe(
            "pair_statistics.chunk_merge_complete",
            f"chunk_index={chunk_index}, "
            f"accumulated_pair_count={len(snapshot.strengths)}, "
            f"elapsed_seconds={time.monotonic() - merge_started_at:.2f}",
        )
        yield snapshot
    if not has_chunk:
        yield PairStatistics({}, {}, {})


def iter_pair_statistics_updates(
    visits: pd.DataFrame,
    config: CooccurrenceConfig,
    process_count: int,
) -> Iterator[PairStatistics]:
    if process_count < 1:
        raise ValueError(f"进程数必须不小于 1: process_count={process_count}")
    partitions = _partition_card_groups(visits, process_count)
    tasks = [
        (chunk_index, partition, config)
        for chunk_index, partition in enumerate(partitions)
    ]
    chunks = _iter_pair_statistics_chunks(tasks, process_count)
    yield from _accumulate_pair_statistics_chunks(chunks)


def _merge_final_pair_statistics_chunks(
    chunks: Iterable[PairStatisticsChunk],
) -> PairStatistics:
    strengths: Dict[MerchantPair, float] = {}
    supports: Dict[MerchantPair, int] = {}
    visit_counts: Dict[str, int] = {}
    for chunk_index, chunk in chunks:
        merge_started_at = time.monotonic()
        print_probe(
            "pair_statistics.final_merge_started",
            f"chunk_index={chunk_index}, "
            f"chunk_pair_count={len(chunk.strengths)}, "
            f"accumulated_pair_count={len(strengths)}",
        )
        for pair, strength in chunk.strengths.items():
            strengths[pair] = strengths.get(pair, 0.0) + strength
        for pair, support in chunk.supports.items():
            supports[pair] = supports.get(pair, 0) + support
        for merchant_id, visit_count in chunk.merchant_visit_counts.items():
            visit_counts[merchant_id] = (
                visit_counts.get(merchant_id, 0) + visit_count
            )
        print_probe(
            "pair_statistics.final_merge_complete",
            f"chunk_index={chunk_index}, "
            f"accumulated_pair_count={len(strengths)}, "
            f"elapsed_seconds={time.monotonic() - merge_started_at:.2f}",
        )
        del chunk
    return PairStatistics(
        strengths=strengths,
        supports=supports,
        merchant_visit_counts=visit_counts,
    )


def build_pair_statistics(
    visits: pd.DataFrame,
    config: CooccurrenceConfig,
    process_count: int,
) -> PairStatistics:
    if process_count < 1:
        raise ValueError(f"进程数必须不小于 1: process_count={process_count}")
    partitions = _partition_card_groups(visits, process_count)
    tasks = [
        (chunk_index, partition, config)
        for chunk_index, partition in enumerate(partitions)
    ]
    chunks = _iter_pair_statistics_chunks(tasks, process_count)
    return _merge_final_pair_statistics_chunks(chunks)


def _calculate_sppmi_candidates(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
    graph_config: GraphConfig,
) -> Dict[MerchantPair, Tuple[float, float, int]]:
    eligible = {
        pair: strength
        for pair, strength in statistics.strengths.items()
        if statistics.supports[pair] >= cooccurrence_config.minimum_unique_users
    }
    if not eligible:
        return {}

    marginals: Dict[str, float] = defaultdict(float)
    for (left, right), strength in eligible.items():
        marginals[left] += strength
        marginals[right] += strength

    total_strength = sum(eligible.values())
    smoothed_total = sum(
        strength ** graph_config.context_smoothing_alpha
        for strength in marginals.values()
    ) / 2.0
    candidates: Dict[MerchantPair, Tuple[float, float, int]] = {}

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
) -> Dict[MerchantPair, EdgeCandidate]:
    return {
        pair: (float(strength), None, int(statistics.supports[pair]))
        for pair, strength in statistics.strengths.items()
        if statistics.supports[pair] >= cooccurrence_config.minimum_unique_users
    }


def calculate_edge_candidates(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
    graph_config: GraphConfig,
) -> Dict[MerchantPair, EdgeCandidate]:
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
    weights_by_merchant: Dict[str, Dict[int, float]],
) -> Dict[str, Dict[int, float]]:
    shares_by_merchant: Dict[str, Dict[int, float]] = {}
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
    candidates: Dict[MerchantPair, EdgeCandidate],
    partition: Dict[str, int],
) -> Dict[str, Dict[int, float]]:
    weights_by_merchant: Dict[str, Dict[int, float]] = defaultdict(
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
    neighbors: Dict[
        str,
        List[Tuple[str, float, Optional[float], int]],
    ] = defaultdict(list)
    for (left, right), (weight, z_score, support) in candidates.items():
        neighbors[left].append((right, weight, z_score, support))
        neighbors[right].append((left, weight, z_score, support))

    top_neighbors: Dict[
        str,
        Dict[str, Tuple[float, Optional[float], int]],
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
            edge_attributes: Dict[str, Union[float, int]] = {
                "weight": float(weight),
                "support": int(support),
            }
            if z_score is not None:
                edge_attributes["sppmi"] = float(weight)
                edge_attributes["z_score"] = float(z_score)
            graph.add_edge(merchant_id, neighbor, **edge_attributes)
    return graph
