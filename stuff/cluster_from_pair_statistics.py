from __future__ import annotations

import json
import math
import pickle
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import igraph as ig
import leidenalg as la
import networkx as nx
import pandas as pd


MerchantPair = Tuple[str, str]
EdgeCandidate = Tuple[float, Optional[float], int]
PreparedEdgeCandidate = Tuple[float, float, Optional[float], int, float]
MERCHANT_ID = "merchant_id"


class PairStatisticsDataError(ValueError):
    pass


class AlgorithmError(RuntimeError):
    pass


@dataclass(frozen=True)
class CityConfig:
    code: str
    name: str


@dataclass(frozen=True)
class InputConfig:
    transactions_path: Path
    timestamp_formats: tuple[str, ...]


@dataclass(frozen=True)
class VisitConfig:
    merge_window_minutes: int
    maximum_daily_merchants_per_card: int


@dataclass(frozen=True)
class CooccurrenceConfig:
    window_minutes: int
    decay_tau_minutes: float
    minimum_unique_users: int


@dataclass(frozen=True)
class GraphConfig:
    edge_weight_method: str
    context_smoothing_alpha: float
    sppmi_shift: float
    top_k_neighbors: int
    minimum_z_score: float
    degree_penalty_gamma: float
    jaccard_threshold: float


@dataclass(frozen=True)
class CommunityConfig:
    algorithm: str
    resolution: float
    random_seed: int
    maximum_cleaning_rounds: int
    minimum_hub_degree: int
    participation_threshold: float


@dataclass(frozen=True)
class AnchorConfig:
    minimum_count: int
    maximum_count: int
    merchants_per_anchor: int
    minimum_community_size: int
    maximum_participation: float
    chain_visit_count_quantile: float
    chain_minimum_visit_count: int


@dataclass(frozen=True)
class OutputConfig:
    directory: Path


@dataclass(frozen=True)
class ExperimentConfig:
    path: Path


@dataclass(frozen=True)
class AppConfig:
    city: CityConfig
    input: InputConfig
    visits: VisitConfig
    cooccurrence: CooccurrenceConfig
    graph: GraphConfig
    community: CommunityConfig
    anchors: AnchorConfig
    output: OutputConfig
    experiments: ExperimentConfig


@dataclass(frozen=True)
class PairStatistics:
    strengths: dict[MerchantPair, float]
    supports: dict[MerchantPair, int]
    merchant_visit_counts: dict[str, int]


@dataclass(frozen=True)
class CleaningResult:
    graph: nx.Graph
    partition: dict[str, int]
    statuses: dict[str, str]
    cleaning_rounds: int


@dataclass(frozen=True)
class ExperimentContext:
    source: str
    source_detail: str
    input_rows: int | None
    visit_rows: int | None
    duration_seconds: float
    started_at: datetime
    output_directory: Path


@dataclass(frozen=True)
class ClusterSummary:
    merchant_count: int
    community_count: int
    edge_count: int
    output_directory: str


def load_pair_statistics(path: Path) -> PairStatistics:
    if not path.is_file():
        raise PairStatisticsDataError(f"商户对中间文件不存在: path={path}")

    try:
        with path.open("rb") as file:
            loaded = pickle.load(file)
    except (OSError, pickle.PickleError, AttributeError, ImportError, EOFError) as error:
        raise PairStatisticsDataError(
            f"商户对中间文件格式错误: path={path}, reason={error}"
        ) from error

    loaded_strengths = getattr(loaded, "strengths", None)
    loaded_supports = getattr(loaded, "supports", None)
    loaded_visit_counts = getattr(loaded, "merchant_visit_counts", None)
    if not isinstance(loaded_strengths, dict):
        raise PairStatisticsDataError(
            f"商户对中间文件格式错误: path={path}, missing=strengths"
        )
    if not isinstance(loaded_supports, dict):
        raise PairStatisticsDataError(
            f"商户对中间文件格式错误: path={path}, missing=supports"
        )
    if not isinstance(loaded_visit_counts, dict):
        raise PairStatisticsDataError(
            f"商户对中间文件格式错误: path={path}, missing=merchant_visit_counts"
        )

    loaded_pair_strengths: dict[MerchantPair, float] = {}
    loaded_pair_supports: dict[MerchantPair, int] = {}
    for pair_key, strength in loaded_strengths.items():
        if not isinstance(pair_key, tuple) or len(pair_key) != 2:
            raise PairStatisticsDataError(
                f"商户对中间文件包含无效商户对: path={path}, pair={pair_key!r}"
            )
        merchant_a, merchant_b = pair_key
        left = str(merchant_a)
        right = str(merchant_b)
        pair = (left, right)
        if pair_key in loaded_supports:
            support = loaded_supports[pair_key]
        elif pair in loaded_supports:
            support = loaded_supports[pair]
        else:
            raise PairStatisticsDataError(
                f"商户对中间文件缺少 support: path={path}, merchant_a={left!r}, merchant_b={right!r}"
            )
        pair_strength = float(strength)
        pair_support = int(support)
        _validate_pair_row(left, right, pair_strength, pair_support, path)
        loaded_pair_strengths[pair] = pair_strength
        loaded_pair_supports[pair] = pair_support

    loaded_merchant_visit_counts: dict[str, int] = {}
    for merchant_id, visit_count in loaded_visit_counts.items():
        merchant_key = str(merchant_id)
        merchant_count = int(visit_count)
        _validate_visit_row(merchant_key, merchant_count, path)
        loaded_merchant_visit_counts[merchant_key] = merchant_count

    return PairStatistics(
        strengths=loaded_pair_strengths,
        supports=loaded_pair_supports,
        merchant_visit_counts=loaded_merchant_visit_counts,
    )

