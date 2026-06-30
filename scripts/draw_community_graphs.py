from __future__ import annotations

import csv
import hashlib
import html
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple


MerchantRecord = Tuple[str, str, bool]
CommunityMerchant = Tuple[str, bool]
GroupedMerchants = Dict[str, List[CommunityMerchant]]

REQUIRED_COLUMNS: FrozenSet[str] = frozenset(
    {"merchant_id", "community_id", "is_anchor_candidate"}
)
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SVG_FONT_FAMILY = "Microsoft YaHei, SimHei, Noto Sans CJK SC, Arial, sans-serif"


def parse_anchor_flag(value: str, row_number: int) -> bool:
    normalized_value = value.strip()
    if normalized_value == "1":
        return True
    if normalized_value == "0":
        return False
    raise ValueError(
        f"第 {row_number} 行的 is_anchor_candidate 必须为 0 或 1，实际值为 {value!r}"
    )


def require_text(value: Optional[str], column_name: str, row_number: int) -> str:
    if value is None:
        raise ValueError(f"第 {row_number} 行缺少字段 {column_name}")
    stripped_value = value.strip()
    if stripped_value == "":
        raise ValueError(f"第 {row_number} 行字段 {column_name} 不能为空")
    return stripped_value


def validate_columns(fieldnames: Sequence[str] | None, csv_path: Path) -> None:
    if fieldnames is None:
        raise ValueError(f"CSV 文件没有表头：{csv_path}")
    current_columns = set(fieldnames)
    missing_columns = REQUIRED_COLUMNS - current_columns
    if len(missing_columns) > 0:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"CSV 文件缺少必要字段：{missing_text}，文件：{csv_path}")


def read_merchants(csv_path: Path) -> List[MerchantRecord]:
    try:
        csv_file = csv_path.open("r", encoding="utf-8-sig", newline="")
    except FileNotFoundError as error:
        raise FileNotFoundError(f"找不到 merchants.csv 文件：{csv_path}") from error
    except PermissionError as error:
        raise PermissionError(f"没有权限读取 CSV 文件：{csv_path}") from error

    with csv_file:
        try:
            reader = csv.DictReader(csv_file)
            validate_columns(reader.fieldnames, csv_path)
            records: List[MerchantRecord] = []
            for row_number, row in enumerate(reader, start=2):
                merchant_id = require_text(row.get("merchant_id"), "merchant_id", row_number)
                community_id = require_text(
                    row.get("community_id"), "community_id", row_number
                )
                anchor_value = require_text(
                    row.get("is_anchor_candidate"), "is_anchor_candidate", row_number
                )
                records.append(
                    (merchant_id, community_id, parse_anchor_flag(anchor_value, row_number))
                )
        except UnicodeDecodeError as error:
            raise UnicodeDecodeError(
                error.encoding,
                error.object,
                error.start,
                error.end,
                f"读取 CSV 失败，文件需要使用 UTF-8 或 UTF-8-SIG 编码：{csv_path}",
            ) from error

    if len(records) == 0:
        raise ValueError(f"CSV 文件没有商户记录：{csv_path}")
    return records


def group_merchants(records: Sequence[MerchantRecord]) -> GroupedMerchants:
    grouped_merchants: GroupedMerchants = {}
    for merchant_id, community_id, is_anchor_candidate in records:
        if community_id not in grouped_merchants:
            grouped_merchants[community_id] = []
        grouped_merchants[community_id].append((merchant_id, is_anchor_candidate))
    return grouped_merchants


def display_units(text: str) -> int:
    return sum(1 if ord(character) < 128 else 2 for character in text)


def truncate_to_units(text: str, max_units: int) -> str:
    result = ""
    used_units = 0
    for character in text:
        character_units = 1 if ord(character) < 128 else 2
        if used_units + character_units > max_units:
            return result
        result = result + character
        used_units = used_units + character_units
    return result


