from __future__ import annotations

from pathlib import Path

import pandas as pd

from business_district.config import InputConfig, VisitConfig
from business_district.errors import TransactionDataError

CARD = "card_id"
MERCHANT = "merchant_id"
TIMESTAMP = "timestamp"
RAW_CARD_INDEX = 0
RAW_MERCHANT_NAME_INDEX = 2
RAW_TIMESTAMP_INDEX = 3


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
        raise TransactionDataError(f"交易文件不存在: {path}")

    try:
        result = pd.read_csv(
            path,
            sep="|",
            header=None,
            dtype="string",
            encoding="utf-8-sig",
            usecols=[RAW_CARD_INDEX, RAW_MERCHANT_NAME_INDEX, RAW_TIMESTAMP_INDEX],
            names=[CARD, MERCHANT, TIMESTAMP],
        )
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as error:
        raise TransactionDataError(
            "交易文件格式错误: "
            f"path={path}, expected=卡号|流水单号|店名|时间戳|||地区|||, reason={error}"
        ) from error
    result = result.copy()
    result[CARD] = result[CARD].str.strip()
    result[MERCHANT] = result[MERCHANT].str.strip()

    invalid_identifier = (
        result[CARD].isna()
        | result[MERCHANT].isna()
        | result[CARD].eq("")
        | result[MERCHANT].eq("")
    )
    if invalid_identifier.any():
        examples = result.loc[invalid_identifier].head(5).to_dict(orient="records")
        raise TransactionDataError(
            "交易文件包含空卡号或空店名: "
            f"path={path}, invalid_rows={int(invalid_identifier.sum())}, examples={examples}"
        )

    parsed_timestamps = _parse_timestamps(result[TIMESTAMP], config.timestamp_formats)
    invalid_timestamp = parsed_timestamps.isna()
    if invalid_timestamp.any():
        examples = result.loc[invalid_timestamp, TIMESTAMP].head(5).tolist()
        raise TransactionDataError(
            "交易时间解析失败: "
            f"path={path}, invalid_rows={int(invalid_timestamp.sum())}, examples={examples}, "
            f"formats={list(config.timestamp_formats)}"
        )

    result[TIMESTAMP] = parsed_timestamps
    result = result.drop_duplicates(subset=[CARD, MERCHANT, TIMESTAMP], keep="first")
    return result.sort_values([CARD, TIMESTAMP, MERCHANT]).reset_index(drop=True)


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
