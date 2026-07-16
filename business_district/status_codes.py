from __future__ import annotations

from typing import Dict, FrozenSet

import pandas as pd

STATUS_CODE_BY_NAME: Dict[str, str] = {
    "active": "1",
    "normal": "1",
    "suspect_online": "2",
    "suspect_isolated": "3",
    "suspect_lost": "4",
    "suspect_cross_region": "5",
    "suspect_chain_store": "6",
    "deleted": "7",
}
NORMAL_STATUS_CODES: FrozenSet[str] = frozenset({"1"})
NORMAL_STATUS_NAMES: FrozenSet[str] = frozenset({"active", "normal", "正常"})


def format_status_code(value: object) -> str:
    text = _clean_status_text(value)
    if text in STATUS_CODE_BY_NAME.values():
        return text
    if text in STATUS_CODE_BY_NAME:
        return STATUS_CODE_BY_NAME[text]
    raise ValueError(
        f"未知商户状态，无法转换 is_abnormal 状态码: value={text!r}, "
        f"allowed_statuses={sorted(STATUS_CODE_BY_NAME)}, "
        f"allowed_codes={sorted(set(STATUS_CODE_BY_NAME.values()))}"
    )


def is_normal_status(value: object) -> bool:
    text = _clean_status_text(value)
    return text in NORMAL_STATUS_CODES or text in NORMAL_STATUS_NAMES


def _clean_status_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()
