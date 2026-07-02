from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from incremental_assignment.models import AppConfig, RunSummary


@dataclass(frozen=True)
class ExperimentContext:
    started_at: datetime
    algorithm_one_summary: dict[str, object]
    output_directory: Path


def _next_experiment_number(content: str) -> int:
    numbers = [
        int(match.group(1))
        for match in re.finditer(r"(?m)^(\d+)\. ", content)
    ]
    return max(numbers, default=0) + 1


def build_experiment_record(
    config: AppConfig,
    summary: RunSummary,
    context: ExperimentContext,
    experiment_number: int,
) -> str:
    timestamp = context.started_at.isoformat(timespec="seconds")
    algorithm_source = context.algorithm_one_summary.get("source", "未知")
    return f"""

{experiment_number}. 自动实验：新商户增量归属
    - 运行信息：
        - 时间：{timestamp}
        - 待判定商户输入：{config.paths.candidate_merchants_path}
        - 算法一结果目录：{config.paths.algorithm_one_directory}
        - 算法一结果来源：{algorithm_source}
        - 输出目录：{context.output_directory}
        - 运行耗时：{summary.duration_seconds:.2f}秒
    - 参数：
    ```python
    MIN_OBSERVATION_DAYS = {config.assignment.min_observation_days}
    MIN_UNIQUE_USERS = {config.assignment.min_unique_users}
    TOP_K_NEIGHBORS = {config.assignment.top_k_neighbors}
    ANCHOR_VOTE_WEIGHT = {config.assignment.anchor_vote_weight}
    THETA = {config.assignment.theta}
    DELTA = {config.assignment.delta}
    GRAPH_WEIGHT = {config.assignment.graph_weight}
    GEO_WEIGHT = {config.assignment.geo_weight}
    CUSTOMER_WEIGHT = {config.assignment.customer_weight}
    MINIMUM_SIGMA_METERS = {config.assignment.minimum_sigma_meters}
    OBSERVATION_POOL_PATH = {str(config.paths.observation_pool_path)!r}
    MERCHANT_ARCHIVE_PATH = {str(config.paths.merchant_archive_path)!r}
    COMMUNITY_ARCHIVE_PATH = {str(config.paths.community_archive_path)!r}
    ```
    - 判定统计：
        - 待判定商户：{summary.candidate_count}个
        - 自动归入：{summary.assigned_count}个
        - 继续观察：{summary.observation_count}个
        - 人工工单：{summary.manual_review_count}个
    - 输出：
        - 观察池：{config.paths.observation_pool_path}
        - 商户档案：{config.paths.merchant_archive_path}
        - 商圈档案：{config.paths.community_archive_path}
        - 人工工单：{context.output_directory / "manual_review.csv"}
"""


def append_experiment_record(
    path: Path,
    config: AppConfig,
    summary: RunSummary,
    context: ExperimentContext,
) -> None:
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "# 增量实验记录\n\n## 结果记录\n"
        path.write_text(content, encoding="utf-8")

    record = build_experiment_record(
        config,
        summary,
        context,
        _next_experiment_number(content),
    )
    with path.open("a", encoding="utf-8") as file:
        file.write(record)

