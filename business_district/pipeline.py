from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time

from business_district.community import CleaningResult, clean_graph
from business_district.config import AppConfig
from business_district.graph import PairStatistics, build_pair_statistics, build_sparse_graph
from business_district.experiments import ExperimentContext, append_experiment_record
from business_district.results import (
    build_community_results,
    build_merchant_results,
    filter_merchants_by_community_size,
    write_outputs,
)
from business_district.run_directory import create_run_directory
from business_district.transactions import load_transactions, merge_visits


@dataclass(frozen=True)
class RunSummary:
    input_rows: int
    visit_rows: int
    merchant_count: int
    community_count: int
    edge_count: int
    output_directory: str


def run_algorithm_one(config: AppConfig) -> RunSummary:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    output_directory = create_run_directory(
        config.output.directory,
        config,
        started_at,
    )
    transactions = load_transactions(config.input)
    visits = merge_visits(transactions, config.visits)
    statistics: PairStatistics = build_pair_statistics(
        visits,
        config.cooccurrence,
    )
    graph = build_sparse_graph(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    cleaning: CleaningResult = clean_graph(graph, config.community)
    raw_merchants = build_merchant_results(
        cleaning,
        statistics,
        config.anchors,
        config.city.code,
    )
    merchants = filter_merchants_by_community_size(raw_merchants)
    communities = build_community_results(
        merchants,
        visits,
        config.city.code,
    )
    write_outputs(
        config,
        output_directory,
        merchants,
        communities,
        cleaning,
        len(transactions),
        len(visits),
    )
    append_experiment_record(
        config.experiments.path,
        config,
        statistics,
        graph,
        cleaning,
        merchants,
        communities,
        ExperimentContext(
            source="原始交易",
            source_detail=str(config.input.transactions_path),
            input_rows=len(transactions),
            visit_rows=len(visits),
            duration_seconds=time.perf_counter() - started,
            started_at=started_at,
            output_directory=output_directory,
        ),
    )
    return RunSummary(
        input_rows=len(transactions),
        visit_rows=len(visits),
        merchant_count=len(merchants),
        community_count=len(communities),
        edge_count=cleaning.graph.number_of_edges(),
        output_directory=str(output_directory),
    )
