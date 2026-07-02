from __future__ import annotations

import argparse
from pathlib import Path

from business_district.config import load_config
from business_district.pipeline import run_algorithm_one


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="商圈初始构建算法")
    parser.add_argument("--config", required=True, help="城市配置文件")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config).resolve())
    summary = run_algorithm_one(config)
    print(f"input_rows={summary.input_rows}")
    print(f"visit_rows={summary.visit_rows}")
    print(f"merchants={summary.merchant_count}")
    print(f"communities={summary.community_count}")
    print(f"edges={summary.edge_count}")
    print(f"output_directory={summary.output_directory}")
