import math
import pickle
from datetime import datetime
from pathlib import Path

import networkx as nx
import pandas as pd
import pytest

from business_district.community import CleaningResult, detect_communities
from business_district.config import (
    AnchorConfig,
    CooccurrenceConfig,
    GraphConfig,
    load_config,
)
from business_district.errors import TransactionDataError
from business_district.geo import prepare_geographic_transactions
from business_district.graph import (
    PairStatistics,
    _calculate_sppmi_candidates,
    build_pair_statistics,
    build_sparse_graph,
)
from business_district.pipeline import (
    run_algorithm_one_from_transactions,
)
from business_district.results import build_business_results, build_merchant_results
from business_district.transactions import (
    CARD,
    DT,
    LATITUDE,
    LONGITUDE,
    MERCHANT,
    REGION,
    SOURCE_MERCHANT,
    TIMESTAMP,
    load_hive_transactions,
    load_transactions,
)


def _transaction_row(
    account_number: str,
    flow_number: str,
    storename: str,
    transaction_time: str,
    region: str,
    dt: str,
) -> str:
    return (
        f"{account_number}|{flow_number}|{storename}|{transaction_time}"
        f"|||{region}|||{dt}"
    )


def _transaction_row_with_coordinates(
    account_number: str,
    flow_number: str,
    storename: str,
    transaction_time: str,
    longitude: str,
    latitude: str,
    region: str,
    dt: str,
) -> str:
    return (
        f"{account_number}|{flow_number}|{storename}|{transaction_time}|"
        f"{longitude}|{latitude}|{region}|||{dt}"
    )


def _write_transaction_file(path: Path, rows: list[str]) -> None:
    header = (
        "account_number|global_flow_number|storename|transaction_time|"
        "pos_longitude|pos_latitude|region|is_intefere|status|dt"
    )
    path.write_text("\n".join([header, *rows]), encoding="utf-8")


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


def test_load_transactions_rejects_duplicate_flow_number(tmp_path: Path) -> None:
    transaction_path = tmp_path / "data.txt"
    _write_transaction_file(
        transaction_path,
        [
            _transaction_row("u1", "f1", "a", "20260101T100000", "上海", "20260101"),
            _transaction_row("u2", "f1", "b", "20260101T101000", "上海", "20260101"),
        ],
    )
    config_path = tmp_path / "city.ini"
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
minimum_unique_users = 1

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

[geo]
cluster_radius_meters = 1000.0

[anchors]
minimum_count = 1
maximum_count = 2
merchants_per_anchor = 2
minimum_community_size = 2
maximum_participation = 0.99
chain_visit_count_quantile = 1.0
chain_minimum_visit_count = 100

