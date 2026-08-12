from __future__ import annotations

import math
import pickle
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterator

import networkx as nx
import pandas as pd
import pytest

from business_district.community import CleaningResult, clean_graph, detect_communities
from business_district.config import (
    AnchorConfig,
    AppConfig,
    CityConfig,
    CommunityConfig,
    CooccurrenceConfig,
    GeoConfig,
    GraphConfig,
    InputConfig,
    OutputConfig,
    RuntimeConfig,
    VisitConfig,
)
from business_district.errors import TransactionDataError
from business_district.geo import CoordinatePoint, prepare_geographic_transactions
from business_district.graph import (
    PairStatistics,
    _calculate_sppmi_candidates,
    build_pair_statistics,
    build_sparse_graph,
    iter_pair_statistics_updates,
)
from business_district.intermediate import (
    read_pair_statistics,
    write_pair_statistics_updates,
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


def _app_config(
    transaction_path: Path,
    output_path: Path,
    minimum_unique_users: int,
) -> AppConfig:
    return AppConfig(
        city=CityConfig(code="test-city", name="测试市"),
        input=InputConfig(
            transactions_path=transaction_path,
            timestamp_formats=("%Y%m%dT%H%M%S",),
        ),
        visits=VisitConfig(
            merge_window_minutes=30,
            maximum_daily_merchants_per_card=30,
        ),
        cooccurrence=CooccurrenceConfig(
            window_minutes=120,
            decay_tau_minutes=60.0,
            minimum_unique_users=minimum_unique_users,
        ),
        graph=GraphConfig(
            edge_weight_method="transaction_count",
            context_smoothing_alpha=0.75,
            sppmi_shift=1.0,
            top_k_neighbors=5,
            minimum_z_score=0.0,
        ),
        community=CommunityConfig(
            algorithm="leiden",
            resolution=1.0,
            random_seed=42,
            minimum_online_neighbor_count=2,
        ),
        geo=GeoConfig(
            cluster_radius_meters=1000.0,
            maximum_merchants_per_coordinate=100,
        ),
        anchors=AnchorConfig(
            minimum_count=1,
            maximum_count=2,
            merchants_per_anchor=2,
            minimum_community_size=2,
            maximum_participation=0.99,
            chain_visit_count_quantile=1.0,
            chain_minimum_visit_count=100,
        ),
        output=OutputConfig(directory=output_path),
        runtime=RuntimeConfig(process_count=2),
    )


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
        2,
    )

    assert statistics.supports[("a", "b")] == 1
    assert round(statistics.strengths[("a", "b")], 6) == round(
        math.exp(-10.0 / 60.0),
        6,
    )


def test_pair_statistics_multiprocessing_matches_serial_result() -> None:
    visits = pd.DataFrame(
        [
            {CARD: "u1", MERCHANT: "a", TIMESTAMP: pd.Timestamp("2026-01-01 10:00:00")},
            {CARD: "u1", MERCHANT: "b", TIMESTAMP: pd.Timestamp("2026-01-01 10:10:00")},
            {CARD: "u2", MERCHANT: "b", TIMESTAMP: pd.Timestamp("2026-01-01 11:00:00")},
            {CARD: "u2", MERCHANT: "c", TIMESTAMP: pd.Timestamp("2026-01-01 11:20:00")},
        ]
    )
    config = CooccurrenceConfig(
        window_minutes=60,
        decay_tau_minutes=30.0,
        minimum_unique_users=1,
    )

    serial = build_pair_statistics(visits, config, 1)
    parallel = build_pair_statistics(visits, config, 2)
    updates = list(iter_pair_statistics_updates(visits, config, 1))

    assert parallel == serial
    assert len(updates) == 2
    assert updates[0].supports == {("a", "b"): 1}
    assert updates[-1] == serial


def test_pair_statistics_updates_overwrite_previous_snapshot(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "pair_statistics_test.pkl"
    first = PairStatistics(
        strengths={("a", "b"): 1.0},
        supports={("a", "b"): 1},
        merchant_visit_counts={"a": 1, "b": 1},
    )
    second = PairStatistics(
        strengths={("a", "b"): 2.0, ("b", "c"): 1.0},
        supports={("a", "b"): 2, ("b", "c"): 1},
        merchant_visit_counts={"a": 2, "b": 3, "c": 1},
    )

    def build_updates() -> Iterator[PairStatistics]:
        yield first
        assert read_pair_statistics(output_path) == first
        yield second

    saved = write_pair_statistics_updates(build_updates(), output_path)

    assert saved == second
    assert read_pair_statistics(output_path) == second
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "pair_statistics_test.pkl"
    ]
    assert capsys.readouterr().out.splitlines() == [
        "边=1，点=2",
        "边=2，点=3",
    ]


