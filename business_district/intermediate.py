from __future__ import annotations

import os
import sqlite3
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

    connection = sqlite3.connect(str(temporary_path))
    try:
        connection.execute(
            """
            CREATE TABLE merchant_pairs (
                merchant_a TEXT NOT NULL,
                merchant_b TEXT NOT NULL,
                strength REAL NOT NULL,
                support INTEGER NOT NULL,
                PRIMARY KEY (merchant_a, merchant_b)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE merchant_visits (
                merchant_id TEXT NOT NULL PRIMARY KEY,
                visit_count INTEGER NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO merchant_pairs (
                merchant_a,
                merchant_b,
                strength,
                support
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    left,
                    right,
                    float(statistics.strengths[(left, right)]),
                    int(statistics.supports[(left, right)]),
                )
                for left, right in sorted(statistics.strengths)
            ],
        )
        connection.executemany(
            """
            INSERT INTO merchant_visits (
                merchant_id,
                visit_count
            )
            VALUES (?, ?)
            """,
            [
                (merchant_id, int(visit_count))
                for merchant_id, visit_count in sorted(
                    statistics.merchant_visit_counts.items()
                )
            ],
        )
        connection.commit()
    except sqlite3.DatabaseError as error:
        raise AlgorithmError(
            f"商户对中间文件写入失败: path={path}, reason={error}"
        ) from error
    finally:
        connection.close()

    os.replace(temporary_path, path)
