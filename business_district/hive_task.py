from __future__ import annotations

import datetime
import gc
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Dict, Iterator, List, Set, Tuple

import networkx as nx
import pandas as pd
import spdbccc_data as sd
from spdbccc_data import dtDate
from spdbccc_data import formattedExc
from spdbccc_data import loging as logrecord
from spdbccc_data import mountCheck

from business_district.config import (
    AppConfig,
    AlgorithmRuntimeConfig,
    apply_runtime_parameters,
)
from business_district.cross_region import (
    append_cross_region_target_output,
    build_cross_region_target_output,
)
from business_district.errors import TransactionDataError
from business_district.pipeline import run_algorithm_one_from_transactions
from business_district.probes import print_dataframe_probe, print_probe
from business_district.region_filter import build_storename_city_mask
from business_district.status_codes import format_status_code
from business_district.transactions import (
    MERCHANT,
    RAW_BUSINESS_DISTRICT,
    RAW_MERCHANT,
    REGION,
    load_hive_transactions,
    preserve_storename,
)
from incremental_assignment.models import AssignmentConfig

SOURCE_TABLE = "dev_icamp.icamp_merchant_cluster_algo_input"
PARAMETER_TABLE = "dev_icamp.icamp_merchant_cluster_algo_param"
TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output"
HIVE_TABLE_ROOT = Path("/appdata/project/fid_bg_icmp/tbl")
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
    "min_transaction_number",
    "min_merchant_count",
    "is_daily",
}


def _split_hive_table(full_table_name: str) -> Tuple[str, str]:
    parts = full_table_name.strip().split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Hive 表名必须是 db.table 格式: table={full_table_name!r}"
        )
    return parts[0], parts[1]


def _hive_storage_table_name(full_table_name: str) -> str:
    text = full_table_name.strip()
    parts = text.split(".")
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[1]
    if len(parts) == 1 and parts[0]:
        return parts[0]
    raise TransactionDataError(f"Hive 表名格式错误: table={full_table_name!r}")


def _hive_partition_path(table_name: str, dt_value: str) -> Path:
    return HIVE_TABLE_ROOT / _hive_storage_table_name(table_name) / f"dt={dt_value}"


def _prepare_hive_partition(table_name: str, dt_value: str) -> None:
    sd.read_table(table_name, dt=[dt_value])


def _hive_partition_part_files(
    partition_path: Path,
    table_name: str,
    dt_value: str,
) -> List[Path]:
    if not partition_path.exists():
        return []
    files = [
        path
        for path in sorted(partition_path.rglob("part*"))
        if path.is_file()
    ]
    if not files:
        return []
    return files


def _read_hive_part_file(
    file_path: Path,
    table_name: str,
    dt_value: str,
) -> pd.DataFrame:
    # print_probe(
    #     "Hive分片读取开始",
    #     f"table={table_name!r}, dt={dt_value!r}, path={str(file_path)!r}",
    # )
    try:
        dataframe = pd.read_parquet(file_path)
    except (OSError, ValueError, ImportError) as error:
        raise TransactionDataError(
            "Hive 分片 parquet 读取失败: "
            f"table={table_name}, dt={dt_value}, path={file_path}, reason={error}"
        ) from error
    # print_dataframe_probe("Hive分片读取完成", dataframe)
    return dataframe


def _iter_partitioned_hive_table_parts(
    table_name: str,
    dt_values: List[str],
) -> Iterator[Tuple[pd.DataFrame, str]]:
    if not dt_values:
        raise TransactionDataError(f"Hive 读表 dt 不能为空: table={table_name}")

    for dt_value in dt_values:
        dt_text = str(dt_value)
        _prepare_hive_partition(table_name, dt_text)
        partition_path = _hive_partition_path(table_name, dt_text)
        for file_path in _hive_partition_part_files(
            partition_path,
            table_name,
            dt_text,
        ):
            dataframe = _read_hive_part_file(file_path, table_name, dt_text)
            if dataframe.empty:
                del dataframe
                continue
            yield dataframe, dt_text
            del dataframe