def test_load_transactions_rejects_duplicate_flow_number(tmp_path: Path) -> None:
    transaction_path = tmp_path / "data.txt"
    _write_transaction_file(
        transaction_path,
        [
            _transaction_row("u1", "f1", "a", "20260101T100000", "上海", "20260101"),
            _transaction_row("u2", "f1", "b", "20260101T101000", "上海", "20260101"),
        ],
    )
    with pytest.raises(TransactionDataError, match="重复流水号"):
        load_transactions(
            InputConfig(
                transactions_path=transaction_path,
                timestamp_formats=("%Y%m%dT%H%M%S",),
            )
        )


def test_load_hive_transactions_fills_partition_dt() -> None:
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "a",
                "merchant_category": "1",
                "transaction_time": "20260101T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
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
                "merchant_category": "1",
                "transaction_time": "20260102T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
                "dt": "20260102",
            },
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "early",
                "merchant_category": "1",
                "transaction_time": "20260101T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
                "dt": "20260101",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "normal",
                "merchant_category": "1",
                "transaction_time": "20260102T110000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
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
                "merchant_category": "1",
                "transaction_time": "20260101T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
                "dt": "20260101",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "bad-number",
                "merchant_category": "1",
                "transaction_time": "20260101T101000",
                "pos_longitude": "bad",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
                "dt": "20260101",
            },
            {
                "account_number": "u3",
                "global_flow_number": "f3",
                "storename": "out-of-range",
                "merchant_category": "1",
                "transaction_time": "20260101T102000",
                "pos_longitude": "181.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
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


def test_load_hive_transactions_keeps_only_offline_categories() -> None:
    source = pd.DataFrame(
        [
            {
                "account_number": f"u{category}",
                "global_flow_number": f"f{category}",
                "storename": f"shop-{category}",
                "merchant_category": str(category),
                "transaction_time": "20260101T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "N",
                "is_abnormal": "",
                "business_district": "",
                "dt": "20260101",
            }
            for category in range(4)
        ]
    )

    transactions = load_hive_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260101",
        "source_table",
    )

    assert transactions[MERCHANT].tolist() == ["shop-1", "shop-2"]
    assert transactions["merchant_category"].tolist() == [1, 2]


def test_load_hive_transactions_rejects_conflicting_merchant_categories() -> None:
    source = pd.DataFrame(
        [
            {
                "account_number": f"u{category}",
                "global_flow_number": f"f{category}",
                "storename": "same-shop",
                "merchant_category": str(category),
                "transaction_time": "20260101T100000",
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "shanghai",
                "is_interfere": "N",
                "is_abnormal": "",
                "business_district": "",
                "dt": "20260101",
            }
            for category in (1, 2)
        ]
    )

    with pytest.raises(TransactionDataError, match="同一商户对应多个商户分类"):
        load_hive_transactions(
            source,
            ("%Y%m%dT%H%M%S",),
            "20260101",
            "source_table",
        )


def test_geographic_preparation_uses_one_identical_coordinate_per_merchant() -> None:
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
                LONGITUDE: 121.0000,
                LATITUDE: 31.0000,
                REGION: "shanghai",
                DT: "20260101",
            },
            {
                CARD: "u3",
                MERCHANT: "shop",
                SOURCE_MERCHANT: "shop",
                TIMESTAMP: pd.Timestamp("2026-01-01 10:10:00"),
                LONGITUDE: 121.0000,
                LATITUDE: 31.0000,
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
    assert merchant_ids == {"shop"}
    assert prepared.summary.positioned_transaction_count == 3
    assert prepared.summary.positioned_merchant_count == 1
    assert prepared.summary.split_entity_count == 0


def test_geographic_preparation_rejects_conflicting_merchant_coordinates() -> None:
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
                LONGITUDE: 122.0000,
                LATITUDE: 32.0000,
                REGION: "shanghai",
                DT: "20260101",
            },
        ]
    )

    with pytest.raises(
        TransactionDataError,
        match=r"shop.*121\.0.*31\.0.*122\.0.*32\.0",
    ):
        prepare_geographic_transactions(transactions, 1000.0)


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


