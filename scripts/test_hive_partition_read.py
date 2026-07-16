from __future__ import annotations

import datetime
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import pandas as pd


class HiveReadError(RuntimeError):
    """Hive 分区表读取失败。"""


def latest_completed_month_end(today: datetime.date) -> str:
    current_month_start = today.replace(day=1)
    previous_month_end = current_month_start - datetime.timedelta(days=1)
    return previous_month_end.strftime("%Y%m%d")


@dataclass(frozen=True)
class ReadConfig:
    table_root: Path
    table_name: str
    dt_values: Tuple[str, ...]
    preview_rows: int


CONFIG = ReadConfig(
    table_root=Path("/appdata/project/fid_bg_icmp/tbl"),
    table_name="dev_icamp.icamp_merchant_cluster_algo_input",
    dt_values=(latest_completed_month_end(datetime.date.today()),),
    preview_rows=5,
)


def _hive_storage_table_name(full_table_name: str) -> str:
    text = full_table_name.strip()
    parts = text.split(".")
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[1]
    if len(parts) == 1 and parts[0]:
        return parts[0]
    raise HiveReadError(
        f"Hive 表名格式错误，应为 db.table 或 table: table={full_table_name!r}"
    )


def _hive_partition_path(
    table_root: Path,
    table_name: str,
    dt_value: str,
) -> Path:
    storage_table_name = _hive_storage_table_name(table_name)
    return table_root / storage_table_name / f"dt={dt_value}"


def _hive_partition_part_files(partition_path: Path) -> List[Path]:
    if not partition_path.exists():
        return []
    return [
        path
        for path in sorted(partition_path.rglob("part*"))
        if path.is_file()
    ]


def prepare_hive_partition(table_name: str, dt_value: str) -> None:
    try:
        import spdbccc_data as sd
    except ImportError as error:
        raise HiveReadError(
            "无法导入 spdbccc_data，请在线上任务环境中运行此脚本"
        ) from error
    sd.read_table(table_name, dt=[dt_value])


def _read_hive_part_file(
    file_path: Path,
    table_name: str,
    dt_value: str,
) -> pd.DataFrame:
    try:
        return pd.read_parquet(file_path)
    except (OSError, ValueError, ImportError) as error:
        raise HiveReadError(
            "Hive 分片 parquet 读取失败: "
            f"table={table_name}, dt={dt_value}, path={file_path}, reason={error}"
        ) from error


def _with_partition_dt(dataframe: pd.DataFrame, dt_value: str) -> pd.DataFrame:
    result = dataframe.copy()
    result["dt"] = str(dt_value)
    return result


def read_partitioned_hive_table(
    table_root: Path,
    table_name: str,
    dt_values: Tuple[str, ...],
) -> pd.DataFrame:
    if not dt_values:
        raise HiveReadError(f"Hive 读表 dt 不能为空: table={table_name}")

    dataframes: List[pd.DataFrame] = []
    for dt_value in dt_values:
        dt_text = str(dt_value)
        prepare_hive_partition(table_name, dt_text)
        partition_path = _hive_partition_path(
            table_root,
            table_name,
            dt_text,
        )
        part_files = _hive_partition_part_files(partition_path)
        print(
            "partition_scan "
            f"table={table_name!r} dt={dt_text!r} "
            f"path={str(partition_path)!r} exists={partition_path.exists()} "
            f"part_count={len(part_files)}"
        )

        for file_path in part_files:
            start_time = time.time()
            dataframe = _read_hive_part_file(
                file_path,
                table_name,
                dt_text,
            )
            print(
                "part_read "
                f"path={str(file_path)!r} rows={len(dataframe)} "
                f"columns={len(dataframe.columns)} "
                f"seconds={time.time() - start_time:.2f}"
            )
            if dataframe.empty:
                continue
            dataframes.append(_with_partition_dt(dataframe, dt_text))

    if not dataframes:
        raise HiveReadError(
            "Hive 日期范围内没有非空分区: "
            f"table_root={table_root}, table={table_name}, dt_values={dt_values}"
        )

    return pd.concat(dataframes, ignore_index=True, copy=False)


def mount_hive_storage() -> None:
    try:
        from spdbccc_data import mountCheck
    except ImportError as error:
        raise HiveReadError(
            "无法导入 spdbccc_data.mountCheck，请在线上任务环境中运行此脚本"
        ) from error
    mountCheck.mount_check()


def finish_task() -> None:
    try:
        from spdbccc_data import task as taskfinish
    except ImportError as error:
        raise HiveReadError(
            "无法导入 spdbccc_data.task，请在线上任务环境中运行此脚本"
        ) from error
    print("taskfinish_start")
    taskfinish.finish_task()
    print("taskfinish_success")


def validate_config(config: ReadConfig) -> None:
    if not config.dt_values:
        raise HiveReadError(f"Hive 读表 dt 不能为空: table={config.table_name}")
    if config.preview_rows < 1:
        raise HiveReadError(
            f"预览行数必须是正整数: preview_rows={config.preview_rows}"
        )


def main(config: ReadConfig) -> int:
    try:
        validate_config(config)
        total_start = time.time()
        print(
            "read_start "
            f"table_root={str(config.table_root)!r} "
            f"table={config.table_name!r} dt_values={config.dt_values!r}"
        )
        mount_hive_storage()
        result = read_partitioned_hive_table(
            config.table_root,
            config.table_name,
            config.dt_values,
        )
        print(
            "read_success "
            f"rows={len(result)} columns={result.columns.tolist()} "
            f"seconds={time.time() - total_start:.2f}"
        )
        print(result.head(config.preview_rows).to_string(index=False))
        return 0
    finally:
        finish_task()


if __name__ == "__main__":
    sys.exit(main(CONFIG))
