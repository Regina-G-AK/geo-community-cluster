from datetime import datetime
import importlib
import pickle
import sys
import types
from pathlib import Path

import networkx as nx
import pandas as pd
import pytest

from business_district.graph import PairStatistics
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
from incremental_assignment.models import AssignmentConfig


def _app_config(output_directory: Path) -> AppConfig:
    return AppConfig(
        city=CityConfig(code="shanghai", name="shanghai"),
        input=InputConfig(Path("data.txt"), ("%Y%m%dT%H%M%S",)),
        visits=VisitConfig(30, 30),
        cooccurrence=CooccurrenceConfig(1, 1.0, 1),
        graph=GraphConfig("transaction_count", 0.75, 1.0, 10, 0.0),
        community=CommunityConfig("leiden", 1.0, 42, 1, 10, 0.9),
        geo=GeoConfig(1000.0),
        anchors=AnchorConfig(1, 2, 2, 1, 0.99, 1.0, 100),
        output=OutputConfig(output_directory),
        runtime=RuntimeConfig(2),
    )


class _FakeSd:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.tables: list[pd.DataFrame] = []

    def execute_sql(self, sql: str) -> None:
        self.sql.append(sql)

    def write_table(
        self,
        dataframe: pd.DataFrame,
        temp_table_name: str,
        debug: bool,
        dt: object,
    ) -> None:
        self.tables.append(dataframe.copy())


class _FakeTaskSd:
    def __init__(
        self,
        parameter_data: pd.DataFrame,
        source_data: pd.DataFrame,
    ) -> None:
        self.parameter_data = parameter_data
        self.source_data = source_data
        self.reads: list[tuple[str, list[str]]] = []
        self.sql: list[str] = []
        self.tables: list[pd.DataFrame] = []

    def read_table(self, table_name: str, dt: list[str]) -> pd.DataFrame:
        self.reads.append((table_name, dt))
        if table_name == "param_table":
            return self.parameter_data.copy()
        if table_name == "source_table":
            if "dt" not in self.source_data.columns:
                return self.source_data.copy()
            return self.source_data.loc[self.source_data["dt"].isin(dt)].copy()
        raise AssertionError(f"unexpected table={table_name}")

    def execute_sql(self, sql: str) -> None:
        self.sql.append(sql)

    def write_table(
        self,
        dataframe: pd.DataFrame,
        temp_table_name: str,
        debug: bool,
        dt: object,
    ) -> None:
        self.tables.append(dataframe.copy())


def _install_spdbccc_data_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    spdbccc_data = types.ModuleType("spdbccc_data")
    spdbccc_data.dtDate = types.SimpleNamespace(dt_date=lambda expression: "20260101")
    spdbccc_data.formattedExc = types.SimpleNamespace(formatted_exc=lambda: None)
    spdbccc_data.loging = types.SimpleNamespace(log_data=lambda message: None)
    spdbccc_data.mountCheck = types.SimpleNamespace(mount_check=lambda: None)
    spdbccc_data.task = types.SimpleNamespace(finish_task=lambda: None)
    spdbccc_data.read_table = lambda table_name, dt: pd.DataFrame()
    spdbccc_data.write_table = lambda dataframe, table_name, debug, dt: None
    spdbccc_data.execute_sql = lambda sql: None
    monkeypatch.setitem(sys.modules, "spdbccc_data", spdbccc_data)


def _config(
    community_assignment_distance_meters: float,
    city_maximum_distance_meters: float,
) -> AssignmentConfig:
    return AssignmentConfig(
        top_k_neighbors=15,
        theta=0.55,
        delta=0.10,
        graph_weight=0.6,
        geo_weight=0.3,
        community_assignment_distance_meters=community_assignment_distance_meters,
        city_maximum_distance_meters=city_maximum_distance_meters,
    )


def test_incremental_output_uses_graph_vote_and_marks_unassigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    members = {
        "member-a": hive_task.CommunityMember(
            storename="member-a",
            community_id="1",
            is_anchor=False,
        ),
        "member-b": hive_task.CommunityMember(
            storename="member-b",
            community_id="2",
            is_anchor=False,
        ),
    }
    graph = nx.Graph()
    graph.add_edge("new-shop", "member-a", weight=1.0)
    graph.add_edge("new-shop", "member-b", weight=3.0)
    candidates = [
        hive_task.HiveCandidateMerchant(
                storename="new-shop",
                region="shanghai",
                dt="20260101",
                merchant_category=1,
            ),
        hive_task.HiveCandidateMerchant(
                storename="no-edge-shop",
                region="shanghai",
                dt="20260101",
                merchant_category=1,
        ),
    ]

    output = hive_task.build_incremental_output(
        candidates,
        graph,
        members,
        {},
        {},
        _config(3000.0, 50000.0),
        datetime.fromisoformat("2026-01-01T10:00:00"),
        1,
    )

    assert output.loc[0, "community_id"] == "2"
    assert output.loc[0, "is_abnormal"] == "1"
    assert output.loc[1, "community_id"] == ""
    assert output.loc[1, "is_abnormal"] == "3"