def _calculate_sppmi_candidates(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
    graph_config: GraphConfig,
) -> dict[MerchantPair, EdgeCandidate]:
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
    candidates: dict[MerchantPair, EdgeCandidate] = {}

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
    raise ValueError(f"不支持的商户边权重计算方式: {graph_config.edge_weight_method}")


def _candidate_neighbor_sets(
    candidates: dict[MerchantPair, EdgeCandidate],
) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for left, right in candidates:
        neighbors[left].add(right)
        neighbors[right].add(left)
    return neighbors


def _edge_jaccard(
    left: str,
    right: str,
    neighbor_sets: dict[str, set[str]],
) -> float:
    left_neighbors = neighbor_sets[left]
    right_neighbors = neighbor_sets[right]
    union = left_neighbors | right_neighbors
    if not union:
        return 0.0
    return len(left_neighbors & right_neighbors) / len(union)


def _degree_penalized_weight(
    left: str,
    right: str,
    weight: float,
    neighbor_sets: dict[str, set[str]],
    gamma: float,
) -> float:
    if gamma == 0.0:
        return weight
    left_degree = len(neighbor_sets[left])
    right_degree = len(neighbor_sets[right])
    if left_degree < 1 or right_degree < 1:
        raise AlgorithmError(
            f"候选边端点缺少邻居度数: merchant_a={left!r}, merchant_b={right!r}"
        )
    return weight / ((left_degree * right_degree) ** gamma)


def prepare_edge_candidates(
    candidates: dict[MerchantPair, EdgeCandidate],
    graph_config: GraphConfig,
) -> dict[MerchantPair, PreparedEdgeCandidate]:
    neighbor_sets = _candidate_neighbor_sets(candidates)
    prepared: dict[MerchantPair, PreparedEdgeCandidate] = {}
    for pair, (weight, z_score, support) in candidates.items():
        left, right = pair
        jaccard = _edge_jaccard(left, right, neighbor_sets)
        if jaccard < graph_config.jaccard_threshold:
            continue
        graph_weight = (
            _degree_penalized_weight(
                left,
                right,
                weight,
                neighbor_sets,
                graph_config.degree_penalty_gamma,
            )
            if graph_config.edge_weight_method == "sppmi"
            else weight
        )
        prepared[pair] = (
            graph_weight,
            weight,
            z_score,
            support,
            jaccard,
        )
    return prepared


def build_sparse_graph(
    statistics: PairStatistics,
    cooccurrence_config: CooccurrenceConfig,
    graph_config: GraphConfig,
) -> nx.Graph:
    prepared_candidates = prepare_edge_candidates(
        calculate_edge_candidates(
            statistics,
            cooccurrence_config,
            graph_config,
        ),
        graph_config,
    )
    neighbors: dict[
        str,
        list[tuple[str, float, float, float | None, int, float]],
    ] = defaultdict(list)
    for (left, right), (
        graph_weight,
        source_weight,
        z_score,
        support,
        jaccard,
    ) in prepared_candidates.items():
        neighbors[left].append(
            (right, graph_weight, source_weight, z_score, support, jaccard)
        )
        neighbors[right].append(
            (left, graph_weight, source_weight, z_score, support, jaccard)
        )

    top_neighbors: dict[str, dict[str, PreparedEdgeCandidate]] = {}
    for merchant_id, merchant_neighbors in neighbors.items():
        ordered = sorted(
            merchant_neighbors,
            key=lambda item: (-item[1], -item[4], item[0]),
        )[: graph_config.top_k_neighbors]
        top_neighbors[merchant_id] = {
            neighbor: (weight, source_weight, z_score, support, jaccard)
            for neighbor, weight, source_weight, z_score, support, jaccard in ordered
        }

    graph = nx.Graph()
    graph.add_nodes_from(sorted(statistics.merchant_visit_counts))
    for merchant_id, merchant_neighbors in top_neighbors.items():
        for neighbor, (
            weight,
            source_weight,
            z_score,
            support,
            jaccard,
        ) in merchant_neighbors.items():
            reverse = top_neighbors.get(neighbor, {})
            if merchant_id not in reverse or graph.has_edge(merchant_id, neighbor):
                continue
            edge_attributes: dict[str, float | int] = {
                "weight": float(weight),
                "support": int(support),
                "jaccard": float(jaccard),
            }
            if z_score is not None:
                edge_attributes["sppmi"] = float(source_weight)
                edge_attributes["z_score"] = float(z_score)
            graph.add_edge(merchant_id, neighbor, **edge_attributes)
    return graph


def detect_communities(
    graph: nx.Graph,
    resolution: float,
    random_seed: int,
) -> dict[str, int]:
    connected_nodes = sorted(node for node in graph if graph.degree(node) > 0)
    isolated_nodes = sorted(node for node in graph if graph.degree(node) == 0)
    connected_graph = graph.subgraph(connected_nodes).copy()

    communities: list[set[str]] = []
    if connected_graph.number_of_nodes() > 0:
        node_ids = [str(node) for node in connected_nodes]
        node_indices = {
            node_id: node_index
            for node_index, node_id in enumerate(node_ids)
        }
        networkx_edges = list(connected_graph.edges(data=True))
        igraph_graph = ig.Graph(
            n=len(node_ids),
            edges=[
                (node_indices[str(left)], node_indices[str(right)])
                for left, right, _ in networkx_edges
            ],
            directed=False,
        )
        igraph_graph.vs["name"] = node_ids
        weights = [float(edge_data["weight"]) for _, _, edge_data in networkx_edges]
        partition = la.find_partition(
            igraph_graph,
            la.RBConfigurationVertexPartition,
            weights=weights,
            resolution_parameter=resolution,
            seed=random_seed,
        )
        communities = [
            {node_ids[node_index] for node_index in community}
            for community in partition
        ]
    communities.extend({node} for node in isolated_nodes)
    ordered = sorted(
        communities,
        key=lambda members: (-len(members), min(str(member) for member in members)),
    )
    return {
        str(node): community_id
        for community_id, members in enumerate(ordered)
        for node in members
    }


