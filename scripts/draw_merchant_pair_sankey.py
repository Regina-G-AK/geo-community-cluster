from __future__ import annotations

from html import escape
from pathlib import Path
from typing import List, NamedTuple, Sequence, Tuple


class SankeyInput(NamedTuple):
    total_merchant_count: int
    offline_merchant_count: int
    offline_non_individual_merchant_count: int
    paired_merchant_count: int
    effective_merchant_count: int
    circle_capable_merchant_count: int
    geographic_assigned_merchant_count: int
    pair_assigned_merchant_count: int
    total_transaction_count: int
    offline_transaction_count: int
    non_individual_transaction_count: int
    matched_circle_transaction_count: int
    filtered_circle_transaction_count: int
    final_circle_transaction_count: int
    pair_count: int
    effective_pair_count: int
    support_threshold: int


class SankeyIndicators(NamedTuple):
    non_offline_merchant_count: int
    individual_merchant_count: int
    unpaired_merchant_count: int
    low_support_merchant_count: int
    effective_without_circle_count: int
    offline_merchant_pct: float
    non_individual_merchant_pct: float
    paired_merchant_pct: float
    effective_merchant_pct: float
    circle_capable_of_effective_pct: float
    circle_capable_of_all_pct: float
    geographic_of_circle_pct: float
    pair_of_circle_pct: float
    offline_transaction_pct: float
    non_individual_transaction_pct: float
    matched_circle_transaction_pct: float
    filtered_circle_transaction_pct: float
    final_circle_transaction_pct: float
    ineffective_pair_count: int
    effective_pair_pct: float


OUTPUT_PATH = Path(__file__).resolve().with_name("merchant_pair_sankey.svg")
DATA = SankeyInput(
    total_merchant_count=2_734_405,
    offline_merchant_count=1_920_000,
    offline_non_individual_merchant_count=1_544_373,
    paired_merchant_count=461_097,
    effective_merchant_count=82_345,
    circle_capable_merchant_count=54_828,
    geographic_assigned_merchant_count=43_569,
    pair_assigned_merchant_count=11_259,
    total_transaction_count=163_000_000,
    offline_transaction_count=31_240_000,
    non_individual_transaction_count=22_670_000,
    matched_circle_transaction_count=8_030_000,
    filtered_circle_transaction_count=4_340_000,
    final_circle_transaction_count=3_690_000,
    pair_count=1_498_702,
    effective_pair_count=96_607,
    support_threshold=2,
)


def validate_nested_counts(
    flow_name: str,
    counts: Sequence[int],
) -> None:
    if len(counts) < 2:
        raise ValueError(
            f"{flow_name}至少需要两个阶段: counts={list(counts)}"
        )
    if any(count < 0 for count in counts):
        raise ValueError(
            f"{flow_name}各阶段数量不能为负数: counts={list(counts)}"
        )
    if counts[0] <= 0:
        raise ValueError(
            f"{flow_name}首阶段数量必须大于 0: counts={list(counts)}"
        )
    if any(
        current_count < next_count
        for current_count, next_count in zip(counts, counts[1:])
    ):
        raise ValueError(
            f"{flow_name}各阶段数量必须依次不增加: counts={list(counts)}"
        )