def wrap_text(text: str, max_units: int, max_lines: int) -> List[str]:
    lines: List[str] = []
    current_line = ""
    current_units = 0
    for character in text:
        character_units = 1 if ord(character) < 128 else 2
        if current_units + character_units > max_units and current_line != "":
            lines.append(current_line)
            current_line = character
            current_units = character_units
        else:
            current_line = current_line + character
            current_units = current_units + character_units
        if len(lines) == max_lines:
            break

    if len(lines) < max_lines and current_line != "":
        lines.append(current_line)

    if display_units(text) > sum(display_units(line) for line in lines):
        last_index = len(lines) - 1
        shortened_line = truncate_to_units(lines[last_index], max_units - 3)
        lines[last_index] = shortened_line + "..."
    return lines


def sanitize_filename(value: str) -> str:
    normalized_value = INVALID_FILENAME_CHARS.sub("_", value.strip())
    normalized_value = normalized_value.rstrip(". ")
    if normalized_value == "":
        raise ValueError(f"community_id 不能作为文件名：{value!r}")
    return normalized_value


def svg_text(
    text: str,
    x: float,
    y: float,
    font_size: int,
    fill: str,
    text_anchor: str,
    font_weight: str,
) -> str:
    escaped_text = html.escape(text)
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{font_size}" '
        f'fill="{fill}" text-anchor="{text_anchor}" '
        f'font-family="{SVG_FONT_FAMILY}" font-weight="{font_weight}">'
        f"{escaped_text}</text>"
    )


def svg_wrapped_text(
    lines: Sequence[str],
    x: float,
    y: float,
    font_size: int,
    fill: str,
    text_anchor: str,
) -> str:
    escaped_lines = [html.escape(line) for line in lines]
    tspans = [
        f'<tspan x="{x:.2f}" dy="{0 if index == 0 else font_size + 4}">{line}</tspan>'
        for index, line in enumerate(escaped_lines)
    ]
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{font_size}" '
        f'fill="{fill}" text-anchor="{text_anchor}" '
        f'font-family="{SVG_FONT_FAMILY}">'
        f"{''.join(tspans)}</text>"
    )


def svg_merchant_node(x: float, y: float, is_anchor_candidate: bool) -> str:
    if is_anchor_candidate:
        points = [
            (x, y - 13),
            (x + 13, y),
            (x, y + 13),
            (x - 13, y),
        ]
        points_text = " ".join(f"{point_x:.2f},{point_y:.2f}" for point_x, point_y in points)
        return (
            f'<polygon points="{points_text}" fill="#f97316" '
            f'stroke="#9a3412" stroke-width="2" />'
        )
    return f'<circle cx="{x:.2f}" cy="{y:.2f}" r="10" fill="#2563eb" stroke="#1e3a8a" stroke-width="2" />'


def calculate_canvas_size(merchant_count: int) -> Tuple[int, int, int, int]:
    radius = max(220, math.ceil(merchant_count * 84 / (2 * math.pi)))
    margin = 220
    size = (radius + margin) * 2
    return size, size, radius, margin