def test_clean_graph_marks_coordinate_missing_merchant_linked_to_distant_merchants(
) -> None:
    graph = nx.Graph()
    graph.add_edge("online-shop", "near-shop", weight=3.0)
    graph.add_edge("online-shop", "far-shop", weight=2.0)
    coordinates = {
        "near-shop": CoordinatePoint("near-shop", 121.0, 31.0),
        "far-shop": CoordinatePoint("far-shop", 121.05, 31.0),
    }

    cleaning = clean_graph(
        graph,
        CommunityConfig("leiden", 1.0, 42, 2),
        coordinates,
        1000.0,
    )

    assert cleaning.statuses["online-shop"] == "suspect_online"
    assert "online-shop" not in cleaning.graph


def test_clean_graph_keeps_positioned_or_geographically_concentrated_merchants(
) -> None:
    graph = nx.Graph()
    graph.add_edge("positioned-shop", "near-shop", weight=3.0)
    graph.add_edge("positioned-shop", "far-shop", weight=2.0)
    graph.add_edge("local-shop", "near-shop", weight=3.0)
    graph.add_edge("local-shop", "local-neighbor", weight=2.0)
    coordinates = {
        "positioned-shop": CoordinatePoint("positioned-shop", 121.02, 31.0),
        "near-shop": CoordinatePoint("near-shop", 121.0, 31.0),
        "far-shop": CoordinatePoint("far-shop", 121.05, 31.0),
        "local-neighbor": CoordinatePoint("local-neighbor", 121.001, 31.0),
    }

    cleaning = clean_graph(
        graph,
        CommunityConfig("leiden", 1.0, 42, 2),
        coordinates,
        1000.0,
    )

    assert cleaning.statuses["positioned-shop"] == "active"
    assert cleaning.statuses["local-shop"] == "active"
    assert {"positioned-shop", "local-shop"}.issubset(cleaning.graph)


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
        set(),
        100,
        "test",
    )
    chain_rows = merchants.loc[merchants["merchant_id"] == "chain"]

    assert set(chain_rows["community_id"].astype(int)) == {0, 1}
    assert int(chain_rows["is_chain_like"].max()) == 1
    assert set(chain_rows["chain_reason"].astype(str)) == {"visit_count"}


def test_category_two_merchant_is_non_anchor_multi_community_member() -> None:
    graph = nx.Graph()
    graph.add_edge("a", "b", weight=10.0, support=3)
    graph.add_edge("c", "d", weight=10.0, support=3)
    cleaning = CleaningResult(
        graph=graph,
        partition={"a": 0, "b": 0, "c": 1, "d": 1},
        statuses={merchant_id: "active" for merchant_id in ("a", "b", "c", "d")},
    )

    merchants = build_merchant_results(
        cleaning,
        PairStatistics(
            strengths={},
            supports={},
            merchant_visit_counts={"a": 5, "b": 5, "chain": 5, "c": 5, "d": 5},
        ),
        AnchorConfig(1, 2, 2, 1, 0.3, 0.8, 100),
        {"chain": {0: 0.7, 1: 0.3}},
        {"chain"},
        {"chain"},
        100,
        "test",
    )

    chain_rows = merchants.loc[merchants["merchant_id"].eq("chain")]
    assert set(chain_rows["community_id"].astype(int)) == {0, 1}
    assert int(chain_rows["is_anchor_candidate"].sum()) == 0
    assert set(chain_rows["chain_reason"].astype(str)) == {"merchant_category"}
    business_results = build_business_results(
        merchants,
        pd.DataFrame(
            [
                {
                    MERCHANT: merchant_id,
                    REGION: "shanghai",
                    DT: "20260101",
                }
                for merchant_id in ("a", "b", "chain", "c", "d")
            ]
        ),
        datetime.fromisoformat("2026-01-01T10:00:00"),
        1,
    )
    category_chain_rows = business_results.loc[
        business_results["storename"].eq("chain")
    ]
    assert set(category_chain_rows["status"].astype(str)) == {"normal"}
    assert int(category_chain_rows["is_position"].sum()) == 0
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
        set(),
        100,
        "test",
    )
    bridge_rows = merchants.loc[merchants["merchant_id"] == "bridge"]

    assert set(bridge_rows["community_id"].astype(int)) == {0, 1}
    assert set(bridge_rows["community_share"].round(6)) == {0.5}
    assert int(bridge_rows["is_chain_like"].max()) == 0
    assert int(bridge_rows["is_multi_community_member"].max()) == 1


