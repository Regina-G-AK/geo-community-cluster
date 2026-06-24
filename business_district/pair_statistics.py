from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from business_district.errors import TransactionDataError
from business_district.graph import MerchantPair, PairStatistics


def write_pair_statistics(statistics: PairStatistics, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    if temporary_path.exists():
        temporary_path.unlink()

    connection = sqlite3.connect(temporary_path)
    try:
        connection.executescript(
            """
            CREATE TABLE merchant_pairs (
                merchant_a TEXT NOT NULL,
                merchant_b TEXT NOT NULL,
                strength REAL NOT NULL CHECK (strength >= 0),
                support INTEGER NOT NULL CHECK (support >= 1),
                PRIMARY KEY (merchant_a, merchant_b),
                CHECK (merchant_a < merchant_b)
            );
            CREATE TABLE merchant_visits (
                merchant_id TEXT PRIMARY KEY,
                visit_count INTEGER NOT NULL CHECK (visit_count >= 1)
            );
            """
        )
        pair_rows = [
            (left, right, float(strength), int(statistics.supports[(left, right)]))
            for (left, right), strength in sorted(statistics.strengths.items())
        ]
        visit_rows = [
            (merchant_id, int(visit_count))
            for merchant_id, visit_count in sorted(
                statistics.merchant_visit_counts.items()
            )
        ]
        connection.executemany(
            "INSERT INTO merchant_pairs VALUES (?, ?, ?, ?)",
            pair_rows,
        )
        connection.executemany(
            "INSERT INTO merchant_visits VALUES (?, ?)",
            visit_rows,
        )
        connection.commit()
    finally:
        connection.close()

    os.replace(temporary_path, path)


def load_pair_statistics(path: Path) -> PairStatistics:
    if not path.is_file():
        raise TransactionDataError(f"商户对数据文件不存在: {path}")

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        pair_rows = connection.execute(
            "SELECT merchant_a, merchant_b, strength, support FROM merchant_pairs"
        ).fetchall()
        visit_rows = connection.execute(
            "SELECT merchant_id, visit_count FROM merchant_visits"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise TransactionDataError(
            f"商户对数据文件格式错误: path={path}, reason={error}"
        ) from error
    finally:
        connection.close()

    strengths: dict[MerchantPair, float] = {}
    supports: dict[MerchantPair, int] = {}
    for merchant_a, merchant_b, strength, support in pair_rows:
        pair = (str(merchant_a), str(merchant_b))
        strengths[pair] = float(strength)
        supports[pair] = int(support)

    merchant_visit_counts = {
        str(merchant_id): int(visit_count)
        for merchant_id, visit_count in visit_rows
    }
    return PairStatistics(
        strengths=strengths,
        supports=supports,
        merchant_visit_counts=merchant_visit_counts,
    )
