from __future__ import annotations

import json
import math
from pathlib import Path

import networkx as nx
import pandas as pd

from business_district.community import CleaningResult, calculate_participation
from business_district.config import AnchorConfig, AppConfig
from business_district.graph import PairStatistics
from business_district.transactions import MERCHANT, TIMESTAMP

MINIMUM_OUTPUT_COMMUNITY_SIZE = 3
COMMUNITY_COLUMNS = [
    "city_code",
    "community_id",
    "merchant_count",
    "anchor_count",
    "anchor_merchants",
    "peak_hour",
    "hourly_consistency",
]


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
        anchor_score = centrality * (
            1.0 - participation.get(merchant_id, 0.0)
        )
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
            "visit_count": int(
                statistics.merchant_visit_counts.get(merchant_id, 0)
            ),
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


def filter_merchants_by_community_size(merchants: pd.DataFrame) -> pd.DataFrame:
    active = merchants.loc[merchants["community_id"] >= 0]
    community_sizes = active.groupby("community_id").size()
    kept_community_ids = set(
        community_sizes.loc[
            community_sizes >= MINIMUM_OUTPUT_COMMUNITY_SIZE
        ].index
    )
    filtered = merchants.loc[
        merchants["community_id"].isin(kept_community_ids)
    ]
    return filtered.copy().reset_index(drop=True)


def build_output_graph(graph: nx.Graph, merchants: pd.DataFrame) -> nx.Graph:
    merchant_ids = set(merchants["merchant_id"].astype(str))
    return graph.subgraph(merchant_ids).copy()


def _hourly_consistency(
    community_merchants: set[str],
    visits: pd.DataFrame,
) -> tuple[int, float]:
    selected = visits[visits[MERCHANT].isin(community_merchants)].copy()
    hourly = pd.crosstab(selected[MERCHANT], selected[TIMESTAMP].dt.hour)
    hourly = hourly.reindex(columns=range(24), fill_value=0).astype(float)
    community_profile = hourly.sum(axis=0)
    peak_hour = int(community_profile.idxmax())
    profile_norm = float(math.sqrt((community_profile**2).sum()))
    similarities: list[float] = []
    for _, merchant_profile in hourly.iterrows():
        merchant_norm = float(math.sqrt((merchant_profile**2).sum()))
        if merchant_norm == 0 or profile_norm == 0:
            continue
        similarity = float(
            merchant_profile.dot(community_profile)
            / (merchant_norm * profile_norm)
        )
        similarities.append(similarity)
    consistency = sum(similarities) / len(similarities) if similarities else 0.0
    return peak_hour, consistency


def build_community_results(
    merchants: pd.DataFrame,
    visits: pd.DataFrame,
    city_code: str,
) -> pd.DataFrame:
    rows: list[dict[str, str | int | float]] = []
    active = merchants[merchants["community_id"] >= 0]
    for community_id, group in active.groupby("community_id", sort=True):
        merchant_ids = set(group["merchant_id"].astype(str))
        peak_hour, consistency = _hourly_consistency(merchant_ids, visits)
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
                "peak_hour": peak_hour,
                "hourly_consistency": float(consistency),
            }
        )
    return pd.DataFrame(rows, columns=COMMUNITY_COLUMNS)


def write_outputs(
    config: AppConfig,
    output_directory: Path,
    merchants: pd.DataFrame,
    communities: pd.DataFrame,
    cleaning: CleaningResult,
    input_rows: int,
    visit_rows: int,
) -> None:
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
                "city_code": config.city.code,
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
        "city_code": config.city.code,
        "city_name": config.city.name,
        "input_rows": input_rows,
        "visit_rows": visit_rows,
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
    }
    with (output_directory / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
