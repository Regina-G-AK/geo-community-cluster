from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from business_district.community import CleaningResult, clean_graph
from business_district.config import AppConfig
from business_district.geo import (
    add_geographic_seed_edges,
    prepare_geographic_transactions,
)
from business_district.graph import (
    PairStatistics,
    build_pair_statistics,
    build_sparse_graph,
    calculate_candidate_community_weight_shares,
    calculate_edge_candidates,
)
from business_district.intermediate import (
    build_pair_statistics_path,
    write_pair_statistics,
)
from business_district.results import (
    build_business_results,
    build_community_results,
    build_merchant_results,
    calculate_chain_visit_count_threshold,
    filter_merchants_by_community_size,
    identify_chain_like_merchants,
)
from business_district.resource_usage import record_resource_phase
from business_district.transactions import (
    build_merchant_metadata,
    load_transactions,
    merge_visits,
)


@dataclass(frozen=True)
class RunSummary:
    input_rows: int
    visit_rows: int
    merchant_count: int
    community_count: int
    edge_count: int
    output_directory: str


@dataclass(frozen=True)
class RunResult:
    summary: RunSummary
    business_results: pd.DataFrame


def run_algorithm_one_from_transactions(
    config: AppConfig,
    transactions: pd.DataFrame,
    source: str,
    source_detail: str,
) -> RunResult:
    started_at = datetime.now().astimezone()
    output_directory = config.output.directory
    geographic_preparation = prepare_geographic_transactions(
        transactions,
        config.geo.cluster_radius_meters,
    )
    record_resource_phase("地理种子准备")
    prepared_transactions = geographic_preparation.transactions
    merchant_metadata = build_merchant_metadata(prepared_transactions)
    record_resource_phase("商户元数据构建")
    visits = merge_visits(prepared_transactions, config.visits)
    record_resource_phase("访问合并")
    statistics: PairStatistics = build_pair_statistics(
        visits,
        config.cooccurrence,
    )
    record_resource_phase("商户对统计")
    write_pair_statistics(
        statistics,
        build_pair_statistics_path(output_directory, config.city.code),
    )
    record_resource_phase("商户对中间文件写入")
    transaction_graph = build_sparse_graph(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    record_resource_phase("交易图构建")
    graph = add_geographic_seed_edges(
        transaction_graph,
        geographic_preparation.seed_pairs,
    )
    record_resource_phase("地理种子边合并")
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
    record_resource_phase("连锁商户识别")
    clustering_graph = graph.copy()
    clustering_graph.remove_nodes_from(chain_like_merchant_ids)
    record_resource_phase("聚类图准备")
    cleaning: CleaningResult = clean_graph(clustering_graph, config.community)
    record_resource_phase("社区清洗")
    edge_candidates = calculate_edge_candidates(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    record_resource_phase("候选边计算")
    candidate_community_shares = calculate_candidate_community_weight_shares(
        edge_candidates,
        cleaning.partition,
    )
    record_resource_phase("社区权重占比计算")
    raw_merchants = build_merchant_results(
        cleaning,
        statistics,
        config.anchors,
        candidate_community_shares,
        chain_like_merchant_ids,
        chain_visit_count_threshold,
        config.city.code,
    )
    record_resource_phase("商户结果构建")
    merchants = filter_merchants_by_community_size(
        raw_merchants,
        config.anchors.minimum_community_size,
    )
    record_resource_phase("有效社区过滤")
    communities = build_community_results(
        merchants,
        visits,
        config.city.code,
    )
    record_resource_phase("社区结果构建")
    business_results = build_business_results(
        raw_merchants,
        merchant_metadata,
        started_at,
        config.anchors.minimum_community_size,
    )
    record_resource_phase("业务结果构建")
    return RunResult(
        summary=RunSummary(
            input_rows=len(transactions),
            visit_rows=len(visits),
            merchant_count=len(business_results),
            community_count=len(communities),
            edge_count=cleaning.graph.number_of_edges(),
            output_directory=str(output_directory),
        ),
        business_results=business_results,
    )


def run_algorithm_one(config: AppConfig) -> RunSummary:
    transactions = load_transactions(config.input)
    record_resource_phase("本地交易文件读取")
    result = run_algorithm_one_from_transactions(
        config,
        transactions,
        "原始交易",
        str(config.input.transactions_path),
    )
    return result.summary
