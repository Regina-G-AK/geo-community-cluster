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

from business_district.config import (
    AlgorithmRuntimeConfig,
    load_config_with_runtime_parameters,
)
from business_district.errors import TransactionDataError
from business_district.pipeline import run_algorithm_one_from_transactions
from business_district.transactions import RAW_TIMESTAMP, REGION, load_hive_transactions

SOURCE_TABLE = "dev_icamp.icamp_merchant_cluster_algo_input"
PARAMETER_TABLE = "dev_icamp.icamp_merchant_cluster_algo_param"
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
PARAMETER_REQUIRED_COLUMNS = {
    "start_date",
    "end_date",
    "region",
    "max_transaction_time_interval",
    "transaction_time_interval_weight",
    "min_transaction_number",
    "min_merchant_count",
    "is_daily",
}


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
    parameter_table: str
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


@dataclass(frozen=True)
class HiveAlgorithmParameter:
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    end_exclusive: pd.Timestamp
    region: str
    max_transaction_time_interval: int
    transaction_time_interval_weight: float
    min_transaction_number: int
    min_merchant_count: int
    is_daily: bool


def build_default_hive_task_config() -> HiveTaskConfig:
    return HiveTaskConfig(
        config_path=DEFAULT_CONFIG_PATH,
        source_table=SOURCE_TABLE,
        parameter_table=PARAMETER_TABLE,
        target_table=TARGET_TABLE,
        risk_target_table=RISK_TARGET_TABLE,
        target_temp_table=TARGET_TEMP_TABLE,
        risk_target_temp_table=RISK_TARGET_TEMP_TABLE,
        dt_expression=DEFAULT_DT_EXPRESSION,
    )


