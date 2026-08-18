from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from business_district.config import InputConfig, VisitConfig
from business_district.errors import TransactionDataError
from business_district.probes import print_dataframe_probe, print_probe, print_series_probe

CARD = "card_id"
MERCHANT = "merchant_id"
SOURCE_MERCHANT = "source_merchant_id"
TIMESTAMP = "timestamp"
FLOW_NUMBER = "global_flow_number"
REGION = "region"
DT = "dt"
LONGITUDE = "longitude"
LATITUDE = "latitude"
MERCHANT_CATEGORY = "merchant_category"
RAW_CARD = "account_number"
RAW_MERCHANT = "storename"
RAW_TIMESTAMP = "transaction_time"
RAW_LONGITUDE = "pos_longitude"
RAW_LATITUDE = "pos_latitude"
RAW_INTERFERE = "is_interfere"
RAW_ABNORMAL = "is_abnormal"
RAW_BUSINESS_DISTRICT = "business_district"
RAW_MERCHANT_CATEGORY = "merchant_category"
HIVE_SOURCE_COLUMNS = (
    RAW_CARD,
    FLOW_NUMBER,
    RAW_MERCHANT,
    RAW_MERCHANT_CATEGORY,
    RAW_TIMESTAMP,
    RAW_LONGITUDE,
    RAW_LATITUDE,
    REGION,
    RAW_INTERFERE,
    RAW_ABNORMAL,
    RAW_BUSINESS_DISTRICT,
)
REQUIRED_COLUMNS = {
    RAW_CARD,
    FLOW_NUMBER,
    RAW_MERCHANT,
    RAW_TIMESTAMP,
    RAW_LONGITUDE,
    RAW_LATITUDE,
    REGION,
    "is_intefere",
    "status",
    DT,
}
HIVE_REQUIRED_COLUMNS = set(HIVE_SOURCE_COLUMNS).union({DT})

CLUSTERING_MERCHANT_CATEGORIES = frozenset({1, 2})


