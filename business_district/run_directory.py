from __future__ import annotations

from datetime import datetime
from pathlib import Path

from business_district.config import AppConfig
from business_district.errors import AlgorithmError


def build_run_directory(root: Path, config: AppConfig, started_at: datetime) -> Path:
    timestamp = started_at.strftime("%y%m%d%H%M%S")
    name = (
        f"{config.graph.edge_weight_method}_"
        f"{config.community.algorithm.lower()}_{timestamp}"
    )
    return root / name


def create_run_directory(root: Path, config: AppConfig, started_at: datetime) -> Path:
    directory = build_run_directory(root, config, started_at)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise AlgorithmError(
            f"运行输出目录已存在，拒绝覆盖: directory={directory}"
        ) from error
    return directory