def calculate_indicators(data: SankeyInput) -> SankeyIndicators:
    merchant_counts = (
        data.total_merchant_count,
        data.offline_merchant_count,
        data.offline_non_individual_merchant_count,
        data.paired_merchant_count,
        data.effective_merchant_count,
        data.circle_capable_merchant_count,
    )
    transaction_counts = (
        data.total_transaction_count,
        data.offline_transaction_count,
        data.non_individual_transaction_count,
        data.matched_circle_transaction_count,
        data.filtered_circle_transaction_count,
        data.final_circle_transaction_count,
    )
    validate_nested_counts("商户桑基流", merchant_counts)
    validate_nested_counts("交易桑基流", transaction_counts)

    if (
        data.geographic_assigned_merchant_count < 0
        or data.pair_assigned_merchant_count < 0
        or (
            data.geographic_assigned_merchant_count
            + data.pair_assigned_merchant_count
            != data.circle_capable_merchant_count
        )
    ):
        raise ValueError(
            "能够形成商圈的商户必须完整拆分为经纬度添加和交易对添加: "
            f"circle_capable_merchant_count="
            f"{data.circle_capable_merchant_count}, "
            f"geographic_assigned_merchant_count="
            f"{data.geographic_assigned_merchant_count}, "
            f"pair_assigned_merchant_count="
            f"{data.pair_assigned_merchant_count}"
        )
    if data.pair_count <= 0:
        raise ValueError(
            f"交易对数量必须大于 0: pair_count={data.pair_count}"
        )
    if not 0 <= data.effective_pair_count <= data.pair_count:
        raise ValueError(
            "有效交易对数量必须位于 0 到交易对总数之间: "
            f"pair_count={data.pair_count}, "
            f"effective_pair_count={data.effective_pair_count}"
        )
    if data.support_threshold < 1:
        raise ValueError(
            "支持度必须是正整数: "
            f"support_threshold={data.support_threshold}"
        )

    return SankeyIndicators(
        non_offline_merchant_count=(
            data.total_merchant_count - data.offline_merchant_count
        ),
        individual_merchant_count=(
            data.offline_merchant_count
            - data.offline_non_individual_merchant_count
        ),
        unpaired_merchant_count=(
            data.offline_non_individual_merchant_count
            - data.paired_merchant_count
        ),
        low_support_merchant_count=(
            data.paired_merchant_count - data.effective_merchant_count
        ),
        effective_without_circle_count=(
            data.effective_merchant_count
            - data.circle_capable_merchant_count
        ),
        offline_merchant_pct=(
            100.0
            * data.offline_merchant_count
            / data.total_merchant_count
        ),
        non_individual_merchant_pct=(
            100.0
            * data.offline_non_individual_merchant_count
            / data.total_merchant_count
        ),
        paired_merchant_pct=(
            100.0
            * data.paired_merchant_count
            / data.total_merchant_count
        ),
        effective_merchant_pct=(
            100.0
            * data.effective_merchant_count
            / data.paired_merchant_count
        ),
        circle_capable_of_effective_pct=(
            100.0
            * data.circle_capable_merchant_count
            / data.effective_merchant_count
        ),
        circle_capable_of_all_pct=(
            100.0
            * data.circle_capable_merchant_count
            / data.total_merchant_count
        ),
        geographic_of_circle_pct=(
            100.0
            * data.geographic_assigned_merchant_count
            / data.circle_capable_merchant_count
        ),
        pair_of_circle_pct=(
            100.0
            * data.pair_assigned_merchant_count
            / data.circle_capable_merchant_count
        ),
        offline_transaction_pct=(
            100.0
            * data.offline_transaction_count
            / data.total_transaction_count
        ),
        non_individual_transaction_pct=(
            100.0
            * data.non_individual_transaction_count
            / data.total_transaction_count
        ),
        matched_circle_transaction_pct=(
            100.0
            * data.matched_circle_transaction_count
            / data.total_transaction_count
        ),
        filtered_circle_transaction_pct=(
            100.0
            * data.filtered_circle_transaction_count
            / data.total_transaction_count
        ),
        final_circle_transaction_pct=(
            100.0
            * data.final_circle_transaction_count
            / data.total_transaction_count
        ),
        ineffective_pair_count=data.pair_count - data.effective_pair_count,
        effective_pair_pct=(
            100.0 * data.effective_pair_count / data.pair_count
        ),
    )


def format_number(value: int) -> str:
    return f"{value:,}"


def format_wan(value: int) -> str:
    return f"{value / 10_000:,.0f}万"


def build_band_path(
    source_x: float,
    target_x: float,
    source_top: float,
    source_bottom: float,
    target_top: float,
    target_bottom: float,
) -> str:
    control_offset = (target_x - source_x) * 0.46
    return (
        f"M {source_x:.1f} {source_top:.1f} "
        f"C {source_x + control_offset:.1f} {source_top:.1f}, "
        f"{target_x - control_offset:.1f} {target_top:.1f}, "
        f"{target_x:.1f} {target_top:.1f} "
        f"L {target_x:.1f} {target_bottom:.1f} "
        f"C {target_x - control_offset:.1f} {target_bottom:.1f}, "
        f"{source_x + control_offset:.1f} {source_bottom:.1f}, "
        f"{source_x:.1f} {source_bottom:.1f} Z"
    )


