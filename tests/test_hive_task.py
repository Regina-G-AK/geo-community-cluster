import importlib
from pathlib import Path
import sys
import types

import pandas as pd
import pytest

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


def _app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        city=CityConfig(code="test-city", name="测试市"),
        input=InputConfig(
            transactions_path=tmp_path / "data.txt",
            timestamp_formats=("%Y%m%dT%H%M%S",),
        ),
        visits=VisitConfig(30, 30),
        cooccurrence=CooccurrenceConfig(1, 1.0, 1),
        graph=GraphConfig("transaction_count", 0.75, 1.0, 5, 0.0),
        community=CommunityConfig("leiden", 1.0, 42, 2, 10, 0.9),
        geo=GeoConfig(1000.0),
        anchors=AnchorConfig(1, 2, 2, 1, 0.99, 1.0, 100),
        output=OutputConfig(tmp_path / "output"),
        runtime=RuntimeConfig(2),
    )


def _install_spdbccc_data_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    spdbccc_data = types.ModuleType("spdbccc_data")
    spdbccc_data.dtDate = types.SimpleNamespace(dt_date=lambda expression: "20260101")
    spdbccc_data.formattedExc = types.SimpleNamespace(formatted_exc=lambda: None)
    spdbccc_data.loging = types.SimpleNamespace(log_data=lambda message: None)
    spdbccc_data.mountCheck = types.SimpleNamespace(mount_check=lambda: None)
    spdbccc_data.task = types.SimpleNamespace(finish_task=lambda: None)
    spdbccc_data.read_table = lambda table_name, dt: pd.DataFrame()
    monkeypatch.setitem(sys.modules, "spdbccc_data", spdbccc_data)


def test_read_partitioned_hive_table_reads_part_files_and_adds_dt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    monkeypatch.setattr(hive_task, "HIVE_TABLE_ROOT", tmp_path)
    first_partition = tmp_path / "input_table" / "dt=20260101"
    second_partition = tmp_path / "input_table" / "dt=20260102"
    first_partition.mkdir(parents=True)
    second_partition.mkdir(parents=True)
    pd.DataFrame([{"storename": "a"}]).to_parquet(
        first_partition / "part-000.parquet",
        index=False,
    )
    pd.DataFrame([{"storename": "b"}]).to_parquet(
        first_partition / "part-001.parquet",
        index=False,
    )
    pd.DataFrame([{"storename": "c"}]).to_parquet(
        second_partition / "part-000.parquet",
        index=False,
    )

    result = hive_task.read_partitioned_hive_table(
        "dev_icamp.input_table",
        ["20260101", "20260102"],
    )

    assert result.to_dict(orient="records") == [
        {"storename": "a", "dt": "20260101"},
        {"storename": "b", "dt": "20260101"},
        {"storename": "c", "dt": "20260102"},
    ]


def test_hive_target_output_formats_status_as_dict_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    business_results = pd.DataFrame(
        [
            {
                "storename": "a",
                "community_id": 1,
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": 0,
                "update_time": "2026-01-01 10:00:00",
                "status": "active",
                "is_position": 1,
                "dt": "20260101",
            },
            {
                "storename": "b",
                "community_id": "",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": 0,
                "update_time": "2026-01-01 10:00:00",
                "status": "suspect_online",
                "is_position": 0,
                "dt": "20260101",
            },
            {
                "storename": "c",
                "community_id": 2,
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": 0,
                "update_time": "2026-01-01 10:00:00",
                "status": "suspect_chain_store",
                "is_position": 0,
                "dt": "20260101",
            },
        ]
    )

    output = hive_task.build_hive_target_output(business_results)

    assert output["is_abnormal"].tolist() == ["1", "2", "6"]
    assert output["is_interfere"].tolist() == ["N", "N", "N"]


def test_overwrite_target_table_replaces_current_regions_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    sql_statements: list[str] = []
    written_tables: list[tuple[pd.DataFrame, str]] = []

    def execute_sql(sql: str) -> None:
        sql_statements.append(sql)

    def write_table(
        dataframe: pd.DataFrame,
        table_name: str,
        debug: bool,
        dt: None,
    ) -> None:
        assert debug is False
        assert dt is None
        written_tables.append((dataframe.copy(), table_name))

    fake_sd = types.SimpleNamespace(
        execute_sql=execute_sql,
        write_table=write_table,
    )
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

    hive_task.overwrite_target_table(
        fake_sd,
        output,
        "target_table",
        "temp_table",
    )

    joined_sql = " ".join(" ".join(sql.split()) for sql in sql_statements).lower()
    assert len(written_tables) == 1
    assert written_tables[0][1] == "temp_table"
    assert written_tables[0][0]["region"].tolist() == ["shanghai"]
    assert "create table temp_table_merged as" in joined_sql
    assert "from target_table target" in joined_sql
    assert "select distinct region from temp_table" in joined_sql
    assert "target.region = source_regions.region" in joined_sql
    assert "source_regions.region is null" in joined_sql
    assert "union all" in joined_sql
    assert "insert overwrite table target_table partition (dt=20260101)" in joined_sql
    assert "from temp_table_merged" in joined_sql


