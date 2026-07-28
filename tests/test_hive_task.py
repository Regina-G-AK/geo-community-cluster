from __future__ import annotations

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
        community=CommunityConfig("leiden", 1.0, 42, 10),
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


def test_read_partitioned_hive_table_prepares_partition_before_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    monkeypatch.setattr(hive_task, "HIVE_TABLE_ROOT", tmp_path)
    read_calls: list[tuple[str, list[str]]] = []

    def read_table(table_name: str, dt: list[str]) -> pd.DataFrame:
        read_calls.append((table_name, dt))
        partition = tmp_path / "input_table" / f"dt={dt[0]}"
        partition.mkdir(parents=True)
        pd.DataFrame([{"storename": "mounted"}]).to_parquet(
            partition / "part-000.parquet",
            index=False,
        )
        return pd.DataFrame()

    monkeypatch.setattr(hive_task.sd, "read_table", read_table)

    result = hive_task.read_partitioned_hive_table(
        "dev_icamp.input_table",
        ["20260101"],
    )

    assert read_calls == [
        ("dev_icamp.input_table", ["20260101"]),
    ]
    assert result.to_dict(orient="records") == [
        {"storename": "mounted", "dt": "20260101"},
    ]


def test_read_partitioned_hive_table_skips_missing_and_empty_partitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    monkeypatch.setattr(hive_task, "HIVE_TABLE_ROOT", tmp_path)
    empty_partition = tmp_path / "input_table" / "dt=20260102"
    valid_partition = tmp_path / "input_table" / "dt=20260103"
    empty_partition.mkdir(parents=True)
    valid_partition.mkdir(parents=True)
    pd.DataFrame(columns=["storename"]).to_parquet(
        empty_partition / "part-000.parquet",
        index=False,
    )
    pd.DataFrame(
        [{"storename": "available", "dt": pd.Timestamp("1999-01-01")}]
    ).to_parquet(
        valid_partition / "part-000.parquet",
        index=False,
    )

    result = hive_task.read_partitioned_hive_table(
        "dev_icamp.input_table",
        ["20260101", "20260102", "20260103"],
    )

    assert result.to_dict(orient="records") == [
        {"storename": "available", "dt": "20260103"},
    ]


def test_read_partitioned_hive_table_rejects_all_empty_partitions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    monkeypatch.setattr(hive_task, "HIVE_TABLE_ROOT", tmp_path)
    empty_partition = tmp_path / "input_table" / "dt=20260102"
    empty_partition.mkdir(parents=True)
    pd.DataFrame(columns=["storename"]).to_parquet(
        empty_partition / "part-000.parquet",
        index=False,
    )

    with pytest.raises(
        hive_task.TransactionDataError,
        match="日期范围内没有非空分区",
    ):
        hive_task.read_partitioned_hive_table(
            "dev_icamp.input_table",
            ["20260101", "20260102"],
        )


def test_read_filtered_source_hive_table_filters_each_part_before_concat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    monkeypatch.setattr(hive_task, "HIVE_TABLE_ROOT", tmp_path)
    partition = tmp_path / "input_table" / "dt=20260131"
    partition.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "storename": "first-match",
                "transaction_time": "20260102T100000",
                "region": "shanghai",
            },
            {
                "storename": "wrong-region",
                "transaction_time": "20260102T100000",
                "region": "beijing",
            },
        ]
    ).to_parquet(partition / "part-000.parquet", index=False)
    pd.DataFrame(
        [
            {
                "storename": "second-match",
                "transaction_time": "20260103T100000",
                "region": "shanghai",
            },
            {
                "storename": "out-of-range",
                "transaction_time": "20260104T000000",
                "region": "shanghai",
            },
        ]
    ).to_parquet(partition / "part-001.parquet", index=False)
    parameters = [
        hive_task.HiveAlgorithmParameter(
            start_date=pd.Timestamp("2026-01-02"),
            end_date=pd.Timestamp("2026-01-03"),
            end_exclusive=pd.Timestamp("2026-01-04"),
            region="shanghai",
            max_transaction_time_interval=30,
            min_transaction_number=2,
            min_merchant_count=3,
            is_daily=False,
        )
    ]
    original_concat = hive_task.pd.concat
    concat_storenames: list[list[str]] = []

    def concat_filtered_parts(
        dataframes: list[pd.DataFrame],
        ignore_index: bool,
        copy: bool,
    ) -> pd.DataFrame:
        concat_storenames.extend(
            dataframe["storename"].tolist() for dataframe in dataframes
        )
        return original_concat(
            dataframes,
            ignore_index=ignore_index,
            copy=copy,
        )

    monkeypatch.setattr(hive_task.pd, "concat", concat_filtered_parts)

    result = hive_task.read_filtered_source_hive_table(
        "dev_icamp.input_table",
        ["20260131"],
        parameters,
        "param_table",
    )

    assert concat_storenames == [
        ["first-match"],
        ["second-match", "out-of-range"],
    ]
    assert result["storename"].tolist() == [
        "first-match",
        "second-match",
        "out-of-range",
    ]
    assert result["dt"].tolist() == ["20260131", "20260131", "20260131"]


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

    output = hive_task.build_hive_target_output(
        business_results,
        "20260102",
        1,
    )

    assert output["is_abnormal"].tolist() == ["1", "2", "6"]
    assert output["is_interfere"].tolist() == ["N", "N", "N"]
    assert output["dt"].tolist() == ["20260102", "20260102", "20260102"]
    assert output.columns.tolist() == hive_task.TARGET_COLUMNS
    assert output.columns.get_loc("update_time") < output.columns.get_loc("is_abnormal")