def build_node(
    x: float,
    y: float,
    height: float,
    color: str,
) -> str:
    visible_height = max(2.0, height)
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="16" '
        f'height="{visible_height:.1f}" rx="5" '
        f'fill="{escape(color)}"/>'
    )


def build_chain_shapes(
    counts: Sequence[int],
    x_positions: Sequence[float],
    main_y: float,
    source_height: float,
    loss_y: float,
    node_colors: Sequence[str],
    retained_class: str,
) -> str:
    if not (
        len(counts) == len(x_positions) == len(node_colors)
    ):
        raise ValueError(
            "桑基链阶段数量、横坐标数量和颜色数量必须一致: "
            f"counts={len(counts)}, "
            f"x_positions={len(x_positions)}, "
            f"node_colors={len(node_colors)}"
        )

    scale = source_height / counts[0]
    shapes: List[str] = []
    for index in range(len(counts) - 1):
        source_count = counts[index]
        target_count = counts[index + 1]
        source_x = x_positions[index]
        target_x = x_positions[index + 1]
        source_stage_height = source_count * scale
        target_stage_height = target_count * scale
        loss_height = source_stage_height - target_stage_height
        retained_path = build_band_path(
            source_x + 16.0,
            target_x,
            main_y,
            main_y + target_stage_height,
            main_y,
            main_y + target_stage_height,
        )
        loss_path = build_band_path(
            source_x + 16.0,
            target_x,
            main_y + target_stage_height,
            main_y + source_stage_height,
            loss_y,
            loss_y + loss_height,
        )
        shapes.append(
            f'<path class="flow {escape(retained_class)}" '
            f'd="{retained_path}"/>'
        )
        shapes.append(
            f'<path class="flow flow-loss" d="{loss_path}"/>'
        )
        shapes.append(
            build_node(
                target_x,
                loss_y,
                loss_height,
                "#CBD5E1",
            )
        )

    for count, x, color in zip(counts, x_positions, node_colors):
        shapes.append(
            build_node(
                x,
                main_y,
                count * scale,
                color,
            )
        )
    return "\n".join(shapes)


def build_stage_label(
    x: float,
    title: str,
    value: str,
    rate: float,
    title_y: float,
) -> str:
    center_x = x + 8.0
    return (
        f'<text class="node-title" x="{center_x:.1f}" '
        f'y="{title_y:.1f}" text-anchor="middle">'
        f"{escape(title)}</text>"
        f'<text class="node-value" x="{center_x:.1f}" '
        f'y="{title_y + 24.0:.1f}" text-anchor="middle">'
        f"{escape(value)}</text>"
        f'<text class="node-rate" x="{center_x:.1f}" '
        f'y="{title_y + 44.0:.1f}" text-anchor="middle">'
        f"占首阶段 {rate:.2f}%</text>"
    )


def build_loss_label(
    x: float,
    title: str,
    value: str,
    title_y: float,
) -> str:
    center_x = x + 8.0
    return (
        f'<text class="loss-title" x="{center_x:.1f}" '
        f'y="{title_y:.1f}" text-anchor="middle">'
        f"{escape(title)}</text>"
        f'<text class="loss-value" x="{center_x:.1f}" '
        f'y="{title_y + 22.0:.1f}" text-anchor="middle">'
        f"{escape(value)}</text>"
    )


