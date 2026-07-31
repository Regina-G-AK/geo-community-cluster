from __future__ import annotations

from types import MappingProxyType
from typing import List, Mapping, Tuple

import pandas as pd

from business_district.errors import TransactionDataError

RegionCityLabels = Mapping[str, Tuple[str, ...]]

REGION_CITY_LABELS: RegionCityLabels = MappingProxyType(
    {
        "上海": ("上海",),
        "shanghai": ("上海",),
        "乌鲁木齐": ("乌鲁木齐", "喀什", "昌吉市", "图木舒克", "阿克苏"),
        "兰州": ("兰州", "酒泉"),
        "北京": ("北京",),
        "南京": (
            "连云港",
            "南京",
            "镇江",
            "无锡",
            "泰州",
            "扬州",
            "徐州",
            "盐城",
            "淮安",
            "江阴",
            "南通",
            "宿迁",
            "常州",
        ),
        "南宁": (
            "南宁",
            "柳州",
            "桂林",
            "吉安市",
            "赣州",
            "九江",
            "南昌",
            "抚州",
            "宜春",
            "上饶",
        ),
        "厦门": ("自贸厦门片区", "厦门", "龙岩市（地级市）"),
        "合肥": (
            "安庆",
            "马鞍山市",
            "芜湖",
            "阜阳",
            "合肥",
            "铜陵",
            "宣城",
            "滁州",
            "蚌埠",
            "淮南",
        ),
        "呼和浩特": ("包头", "鄂尔多斯", "呼伦贝尔", "呼和浩特"),
        "哈尔滨": (
            "齐齐哈尔",
            "牡丹江",
            "绥化",
            "鸡西",
            "佳木斯",
            "黑河",
            "大庆",
            "哈尔滨",
            "七台河",
            "大兴安岭",
            "伊春",
            "双鸭山",
            "鹤岗",
        ),
        "大连": ("鞍山", "营口", "大连", "丹东", "锦州"),
        "天津": ("天津", "天津自由贸易试验区"),
        "太原": (
            "晋中",
            "晋城",
            "朔州",
            "太原",
            "运城",
            "大同",
            "忻州市",
            "长治",
        ),
        "宁波": ("宁波", "台州"),
        "广州": ("佛山", "南沙市", "广州", "中山", "惠州", "肇庆", "东莞", "江门"),
        "成都": (
            "广元",
            "资阳",
            "凉山市",
            "攀枝花",
            "乐山市",
            "成都",
            "绵阳",
            "自贡",
            "宜宾",
            "南充",
            "巴中",
            "德阳",
            "泸州",
            "雅安市",
            "遂宁",
            "广安",
            "眉山",
            "达州",
            "内江",
        ),
        "拉萨": ("拉萨", "日喀则"),
        "昆明": ("保山市", "楚雄", "昆明", "玉溪", "曲靖"),
        "杭州": (
            "金华",
            "丽水",
            "舟山",
            "绍兴",
            "义乌市",
            "温州",
            "杭州",
            "湖州市",
            "嘉兴",
            "衢州市",
        ),
        "武汉": (
            "武汉",
            "襄阳",
            "荆门市",
            "十堰市",
            "宜昌",
            "恩施市",
            "仙桃市",
            "随州市",
            "孝感市",
            "咸宁市",
            "潜江市",
            "神龙架林区",
            "天门市",
            "鄂州市",
            "荆州",
            "荆州市",
        ),
        "沈阳": ("盘锦市", "葫芦岛", "辽阳", "铁岭", "本溪", "沈阳"),
        "济南": (
            "济宁",
            "菏泽",
            "日照",
            "莱芜",
            "枣庄",
            "临沂",
            "淄博",
            "滨州",
            "泰安",
            "德州",
            "东营",
            "济南",
            "聊城",
            "潍坊",
        ),
        "海口": ("三亚市", "海口"),
        "深圳": ("湛江", "茂名", "珠海", "横琴市", "深圳", "前海市", "深圳特别合作区"),
        "石家庄": (
            "秦皇岛",
            "唐山",
            "邯郸",
            "衡水市",
            "廊坊",
            "沧州",
            "保定",
            "石家庄",
            "邢台",
            "张家口",
            "承德",
        ),
        "福州": ("漳州", "福州", "莆田", "马尾区", "泉州"),
        "苏州": ("张家港", "常熟", "昆山", "苏州", "太仓"),
        "西宁": ("西宁",),
        "西安": ("宝鸡市", "安康市", "渭南", "汉中市", "延安市", "西安", "咸阳市", "榆林"),
        "贵阳": (
            "贵阳",
            "黔东南州",
            "安顺市",
            "铜仁市",
            "黔西南州",
            "遵义",
            "毕节市",
            "黔南州",
            "六盘水市",
        ),
        "郑州": ("新乡", "安阳", "洛阳", "信阳市", "开封", "南阳市", "许昌", "郑州", "商丘"),
        "重庆": ("黔江", "涪陵", "万州", "重庆"),
        "银川": ("银川",),
        "长春": ("吉林市", "长春"),
        "长沙": ("衡阳市", "常德市", "株洲", "郴州", "湘潭", "长沙", "岳阳市"),
        "青岛": ("青岛", "烟台", "威海"),
    }
)


def _normalize_city_label(city_label: str) -> str:
    city_index = city_label.find("市")
    if city_index >= 0:
        return city_label[: city_index + 1]
    if city_label.endswith(("区", "州")) or city_label == "大兴安岭":
        return city_label
    return f"{city_label}市"


def _build_city_keywords(city_labels: Tuple[str, ...]) -> Tuple[str, ...]:
    keywords: List[str] = []
    for city_label in city_labels:
        keyword = _normalize_city_label(city_label)
        if keyword not in keywords:
            keywords.append(keyword)
    return tuple(keywords)


def build_storename_city_mask(storenames: pd.Series, region: str) -> pd.Series:
    region_text = region.strip()
    if region_text not in REGION_CITY_LABELS:
        raise TransactionDataError(
            "参数表 region 没有配置商户名称城市筛选规则: "
            f"region={region_text!r}, supported_regions={sorted(REGION_CITY_LABELS)}"
        )

    names = storenames.astype("string")
    contains_city = names.str.contains("市", regex=False, na=False)
    contains_allowed_city = pd.Series(False, index=storenames.index)
    for city_keyword in _build_city_keywords(REGION_CITY_LABELS[region_text]):
        contains_allowed_city = contains_allowed_city | names.str.contains(
            city_keyword,
            regex=False,
            na=False,
        )
    return ~contains_city | contains_allowed_city