def test_build_hive_target_output_clears_small_community_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    business_results = pd.DataFrame(
        [
            {
                "storename": "small-shop",
                "community_id": 1,
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "status": "normal",
                "is_position": 1,
                "dt": "20260101",
            },
            {
                "storename": "large-shop-a",
                "community_id": 2,
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "status": "normal",
                "is_position": 1,
                "dt": "20260101",
            },
            {
                "storename": "large-shop-b",
                "community_id": 2,
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "status": "normal",
                "is_position": 0,
                "dt": "20260101",
            },
        ]
    )

    output = hive_task.build_hive_target_output(
        business_results,
        "20260102",
        2,
    )

    small_row = output.loc[output["storename"].eq("small-shop")].iloc[0]
    large_rows = output.loc[output["community_id"].eq("2")]
    assert small_row["community_id"] == ""
    assert small_row["is_abnormal"] == "3"
    assert small_row["is_position"] == 0
    assert large_rows["storename"].tolist() == ["large-shop-a", "large-shop-b"]


def test_overwrite_target_table_overwrites_partition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    sql_statements: list[str] = []

    fake_sd = types.SimpleNamespace(
        execute_sql=lambda sql: sql_statements.append(sql),
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
                "dt": "20260102",
            }
        ]
    )

    hive_task.overwrite_target_table(
        fake_sd,
        output,
        "target_table",
        "20260102",
    )

    joined_sql = " ".join(sql_statements[0].split()).lower()
    assert len(sql_statements) == 1
    assert joined_sql.startswith("insert overwrite table target_table")
    assert "partition (dt='20260102')" in joined_sql
    assert (
        "select 'new-shop', '1', '', 'shanghai', 'n', "
        "'2026-01-01 10:00:00', '1', 0"
    ) in joined_sql


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
    writes: list[tuple[str, list[str]]] = []

    def read_table(table_name: str, dt: list[str]) -> pd.DataFrame:
        reads.append((table_name, dt))
        if table_name == "param_table":
            return parameter_data.copy()
        raise AssertionError(f"unexpected table={table_name}")

    def read_filtered_source_hive_table(
        table_name: str,
        dt_values: list[str],
        parameters: list[object],
        parameter_table: str,
    ) -> pd.DataFrame:
        partition_reads.append((table_name, dt_values))
        assert len(parameters) == 1
        assert parameter_table == "param_table"
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

    def overwrite_target_table(
        sd: types.SimpleNamespace,
        result: pd.DataFrame,
        table_name: str,
        output_dt: str,
    ) -> None:
        writes.append((output_dt, result["dt"].tolist()))

    monkeypatch.setattr(hive_task, "sd", types.SimpleNamespace(read_table=read_table))
    monkeypatch.setattr(
        hive_task,
        "read_filtered_source_hive_table",
        read_filtered_source_hive_table,
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
        overwrite_target_table,
    )

    task_config = hive_task.HiveTaskConfig(
        algorithm_config=_app_config(tmp_path),
        source_table="source_table",
        parameter_table="param_table",
        target_table="target_table",
        dt_expression="T-1",
    )
    summary = hive_task.TaskMain(task_config).taskrun()

    assert reads == [("param_table", ["20260101"])]
    assert partition_reads == [("source_table", ["20260101"])]
    assert writes == [("20260101", ["20260101"])]
    assert summary.input_rows == 1


def test_hive_parameters_preserve_notebook_decay_tau(
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
                "min_transaction_number": "4",
                "min_merchant_count": "5",
                "is_daily": "1",
            }
        ]
    )

    parameters = hive_task.load_hive_algorithm_parameters(parameter_data, "param_table")
    notebook_config = _app_config(tmp_path)
    runtime_config = hive_task.build_runtime_config(
        parameters,
        "param_table",
        notebook_config.cooccurrence.decay_tau_minutes,
    )
    config = hive_task.apply_runtime_parameters(
        notebook_config,
        runtime_config,
    )

    assert config.cooccurrence.window_minutes == 90
    assert config.cooccurrence.decay_tau_minutes == 1.0
    assert config.cooccurrence.minimum_unique_users == 4
    assert config.anchors.minimum_community_size == 5


def test_filter_source_data_by_parameters_uses_region_only(
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
        "source_table",
        "param_table",
    )

    assert filtered["storename"].tolist() == ["in-range", "out-of-range"]
    assert hive_task.build_source_dt_list(parameters) == ["20260131"]


def test_build_source_dt_list_uses_each_month_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260115",
                "end_date": "20260302",
                "region": "shanghai",
                "max_transaction_time_interval": "120",
                "min_transaction_number": "3",
                "min_merchant_count": "3",
                "is_daily": "0",
            }
        ]
    )

    parameters = hive_task.load_hive_algorithm_parameters(
        parameter_data,
        "param_table",
    )

    assert hive_task.build_source_dt_list(parameters) == [
        "20260131",
        "20260228",
        "20260331",
    ]


def test_build_source_dt_list_uses_exact_single_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260115",
                "end_date": "20260115",
                "region": "shanghai",
                "max_transaction_time_interval": "120",
                "min_transaction_number": "3",
                "min_merchant_count": "3",
                "is_daily": "0",
            }
        ]
    )

    parameters = hive_task.load_hive_algorithm_parameters(
        parameter_data,
        "param_table",
    )

    assert hive_task.build_source_dt_list(parameters) == ["20260115"]


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
                "min_transaction_number": "3",
                "min_merchant_count": "3",
                "is_daily": "true",
            }
        ]
    )

    with pytest.raises(hive_task.TransactionDataError, match="0 或 1"):
        hive_task.load_hive_algorithm_parameters(parameter_data, "param_table")
