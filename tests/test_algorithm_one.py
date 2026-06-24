import math
import json
import re
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd
import pytest

from business_district.community import detect_communities
from business_district.cached_cluster import run_cluster_from_pairs
from business_district.config import load_config
from business_district.config import CooccurrenceConfig, GraphConfig
from business_district.errors import AlgorithmError
from business_district.graph import (
    PairStatistics,
    _calculate_sppmi_candidates,
    build_pair_statistics,
    build_sparse_graph,
)
from business_district.pipeline import run_algorithm_one
from business_district.pair_statistics import load_pair_statistics, write_pair_statistics
from business_district.run_directory import create_run_directory
from business_district.transactions import CARD, MERCHANT, TIMESTAMP


def test_user_pair_contribution_uses_maximum() -> None:
    visits = pd.DataFrame(
        [
            {CARD: "u1", MERCHANT: "a", TIMESTAMP: pd.Timestamp("2026-01-01 10:00:00")},
            {CARD: "u1", MERCHANT: "b", TIMESTAMP: pd.Timestamp("2026-01-01 10:10:00")},
            {CARD: "u1", MERCHANT: "a", TIMESTAMP: pd.Timestamp("2026-01-02 10:00:00")},
            {CARD: "u1", MERCHANT: "b", TIMESTAMP: pd.Timestamp("2026-01-02 11:00:00")},
        ]
    )
    statistics = build_pair_statistics(
        visits,
        CooccurrenceConfig(
            window_minutes=120,
            decay_tau_minutes=60.0,
            minimum_unique_users=1,
        ),
    )

    assert statistics.supports[("a", "b")] == 1
    assert round(statistics.strengths[("a", "b")], 6) == round(
        math.exp(-10.0 / 60.0),
        6,
    )


def test_pair_statistics_file_round_trip(tmp_path: Path) -> None:
    statistics = PairStatistics(
        strengths={("a", "b"): 1.5},
        supports={("a", "b"): 3},
        merchant_visit_counts={"a": 4, "b": 5, "isolated": 1},
    )
    output_path = tmp_path / "pair_statistics.sqlite3"

    write_pair_statistics(statistics, output_path)

    assert load_pair_statistics(output_path) == statistics