def build_merchant_sankey(
    data: SankeyInput,
    indicators: SankeyIndicators,
) -> str:
    counts = (
        data.total_merchant_count,
        data.offline_merchant_count,
        data.offline_non_individual_merchant_count,
        data.paired_merchant_count,
        data.effective_merchant_count,
        data.circle_capable_merchant_count,
    )
    x_positions = (72.0, 336.0, 600.0, 864.0, 1128.0, 1392.0)
    node_colors = (
        "#2563EB",
        "#4F46E5",
        "#7C3AED",
        "#0891B2",
        "#0F766E",
        "#059669",
    )
    main_y = 245.0
    source_height = 280.0
    loss_y = 420.0
    shapes = build_chain_shapes(
        counts,
        x_positions,
        main_y,
        source_height,
        loss_y,
        node_colors,
        "flow-merchant",
    )

    stage_titles = (
        "上海总商户量",
        "线下商户量",
        "线下非个体户量",
        "能形成交易对的商户",
        "有效交易对商户",
        "能够形成商圈的商户",
    )
    stage_rates = (
        100.0,
        indicators.offline_merchant_pct,
        indicators.non_individual_merchant_pct,
        indicators.paired_merchant_pct,
        (
            100.0
            * data.effective_merchant_count
            / data.total_merchant_count
        ),
        indicators.circle_capable_of_all_pct,
    )
    stage_labels = "\n".join(
        build_stage_label(
            x,
            title,
            format_number(count),
            rate,
            174.0,
        )
        for x, title, count, rate in zip(
            x_positions,
            stage_titles,
            counts,
            stage_rates,
        )
    )

    loss_titles = (
        "非线下商户",
        "个体户等",
        "未形成交易对",
        f"未达 support≥{data.support_threshold}",
        "有效但未形成商圈",
    )
    loss_counts = (
        indicators.non_offline_merchant_count,
        indicators.individual_merchant_count,
        indicators.unpaired_merchant_count,
        indicators.low_support_merchant_count,
        indicators.effective_without_circle_count,
    )
    loss_labels = "\n".join(
        build_loss_label(
            x,
            title,
            format_number(count),
            392.0,
        )
        for x, title, count in zip(
            x_positions[1:],
            loss_titles,
            loss_counts,
        )
    )

    scale = source_height / data.total_merchant_count
    circle_height = data.circle_capable_merchant_count * scale
    geographic_height = (
        data.geographic_assigned_merchant_count * scale
    )
    pair_assigned_height = (
        data.pair_assigned_merchant_count * scale
    )
    method_x = 1650.0
    geographic_y = 245.0
    pair_assigned_y = 298.0
    geographic_path = build_band_path(
        x_positions[-1] + 16.0,
        method_x,
        main_y,
        main_y + geographic_height,
        geographic_y,
        geographic_y + geographic_height,
    )
    pair_assigned_path = build_band_path(
        x_positions[-1] + 16.0,
        method_x,
        main_y + geographic_height,
        main_y + circle_height,
        pair_assigned_y,
        pair_assigned_y + pair_assigned_height,
    )

    return f"""
    <g aria-label="商户转化桑基图">
      <text class="section-title" x="62" y="132">商户转化流（单位：户）</text>
      {shapes}
      {stage_labels}
      {loss_labels}
      <path class="flow flow-geographic" d="{geographic_path}"/>
      <path class="flow flow-pair-assigned" d="{pair_assigned_path}"/>
      {build_node(method_x, geographic_y, geographic_height, "#0891B2")}
      {build_node(method_x, pair_assigned_y, pair_assigned_height, "#DB2777")}
      <text class="node-title" x="1676" y="236">经纬度直接添加</text>
      <text class="node-value cyan-value" x="1676" y="260">{format_number(data.geographic_assigned_merchant_count)}</text>
      <text class="node-rate" x="1676" y="280">占形成商圈 {indicators.geographic_of_circle_pct:.2f}%</text>
      <text class="node-title" x="1676" y="294">交易对添加</text>
      <text class="node-value pink-value" x="1676" y="318">{format_number(data.pair_assigned_merchant_count)}</text>
      <text class="node-rate" x="1676" y="338">占形成商圈 {indicators.pair_of_circle_pct:.2f}%</text>
      <text class="transition-note" x="1004" y="552">有效交易对商户 → 能够形成商圈：{indicators.circle_capable_of_effective_pct:.2f}%</text>
    </g>"""


