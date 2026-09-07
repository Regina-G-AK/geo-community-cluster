from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from scripts import clear_hive_table_directory_task as cleanup_task
from scripts.clear_hive_table_directory_task import (
    DirectoryCleanupConfig,
    DirectoryCleanupConfigError,
    DirectoryCleanupError,
    TaskMain,
    TaskPlatform,
    run_task,
)


def build_config(project_root: Path) -> DirectoryCleanupConfig:
    return DirectoryCleanupConfig(
        project_root=project_root,
        target_directory=project_root / "tbl",
    )


def build_platform(events: List[str]) -> TaskPlatform:
    return TaskPlatform(
        mount_check=lambda: events.append("mount"),
        log_data=lambda message: events.append(f"log:{message}"),
        format_exception=lambda: events.append("formatted"),
        finish_task=lambda: events.append("finish"),
    )


def test_task_clears_all_contents_and_runs_complete_lifecycle(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "fid_bg_icmp"
    target_directory = project_root / "tbl"
    nested_directory = target_directory / "table_a" / "dt=20260831"
    empty_directory = target_directory / "empty"
    nested_directory.mkdir(parents=True)
    empty_directory.mkdir()
    (target_directory / "part-00000").write_text("root", encoding="utf-8")
    (target_directory / ".hidden").write_text("hidden", encoding="utf-8")
    (nested_directory / "part-00001").write_text("nested", encoding="utf-8")
    events: List[str] = []

    summary = run_task(TaskMain(build_platform(events), build_config(project_root)))

    assert target_directory.is_dir()
    assert tuple(target_directory.iterdir()) == ()
    assert summary.target_directory == target_directory
    assert summary.removed_entry_count == 4
    assert events[0] == "mount"
    assert events[1].startswith("log:directory cleanup task mount and check success")
    assert events[2].startswith("log:directory cleanup task start")
    assert events[3].startswith("log:directory cleanup task success")
    assert events[4:] == [
        "log:directory cleanup task destroy success temporary_resource_count=0",
        "finish",
    ]


def test_task_removes_directory_link_without_deleting_link_target(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "fid_bg_icmp"
    target_directory = project_root / "tbl"
    external_directory = tmp_path / "external"
    target_directory.mkdir(parents=True)
    external_directory.mkdir()
    external_file = external_directory / "preserved.txt"
    external_file.write_text("preserved", encoding="utf-8")
    directory_link = target_directory / "linked_table"
    try:
        directory_link.symlink_to(external_directory, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"当前环境无法创建目录符号链接: reason={error}")
    events: List[str] = []

    summary = run_task(TaskMain(build_platform(events), build_config(project_root)))

    assert target_directory.is_dir()
    assert tuple(target_directory.iterdir()) == ()
    assert external_file.read_text(encoding="utf-8") == "preserved"
    assert summary.removed_entry_count == 1
    assert events[-1] == "finish"


def test_task_destroys_and_finishes_when_target_directory_is_missing(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "fid_bg_icmp"
    project_root.mkdir()
    events: List[str] = []

    with pytest.raises(FileNotFoundError, match="待清理目录不存在"):
        run_task(TaskMain(build_platform(events), build_config(project_root)))

    assert events == [
        "mount",
        "log:directory cleanup task destroy success temporary_resource_count=0",
        "finish",
    ]


def test_task_formats_exception_destroys_and_finishes_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "fid_bg_icmp"
    target_directory = project_root / "tbl"
    target_directory.mkdir(parents=True)
    events: List[str] = []

    def fail_cleanup(config: DirectoryCleanupConfig) -> int:
        raise DirectoryCleanupError(
            "目录条目删除失败: path='blocked', reason='permission denied'"
        )

    monkeypatch.setattr(cleanup_task, "clear_directory", fail_cleanup)

    with pytest.raises(DirectoryCleanupError, match="permission denied"):
        run_task(TaskMain(build_platform(events), build_config(project_root)))

    assert events[0] == "mount"
    assert events[1].startswith("log:directory cleanup task mount and check success")
    assert events[2].startswith("log:directory cleanup task start")
    assert events[3:] == [
        "formatted",
        "log:directory cleanup task destroy success temporary_resource_count=0",
        "finish",
    ]


def test_task_rejects_target_outside_project_tbl_directory(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "fid_bg_icmp"
    project_root.mkdir()
    other_directory = project_root / "other"
    other_directory.mkdir()
    config = DirectoryCleanupConfig(
        project_root=project_root,
        target_directory=other_directory,
    )
    events: List[str] = []

    with pytest.raises(
        DirectoryCleanupConfigError,
        match="target_directory 必须是 project_root 下的 tbl 目录",
    ):
        run_task(TaskMain(build_platform(events), config))

    assert other_directory.is_dir()
    assert events == [
        "mount",
        "log:directory cleanup task destroy success temporary_resource_count=0",
        "finish",
    ]
