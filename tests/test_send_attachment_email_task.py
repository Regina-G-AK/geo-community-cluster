from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pandas as pd
import pytest

from scripts.send_attachment_email_task import (
    TableExportEmailConfig,
    TaskMain,
    TaskPlatform,
    run_task,
)


def _build_config(
    root_path: Path,
    maximum_attempts: int,
) -> TableExportEmailConfig:
    return TableExportEmailConfig(
        root_path=root_path,
        source_table="dev_icamp.icamp_merchant_cluster_algo_output",
        dt_values=("20260720",),
        output_filename="community_output.xlsx",
        title="测试标题",
        content="测试正文",
        recipients=("to@example.com",),
        copy_recipients=(),
        maximum_attempts=maximum_attempts,
        retry_delay_seconds=0.0,
    )


def test_table_export_email_task_writes_excel_and_runs_complete_lifecycle(
    tmp_path: Path,
) -> None:
    events: List[str] = []
    read_request: Dict[str, object] = {}
    email_request: Dict[str, object] = {}
    source_data = pd.DataFrame(
        [
            {"storename": "商户甲", "community_id": "1"},
            {"storename": "商户乙", "community_id": ""},
            {"storename": "商户丙", "community_id": None},
        ]
    )

    def read_table(
        table_name: str,
        dt: List[str],
    ) -> object:
        events.append("read")
        read_request.update({"table_name": table_name, "dt": dt})
        return source_data.copy()

    def send_email(**parameters: object) -> object:
        events.append("send")
        email_request.update(parameters)
        return None

    platform = TaskPlatform(
        mount_check=lambda: events.append("mount"),
        read_table=read_table,
        send_email=send_email,
        log_data=lambda message: events.append(f"log:{message}"),
        format_exception=lambda: events.append("formatted"),
        finish_task=lambda: events.append("finish"),
    )

    summary = run_task(TaskMain(platform, _build_config(tmp_path, 1)))
    output_path = tmp_path / "community_output.xlsx"

    assert summary.source_row_count == 3
    assert summary.exported_row_count == 1
    assert summary.source_column_count == 2
    assert summary.output_path == output_path
    assert read_request == {
        "table_name": "dev_icamp.icamp_merchant_cluster_algo_output",
        "dt": ["20260720"],
    }
    assert email_request == {
        "title": "测试标题",
        "content": "测试正文",
        "to": ["to@example.com"],
        "files": [str(output_path)],
    }
    assert pd.read_excel(output_path).to_dict("records") == [
        {"storename": "商户甲", "community_id": 1}
    ]
    assert events[0] == "mount"
    assert events[1].startswith(
        "log:table export email task mount and check success"
    )
    assert events[2].startswith("log:table export email task start")
    assert events[3] == "read"
    assert events[4].startswith(
        "log:table export email task file write success"
    )
    assert events[5] == "send"
    assert events[6].startswith("log:table export email task success")
    assert events[7] == (
        "log:table export email task destroy success "
        "temporary_resource_count=0, generated_file_preserved=1"
    )
    assert events[8] == "finish"


def test_table_export_email_task_retries_and_finishes_after_send_failure(
    tmp_path: Path,
) -> None:
    events: List[str] = []

    def send_email(**parameters: object) -> object:
        events.append(f"send:{parameters['title']}")
        raise RuntimeError("邮件服务不可用")

    platform = TaskPlatform(
        mount_check=lambda: events.append("mount"),
        read_table=lambda table_name, dt: pd.DataFrame(
            [{"value": 1, "community_id": "1"}]
        ),
        send_email=send_email,
        log_data=lambda message: events.append(f"log:{message}"),
        format_exception=lambda: events.append("formatted"),
        finish_task=lambda: events.append("finish"),
    )

    with pytest.raises(RuntimeError, match="邮件服务不可用"):
        run_task(TaskMain(platform, _build_config(tmp_path, 2)))

    assert (tmp_path / "community_output.xlsx").is_file()
    assert events.count("send:测试标题") == 2
    assert any(
        "operation='send_email'" in event
        for event in events
        if event.startswith("log:external call retry warning")
    )
    assert events[-3] == "formatted"
    assert events[-2] == (
        "log:table export email task destroy success "
        "temporary_resource_count=0, generated_file_preserved=1"
    )
    assert events[-1] == "finish"


def test_table_export_email_task_retries_read_and_does_not_send_after_failure(
    tmp_path: Path,
) -> None:
    events: List[str] = []

    def read_table(
        table_name: str,
        dt: List[str],
    ) -> object:
        events.append(f"read:{table_name}:{dt[0]}")
        raise RuntimeError("读表请求失败")

    platform = TaskPlatform(
        mount_check=lambda: events.append("mount"),
        read_table=read_table,
        send_email=lambda **parameters: events.append(
            f"send:{parameters['title']}"
        ),
        log_data=lambda message: events.append(f"log:{message}"),
        format_exception=lambda: events.append("formatted"),
        finish_task=lambda: events.append("finish"),
    )

    with pytest.raises(RuntimeError, match="读表请求失败"):
        run_task(TaskMain(platform, _build_config(tmp_path, 2)))

    assert events.count(
        "read:dev_icamp.icamp_merchant_cluster_algo_output:20260720"
    ) == 2
    assert not any(event.startswith("send:") for event in events)
    assert not (tmp_path / "community_output.xlsx").exists()
    assert events[-3] == "formatted"
    assert events[-2] == (
        "log:table export email task destroy success "
        "temporary_resource_count=0, generated_file_preserved=1"
    )
    assert events[-1] == "finish"


def test_table_export_email_task_finishes_when_mount_root_is_missing(
    tmp_path: Path,
) -> None:
    events: List[str] = []
    platform = TaskPlatform(
        mount_check=lambda: events.append("mount"),
        read_table=lambda table_name, dt: events.append(
            f"read:{table_name}:{dt[0]}"
        ),
        send_email=lambda **parameters: events.append(
            f"send:{parameters['title']}"
        ),
        log_data=lambda message: events.append(f"log:{message}"),
        format_exception=lambda: events.append("formatted"),
        finish_task=lambda: events.append("finish"),
    )

    with pytest.raises(FileNotFoundError, match="挂载根目录不存在"):
        run_task(
            TaskMain(
                platform,
                _build_config(tmp_path / "missing", 1),
            )
        )

    assert events == [
        "mount",
        (
            "log:table export email task destroy success "
            "temporary_resource_count=0, generated_file_preserved=1"
        ),
        "finish",
    ]
