from __future__ import annotations

import argparse
import configparser
import json
import math
import re
import sqlite3
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
MERCHANT_ID = "merchant_id"
DEFAULT_CONFIG_PATH = Path("configs/shanghai.toml")
DEFAULT_PAIRS_PATH = Path("data/shanghai/pair_statistics.sqlite3")
DEFAULT_OUTPUT_PATH = Path("pmi_output/shanghai")


class ConfigurationError(ValueError):
    pass


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


def _read_config_parser(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        with path.open("r", encoding="utf-8") as file:
            parser.read_file(file)
    except configparser.Error as error:
        raise ConfigurationError(
            f"配置文件格式错误: path={path}, reason={error}"
        ) from error
    return parser


def _require_table(
    parser: configparser.ConfigParser,
    key: str,
) -> configparser.SectionProxy:
    if not parser.has_section(key):
        raise ConfigurationError(f"配置缺少 [{key}] 小节")
    return parser[key]


def _strip_optional_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1].strip()
    return stripped


def _require_string(
    data: configparser.SectionProxy,
    key: str,
    section: str,
) -> str:
    value = data.get(key)
    if value is None or not value.strip():
        raise ConfigurationError(f"配置项 {section}.{key} 必须是非空字符串")
    return _strip_optional_quotes(value)


def _require_int(
    data: configparser.SectionProxy,
    key: str,
    section: str,
    minimum: int,
) -> int:
    raw_value = _require_string(data, key, section)
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(
            f"配置项 {section}.{key} 必须是不小于 {minimum} 的整数"
        ) from error
    if value < minimum:
        raise ConfigurationError(
            f"配置项 {section}.{key} 必须是不小于 {minimum} 的整数"
        )
    return value


def _require_float(
    data: configparser.SectionProxy,
    key: str,
    section: str,
    minimum: float,
) -> float:
    raw_value = _require_string(data, key, section)
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ConfigurationError(
            f"配置项 {section}.{key} 必须是不小于 {minimum} 的数值"
        ) from error
    if value < minimum:
        raise ConfigurationError(
            f"配置项 {section}.{key} 必须是不小于 {minimum} 的数值"
        )
    return value


def _require_string_list(
    data: configparser.SectionProxy,
    key: str,
    section: str,
) -> tuple[str, ...]:
    raw_value = data.get(key)
    if raw_value is None or not raw_value.strip():
        raise ConfigurationError(f"配置项 {section}.{key} 必须是非空逗号分隔字符串")
    values = tuple(
        _strip_optional_quotes(item)
        for item in raw_value.split(",")
        if item.strip()
    )
    if not values or any(not item for item in values):
        raise ConfigurationError(f"配置项 {section}.{key} 包含无效格式")
    return values


def _edge_weight_method(data: configparser.SectionProxy) -> str:
    value = _strip_optional_quotes(data.get("edge_weight_method", "sppmi"))
    if value not in {"sppmi", "transaction_count"}:
        raise ConfigurationError(
            "配置项 graph.edge_weight_method 必须是 sppmi 或 transaction_count"
        )
    return value


def _community_algorithm(data: configparser.SectionProxy) -> str:
    value = _require_string(data, "algorithm", "community").lower()
    if not value.replace("_", "").isalnum() or not value.isascii():
        raise ConfigurationError(
            "配置项 community.algorithm 只能包含 ASCII 小写字母、数字和下划线"
        )
    if value != "leiden":
        raise ConfigurationError(
            f"配置项 community.algorithm 暂不支持: {value}; 当前仅支持 leiden"
        )
    return value