[output]
directory = "{(tmp_path / 'output').as_posix()}"
""",
        encoding="utf-8",
    )

    with pytest.raises(TransactionDataError, match="重复流水号"):
        load_transactions(load_config(config_path).input)


def test_load_hive_transactions_fills_partition_dt() -> None:
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "a",
                "transaction_time": "20260101T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
            }
        ]
    )

    transactions = load_hive_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260101",
        "source_table",
    )

    assert transactions[DT].tolist() == ["20260101"]


def test_load_hive_transactions_keeps_first_duplicate_flow_day() -> None:
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "late",
                "transaction_time": "20260102T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "dt": "20260102",
            },
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "early",
                "transaction_time": "20260101T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "dt": "20260101",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "normal",
                "transaction_time": "20260102T110000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "dt": "20260102",
            },
        ]
    )

    transactions = load_hive_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260102",
        "source_table",
    )

    assert transactions[MERCHANT].tolist() == ["early", "normal"]
    assert transactions[DT].tolist() == ["20260101", "20260102"]


def test_load_hive_transactions_treats_invalid_coordinates_as_missing() -> None:
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "partial",
                "transaction_time": "20260101T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "dt": "20260101",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "bad-number",
                "transaction_time": "20260101T101000",
                "pos_longitude": "bad",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "dt": "20260101",
            },
            {
                "account_number": "u3",
                "global_flow_number": "f3",
                "storename": "out-of-range",
                "transaction_time": "20260101T102000",
                "pos_longitude": "181.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "dt": "20260101",
            },
        ]
    )

    transactions = load_hive_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260101",
        "source_table",
    )

    assert transactions[LONGITUDE].isna().all()
    assert transactions[LATITUDE].isna().all()


def test_geographic_preparation_splits_same_name_far_stores() -> None:
    transactions = pd.DataFrame(
        [
            {
                CARD: "u1",
                MERCHANT: "shop",
                SOURCE_MERCHANT: "shop",
                TIMESTAMP: pd.Timestamp("2026-01-01 10:00:00"),
                LONGITUDE: 121.0000,
                LATITUDE: 31.0000,
                REGION: "shanghai",
                DT: "20260101",
            },
            {
                CARD: "u2",
                MERCHANT: "shop",
                SOURCE_MERCHANT: "shop",
                TIMESTAMP: pd.Timestamp("2026-01-01 10:05:00"),
                LONGITUDE: 121.0010,
                LATITUDE: 31.0010,
                REGION: "shanghai",
                DT: "20260101",
            },
            {
                CARD: "u3",
                MERCHANT: "shop",
                SOURCE_MERCHANT: "shop",
                TIMESTAMP: pd.Timestamp("2026-01-01 10:10:00"),
                LONGITUDE: 122.0000,
                LATITUDE: 32.0000,
                REGION: "shanghai",
                DT: "20260101",
            },
            {
                CARD: "u4",
                MERCHANT: "shop",
                SOURCE_MERCHANT: "shop",
                TIMESTAMP: pd.Timestamp("2026-01-01 10:15:00"),
                LONGITUDE: pd.NA,
                LATITUDE: pd.NA,
                REGION: "shanghai",
                DT: "20260101",
            },
        ]
    )

    prepared = prepare_geographic_transactions(transactions, 1000.0)

    merchant_ids = set(prepared.transactions[MERCHANT].astype(str))
    assert "shop" in merchant_ids
    split_ids = [
        merchant_id
        for merchant_id in merchant_ids
        if "#geo" in merchant_id
    ]
    assert len(split_ids) == 2
    assert prepared.summary.split_entity_count == 2


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


def test_chain_like_merchants_use_candidate_community_votes() -> None:
    graph = nx.Graph()
    graph.add_edge("a", "b", weight=10.0, support=3)
    graph.add_edge("c", "d", weight=10.0, support=3)
    cleaning = CleaningResult(
        graph=graph,
        partition={"a": 0, "b": 0, "c": 1, "d": 1},
        statuses={
            "a": "active",
            "b": "active",
            "c": "active",
            "d": "active",
        },
        cleaning_rounds=0,
    )

    merchants = build_merchant_results(
        cleaning,
        PairStatistics(
            strengths={},
            supports={},
            merchant_visit_counts={"a": 5, "b": 5, "chain": 200, "c": 5, "d": 5},
        ),
        AnchorConfig(
            minimum_count=1,
            maximum_count=2,
            merchants_per_anchor=2,
            minimum_community_size=1,
            maximum_participation=0.3,
            chain_visit_count_quantile=0.8,
            chain_minimum_visit_count=100,
        ),
        {"chain": {0: 0.9, 1: 0.1}},
        {"chain"},
        100,
        "test",
    )
    chain_rows = merchants.loc[merchants["merchant_id"] == "chain"]

    assert set(chain_rows["community_id"].astype(int)) == {0, 1}
    assert int(chain_rows["is_chain_like"].max()) == 1
    assert set(chain_rows["chain_reason"].astype(str)) == {"visit_count"}
    assert int(chain_rows["is_multi_community_member"].max()) == 1
    assert int(chain_rows["is_anchor_candidate"].sum()) == 0


def test_normal_merchants_use_final_graph_community_shares() -> None:
    graph = nx.Graph()
    graph.add_edge("a", "b", weight=10.0, support=3)
    graph.add_edge("c", "d", weight=10.0, support=3)
    graph.add_edge("bridge", "a", weight=1.0, support=3)
    graph.add_edge("bridge", "c", weight=1.0, support=3)
    cleaning = CleaningResult(
        graph=graph,
        partition={"a": 0, "b": 0, "bridge": 0, "c": 1, "d": 1},
        statuses={
            "a": "active",
            "b": "active",
            "bridge": "active",
            "c": "active",
            "d": "active",
        },
        cleaning_rounds=0,
    )

    merchants = build_merchant_results(
        cleaning,
        PairStatistics(
            strengths={},
            supports={},
            merchant_visit_counts={"a": 5, "b": 5, "bridge": 10, "c": 5, "d": 5},
        ),
        AnchorConfig(
            minimum_count=1,
            maximum_count=2,
            merchants_per_anchor=2,
            minimum_community_size=1,
            maximum_participation=0.99,
            chain_visit_count_quantile=1.0,
            chain_minimum_visit_count=100,
        ),
        {},
        set(),
        100,
        "test",
    )
    bridge_rows = merchants.loc[merchants["merchant_id"] == "bridge"]

    assert set(bridge_rows["community_id"].astype(int)) == {0, 1}
    assert set(bridge_rows["community_share"].round(6)) == {0.5}
    assert int(bridge_rows["is_chain_like"].max()) == 0
    assert int(bridge_rows["is_multi_community_member"].max()) == 1


def test_business_results_use_configured_minimum_community_size() -> None:
    merchants = pd.DataFrame(
        [
            {
                "merchant_id": "a",
                "primary_community_id": 0,
                "community_id": 0,
                "merchant_status": "active",
                "is_anchor_candidate": 1,
                "community_share": 1.0,
                "is_primary_community": 1,
                "is_multi_community_member": 0,
                "connected_community_count": 1,
                "chain_visit_count_threshold": 100,
                "is_chain_like": 0,
                "chain_reason": "",
            },
            {
                "merchant_id": "b",
                "primary_community_id": 0,
                "community_id": 0,
                "merchant_status": "active",
                "is_anchor_candidate": 0,
                "community_share": 1.0,
                "is_primary_community": 1,
                "is_multi_community_member": 0,
                "connected_community_count": 1,
                "chain_visit_count_threshold": 100,
                "is_chain_like": 0,
                "chain_reason": "",
            },
            {
                "merchant_id": "c",
                "primary_community_id": 1,
                "community_id": 1,
                "merchant_status": "active",
                "is_anchor_candidate": 0,
                "community_share": 1.0,
                "is_primary_community": 1,
                "is_multi_community_member": 0,
                "connected_community_count": 1,
                "chain_visit_count_threshold": 100,
                "is_chain_like": 0,
                "chain_reason": "",
            },
        ]
    )
    merchant_metadata = pd.DataFrame(
        [
            {MERCHANT: "a", "region": "shanghai", "dt": "20260101"},
            {MERCHANT: "b", "region": "shanghai", "dt": "20260101"},
            {MERCHANT: "c", "region": "shanghai", "dt": "20260101"},
        ]
    )

    result = build_business_results(
        merchants,
        merchant_metadata,
        datetime(2026, 1, 1, 10, 0, 0),
        2,
    )

    valid_rows = result.loc[result["storename"].isin(["a", "b"])]
    invalid_row = result.loc[result["storename"].eq("c")].iloc[0]
    assert set(valid_rows["status"]) == {"normal"}
    assert set(valid_rows["community_id"].astype(int)) == {0}
    assert invalid_row["status"] == "suspect_isolated"
    assert invalid_row["community_id"] == ""


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


def test_pipeline_uses_geographic_seed_and_pmi_to_join_unpositioned_merchant(
    tmp_path: Path,
) -> None:
    transaction_path = tmp_path / "data.txt"
    rows = [
        _transaction_row_with_coordinates(
            "u1",
            "f1a",
            "a",
            "20260101T100000",
            "121.0000",
            "31.0000",
            "shanghai",
            "20260101",
        ),
        _transaction_row_with_coordinates(
            "u1",
            "f1x",
            "x",
            "20260101T101000",
            "",
            "",
            "shanghai",
            "20260101",
        ),
        _transaction_row_with_coordinates(
            "u2",
            "f2b",
            "b",
            "20260101T100000",
            "121.0010",
            "31.0010",
            "shanghai",
            "20260101",
        ),
    ]
    _write_transaction_file(transaction_path, rows)

    output_path = tmp_path / "output"
    config_path = tmp_path / "city.ini"
    config_path.write_text(
        f"""
