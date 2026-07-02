from __future__ import annotations

from pathlib import Path

import pandas as pd

from business_district.config import InputConfig, VisitConfig
from business_district.errors import TransactionDataError

CARD = "card_id"
MERCHANT = "merchant_id"
TIMESTAMP = "timestamp"
FLOW_NUMBER = "global_flow_number"
REGION = "region"
DT = "dt"
RAW_CARD = "account_number"
RAW_MERCHANT = "storename"
RAW_TIMESTAMP = "transaction_time"
RAW_INTERFERE = "is_interfere"
RAW_ABNORMAL = "is_abnormal"
REQUIRED_COLUMNS = {
    RAW_CARD,
    FLOW_NUMBER,
    RAW_MERCHANT,
    RAW_TIMESTAMP,
    "pos_longitude",
    "pos_latitude",
    REGION,
    "is_intefere",
    "status",
    DT,
}
HIVE_REQUIRED_COLUMNS = {
    RAW_CARD,
    FLOW_NUMBER,
    RAW_MERCHANT,
    RAW_TIMESTAMP,
    "pos_longitude",
    "pos_latitude",
    REGION,
    RAW_INTERFERE,
    RAW_ABNORMAL,
    DT,
}


def _parse_timestamps(values: pd.Series, formats: tuple[str, ...]) -> pd.Series:
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    text_values = values.astype("string").str.strip()
    for timestamp_format in formats:
        missing = parsed.isna()
        if not missing.any():
            break
        parsed.loc[missing] = pd.to_datetime(
            text_values.loc[missing],
            format=timestamp_format,
            errors="coerce",
        )
    return parsed


def load_transactions(config: InputConfig) -> pd.DataFrame:
    path: Path = config.transactions_path
    if not path.exists():
        raise TransactionDataError(f"交易文件不存在: path={path}")

    try:
        source = pd.read_csv(
            path,
            sep="|",
            header=0,
            dtype="string",
            encoding="utf-8-sig",
        )
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as error:
        raise TransactionDataError(
            "交易文件格式错误: "
            f"path={path}, required_columns={sorted(REQUIRED_COLUMNS)}, reason={error}"
        ) from error

    source.columns = source.columns.astype("string").str.strip()
    missing_columns = sorted(REQUIRED_COLUMNS.difference(set(source.columns)))
    if missing_columns:
        raise TransactionDataError(
            "交易文件缺少必要字段: "
            f"path={path}, missing_columns={missing_columns}"
        )

    selected = source[
        [RAW_CARD, FLOW_NUMBER, RAW_MERCHANT, RAW_TIMESTAMP, REGION, DT]
    ].copy()
    selected[RAW_CARD] = selected[RAW_CARD].str.strip()
    selected[FLOW_NUMBER] = selected[FLOW_NUMBER].str.strip()
    selected[RAW_MERCHANT] = selected[RAW_MERCHANT].str.strip()
    selected[REGION] = selected[REGION].str.strip()
    selected[DT] = selected[DT].str.strip()

    invalid_identifier = (
        selected[RAW_CARD].isna()
        | selected[FLOW_NUMBER].isna()
        | selected[RAW_MERCHANT].isna()
        | selected[REGION].isna()
        | selected[DT].isna()
        | selected[RAW_CARD].eq("")
        | selected[FLOW_NUMBER].eq("")
        | selected[RAW_MERCHANT].eq("")
        | selected[REGION].eq("")
        | selected[DT].eq("")
    )
    if invalid_identifier.any():
        examples = selected.loc[invalid_identifier].head(5).to_dict(orient="records")
        raise TransactionDataError(
            "交易文件包含空卡号、流水号、店名、地区或日期: "
            f"path={path}, invalid_rows={int(invalid_identifier.sum())}, examples={examples}"
        )

    duplicate_flow_numbers = selected.loc[
        selected[FLOW_NUMBER].duplicated(keep=False),
        FLOW_NUMBER,
    ].head(10).tolist()
    if duplicate_flow_numbers:
        raise TransactionDataError(
            "交易文件包含重复流水号: "
            f"path={path}, examples={duplicate_flow_numbers}"
        )

    parsed_timestamps = _parse_timestamps(
        selected[RAW_TIMESTAMP],
        config.timestamp_formats,
    )
    invalid_timestamp = parsed_timestamps.isna()
    if invalid_timestamp.any():
        examples = selected.loc[invalid_timestamp, RAW_TIMESTAMP].head(5).tolist()
        raise TransactionDataError(
            "交易时间解析失败: "
            f"path={path}, invalid_rows={int(invalid_timestamp.sum())}, "
            f"examples={examples}, formats={list(config.timestamp_formats)}"
        )

    result = selected.rename(
        columns={
            RAW_CARD: CARD,
            RAW_MERCHANT: MERCHANT,
            RAW_TIMESTAMP: TIMESTAMP,
        }
    )
    result[TIMESTAMP] = parsed_timestamps
    return result.sort_values([CARD, TIMESTAMP, MERCHANT]).reset_index(drop=True)