def render_community_svg(community_id: str, merchants: Sequence[CommunityMerchant]) -> str:
    merchant_count = len(merchants)
    if merchant_count == 0:
        raise ValueError(f"商圈 {community_id} 没有商户，无法生成图")

    width, height, radius, _margin = calculate_canvas_size(merchant_count)
    center_x = width / 2
    center_y = height / 2
    elements: List[str] = []

    elements.append(
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f8fafc" />'
    )
    elements.append(
        svg_text(
            f"商圈 {community_id}",
            center_x,
            54,
            28,
            "#111827",
            "middle",
            "700",
        )
    )
    elements.append(
        svg_text(
            f"商户数：{merchant_count}    锚点商户：{sum(1 for _, is_anchor in merchants if is_anchor)}",
            center_x,
            88,
            16,
            "#475569",
            "middle",
            "400",
        )
    )

    elements.append(
        '<line x1="36" y1="124" x2="86" y2="124" stroke="#94a3b8" stroke-width="2" />'
    )
    elements.append(
        '<circle cx="112" cy="124" r="10" fill="#2563eb" stroke="#1e3a8a" stroke-width="2" />'
    )
    elements.append(svg_text("普通商户", 132, 129, 14, "#334155", "start", "400"))
    elements.append(
        '<polygon points="236,111 249,124 236,137 223,124" fill="#f97316" stroke="#9a3412" stroke-width="2" />'
    )
    elements.append(svg_text("锚点商户", 258, 129, 14, "#334155", "start", "400"))

    elements.append(
        f'<circle cx="{center_x:.2f}" cy="{center_y:.2f}" r="58" fill="#0f172a" stroke="#020617" stroke-width="3" />'
    )
    center_lines = wrap_text(f"商圈 {community_id}", 12, 2)
    elements.append(
        svg_wrapped_text(center_lines, center_x, center_y - 6, 18, "#ffffff", "middle")
    )

    for index, (merchant_id, is_anchor_candidate) in enumerate(merchants):
        angle = (2 * math.pi * index / merchant_count) - (math.pi / 2)
        merchant_x = center_x + radius * math.cos(angle)
        merchant_y = center_y + radius * math.sin(angle)
        label_radius = radius + 30
        label_x = center_x + label_radius * math.cos(angle)
        label_y = center_y + label_radius * math.sin(angle)
        text_anchor = "middle"
        if math.cos(angle) > 0.25:
            text_anchor = "start"
        elif math.cos(angle) < -0.25:
            text_anchor = "end"

        elements.append(
            f'<line x1="{center_x:.2f}" y1="{center_y:.2f}" x2="{merchant_x:.2f}" y2="{merchant_y:.2f}" '
            f'stroke="#cbd5e1" stroke-width="1.5" />'
        )
        elements.append(
            f"<g><title>{html.escape(merchant_id)}</title>"
            f"{svg_merchant_node(merchant_x, merchant_y, is_anchor_candidate)}</g>"
        )
        label_lines = wrap_text(merchant_id, 18, 3)
        elements.append(
            svg_wrapped_text(label_lines, label_x, label_y + 5, 13, "#1f2937", text_anchor)
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="商圈 {html.escape(community_id)} 图">'
        f"{''.join(elements)}</svg>\n"
    )


def build_svg_filename(community_id: str) -> str:
    safe_community_id = sanitize_filename(community_id)
    digest = hashlib.sha1(community_id.encode("utf-8")).hexdigest()[:8]
    return f"community_{safe_community_id}_{digest}.svg"


def write_community_svgs(grouped_merchants: GroupedMerchants, output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: List[Path] = []
    for community_id in sorted(grouped_merchants.keys()):
        merchants = grouped_merchants[community_id]
        svg_content = render_community_svg(community_id, merchants)
        output_path = output_dir / build_svg_filename(community_id)
        with output_path.open("w", encoding="utf-8", newline="") as svg_file:
            svg_file.write(svg_content)
        written_paths.append(output_path)
    return written_paths


def parse_paths(argv: Sequence[str]) -> Tuple[Path, Path]:
    if len(argv) == 0:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Path("merchants.csv"), Path(f"community_graphs_{timestamp}")
    if len(argv) == 2:
        return Path(argv[0]), Path(argv[1])
    raise ValueError(
        "用法：python scripts/draw_community_graphs.py [merchants.csv 输出目录]"
    )


def run(argv: Sequence[str]) -> int:
    csv_path, output_dir = parse_paths(argv)
    records = read_merchants(csv_path)
    grouped_merchants = group_merchants(records)
    written_paths = write_community_svgs(grouped_merchants, output_dir)
    print(f"已生成 {len(written_paths)} 张商圈图，输出目录：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