def calculate_participation(
    graph: nx.Graph,
    partition: dict[str, int],
) -> dict[str, float]:
    community_shares = calculate_community_weight_shares(graph, partition)
    return {
        merchant_id: 1.0 - sum(share**2 for share in shares.values())
        for merchant_id, shares in community_shares.items()
    }


def calculate_community_weight_shares(
    graph: nx.Graph,
    partition: dict[str, int],
) -> dict[str, dict[int, float]]:
    shares_by_merchant: dict[str, dict[int, float]] = {}
    for node in graph:
        weight_by_community: dict[int, float] = defaultdict(float)
        total_weight = 0.0
        for neighbor, edge_data in graph[node].items():
            weight = float(edge_data["weight"])
            total_weight += weight
            weight_by_community[partition[neighbor]] += weight
        if total_weight == 0:
            shares_by_merchant[str(node)] = {partition[str(node)]: 1.0}
            continue
        shares_by_merchant[str(node)] = {
            community_id: weight / total_weight
            for community_id, weight in weight_by_community.items()
        }
    return shares_by_merchant


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
    candidates: dict[MerchantPair, PreparedEdgeCandidate],
    partition: dict[str, int],
) -> dict[str, dict[int, float]]:
    weights_by_merchant: dict[str, dict[int, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for (left, right), (weight, _, _, _, _) in candidates.items():
        if left in partition and right in partition:
            weights_by_merchant[left][partition[right]] += weight
            weights_by_merchant[right][partition[left]] += weight
        elif left in partition and right not in partition:
            weights_by_merchant[right][partition[left]] += weight
        elif right in partition and left not in partition:
            weights_by_merchant[left][partition[right]] += weight
    return _normalize_community_weights(weights_by_merchant)


def clean_graph(
    graph: nx.Graph,
    config: CommunityConfig,
) -> CleaningResult:
    working_graph = graph.copy()
    statuses = {
        str(node): "suspect_isolated" if graph.degree(node) == 0 else "active"
        for node in graph
    }
    completed_rounds = 0

    for round_index in range(config.maximum_cleaning_rounds):
        partition = detect_communities(
            working_graph,
            config.resolution,
            config.random_seed,
        )
        participation = calculate_participation(working_graph, partition)
        hubs = [
            str(node)
            for node in working_graph
            if working_graph.degree(node) >= config.minimum_hub_degree
            and participation[str(node)] >= config.participation_threshold
        ]
        if not hubs:
            break
        working_graph.remove_nodes_from(hubs)
        for node in hubs:
            statuses[node] = "suspect_online"
        completed_rounds = round_index + 1

    final_partition = detect_communities(
        working_graph,
        config.resolution,
        config.random_seed,
    )
    return CleaningResult(
        graph=working_graph,
        partition=final_partition,
        statuses=statuses,
        cleaning_rounds=completed_rounds,
    )


def _anchor_count(community_size: int, config: AnchorConfig) -> int:
    scaled = math.ceil(community_size / config.merchants_per_anchor)
    return min(
        community_size,
        max(config.minimum_count, min(scaled, config.maximum_count)),
    )


def _membership_community_ids(
    merchant_id: str,
    primary_community_id: int,
    is_chain_like: bool,
    community_shares: dict[str, dict[int, float]],
    candidate_community_shares: dict[str, dict[int, float]],
) -> tuple[int, ...]:
    if not is_chain_like:
        return (primary_community_id,)
    shares = candidate_community_shares.get(
        merchant_id,
        community_shares.get(merchant_id, {}),
    )
    linked_community_ids = {
        community_id
        for community_id, share in shares.items()
        if share > 0.0
    }
    linked_community_ids.add(primary_community_id)
    return tuple(sorted(linked_community_ids))


def _expand_multi_community_memberships(
    merchants: pd.DataFrame,
    community_shares: dict[str, dict[int, float]],
    candidate_community_shares: dict[str, dict[int, float]],
) -> pd.DataFrame:
    if merchants.empty:
        return merchants.assign(
            primary_community_id=pd.Series(dtype="int64"),
            community_share=pd.Series(dtype="float64"),
            is_primary_community=pd.Series(dtype="int64"),
            is_multi_community_member=pd.Series(dtype="int64"),
        )
    rows: list[dict[str, str | int | float]] = []
    for row in merchants.to_dict("records"):
        merchant_id = str(row["merchant_id"])
        primary_community_id = int(row["community_id"])
        is_chain_like = bool(row["is_chain_like"])
        community_ids = _membership_community_ids(
            merchant_id,
            primary_community_id,
            is_chain_like,
            community_shares,
            candidate_community_shares,
        )
        is_multi_community_member = int(len(community_ids) > 1)
        for community_id in community_ids:
            membership = dict(row)
            shares = (
                candidate_community_shares.get(merchant_id, {})
                if is_chain_like
                else community_shares.get(merchant_id, {})
            )
            membership["primary_community_id"] = primary_community_id
            membership["community_id"] = int(community_id)
            membership["community_share"] = float(shares.get(community_id, 0.0))
            membership["is_primary_community"] = int(community_id == primary_community_id)
            membership["is_multi_community_member"] = is_multi_community_member
            if community_id != primary_community_id:
                membership["is_anchor_candidate"] = 0
                membership["anchor_score"] = 0.0
            rows.append(membership)
    return pd.DataFrame(rows)


def _visit_count_quantile_threshold(
    visit_counts: list[int],
    quantile: float,
) -> int:
    if not visit_counts:
        return 0
    ordered = sorted(visit_counts)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return int(ordered[index])


def calculate_chain_visit_count_threshold(
    visit_counts: list[int],
    anchor_config: AnchorConfig,
) -> int:
    quantile_threshold = _visit_count_quantile_threshold(
        visit_counts,
        anchor_config.chain_visit_count_quantile,
    )
    return max(anchor_config.chain_minimum_visit_count, quantile_threshold)


def identify_chain_like_merchants(
    merchant_visit_counts: dict[str, int],
    chain_visit_count_threshold: int,
) -> set[str]:
    return {
        merchant_id
        for merchant_id, visit_count in merchant_visit_counts.items()
        if int(visit_count) >= chain_visit_count_threshold
    }


def _connected_community_count(
    merchant_id: str,
    community_shares: dict[str, dict[int, float]],
) -> int:
    return sum(
        1
        for share in community_shares.get(merchant_id, {}).values()
        if share > 0.0
    )


def _chain_reason(
    is_chain_like: bool,
) -> str:
    if is_chain_like:
        return "visit_count"
    return ""


def _add_chain_like_flags(
    result: pd.DataFrame,
    community_shares: dict[str, dict[int, float]],
    chain_like_merchant_ids: set[str],
    chain_visit_count_threshold: int,
) -> pd.DataFrame:
    if result.empty:
        return result.assign(
            connected_community_count=pd.Series(dtype="int64"),
            chain_visit_count_threshold=pd.Series(dtype="int64"),
            is_chain_like=pd.Series(dtype="int64"),
            chain_reason=pd.Series(dtype="object"),
        )
    enriched = result.copy()
    connected_counts = [
        _connected_community_count(str(merchant_id), community_shares)
        for merchant_id in enriched["merchant_id"].tolist()
    ]
    enriched["connected_community_count"] = connected_counts
    enriched["chain_visit_count_threshold"] = chain_visit_count_threshold
    is_chain_like = [
        str(merchant_id) in chain_like_merchant_ids
        for merchant_id in enriched["merchant_id"].tolist()
    ]
    enriched["is_chain_like"] = pd.Series(is_chain_like, index=enriched.index).astype(
        int
    )
    enriched["chain_reason"] = [
        _chain_reason(chain_flag)
        for chain_flag in is_chain_like
    ]
    return enriched


def _primary_community_id(shares: dict[int, float]) -> int:
    if not shares:
        return -1
    return sorted(shares.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _chain_participation(shares: dict[int, float]) -> float:
    if not shares:
        return 0.0
    return 1.0 - sum(share**2 for share in shares.values())


def _build_chain_membership_rows(
    chain_like_merchant_ids: set[str],
    statistics: PairStatistics,
    candidate_community_shares: dict[str, dict[int, float]],
    city_code: str,
    chain_visit_count_threshold: int,
) -> list[dict[str, str | int | float]]:
    rows: list[dict[str, str | int | float]] = []
    for merchant_id in sorted(chain_like_merchant_ids):
        shares = candidate_community_shares.get(merchant_id, {})
        primary_community_id = _primary_community_id(shares)
        community_ids = sorted(shares) if shares else [-1]
        is_multi_community_member = int(len(community_ids) > 1)
        connected_community_count = len(shares)
        merchant_status = (
            "active"
            if primary_community_id >= 0
            else "suspect_isolated"
        )
        for community_id in community_ids:
            rows.append(
                {
                    "city_code": city_code,
                    "merchant_id": merchant_id,
                    "primary_community_id": primary_community_id,
                    "community_id": int(community_id),
                    "merchant_status": merchant_status,
                    "is_anchor_candidate": 0,
                    "anchor_score": 0.0,
                    "pagerank": 0.0,
                    "participation": _chain_participation(shares),
                    "weighted_degree": 0.0,
                    "community_share": float(shares.get(community_id, 0.0)),
                    "is_primary_community": int(community_id == primary_community_id),
                    "is_multi_community_member": is_multi_community_member,
                    "connected_community_count": connected_community_count,
                    "chain_visit_count_threshold": chain_visit_count_threshold,
                    "is_chain_like": 1,
                    "chain_reason": "visit_count",
                    "visit_count": int(
                        statistics.merchant_visit_counts.get(merchant_id, 0)
                    ),
                }
            )
    return rows


def build_merchant_results(
    cleaning: CleaningResult,
    statistics: PairStatistics,
    anchor_config: AnchorConfig,
    candidate_community_shares: dict[str, dict[int, float]],
    chain_like_merchant_ids: set[str],
    chain_visit_count_threshold: int,
    city_code: str,
) -> pd.DataFrame:
    participation = calculate_participation(cleaning.graph, cleaning.partition)
    community_shares = calculate_community_weight_shares(
        cleaning.graph,
        cleaning.partition,
    )
    communities: dict[int, list[str]] = {}
    for merchant_id, community_id in cleaning.partition.items():
        if merchant_id in chain_like_merchant_ids:
            continue
        communities.setdefault(community_id, []).append(merchant_id)

    centrality_by_merchant: dict[str, float] = {}
    for community_nodes in communities.values():
        subgraph = cleaning.graph.subgraph(community_nodes)
        if subgraph.number_of_edges() > 0:
            centrality_by_merchant.update(nx.pagerank(subgraph, weight="weight"))
        else:
            equal_score = 1.0 / len(community_nodes)
            centrality_by_merchant.update(
                {node: equal_score for node in community_nodes}
            )

    rows: list[dict[str, str | int | float]] = []
    for merchant_id, community_id in cleaning.partition.items():
        if merchant_id in chain_like_merchant_ids:
            continue
        centrality = centrality_by_merchant[merchant_id]
        anchor_score = centrality * (1.0 - participation.get(merchant_id, 0.0))
        rows.append(
            {
                "city_code": city_code,
                "merchant_id": merchant_id,
                "community_id": int(community_id),
                "merchant_status": cleaning.statuses[merchant_id],
                "is_anchor_candidate": 0,
                "anchor_score": float(anchor_score),
                "pagerank": float(centrality),
                "participation": float(participation.get(merchant_id, 0.0)),
                "weighted_degree": float(
                    cleaning.graph.degree(merchant_id, weight="weight")
                ),
                "visit_count": int(
                    statistics.merchant_visit_counts.get(merchant_id, 0)
                ),
            }
        )

    result = pd.DataFrame(rows)
    result = _add_chain_like_flags(
        result,
        community_shares,
        chain_like_merchant_ids,
        chain_visit_count_threshold,
    )
    if not result.empty:
        anchor_indices: list[int] = []
        for _, group in result.groupby("community_id", sort=True):
            if len(group) < anchor_config.minimum_community_size:
                continue
            count = _anchor_count(len(group), anchor_config)
            eligible = group.loc[
                (group["is_chain_like"] == 0)
                & (
                    group["participation"].astype(float)
                    <= anchor_config.maximum_participation
                )
            ]
            ordered = eligible.sort_values(
                ["anchor_score", "weighted_degree", "visit_count", "merchant_id"],
                ascending=[False, False, False, True],
            )
            anchor_indices.extend(ordered.head(count).index.tolist())
        result.loc[anchor_indices, "is_anchor_candidate"] = 1

    result = _expand_multi_community_memberships(
        result,
        community_shares,
        candidate_community_shares,
    )

    chain_rows = _build_chain_membership_rows(
        chain_like_merchant_ids,
        statistics,
        candidate_community_shares,
        city_code,
        chain_visit_count_threshold,
    )
    if chain_rows:
        result = pd.concat([result, pd.DataFrame(chain_rows)], ignore_index=True)

    removed_rows = [
        {
            "city_code": city_code,
            "merchant_id": merchant_id,
            "primary_community_id": -1,
            "community_id": -1,
            "merchant_status": status,
            "is_anchor_candidate": 0,
            "anchor_score": 0.0,
            "pagerank": 0.0,
            "participation": 0.0,
            "weighted_degree": 0.0,
            "community_share": 0.0,
            "is_primary_community": 0,
            "is_multi_community_member": 0,
            "connected_community_count": 0,
            "chain_visit_count_threshold": chain_visit_count_threshold,
            "is_chain_like": 0,
            "chain_reason": "",
            "visit_count": int(statistics.merchant_visit_counts.get(merchant_id, 0)),
        }
        for merchant_id, status in cleaning.statuses.items()
        if status == "suspect_online" and merchant_id not in chain_like_merchant_ids
    ]
    if removed_rows:
        result = pd.concat([result, pd.DataFrame(removed_rows)], ignore_index=True)

    return result.sort_values(
        ["community_id", "merchant_status", "anchor_score", "merchant_id"],
        ascending=[True, True, False, True],
    ).reset_index(drop=True)


def filter_merchants_by_community_size(
    merchants: pd.DataFrame,
    minimum_output_community_size: int,
) -> pd.DataFrame:
    active = merchants.loc[merchants["community_id"] >= 0]
    community_sizes = active.groupby("community_id").size()
    kept_community_ids = set(
        community_sizes.loc[
            community_sizes >= minimum_output_community_size
        ].index
    )
    filtered = merchants.loc[merchants["community_id"].isin(kept_community_ids)]
    return filtered.copy().reset_index(drop=True)


def build_basic_community_results(
    merchants: pd.DataFrame,
    city_code: str,
) -> pd.DataFrame:
    active = merchants.loc[merchants["community_id"] >= 0]
    columns = [
        "city_code",
        "community_id",
        "merchant_count",
        "anchor_count",
        "anchor_merchants",
    ]
    rows: list[dict[str, str | int]] = []
    for community_id, group in active.groupby("community_id", sort=True):
        anchors = group.loc[group["is_anchor_candidate"] == 1, "merchant_id"].astype(str)
        rows.append(
            {
                "city_code": city_code,
                "community_id": int(community_id),
                "merchant_count": int(len(group)),
                "anchor_count": int(group["is_anchor_candidate"].sum()),
                "anchor_merchants": "|".join(anchors),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_output_graph(
    graph: nx.Graph,
    merchants: pd.DataFrame,
) -> nx.Graph:
    merchant_ids = set(merchants["merchant_id"].astype(str))
    return graph.subgraph(merchant_ids).copy()


def build_run_directory(
    root: Path,
    config: AppConfig,
    started_at: datetime,
) -> Path:
    timestamp = started_at.strftime("%y%m%d%H%M%S")
    name = (
        f"{config.graph.edge_weight_method}_"
        f"{config.community.algorithm.lower()}_{timestamp}"
    )
    return root / name


def create_run_directory(
    root: Path,
    config: AppConfig,
    started_at: datetime,
) -> Path:
    directory = build_run_directory(root, config, started_at)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise AlgorithmError(f"运行输出目录已存在，拒绝覆盖: directory={directory}") from error
    return directory


def write_cluster_outputs(
    config: AppConfig,
    output_directory: Path,
    city_code: str,
    merchants: pd.DataFrame,
    communities: pd.DataFrame,
    cleaning: CleaningResult,
) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    output_graph = build_output_graph(cleaning.graph, merchants)
    merchants.to_csv(
        output_directory / "merchants.csv",
        index=False,
        encoding="utf-8-sig",
    )
    communities.to_csv(
        output_directory / "communities.csv",
        index=False,
        encoding="utf-8-sig",
    )
    edge_columns = [
        "city_code",
        "merchant_a",
        "merchant_b",
        "weight",
        "sppmi",
        "jaccard",
        "support",
        "z_score",
    ]
    edges = pd.DataFrame(
        [
            {
                "city_code": city_code,
                "merchant_a": str(left),
                "merchant_b": str(right),
                "weight": float(data["weight"]),
                "sppmi": data.get("sppmi"),
                "jaccard": data.get("jaccard"),
                "support": int(data["support"]),
                "z_score": data.get("z_score"),
            }
            for left, right, data in output_graph.edges(data=True)
        ],
        columns=edge_columns,
    )
    edges.to_csv(
        output_directory / "edges.csv",
        index=False,
        encoding="utf-8-sig",
    )
    nx.write_graphml(output_graph, output_directory / "graph.graphml")

    summary: dict[str, str | int | float] = {
        "city_code": city_code,
        "merchant_count": int(len(merchants)),
        "community_count": int(len(communities)),
        "community_count_ge_3": int((communities["merchant_count"] >= 3).sum()),
        "community_count_ge_10": int((communities["merchant_count"] >= 10).sum()),
        "edge_count": int(output_graph.number_of_edges()),
        "suspect_online_count": int(
            (merchants["merchant_status"] == "suspect_online").sum()
        ),
        "suspect_isolated_count": int(
            (merchants["merchant_status"] == "suspect_isolated").sum()
        ),
        "anchor_candidate_count": int(merchants["is_anchor_candidate"].sum()),
        "chain_like_merchant_count": int(
            merchants.loc[
                merchants["is_chain_like"] == 1,
                "merchant_id",
            ].nunique()
        ),
        "visit_count_chain_like_merchant_count": int(
            merchants.loc[
                merchants["chain_reason"].astype(str).str.contains("visit_count"),
                "merchant_id",
            ].nunique()
        ),
        "multi_community_member_count": int(
            merchants.loc[
                merchants["is_multi_community_member"] == 1,
                "merchant_id",
            ].nunique()
        ),
        "cleaning_rounds": cleaning.cleaning_rounds,
        "community_algorithm": config.community.algorithm,
        "edge_weight_method": config.graph.edge_weight_method,
        "degree_penalty_gamma": config.graph.degree_penalty_gamma,
        "jaccard_threshold": config.graph.jaccard_threshold,
        "maximum_anchor_participation": config.anchors.maximum_participation,
        "chain_visit_count_quantile": config.anchors.chain_visit_count_quantile,
        "chain_minimum_visit_count": config.anchors.chain_minimum_visit_count,
        "source": "pair_statistics",
    }
    with (output_directory / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)


def _pair_merchants(pairs: set[MerchantPair]) -> set[str]:
    return {merchant for pair in pairs for merchant in pair}


def _next_experiment_number(content: str) -> int:
    numbers = [int(match.group(1)) for match in re.finditer(r"(?m)^(\d+)\. ", content)]
    return max(numbers, default=0) + 1


def _format_optional_count(value: int | None) -> str:
    return "未读取" if value is None else str(value)


def _community_size_series(cleaning: CleaningResult) -> pd.Series:
    return pd.Series(dict(Counter(cleaning.partition.values())), dtype="int64")


def _maximum_loss_stage(
    losses: dict[str, int],
) -> tuple[str, int]:
    return max(losses.items(), key=lambda item: item[1])


def build_experiment_record(
    config: AppConfig,
    statistics: PairStatistics,
    graph: nx.Graph,
    cleaning: CleaningResult,
    raw_merchants: pd.DataFrame,
    communities: pd.DataFrame,
    context: ExperimentContext,
    experiment_number: int,
) -> str:
    total_merchants = len(statistics.merchant_visit_counts)
    raw_pairs = set(statistics.strengths)
    raw_pair_merchants = _pair_merchants(raw_pairs)
    supported_pairs = {
        pair
        for pair, support in statistics.supports.items()
        if support >= config.cooccurrence.minimum_unique_users
    }
    supported_merchants = _pair_merchants(supported_pairs)
    candidates = calculate_edge_candidates(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    structurally_kept_candidates = prepare_edge_candidates(candidates, config.graph)
    candidate_merchants = _pair_merchants(set(candidates))
    structurally_kept_merchants = _pair_merchants(set(structurally_kept_candidates))
    connected_merchants = {str(node) for node in graph if graph.degree(node) > 0}
    cleaned_connected_merchants = {
        str(node)
        for node in cleaning.graph
        if cleaning.graph.degree(node) > 0
    }

    community_sizes = _community_size_series(cleaning)
    valid_community_count = int((community_sizes >= 3).sum())
    large_community_count = int((community_sizes >= 10).sum())
    valid_merchant_count = int(community_sizes.loc[community_sizes >= 3].sum())
    coverage = valid_merchant_count / total_merchants if total_merchants else 0.0

    active_merchants = raw_merchants.loc[raw_merchants["community_id"] >= 0]
    anchor_counts = active_merchants.groupby("community_id")[
        "is_anchor_candidate"
    ].sum()
    maximum_anchor_count = int(anchor_counts.max()) if not anchor_counts.empty else 0
    invalid_community_ids = set(community_sizes.loc[community_sizes < 3].index.astype(int))
    invalid_anchor_count = int(
        active_merchants.loc[
            active_merchants["community_id"].isin(invalid_community_ids),
            "is_anchor_candidate",
        ].sum()
    )
    multi_community_member_count = int(
        active_merchants.loc[
            active_merchants["is_multi_community_member"] == 1,
            "merchant_id",
        ].nunique()
    )
    chain_like_merchant_count = int(
        active_merchants.loc[
            active_merchants["is_chain_like"] == 1,
            "merchant_id",
        ].nunique()
    )
    visit_count_chain_like_merchant_count = int(
        active_merchants.loc[
            active_merchants["chain_reason"].astype(str).str.contains("visit_count"),
            "merchant_id",
        ].nunique()
    )

    losses: dict[str, int] = {
        "未形成时间窗商户对": total_merchants - len(raw_pair_merchants),
        "最小支持人数过滤": len(raw_pair_merchants) - len(supported_merchants),
        "Jaccard 结构门槛过滤": len(candidate_merchants) - len(structurally_kept_merchants),
        "互为 top-k 过滤": len(structurally_kept_merchants) - len(connected_merchants),
        "迭代 hub 清洗": len(connected_merchants) - len(cleaned_connected_merchants),
        "有效社区规模过滤": len(cleaned_connected_merchants) - valid_merchant_count,
    }
    if config.graph.edge_weight_method == "sppmi":
        losses["SPPMI 与显著性过滤"] = (
            len(supported_merchants) - len(candidate_merchants)
        )
    largest_loss_stage, largest_loss_count = _maximum_loss_stage(losses)
    supported_pair_ratio = len(supported_pairs) / len(raw_pairs) if raw_pairs else 0.0
    timestamp = context.started_at.isoformat(timespec="seconds")
    sppmi_parameters_active = config.graph.edge_weight_method == "sppmi"
    candidate_stage = (
        "通过 SPPMI 与显著性过滤"
        if sppmi_parameters_active
        else "SPPMI 阶段（已跳过）"
    )

    return f"""

{experiment_number}. 自动实验：从{context.source}执行{config.community.algorithm.capitalize()}聚类
    - 运行信息：
        - 时间：{timestamp}
        - 来源：{context.source_detail}
        - 输出目录：{context.output_directory}
        - 原始交易行数：{_format_optional_count(context.input_rows)}
        - 清洗后到访行数：{_format_optional_count(context.visit_rows)}
        - 运行耗时：{context.duration_seconds:.2f}秒
    - 参数：
    ```python
    CITY_CODE = {config.city.code!r}
    TRANSACTIONS_PATH = {str(config.input.transactions_path)!r}
    TIMESTAMP_FORMATS = {config.input.timestamp_formats!r}
    VISIT_MERGE_WINDOW_MINUTES = {config.visits.merge_window_minutes}
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = {config.visits.maximum_daily_merchants_per_card}
    COOC_WINDOW_MINUTES = {config.cooccurrence.window_minutes}
    TIME_DECAY_TAU_MINUTES = {config.cooccurrence.decay_tau_minutes}
    MIN_EDGE_SUPPORT = {config.cooccurrence.minimum_unique_users}
    EDGE_WEIGHT_METHOD = {config.graph.edge_weight_method!r}
    SPPMI_PARAMETERS_ACTIVE = {sppmi_parameters_active}
    PMI_SHIFT_K = {config.graph.sppmi_shift}
    PMI_ALPHA = {config.graph.context_smoothing_alpha}
    TOP_K_NEIGHBORS = {config.graph.top_k_neighbors}
    MINIMUM_Z_SCORE = {config.graph.minimum_z_score}
    DEGREE_PENALTY_GAMMA = {config.graph.degree_penalty_gamma}
    JACCARD_THRESHOLD = {config.graph.jaccard_threshold}
    COMMUNITY_ALGORITHM = {config.community.algorithm!r}
    COMMUNITY_RANDOM_SEED = {config.community.random_seed}
    MAX_CLEANING_ROUNDS = {config.community.maximum_cleaning_rounds}
    MIN_HUB_DEGREE = {config.community.minimum_hub_degree}
    PARTICIPATION_THRESHOLD = {config.community.participation_threshold}
    COMMUNITY_RESOLUTION = {config.community.resolution}
    ANCHOR_MIN_N = {config.anchors.minimum_count}
    ANCHOR_MAX_N = {config.anchors.maximum_count}
    ANCHOR_MERCHANTS_PER_COUNT = {config.anchors.merchants_per_anchor}
    ANCHOR_MIN_COMMUNITY_SIZE = {config.anchors.minimum_community_size}
    ANCHOR_MAX_PARTICIPATION = {config.anchors.maximum_participation}
    CHAIN_VISIT_COUNT_QUANTILE = {config.anchors.chain_visit_count_quantile}
    CHAIN_MINIMUM_VISIT_COUNT = {config.anchors.chain_minimum_visit_count}
    OUTPUT_ROOT = {str(config.output.directory)!r}
    EXPERIMENT_PATH = {str(config.experiments.path)!r}
    ```
    - 数据与过滤漏斗：
        - 全部商户：{total_merchants}个
        - 原始商户对：{len(raw_pairs)}对，覆盖商户{len(raw_pair_merchants)}个
        - 支持人数>={config.cooccurrence.minimum_unique_users}：{len(supported_pairs)}对，覆盖商户{len(supported_merchants)}个
        - {candidate_stage}：{len(candidates)}对，覆盖商户{len(candidate_merchants)}个
        - 通过 Jaccard 结构门槛：{len(structurally_kept_candidates)}对，覆盖商户{len(structurally_kept_merchants)}个
        - 通过互为 top-k：{graph.number_of_edges()}条边，覆盖商户{len(connected_merchants)}个
        - 迭代 hub 清洗后：{cleaning.graph.number_of_edges()}条边，覆盖商户{len(cleaned_connected_merchants)}个
    - 聚类结果：
        - 全部社区：{len(community_sizes)}个，孤立商户{int((community_sizes == 1).sum())}个
        - 有效社区：{valid_community_count}个（商户数>=3）
        - 较大社区：{large_community_count}个（商户数>=10）
        - 有效社区商户：{valid_merchant_count}个，占全部商户{coverage:.2%}
        - 候选锚点：{int(raw_merchants['is_anchor_candidate'].sum())}个，单社区最大{maximum_anchor_count}个
        - 无效社区锚点：{invalid_anchor_count}个
        - 连锁/泛客群商户：{chain_like_merchant_count}个，其中访问量规则命中{visit_count_chain_like_merchant_count}个
        - 多商圈普通成员：{multi_community_member_count}个
        - 清洗轮数：{cleaning.cleaning_rounds}
    - 自动分析：
        - 最大商户损失阶段：{largest_loss_stage}，减少{largest_loss_count}个商户
        - 最小支持人数过滤保留原始商户对的{supported_pair_ratio:.2%}
        - 有效社区覆盖率为{coverage:.2%}，未进入有效社区的商户为{total_merchants - valid_merchant_count}个
"""


def append_experiment_record(
    path: Path,
    config: AppConfig,
    statistics: PairStatistics,
    graph: nx.Graph,
    cleaning: CleaningResult,
    raw_merchants: pd.DataFrame,
    communities: pd.DataFrame,
    context: ExperimentContext,
) -> None:
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "# 实验记录\n\n## 结果记录\n"
        path.write_text(content, encoding="utf-8")

    record = build_experiment_record(
        config,
        statistics,
        graph,
        cleaning,
        raw_merchants,
        communities,
        context,
        _next_experiment_number(content),
    )
    with path.open("a", encoding="utf-8") as file:
        file.write(record)


def run_cluster_from_pairs(
    config: AppConfig,
    pair_statistics_path: Path,
    output_root: Path,
) -> ClusterSummary:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    run_directory = create_run_directory(output_root, config, started_at)
    statistics = load_pair_statistics(pair_statistics_path)
    graph = build_sparse_graph(statistics, config.cooccurrence, config.graph)
    chain_visit_count_threshold = calculate_chain_visit_count_threshold(
        [
            int(visit_count)
            for visit_count in statistics.merchant_visit_counts.values()
        ],
        config.anchors,
    )
    chain_like_merchant_ids = identify_chain_like_merchants(
        statistics.merchant_visit_counts,
        chain_visit_count_threshold,
    )
    clustering_graph = graph.copy()
    clustering_graph.remove_nodes_from(chain_like_merchant_ids)
    cleaning = clean_graph(clustering_graph, config.community)
    prepared_candidates = prepare_edge_candidates(
        calculate_edge_candidates(
            statistics,
            config.cooccurrence,
            config.graph,
        ),
        config.graph,
    )
    candidate_community_shares = calculate_candidate_community_weight_shares(
        prepared_candidates,
        cleaning.partition,
    )
    raw_merchants = build_merchant_results(
        cleaning,
        statistics,
        config.anchors,
        candidate_community_shares,
        chain_like_merchant_ids,
        chain_visit_count_threshold,
        config.city.code,
    )
    merchants = filter_merchants_by_community_size(raw_merchants, 3)
    communities = build_basic_community_results(merchants, config.city.code)
    write_cluster_outputs(
        config,
        run_directory,
        config.city.code,
        merchants,
        communities,
        cleaning,
    )
    append_experiment_record(
        config.experiments.path,
        config,
        statistics,
        graph,
        cleaning,
        raw_merchants,
        communities,
        ExperimentContext(
            source="商户对中间数据",
            source_detail=str(pair_statistics_path),
            input_rows=None,
            visit_rows=None,
            duration_seconds=time.perf_counter() - started,
            started_at=started_at,
            output_directory=run_directory,
        ),
    )
    return ClusterSummary(
        merchant_count=len(merchants),
        community_count=len(communities),
        edge_count=cleaning.graph.number_of_edges(),
        output_directory=str(run_directory),
    )


def main() -> None:
    raise RuntimeError(
        "项目不再支持配置文件或独立实验脚本入口，请通过 "
        "notebooks/run_hive_business_district.ipynb 配置并运行任务"
    )


if __name__ == "__main__":
    main()
