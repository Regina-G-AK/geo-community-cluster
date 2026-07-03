from datetime import datetime
import importlib
import sys
import types

import networkx as nx
import pandas as pd
import pytest

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
    assert "left join target_table target" in joined_sql


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
