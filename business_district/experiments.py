from __future__ import annotations

from collections import Counter
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd

from business_district.community import CleaningResult
from business_district.config import AppConfig
from business_district.graph import PairStatistics, calculate_edge_candidates


@dataclass(frozen=True)
class ExperimentContext:
    source: str
    source_detail: str
    input_rows: int | None
    visit_rows: int | None
    duration_seconds: float
    started_at: datetime
    output_directory: Path


def _pair_merchants(pairs: set[tuple[str, str]]) -> set[str]:
    return {merchant for pair in pairs for merchant in pair}


def _next_experiment_number(content: str) -> int:
    numbers = [
        int(match.group(1))
        for match in re.finditer(r"(?m)^(\d+)\. ", content)
    ]
    return max(numbers, default=0) + 1


def _format_optional_count(value: int | None) -> str:
    return "未读取" if value is None else str(value)


def build_experiment_record(
    config: AppConfig,
    statistics: PairStatistics,
    graph: nx.Graph,
    cleaning: CleaningResult,
    merchants: pd.DataFrame,
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
    connected_merchants = {
        str(node)
        for node in graph
        if graph.degree(node) > 0
    }
    cleaned_connected_merchants = {
        str(node)
        for node in cleaning.graph
        if cleaning.graph.degree(node) > 0
    }

    community_sizes = pd.Series(
        dict(Counter(cleaning.partition.values())),
        dtype="int64",
    )
    valid_community_count = int((community_sizes >= 3).sum())
    large_community_count = int((community_sizes >= 10).sum())
    valid_merchant_count = int(community_sizes.loc[community_sizes >= 3].sum())
    coverage = valid_merchant_count / total_merchants if total_merchants else 0.0

    active_merchants = merchants.loc[merchants["community_id"] >= 0]
    anchor_counts = active_merchants.groupby("community_id")[
        "is_anchor_candidate"
    ].sum()
    maximum_anchor_count = int(anchor_counts.max()) if not anchor_counts.empty else 0
    invalid_community_ids = set(
        community_sizes.loc[community_sizes < 3].index.astype(int)
    )
    invalid_anchor_count = int(
        active_merchants.loc[
            active_merchants["community_id"].isin(invalid_community_ids),
            "is_anchor_candidate",
        ].sum()
    )
    chain_like_merchant_count = int(
        merchants.loc[
            merchants["is_chain_like"] == 1,
            "merchant_id",
        ].nunique()
    )
    visit_count_chain_like_merchant_count = int(
        merchants.loc[
            merchants["chain_reason"].astype(str).str.contains("visit_count"),
            "merchant_id",
        ].nunique()
    )
    multi_community_member_count = int(
        active_merchants.loc[
            active_merchants["is_multi_community_member"] == 1,
            "merchant_id",
        ].nunique()
    )

    losses: dict[str, int] = {
        "未形成时间窗商户对": total_merchants - len(raw_pair_merchants),
        "最小支持人数过滤": len(raw_pair_merchants) - len(supported_merchants),
        "互为top-k过滤": len(candidate_merchants) - len(connected_merchants),
        "迭代hub清洗": len(connected_merchants) - len(cleaned_connected_merchants),
        "有效社区规模过滤": len(cleaned_connected_merchants) - valid_merchant_count,
    }
    if config.graph.edge_weight_method == "sppmi":
        losses["SPPMI与显著性过滤"] = (
            len(supported_merchants) - len(candidate_merchants)
        )
    largest_loss_stage, largest_loss_count = max(
        losses.items(),
        key=lambda item: item[1],
    )
    supported_pair_ratio = len(supported_pairs) / len(raw_pairs) if raw_pairs else 0.0

    timestamp = context.started_at.isoformat(timespec="seconds")
    sppmi_parameters_active = config.graph.edge_weight_method == "sppmi"
    candidate_stage = (
        "通过SPPMI与显著性过滤"
        if sppmi_parameters_active
        else "SPPMI阶段（已跳过）"
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
        - 通过互为top-k：{graph.number_of_edges()}条边，覆盖商户{len(connected_merchants)}个
        - 迭代hub清洗后：{cleaning.graph.number_of_edges()}条边，覆盖商户{len(cleaned_connected_merchants)}个
    - 聚类结果：
        - 全部社区：{len(community_sizes)}个，孤立商户{int((community_sizes == 1).sum())}个
        - 有效社区：{valid_community_count}个（商户数>=3）
        - 较大社区：{large_community_count}个（商户数>=10）
        - 有效社区商户：{valid_merchant_count}个，占全部商户{coverage:.2%}
        - 候选锚点：{int(merchants['is_anchor_candidate'].sum())}个，单社区最多{maximum_anchor_count}个
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
    merchants: pd.DataFrame,
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
        merchants,
        communities,
        context,
        _next_experiment_number(content),
    )
    with path.open("a", encoding="utf-8") as file:
        file.write(record)