def test_category_two_candidate_can_join_multiple_communities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    members = {
        "member-a": hive_task.CommunityMember("member-a", "1", False),
        "member-b": hive_task.CommunityMember("member-b", "2", False),
    }
    graph = nx.Graph()
    graph.add_edge("chain-shop", "member-a", weight=1.0)
    graph.add_edge("chain-shop", "member-b", weight=3.0)

    output = hive_task.build_incremental_output(
        [
            hive_task.HiveCandidateMerchant(
                storename="chain-shop",
                region="shanghai",
                dt="20260101",
                merchant_category=2,
            )
        ],
        graph,
        members,
        {},
        {},
        _config(3000.0, 50000.0),
        datetime.fromisoformat("2026-01-01T10:00:00"),
        1,
    )

    assert output["community_id"].tolist() == ["1", "2"]
    assert output["is_position"].tolist() == [0, 0]
    assert output["is_abnormal"].tolist() == ["1", "1"]


def test_incremental_output_uses_geographic_vote_without_graph_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    members = {
        "near-member": hive_task.CommunityMember(
            storename="near-member",
            community_id="8",
            is_anchor=False,
        ),
        "far-member": hive_task.CommunityMember(
            storename="far-member",
            community_id="9",
            is_anchor=False,
        ),
    }
    candidate_coordinates = {
        "new-shop": hive_task.CoordinatePoint(
            item_id="new-shop",
            longitude=121.0001,
            latitude=31.0001,
        )
    }
    member_coordinates = {
        "near-member": hive_task.CoordinatePoint(
            item_id="near-member",
            longitude=121.0002,
            latitude=31.0002,
        ),
        "far-member": hive_task.CoordinatePoint(
            item_id="far-member",
            longitude=122.0,
            latitude=32.0,
        ),
    }

    output = hive_task.build_incremental_output(
        [
            hive_task.HiveCandidateMerchant(
                    storename="new-shop",
                    region="shanghai",
                    dt="20260101",
                    merchant_category=1,
            )
        ],
        nx.Graph(),
        members,
        candidate_coordinates,
        member_coordinates,
        _config(3000.0, 50000.0),
        datetime.fromisoformat("2026-01-01T10:00:00"),
        1,
    )

    assert output.loc[0, "community_id"] == "8"
    assert output.loc[0, "is_abnormal"] == "1"


def test_coordinate_candidate_far_from_city_skips_graph_vote_as_cross_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    members = {
        "member-a": hive_task.CommunityMember(
            storename="member-a",
            community_id="1",
            is_anchor=False,
        )
    }
    graph = nx.Graph()
    graph.add_edge("new-shop", "member-a", weight=10.0)

    output = hive_task.build_incremental_output(
        [
            hive_task.HiveCandidateMerchant(
                    storename="new-shop",
                    region="shanghai",
                    dt="20260101",
                    merchant_category=1,
            )
        ],
        graph,
        members,
        {
            "new-shop": hive_task.CoordinatePoint(
                item_id="new-shop",
                longitude=122.0,
                latitude=32.0,
            )
        },
        {
            "member-a": hive_task.CoordinatePoint(
                item_id="member-a",
                longitude=121.0,
                latitude=31.0,
            )
        },
        _config(3000.0, 50000.0),
        datetime.fromisoformat("2026-01-01T10:00:00"),
        1,
    )

    assert output.loc[0, "community_id"] == ""
    assert output.loc[0, "is_abnormal"] == "5"


def test_coordinate_candidate_outside_community_distance_skips_graph_vote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    members = {
        "member-a": hive_task.CommunityMember(
            storename="member-a",
            community_id="1",
            is_anchor=False,
        )
    }
    graph = nx.Graph()
    graph.add_edge("new-shop", "member-a", weight=10.0)

    output = hive_task.build_incremental_output(
        [
            hive_task.HiveCandidateMerchant(
                    storename="new-shop",
                    region="shanghai",
                    dt="20260101",
                    merchant_category=1,
            )
        ],
        graph,
        members,
        {
            "new-shop": hive_task.CoordinatePoint(
                item_id="new-shop",
                longitude=121.05,
                latitude=31.0,
            )
        },
        {
            "member-a": hive_task.CoordinatePoint(
                item_id="member-a",
                longitude=121.0,
                latitude=31.0,
            )
        },
        _config(3000.0, 50000.0),
        datetime.fromisoformat("2026-01-01T10:00:00"),
        1,
    )

    assert output.loc[0, "community_id"] == ""
    assert output.loc[0, "is_abnormal"] == "3"


