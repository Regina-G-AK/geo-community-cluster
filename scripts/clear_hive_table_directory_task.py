from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable, NamedTuple, Tuple


PROJECT_ROOT = Path("/appdata/project/fid_bg_icmp")
TARGET_DIRECTORY = PROJECT_ROOT / "tbl"


class DirectoryCleanupConfig(NamedTuple):
    project_root: Path
    target_directory: Path


class DirectoryCleanupSummary(NamedTuple):
    target_directory: Path
    removed_entry_count: int
    seconds: float


class TaskPlatform(NamedTuple):
    mount_check: Callable[[], None]
    log_data: Callable[[str], None]
    format_exception: Callable[[], None]
    finish_task: Callable[[], None]


class DirectoryCleanupConfigError(ValueError):
    """目录清理任务配置错误。"""


class DirectoryCleanupError(RuntimeError):
    """目录清理失败。"""


CONFIG = DirectoryCleanupConfig(
    project_root=PROJECT_ROOT,
    target_directory=TARGET_DIRECTORY,
)


def validate_config(config: DirectoryCleanupConfig) -> None:
    if not config.project_root.is_absolute():
        raise DirectoryCleanupConfigError(
            "project_root 必须是绝对路径: "
            f"project_root={str(config.project_root)!r}"
        )
    if not config.target_directory.is_absolute():
        raise DirectoryCleanupConfigError(
            "target_directory 必须是绝对路径: "
            f"target_directory={str(config.target_directory)!r}"
        )

    expected_directory = config.project_root / "tbl"
    if config.target_directory != expected_directory:
        raise DirectoryCleanupConfigError(
            "target_directory 必须是 project_root 下的 tbl 目录: "
            f"project_root={str(config.project_root)!r}, "
            f"target_directory={str(config.target_directory)!r}, "
            f"expected_directory={str(expected_directory)!r}"
        )
    if not config.project_root.is_dir():
        raise FileNotFoundError(
            "项目挂载目录不存在或不是目录，请检查 mount_check 结果: "
            f"project_root={str(config.project_root)!r}"
        )
    if config.target_directory.is_symlink():
        raise DirectoryCleanupConfigError(
            "禁止清理符号链接形式的目标目录: "
            f"target_directory={str(config.target_directory)!r}"
        )
    if not config.target_directory.exists():
        raise FileNotFoundError(
            "待清理目录不存在: "
            f"target_directory={str(config.target_directory)!r}"
        )
    if not config.target_directory.is_dir():
        raise NotADirectoryError(
            "待清理路径不是目录: "
            f"target_directory={str(config.target_directory)!r}"
        )
    if config.target_directory.resolve().parent != config.project_root.resolve():
        raise DirectoryCleanupConfigError(
            "待清理目录解析后不在项目挂载目录下: "
            f"project_root={str(config.project_root)!r}, "
            f"target_directory={str(config.target_directory)!r}, "
            f"resolved_target={str(config.target_directory.resolve())!r}"
        )


def remove_entry(path: Path) -> None:
    try:
        if path.is_symlink() or not path.is_dir():
            path.unlink()
            return
        shutil.rmtree(str(path))
    except OSError as error:
        raise DirectoryCleanupError(
            "目录条目删除失败: "
            f"path={str(path)!r}, "
            f"error_type={type(error).__name__!r}, reason={str(error)!r}"
        ) from error


def clear_directory(config: DirectoryCleanupConfig) -> int:
    validate_config(config)
    target_directory = config.target_directory
    try:
        entries: Tuple[Path, ...] = tuple(target_directory.iterdir())
    except OSError as error:
        raise DirectoryCleanupError(
            "待清理目录读取失败: "
            f"target_directory={str(target_directory)!r}, "
            f"error_type={type(error).__name__!r}, reason={str(error)!r}"
        ) from error

    for entry in entries:
        validate_config(config)
        remove_entry(entry)

    validate_config(config)
    try:
        remaining_entries: Tuple[Path, ...] = tuple(target_directory.iterdir())
    except OSError as error:
        raise DirectoryCleanupError(
            "清理后无法读取目标目录: "
            f"target_directory={str(target_directory)!r}, "
            f"error_type={type(error).__name__!r}, reason={str(error)!r}"
        ) from error
    if remaining_entries:
        raise DirectoryCleanupError(
            "目标目录清理后仍有残留条目: "
            f"target_directory={str(target_directory)!r}, "
            f"remaining_entries={tuple(str(path) for path in remaining_entries)!r}"
        )
    return len(entries)


def load_task_platform() -> TaskPlatform:
    try:
        from spdbccc_data import formattedExc
        from spdbccc_data import loging as logrecord
        from spdbccc_data import mountCheck
        from spdbccc_data import task as taskfinish
    except ImportError as error:
        raise RuntimeError(
            "无法导入 spdbccc_data 任务组件，请在线上任务环境中运行此脚本"
        ) from error

    return TaskPlatform(
        mount_check=mountCheck.mount_check,
        log_data=logrecord.log_data,
        format_exception=formattedExc.formatted_exc,
        finish_task=taskfinish.finish_task,
    )


class TaskMain:
    def __init__(
        self,
        platform: TaskPlatform,
        config: DirectoryCleanupConfig,
    ) -> None:
        self.platform = platform
        self.config = config

    def check(self) -> None:
        self.platform.mount_check()
        validate_config(self.config)
        self.platform.log_data(
            "directory cleanup task mount and check success "
            f"target_directory={str(self.config.target_directory)!r}"
        )

    def taskrun(self) -> DirectoryCleanupSummary:
        started_at = time.time()
        self.platform.log_data(
            "directory cleanup task start "
            f"target_directory={str(self.config.target_directory)!r}"
        )
        try:
            removed_entry_count = clear_directory(self.config)
            summary = DirectoryCleanupSummary(
                target_directory=self.config.target_directory,
                removed_entry_count=removed_entry_count,
                seconds=time.time() - started_at,
            )
            self.platform.log_data(
                "directory cleanup task success "
                f"target_directory={str(summary.target_directory)!r}, "
                f"removed_entry_count={summary.removed_entry_count}, "
                f"seconds={summary.seconds:.2f}"
            )
            return summary
        except Exception:
            self.platform.format_exception()
            raise

    def destroy(self) -> None:
        self.platform.log_data(
            "directory cleanup task destroy success temporary_resource_count=0"
        )


def run_task(task: TaskMain) -> DirectoryCleanupSummary:
    try:
        task.check()
        return task.taskrun()
    finally:
        try:
            task.destroy()
        finally:
            task.platform.finish_task()


def main() -> None:
    summary = run_task(TaskMain(load_task_platform(), CONFIG))
    print(
        "目录清理完成 "
        f"目录={summary.target_directory} "
        f"清除数量={summary.removed_entry_count} "
        f"seconds={summary.seconds:.2f}"
    )


if __name__ == "__main__":
    main()