def build_transaction_sankey(
    data: SankeyInput,
    indicators: SankeyIndicators,
) -> str:
    counts = (
        data.total_transaction_count,
        data.offline_transaction_count,
        data.non_individual_transaction_count,
        data.matched_circle_transaction_count,
        data.filtered_circle_transaction_count,
        data.final_circle_transaction_count,
    )
    x_positions = (72.0, 366.0, 660.0, 954.0, 1248.0, 1542.0)
    node_colors = (
        "#2563EB",
        "#3B82F6",
        "#65A30D",
        "#D97706",
        "#DC2626",
        "#BE123C",
    )
    main_y = 742.0
    shapes = build_chain_shapes(
        counts,
        x_positions,
        main_y,
        180.0,
        900.0,
        node_colors,
        "flow-transaction",
    )
    titles = (
        "总交易量",
        "线下交易",
        "剔除个体户后可聚类交易",
        "匹配商圈有效交易",
        "商圈交易筛选后",
        "最终商圈交易",
    )
    rates = (
        100.0,
        indicators.offline_transaction_pct,
        indicators.non_individual_transaction_pct,
        indicators.matched_circle_transaction_pct,
        indicators.filtered_circle_transaction_pct,
        indicators.final_circle_transaction_pct,
    )
    stage_labels = "\n".join(
        build_stage_label(
            x,
            title,
            format_wan(count),
            rate,
            671.0,
        )
        for x, title, count, rate in zip(
            x_positions,
            titles,
            counts,
            rates,
        )
    )
    loss_titles = (
        "线上等交易",
        "个体户等交易",
        "未匹配商圈交易",
        "商圈筛选流失",
        "最终筛选流失",
    )
    loss_counts = tuple(
        current_count - next_count
        for current_count, next_count in zip(counts, counts[1:])
    )
    loss_labels = "\n".join(
        build_loss_label(
            x,
            title,
            format_wan(count),
            874.0,
        )
        for x, title, count in zip(
            x_positions[1:],
            loss_titles,
            loss_counts,
        )
    )
    return f"""
    <g aria-label="交易量转化桑基图">
      <text class="section-title" x="62" y="628">交易量转化流（单位：万笔）</text>
      {shapes}
      {stage_labels}
      {loss_labels}
    </g>"""


def build_pair_sankey(
    data: SankeyInput,
    indicators: SankeyIndicators,
) -> str:
    source_height = 118.0
    effective_height = (
        source_height * data.effective_pair_count / data.pair_count
    )
    ineffective_height = source_height - effective_height
    source_x = 72.0
    target_x = 720.0
    source_y = 1225.0
    effective_y = 1225.0
    ineffective_y = 1260.0
    effective_path = build_band_path(
        source_x + 16.0,
        target_x,
        source_y,
        source_y + effective_height,
        effective_y,
        effective_y + effective_height,
    )
    ineffective_path = build_band_path(
        source_x + 16.0,
        target_x,
        source_y + effective_height,
        source_y + source_height,
        ineffective_y,
        ineffective_y + ineffective_height,
    )
    return f"""
    <g aria-label="交易对证据桑基图">
      <text class="section-title" x="62" y="1165">交易对证据流（单位：对）</text>
      <path class="flow flow-pair-effective" d="{effective_path}"/>
      <path class="flow flow-loss" d="{ineffective_path}"/>
      {build_node(source_x, source_y, source_height, "#2563EB")}
      {build_node(target_x, effective_y, effective_height, "#7C3AED")}
      {build_node(target_x, ineffective_y, ineffective_height, "#CBD5E1")}
      <text class="node-title" x="80" y="1198" text-anchor="middle">有交易关联的交易对</text>
      <text class="node-value" x="80" y="1222" text-anchor="middle">{format_number(data.pair_count)}</text>
      <text class="node-title" x="748" y="1212">有效交易对</text>
      <text class="node-value" x="748" y="1236">{format_number(data.effective_pair_count)}</text>
      <text class="node-rate" x="888" y="1236">有效率 {indicators.effective_pair_pct:.2f}%</text>
      <text class="loss-title" x="748" y="1288">未达 support≥{data.support_threshold}</text>
      <text class="loss-value" x="748" y="1312">{format_number(indicators.ineffective_pair_count)}</text>
      <text class="node-rate" x="912" y="1312">占 {100.0 - indicators.effective_pair_pct:.2f}%</text>
    </g>"""