def preserve_storename(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _parse_timestamps(values: pd.Series, formats: Tuple[str, ...]) -> pd.Series:
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    # text_values = values.astype("string").str.strip()
    for timestamp_format in formats:
        missing = parsed.isna()
        if not missing.any():
            break
        parsed.loc[missing] = pd.to_datetime(
            values.loc[missing],
            format=timestamp_format,
            errors="coerce",
        )
    return parsed


def filter_clustering_merchant_categories(
    dataframe: pd.DataFrame,
    source_name: str,
) -> pd.DataFrame:
    if RAW_MERCHANT_CATEGORY not in dataframe.columns:
        raise TransactionDataError(
            "Hive 输入表缺少商户分类字段: "
            f"table={source_name}, missing_column={RAW_MERCHANT_CATEGORY}"
        )
    # print_series_probe(
    #     "Hive商户分类清理开始",
    #     dataframe[RAW_MERCHANT_CATEGORY],
    # )
    category_text = dataframe[RAW_MERCHANT_CATEGORY].astype("string").str.strip()
    # print_series_probe("Hive商户分类清理完成", category_text)
    categories = pd.to_numeric(category_text, errors="coerce")
    invalid = categories.isna() | categories.mod(1).ne(0) | ~categories.isin({0, 1, 2, 3})
    if invalid.any():
        examples = category_text.loc[invalid].head(5).tolist()
        raise TransactionDataError(
            "Hive 输入表商户分类必须是 0、1、2 或 3: "
            f"table={source_name}, invalid_rows={int(invalid.sum())}, examples={examples}"
        )
    conflicts = (
        categories.groupby(dataframe[RAW_MERCHANT], sort=True)
        .nunique()
    )
    conflicted_merchants = conflicts.loc[conflicts > 1].index.astype(str).tolist()
    if conflicted_merchants:
        raise TransactionDataError(
            "Hive 输入表同一商户对应多个商户分类: "
            f"table={source_name}, merchants={conflicted_merchants[:10]}"
        )
    eligible = categories.isin(CLUSTERING_MERCHANT_CATEGORIES)
    filtered = dataframe.loc[eligible].copy()
    filtered[RAW_MERCHANT_CATEGORY] = categories.loc[eligible].astype(int)
    if filtered.empty:
        raise TransactionDataError(
            "Hive 输入表没有可用于聚类的线下商户交易: "
            f"table={source_name}, allowed_categories={sorted(CLUSTERING_MERCHANT_CATEGORIES)}"
        )
    return filtered


def keep_first_hive_flow_number_rows(selected: pd.DataFrame) -> pd.DataFrame:
    ordered = selected.sort_values([FLOW_NUMBER, DT, RAW_TIMESTAMP], kind="stable")
    ordered.drop_duplicates(subset=[FLOW_NUMBER], keep="first", inplace=True)
    return ordered


def _parse_coordinates(selected: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    longitude_text = selected[RAW_LONGITUDE]
    latitude_text = selected[RAW_LATITUDE]
    longitude_empty = longitude_text.isna() | longitude_text.eq("")
    latitude_empty = latitude_text.isna() | latitude_text.eq("")
    partial_coordinate = longitude_empty != latitude_empty
    complete_coordinate = (~longitude_empty) & (~latitude_empty)
    longitude = pd.to_numeric(
        longitude_text.mask(~complete_coordinate),
        errors="coerce",
    )
    latitude = pd.to_numeric(
        latitude_text.mask(~complete_coordinate),
        errors="coerce",
    )
    valid_number = complete_coordinate & longitude.notna() & latitude.notna()
    valid_range = (
        valid_number
        & longitude.ge(-180.0)
        & longitude.le(180.0)
        & latitude.ge(-90.0)
        & latitude.le(90.0)
    )
    valid_coordinate = (~partial_coordinate) & valid_range

    return (
        longitude.where(valid_coordinate).astype("Float64"),
        latitude.where(valid_coordinate).astype("Float64"),
    )


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

    # source.columns = source.columns.astype("string").str.strip()
    missing_columns = sorted(REQUIRED_COLUMNS.difference(set(source.columns)))
    if missing_columns:
        raise TransactionDataError(
            "交易文件缺少必要字段: "
            f"path={path}, missing_columns={missing_columns}"
        )

    selected_columns = [
        RAW_CARD,
        FLOW_NUMBER,
        RAW_MERCHANT,
        RAW_TIMESTAMP,
        RAW_LONGITUDE,
        RAW_LATITUDE,
        REGION,
        DT,
    ]
    unused_columns = [
        column for column in source.columns if column not in selected_columns
    ]
    if unused_columns:
        source.drop(columns=unused_columns, inplace=True)
    selected = (
        source.reindex(columns=selected_columns, copy=False)
        if list(source.columns) != selected_columns
        else source
    )
    del source
    selected[RAW_CARD] = selected[RAW_CARD].str.strip()
    selected[FLOW_NUMBER] = selected[FLOW_NUMBER].str.strip()
    selected[RAW_MERCHANT] = selected[RAW_MERCHANT].astype("string")
    selected[RAW_LONGITUDE] = selected[RAW_LONGITUDE].str.strip()
    selected[RAW_LATITUDE] = selected[RAW_LATITUDE].str.strip()
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

    longitude, latitude = _parse_coordinates(selected)
    selected[LONGITUDE] = longitude
    selected[LATITUDE] = latitude
    del longitude, latitude

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

    selected.rename(
        columns={
            RAW_CARD: CARD,
            RAW_MERCHANT: MERCHANT,
            RAW_TIMESTAMP: TIMESTAMP,
        },
        inplace=True,
    )
    selected[SOURCE_MERCHANT] = selected[MERCHANT]
    selected[TIMESTAMP] = parsed_timestamps
    selected.sort_values([CARD, TIMESTAMP, MERCHANT], inplace=True)
    selected.reset_index(drop=True, inplace=True)
    return selected


def load_hive_transactions(
    dataframe: pd.DataFrame,
    timestamp_formats: Tuple[str, ...],
    dt_value: str,
    source_name: str,
) -> pd.DataFrame:
    # print_dataframe_probe("Hive交易加载输入", dataframe)
    # print_probe("Hive交易列名清理开始", f"columns_type={type(source.columns).__name__}")
    # source.columns = source.columns.astype("string").str.strip()
    # print_dataframe_probe("Hive交易列名清理完成", source)
    required_columns = set(HIVE_SOURCE_COLUMNS)
    missing_columns = sorted(required_columns.difference(set(dataframe.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 输入表缺少必要字段: "
            f"table={source_name}, missing_columns={missing_columns}"
        )

    # print_probe("Hive商户分类过滤开始", f"row_count={len(source)}")
    selected = filter_clustering_merchant_categories(dataframe, source_name)
    # print_dataframe_probe("Hive商户分类过滤完成", source)
    unused_columns = [
        column
        for column in selected.columns
        if column not in required_columns and column != DT
    ]
    if unused_columns:
        selected.drop(columns=unused_columns, inplace=True)
    if DT not in selected.columns:
        selected[DT] = dt_value
    ordered_columns = list(HIVE_SOURCE_COLUMNS) + [DT]
    if list(selected.columns) != ordered_columns:
        selected = selected.reindex(columns=ordered_columns, copy=False)
    selected[RAW_CARD] = selected[RAW_CARD].astype("string").str.strip()
    selected[FLOW_NUMBER] = selected[FLOW_NUMBER].astype("string").str.strip()
    selected[RAW_MERCHANT] = selected[RAW_MERCHANT].astype("string")
    selected[RAW_TIMESTAMP] = selected[RAW_TIMESTAMP].astype("string").str.strip()
    selected[RAW_LONGITUDE] = selected[RAW_LONGITUDE].astype("string").str.strip()
    selected[RAW_LATITUDE] = selected[RAW_LATITUDE].astype("string").str.strip()
    selected[REGION] = selected[REGION].astype("string").str.strip()
    selected[RAW_INTERFERE] = selected[RAW_INTERFERE].astype("string").str.strip()
    selected[RAW_ABNORMAL] = selected[RAW_ABNORMAL].astype("string").str.strip()
    selected[RAW_BUSINESS_DISTRICT] = (
        selected[RAW_BUSINESS_DISTRICT].astype("string").str.strip()
    )
    selected[RAW_MERCHANT_CATEGORY] = selected[RAW_MERCHANT_CATEGORY].astype(int)
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

    # print_probe("Hive坐标解析开始", f"row_count={len(selected)}")
    longitude, latitude = _parse_coordinates(selected)
    selected[LONGITUDE] = longitude
    selected[LATITUDE] = latitude
    del longitude, latitude
    # print_dataframe_probe("Hive坐标解析完成", selected)
    selected = keep_first_hive_flow_number_rows(selected)

    # print_probe("Hive交易时间解析开始", f"row_count={len(selected)}")
    parsed_timestamps = _parse_timestamps(
        selected[RAW_TIMESTAMP],
        timestamp_formats,
    )
    # print_series_probe("Hive交易时间解析完成", parsed_timestamps)
    invalid_timestamp = parsed_timestamps.isna()
    if invalid_timestamp.any():
        examples = selected.loc[invalid_timestamp, RAW_TIMESTAMP].head(5).tolist()
        raise TransactionDataError(
            "Hive 输入表交易时间解析失败: "
            f"table={source_name}, invalid_rows={int(invalid_timestamp.sum())}, "
            f"examples={examples}, formats={list(timestamp_formats)}"
        )

    selected.rename(
        columns={
            RAW_CARD: CARD,
            RAW_MERCHANT: MERCHANT,
            RAW_TIMESTAMP: TIMESTAMP,
            RAW_MERCHANT_CATEGORY: MERCHANT_CATEGORY,
        },
        inplace=True,
    )
    selected[SOURCE_MERCHANT] = selected[MERCHANT]
    selected[TIMESTAMP] = parsed_timestamps
    selected.sort_values([CARD, TIMESTAMP, MERCHANT], inplace=True)
    selected.reset_index(drop=True, inplace=True)
    # print_dataframe_probe("Hive交易加载完成", ordered)
    return selected


def build_merchant_metadata(transactions: pd.DataFrame) -> pd.DataFrame:
    metadata_columns = [MERCHANT, REGION, DT]
    if MERCHANT_CATEGORY in transactions.columns:
        metadata_columns.append(MERCHANT_CATEGORY)
    if SOURCE_MERCHANT in transactions.columns:
        metadata_columns.append(SOURCE_MERCHANT)
    ordered = transactions[[TIMESTAMP] + metadata_columns].sort_values(
        [MERCHANT, TIMESTAMP, REGION, DT]
    )
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
    result = latest_rows.drop_duplicates(subset=[MERCHANT], keep="last")
    result.drop(columns=[TIMESTAMP], inplace=True)
    result.reset_index(drop=True, inplace=True)
    return result


def merge_visits(transactions: pd.DataFrame, config: VisitConfig) -> pd.DataFrame:
    merge_window = pd.Timedelta(minutes=config.merge_window_minutes)
    visit_rows: List[Tuple[str, str, pd.Timestamp]] = []

    for card_id, group in transactions.groupby(CARD, sort=False):
        last_timestamp_by_merchant: Dict[str, pd.Timestamp] = {}
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
    del visit_rows
    visits["visit_date"] = visits[TIMESTAMP].dt.normalize()
    daily_counts = (
        visits.groupby([CARD, "visit_date"])[MERCHANT]
        .nunique()
        .rename("daily_merchant_count")
    )
    visits = visits.join(daily_counts, on=[CARD, "visit_date"])
    del daily_counts
    valid = (
        visits["daily_merchant_count"]
        <= config.maximum_daily_merchants_per_card
    )
    visits.drop(index=visits.index[~valid], inplace=True)
    del valid
    visits.drop(columns=["visit_date", "daily_merchant_count"], inplace=True)
    visits.sort_values([CARD, TIMESTAMP, MERCHANT], inplace=True)
    visits.reset_index(drop=True, inplace=True)
    return visits
