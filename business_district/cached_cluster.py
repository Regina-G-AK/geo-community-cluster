from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd

from business_district.community import CleaningResult, clean_graph
from business_district.config import AppConfig
from business_district.graph import PairStatistics, build_sparse_graph
from business_district.experiments import ExperimentContext, append_experiment_record
from business_district.pair_statistics import load_pair_statistics
from business_district.results import (
    build_merchant_results,
    build_output_graph,
    filter_merchants_by_community_size,
)
from business_district.run_directory import create_run_directory


@dataclass(frozen=True)
class CachedClusterSummary:
    merchant_count: int
    community_count: int
    edge_count: int
    output_directory: str


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
        anchors = group.loc[
            group["is_anchor_candidate"] == 1,
            "merchant_id",
        ].astype(str)
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


def write_cached_cluster_outputs(
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

    summary = {
        "city_code": city_code,
        "merchant_count": int(len(merchants)),
        "community_count": int(len(communities)),
        "community_count_ge_3": int(
            (communities["merchant_count"] >= 3).sum()
        ),
        "community_count_ge_10": int(
            (communities["merchant_count"] >= 10).sum()
        ),
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


def run_cluster_from_pairs(
    config: AppConfig,
    pair_statistics_path: Path,
    output_directory: Path,
) -> CachedClusterSummary:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    run_directory = create_run_directory(output_directory, config, started_at)
    statistics: PairStatistics = load_pair_statistics(pair_statistics_path)
    graph = build_sparse_graph(
        statistics,
        config.cooccurrence,
        config.graph,
    )
    cleaning = clean_graph(graph, config.community)
    raw_merchants = build_merchant_results(
        cleaning,
        statistics,
        config.anchors,
        config.city.code,
    )
    merchants = filter_merchants_by_community_size(raw_merchants)
    communities = build_basic_community_results(merchants, config.city.code)
    write_cached_cluster_outputs(
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
        merchants,
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
    return CachedClusterSummary(
        merchant_count=len(merchants),
        community_count=len(communities),
        edge_count=cleaning.graph.number_of_edges(),
        output_directory=str(run_directory),
    )
