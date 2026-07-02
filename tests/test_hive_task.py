import importlib
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


def test_hive_target_output_formats_active_status_as_normal(
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

    assert output["is_abnormal"].tolist() == ["normal", "suspect_online"]
