import importlib
from pathlib import Path
import sys
import types

import pandas as pd
import pytest


def _install_spdbccc_data_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    spdbccc_data = types.ModuleType("spdbccc_data")
    spdbccc_data.dtDate = types.SimpleNamespace(dt_date=lambda expression: "20260101")
    spdbccc_data.formattedExc = types.SimpleNamespace(formatted_exc=lambda: None)
    spdbccc_data.loging = types.SimpleNamespace(log_data=lambda message: None)
    spdbccc_data.mountCheck = types.SimpleNamespace(mount_check=lambda: None)
    spdbccc_data.task = types.SimpleNamespace(finish_task=lambda: None)
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
        ]
    )

    output = hive_task.build_hive_target_output(business_results)

    assert output["is_abnormal"].tolist() == ["1", "2"]


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


def test_hive_parameters_override_removed_ini_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_spdbccc_data_stub(monkeypatch)
    hive_task = importlib.import_module("business_district.hive_task")
    config_module = importlib.import_module("business_district.config")
    config_path = tmp_path / "city.ini"
    config_path.write_text(
        f"""
[city]
code = "test-city"
name = "测试市"

[input]
transactions_path = "../data.txt"
timestamp_formats = %Y%m%dT%H%M%S

[visits]
merge_window_minutes = 30
maximum_daily_merchants_per_card = 30

[cooccurrence]

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
maximum_participation = 0.99
chain_visit_count_quantile = 1.0
chain_minimum_visit_count = 100

[output]
directory = "{(tmp_path / 'output').as_posix()}"
""",
        encoding="utf-8",
    )
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
    config = config_module.load_config_with_runtime_parameters(
        config_path,
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
                "is_daily": "true",
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