def test_hive_task_config_uses_explicit_algorithm_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    algorithm_config = _app_config(tmp_path)
    config = hive_task.HiveTaskConfig(
        algorithm_config=algorithm_config,
        timestamp_formats=("%Y%m%dT%H%M%S",),
        visit_config=VisitConfig(30, 30),
        graph_config=GraphConfig("transaction_count", 0.75, 1.0, 10, 0.0),
        assignment_config=_config(3000.0, 50000.0),
        source_table="source_table",
        parameter_table="parameter_table",
        target_table="target_table",
        target_temp_table="temp_table",
        dt_expression="T-1",
    )

    assert config.algorithm_config is algorithm_config
    assert config.source_table == "source_table"
    assert config.parameter_table == "parameter_table"


def test_incremental_cooccurrence_config_uses_parameter_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260101",
                "end_date": "20260102",
                "region": "shanghai",
                "max_transaction_time_interval": "90",
                "transaction_time_interval_weight": "45.5",
                "min_transaction_number": "4",
                "min_merchant_count": "5",
                "is_daily": "1",
            }
        ]
    )

    parameters = hive_task.load_hive_algorithm_parameters(parameter_data, "param_table")
    config = hive_task.build_incremental_cooccurrence_config(
        parameters,
        "param_table",
    )

    assert config.window_minutes == 90
    assert config.decay_tau_minutes == 45.5
    assert config.minimum_unique_users == 4
    assert hive_task.build_source_dt_list(parameters) == ["20260101", "20260102"]


def test_merge_pair_statistics_keeps_existing_support_and_adds_visits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    base = PairStatistics(
        strengths={("a", "b"): 2.0},
        supports={("a", "b"): 5},
        merchant_visit_counts={"a": 3, "b": 2},
    )
    incremental = PairStatistics(
        strengths={("a", "b"): 1.5, ("b", "c"): 4.0},
        supports={("a", "b"): 7, ("b", "c"): 2},
        merchant_visit_counts={"b": 4, "c": 6},
    )

    result = hive_task.merge_pair_statistics(base, incremental)

    assert result.strengths == {("a", "b"): 3.5, ("b", "c"): 4.0}
    assert result.supports == {("a", "b"): 5, ("b", "c"): 2}
    assert result.merchant_visit_counts == {"a": 3, "b": 6, "c": 6}


def test_source_community_state_skips_multi_community_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    transactions = pd.DataFrame(
        [
            {hive_task.MERCHANT: "stable", "business_district": "1"},
            {hive_task.MERCHANT: "multi", "business_district": "1"},
            {hive_task.MERCHANT: "multi", "business_district": "2"},
            {hive_task.MERCHANT: "new", "business_district": ""},
        ]
    )

    state = hive_task.build_source_community_state(transactions, "source_table")

    assert set(state.existing_storenames) == {"stable", "multi"}
    assert sorted(state.members) == ["stable"]
    assert state.members["stable"].community_id == "1"
    assert state.skipped_multi_community_storenames == frozenset({"multi"})