[city]
code = "test-city"
name = "test-city"

[input]
transactions_path = "{transaction_path.as_posix()}"
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
context_smoothing_alpha = 0.75
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

[geo]
cluster_radius_meters = 1000.0

[anchors]
minimum_count = 1
maximum_count = 2
merchants_per_anchor = 2
minimum_community_size = 2
maximum_participation = 0.99
chain_visit_count_quantile = 1.0
chain_minimum_visit_count = 100

[output]
directory = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    run_result = run_algorithm_one_from_transactions(
        config,
        load_transactions(config.input),
        "test",
        "test",
    )

    result = run_result.business_results
    communities = result.set_index("storename")["community_id"].to_dict()
    assert communities["a"] == communities["b"] == communities["x"]


def test_pipeline_writes_intermediate_output_only(tmp_path: Path) -> None:
    transaction_path = tmp_path / "data.txt"
    rows: list[str] = []
    for user_index in range(8):
        rows.extend(
            [
                _transaction_row(
                    f"u{user_index}",
                    f"f{user_index}a",
                    "a",
                    f"20260101T10{user_index:02d}00",
                    "上海",
                    "20260101",
                ),
                _transaction_row(
                    f"u{user_index}",
                    f"f{user_index}b",
                    "b",
                    f"20260101T10{user_index + 10:02d}00",
                    "上海",
                    "20260101",
                ),
                _transaction_row(
                    f"u{user_index}",
                    f"f{user_index}c",
                    "c",
                    f"20260101T10{user_index + 20:02d}00",
                    "上海",
                    "20260101",
                ),
            ]
        )
    rows.append(_transaction_row("u9", "f9a", "a", "20260102T100000", "浦东", "20260102"))
    rows.append(_transaction_row("u10", "f10d", "d", "20260101T100000", "上海", "20260101"))
    _write_transaction_file(transaction_path, rows)

    output_path = tmp_path / "output"
    config_path = tmp_path / "city.ini"
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

