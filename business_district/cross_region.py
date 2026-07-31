from __future__ import annotations

import datetime
from typing import List, Set

import pandas as pd

from business_district.errors import TransactionDataError
from business_district.status_codes import format_status_code
from business_district.transactions import (
    RAW_MERCHANT,
    RAW_MERCHANT_CATEGORY,
    REGION,
)

CROSS_REGION_TARGET_COLUMNS = [
    "storename",
    "community_id",
    "previous_community_id",
    "region",
    "is_interfere",
    "update_time",
    "is_abnormal",
    "is_position",
    "dt",
]


def _normalize_merchant_categories(
    source_data: pd.DataFrame,
    source_table: str,
) -> pd.Series:
    category_text = source_data[RAW_MERCHANT_CATEGORY].astype("string").str.strip()
    categories = pd.to_numeric(category_text, errors="coerce")
    invalid = (
        categories.isna()
        | categories.mod(1).ne(0)
        | ~categories.isin({0, 1, 2, 3})
    )
    if invalid.any():
        examples: List[str] = category_text.loc[invalid].head(5).tolist()
        raise TransactionDataError(
            "Hive 输入表商户分类必须是 0、1、2 或 3: "
            f"table={source_table}, invalid_rows={int(invalid.sum())}, "
            f"examples={examples}"
        )
    return categories.astype(int)


def build_cross_region_target_output(
    cross_region_source_data: pd.DataFrame,
    included_source_data: pd.DataFrame,
    source_table: str,
    output_dt: str,
    update_time: datetime.datetime,
) -> pd.DataFrame:
    if cross_region_source_data.empty:
        return pd.DataFrame(columns=CROSS_REGION_TARGET_COLUMNS)

    required_columns = {RAW_MERCHANT, RAW_MERCHANT_CATEGORY, REGION}
    missing_columns = sorted(
        required_columns.difference(set(cross_region_source_data.columns))
    )
    if missing_columns:
        raise TransactionDataError(
            "疑似跨区域商户输入缺少必要字段: "
            f"table={source_table}, missing_columns={missing_columns}"
        )
    included_required_columns = {RAW_MERCHANT, RAW_MERCHANT_CATEGORY}
    included_missing_columns = sorted(
        included_required_columns.difference(set(included_source_data.columns))
    )
    if included_missing_columns:
        raise TransactionDataError(
            "正常商户输入缺少必要字段，无法排除已参与聚类的商户: "
            f"table={source_table}, missing_columns={included_missing_columns}"
        )

    source = cross_region_source_data.copy()
    source[RAW_MERCHANT] = source[RAW_MERCHANT].astype("string").str.strip()
    source[REGION] = source[REGION].astype("string").str.strip()
    invalid_identifier = (
        source[RAW_MERCHANT].isna()
        | source[RAW_MERCHANT].eq("")
        | source[REGION].isna()
        | source[REGION].eq("")
    )
    if invalid_identifier.any():
        examples = source.loc[
            invalid_identifier,
            [RAW_MERCHANT, REGION],
        ].head(5).to_dict("records")
        raise TransactionDataError(
            "疑似跨区域商户包含空店名或地区: "
            f"table={source_table}, invalid_rows={int(invalid_identifier.sum())}, "
            f"examples={examples}"
        )

    source[RAW_MERCHANT_CATEGORY] = _normalize_merchant_categories(
        source,
        source_table,
    )
    category_counts = source.groupby(
        RAW_MERCHANT,
        sort=True,
    )[RAW_MERCHANT_CATEGORY].nunique()
    conflicted_storenames = category_counts.loc[
        category_counts > 1
    ].index.astype(str).tolist()
    if conflicted_storenames:
        raise TransactionDataError(
            "疑似跨区域的同一商户对应多个商户分类: "
            f"table={source_table}, merchants={conflicted_storenames[:10]}"
        )

    included_source = included_source_data.copy()
    included_source[RAW_MERCHANT_CATEGORY] = _normalize_merchant_categories(
        included_source,
        source_table,
    )
    included_storenames: Set[str] = set(
        included_source.loc[
            included_source[RAW_MERCHANT_CATEGORY].isin({1, 2}),
            RAW_MERCHANT,
        ]
        .astype("string")
        .str.strip()
        .dropna()
        .tolist()
    )
    eligible = source.loc[
        source[RAW_MERCHANT_CATEGORY].isin({1, 2})
        & ~source[RAW_MERCHANT].isin(included_storenames)
    ].copy()
    eligible = eligible.drop_duplicates(
        subset=[RAW_MERCHANT],
        keep="last",
    ).sort_values(RAW_MERCHANT)

    timestamp = update_time.strftime("%Y-%m-%d %H:%M:%S")
    output = pd.DataFrame(
        {
            "storename": eligible[RAW_MERCHANT],
            "community_id": "",
            "previous_community_id": "",
            "region": eligible[REGION],
            "is_interfere": "N",
            "update_time": timestamp,
            "is_abnormal": format_status_code("suspect_cross_region"),
            "is_position": 0,
            "dt": str(output_dt),
        }
    )
    return output[CROSS_REGION_TARGET_COLUMNS].reset_index(drop=True)


def append_cross_region_target_output(
    target_output: pd.DataFrame,
    cross_region_output: pd.DataFrame,
) -> pd.DataFrame:
    if cross_region_output.empty:
        return target_output.reset_index(drop=True)
    existing_storenames: Set[str] = set(
        target_output["storename"].astype(str).tolist()
    )
    new_cross_region_rows = cross_region_output.loc[
        ~cross_region_output["storename"].isin(existing_storenames)
    ]
    return pd.concat(
        [target_output, new_cross_region_rows],
        ignore_index=True,
        copy=False,
    )