def read_partitioned_hive_table(
    table_name: str,
    dt_values: List[str],
) -> pd.DataFrame:
    # print_probe(
    #     "Hive分区表读取开始",
    #     f"table={table_name!r}, dt_values={dt_values!r}",
    # )
    dataframes: List[pd.DataFrame] = []
    for dataframe, dt_text in _iter_partitioned_hive_table_parts(
        table_name,
        dt_values,
    ):
        dataframe["dt"] = dt_text
        dataframes.append(dataframe)
        del dataframe

    if not dataframes:
        raise TransactionDataError(
            "Hive 日期范围内没有非空分区: "
            f"table={table_name}, dt_values={dt_values}"
        )

    # print_probe("Hive分片合并开始", f"part_count={len(dataframes)}")
    result = pd.concat(dataframes, ignore_index=True, copy=False)
    dataframes.clear()
    # print_dataframe_probe("Hive分片合并完成", result)
    return result


@dataclass(frozen=True)
class HiveTaskConfig:
    algorithm_config: AppConfig
    assignment_config: AssignmentConfig
    source_table: str
    parameter_table: str
    target_table: str
    target_temp_table: str
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
    min_transaction_number: int
    min_merchant_count: int
    is_daily: bool


@dataclass(frozen=True)
class SourceDataSelection:
    included: pd.DataFrame
    cross_region: pd.DataFrame


def _clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _require_parameter_columns(
    dataframe: pd.DataFrame,
    table_name: str,
) -> pd.DataFrame:
    # dataframe.columns = dataframe.columns.astype("string").str.strip()
    missing_columns = sorted(
        PARAMETER_REQUIRED_COLUMNS.difference(set(dataframe.columns))
    )
    if missing_columns:
        raise TransactionDataError(
            "Hive 参数表缺少必要字段: "
            f"table={table_name}, missing_columns={missing_columns}"
        )
    return dataframe


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