def _resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigurationError(f"配置文件不存在: {path}")

    parser = _read_config_parser(path)
    city = _require_table(parser, "city")
    input_data = _require_table(parser, "input")
    visits = _require_table(parser, "visits")
    cooccurrence = _require_table(parser, "cooccurrence")
    graph = _require_table(parser, "graph")
    community = _require_table(parser, "community")
    anchors = _require_table(parser, "anchors")
    output = _require_table(parser, "output")
    experiments = _require_table(parser, "experiments")

    minimum_anchor_count = _require_int(anchors, "minimum_count", "anchors", 1)
    maximum_anchor_count = _require_int(anchors, "maximum_count", "anchors", 1)
    if minimum_anchor_count > maximum_anchor_count:
        raise ConfigurationError("配置项 anchors.minimum_count 不能大于 maximum_count")

    smoothing_alpha = _require_float(graph, "context_smoothing_alpha", "graph", 0.0)
    if smoothing_alpha > 1.0:
        raise ConfigurationError("配置项 graph.context_smoothing_alpha 不能大于 1")

    participation_threshold = _require_float(
        community,
        "participation_threshold",
        "community",
        0.0,
    )
    if participation_threshold > 1.0:
        raise ConfigurationError("配置项 community.participation_threshold 不能大于 1")

    return AppConfig(
        city=CityConfig(
            code=_require_string(city, "code", "city"),
            name=_require_string(city, "name", "city"),
        ),
        input=InputConfig(
            transactions_path=_resolve_path(
                path,
                _require_string(input_data, "transactions_path", "input"),
            ),
            timestamp_formats=_require_string_list(
                input_data,
                "timestamp_formats",
                "input",
            ),
        ),
        visits=VisitConfig(
            merge_window_minutes=_require_int(
                visits,
                "merge_window_minutes",
                "visits",
                1,
            ),
            maximum_daily_merchants_per_card=_require_int(
                visits,
                "maximum_daily_merchants_per_card",
                "visits",
                1,
            ),
        ),
        cooccurrence=CooccurrenceConfig(
            window_minutes=_require_int(
                cooccurrence,
                "window_minutes",
                "cooccurrence",
                1,
            ),
            decay_tau_minutes=_require_float(
                cooccurrence,
                "decay_tau_minutes",
                "cooccurrence",
                0.000001,
            ),
            minimum_unique_users=_require_int(
                cooccurrence,
                "minimum_unique_users",
                "cooccurrence",
                1,
            ),
        ),
        graph=GraphConfig(
            edge_weight_method=_edge_weight_method(graph),
            context_smoothing_alpha=smoothing_alpha,
            sppmi_shift=_require_float(graph, "sppmi_shift", "graph", 1.0),
            top_k_neighbors=_require_int(graph, "top_k_neighbors", "graph", 1),
            minimum_z_score=_require_float(graph, "minimum_z_score", "graph", 0.0),
        ),
        community=CommunityConfig(
            algorithm=_community_algorithm(community),
            resolution=_require_float(community, "resolution", "community", 0.000001),
            random_seed=_require_int(community, "random_seed", "community", 0),
            maximum_cleaning_rounds=_require_int(
                community,
                "maximum_cleaning_rounds",
                "community",
                1,
            ),
            minimum_hub_degree=_require_int(
                community,
                "minimum_hub_degree",
                "community",
                1,
            ),
            participation_threshold=participation_threshold,
        ),
        anchors=AnchorConfig(
            minimum_count=minimum_anchor_count,
            maximum_count=maximum_anchor_count,
            merchants_per_anchor=_require_int(
                anchors,
                "merchants_per_anchor",
                "anchors",
                1,
            ),
            minimum_community_size=_require_int(
                anchors,
                "minimum_community_size",
                "anchors",
                1,
            ),
        ),
        output=OutputConfig(
            directory=_resolve_path(
                path,
                _require_string(output, "directory", "output"),
            ),
        ),
        experiments=ExperimentConfig(
            path=_resolve_path(
                path,
                _require_string(experiments, "path", "experiments"),
            ),
        ),
    )


