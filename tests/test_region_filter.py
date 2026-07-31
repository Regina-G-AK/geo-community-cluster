import pandas as pd
import pytest

from business_district.errors import TransactionDataError
from business_district.region_filter import build_storename_city_mask


def test_build_storename_city_mask_keeps_only_shanghai_city_names() -> None:
    storenames = pd.Series(
        [
            "普通商户",
            "上海市浦东新区商户",
            "北京市朝阳区商户",
            "上海区北京市商户",
        ]
    )

    mask = build_storename_city_mask(storenames, "上海")

    assert mask.tolist() == [True, True, False, False]


def test_build_storename_city_mask_supports_lanzhou_and_jiuquan() -> None:
    storenames = pd.Series(
        [
            "兰州市城关区商户",
            "酒泉市肃州区商户",
            "西安市雁塔区商户",
            "普通商户",
        ]
    )

    mask = build_storename_city_mask(storenames, "兰州")

    assert mask.tolist() == [True, True, False, True]


def test_build_storename_city_mask_rejects_unconfigured_region() -> None:
    with pytest.raises(
        TransactionDataError,
        match="region 没有配置商户名称城市筛选规则",
    ):
        build_storename_city_mask(pd.Series(["普通商户"]), "未配置分行")
