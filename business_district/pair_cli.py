from __future__ import annotations

import argparse
from pathlib import Path

from business_district.config import load_config
from business_district.graph import PairStatistics, build_pair_statistics
from business_district.pair_statistics import write_pair_statistics
from business_district.transactions import load_transactions, merge_visits


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成商户对中间数据")
    parser.add_argument("--config", required=True, help="城市配置文件")
    parser.add_argument("--output", required=True, help="输出 SQLite 文件")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(Path(args.config).resolve())
    output_path = Path(args.output).resolve()

    print("step=load_transactions")
    transactions = load_transactions(config.input)
    print(f"transaction_rows={len(transactions)}")
    print("step=merge_visits")
    visits = merge_visits(transactions, config.visits)
    print(f"visit_rows={len(visits)}")
    print("step=build_pair_statistics")
    statistics: PairStatistics = build_pair_statistics(
        visits,
        config.cooccurrence,
    )
    print(f"pair_count={len(statistics.strengths)}")
    print("step=write_pair_statistics")
    write_pair_statistics(statistics, output_path)
    print(f"output={output_path}")


if __name__ == "__main__":
    main()