def _validate_pair_row(
    merchant_a: str,
    merchant_b: str,
    strength: float,
    support: int,
    path: Path,
) -> None:
    if not merchant_a or not merchant_b:
        raise PairStatisticsDataError(
            f"商户对中间文件包含空商户 ID: path={path}, merchant_a={merchant_a!r}, merchant_b={merchant_b!r}"
        )
    if merchant_a >= merchant_b:
        raise PairStatisticsDataError(
            f"商户对中间文件要求 merchant_a 小于 merchant_b: path={path}, merchant_a={merchant_a!r}, merchant_b={merchant_b!r}"
        )
    if strength < 0:
        raise PairStatisticsDataError(
            f"商户对 strength 不能小于 0: path={path}, merchant_a={merchant_a!r}, merchant_b={merchant_b!r}, strength={strength}"
        )
    if support < 1:
        raise PairStatisticsDataError(
            f"商户对 support 不能小于 1: path={path}, merchant_a={merchant_a!r}, merchant_b={merchant_b!r}, support={support}"
        )


def _validate_visit_row(
    merchant_id: str,
    visit_count: int,
    path: Path,
) -> None:
    if not merchant_id:
        raise PairStatisticsDataError(f"商户访问中间文件包含空商户 ID: path={path}")
    if visit_count < 1:
        raise PairStatisticsDataError(
            f"商户访问次数不能小于 1: path={path}, merchant_id={merchant_id!r}, visit_count={visit_count}"
        )


