from __future__ import annotations

import argparse
from pathlib import Path

from incremental_assignment.config import load_config
from incremental_assignment.pipeline import run_incremental_assignment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="新商户增量归属算法")
    parser.add_argument("--config", required=True, help="增量归属配置文件")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config).resolve())
    summary = run_incremental_assignment(config)
    print(f"candidates={summary.candidate_count}")
    print(f"assigned={summary.assigned_count}")
    print(f"observe={summary.observation_count}")
    print(f"manual_review={summary.manual_review_count}")
    print(f"output_directory={summary.output_directory}")