def _parse_bool(value: object, column: str, table_name: str) -> bool:
    text = _clean_text(value)
    if text == "1":
        return True
    if text == "0":
        return False
    raise TransactionDataError(
        "Hive 参数表字段必须是 0 或 1: "
        f"table={table_name}, column={column}, value={text!r}"
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
) -> List[HiveAlgorithmParameter]:
    source = _require_parameter_columns(parameter_data, parameter_table)
    parameters: List[HiveAlgorithmParameter] = []
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
    parameters: List[HiveAlgorithmParameter],
    parameter_table: str,
    decay_tau_minutes: float,
) -> AlgorithmRuntimeConfig:
    first = parameters[0]
    inconsistent = [
        parameter
        for parameter in parameters
        if (
            parameter.max_transaction_time_interval
            != first.max_transaction_time_interval
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
        decay_tau_minutes=decay_tau_minutes,
        minimum_unique_users=first.min_transaction_number,
        minimum_community_size=first.min_merchant_count,
    )


def build_source_dt_list(parameters: List[HiveAlgorithmParameter]) -> List[str]:
    dates: Set[str] = set()
    for parameter in parameters:
        start_date = parameter.start_date.normalize()
        end_date = parameter.end_date.normalize()
        if start_date == end_date:
            dates.add(start_date.strftime("%Y%m%d"))
            continue
        start_month = start_date.replace(day=1)
        end_month = end_date.replace(day=1)
        for month_start in pd.date_range(
            start_month,
            end_month,
            freq="MS",
        ):
            month_end = pd.Timestamp(month_start) + pd.offsets.MonthEnd(0)
            dates.add(month_end.strftime("%Y%m%d"))
    return sorted(dates)


def build_target_output_dt(
    parameters: List[HiveAlgorithmParameter],
    parameter_table: str,
) -> str:
    output_dates = {
        parameter.end_date.normalize().strftime("%Y%m%d")
        for parameter in parameters
    }
    if len(output_dates) != 1:
        raise TransactionDataError(
            "Hive 参数表同一任务分区内的 end_date 必须一致，无法写入多个目标分区: "
            f"table={parameter_table}, end_dates={sorted(output_dates)}"
        )
    return next(iter(output_dates))


def split_source_data_by_parameters(
    source_data: pd.DataFrame,
    parameters: List[HiveAlgorithmParameter],
    source_table: str,
    parameter_table: str,
) -> SourceDataSelection:
    # source.columns = source.columns.astype("string").str.strip()
    missing_columns = sorted(
        {RAW_MERCHANT, REGION}.difference(set(source_data.columns))
    )
    if missing_columns:
        raise TransactionDataError(
            "Hive 输入表缺少参数表关联字段: "
            f"source_table={source_table}, parameter_table={parameter_table}, "
            f"missing_columns={missing_columns}"
        )
    included = pd.Series(False, index=source_data.index)
    cross_region = pd.Series(False, index=source_data.index)
    source_region = source_data[REGION]
    for parameter in parameters:
        region_matched = source_region.eq(parameter.region)
        storename_matched = build_storename_city_mask(
            source_data[RAW_MERCHANT],
            parameter.region,
        )
        included |= region_matched & storename_matched
        cross_region |= (
            region_matched & ~storename_matched
        )
    return SourceDataSelection(
        included=source_data.loc[included].copy(),
        cross_region=source_data.loc[cross_region].copy(),
    )


def _select_source_data_by_parameters(
    source_data: pd.DataFrame,
    parameters: List[HiveAlgorithmParameter],
    source_table: str,
    parameter_table: str,
) -> pd.DataFrame:
    return split_source_data_by_parameters(
        source_data,
        parameters,
        source_table,
        parameter_table,
    ).included


def filter_source_data_by_parameters(
    source_data: pd.DataFrame,
    parameters: List[HiveAlgorithmParameter],
    source_table: str,
    parameter_table: str,
) -> pd.DataFrame:
    # print_dataframe_probe("Hive参数过滤输入", source_data)
    result = _select_source_data_by_parameters(
        source_data,
        parameters,
        source_table,
        parameter_table,
    )
    # print_dataframe_probe("Hive参数过滤结果", result)
    if result.empty:
        raise TransactionDataError(
            "Hive 参数表没有匹配到输入交易: "
            f"source_table={source_table}, parameter_table={parameter_table}, "
            f"parameter_count={len(parameters)}, source_rows={len(source_data)}"
        )
    return result.reset_index(drop=True)


def read_filtered_source_hive_table(
    table_name: str,
    dt_values: List[str],
    parameters: List[HiveAlgorithmParameter],
    parameter_table: str,
) -> pd.DataFrame:
    selection = read_source_hive_table_by_parameters(
        table_name,
        dt_values,
        parameters,
        parameter_table,
    )
    if selection.included.empty:
        raise TransactionDataError(
            "Hive 参数表没有匹配到可参与聚类的输入交易: "
            f"source_table={table_name}, parameter_table={parameter_table}, "
            f"parameter_count={len(parameters)}, "
            f"cross_region_rows={len(selection.cross_region)}"
        )
    return selection.included


def read_source_hive_table_by_parameters(
    table_name: str,
    dt_values: List[str],
    parameters: List[HiveAlgorithmParameter],
    parameter_table: str,
) -> SourceDataSelection:
    included_dataframes: List[pd.DataFrame] = []
    cross_region_dataframes: List[pd.DataFrame] = []
    empty_template = pd.DataFrame()
    source_rows = 0
    for dataframe, dt_text in _iter_partitioned_hive_table_parts(
        table_name,
        dt_values,
    ):
        source_rows += len(dataframe)
        dataframe["dt"] = dt_text
        selection = split_source_data_by_parameters(
            dataframe,
            parameters,
            table_name,
            parameter_table,
        )
        empty_template = dataframe.iloc[0:0].copy()
        if not selection.included.empty:
            included_dataframes.append(selection.included)
        if not selection.cross_region.empty:
            cross_region_dataframes.append(selection.cross_region)
        del selection, dataframe

    if source_rows == 0:
        raise TransactionDataError(
            "Hive 日期范围内没有非空分区: "
            f"table={table_name}, dt_values={dt_values}"
        )
    if not included_dataframes and not cross_region_dataframes:
        raise TransactionDataError(
            "Hive 参数表没有匹配到输入交易: "
            f"source_table={table_name}, parameter_table={parameter_table}, "
            f"parameter_count={len(parameters)}, source_rows={source_rows}"
        )

    included = (
        pd.concat(included_dataframes, ignore_index=True, copy=False)
        if included_dataframes
        else empty_template.copy()
    )
    included_dataframes.clear()
    cross_region = (
        pd.concat(cross_region_dataframes, ignore_index=True, copy=False)
        if cross_region_dataframes
        else empty_template.copy()
    )
    cross_region_dataframes.clear()
    gc.collect()
    return SourceDataSelection(
        included=included,
        cross_region=cross_region,
    )


def _format_hive_id(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    try:
        numeric = float(text)
    except ValueError:
        return text
    if numeric.is_integer():
        return str(int(numeric))
    return text


def _format_abnormal_status(value: object) -> str:
    return format_status_code(value)


def load_existing_community_ids(
    transactions: pd.DataFrame,
    source_table: str,
) -> Dict[str, str]:
    required_columns = {MERCHANT, RAW_BUSINESS_DISTRICT}
    missing_columns = sorted(required_columns.difference(set(transactions.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 初始化交易缺少已有商圈字段: "
            f"table={source_table}, missing_columns={missing_columns}"
        )

    community_ids_by_storename: Dict[str, Set[str]] = {}
    for storename_value, community_id_value in transactions[
        [MERCHANT, RAW_BUSINESS_DISTRICT]
    ].itertuples(index=False, name=None):
        storename = preserve_storename(storename_value)
        community_id = _clean_text(community_id_value)
        if not community_id:
            continue
        community_ids_by_storename.setdefault(storename, set()).add(community_id)

    conflicts = {
        storename: sorted(community_ids)
        for storename, community_ids in community_ids_by_storename.items()
        if len(community_ids) > 1
    }
    if conflicts:
        examples = list(sorted(conflicts.items()))[:10]
        raise TransactionDataError(
            "Hive 初始化输入的同一商户对应多个已有商圈 ID: "
            f"table={source_table}, conflicted_merchants={len(conflicts)}, "
            f"examples={examples}"
        )
    return {
        storename: next(iter(community_ids))
        for storename, community_ids in community_ids_by_storename.items()
    }


def merge_initial_assignment_output(
    clustered_output: pd.DataFrame,
    assignment_output: pd.DataFrame,
    existing_community_ids: Dict[str, str],
    minimum_community_size: int,
) -> pd.DataFrame:
    missing_columns = sorted(set(TARGET_COLUMNS).difference(clustered_output.columns))
    if missing_columns:
        raise TransactionDataError(
            "初始化聚类结果缺少输出字段: "
            f"missing_columns={missing_columns}"
        )
    existing_storenames = set(existing_community_ids)
    existing_rows = (
        clustered_output.loc[
            clustered_output["storename"].isin(existing_storenames)
        ]
        .drop_duplicates(subset=["storename"], keep="first")
    )
    missing_existing_storenames = sorted(
        existing_storenames.difference(set(existing_rows["storename"]))
    )
    if missing_existing_storenames:
        raise TransactionDataError(
            "初始化聚类结果缺少需要保留原商圈 ID 的商户: "
            f"missing_storenames={missing_existing_storenames[:10]}"
        )
    existing_rows["community_id"] = existing_rows["storename"].map(
        existing_community_ids
    )
    existing_rows["previous_community_id"] = ""
    existing_rows["is_abnormal"] = format_status_code("normal")
    existing_rows["is_position"] = 0

    assigned_rows = assignment_output.loc[
        assignment_output["community_id"].ne("")
    ]
    assigned_rows = assigned_rows.drop_duplicates(
        subset=["storename", "community_id"],
        keep="first",
    )
    assigned_storenames = set(assigned_rows["storename"])
    residual_rows = clustered_output.loc[
        ~clustered_output["storename"].isin(
            existing_storenames.union(assigned_storenames)
        )
    ]
    combined = pd.concat(
        [existing_rows, assigned_rows, residual_rows],
        ignore_index=True,
        copy=False,
    )
    del existing_rows, assigned_rows, residual_rows

    existing_ids = set(existing_community_ids.values())
    new_community_mask = (
        combined["community_id"].ne("")
        & ~combined["community_id"].isin(existing_ids)
    )
    new_community_sizes = (
        combined.loc[new_community_mask]
        .groupby("community_id")["storename"]
        .nunique()
    )
    small_new_community_ids = set(
        new_community_sizes.loc[
            new_community_sizes < minimum_community_size
        ].index
    )
    small_new_community_mask = combined["community_id"].isin(
        small_new_community_ids
    )
    combined.loc[small_new_community_mask, "community_id"] = ""
    combined.loc[small_new_community_mask, "is_abnormal"] = format_status_code(
        "suspect_isolated"
    )
    combined.loc[small_new_community_mask, "is_position"] = 0
    combined["community_id"] = combined["community_id"].map(_format_hive_id)
    return (
        combined[TARGET_COLUMNS]
        .sort_values(["community_id", "storename"])
        .reset_index(drop=True)
    )


def build_initial_assignment_output(
    clustered_output: pd.DataFrame,
    transactions: pd.DataFrame,
    transaction_graph: nx.Graph,
    config: AppConfig,
    assignment_config: AssignmentConfig,
    source_table: str,
    output_dt: str,
    minimum_community_size: int,
) -> pd.DataFrame:
    existing_community_ids = load_existing_community_ids(
        transactions,
        source_table,
    )
    if not existing_community_ids:
        return clustered_output

    from incremental_assignment.hive_task import (
        build_incremental_output,
        build_latest_merchant_coordinates,
        build_source_community_state,
        load_source_candidates,
    )

    community_state = build_source_community_state(transactions, source_table)
    candidates = load_source_candidates(
        transactions,
        set(community_state.existing_storenames),
        output_dt,
    )
    merchant_coordinates = build_latest_merchant_coordinates(
        transactions,
        config.geo.maximum_merchants_per_coordinate,
    )
    assignment_output = build_incremental_output(
        candidates,
        transaction_graph,
        community_state.members,
        merchant_coordinates,
        assignment_config,
        datetime.datetime.now().astimezone(),
        config.runtime.process_count,
    )
    del candidates, merchant_coordinates, community_state
    gc.collect()
    result = merge_initial_assignment_output(
        clustered_output,
        assignment_output,
        existing_community_ids,
        minimum_community_size,
    )
    del assignment_output
    return result


def build_hive_target_output(
    business_results: pd.DataFrame,
    output_dt: str,
    minimum_community_size: int,
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
        ]
    ].copy()
    output["storename"] = output["storename"].astype(str)
    output["community_id"] = output["community_id"].map(_format_hive_id)
    active = output.loc[output["community_id"].ne("")]
    community_sizes = active.groupby("community_id")["storename"].nunique()
    small_community_ids = set(
        community_sizes.loc[
            community_sizes < minimum_community_size
        ].index
    )
    small_community_mask = output["community_id"].isin(small_community_ids)
    output.loc[small_community_mask, "community_id"] = ""
    output.loc[small_community_mask, "status"] = "suspect_isolated"
    output.loc[small_community_mask, "is_position"] = 0
    output["previous_community_id"] = ""
    output["region"] = output["region"].astype(str)
    output["is_interfere"] = "N"
    output["update_time"] = output["update_time"].astype(str)
    output["is_abnormal"] = output["status"].map(_format_abnormal_status)
    output["is_position"] = output["is_position"].astype(int)
    output["dt"] = str(output_dt)
    output = output.drop(columns=["status"])
    output = output[TARGET_COLUMNS]
    return output.reset_index(drop=True)


def overwrite_target_table(
    sd: ModuleType,
    result: pd.DataFrame,
    table_name: str,
    temp_table_name: str,
    output_dt: str,
) -> None:
    if result.empty:
        raise TransactionDataError("结果为空")

    select_columns = ", ".join(
        f"source.{column}" for column in TARGET_SELECT_COLUMNS
    )
    write_df = result[TARGET_SELECT_COLUMNS]
    sd.execute_sql(f"drop table if exists {temp_table_name}")
    try:
        sd.write_table(write_df, temp_table_name, debug=False, dt=None)
        sd.execute_sql(
            f"""
            insert overwrite table {table_name}
            partition (dt='{str(output_dt)}')
            select {select_columns}
            from {temp_table_name} source
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
            self.dt_var = dtDate.dt_date(self.task_config.dt_expression)
            logrecord.log_data(f"task dt={self.dt_var}")
            parameter_dt_list = [self.dt_var]
            parameter_data = sd.read_table(
                self.task_config.parameter_table,
                dt=parameter_dt_list,
            )
            parameters = load_hive_algorithm_parameters(
                parameter_data,
                self.task_config.parameter_table,
            )
            del parameter_data
            output_dt = build_target_output_dt(
                parameters,
                self.task_config.parameter_table,
            )
            print_probe(
                "initial_hive.parameters_ready",
                f"parameter_rows={len(parameters)}, output_dt={output_dt}",
            )
            runtime_config = build_runtime_config(
                parameters,
                self.task_config.parameter_table,
                self.task_config.algorithm_config.cooccurrence.decay_tau_minutes,
            )
            config = apply_runtime_parameters(
                self.task_config.algorithm_config,
                runtime_config,
            )
            source_dt_list = build_source_dt_list(parameters)
            logrecord.log_data(
                f"task parameter_dt={parameter_dt_list}, source_dt={source_dt_list}"
            )
            print_probe(
                "initial_hive.source_read_started",
                f"partition_count={len(source_dt_list)}",
            )
            source_selection = read_source_hive_table_by_parameters(
                self.task_config.source_table,
                source_dt_list,
                parameters,
                self.task_config.parameter_table,
            )
            filtered_source_data = source_selection.included
            source_rows = len(filtered_source_data)
            cross_region_rows = len(source_selection.cross_region)
            print_probe(
                "initial_hive.source_ready",
                f"included_rows={source_rows}, "
                f"cross_region_rows={cross_region_rows}",
            )
            update_time = datetime.datetime.now().astimezone()
            cross_region_output = build_cross_region_target_output(
                source_selection.cross_region,
                source_selection.included,
                self.task_config.source_table,
                output_dt,
                update_time,
            )
            del source_selection
            gc.collect()
            if filtered_source_data.empty:
                target_output = pd.DataFrame(columns=TARGET_COLUMNS)
                output_directory = str(config.output.directory)
                del filtered_source_data
                gc.collect()
            else:
                transactions = load_hive_transactions(
                    filtered_source_data,
                    config.input.timestamp_formats,
                    self.dt_var,
                    self.task_config.source_table,
                )
                del filtered_source_data
                gc.collect()
                print_probe(
                    "initial_hive.transactions_ready",
                    f"transaction_rows={len(transactions)}",
                )
                result = run_algorithm_one_from_transactions(
                    config,
                    transactions,
                )
                output_directory = result.summary.output_directory
                target_output = build_hive_target_output(
                    result.business_results,
                    output_dt,
                    runtime_config.minimum_community_size,
                )
                target_output = build_initial_assignment_output(
                    target_output,
                    transactions,
                    result.transaction_graph,
                    config,
                    self.task_config.assignment_config,
                    self.task_config.source_table,
                    output_dt,
                    runtime_config.minimum_community_size,
                )
                del result, transactions
                gc.collect()
            target_output = append_cross_region_target_output(
                target_output,
                cross_region_output,
            )
            del cross_region_output
            gc.collect()
            print_probe(
                "initial_hive.output_ready",
                f"output_rows={len(target_output)}",
            )
            if target_output.empty:
                raise TransactionDataError(
                    "参数匹配交易中没有可输出的分类 1、2 商户: "
                    f"source_table={self.task_config.source_table}, "
                    f"included_rows={source_rows}, "
                    f"cross_region_rows={cross_region_rows}"
                )
            print_probe(
                "initial_hive.target_write_started",
                f"output_rows={len(target_output)}",
            )
            overwrite_target_table(
                sd,
                target_output,
                self.task_config.target_table,
                self.task_config.target_temp_table,
                output_dt,
            )
            print_probe(
                "initial_hive.target_write_complete",
                f"output_rows={len(target_output)}",
            )
            logrecord.log_data(
                f"taskrun seconds={time.time() - total_start:.2f}, "
                f"output_rows={len(target_output)}, "
                f"output_directory={output_directory}"
            )
            summary = HiveTaskSummary(
                dt=output_dt,
                input_rows=source_rows,
                output_rows=len(target_output),
                output_directory=output_directory,
                target_table=self.task_config.target_table,
            )
        except Exception as error:
            formattedExc.formatted_exc()
            raise

        logrecord.log_data("python task log record.")
        return summary

    def destroy(self) -> None:
        try:
            sd.execute_sql(
                f"drop table if exists {self.task_config.target_temp_table}"
            )
        except Exception as error:
            raise RuntimeError(
                "Hive 临时表清理失败: "
                f"table={self.task_config.target_temp_table}"
            ) from error


def run_hive_task(task_config: HiveTaskConfig) -> HiveTaskSummary:
    task = TaskMain(task_config)
    try:
        task.check()
        return task.taskrun()
    finally:
        task.destroy()


def main() -> None:
    raise RuntimeError(
        "项目不再提供代码内默认配置，请运行项目根目录的 "
        "run_hive_business_district.py"
    )


if __name__ == "__main__":
    main()
