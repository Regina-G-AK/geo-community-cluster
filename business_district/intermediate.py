from __future__ import annotations

import os
import pickle
from pathlib import Path

from business_district.errors import AlgorithmError
from business_district.graph import PairStatistics


PICKLE_PROTOCOL = 4


def build_pair_statistics_path(
    output_directory: Path,
    region: str,
) -> Path:
    region_text: str = region.strip()
    if not region_text:
        raise AlgorithmError("商户对中间文件地区不能为空")
    if any(separator in region_text for separator in ("/", "\\")):
        raise AlgorithmError(
            f"商户对中间文件地区不能包含路径分隔符: region={region_text!r}"
        )
    return output_directory / f"pair_statistics_{region_text}.pkl"


def write_pair_statistics(
    statistics: PairStatistics,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    if temporary_path.exists():
        temporary_path.unlink()

    try:
        with temporary_path.open("wb") as file:
            pickle.dump(statistics, file, protocol=PICKLE_PROTOCOL)
        os.replace(temporary_path, path)
    except (OSError, pickle.PickleError) as error:
        raise AlgorithmError(
            f"商户对中间文件写入失败: path={path}, reason={error}"
        ) from error


def read_pair_statistics(
    path: Path,
) -> PairStatistics:
    if not path.exists():
        raise AlgorithmError(f"商户对中间文件不存在: path={path}")
    try:
        with path.open("rb") as file:
            statistics = pickle.load(file)
    except (OSError, pickle.PickleError, EOFError, AttributeError, ValueError) as error:
        raise AlgorithmError(
            f"商户对中间文件读取失败: path={path}, reason={error}"
        ) from error
    if not isinstance(statistics, PairStatistics):
        raise AlgorithmError(
            "商户对中间文件类型错误: "
            f"path={path}, type={type(statistics).__name__}"
        )
    return statistics
