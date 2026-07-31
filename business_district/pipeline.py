from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from business_district.community import CleaningResult, clean_graph
from business_district.config import AppConfig
from business_district.geo import (
    add_geographic_seed_edges,
    build_merchant_coordinates,
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


def run_algorithm_one_from_transactions(
    config: AppConfig,
    transactions: pd.DataFrame,
) -> RunResult:
    started_at = datetime.now().astimezone()
    output_directory = config.output.directory
    geographic_preparation = prepare_geographic_transactions(
        transactions,
        config.geo.cluster_radius_meters,
    )
    prepared_transactions = geographic_preparation.transactions
    merchant_coordinates = build_merchant_coordinates(prepared_transactions)
    merchant_metadata = build_merchant_metadata(prepared_transactions)
    visits = merge_visits(prepared_transactions, config.visits)
    statistics: PairStatistics = build_pair_statistics(
        visits,
        config.cooccurrence,
        config.runtime.process_count,
    )
    write_pair_statistics(
        statistics,
        build_pair_statistics_path(output_directory, config.city.code),
    )
    transaction_graph = build_sparse_graph(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    graph = add_geographic_seed_edges(
        transaction_graph,
        geographic_preparation.seed_pairs,
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
    clustering_graph = graph.copy()
    clustering_graph.remove_nodes_from(chain_like_merchant_ids)
    cleaning: CleaningResult = clean_graph(
        clustering_graph,
        config.community,
        merchant_coordinates,
        config.geo.cluster_radius_meters,
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
    merchants = filter_merchants_by_community_size(
        raw_merchants,
        config.anchors.minimum_community_size,
    )
    communities = build_community_results(
        merchants,
        visits,
        config.city.code,
    )
    business_results = build_business_results(
        raw_merchants,
        merchant_metadata,
        started_at,
        config.anchors.minimum_community_size,
    )
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
    result = run_algorithm_one_from_transactions(
        config,
        transactions,
    )
    return result.summary