[geo]
cluster_radius_meters = 1000.0

[anchors]
minimum_count = 1
maximum_count = 2
merchants_per_anchor = 2
minimum_community_size = 2
maximum_participation = 0.99
chain_visit_count_quantile = 1.0
chain_minimum_visit_count = 100

[output]
directory = "{output_path.as_posix()}"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    run_result = run_algorithm_one_from_transactions(
        config,
        load_transactions(config.input),
        "test",
        "test",
    )
    summary = run_result.summary

    output_directory = Path(summary.output_directory)
    assert output_directory == output_path
    assert sorted(path.name for path in output_directory.iterdir()) == [
        "pair_statistics_test-city.pkl"
    ]
    assert summary.merchant_count == 4
    assert summary.community_count == 1

    result = run_result.business_results
    assert list(result.columns) == [
        "storename",
        "primary_community_id",
        "community_id",
        "previous_community_id",
        "region",
        "is_interfere",
        "update_time",
        "status",
        "is_position",
        "community_share",
        "is_primary_community",
        "is_multi_community_member",
        "is_chain_like",
        "chain_reason",
        "chain_visit_count_threshold",
        "connected_community_count",
        "dt",
    ]
    assert set(result["storename"]) == {"a", "b", "c", "d"}
    assert set(result["status"]) == {"normal", "suspect_isolated"}
    assert "suspect_lost" not in set(result["status"])
    assert set(result["is_interfere"]) == {0}
    assert result["update_time"].str.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}").all()
    merchant_a = result.loc[result["storename"].eq("a")].iloc[0]
    merchant_d = result.loc[result["storename"].eq("d")].iloc[0]
    assert merchant_a["region"] == "浦东"
    assert str(merchant_a["dt"]) == "20260102"
    assert merchant_a["status"] == "normal"
    assert merchant_d["status"] == "suspect_isolated"
    assert merchant_d["community_id"] == ""
    assert merchant_a["is_chain_like"] == 0
    assert merchant_a["is_primary_community"] == 1
    assert merchant_a["community_share"] == 1.0
    assert merchant_a["chain_visit_count_threshold"] == 100

    with (output_directory / "pair_statistics_test-city.pkl").open("rb") as file:
        pair_statistics = pickle.load(file)
    assert len(pair_statistics.strengths) == 3
    assert len(pair_statistics.merchant_visit_counts) == 4

    assert not (tmp_path / "experiments.md").exists()
