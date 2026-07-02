from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from business_district.errors import AlgorithmError, TransactionDataError

from incremental_assignment.models import CandidateMerchant

ALGORITHM_MERCHANT_COLUMNS = {
    "city_code",
    "merchant_id",
    "community_id",
    "is_anchor_candidate",
}
ALGORITHM_COMMUNITY_COLUMNS = {
    "city_code",
    "community_id",
    "merchant_count",
    "anchor_count",
    "anchor_merchants",
}
ALGORITHM_EDGE_COLUMNS = {
    "city_code",
    "merchant_a",
    "merchant_b",
    "weight",
    "sppmi",
}
CANDIDATE_COLUMNS = {
    "city_code",
    "merchant_id",
    "first_seen_at",
    "last_seen_at",
    "unique_user_count",
    "customer_ids",
}
COORDINATE_COLUMNS = {"city_code", "merchant_id", "latitude", "longitude"}


def read_csv_checked(
    path: Path,
    required_columns: set[str],
    table_name: str,
) -> pd.DataFrame:
    if not path.exists():
        raise TransactionDataError(f"{table_name} 不存在: path={path}")
    data = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    missing = required_columns.difference(data.columns)
    if missing:
        raise TransactionDataError(
            f"{table_name} 缺少必需字段: path={path}, missing={sorted(missing)}"
        )
    return data


def load_algorithm_one_outputs(
    algorithm_one_directory: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    if not algorithm_one_directory.exists():
        raise TransactionDataError(f"算法一结果目录不存在: path={algorithm_one_directory}")
    merchants = read_csv_checked(
        algorithm_one_directory / "merchants.csv",
        ALGORITHM_MERCHANT_COLUMNS,
        "算法一商户表",
    )
    communities = read_csv_checked(
        algorithm_one_directory / "communities.csv",
        ALGORITHM_COMMUNITY_COLUMNS,
        "算法一商圈表",
    )
    edges = read_csv_checked(
        algorithm_one_directory / "edges.csv",
        ALGORITHM_EDGE_COLUMNS,
        "算法一边表",
    )
    summary_path = algorithm_one_directory / "summary.json"
    if not summary_path.exists():
        raise TransactionDataError(f"算法一摘要不存在: path={summary_path}")
    with summary_path.open("r", encoding="utf-8") as file:
        summary = json.load(file)
    if not isinstance(summary, dict):
        raise TransactionDataError(f"算法一摘要必须是 JSON 对象: path={summary_path}")
    return merchants, communities, edges, summary


def _parse_timestamp(value: str, column: str, merchant_id: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise TransactionDataError(
            f"候选商户时间字段格式无效: merchant_id={merchant_id}, column={column}, value={value}"
        ) from error


def _parse_int(value: str, column: str, merchant_id: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise TransactionDataError(
            f"候选商户整数字段格式无效: merchant_id={merchant_id}, column={column}, value={value}"
        ) from error
    if result < 0:
        raise TransactionDataError(
            f"候选商户整数字段不能为负数: merchant_id={merchant_id}, column={column}, value={value}"
        )
    return result


def _parse_optional_float(
    value: object,
    column: str,
    merchant_id: str,
) -> float | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as error:
        raise TransactionDataError(
            f"候选商户数值字段格式无效: merchant_id={merchant_id}, column={column}, value={text}"
        ) from error


def _parse_customer_ids(value: str, merchant_id: str) -> frozenset[str]:
    ids = frozenset(item.strip() for item in value.split("|") if item.strip())
    if not ids:
        raise TransactionDataError(f"候选商户 customer_ids 不能为空: merchant_id={merchant_id}")
    return ids


def load_candidates(path: Path) -> list[CandidateMerchant]:
    data = read_csv_checked(path, CANDIDATE_COLUMNS, "待判定商户表")
    candidates: list[CandidateMerchant] = []
    seen: set[tuple[str, str]] = set()
    for row in data.to_dict("records"):
        merchant_id = str(row["merchant_id"]).strip()
        city_code = str(row["city_code"]).strip()
        if not merchant_id or not city_code:
            raise TransactionDataError("待判定商户表 city_code 和 merchant_id 必须非空")
        key = (city_code, merchant_id)
        if key in seen:
            raise TransactionDataError(
                f"待判定商户表包含重复商户: city_code={city_code}, merchant_id={merchant_id}"
            )
        seen.add(key)
        first_seen_at = _parse_timestamp(str(row["first_seen_at"]), "first_seen_at", merchant_id)
        last_seen_at = _parse_timestamp(str(row["last_seen_at"]), "last_seen_at", merchant_id)
        if last_seen_at < first_seen_at:
            raise TransactionDataError(
                f"候选商户 last_seen_at 不能早于 first_seen_at: merchant_id={merchant_id}"
            )
        candidates.append(
            CandidateMerchant(
                city_code=city_code,
                merchant_id=merchant_id,
                first_seen_at=first_seen_at,
                last_seen_at=last_seen_at,
                unique_user_count=_parse_int(
                    str(row["unique_user_count"]),
                    "unique_user_count",
                    merchant_id,
                ),
                customer_ids=_parse_customer_ids(str(row["customer_ids"]), merchant_id),
                latitude=_parse_optional_float(row.get("latitude"), "latitude", merchant_id),
                longitude=_parse_optional_float(row.get("longitude"), "longitude", merchant_id),
            )
        )
    return candidates


def write_csv(data: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(path, index=False, encoding="utf-8-sig")


def create_run_directory(output_root: Path, started_at: datetime) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    name = f"incremental_assignment_{started_at.strftime('%y%m%d%H%M%S')}"
    run_directory = output_root / name
    if run_directory.exists():
        raise AlgorithmError(f"运行目录已存在，拒绝覆盖: path={run_directory}")
    run_directory.mkdir(parents=True)
    return run_directory


def read_optional_coordinates(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame(columns=sorted(COORDINATE_COLUMNS))
    return read_csv_checked(path, COORDINATE_COLUMNS, "商户坐标表")