def load_pair_statistics(path: Path) -> PairStatistics:
    if not path.is_file():
        raise PairStatisticsDataError(f"商户对中间文件不存在: path={path}")

    connection = sqlite3.connect(str(path))
    try:
        pair_rows = connection.execute(
            "SELECT merchant_a, merchant_b, strength, support FROM merchant_pairs"
        ).fetchall()
        visit_rows = connection.execute(
            "SELECT merchant_id, visit_count FROM merchant_visits"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise PairStatisticsDataError(
            f"商户对中间文件格式错误: path={path}, reason={error}"
        ) from error
    finally:
        connection.close()

    strengths: dict[MerchantPair, float] = {}
    supports: dict[MerchantPair, int] = {}
    for merchant_a, merchant_b, strength, support in pair_rows:
        left = str(merchant_a)
        right = str(merchant_b)
        pair_strength = float(strength)
        pair_support = int(support)
        _validate_pair_row(left, right, pair_strength, pair_support, path)
        pair = (left, right)
        strengths[pair] = pair_strength
        supports[pair] = pair_support

    merchant_visit_counts: dict[str, int] = {}
    for merchant_id, visit_count in visit_rows:
        merchant_key = str(merchant_id)
        merchant_count = int(visit_count)
        _validate_visit_row(merchant_key, merchant_count, path)
        merchant_visit_counts[merchant_key] = merchant_count

    return PairStatistics(
        strengths=strengths,
        supports=supports,
        merchant_visit_counts=merchant_visit_counts,
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
    neighbors: dict[str, list[tuple[str, float, float | None, int]]] = defaultdict(list)
    for (left, right), (weight, z_score, support) in candidates.items():
        neighbors[left].append((right, weight, z_score, support))
        neighbors[right].append((left, weight, z_score, support))

    top_neighbors: dict[str, dict[str, EdgeCandidate]] = {}
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
    result: dict[str, float] = {}
    for node in graph:
        weight_by_community: dict[int, float] = defaultdict(float)
        total_weight = 0.0
        for neighbor, edge_data in graph[node].items():
            weight = float(edge_data["weight"])
            total_weight += weight
            weight_by_community[partition[neighbor]] += weight
        if total_weight == 0:
            result[str(node)] = 0.0
            continue
        result[str(node)] = 1.0 - sum(
            (weight / total_weight) ** 2
            for weight in weight_by_community.values()
        )
    return result


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


def build_merchant_results(
    cleaning: CleaningResult,
    statistics: PairStatistics,
    anchor_config: AnchorConfig,
    city_code: str,
) -> pd.DataFrame:
    participation = calculate_participation(cleaning.graph, cleaning.partition)
    communities: dict[int, list[str]] = {}
    for merchant_id, community_id in cleaning.partition.items():
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
    if not result.empty:
        anchor_indices: list[int] = []
        for _, group in result.groupby("community_id", sort=True):
            if len(group) < anchor_config.minimum_community_size:
                continue
            count = _anchor_count(len(group), anchor_config)
            ordered = group.sort_values(
                ["anchor_score", "weighted_degree", "visit_count", "merchant_id"],
                ascending=[False, False, False, True],
            )
            anchor_indices.extend(ordered.head(count).index.tolist())
        result.loc[anchor_indices, "is_anchor_candidate"] = 1

    removed_rows = [
        {
            "city_code": city_code,
            "merchant_id": merchant_id,
            "community_id": -1,
            "merchant_status": status,
            "is_anchor_candidate": 0,
            "anchor_score": 0.0,
            "pagerank": 0.0,
            "participation": 0.0,
            "weighted_degree": 0.0,
            "visit_count": int(statistics.merchant_visit_counts.get(merchant_id, 0)),
        }
        for merchant_id, status in cleaning.statuses.items()
        if status == "suspect_online"
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

    summary: dict[str, str | int] = {
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
        "cleaning_rounds": cleaning.cleaning_rounds,
        "community_algorithm": config.community.algorithm,
        "edge_weight_method": config.graph.edge_weight_method,
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
    candidate_merchants = _pair_merchants(set(candidates))
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

    losses: dict[str, int] = {
        "未形成时间窗商户对": total_merchants - len(raw_pair_merchants),
        "最小支持人数过滤": len(raw_pair_merchants) - len(supported_merchants),
        "互为 top-k 过滤": len(candidate_merchants) - len(connected_merchants),
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
    OUTPUT_ROOT = {str(config.output.directory)!r}
    EXPERIMENT_PATH = {str(config.experiments.path)!r}
    ```
    - 数据与过滤漏斗：
        - 全部商户：{total_merchants}个
        - 原始商户对：{len(raw_pairs)}对，覆盖商户{len(raw_pair_merchants)}个
        - 支持人数>={config.cooccurrence.minimum_unique_users}：{len(supported_pairs)}对，覆盖商户{len(supported_merchants)}个
        - {candidate_stage}：{len(candidates)}对，覆盖商户{len(candidate_merchants)}个
        - 通过互为 top-k：{graph.number_of_edges()}条边，覆盖商户{len(connected_merchants)}个
        - 迭代 hub 清洗后：{cleaning.graph.number_of_edges()}条边，覆盖商户{len(cleaned_connected_merchants)}个
    - 聚类结果：
        - 全部社区：{len(community_sizes)}个，孤立商户{int((community_sizes == 1).sum())}个
        - 有效社区：{valid_community_count}个（商户数>=3）
        - 较大社区：{large_community_count}个（商户数>=10）
        - 有效社区商户：{valid_merchant_count}个，占全部商户{coverage:.2%}
        - 候选锚点：{int(raw_merchants['is_anchor_candidate'].sum())}个，单社区最大{maximum_anchor_count}个
        - 无效社区锚点：{invalid_anchor_count}个
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
    cleaning = clean_graph(graph, config.community)
    raw_merchants = build_merchant_results(
        cleaning,
        statistics,
        config.anchors,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从商户对中间数据执行最终聚类")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"城市配置文件，默认 {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--pairs",
        default=str(DEFAULT_PAIRS_PATH),
        help=f"已生成的商户对 SQLite 中间文件，默认 {DEFAULT_PAIRS_PATH}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help=f"聚类输出根目录，默认 {DEFAULT_OUTPUT_PATH}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_cluster_from_pairs(
        load_config(Path(args.config).resolve()),
        Path(args.pairs).resolve(),
        Path(args.output).resolve(),
    )
    print(f"merchants={summary.merchant_count}")
    print(f"communities={summary.community_count}")
    print(f"edges={summary.edge_count}")
    print(f"output_directory={summary.output_directory}")


if __name__ == "__main__":
    main()
