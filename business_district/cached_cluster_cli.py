from __future__ import annotations

import argparse
from pathlib import Path

from business_district.cached_cluster import run_cluster_from_pairs
from business_district.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从商户对中间数据执行聚类")
    parser.add_argument("--config", required=True, help="城市配置文件")
    parser.add_argument("--pairs", required=True, help="商户对 SQLite 文件")
    parser.add_argument("--output", required=True, help="聚类输出根目录")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_cluster_from_pairs(
        load_config(Path(args.config).resolve()),
        Path(args.pairs).resolve(),
        Path(args.output).resolve(),
    )
    print(f"merchants={summary.merchant_count}")
    print(f"communities={summary.community_count}")
    print(f"edges={summary.edge_count}")
    print(f"output_directory={summary.output_directory}")


if __name__ == "__main__":
    main()
