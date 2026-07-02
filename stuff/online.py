from __future__ import annotations

import csv
import math
import time
import tracemalloc
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import igraph as ig
import leidenalg as la
import networkx as nx
import pandas as pd


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
INPUT_PATH = SCRIPT_DIRECTORY / "merchants_sorted_offline.txt"
MERCHANT_OUTPUT_PATH = SCRIPT_DIRECTORY / "merchants_offline.csv"
COMMUNITY_OUTPUT_PATH = SCRIPT_DIRECTORY / "communities_offline.csv"
EDGE_OUTPUT_PATH = SCRIPT_DIRECTORY / "edges_offline.csv"
INPUT_DELIMITER = "·"
INPUT_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

CITY_CODE = "shanghai"
VISIT_MERGE_WINDOW_MINUTES = 30
MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
COOC_WINDOW_MINUTES = 120
TIME_DECAY_TAU_MINUTES = 60.0
MIN_EDGE_SUPPORT = 3
PMI_ALPHA = 0.75
PMI_SHIFT_K = 3.0
TOP_K_NEIGHBORS = 15
MINIMUM_Z_SCORE = 0.0
COMMUNITY_RESOLUTION = 1.0
RANDOM_SEED = 42
MAX_CLEANING_ROUNDS = 3
MIN_HUB_DEGREE = 8
PARTICIPATION_THRESHOLD = 0.75
ANCHOR_MIN_N = 3
ANCHOR_MAX_N = 3
ANCHOR_MERCHANTS_PER_COUNT = 20
ANCHOR_MIN_COMMUNITY_SIZE = 3

CARD = "card_id"
MERCHANT = "merchant_id"
TIMESTAMP = "timestamp"
MerchantPair = tuple[str, str]


class TransactionDataError(ValueError):
    """输入交易数据不符合约束。"""


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