def test_business_results_mark_visit_count_chain_as_suspect_chain_store() -> None:
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
                "is_chain_like": 1,
                "chain_reason": "visit_count",
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
    merchant_a = result.loc[result["storename"].eq("a")].iloc[0]
    merchant_b = result.loc[result["storename"].eq("b")].iloc[0]
    assert merchant_a["status"] == "normal"
    assert merchant_b["status"] == "suspect_chain_store"
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
    communities = (
        [
            node
            for node, node_community_id in first.items()
            if node_community_id == community_id
        ]
        for community_id in set(first.values())
    )
    assert all(
        nx.is_connected(graph.subgraph(nodes))
        for nodes in communities
    )


def test_pipeline_uses_only_transaction_edges_for_initial_communities(
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
    config = _app_config(transaction_path, output_path, 1)
    run_result = run_algorithm_one_from_transactions(
        config,
        load_transactions(config.input),
    )

    result = run_result.business_results
    communities = result.set_index("storename")["community_id"].to_dict()
    assert communities["a"] == communities["x"]
    assert communities["b"] == ""


def test_pipeline_marks_high_visit_merchant_as_suspect_chain_store(
    tmp_path: Path,
) -> None:
    transaction_path = tmp_path / "data.txt"
    rows: list[str] = []
    for user_index in range(4):
        rows.extend(
            [
                _transaction_row(
                    f"linked-{user_index}",
                    f"linked-{user_index}-chain",
                    "chain",
                    f"20260101T10{user_index:02d}00",
                    "shanghai",
                    "20260101",
                ),
                _transaction_row(
                    f"linked-{user_index}",
                    f"linked-{user_index}-a",
                    "a",
                    f"20260101T10{user_index + 10:02d}00",
                    "shanghai",
                    "20260101",
                ),
                _transaction_row(
                    f"linked-{user_index}",
                    f"linked-{user_index}-b",
                    "b",
                    f"20260101T10{user_index + 20:02d}00",
                    "shanghai",
                    "20260101",
                ),
            ]
        )
    for user_index in range(4):
        rows.append(
            _transaction_row(
                f"chain-only-{user_index}",
                f"chain-only-{user_index}",
                "chain",
                f"20260102T10{user_index:02d}00",
                "shanghai",
                "20260102",
            )
        )
    _write_transaction_file(transaction_path, rows)

    output_path = tmp_path / "output"
    base_config = _app_config(transaction_path, output_path, 2)
    config = replace(
        base_config,
        anchors=replace(
            base_config.anchors,
            chain_visit_count_quantile=1.0,
            chain_minimum_visit_count=1,
        ),
    )
    run_result = run_algorithm_one_from_transactions(
        config,
        load_transactions(config.input),
    )

    chain_row = run_result.business_results.loc[
        run_result.business_results["storename"].eq("chain")
    ].iloc[0]
    assert chain_row["status"] == "suspect_chain_store"
    assert chain_row["is_chain_like"] == 1
    assert chain_row["chain_reason"] == "visit_count"
    assert chain_row["chain_visit_count_threshold"] == 8
    assert chain_row["community_id"] != ""
    assert chain_row["is_position"] == 0


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
    config = _app_config(transaction_path, output_path, 2)
    run_result = run_algorithm_one_from_transactions(
        config,
        load_transactions(config.input),
    )
    summary = run_result.summary

    output_directory = Path(summary.output_directory)
    assert output_directory == output_path
    assert sorted(path.name for path in output_directory.iterdir()) == [
        "pair_statistics_test-city.pkl"
    ]
    intermediate_path = output_directory / "pair_statistics_test-city.pkl"
    assert intermediate_path.read_bytes()[:2] == b"\x80\x04"
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
    assert set(result["is_interfere"]) == {"N"}
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
