import datetime

import pandas as pd

from business_district.cross_region import build_cross_region_target_output


def test_build_cross_region_target_output_uses_status_code_and_normal_wins() -> None:
    cross_region_source = pd.DataFrame(
        [
            {
                "storename": "北京市朝阳区商户",
                "merchant_category": "1",
                "region": "上海",
            },
            {
                "storename": "南京市鼓楼区商户",
                "merchant_category": "2",
                "region": "上海",
            },
            {
                "storename": "分类零商户",
                "merchant_category": "0",
                "region": "上海",
            },
        ]
    )
    included_source = pd.DataFrame(
        [
            {
                "storename": "南京市鼓楼区商户",
                "merchant_category": "2",
                "region": "南京",
            }
        ]
    )

    output = build_cross_region_target_output(
        cross_region_source,
        included_source,
        "source_table",
        "20260101",
        datetime.datetime(2026, 1, 2, 10, 30, 0),
    )

    assert output.to_dict("records") == [
        {
            "storename": "北京市朝阳区商户",
            "community_id": "",
            "previous_community_id": "",
            "region": "上海",
            "is_interfere": "N",
            "update_time": "2026-01-02 10:30:00",
            "is_abnormal": "5",
            "is_position": 0,
            "dt": "20260101",
        }
    ]


def test_build_cross_region_target_output_preserves_storename_whitespace() -> None:
    cross_region_source = pd.DataFrame(
        [
            {
                "storename": " shop ",
                "merchant_category": "1",
                "region": "shanghai",
            }
        ]
    )
    included_source = pd.DataFrame(
        [
            {
                "storename": "shop",
                "merchant_category": "1",
                "region": "shanghai",
            }
        ]
    )

    output = build_cross_region_target_output(
        cross_region_source,
        included_source,
        "source_table",
        "20260101",
        datetime.datetime(2026, 1, 2, 10, 30, 0),
    )

    assert output["storename"].tolist() == [" shop "]