def load_hive_transactions(
    dataframe: pd.DataFrame,
    timestamp_formats: tuple[str, ...],
    dt_value: str,
    source_name: str,
) -> pd.DataFrame:
    source = dataframe.copy()
    source.columns = source.columns.astype("string").str.strip()
    missing_columns = sorted(HIVE_REQUIRED_COLUMNS.difference(set(source.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 输入表缺少必要字段: "
            f"table={source_name}, missing_columns={missing_columns}"
        )

    selected = source[
        [RAW_CARD, FLOW_NUMBER, RAW_MERCHANT, RAW_TIMESTAMP, REGION, DT]
    ].copy()
    selected[RAW_CARD] = selected[RAW_CARD].astype("string").str.strip()
    selected[FLOW_NUMBER] = selected[FLOW_NUMBER].astype("string").str.strip()
    selected[RAW_MERCHANT] = selected[RAW_MERCHANT].astype("string").str.strip()
    selected[RAW_TIMESTAMP] = selected[RAW_TIMESTAMP].astype("string").str.strip()
    selected[REGION] = selected[REGION].astype("string").str.strip()
    selected[DT] = selected[DT].astype("string").str.strip()

    invalid_identifier = (
        selected[RAW_CARD].isna()
        | selected[FLOW_NUMBER].isna()
        | selected[RAW_MERCHANT].isna()
        | selected[REGION].isna()
        | selected[DT].isna()
        | selected[RAW_CARD].eq("")
        | selected[FLOW_NUMBER].eq("")
        | selected[RAW_MERCHANT].eq("")
        | selected[REGION].eq("")
        | selected[DT].eq("")
    )
    if invalid_identifier.any():
        examples = selected.loc[invalid_identifier].head(5).to_dict(orient="records")
        raise TransactionDataError(
            "Hive 输入表包含空卡号、流水号、店名、地区或日期: "
            f"table={source_name}, invalid_rows={int(invalid_identifier.sum())}, examples={examples}"
        )

    duplicate_flow_numbers = selected.loc[
        selected[FLOW_NUMBER].duplicated(keep=False),
        FLOW_NUMBER,
    ].head(10).tolist()
    if duplicate_flow_numbers:
        raise TransactionDataError(
            "Hive 输入表包含重复流水号: "
            f"table={source_name}, examples={duplicate_flow_numbers}"
        )

    parsed_timestamps = _parse_timestamps(
        selected[RAW_TIMESTAMP],
        timestamp_formats,
    )
    invalid_timestamp = parsed_timestamps.isna()
    if invalid_timestamp.any():
        examples = selected.loc[invalid_timestamp, RAW_TIMESTAMP].head(5).tolist()
        raise TransactionDataError(
            "Hive 输入表交易时间解析失败: "
            f"table={source_name}, invalid_rows={int(invalid_timestamp.sum())}, "
            f"examples={examples}, formats={list(timestamp_formats)}"
        )

    result = selected.rename(
        columns={
            RAW_CARD: CARD,
            RAW_MERCHANT: MERCHANT,
            RAW_TIMESTAMP: TIMESTAMP,
        }
    )
    result[TIMESTAMP] = parsed_timestamps
    return result.sort_values([CARD, TIMESTAMP, MERCHANT]).reset_index(drop=True)


def build_merchant_metadata(transactions: pd.DataFrame) -> pd.DataFrame:
    ordered = transactions.sort_values([MERCHANT, TIMESTAMP, REGION, DT])
    latest_timestamp = ordered.groupby(MERCHANT, sort=True)[TIMESTAMP].transform("max")
    latest_rows = ordered.loc[ordered[TIMESTAMP].eq(latest_timestamp)]
    conflicts = (
        latest_rows.groupby(MERCHANT, sort=True)[[REGION, DT]]
        .nunique()
        .max(axis=1)
    )
    conflicted_merchants = conflicts.loc[conflicts > 1].index.astype(str).tolist()
    if conflicted_merchants:
        raise TransactionDataError(
            "商户最新交易时间对应的地区或日期不唯一: "
            f"merchants={conflicted_merchants[:10]}"
        )
    return (
        latest_rows.drop_duplicates(subset=[MERCHANT], keep="last")[
            [MERCHANT, REGION, DT]
        ]
        .copy()
        .reset_index(drop=True)
    )


def merge_visits(transactions: pd.DataFrame, config: VisitConfig) -> pd.DataFrame:
    merge_window = pd.Timedelta(minutes=config.merge_window_minutes)
    visit_rows: list[tuple[str, str, pd.Timestamp]] = []

    for card_id, group in transactions.groupby(CARD, sort=False):
        last_timestamp_by_merchant: dict[str, pd.Timestamp] = {}
        for row in group.itertuples(index=False):
            merchant_id = str(getattr(row, MERCHANT))
            timestamp = pd.Timestamp(getattr(row, TIMESTAMP))
            previous_timestamp = last_timestamp_by_merchant.get(merchant_id)
            if (
                previous_timestamp is not None
                and timestamp - previous_timestamp < merge_window
            ):
                last_timestamp_by_merchant[merchant_id] = timestamp
                continue
            visit_rows.append((str(card_id), merchant_id, timestamp))
            last_timestamp_by_merchant[merchant_id] = timestamp

    visits = pd.DataFrame(visit_rows, columns=[CARD, MERCHANT, TIMESTAMP])
    visits["visit_date"] = visits[TIMESTAMP].dt.normalize()
    daily_counts = (
        visits.groupby([CARD, "visit_date"])[MERCHANT]
        .nunique()
        .rename("daily_merchant_count")
    )
    visits = visits.join(daily_counts, on=[CARD, "visit_date"])
    valid = (
        visits["daily_merchant_count"]
        <= config.maximum_daily_merchants_per_card
    )
    return (
        visits.loc[valid, [CARD, MERCHANT, TIMESTAMP]]
        .sort_values([CARD, TIMESTAMP, MERCHANT])
        .reset_index(drop=True)
    )
