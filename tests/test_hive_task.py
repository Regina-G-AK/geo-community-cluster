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
from incremental_assignment.models import AssignmentConfig


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
        geo=GeoConfig(1000.0, 100),
        anchors=AnchorConfig(1, 2, 2, 1, 0.99, 1.0, 100),
        output=OutputConfig(tmp_path / "output"),
        runtime=RuntimeConfig(2),
    )


def _assignment_config() -> AssignmentConfig:
    return AssignmentConfig(
        community_assignment_distance_meters=3000.0,
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


def test_load_hive_transactions_keeps_business_district_as_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    transactions = importlib.import_module("business_district.transactions")
    source = pd.DataFrame(
        [
            {
                "account_number": "u1",
                "global_flow_number": "f1",
                "storename": "old-shop",
                "transaction_time": "20260101T100000",
                "pos_longitude": "121.0",
                "pos_latitude": "31.0",
                "region": "shanghai",
                "is_interfere": "N",
                "is_abnormal": "1",
                "business_district": "BD001",
                "merchant_category": "1",
                "dt": "20260101",
            }
        ]
    )

    result = transactions.load_hive_transactions(
        source,
        ("%Y%m%dT%H%M%S",),
        "20260101",
        "source_table",
    )

    assert result.loc[0, "business_district"] == "BD001"
    assert str(result["business_district"].dtype) == "string"
    assert result.columns.tolist()[:11] == [
        "card_id",
        "global_flow_number",
        "merchant_id",
        "merchant_category",
        "timestamp",
        "pos_longitude",
        "pos_latitude",
        "region",
        "is_interfere",
        "is_abnormal",
        "business_district",
    ]


def test_load_existing_community_ids_preserves_alphanumeric_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    transactions = pd.DataFrame(
        [
            {"merchant_id": "old-shop", "business_district": "BD001"},
            {"merchant_id": "old-shop", "business_district": "BD001"},
            {"merchant_id": "new-shop", "business_district": ""},
        ]
    )

    result = hive_task.load_existing_community_ids(
        transactions,
        "source_table",
    )

    assert result == {"old-shop": "BD001"}


def test_load_existing_community_ids_accepts_existing_id_without_format_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    transactions = pd.DataFrame(
        [{"merchant_id": "old-shop", "business_district": " 商圈-A/001 "}]
    )

    result = hive_task.load_existing_community_ids(
        transactions,
        "source_table",
    )

    assert result == {"old-shop": "商圈-A/001"}


def test_merge_initial_assignment_prioritizes_existing_communities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    clustered_output = pd.DataFrame(
        [
            {
                "storename": "old-shop",
                "community_id": "0",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "is_abnormal": "1",
                "is_position": 1,
                "dt": "20260101",
            },
            {
                "storename": "assigned-shop",
                "community_id": "0",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "is_abnormal": "1",
                "is_position": 1,
                "dt": "20260101",
            },
            {
                "storename": "new-shop-a",
                "community_id": "1",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "is_abnormal": "1",
                "is_position": 1,
                "dt": "20260101",
            },
            {
                "storename": "new-shop-b",
                "community_id": "1",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "is_abnormal": "1",
                "is_position": 0,
                "dt": "20260101",
            },
            {
                "storename": "new-shop-c",
                "community_id": "1",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:00:00",
                "is_abnormal": "1",
                "is_position": 0,
                "dt": "20260101",
            },
        ],
        columns=hive_task.TARGET_COLUMNS,
    )
    assignment_output = pd.DataFrame(
        [
            {
                "storename": "assigned-shop",
                "community_id": "BD001",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:01:00",
                "is_abnormal": "1",
                "is_position": 0,
                "dt": "20260101",
            },
            {
                "storename": "new-shop-a",
                "community_id": "",
                "previous_community_id": "",
                "region": "shanghai",
                "is_interfere": "N",
                "update_time": "2026-01-01 10:01:00",
                "is_abnormal": "3",
                "is_position": 0,
                "dt": "20260101",
            },
        ],
        columns=hive_task.TARGET_COLUMNS,
    )

    result = hive_task.merge_initial_assignment_output(
        clustered_output,
        assignment_output,
        {"old-shop": "BD001"},
        3,
    )
    community_ids = result.set_index("storename")["community_id"].to_dict()

    assert community_ids == {
        "new-shop-a": "1",
        "new-shop-b": "1",
        "new-shop-c": "1",
        "old-shop": "BD001",
        "assigned-shop": "BD001",
    }
    assert all(isinstance(value, str) for value in result["community_id"])
    assert result.loc[result["storename"].eq("old-shop"), "is_position"].item() == 0


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
    table_writes: list[tuple[pd.DataFrame, str, bool, object]] = []

    fake_sd = types.SimpleNamespace(
        execute_sql=lambda sql: sql_statements.append(sql),
        write_table=lambda dataframe, table_name, debug, dt: table_writes.append(
            (dataframe.copy(), table_name, debug, dt)
        ),
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
        "temp_table",
        "20260102",
    )

    joined_sql = " ".join(sql_statements[1].split()).lower()
    assert len(sql_statements) == 3
    assert sql_statements[0] == "drop table if exists temp_table"
    assert sql_statements[2] == "drop table if exists temp_table"
    assert len(table_writes) == 1
    written_frame, written_table, debug, dt = table_writes[0]
    assert written_table == "temp_table"
    assert debug is False
    assert dt is None
    assert written_frame.columns.tolist() == hive_task.TARGET_SELECT_COLUMNS
    assert written_frame["storename"].tolist() == ["new-shop"]
    assert joined_sql.startswith("insert overwrite table target_table")
    assert "partition (dt='20260102')" in joined_sql
    assert "from temp_table source" in joined_sql


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
                    "merchant_category": "1",
                    "transaction_time": "20260101T100000",
                    "region": "shanghai",
                }
        ]
    )
    cross_region_source_data = pd.DataFrame(
        [
            {
                "storename": "北京市朝阳区商户",
                "merchant_category": "1",
                "transaction_time": "20260101T110000",
                "region": "shanghai",
            }
        ]
    )
    reads: list[tuple[str, list[str]]] = []
    partition_reads: list[tuple[str, list[str]]] = []
    writes: list[tuple[str, str, dict[str, str]]] = []

    def read_table(table_name: str, dt: list[str]) -> pd.DataFrame:
        reads.append((table_name, dt))
        if table_name == "param_table":
            return parameter_data.copy()
        raise AssertionError(f"unexpected table={table_name}")

    def read_source_hive_table_by_parameters(
        table_name: str,
        dt_values: list[str],
        parameters: list[object],
        parameter_table: str,
    ) -> object:
        partition_reads.append((table_name, dt_values))
        assert len(parameters) == 1
        assert parameter_table == "param_table"
        if table_name == "source_table":
            return hive_task.SourceDataSelection(
                included=source_data.copy(),
                cross_region=cross_region_source_data.copy(),
            )
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
        temp_table_name: str,
        output_dt: str,
    ) -> None:
        writes.append(
            (
                temp_table_name,
                output_dt,
                result.set_index("storename")["is_abnormal"].to_dict(),
            )
        )

    monkeypatch.setattr(hive_task, "sd", types.SimpleNamespace(read_table=read_table))
    monkeypatch.setattr(
        hive_task,
        "read_source_hive_table_by_parameters",
        read_source_hive_table_by_parameters,
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
        lambda config, transactions: run_result,
    )
    monkeypatch.setattr(
        hive_task,
        "build_initial_assignment_output",
        lambda clustered_output,
        transactions,
        config,
        assignment_config,
        source_table,
        output_dt,
        minimum_community_size: clustered_output,
    )
    monkeypatch.setattr(
        hive_task,
        "overwrite_target_table",
        overwrite_target_table,
    )

    task_config = hive_task.HiveTaskConfig(
        algorithm_config=_app_config(tmp_path),
        assignment_config=_assignment_config(),
        source_table="source_table",
        parameter_table="param_table",
        target_table="target_table",
        target_temp_table="temp_table",
        dt_expression="T-1",
    )
    summary = hive_task.TaskMain(task_config).taskrun()

    assert reads == [("param_table", ["20260101"])]
    assert partition_reads == [("source_table", ["20260101"])]
    assert writes == [
        (
            "temp_table",
                "20260101",
                {
                    "shop-a": "3",
                    "北京市朝阳区商户": "5",
                },
        )
    ]
    assert summary.input_rows == 1
    assert summary.output_rows == 2


