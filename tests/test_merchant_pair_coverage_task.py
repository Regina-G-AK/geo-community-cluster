from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from business_district.graph import PairStatistics
from merchant_pair_coverage_task import (
    MerchantPairCoverageConfig,
    TaskMain,
    TaskPlatform,
    calculate_coverage_metrics,
    run_task,
)


def _build_config(
    hive_table_root: Path,
) -> MerchantPairCoverageConfig:
    return MerchantPairCoverageConfig(
        hive_table_root=hive_table_root,
        source_table="dev_icamp.icamp_merchant_cluster_algo_input",
        source_dt_values=("20260131",),
        result_table="dev_icamp.icamp_merchant_cluster_algo_output",
        result_dt="20260727",
        target_table="dev_icamp.icamp_merchant_pair_coverage_tmp",
        region="上海",
        city_name="上海",
        merge_window_minutes=60,
        maximum_daily_merchants_per_card=100,
        pair_window_minutes=120,
        decay_tau_minutes=60.0,
        minimum_unique_cards=2,
        process_count=1,
        maximum_attempts=1,
        retry_delay_seconds=0.0,
    )


def _source_rows() -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    transactions: Tuple[Tuple[str, str, str, str], ...] = (
        ("card_1", "flow_1", "商户甲", "20260101T100000"),
        ("card_1", "flow_2", "商户甲", "20260101T103000"),
        ("card_1", "flow_3", "商户乙", "20260101T110000"),
        ("card_2", "flow_4", "商户甲", "20260102T100000"),
        ("card_2", "flow_5", "商户乙", "20260102T113000"),
        ("card_3", "flow_6", "商户甲", "20260103T100000"),
        ("card_3", "flow_7", "商户乙", "20260103T130000"),
    )
    for card_id, flow_number, storename, transaction_time in transactions:
        rows.append(
            {
                "account_number": card_id,
                "global_flow_number": flow_number,
                "storename": storename,
                "merchant_category": "1",
                "transaction_time": transaction_time,
                "pos_longitude": "",
                "pos_latitude": "",
                "region": "上海",
                "is_interfere": "",
                "is_abnormal": "",
                "business_district": "",
            }
        )
    return pd.DataFrame(rows)


def test_calculate_coverage_metrics_counts_raw_and_effective_pairs() -> None:
    statistics = PairStatistics(
        strengths={
            ("商户甲", "商户乙"): 1.5,
            ("商户乙", "商户丙"): 0.5,
        },
        supports={
            ("商户甲", "商户乙"): 2,
            ("商户乙", "商户丙"): 1,
        },
        merchant_visit_counts={
            "商户甲": 2,
            "商户乙": 3,
            "商户丙": 1,
        },
    )

    metrics = calculate_coverage_metrics(
        statistics,
        {"商户甲", "商户乙"},
        2,
    )

    assert metrics.paired_merchant_count == 3
    assert metrics.paired_merchant_with_community_count == 2
    assert metrics.paired_merchant_with_community_pct == 66.67
    assert metrics.raw_pair_count == 2
    assert metrics.raw_both_have_community_pair_count == 1
    assert metrics.raw_any_has_community_pair_count == 2
    assert metrics.effective_pair_count == 1
    assert metrics.effective_both_have_community_pair_count == 1
    assert metrics.effective_both_have_community_pair_pct == 100.0


def test_task_reads_partitions_writes_table_and_finishes(
    tmp_path: Path,
) -> None:
    events: List[str] = []
    written: Dict[str, object] = {}
    source = _source_rows()
    result = pd.DataFrame(
        [
            {
                "storename": "商户甲",
                "community_id": "1",
                "region": "上海",
            }
        ]
    )

    def read_table(table_name: str, dt: List[str]) -> object:
        events.append(f"read:{table_name}:{dt[0]}")
        return None

    def read_parquet(file_path: Path) -> pd.DataFrame:
        events.append(f"parquet:{str(file_path)}")
        if "algo_input" in str(file_path):
            return source.copy()
        return result.copy()

    def write_table(
        dataframe: pd.DataFrame,
        table_name: str,
        debug: bool,
        dt: object,
    ) -> object:
        events.append(f"write:{table_name}")
        written.update(
            {
                "dataframe": dataframe.copy(),
                "table_name": table_name,
                "debug": debug,
                "dt": dt,
            }
        )
        return None

    input_partition = (
        tmp_path
        / "icamp_merchant_cluster_algo_input"
        / "dt=20260131"
    )
    result_partition = (
        tmp_path
        / "icamp_merchant_cluster_algo_output"
        / "dt=20260727"
    )
    input_partition.mkdir(parents=True)
    result_partition.mkdir(parents=True)
    (input_partition / "part-00000").touch()
    (result_partition / "part-00000").touch()

    platform = TaskPlatform(
        mount_check=lambda: events.append("mount"),
        read_table=read_table,
        read_parquet=read_parquet,
        write_table=write_table,
        execute_sql=lambda sql: events.append(f"sql:{sql}"),
        log_data=lambda message: events.append(f"log:{message}"),
        format_exception=lambda: events.append("formatted"),
        finish_task=lambda: events.append("finish"),
    )

    summary = run_task(TaskMain(platform, _build_config(tmp_path)))
    output = written["dataframe"]

    assert isinstance(output, pd.DataFrame)
    assert summary.source_row_count == 7
    assert summary.visit_row_count == 6
    assert summary.result_merchant_count == 1
    assert summary.raw_pair_count == 1
    assert summary.effective_pair_count == 1
    assert written["table_name"] == (
        "dev_icamp.icamp_merchant_pair_coverage_tmp"
    )
    assert output.loc[0, "paired_merchant_count"] == 2
    assert output.loc[0, "paired_merchant_with_community_count"] == 1
    assert output.loc[0, "raw_any_has_community_pair_count"] == 1
    assert output.loc[0, "raw_both_have_community_pair_count"] == 0
    assert events[-1] == "finish"