def test_cached_cluster_writes_outputs(tmp_path: Path) -> None:
    pair_path = tmp_path / "pair_statistics.sqlite3"
    write_pair_statistics(
        PairStatistics(
            strengths={
                ("a", "b"): 8.0,
                ("a", "c"): 8.0,
                ("b", "c"): 8.0,
                ("x", "y"): 8.0,
            },
            supports={
                ("a", "b"): 8,
                ("a", "c"): 8,
                ("b", "c"): 8,
                ("x", "y"): 8,
            },
            merchant_visit_counts={
                "a": 8,
                "b": 8,
                "c": 8,
                "x": 8,
                "y": 8,
                "isolated": 1,
            },
        ),
        pair_path,
    )
    config_path = tmp_path / "cached-city.toml"
    output_path = tmp_path / "cached-output"
    config_path.write_text(
        f"""
[city]
code = "cached-city"
name = "缓存测试市"

[input]
transactions_path = "unused.csv"
timestamp_formats = %Y%m%dT%H%M%S

[visits]
merge_window_minutes = 30
maximum_daily_merchants_per_card = 30

[cooccurrence]
window_minutes = 120
decay_tau_minutes = 60.0
minimum_unique_users = 1

[graph]
edge_weight_method = "transaction_count"
context_smoothing_alpha = 1.0
sppmi_shift = 1.0
top_k_neighbors = 5
minimum_z_score = 0.0

[community]
algorithm = "leiden"
resolution = 1.0
random_seed = 42
maximum_cleaning_rounds = 1
minimum_hub_degree = 10
participation_threshold = 0.9

[anchors]
minimum_count = 1
maximum_count = 2
merchants_per_anchor = 2
minimum_community_size = 2

[output]
directory = "unused-output"

[experiments]
path = "{(tmp_path / 'cached-experiments.md').as_posix()}"
""",
        encoding="utf-8",
    )
    default_config_path = tmp_path / "default-method-city.toml"
    default_config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'edge_weight_method = "transaction_count"\n',
            "",
        ),
        encoding="utf-8",
    )
    assert load_config(default_config_path).graph.edge_weight_method == "sppmi"

    summary = run_cluster_from_pairs(
        load_config(config_path),
        pair_path,
        output_path,
    )

    assert summary.merchant_count == 3
    assert summary.community_count == 1
    run_directory = Path(summary.output_directory)
    assert run_directory.parent == output_path
    assert re.fullmatch(
        r"transaction_count_leiden_\d{12}",
        run_directory.name,
    )
    merchants = pd.read_csv(run_directory / "merchants.csv")
    assert set(merchants["merchant_id"]) == {"a", "b", "c"}
    assert (run_directory / "merchants.csv").exists()
    assert (run_directory / "communities.csv").exists()
    assert (run_directory / "edges.csv").exists()
    assert (run_directory / "graph.graphml").exists()
    assert (run_directory / "summary.json").exists()
    summary_data = json.loads(
        (run_directory / "summary.json").read_text(encoding="utf-8")
    )
    assert summary_data["edge_weight_method"] == "transaction_count"
    edges = pd.read_csv(run_directory / "edges.csv")
    assert len(edges) == 3
    assert set(edges["weight"]) == {8.0}
    assert edges["sppmi"].isna().all()
    assert edges["z_score"].isna().all()
    experiment_text = (tmp_path / "cached-experiments.md").read_text(encoding="utf-8")
    assert "数据与过滤漏斗" in experiment_text
    assert "有效社区覆盖率" in experiment_text
    assert "SPPMI阶段（已跳过）" in experiment_text
    assert "SPPMI_PARAMETERS_ACTIVE = False" in experiment_text

    collision_time = datetime(2026, 6, 22, 14, 30, 0).astimezone()
    collision_directory = create_run_directory(
        tmp_path / "collision-output",
        load_config(config_path),
        collision_time,
    )
    assert collision_directory.exists()
    assert collision_directory.name == "transaction_count_leiden_260622143000"
    with pytest.raises(AlgorithmError, match="拒绝覆盖"):
        create_run_directory(
            tmp_path / "collision-output",
            load_config(config_path),
            collision_time,
        )


def test_cds_pmi_matches_base_pmi_when_alpha_is_one() -> None:
    statistics = PairStatistics(
        strengths={
            ("a", "b"): 8.0,
            ("a", "c"): 2.0,
            ("b", "c"): 2.0,
            ("d", "e"): 100.0,
        },
        supports={
            ("a", "b"): 8,
            ("a", "c"): 2,
            ("b", "c"): 2,
            ("d", "e"): 100,
        },
        merchant_visit_counts={"a": 10, "b": 10, "c": 4, "d": 100, "e": 100},
    )
    candidates = _calculate_sppmi_candidates(
        statistics,
        CooccurrenceConfig(
            window_minutes=120,
            decay_tau_minutes=60.0,
            minimum_unique_users=1,
        ),
        GraphConfig(
            edge_weight_method="sppmi",
            context_smoothing_alpha=1.0,
            sppmi_shift=1.0,
            top_k_neighbors=3,
            minimum_z_score=0.0,
        ),
    )
    expected_pmi = math.log(8.0 * 112.0 / (10.0 * 10.0))

    assert round(candidates[("a", "b")][0], 6) == round(expected_pmi, 6)


def test_transaction_count_graph_uses_supported_pair_strength() -> None:
    statistics = PairStatistics(
        strengths={("a", "b"): 7.5, ("a", "c"): 20.0},
        supports={("a", "b"): 3, ("a", "c"): 1},
        merchant_visit_counts={"a": 10, "b": 8, "c": 4},
    )
    graph = build_sparse_graph(
        statistics,
        CooccurrenceConfig(
            window_minutes=120,
            decay_tau_minutes=60.0,
            minimum_unique_users=2,
        ),
        GraphConfig(
            edge_weight_method="transaction_count",
            context_smoothing_alpha=0.75,
            sppmi_shift=3.0,
            top_k_neighbors=5,
            minimum_z_score=10.0,
        ),
    )

    assert set(graph.edges) == {("a", "b")}
    assert graph.edges["a", "b"]["weight"] == 7.5
    assert "sppmi" not in graph.edges["a", "b"]
    assert "z_score" not in graph.edges["a", "b"]