def test_taskrun_reads_source_partitions_from_parameter_table(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260101",
                "end_date": "20260102",
                "region": "shanghai",
                "max_transaction_time_interval": "90",
                "transaction_time_interval_weight": "45",
                "min_transaction_number": "1",
                "min_merchant_count": "3",
                "is_daily": "1",
            }
        ]
    )
    source_data = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "old-shop",
                "merchant_category": "1",
                "transaction_time": "20260101T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "N",
                "business_district": "D001",
            },
            {
                "account_number": "u1",
                "global_flow_number": "f2",
                "storename": "new-shop",
                "merchant_category": "1",
                "transaction_time": "20260101T100500",
                "pos_longitude": "121.0001",
                "pos_latitude": "31.0001",
                "region": "shanghai",
                "is_interfere": "N",
                "business_district": "",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f3",
                "storename": "other-shop",
                "merchant_category": "1",
                "transaction_time": "20260103T100000",
                "pos_longitude": "121.2",
                "pos_latitude": "31.2",
                "region": "shanghai",
                "is_interfere": "N",
                "business_district": "",
            },
        ]
    )
    fake_sd = _FakeTaskSd(parameter_data, source_data)
    monkeypatch.setattr(hive_task, "sd", fake_sd)
    output_directory = tmp_path / "algorithm_one_output"
    output_directory.mkdir()
    with (output_directory / "pair_statistics_shanghai.pkl").open("wb") as file:
        pickle.dump(
            PairStatistics(
                strengths={},
                supports={},
                merchant_visit_counts={},
            ),
            file,
        )
    config = hive_task.HiveTaskConfig(
        algorithm_config=_app_config(output_directory),
        timestamp_formats=("%Y%m%dT%H%M%S",),
        visit_config=VisitConfig(
            merge_window_minutes=30,
            maximum_daily_merchants_per_card=30,
        ),
        graph_config=GraphConfig(
            edge_weight_method="transaction_count",
            context_smoothing_alpha=0.75,
            sppmi_shift=3.0,
            top_k_neighbors=10,
            minimum_z_score=0.0,
        ),
        assignment_config=_config(3000.0, 50000.0),
        source_table="source_table",
        parameter_table="param_table",
        target_table="target_table",
        target_temp_table="temp_table",
        dt_expression="T-1",
    )

    summary = hive_task.TaskMain(config).taskrun()

    assert ("param_table", ["20260101"]) in fake_sd.reads
    assert ("source_table", ["20260101", "20260102"]) in fake_sd.reads
    assert summary.source_rows == 2
    assert summary.community_rows == 1
    assert summary.inserted_rows == 1
    assert fake_sd.tables[0].loc[0, "storename"] == "new-shop"
    assert fake_sd.tables[0].loc[0, "community_id"] == "D001"
    with (output_directory / "pair_statistics_shanghai.pkl").open("rb") as file:
        statistics = pickle.load(file)
    assert statistics.supports[("new-shop", "old-shop")] == 1
    assert statistics.merchant_visit_counts == {"new-shop": 1, "old-shop": 1}


def test_insert_new_target_rows_uses_insert_into(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    fake_sd = _FakeSd()
    output = pd.DataFrame(
        [
            {
                "storename": "new-shop",
                "community_id": "1",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "is_abnormal": "1",
                "is_position": 0,
                "dt": "20260101",
            }
        ]
    )

    hive_task.insert_new_target_rows(fake_sd, output, "target_table", "temp_table")

    joined_sql = "\n".join(fake_sd.sql).lower()
    assert "insert into table target_table" in joined_sql
    assert "insert overwrite" not in joined_sql
    assert "left join target_table target" not in joined_sql


def test_load_incremental_transactions_keeps_first_duplicate_flow_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "late",
                "merchant_category": "1",
                "transaction_time": "20260102T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "N",
                "dt": "20260102",
                "business_district": "",
            },
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "early",
                "merchant_category": "1",
                "transaction_time": "20260101T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "N",
                "dt": "20260101",
                "business_district": "",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "normal",
                "merchant_category": "1",
                "transaction_time": "20260102T110000",
                "pos_longitude": "121.1",
                "pos_latitude": "31.1",
                "region": "shanghai",
                "is_interfere": "N",
                "dt": "20260102",
                "business_district": "",
            },
        ]
    )

    transactions = hive_task.load_incremental_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260101",
        "source_table",
    )

    assert transactions[hive_task.MERCHANT].tolist() == ["early", "normal"]
    assert transactions[hive_task.DT].tolist() == ["20260101", "20260102"]


def test_load_incremental_transactions_fills_missing_hive_partition_dt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "normal",
                "merchant_category": "1",
                "transaction_time": "20260101T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "N",
                "business_district": "D001",
            }
        ]
    )

    transactions = hive_task.load_incremental_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260101",
        "source_table",
    )

    assert transactions[hive_task.DT].tolist() == ["20260101"]


def test_load_incremental_transactions_skips_interfered_merchants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "interfered",
                "merchant_category": "1",
                "transaction_time": "20260101T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "Y",
                "dt": "20260101",
                "business_district": "",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "normal",
                "merchant_category": "1",
                "transaction_time": "20260101T110000",
                "pos_longitude": "121.1",
                "pos_latitude": "31.1",
                "region": "shanghai",
                "is_interfere": "N",
                "dt": "20260101",
                "business_district": "",
            },
        ]
    )

    transactions = hive_task.load_incremental_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260101",
        "source_table",
    )

    assert transactions[hive_task.MERCHANT].tolist() == ["normal"]