def load_offline_transactions(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise TransactionDataError(f"输入文件不存在: path={path}")

    rows: list[tuple[str, str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file, delimiter=INPUT_DELIMITER)
        header = next(reader, None)
        if header is None:
            raise TransactionDataError(f"输入文件为空: path={path}")
        for line_number, fields in enumerate(reader, start=2):
            if len(fields) != 3:
                raise TransactionDataError(
                    "交易数据列数错误: "
                    f"path={path}, line={line_number}, expected=3, "
                    f"actual={len(fields)}, fields={fields}"
                )
            card_id, transaction_time, merchant_id = (
                field.strip() for field in fields
            )
            rows.append((card_id, transaction_time, merchant_id))
    if not rows:
        raise TransactionDataError(f"输入文件忽略首行后没有交易数据: path={path}")
    return pd.DataFrame(
        rows,
        columns=["account_number", "transaction_time", "storename"],
    )


def validate_and_normalize_source(source: pd.DataFrame) -> pd.DataFrame:
    required_columns = {"account_number", "storename", "transaction_time"}
    missing_columns = sorted(required_columns - set(source.columns))
    if missing_columns:
        raise TransactionDataError(
            f"输入数据缺少必要字段: missing_columns={missing_columns}"
        )
    if source.empty:
        raise TransactionDataError("输入文件没有交易数据")

    result = source[["account_number", "storename", "transaction_time"]].rename(
        columns={
            "account_number": CARD,
            "storename": MERCHANT,
            "transaction_time": TIMESTAMP,
        }
    )
    result = result.copy()
    result[CARD] = result[CARD].astype("string").str.strip()
    result[MERCHANT] = result[MERCHANT].astype("string").str.strip()
    invalid_identifier = (
        result[CARD].isna()
        | result[MERCHANT].isna()
        | result[CARD].eq("")
        | result[MERCHANT].eq("")
    )
    if invalid_identifier.any():
        examples = result.loc[invalid_identifier].head(5).to_dict(orient="records")
        raise TransactionDataError(
            "输入文件包含空卡号或空商户名称: "
            f"invalid_rows={int(invalid_identifier.sum())}, examples={examples}"
        )

    result[TIMESTAMP] = pd.to_datetime(
        result[TIMESTAMP],
        format=INPUT_TIMESTAMP_FORMAT,
        errors="coerce",
    )
    invalid_timestamp = result[TIMESTAMP].isna()
    if invalid_timestamp.any():
        examples = source.loc[invalid_timestamp, "transaction_time"].head(5).tolist()
        raise TransactionDataError(
            "输入文件包含格式错误的交易时间: "
            f"required_format={INPUT_TIMESTAMP_FORMAT}, "
            f"invalid_rows={int(invalid_timestamp.sum())}, examples={examples}"
        )
    result = result.drop_duplicates(subset=[CARD, MERCHANT, TIMESTAMP], keep="first")
    return result.sort_values([CARD, TIMESTAMP, MERCHANT]).reset_index(drop=True)


def merge_visits(transactions: pd.DataFrame) -> pd.DataFrame:
    merge_window = pd.Timedelta(minutes=VISIT_MERGE_WINDOW_MINUTES)
    visit_rows: list[tuple[str, str, pd.Timestamp]] = []
    for card_id, group in transactions.groupby(CARD, sort=False):
        last_timestamp_by_merchant: dict[str, pd.Timestamp] = {}
        for row in group.itertuples(index=False):
            merchant_id = str(getattr(row, MERCHANT))
            timestamp = pd.Timestamp(getattr(row, TIMESTAMP))
            previous_timestamp = last_timestamp_by_merchant.get(merchant_id)
            if previous_timestamp is not None and timestamp - previous_timestamp < merge_window:
                last_timestamp_by_merchant[merchant_id] = timestamp
                continue
            visit_rows.append((str(card_id), merchant_id, timestamp))
            last_timestamp_by_merchant[merchant_id] = timestamp

    visits = pd.DataFrame(visit_rows, columns=[CARD, MERCHANT, TIMESTAMP])
    visits["visit_date"] = visits[TIMESTAMP].dt.normalize()
    daily_counts = (
        visits.groupby([CARD, "visit_date"])[MERCHANT]
        .nunique()
        .rename("daily_merchant_count")
    )
    visits = visits.join(daily_counts, on=[CARD, "visit_date"])
    valid = visits["daily_merchant_count"] <= MAXIMUM_DAILY_MERCHANTS_PER_CARD
    return visits.loc[valid, [CARD, MERCHANT, TIMESTAMP]].sort_values(
        [CARD, TIMESTAMP, MERCHANT]
    ).reset_index(drop=True)


def build_pair_statistics(visits: pd.DataFrame) -> PairStatistics:
    window = pd.Timedelta(minutes=COOC_WINDOW_MINUTES)
    strength_by_pair: dict[MerchantPair, float] = defaultdict(float)
    support_by_pair: dict[MerchantPair, int] = defaultdict(int)
    merchant_visit_counts: Counter[str] = Counter(visits[MERCHANT].astype(str))

    for _, group in visits.groupby(CARD, sort=False):
        rows = list(
            group.sort_values(TIMESTAMP)[[MERCHANT, TIMESTAMP]].itertuples(
                index=False,
                name=None,
            )
        )
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
                weight = math.exp(-minutes / TIME_DECAY_TAU_MINUTES)
                user_pair_max[pair] = max(user_pair_max.get(pair, 0.0), weight)
        for pair, weight in user_pair_max.items():
            strength_by_pair[pair] += weight
            support_by_pair[pair] += 1

    return PairStatistics(
        strengths=dict(strength_by_pair),
        supports=dict(support_by_pair),
        merchant_visit_counts=dict(merchant_visit_counts),
    )


def calculate_sppmi_candidates(
    statistics: PairStatistics,
) -> dict[MerchantPair, tuple[float, float, int]]:
    eligible = {
        pair: strength
        for pair, strength in statistics.strengths.items()
        if statistics.supports[pair] >= MIN_EDGE_SUPPORT
    }
    if not eligible:
        return {}

    marginals: dict[str, float] = defaultdict(float)
    for (left, right), strength in eligible.items():
        marginals[left] += strength
        marginals[right] += strength
    total_strength = sum(eligible.values())
    smoothed_total = sum(
        strength**PMI_ALPHA for strength in marginals.values()
    ) / 2.0
    candidates: dict[MerchantPair, tuple[float, float, int]] = {}
    for pair, strength in eligible.items():
        left, right = pair
        left_context_pmi = math.log(
            strength * smoothed_total / (marginals[left] * marginals[right] ** PMI_ALPHA)
        )
        right_context_pmi = math.log(
            strength * smoothed_total / (marginals[right] * marginals[left] ** PMI_ALPHA)
        )
        sppmi = max(
            (left_context_pmi + right_context_pmi) / 2.0 - math.log(PMI_SHIFT_K),
            0.0,
        )
        expected = marginals[left] * marginals[right] / total_strength
        z_score = (strength - expected) / math.sqrt(expected) if expected > 0 else 0.0
        if sppmi > 0 and z_score >= MINIMUM_Z_SCORE:
            candidates[pair] = (sppmi, z_score, statistics.supports[pair])
    return candidates


def build_sparse_graph(statistics: PairStatistics) -> nx.Graph:
    candidates = calculate_sppmi_candidates(statistics)
    neighbors: dict[str, list[tuple[str, float, float, int]]] = defaultdict(list)
    for (left, right), (weight, z_score, support) in candidates.items():
        neighbors[left].append((right, weight, z_score, support))
        neighbors[right].append((left, weight, z_score, support))

    top_neighbors: dict[str, dict[str, tuple[float, float, int]]] = {}
    for merchant_id, merchant_neighbors in neighbors.items():
        ordered = sorted(
            merchant_neighbors,
            key=lambda item: (-item[1], -item[3], item[0]),
        )[:TOP_K_NEIGHBORS]
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
            graph.add_edge(
                merchant_id,
                neighbor,
                weight=float(weight),
                support=int(support),
                z_score=float(z_score),
            )
    return graph


def detect_communities(graph: nx.Graph) -> dict[str, int]:
    connected_nodes = sorted(str(node) for node in graph if graph.degree(node) > 0)
    isolated_nodes = sorted(str(node) for node in graph if graph.degree(node) == 0)
    connected_graph = graph.subgraph(connected_nodes).copy()
    communities: list[set[str]] = []
    if connected_graph.number_of_nodes() > 0:
        node_indices = {
            node_id: node_index for node_index, node_id in enumerate(connected_nodes)
        }
        edges = list(connected_graph.edges(data=True))
        igraph_graph = ig.Graph(
            n=len(connected_nodes),
            edges=[
                (node_indices[str(left)], node_indices[str(right)])
                for left, right, _ in edges
            ],
            directed=False,
        )
        partition = la.find_partition(
            igraph_graph,
            la.RBConfigurationVertexPartition,
            weights=[float(data["weight"]) for _, _, data in edges],
            resolution_parameter=COMMUNITY_RESOLUTION,
            seed=RANDOM_SEED,
        )
        communities = [
            {connected_nodes[node_index] for node_index in community}
            for community in partition
        ]
    communities.extend({node} for node in isolated_nodes)
    ordered = sorted(communities, key=lambda members: (-len(members), min(members)))
    return {
        node: community_id
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
            weight_by_community[partition[str(neighbor)]] += weight
        if total_weight == 0:
            result[str(node)] = 0.0
            continue
        result[str(node)] = 1.0 - sum(
            (weight / total_weight) ** 2 for weight in weight_by_community.values()
        )
    return result


def clean_graph(graph: nx.Graph) -> CleaningResult:
    working_graph = graph.copy()
    statuses = {
        str(node): "suspect_isolated" if graph.degree(node) == 0 else "active"
        for node in graph
    }
    completed_rounds = 0
    for round_index in range(MAX_CLEANING_ROUNDS):
        partition = detect_communities(working_graph)
        participation = calculate_participation(working_graph, partition)
        hubs = [
            str(node)
            for node in working_graph
            if working_graph.degree(node) >= MIN_HUB_DEGREE
            and participation[str(node)] >= PARTICIPATION_THRESHOLD
        ]
        if not hubs:
            break
        working_graph.remove_nodes_from(hubs)
        for node in hubs:
            statuses[node] = "suspect_online"
        completed_rounds = round_index + 1
    return CleaningResult(
        graph=working_graph,
        partition=detect_communities(working_graph),
        statuses=statuses,
        cleaning_rounds=completed_rounds,
    )


def anchor_count(community_size: int) -> int:
    scaled = math.ceil(community_size / ANCHOR_MERCHANTS_PER_COUNT)
    return min(community_size, max(ANCHOR_MIN_N, min(scaled, ANCHOR_MAX_N)))


def build_merchant_results(
    cleaning: CleaningResult,
    statistics: PairStatistics,
) -> pd.DataFrame:
    participation = calculate_participation(cleaning.graph, cleaning.partition)
    communities: dict[int, list[str]] = defaultdict(list)
    for merchant_id, community_id in cleaning.partition.items():
        communities[community_id].append(merchant_id)

    centrality_by_merchant: dict[str, float] = {}
    for community_nodes in communities.values():
        subgraph = cleaning.graph.subgraph(community_nodes)
        if subgraph.number_of_edges() > 0:
            centrality_by_merchant.update(nx.pagerank(subgraph, weight="weight"))
        else:
            equal_score = 1.0 / len(community_nodes)
            centrality_by_merchant.update({node: equal_score for node in community_nodes})

    rows: list[dict[str, str | int | float]] = []
    for merchant_id, community_id in cleaning.partition.items():
        centrality = centrality_by_merchant[merchant_id]
        merchant_participation = participation.get(merchant_id, 0.0)
        rows.append(
            {
                "city_code": CITY_CODE,
                "merchant_id": merchant_id,
                "community_id": int(community_id),
                "merchant_status": cleaning.statuses[merchant_id],
                "is_anchor_candidate": 0,
                "anchor_score": float(centrality * (1.0 - merchant_participation)),
                "pagerank": float(centrality),
                "participation": float(merchant_participation),
                "weighted_degree": float(cleaning.graph.degree(merchant_id, weight="weight")),
                "visit_count": int(statistics.merchant_visit_counts.get(merchant_id, 0)),
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        anchor_indices: list[int] = []
        for _, group in result.groupby("community_id", sort=True):
            if len(group) < ANCHOR_MIN_COMMUNITY_SIZE:
                continue
            ordered = group.sort_values(
                ["anchor_score", "weighted_degree", "visit_count", "merchant_id"],
                ascending=[False, False, False, True],
            )
            anchor_indices.extend(ordered.head(anchor_count(len(group))).index.tolist())
        result.loc[anchor_indices, "is_anchor_candidate"] = 1

    removed_rows = [
        {
            "city_code": CITY_CODE,
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


def build_community_results(merchants: pd.DataFrame, visits: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, str | int | float]] = []
    active = merchants[merchants["community_id"] >= 0]
    for community_id, group in active.groupby("community_id", sort=True):
        merchant_ids = set(group["merchant_id"].astype(str))
        selected = visits[visits[MERCHANT].isin(merchant_ids)].copy()
        hourly = pd.crosstab(selected[MERCHANT], selected[TIMESTAMP].dt.hour)
        hourly = hourly.reindex(columns=range(24), fill_value=0).astype(float)
        profile = hourly.sum(axis=0)
        profile_norm = float(math.sqrt((profile**2).sum()))
        similarities: list[float] = []
        for _, merchant_profile in hourly.iterrows():
            merchant_norm = float(math.sqrt((merchant_profile**2).sum()))
            if merchant_norm > 0 and profile_norm > 0:
                similarities.append(
                    float(merchant_profile.dot(profile) / (merchant_norm * profile_norm))
                )
        anchors = group.loc[group["is_anchor_candidate"] == 1, "merchant_id"].astype(str)
        rows.append(
            {
                "city_code": CITY_CODE,
                "community_id": int(community_id),
                "merchant_count": int(len(group)),
                "anchor_count": int(group["is_anchor_candidate"].sum()),
                "anchor_merchants": "|".join(anchors),
                "peak_hour": int(profile.idxmax()),
                "hourly_consistency": (
                    float(sum(similarities) / len(similarities)) if similarities else 0.0
                ),
            }
        )
    return pd.DataFrame(rows)


def pair_merchants(pairs: set[MerchantPair]) -> set[str]:
    return {merchant for pair in pairs for merchant in pair}


def build_edge_results(graph: nx.Graph) -> pd.DataFrame:
    columns = [
        "city_code",
        "merchant_a",
        "merchant_b",
        "weight",
        "support",
        "z_score",
    ]
    return pd.DataFrame(
        [
            {
                "city_code": CITY_CODE,
                "merchant_a": str(left),
                "merchant_b": str(right),
                "weight": float(data["weight"]),
                "support": int(data["support"]),
                "z_score": float(data["z_score"]),
            }
            for left, right, data in graph.edges(data=True)
        ],
        columns=columns,
    )


def write_csv_outputs(
    merchants: pd.DataFrame,
    communities: pd.DataFrame,
    edges: pd.DataFrame,
    merchant_path: Path,
    community_path: Path,
    edge_path: Path,
) -> None:
    merchants.to_csv(merchant_path, index=False, encoding="utf-8-sig")
    communities.to_csv(community_path, index=False, encoding="utf-8-sig")
    edges.to_csv(edge_path, index=False, encoding="utf-8-sig")


def print_analysis(
    statistics: PairStatistics,
    graph: nx.Graph,
    cleaning: CleaningResult,
    merchants: pd.DataFrame,
    communities: pd.DataFrame,
    input_rows: int,
    visit_rows: int,
    duration_seconds: float,
    input_path: Path,
    merchant_output_path: Path,
    community_output_path: Path,
    edge_output_path: Path,
) -> None:
    total_merchants = len(statistics.merchant_visit_counts)
    raw_pairs = set(statistics.strengths)
    supported_pairs = {
        pair for pair, support in statistics.supports.items() if support >= MIN_EDGE_SUPPORT
    }
    candidates = calculate_sppmi_candidates(statistics)
    connected = {str(node) for node in graph if graph.degree(node) > 0}
    cleaned_connected = {str(node) for node in cleaning.graph if cleaning.graph.degree(node) > 0}
    raw_pair_merchants = pair_merchants(raw_pairs)
    supported_merchants = pair_merchants(supported_pairs)
    candidate_merchants = pair_merchants(set(candidates))
    community_sizes = communities["merchant_count"].astype(int)
    valid_community_count = int((community_sizes >= 3).sum())
    large_community_count = int((community_sizes >= 10).sum())
    valid_merchant_count = int(community_sizes.loc[community_sizes >= 3].sum())
    coverage = valid_merchant_count / total_merchants if total_merchants else 0.0
    losses = {
        "未形成时间窗商户对": total_merchants - len(raw_pair_merchants),
        "最小支持人数过滤": len(raw_pair_merchants) - len(supported_merchants),
        "SPPMI 与显著性过滤": len(supported_merchants) - len(candidate_merchants),
        "互为 top-k 过滤": len(candidate_merchants) - len(connected),
        "迭代 hub 清洗": len(connected) - len(cleaned_connected),
        "有效社区规模过滤": len(cleaned_connected) - valid_merchant_count,
    }
    largest_loss_stage, largest_loss_count = max(losses.items(), key=lambda item: item[1])
    support_ratio = len(supported_pairs) / len(raw_pairs) if raw_pairs else 0.0
    active = merchants[merchants["community_id"] >= 0]
    anchor_counts = active.groupby("community_id")["is_anchor_candidate"].sum()
    maximum_anchor_count = int(anchor_counts.max()) if not anchor_counts.empty else 0
    invalid_ids = set(communities.loc[community_sizes < 3, "community_id"].astype(int))
    invalid_anchor_count = int(
        active.loc[active["community_id"].isin(invalid_ids), "is_anchor_candidate"].sum()
    )
    suspect_online_count = int(
        (merchants["merchant_status"] == "suspect_online").sum()
    )
    print("\n=== 商圈聚类汇总 ===")
    print(f"输入文件: {input_path}")
    print(f"原始交易行数: {input_rows}")
    print(f"清洗合并后到访行数: {visit_rows}")
    print(f"全部商户数: {total_merchants}")
    print(
        f"原始商户对: {len(raw_pairs)} 对，覆盖商户: {len(raw_pair_merchants)}"
    )
    print(
        f"最小支持人数过滤后: {len(supported_pairs)} 对，"
        f"覆盖商户: {len(supported_merchants)}，保留率: {support_ratio:.2%}"
    )
    print(
        f"SPPMI 与显著性过滤后: {len(candidates)} 对，"
        f"覆盖商户: {len(candidate_merchants)}"
    )
    print(
        f"互为 top-k 后: {graph.number_of_edges()} 条边，覆盖商户: {len(connected)}"
    )
    print(
        f"hub 清洗后: {cleaning.graph.number_of_edges()} 条边，"
        f"覆盖商户: {len(cleaned_connected)}，清洗轮数: {cleaning.cleaning_rounds}"
    )
    print(
        f"社区: 全部 {len(communities)}，孤立商户 {int((community_sizes == 1).sum())}，"
        f"有效社区 {valid_community_count}，较大社区 {large_community_count}"
    )
    print(
        f"有效社区商户: {valid_merchant_count}，覆盖率: {coverage:.2%}，"
        f"未覆盖商户: {total_merchants - valid_merchant_count}"
    )
    print(
        f"候选锚点: {int(merchants['is_anchor_candidate'].sum())}，"
        f"单社区最大锚点数: {maximum_anchor_count}，"
        f"无效社区锚点数: {invalid_anchor_count}"
    )
    print(f"疑似线上商户数: {suspect_online_count}")
    print(f"商户损失最大的阶段: {largest_loss_stage}，损失: {largest_loss_count}")
    print(f"总耗时: {duration_seconds:.2f} 秒")
    print(f"商户结果: {merchant_output_path}")
    print(f"社区结果: {community_output_path}")
    print(f"边结果: {edge_output_path}")


def run_clustering(
    input_path: Path,
    merchant_output_path: Path,
    community_output_path: Path,
    edge_output_path: Path,
) -> None:
    started = time.perf_counter()
    source = load_offline_transactions(input_path)
    transactions = validate_and_normalize_source(source)
    visits = merge_visits(transactions)
    statistics = build_pair_statistics(visits)
    graph = build_sparse_graph(statistics)
    cleaning = clean_graph(graph)
    merchants = build_merchant_results(cleaning, statistics)
    communities = build_community_results(merchants, visits)
    edges = build_edge_results(cleaning.graph)
    write_csv_outputs(
        merchants,
        communities,
        edges,
        merchant_output_path,
        community_output_path,
        edge_output_path,
    )
    duration_seconds = time.perf_counter() - started
    print_analysis(
        statistics,
        graph,
        cleaning,
        merchants,
        communities,
        len(transactions),
        len(visits),
        duration_seconds,
        input_path,
        merchant_output_path,
        community_output_path,
        edge_output_path,
    )


if __name__ == "__main__":
    tracemalloc.start()
    run_clustering(
        INPUT_PATH,
        MERCHANT_OUTPUT_PATH,
        COMMUNITY_OUTPUT_PATH,
        EDGE_OUTPUT_PATH,
    )
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    print(
        f"当前内存: {current_memory / 1024 / 1024:.2f} MB，"
        f"峰值内存: {peak_memory / 1024 / 1024:.2f} MB"
    )