def test_hive_parameters_preserve_entrypoint_decay_tau(
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
    entrypoint_config = _app_config(tmp_path)
    runtime_config = hive_task.build_runtime_config(
        parameters,
        "param_table",
        entrypoint_config.cooccurrence.decay_tau_minutes,
    )
    config = hive_task.apply_runtime_parameters(
        entrypoint_config,
        runtime_config,
    )

    assert config.cooccurrence.window_minutes == 90
    assert config.cooccurrence.decay_tau_minutes == 1.0
    assert config.cooccurrence.minimum_unique_users == 4
    assert config.anchors.minimum_community_size == 5


def test_filter_source_data_by_parameters_uses_region_and_storename_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    parameter_data = pd.DataFrame(
        [
            {
                "start_date": "20260101",
                "end_date": "20260102",
                "region": "兰州",
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
                "storename": "兰州市城关区商户",
                "transaction_time": "20260102T100000",
                "region": "兰州",
            },
            {
                "account_number": "u2",
                "global_flow_number": "f2",
                "storename": "酒泉市肃州区商户",
                "transaction_time": "20260102T100000",
                "region": "兰州",
            },
            {
                "account_number": "u3",
                "global_flow_number": "f3",
                "storename": "西安市雁塔区商户",
                "transaction_time": "20260103T000000",
                "region": "兰州",
            },
            {
                "account_number": "u4",
                "global_flow_number": "f4",
                "storename": "普通商户",
                "transaction_time": "20260103T000000",
                "region": "兰州",
            },
            {
                "account_number": "u5",
                "global_flow_number": "f5",
                "storename": "上海市商户",
                "transaction_time": "20260103T000000",
                "region": "上海",
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

    assert filtered["storename"].tolist() == [
        "兰州市城关区商户",
        "酒泉市肃州区商户",
        "普通商户",
    ]
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
