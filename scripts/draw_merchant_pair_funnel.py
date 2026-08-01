from __future__ import annotations

from html import escape
from pathlib import Path
from typing import NamedTuple, Sequence, Tuple


class FunnelMetric(NamedTuple):
    title: str
    source_label: str
    source_value: int
    target_label: str
    target_value: int
    unit: str
    gradient_id: str
    start_color: str
    end_color: str


class FunnelIndicators(NamedTuple):
    retention_pct: float
    reduction_pct: float
    reduction_count: int


OUTPUT_PATH = Path(__file__).resolve().with_name("merchant_pair_funnel.svg")
SUPPORT_THRESHOLD = 2
METRICS: Tuple[FunnelMetric, ...] = (
    FunnelMetric(
        title="交易对有效率",
        source_label="有交易关联的交易对",
        source_value=1_498_702,
        target_label="有效交易对",
        target_value=96_607,
        unit="对",
        gradient_id="pair-gradient",
        start_color="#3B82F6",
        end_color="#2563EB",
    ),
    FunnelMetric(
        title="有效交易对商户覆盖",
        source_label="形成交易对的商户",
        source_value=461_097,
        target_label="有效交易对涉及商户",
        target_value=82_345,
        unit="户",
        gradient_id="merchant-gradient",
        start_color="#8B5CF6",
        end_color="#6D28D9",
    ),
    FunnelMetric(
        title="聚类商户覆盖",
        source_label="形成交易对的商户",
        source_value=461_097,
        target_label="有商圈 ID 的交易对商户",
        target_value=52_929,
        unit="户",
        gradient_id="cluster-gradient",
        start_color="#14B8A6",
        end_color="#0F766E",
    ),
)


def calculate_indicators(metric: FunnelMetric) -> FunnelIndicators:
    if metric.source_value <= 0:
        raise ValueError(
            "漏斗首层数量必须大于 0: "
            f"title={metric.title!r}, source_value={metric.source_value}"
        )
    if metric.target_value < 0:
        raise ValueError(
            "漏斗目标数量不能小于 0: "
            f"title={metric.title!r}, target_value={metric.target_value}"
        )
    if metric.target_value > metric.source_value:
        raise ValueError(
            "漏斗目标数量不能大于首层数量: "
            f"title={metric.title!r}, "
            f"source_value={metric.source_value}, "
            f"target_value={metric.target_value}"
        )
    retention_pct = 100.0 * metric.target_value / metric.source_value
    return FunnelIndicators(
        retention_pct=retention_pct,
        reduction_pct=100.0 - retention_pct,
        reduction_count=metric.source_value - metric.target_value,
    )


def format_number(value: int) -> str:
    return f"{value:,}"


def build_gradient(metric: FunnelMetric) -> str:
    return (
        f'<linearGradient id="{escape(metric.gradient_id)}" '
        'x1="0%" y1="0%" x2="0%" y2="100%">'
        f'<stop offset="0%" stop-color="{escape(metric.start_color)}"/>'
        f'<stop offset="100%" stop-color="{escape(metric.end_color)}"/>'
        "</linearGradient>"
    )


def build_panel(
    metric: FunnelMetric,
    panel_x: int,
) -> str:
    indicators = calculate_indicators(metric)
    panel_width = 400
    center_x = panel_x + panel_width / 2.0
    source_left = panel_x + 34
    source_right = panel_x + panel_width - 34
    source_lower_left = panel_x + 124
    source_lower_right = panel_x + panel_width - 124
    target_width = max(
        16.0,
        (source_lower_right - source_lower_left)
        * indicators.retention_pct
        / 100.0,
    )
    target_top_left = center_x - target_width / 2.0
    target_top_right = center_x + target_width / 2.0
    target_bottom_width = max(8.0, target_width * 0.38)
    target_bottom_left = center_x - target_bottom_width / 2.0
    target_bottom_right = center_x + target_bottom_width / 2.0
    return f"""
    <g aria-label="{escape(metric.title)}">
      <rect class="panel" x="{panel_x}" y="112" width="{panel_width}" height="500" rx="20"/>
      <text class="panel-title" x="{center_x:.1f}" y="154">{escape(metric.title)}</text>
      <text class="stage-label" x="{center_x:.1f}" y="190">{escape(metric.source_label)}</text>
      <text class="source-value" x="{center_x:.1f}" y="220">{format_number(metric.source_value)} {escape(metric.unit)}</text>
      <text class="percent-label" x="{center_x:.1f}" y="246">100.00%</text>

      <path class="source-funnel" d="M {source_left} 270 L {source_right} 270 L {source_lower_right} 386 L {source_lower_left} 386 Z"/>
      <path class="target-funnel" fill="url(#{escape(metric.gradient_id)})"
            d="M {target_top_left:.1f} 398 L {target_top_right:.1f} 398
               L {target_bottom_right:.1f} 486 L {target_bottom_left:.1f} 486 Z"/>

      <text class="stage-label" x="{center_x:.1f}" y="520">{escape(metric.target_label)}</text>
      <text class="target-value" x="{center_x:.1f}" y="550">{format_number(metric.target_value)} {escape(metric.unit)}</text>
      <rect class="rate-pill" x="{center_x - 57:.1f}" y="565" width="114" height="28" rx="14"/>
      <text class="rate-text" x="{center_x:.1f}" y="585">{indicators.retention_pct:.2f}%</text>
      <text class="reduction-label" x="{center_x:.1f}" y="605">减少 {format_number(indicators.reduction_count)} {escape(metric.unit)} · {indicators.reduction_pct:.2f}%</text>
    </g>"""