def test_leiden_communities_are_connected_and_deterministic() -> None:
    graph = nx.Graph()
    graph.add_weighted_edges_from(
        [
            ("a", "b", 10.0),
            ("a", "c", 10.0),
            ("b", "c", 10.0),
            ("d", "e", 10.0),
            ("d", "f", 10.0),
            ("e", "f", 10.0),
            ("c", "d", 0.01),
        ]
    )
    graph.add_node("isolated")

    first = detect_communities(graph, 1.0, 42)
    second = detect_communities(graph, 1.0, 42)

    assert first == second
    assert first["a"] == first["b"] == first["c"]
    assert first["d"] == first["e"] == first["f"]
    assert first["a"] != first["d"]
    assert all(
        nx.is_connected(graph.subgraph(nodes))
        for community_id in set(first.values())
        if (
            nodes := [
                node
                for node, node_community_id in first.items()
                if node_community_id == community_id
            ]
        )
    )


def test_pipeline_writes_city_scoped_outputs(tmp_path: Path) -> None:
    transaction_path = tmp_path / "data.txt"
    rows: list[str] = []
    for user_index in range(8):
        rows.extend(
            [
                f"u{user_index}|t{user_index}a|a|20260101T10{user_index:02d}00|||上海|||",
                f"u{user_index}|t{user_index}b|b|20260101T10{user_index + 10:02d}00|||上海|||",
                f"u{user_index}|t{user_index}c|c|20260101T10{user_index + 20:02d}00|||上海|||",
            ]
        )
    transaction_path.write_text("\n".join(rows), encoding="utf-8")

    output_path = tmp_path / "output"
    config_path = tmp_path / "city.toml"
    config_path.write_text(
        f"""
[city]
code = "test-city"
name = "测试市"

[input]
transactions_path = "{transaction_path.as_posix()}"
timestamp_formats = %Y%m%dT%H%M%S

[visits]
merge_window_minutes = 30
maximum_daily_merchants_per_card = 30

[cooccurrence]
window_minutes = 120
decay_tau_minutes = 60.0
minimum_unique_users = 2

[graph]
edge_weight_method = "transaction_count"
context_smoothing_alpha = 0.75
sppmi_shift = 1.0
top_k_neighbors = 5
minimum_z_score = 0.0

[community]
algorithm = "leiden"
resolution = 1.0
random_seed = 42
maximum_cleaning_rounds = 2
minimum_hub_degree = 10
participation_threshold = 0.9

[anchors]
minimum_count = 1
maximum_count = 2
merchants_per_anchor = 2
minimum_community_size = 2

[output]
directory = "{output_path.as_posix()}"

[experiments]
path = "{(tmp_path / 'experiments.md').as_posix()}"
""",
        encoding="utf-8",
    )

    summary = run_algorithm_one(load_config(config_path))

    assert summary.merchant_count == 3
    assert summary.community_count >= 1
    run_directory = Path(summary.output_directory)
    assert run_directory.parent == output_path
    assert re.fullmatch(r"transaction_count_leiden_\d{12}", run_directory.name)
    assert (run_directory / "merchants.csv").exists()
    assert (run_directory / "communities.csv").exists()
    assert (run_directory / "edges.csv").exists()
    assert (run_directory / "graph.graphml").exists()
    assert (run_directory / "summary.json").exists()
    summary_data = json.loads(
        (run_directory / "summary.json").read_text(encoding="utf-8")
    )
    assert summary_data["community_algorithm"] == "leiden"
    assert summary_data["edge_weight_method"] == "transaction_count"
    edges = pd.read_csv(run_directory / "edges.csv")
    assert "sppmi" in edges.columns
    experiment_text = (tmp_path / "experiments.md").read_text(encoding="utf-8")
    assert "从原始交易执行Leiden聚类" in experiment_text
    assert "有效社区覆盖率" in experiment_text
