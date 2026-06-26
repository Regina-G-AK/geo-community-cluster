from __future__ import annotations

import datetime
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pandas as pd
import spdbccc_data as sd
from spdbccc_data import dtDate
from spdbccc_data import formattedExc
from spdbccc_data import loging as logrecord
from spdbccc_data import mountCheck
from spdbccc_data import task as taskfinish

from business_district.config import load_config
from business_district.pipeline import run_algorithm_one_from_transactions
from business_district.transactions import load_hive_transactions

SOURCE_TABLE = "dev_icamp.icamp_merchant_cluster_algo_input"
TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output"
RISK_TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_risk"
TARGET_TEMP_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output_tmp"
RISK_TARGET_TEMP_TABLE = "dev_icamp.icamp_merchant_cluster_algo_risk_tmp"
DEFAULT_CONFIG_PATH = Path("configs/shanghai.ini")
DEFAULT_DT_EXPRESSION = "T-1"
TARGET_COLUMNS = [
    "storename",
    "community_id",
    "previous_community_id",
    "region",
    "is_interfere",
    "update_time",
    "is_abnormal",
    "is_position",
    "dt",
]
TARGET_SELECT_COLUMNS = [
    "storename",
    "community_id",
    "previous_community_id",
    "region",
    "is_interfere",
    "update_time",
    "is_abnormal",
    "is_position",
]


def _split_hive_table(full_table_name: str) -> tuple[str, str]:
    parts = full_table_name.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Hive 表名必须是 db.table 格式: table={full_table_name!r}"
        )
    return parts[0], parts[1]


@dataclass(frozen=True)
class HiveTaskConfig:
    config_path: Path
    source_table: str
    target_table: str
    risk_target_table: str
    target_temp_table: str
    risk_target_temp_table: str
    dt_expression: str


@dataclass(frozen=True)
class HiveTaskSummary:
    dt: str
    input_rows: int
    output_rows: int
    output_directory: str
    target_table: str


def build_default_hive_task_config() -> HiveTaskConfig:
    return HiveTaskConfig(
        config_path=DEFAULT_CONFIG_PATH,
        source_table=SOURCE_TABLE,
        target_table=TARGET_TABLE,
        risk_target_table=RISK_TARGET_TABLE,
        target_temp_table=TARGET_TEMP_TABLE,
        risk_target_temp_table=RISK_TARGET_TEMP_TABLE,
        dt_expression=DEFAULT_DT_EXPRESSION,
    )


def _format_hive_id(value: object) -> str:
    if pd.isna(value) or value == "":
        return ""
    return str(int(value))


def build_hive_target_output(
    business_results: pd.DataFrame,
) -> pd.DataFrame:
    output = business_results[
        [
            "storename",
            "community_id",
            "previous_community_id",
            "region",
            "is_interfere",
            "update_time",
            "status",
            "is_position",
            "dt",
        ]
    ].copy()
    output["storename"] = output["storename"].astype(str)
    output["community_id"] = output["community_id"].map(_format_hive_id)
    output["previous_community_id"] = ""
    output["region"] = output["region"].astype(str)
    output["is_interfere"] = 0
    output["update_time"] = output["update_time"].astype(str)
    output["is_abnormal"] = output["status"].astype(str)
    output["is_position"] = output["is_position"].astype(int)
    output["dt"] = output["dt"].astype(str)
    output = output.drop(columns=["status"])
    output = output[TARGET_COLUMNS]
    output = output[output["dt"] != ""].copy()
    return output.reset_index(drop=True)


def build_empty_risk_output() -> pd.DataFrame:
    return pd.DataFrame(columns=TARGET_COLUMNS)


def overwrite_target_table(
    sd: ModuleType,
    result: pd.DataFrame,
    table_name: str,
    temp_table_name: str,
) -> None:
    if result.empty:
        return

    select_columns = ", ".join(TARGET_SELECT_COLUMNS)
    for dt_value, partition_df in result.groupby("dt", sort=True):
        write_df = partition_df.drop(columns=["dt"]).reset_index(drop=True)
        sd.execute_sql(f"drop table if exists {temp_table_name}")
        try:
            sd.write_table(write_df, temp_table_name, debug=False, dt=None)
            sd.execute_sql(
                f"""
                insert overwrite table {table_name}
                partition (dt={dt_value})
                select {select_columns}
                from {temp_table_name}
                """
            )
        finally:
            sd.execute_sql(f"drop table if exists {temp_table_name}")


class TaskMain:
    def __init__(self, task_config: HiveTaskConfig) -> None:
        self.task_config = task_config
        self.dt_var = ""

    def check(self) -> None:
        mountCheck.mount_check()

    def taskrun(self) -> HiveTaskSummary:
        try:
            total_start = time.time()
            self.dt_var = str(dtDate.dt_date(self.task_config.dt_expression))
            logrecord.log_data(f"task dt={self.dt_var}")
            dt_list = [self.dt_var]
            print(dt_list)
            source_db, source_table = _split_hive_table(
                self.task_config.source_table
            )
            source_data = sd.read_table(source_db, source_table, dt=dt_list)
            source_data.columns = source_data.columns.astype("string").str.strip()
            config = load_config(self.task_config.config_path)
            transactions = load_hive_transactions(
                source_data,
                config.input.timestamp_formats,
                self.dt_var,
                self.task_config.source_table,
            )
            result = run_algorithm_one_from_transactions(
                config,
                transactions,
                "Hive输入表",
                f"{self.task_config.source_table}, dt={dt_list}",
            )
            target_output = build_hive_target_output(
                result.business_results,
            )
            overwrite_target_table(
                sd,
                target_output,
                self.task_config.target_table,
                self.task_config.target_temp_table,
            )
            overwrite_target_table(
                sd,
                build_empty_risk_output(),
                self.task_config.risk_target_table,
                self.task_config.risk_target_temp_table,
            )
            logrecord.log_data(
                f"taskrun seconds={time.time() - total_start:.2f}, "
                f"output_rows={len(target_output)}, "
                f"output_directory={result.summary.output_directory}"
            )
            summary = HiveTaskSummary(
                dt=self.dt_var,
                input_rows=len(source_data),
                output_rows=len(target_output),
                output_directory=result.summary.output_directory,
                target_table=self.task_config.target_table,
            )
        except Exception:
            formattedExc.formatted_exc()
            raise

        logrecord.log_data("python task log record.")
        return summary

    def destroy(self) -> None:
        errors: list[Exception] = []
        for table_name in [
            self.task_config.target_temp_table,
            self.task_config.risk_target_temp_table,
        ]:
            try:
                sd.execute_sql(f"drop table if exists {table_name}")
            except Exception as error:
                errors.append(error)
                logrecord.log_data(
                    f"drop temp table failed table={table_name}, error={error}"
                )
        if errors:
            raise RuntimeError(
                f"Hive 临时表清理失败: failed_count={len(errors)}"
            ) from errors[-1]


def run_hive_task(task_config: HiveTaskConfig) -> HiveTaskSummary:
    task = TaskMain(task_config)
    try:
        task.check()
        return task.taskrun()
    finally:
        task.destroy()


def main() -> None:
    tracemalloc.start()
    start_time = datetime.datetime.now()
    run_hive_task(build_default_hive_task_config())

    end_time = datetime.datetime.now()
    time_difference = end_time - start_time
    logrecord.log_data(f"task use time {time_difference}")
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    print(
        f"memory_current_mb = {current_memory / 1024 / 1024:.2f},"
        f"memory_peak_mb = {peak_memory / 1024 / 1024:.2f}"
    )
    taskfinish.finish_task()


if __name__ == "__main__":
    main()
