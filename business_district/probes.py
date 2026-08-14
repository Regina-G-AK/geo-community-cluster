from __future__ import annotations

from typing import List, Set

import pandas as pd

# 资源检测已停用，保留原导入代码便于恢复
# from business_district.memory_monitor import (
#     MEBIBYTE_BYTES,
#     read_process_memory_usage,
# )


PROBE_PREFIX = "[stage]"


def _validate_stage(stage: str) -> str:
    stage_name = stage.strip()
    if not stage_name:
        raise ValueError("探针阶段名称不能为空")
    return stage_name


def format_dataframe_probe(dataframe: pd.DataFrame) -> str:
    columns: List[str] = [
        (
            f"name={column!r}, name_type={type(column).__name__}, "
            f"dtype={dtype}"
        )
        for column, dtype in zip(dataframe.columns.tolist(), dataframe.dtypes.tolist())
    ]
    return (
        f"pandas_version={pd.__version__}, "
        f"dataframe_type={type(dataframe).__name__}, "
        f"shape={dataframe.shape}, "
        f"columns_type={type(dataframe.columns).__name__}, "
        f"columns=[{'; '.join(columns)}]"
    )


def format_series_probe(series: pd.Series) -> str:
    sample_types: Set[str] = {
        type(value).__name__ for value in series.head(100).tolist()
    }
    return (
        f"pandas_version={pd.__version__}, "
        f"series_type={type(series).__name__}, "
        f"name={series.name!r}, "
        f"dtype={series.dtype}, "
        f"length={len(series)}, "
        f"sample_value_types={sorted(sample_types)}"
    )


def print_probe(stage: str, details: str) -> None:
    stage_name = _validate_stage(stage)
    # 资源检测已停用，保留原检测代码便于恢复
    # memory_usage = read_process_memory_usage()
    # rss_mib = memory_usage.rss_bytes / MEBIBYTE_BYTES
    details_text = f", {details}" if details else ""
    print(
        f"{PROBE_PREFIX} stage={stage_name}{details_text}",
        flush=True,
    )
    # print(
    #     f"{PROBE_PREFIX} stage={stage_name}, "
    #     f"memory_scope={memory_usage.scope}, rss_mib={rss_mib:.2f}"
    #     f"{details_text}",
    #     flush=True,
    # )


def print_dataframe_probe(stage: str, dataframe: pd.DataFrame) -> None:
    print_probe(stage, format_dataframe_probe(dataframe))


def print_series_probe(stage: str, series: pd.Series) -> None:
    print_probe(stage, format_series_probe(series))