def test_status_name_formats_as_dict_code() -> None:
    status_codes = importlib.import_module("business_district.status_codes")

    assert status_codes.format_status_code("active") == "1"
    assert status_codes.format_status_code("normal") == "1"
    assert status_codes.format_status_code("suspect_online") == "2"
    assert status_codes.format_status_code("suspect_isolated") == "3"
    assert status_codes.format_status_code("suspect_lost") == "4"
    assert status_codes.format_status_code("suspect_cross_region") == "5"
    assert status_codes.format_status_code("suspect_chain_store") == "6"
    assert status_codes.format_status_code("deleted") == "7"


def test_taskrun_reads_parameter_table_with_standard_reader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260101",
                "end_date": "20260101",
                "region": "shanghai",
                "max_transaction_time_interval": "90",
                "transaction_time_interval_weight": "45",
                "min_transaction_number": "1",
                "min_merchant_count": "3",
                "is_daily": "0",
            }
        ]
    )
    source_data = pd.DataFrame(
        [
            {
                "storename": "shop-a",
                "transaction_time": "20260101T100000",
                "region": "shanghai",
            }
        ]
    )
    reads: list[tuple[str, list[str]]] = []
    partition_reads: list[tuple[str, list[str]]] = []

    def read_table(table_name: str, dt: list[str]) -> pd.DataFrame:
        reads.append((table_name, dt))
        if table_name == "param_table":
            return parameter_data.copy()
        raise AssertionError(f"unexpected table={table_name}")

    def read_partitioned_hive_table(
        table_name: str,
        dt_values: list[str],
    ) -> pd.DataFrame:
        partition_reads.append((table_name, dt_values))
        if table_name == "source_table":
            return source_data.copy()
        raise AssertionError(f"parameter table must use sd.read_table: {table_name}")

    config = types.SimpleNamespace(
        input=types.SimpleNamespace(timestamp_formats=("%Y%m%dT%H%M%S",))
    )
    business_results = pd.DataFrame(
        [
            {
                "storename": "shop-a",
                "community_id": 1,
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": 0,
                "update_time": "2026-01-01 10:00:00",
                "status": "active",
                "is_position": 1,
                "dt": "20260101",
            }
        ]
    )
    run_result = types.SimpleNamespace(
        business_results=business_results,
        summary=types.SimpleNamespace(output_directory="out"),
    )
    monkeypatch.setattr(hive_task, "sd", types.SimpleNamespace(read_table=read_table))
    monkeypatch.setattr(
        hive_task,
        "read_partitioned_hive_table",
        read_partitioned_hive_table,
    )
    monkeypatch.setattr(
        hive_task,
        "apply_runtime_parameters",
        lambda algorithm_config, parameters: config,
    )
    monkeypatch.setattr(
        hive_task,
        "load_hive_transactions",
        lambda source, timestamp_formats, dt_value, table_name: source,
    )
    monkeypatch.setattr(
        hive_task,
        "run_algorithm_one_from_transactions",
        lambda config, transactions, source_label, source_detail: run_result,
    )
    monkeypatch.setattr(
        hive_task,
        "overwrite_target_table",
        lambda sd, result, table_name, temp_table_name: None,
    )

    task_config = hive_task.HiveTaskConfig(
        algorithm_config=_app_config(tmp_path),
        source_table="source_table",
        parameter_table="param_table",
        target_table="target_table",
        target_temp_table="temp_table",
        dt_expression="T-1",
    )
    summary = hive_task.TaskMain(task_config).taskrun()

    assert reads == [("param_table", ["20260101"])]
    assert partition_reads == [("source_table", ["20260101"])]
    assert summary.input_rows == 1


def test_hive_parameters_override_notebook_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260101",
                "end_date": "20260103",
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
    runtime_config = hive_task.build_runtime_config(parameters, "param_table")
    config = hive_task.apply_runtime_parameters(
        _app_config(tmp_path),
        runtime_config,
    )

    assert config.cooccurrence.window_minutes == 90
    assert config.cooccurrence.decay_tau_minutes == 45.5
    assert config.cooccurrence.minimum_unique_users == 4
    assert config.anchors.minimum_community_size == 5


def test_filter_source_data_by_parameters_uses_region_and_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260101",
                "end_date": "20260102",
                "region": "shanghai",
                "max_transaction_time_interval": "120",
                "transaction_time_interval_weight": "60",
                "min_transaction_number": "3",
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
                "storename": "in-range",
                "transaction_time": "20260102T100000",
                "region": "shanghai",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "wrong-region",
                "transaction_time": "20260102T100000",
                "region": "beijing",
            },
            {
                "account_number": "u3",
                "global_flow_number": "f3",
                "storename": "out-of-range",
                "transaction_time": "20260103T000000",
                "region": "shanghai",
            },
        ]
    )

    parameters = hive_task.load_hive_algorithm_parameters(parameter_data, "param_table")
    filtered = hive_task.filter_source_data_by_parameters(
        source_data,
        parameters,
        ("%Y%m%dT%H%M%S",),
        "source_table",
        "param_table",
    )

    assert filtered["storename"].tolist() == ["in-range"]
    assert hive_task.build_source_dt_list(parameters) == ["20260101", "20260102"]


def test_hive_parameters_reject_non_numeric_is_daily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260101",
                "end_date": "20260101",
                "region": "shanghai",
                "max_transaction_time_interval": "120",
                "transaction_time_interval_weight": "60",
                "min_transaction_number": "3",
                "min_merchant_count": "3",
                "is_daily": "true",
            }
        ]
    )

    with pytest.raises(hive_task.TransactionDataError, match="0 或 1"):
        hive_task.load_hive_algorithm_parameters(parameter_data, "param_table")
