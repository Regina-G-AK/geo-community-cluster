from __future__ import annotations

import os
import pickle
from pathlib import Path

from business_district.errors import AlgorithmError
from business_district.graph import PairStatistics


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
            pickle.dump(statistics, file, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary_path, path)
    except (OSError, pickle.PickleError) as error:
        raise AlgorithmError(
            f"商户对中间文件写入失败: path={path}, reason={error}"
        ) from error