def build_svg(data: SankeyInput) -> str:
    indicators = calculate_indicators(data)
    merchant_sankey = build_merchant_sankey(data, indicators)
    transaction_sankey = build_transaction_sankey(data, indicators)
    pair_sankey = build_pair_sankey(data, indicators)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="1490" viewBox="0 0 1800 1490" role="img" aria-labelledby="chart-title chart-desc">
  <title id="chart-title">上海1至6月线下交易商户商圈聚类全流程桑基图</title>
  <desc id="chart-desc">展示上海商户从总商户、线下商户、线下非个体户、形成交易对、有效交易对商户到能够形成商圈商户的转化，同时展示交易量转化和交易对有效率。</desc>
  <defs>
    <filter id="surface-shadow" x="-10%" y="-20%" width="120%" height="150%">
      <feDropShadow dx="0" dy="8" stdDeviation="14" flood-color="#16325C" flood-opacity="0.10"/>
    </filter>
  </defs>
  <style>
    text {{
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    }}
    .background {{ fill: #F4F7FC; }}
    .surface {{ fill: #FFFFFF; filter: url(#surface-shadow); }}
    .main-title {{ fill: #102A43; font-size: 28px; font-weight: 700; text-anchor: middle; }}
    .subtitle {{ fill: #627D98; font-size: 15px; text-anchor: middle; }}
    .section-title {{ fill: #243B53; font-size: 19px; font-weight: 700; }}
    .flow {{ stroke: none; }}
    .flow-merchant {{ fill: #60A5FA; fill-opacity: 0.58; }}
    .flow-transaction {{ fill: #A78BFA; fill-opacity: 0.58; }}
    .flow-geographic {{ fill: #22D3EE; fill-opacity: 0.72; }}
    .flow-pair-assigned {{ fill: #F472B6; fill-opacity: 0.78; }}
    .flow-pair-effective {{ fill: #A78BFA; fill-opacity: 0.78; }}
    .flow-loss {{ fill: #CBD5E1; fill-opacity: 0.62; }}
    .node-title {{ fill: #486581; font-size: 14px; }}
    .node-value {{ fill: #102A43; font-size: 20px; font-weight: 700; }}
    .node-rate {{ fill: #829AB1; font-size: 12px; }}
    .loss-title {{ fill: #627D98; font-size: 13px; }}
    .loss-value {{ fill: #829AB1; font-size: 17px; font-weight: 700; }}
    .cyan-value {{ fill: #0E7490; }}
    .pink-value {{ fill: #BE185D; }}
    .transition-note {{ fill: #0F766E; font-size: 13px; font-weight: 700; text-anchor: middle; }}
    .scope-label {{ fill: #486581; font-size: 14px; text-anchor: middle; }}
  </style>
  <rect class="background" width="1800" height="1490" rx="24"/>
  <rect class="surface" x="24" y="102" width="1752" height="486" rx="22"/>
  <rect class="surface" x="24" y="606" width="1752" height="500" rx="22"/>
  <rect class="surface" x="24" y="1124" width="1752" height="276" rx="22"/>
  <text class="main-title" x="900" y="45">上海1—6月线下交易商户商圈聚类全流程桑基图</text>
  <text class="subtitle" x="900" y="76">商户、交易量与交易对采用独立单位和独立宽度比例</text>
  {merchant_sankey}
  {transaction_sankey}
  {pair_sankey}
  <text class="scope-label" x="900" y="1443">有效口径：support ≥ {data.support_threshold}，即至少 {data.support_threshold} 个不同 account_number 均消费过同一商户对</text>
  <text class="scope-label" x="900" y="1469">形成商圈的 54,828 户 = 经纬度直接添加 43,569 户 + 交易对添加 11,259 户</text>
</svg>
"""


def write_svg(
    svg: str,
    output_path: Path,
) -> None:
    output_path.write_text(svg, encoding="utf-8")


def main() -> None:
    svg = build_svg(DATA)
    write_svg(svg, OUTPUT_PATH)
    print(f"sankey_chart_created path={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