def build_svg(
    metrics: Sequence[FunnelMetric],
    support_threshold: int,
) -> str:
    if len(metrics) != 3:
        raise ValueError(
            f"漏斗图必须包含 3 组指标: metric_count={len(metrics)}"
        )
    if support_threshold < 1:
        raise ValueError(
            "支持度必须是正整数: "
            f"support_threshold={support_threshold}"
        )
    gradients = "".join(build_gradient(metric) for metric in metrics)
    panels = "".join(
        build_panel(metric, 28 + index * 424)
        for index, metric in enumerate(metrics)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1328" height="700" viewBox="0 0 1328 700" role="img" aria-labelledby="chart-title chart-desc">
  <title id="chart-title">上海1至6月线下交易对与聚类覆盖漏斗</title>
  <desc id="chart-desc">分别展示交易对有效率、有效交易对商户覆盖率和聚类商户覆盖率。有效交易对支持度至少为{support_threshold}。</desc>
  <defs>
    <filter id="card-shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#16325C" flood-opacity="0.10"/>
    </filter>
    <linearGradient id="base-gradient" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#BFDBFE"/>
      <stop offset="100%" stop-color="#93C5FD"/>
    </linearGradient>
    {gradients}
  </defs>
  <style>
    text {{
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    }}
    .background {{ fill: #F4F7FC; }}
    .panel {{ fill: #FFFFFF; filter: url(#card-shadow); }}
    .main-title {{ fill: #102A43; font-size: 28px; font-weight: 700; text-anchor: middle; }}
    .subtitle {{ fill: #627D98; font-size: 15px; text-anchor: middle; }}
    .panel-title {{ fill: #243B53; font-size: 20px; font-weight: 700; text-anchor: middle; }}
    .stage-label {{ fill: #627D98; font-size: 14px; text-anchor: middle; }}
    .source-value {{ fill: #102A43; font-size: 25px; font-weight: 700; text-anchor: middle; }}
    .target-value {{ fill: #102A43; font-size: 23px; font-weight: 700; text-anchor: middle; }}
    .percent-label {{ fill: #829AB1; font-size: 13px; text-anchor: middle; }}
    .source-funnel {{ fill: url(#base-gradient); }}
    .rate-pill {{ fill: #E8F0FE; }}
    .rate-text {{ fill: #1D4ED8; font-size: 14px; font-weight: 700; text-anchor: middle; }}
    .reduction-label {{ fill: #829AB1; font-size: 12px; text-anchor: middle; }}
    .scope-label {{ fill: #486581; font-size: 14px; text-anchor: middle; }}
  </style>
  <rect class="background" width="1328" height="700" rx="24"/>
  <text class="main-title" x="664" y="48">上海1–6月线下交易对与聚类覆盖漏斗</text>
  <text class="subtitle" x="664" y="78">交易对与商户采用并列口径，避免混用不同单位和非嵌套集合</text>
  {panels}
  <text class="scope-label" x="664" y="662">有效交易对口径：support ≥ {support_threshold}，即至少 {support_threshold} 个不同 account_number 均消费过该商户对</text>
</svg>
"""


def write_svg(
    svg: str,
    output_path: Path,
) -> None:
    output_path.write_text(svg, encoding="utf-8")


def main() -> None:
    svg = build_svg(METRICS, SUPPORT_THRESHOLD)
    write_svg(svg, OUTPUT_PATH)
    print(f"funnel_chart_created path={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
