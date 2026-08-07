from __future__ import annotations

import pickle
from pathlib import Path
from typing import Iterable, Iterator

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
    try:
        with path.open("wb") as file:
            pickle.dump(statistics, file, protocol=PICKLE_PROTOCOL)
    except (OSError, pickle.PickleError) as error:
        raise AlgorithmError(
            "商户对中间文件写入失败: "
            f"type={type(error).__name__}, reason={error}, path={path}"
        ) from error


def _write_pair_statistics_update(
    statistics: PairStatistics,
    path: Path,
) -> None:
    write_pair_statistics(statistics, path)
    print(
        f"边={len(statistics.strengths)}，"
        f"点={len(statistics.merchant_visit_counts)}",
        flush=True,
    )


def write_pair_statistics_updates(
    updates: Iterable[PairStatistics],
    path: Path,
) -> PairStatistics:
    iterator: Iterator[PairStatistics] = iter(updates)
    try:
        statistics = next(iterator)
    except StopIteration as error:
        raise AlgorithmError(
            f"商户对中间文件没有可保存的统计更新: path={path}"
        ) from error
    _write_pair_statistics_update(statistics, path)
    for statistics in iterator:
        _write_pair_statistics_update(statistics, path)
    return statistics


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
