from __future__ import annotations

import gc
from dataclasses import dataclass
from datetime import datetime

import networkx as nx
import pandas as pd

from business_district.community import CleaningResult, clean_graph
from business_district.config import AppConfig
from business_district.geo import (
    build_merchant_coordinates,
    filter_reliable_merchant_coordinates,
)
from business_district.graph import (
    PairStatistics,
    build_pair_statistics,
    build_sparse_graph,
    calculate_candidate_community_weight_shares,
    calculate_edge_candidates,
)
from business_district.probes import print_probe
from business_district.results import (
    build_business_results,
    build_community_results,
    build_merchant_results,
    calculate_chain_visit_count_threshold,
    filter_merchants_by_community_size,
    identify_chain_like_merchants,
)
from business_district.transactions import (
    MERCHANT,
    MERCHANT_CATEGORY,
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
    transaction_graph: nx.Graph


def run_algorithm_one_from_transactions(
    config: AppConfig,
    transactions: pd.DataFrame,
) -> RunResult:
    started_at = datetime.now().astimezone()
    output_directory = config.output.directory
    input_rows = len(transactions)
    print_probe(
        "initial.prepare_started",
        f"transaction_rows={len(transactions)}",
    )
    prepared_transactions = transactions
    merchant_coordinates = filter_reliable_merchant_coordinates(
        build_merchant_coordinates(prepared_transactions),
        config.geo.maximum_merchants_per_coordinate,
    )
    merchant_metadata = build_merchant_metadata(prepared_transactions)
    visits = merge_visits(prepared_transactions, config.visits)
    print_probe(
        "initial.visits_ready",
        f"prepared_transaction_rows={len(prepared_transactions)}, "
        f"visit_rows={len(visits)}, merchant_count={len(merchant_metadata)}",
    )
    # 逐步更新暂时停用，完整计算后一次性写入
    print_probe("initial.pair_statistics_started", f"visit_rows={len(visits)}")
    statistics: PairStatistics = build_pair_statistics(
        visits,
        config.cooccurrence,
        config.runtime.process_count,
    )
    print_probe(
        "initial.pair_statistics_ready",
        f"pair_count={len(statistics.strengths)}, "
        f"merchant_count={len(statistics.merchant_visit_counts)}",
    )
    print_probe("initial.graph_started", "")
    transaction_graph = build_sparse_graph(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    print_probe(
        "initial.graph_ready",
        f"node_count={transaction_graph.number_of_nodes()}, "
        f"edge_count={transaction_graph.number_of_edges()}",
    )
    chain_visit_count_threshold = calculate_chain_visit_count_threshold(
        list(statistics.merchant_visit_counts.values()),
        config.anchors,
    )
    category_chain_merchant_ids = set(
        prepared_transactions.loc[
            prepared_transactions[MERCHANT_CATEGORY].eq(2),
            MERCHANT,
        ].astype(str)
    ) if MERCHANT_CATEGORY in prepared_transactions.columns else set()
    visit_count_chain_merchant_ids = identify_chain_like_merchants(
        statistics.merchant_visit_counts,
        chain_visit_count_threshold,
    )
    chain_like_merchant_ids = (
        category_chain_merchant_ids | visit_count_chain_merchant_ids
    )
    clustering_graph = transaction_graph.subgraph(
        node for node in transaction_graph if node not in chain_like_merchant_ids
    )
    print_probe(
        "initial.community_detection_started",
        f"clustering_node_count={clustering_graph.number_of_nodes()}, "
        f"chain_like_merchant_count={len(chain_like_merchant_ids)}",
    )
    cleaning: CleaningResult = clean_graph(
        clustering_graph,
        config.community,
        merchant_coordinates,
        config.geo.cluster_radius_meters,
    )
    del clustering_graph, merchant_coordinates
    gc.collect()
    print_probe(
        "initial.community_detection_ready",
        f"community_count={len(set(cleaning.partition.values()))}, "
        f"edge_count={cleaning.graph.number_of_edges()}",
    )
    edge_candidates = calculate_edge_candidates(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    candidate_community_shares = calculate_candidate_community_weight_shares(
        edge_candidates,
        cleaning.partition,
    )
    del edge_candidates
    raw_merchants = build_merchant_results(
        cleaning,
        statistics,
        config.anchors,
        candidate_community_shares,
        chain_like_merchant_ids,
        category_chain_merchant_ids,
        chain_visit_count_threshold,
        config.city.code,
    )
    cleaned_edge_count = cleaning.graph.number_of_edges()
    del statistics
    del cleaning
    del candidate_community_shares
    del chain_like_merchant_ids
    del category_chain_merchant_ids
    del visit_count_chain_merchant_ids
    del prepared_transactions
    gc.collect()
    merchants = filter_merchants_by_community_size(
        raw_merchants,
        config.anchors.minimum_community_size,
    )
    communities = build_community_results(
        merchants,
        visits,
        config.city.code,
    )
    visit_rows = len(visits)
    community_count = len(communities)
    del merchants, visits, communities
    business_results = build_business_results(
        raw_merchants,
        merchant_metadata,
        started_at,
        config.anchors.minimum_community_size,
    )
    merchant_count = len(business_results)
    del raw_merchants, merchant_metadata
    gc.collect()
    print_probe(
        "initial.results_ready",
        f"merchant_rows={merchant_count}, community_rows={community_count}",
    )
    return RunResult(
        summary=RunSummary(
            input_rows=input_rows,
            visit_rows=visit_rows,
            merchant_count=merchant_count,
            community_count=community_count,
            edge_count=cleaned_edge_count,
            output_directory=str(output_directory),
        ),
        business_results=business_results,
        transaction_graph=transaction_graph,
    )


def run_algorithm_one(config: AppConfig) -> RunSummary:
    transactions = load_transactions(config.input)
    result = run_algorithm_one_from_transactions(
        config,
        transactions,
    )
    return result.summary
