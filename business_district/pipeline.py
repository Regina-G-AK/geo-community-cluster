from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time

import pandas as pd

from business_district.community import CleaningResult, clean_graph
from business_district.config import AppConfig
from business_district.experiments import ExperimentContext, append_experiment_record
from business_district.graph import (
    PairStatistics,
    build_pair_statistics,
    build_sparse_graph,
    calculate_candidate_community_weight_shares,
    calculate_edge_candidates,
)
from business_district.intermediate import write_pair_statistics
from business_district.results import (
    build_business_results,
    build_community_results,
    build_merchant_results,
    calculate_chain_visit_count_threshold,
    filter_merchants_by_community_size,
    identify_chain_like_merchants,
    write_outputs,
)
from business_district.run_directory import create_run_directory
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
    started = time.perf_counter()
    output_directory = create_run_directory(
        config.output.directory,
        config,
        started_at,
    )
    merchant_metadata = build_merchant_metadata(transactions)
    visits = merge_visits(transactions, config.visits)
    statistics: PairStatistics = build_pair_statistics(
        visits,
        config.cooccurrence,
    )
    write_pair_statistics(
        statistics,
        output_directory / "pair_statistics.pkl",
    )
    graph = build_sparse_graph(
        statistics,
        config.cooccurrence,
        config.graph,
    )
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
    cleaning: CleaningResult = clean_graph(clustering_graph, config.community)
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
    write_outputs(
        output_directory,
        business_results,
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
            source=source,
            source_detail=source_detail,
            input_rows=len(transactions),
            visit_rows=len(visits),
            duration_seconds=time.perf_counter() - started,
            started_at=started_at,
            output_directory=output_directory,
        ),
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
    result = run_algorithm_one_from_transactions(
        config,
        load_transactions(config.input),
        "原始交易",
        str(config.input.transactions_path),
    )
    return result.summary