def _clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _require_parameter_columns(
    dataframe: pd.DataFrame,
    table_name: str,
) -> pd.DataFrame:
    result = dataframe.copy()
    result.columns = result.columns.astype("string").str.strip()
    missing_columns = sorted(PARAMETER_REQUIRED_COLUMNS.difference(set(result.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 参数表缺少必要字段: "
            f"table={table_name}, missing_columns={missing_columns}"
        )
    return result


def _parse_positive_int(value: object, column: str, table_name: str) -> int:
    text = _clean_text(value)
    try:
        parsed = int(text)
    except ValueError as error:
        raise TransactionDataError(
            "Hive 参数表字段必须是正整数: "
            f"table={table_name}, column={column}, value={text!r}"
        ) from error
    if parsed < 1:
        raise TransactionDataError(
            "Hive 参数表字段必须是正整数: "
            f"table={table_name}, column={column}, value={text!r}"
        )
    return parsed


def _parse_positive_float(value: object, column: str, table_name: str) -> float:
    text = _clean_text(value)
    try:
        parsed = float(text)
    except ValueError as error:
        raise TransactionDataError(
            "Hive 参数表字段必须是正数: "
            f"table={table_name}, column={column}, value={text!r}"
        ) from error
    if parsed <= 0.0:
        raise TransactionDataError(
            "Hive 参数表字段必须是正数: "
            f"table={table_name}, column={column}, value={text!r}"
        )
    return parsed


def _parse_bool(value: object, column: str, table_name: str) -> bool:
    text = _clean_text(value).lower()
    true_values = {"1", "true", "yes", "y", "是"}
    false_values = {"0", "false", "no", "n", "否"}
    if text in true_values:
        return True
    if text in false_values:
        return False
    raise TransactionDataError(
        "Hive 参数表字段必须是布尔值: "
        f"table={table_name}, column={column}, value={text!r}, "
        f"true_values={sorted(true_values)}, false_values={sorted(false_values)}"
    )


def _parse_parameter_date(value: object, column: str, table_name: str) -> pd.Timestamp:
    text = _clean_text(value)
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        raise TransactionDataError(
            "Hive 参数表日期字段解析失败: "
            f"table={table_name}, column={column}, value={text!r}"
        )
    return pd.Timestamp(parsed)


def _has_time_component(value: object) -> bool:
    text = _clean_text(value)
    return any(separator in text for separator in (" ", "T", ":"))


def load_hive_algorithm_parameters(
    parameter_data: pd.DataFrame,
    parameter_table: str,
) -> list[HiveAlgorithmParameter]:
    source = _require_parameter_columns(parameter_data, parameter_table)
    parameters: list[HiveAlgorithmParameter] = []
    for row in source.to_dict("records"):
        region = _clean_text(row["region"])
        if not region:
            raise TransactionDataError(
                "Hive 参数表 region 不能为空: "
                f"table={parameter_table}, row={row}"
            )
        start_date = _parse_parameter_date(
            row["start_date"],
            "start_date",
            parameter_table,
        )
        end_date = _parse_parameter_date(row["end_date"], "end_date", parameter_table)
        if start_date > end_date:
            raise TransactionDataError(
                "Hive 参数表 start_date 不能晚于 end_date: "
                f"table={parameter_table}, start_date={start_date}, end_date={end_date}"
            )
        end_exclusive = (
            end_date + pd.Timedelta(days=1)
            if not _has_time_component(row["end_date"])
            else end_date + pd.Timedelta(nanoseconds=1)
        )
        parameters.append(
            HiveAlgorithmParameter(
                start_date=start_date,
                end_date=end_date,
                end_exclusive=end_exclusive,
                region=region,
                max_transaction_time_interval=_parse_positive_int(
                    row["max_transaction_time_interval"],
                    "max_transaction_time_interval",
                    parameter_table,
                ),
                transaction_time_interval_weight=_parse_positive_float(
                    row["transaction_time_interval_weight"],
                    "transaction_time_interval_weight",
                    parameter_table,
                ),
                min_transaction_number=_parse_positive_int(
                    row["min_transaction_number"],
                    "min_transaction_number",
                    parameter_table,
                ),
                min_merchant_count=_parse_positive_int(
                    row["min_merchant_count"],
                    "min_merchant_count",
                    parameter_table,
                ),
                is_daily=_parse_bool(
                    row["is_daily"],
                    "is_daily",
                    parameter_table,
                ),
            )
        )
    if not parameters:
        raise TransactionDataError(f"Hive 参数表没有可用参数行: table={parameter_table}")
    return parameters


def build_runtime_config(
    parameters: list[HiveAlgorithmParameter],
    parameter_table: str,
) -> AlgorithmRuntimeConfig:
    first = parameters[0]
    inconsistent = [
        parameter
        for parameter in parameters
        if (
            parameter.max_transaction_time_interval
            != first.max_transaction_time_interval
            or parameter.transaction_time_interval_weight
            != first.transaction_time_interval_weight
            or parameter.min_transaction_number != first.min_transaction_number
            or parameter.min_merchant_count != first.min_merchant_count
        )
    ]
    if inconsistent:
        raise TransactionDataError(
            "Hive 参数表同一任务分区内算法参数不一致，无法在一次聚类中混用: "
            f"table={parameter_table}, first={first}, "
            f"inconsistent_count={len(inconsistent)}"
        )
    return AlgorithmRuntimeConfig(
        window_minutes=first.max_transaction_time_interval,
        decay_tau_minutes=first.transaction_time_interval_weight,
        minimum_unique_users=first.min_transaction_number,
        minimum_community_size=first.min_merchant_count,
    )


def build_source_dt_list(parameters: list[HiveAlgorithmParameter]) -> list[str]:
    dates: set[str] = set()
    for parameter in parameters:
        for day in pd.date_range(
            parameter.start_date.normalize(),
            parameter.end_date.normalize(),
            freq="D",
        ):
            dates.add(pd.Timestamp(day).strftime("%Y%m%d"))
    return sorted(dates)


def _parse_transaction_time(
    values: pd.Series,
    timestamp_formats: tuple[str, ...],
    table_name: str,
) -> pd.Series:
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    text_values = values.astype("string").str.strip()
    for timestamp_format in timestamp_formats:
        missing = parsed.isna()
        if not missing.any():
            break
        parsed.loc[missing] = pd.to_datetime(
            text_values.loc[missing],
            format=timestamp_format,
            errors="coerce",
        )
    invalid = parsed.isna()
    if invalid.any():
        examples = values.loc[invalid].head(5).astype(str).tolist()
        raise TransactionDataError(
            "Hive 输入表交易时间解析失败: "
            f"table={table_name}, invalid_rows={int(invalid.sum())}, examples={examples}"
        )
    return parsed


def filter_source_data_by_parameters(
    source_data: pd.DataFrame,
    parameters: list[HiveAlgorithmParameter],
    timestamp_formats: tuple[str, ...],
    source_table: str,
    parameter_table: str,
) -> pd.DataFrame:
    source = source_data.copy()
    source.columns = source.columns.astype("string").str.strip()
    missing_columns = sorted({REGION, RAW_TIMESTAMP}.difference(set(source.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 输入表缺少参数表关联字段: "
            f"source_table={source_table}, parameter_table={parameter_table}, "
            f"missing_columns={missing_columns}"
        )
    transaction_time = _parse_transaction_time(
        source[RAW_TIMESTAMP],
        timestamp_formats,
        source_table,
    )
    matched = pd.Series(False, index=source.index)
    source_region = source[REGION].astype("string").str.strip()
    for parameter in parameters:
        matched = matched | (
            source_region.eq(parameter.region)
            & transaction_time.ge(parameter.start_date)
            & transaction_time.lt(parameter.end_exclusive)
        )
    result = source.loc[matched].copy()
    if result.empty:
        raise TransactionDataError(
            "Hive 参数表没有匹配到输入交易: "
            f"source_table={source_table}, parameter_table={parameter_table}, "
            f"parameter_count={len(parameters)}, source_rows={len(source)}"
        )
    return result.reset_index(drop=True)


def _format_hive_id(value: object) -> str:
    if pd.isna(value) or value == "":
        return ""
    return str(int(value))


def _format_abnormal_status(value: object) -> str:
    status = str(value)
    if status == "active":
        return "normal"
    return status


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
    output["is_abnormal"] = output["status"].map(_format_abnormal_status)
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
            parameter_dt_list = [self.dt_var]
            parameter_data = sd.read_table(
                self.task_config.parameter_table,
                dt=parameter_dt_list,
            )
            parameter_data.columns = parameter_data.columns.astype("string").str.strip()
            parameters = load_hive_algorithm_parameters(
                parameter_data,
                self.task_config.parameter_table,
            )
            runtime_config = build_runtime_config(
                parameters,
                self.task_config.parameter_table,
            )
            config = load_config_with_runtime_parameters(
                self.task_config.config_path,
                runtime_config,
            )
            source_dt_list = build_source_dt_list(parameters)
            logrecord.log_data(
                f"task parameter_dt={parameter_dt_list}, source_dt={source_dt_list}"
            )
            source_data = sd.read_table(
                self.task_config.source_table,
                dt=source_dt_list,
            )
            source_data.columns = source_data.columns.astype("string").str.strip()
            filtered_source_data = filter_source_data_by_parameters(
                source_data,
                parameters,
                config.input.timestamp_formats,
                self.task_config.source_table,
                self.task_config.parameter_table,
            )
            transactions = load_hive_transactions(
                filtered_source_data,
                config.input.timestamp_formats,
                self.dt_var,
                self.task_config.source_table,
            )
            result = run_algorithm_one_from_transactions(
                config,
                transactions,
                "Hive输入表",
                (
                    f"{self.task_config.source_table}, source_dt={source_dt_list}, "
                    f"{self.task_config.parameter_table}, dt={parameter_dt_list}"
                ),
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
                input_rows=len(filtered_source_data),
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
