from datetime import datetime
import importlib
import sys
import types

import networkx as nx
import pandas as pd
import pytest

from business_district.config import GraphConfig, VisitConfig
from incremental_assignment.models import AssignmentConfig


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
        community_data: pd.DataFrame,
    ) -> None:
        self.parameter_data = parameter_data
        self.source_data = source_data
        self.community_data = community_data
        self.reads: list[tuple[str, list[str]]] = []
        self.sql: list[str] = []
        self.tables: list[pd.DataFrame] = []

    def read_table(self, table_name: str, dt: list[str]) -> pd.DataFrame:
        self.reads.append((table_name, dt))
        if table_name == "param_table":
            return self.parameter_data.copy()
        if table_name == "source_table":
            return self.source_data.loc[self.source_data["dt"].isin(dt)].copy()
        if table_name == "community_table":
            return self.community_data.copy()
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
        min_observation_days=28,
        min_unique_users=5,
        top_k_neighbors=15,
        anchor_vote_weight=1.5,
        theta=0.55,
        delta=0.10,
        graph_weight=0.6,
        geo_weight=0.3,
        customer_weight=0.1,
        minimum_sigma_meters=100.0,
        community_assignment_distance_meters=community_assignment_distance_meters,
        city_maximum_distance_meters=city_maximum_distance_meters,
    )


def test_incremental_output_uses_graph_vote_and_marks_unassigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    community_data = pd.DataFrame(
        [
            {
                "storename": "member-a",
                "community_id": "1",
                "is_position": "1",
                "is_abnormal": "normal",
            },
            {
                "storename": "member-b",
                "community_id": "2",
                "is_position": "1",
                "is_abnormal": "normal",
            },
        ]
    )
    members = hive_task.build_community_members(community_data, "community_table")
    graph = nx.Graph()
    graph.add_edge("new-shop", "member-a", weight=1.0)
    graph.add_edge("new-shop", "member-b", weight=3.0)
    candidates = [
        hive_task.HiveCandidateMerchant(
            storename="new-shop",
            region="shanghai",
            dt="20260101",
        ),
        hive_task.HiveCandidateMerchant(
            storename="no-edge-shop",
            region="shanghai",
            dt="20260101",
        ),
    ]

    output = hive_task.build_incremental_output(
        candidates,
        graph,
        members,
        _config(3000.0, 50000.0),
        datetime.fromisoformat("2026-01-01T10:00:00"),
    )

    assert output.loc[0, "community_id"] == "2"
    assert output.loc[0, "is_abnormal"] == "normal"
    assert output.loc[1, "community_id"] == ""
    assert output.loc[1, "is_abnormal"] == "suspect_isolated"


def test_default_hive_task_config_uses_declared_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")

    config = hive_task.build_default_hive_task_config()

    assert config.source_table == "dev_icamp.icamp_merchant_cluster_algo_input"
    assert config.parameter_table == "dev_icamp.icamp_merchant_cluster_algo_param"
    assert config.community_table == "dev_icamp.icamp_schedule_pufa_commercial_district"
    assert config.target_table == "dev_icamp.icamp_merchant_cluster_algo_output"
    assert config.timestamp_formats == (
        "%Y%m%dT%H%M%S",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    )
    assert config.graph_config.top_k_neighbors == 10
    assert config.assignment_config.theta == 0.55
    assert not hasattr(config, "cooccurrence_config")
    assert not hasattr(config, "algorithm_config_path")
    assert not hasattr(config, "assignment_config_path")


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


def test_taskrun_reads_source_partitions_from_parameter_table(
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
                "transaction_time": "20260101T100000",
                "region": "shanghai",
                "dt": "20260101",
            },
            {
                "account_number": "u1",
                "global_flow_number": "f2",
                "storename": "new-shop",
                "transaction_time": "20260101T100500",
                "region": "shanghai",
                "dt": "20260101",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f3",
                "storename": "other-shop",
                "transaction_time": "20260103T100000",
                "region": "shanghai",
                "dt": "20260103",
            },
        ]
    )
    community_data = pd.DataFrame(
        [
            {
                "district_id": "D001",
                "district_name": "old-shop",
                "status": "正常",
            }
        ]
    )
    fake_sd = _FakeTaskSd(parameter_data, source_data, community_data)
    monkeypatch.setattr(hive_task, "sd", fake_sd)
    config = hive_task.HiveTaskConfig(
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
        community_table="community_table",
        target_table="target_table",
        target_temp_table="temp_table",
        dt_expression="T-1",
    )

    summary = hive_task.TaskMain(config).taskrun()

    assert ("param_table", ["20260101"]) in fake_sd.reads
    assert ("source_table", ["20260101", "20260102"]) in fake_sd.reads
    assert ("community_table", ["20260101"]) in fake_sd.reads
    assert summary.source_rows == 2
    assert summary.inserted_rows == 1
    assert fake_sd.tables[0].loc[0, "storename"] == "new-shop"
    assert fake_sd.tables[0].loc[0, "community_id"] == "D001"


def test_build_community_members_accepts_district_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("incremental_assignment.hive_task")
    community_data = pd.DataFrame(
        [
            {
                "district_id": "D001",
                "district_name": "district-a",
                "status": "正常",
            },
            {
                "district_id": "D002",
                "district_name": "district-b",
                "status": "停用",
            },
        ]
    )

    members = hive_task.build_community_members(community_data, "district_table")

    assert sorted(members) == ["district-a"]
    assert members["district-a"].community_id == "D001"
    assert members["district-a"].is_anchor is True


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
                "is_interfere": 0,
                "update_time": "2026-01-01 10:00:00",
                "is_abnormal": "normal",
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
                "transaction_time": "20260102T100000",
                "region": "shanghai",
                "dt": "20260102",
            },
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "early",
                "transaction_time": "20260101T100000",
                "region": "shanghai",
                "dt": "20260101",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "normal",
                "transaction_time": "20260102T110000",
                "region": "shanghai",
                "dt": "20260102",
            },
        ]
    )

    transactions = hive_task.load_incremental_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "source_table",
    )

    assert transactions[hive_task.MERCHANT].tolist() == ["early", "normal"]
    assert transactions[hive_task.DT].tolist() == ["20260101", "20260102"]
