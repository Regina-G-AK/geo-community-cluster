from __future__ import annotations

import datetime
import multiprocessing
import time
from dataclasses import dataclass
from types import ModuleType
from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Union

import networkx as nx
import pandas as pd
import spdbccc_data as sd
from spdbccc_data import dtDate
from spdbccc_data import formattedExc
from spdbccc_data import loging as logrecord
from spdbccc_data import mountCheck

from business_district.config import (
    AppConfig,
    CooccurrenceConfig,
    GraphConfig,
    VisitConfig,
    apply_runtime_parameters,
)
from business_district.errors import TransactionDataError
from business_district.graph import (
    PairStatistics,
    build_pair_statistics,
    build_sparse_graph,
)
from business_district.geo import (
    CoordinatePoint,
    haversine_distance_meters,
)
from business_district.hive_task import (
    HiveAlgorithmParameter,
    PARAMETER_TABLE,
    build_runtime_config,
    build_source_dt_list,
    load_hive_algorithm_parameters,
)
from business_district.intermediate import (
    build_pair_statistics_path,
    read_pair_statistics,
    write_pair_statistics,
)
from business_district.status_codes import format_status_code
from business_district.transactions import (
    CARD,
    DT,
    FLOW_NUMBER,
    HIVE_SOURCE_COLUMNS,
    LATITUDE,
    LONGITUDE,
    MERCHANT,
    MERCHANT_CATEGORY,
    RAW_ABNORMAL,
    RAW_BUSINESS_DISTRICT,
    RAW_CARD,
    RAW_INTERFERE,
    RAW_LATITUDE,
    RAW_LONGITUDE,
    RAW_MERCHANT,
    RAW_MERCHANT_CATEGORY,
    RAW_TIMESTAMP,
    REGION,
    TIMESTAMP,
    filter_clustering_merchant_categories,
    keep_first_hive_flow_number_rows,
    merge_visits,
    _parse_coordinates,
)
from incremental_assignment.models import AssignmentConfig

SOURCE_TABLE = "dev_icamp.icamp_merchant_cluster_algo_input"
TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output"
TARGET_TEMP_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output_incremental_tmp"
NORMAL_STATUS = "normal"
SUSPECT_ISOLATED_STATUS = "suspect_isolated"
# 暂停疑似跨区域判断，保留状态名供后续恢复
# SUSPECT_CROSS_REGION_STATUS = "suspect_cross_region"
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
SOURCE_REQUIRED_COLUMNS = set(HIVE_SOURCE_COLUMNS)


@dataclass(frozen=True)
class CommunityMember:
    storename: str
    community_id: str
    is_anchor: bool


@dataclass(frozen=True)
class SourceCommunityState:
    existing_storenames: FrozenSet[str]
    members: Dict[str, CommunityMember]
    skipped_multi_community_storenames: FrozenSet[str]


@dataclass(frozen=True)
class HiveCandidateMerchant:
    storename: str
    region: str
    dt: str
    merchant_category: int


@dataclass(frozen=True)
class HiveTaskConfig:
    algorithm_config: AppConfig
    timestamp_formats: Tuple[str, ...]
    visit_config: VisitConfig
    graph_config: GraphConfig
    assignment_config: AssignmentConfig
    source_table: str
    parameter_table: str
    target_table: str
    target_temp_table: str
    dt_expression: str


@dataclass(frozen=True)
class HiveTaskSummary:
    dt: str
    source_rows: int
    community_rows: int
    candidate_count: int
    inserted_rows: int
    target_table: str


def _require_columns(
    dataframe: pd.DataFrame,
    required_columns: Set[str],
    table_name: str,
) -> pd.DataFrame:
    result = dataframe.copy()
    # result.columns = result.columns.astype("string").str.strip()
    missing_columns = sorted(required_columns.difference(set(result.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 表缺少必要字段: "
            f"table={table_name}, missing_columns={missing_columns}"
        )
    return result


def _clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


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


def _parse_transaction_time(
    values: pd.Series,
    timestamp_formats: Tuple[str, ...],
    table_name: str,
) -> pd.Series:
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    # text_values = values.astype("string").str.strip()
    for timestamp_format in timestamp_formats:
        missing = parsed.isna()
        if not missing.any():
            break
        parsed.loc[missing] = pd.to_datetime(
            values.loc[missing],
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


def _filter_source_data_by_parameters(
    source_data: pd.DataFrame,
    parameters: List[HiveAlgorithmParameter],
    source_table: str,
    parameter_table: str,
) -> pd.DataFrame:
    missing_columns = sorted({REGION}.difference(set(source_data.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 输入表缺少参数表关联字段: "
            f"source_table={source_table}, parameter_table={parameter_table}, "
            f"missing_columns={missing_columns}"
        )
    parameter_regions = {parameter.region for parameter in parameters}
    matched = source_data[REGION].isin(parameter_regions)
    result = source_data.loc[matched].copy()
    if result.empty:
        raise TransactionDataError(
            "Hive 参数表没有匹配到输入交易: "
            f"source_table={source_table}, parameter_table={parameter_table}, "
            f"parameter_count={len(parameters)}, source_rows={len(source_data)}"
        )
    return result.reset_index(drop=True)


def load_incremental_transactions(
    source_data: pd.DataFrame,
    timestamp_formats: Tuple[str, ...],
    dt_value: str,
    source_table: str,
) -> pd.DataFrame:
    source = source_data.copy()
    # source.columns = source.columns.astype("string").str.strip()
    if DT not in source.columns:
        source[DT] = dt_value
    source = _require_columns(source, SOURCE_REQUIRED_COLUMNS.union({DT}), source_table)
    source = filter_clustering_merchant_categories(source, source_table)
    selected = source[list(HIVE_SOURCE_COLUMNS) + [DT]].copy()
    selected[RAW_CARD] = selected[RAW_CARD].astype("string").str.strip()
    selected[FLOW_NUMBER] = selected[FLOW_NUMBER].astype("string").str.strip()
    selected[RAW_MERCHANT] = selected[RAW_MERCHANT].astype("string").str.strip()
    selected[RAW_TIMESTAMP] = selected[RAW_TIMESTAMP].astype("string").str.strip()
    selected[RAW_LONGITUDE] = selected[RAW_LONGITUDE].astype("string").str.strip()
    selected[RAW_LATITUDE] = selected[RAW_LATITUDE].astype("string").str.strip()
    selected[REGION] = selected[REGION].astype("string").str.strip()
    selected[RAW_INTERFERE] = selected[RAW_INTERFERE].astype("string").str.strip()
    selected[RAW_ABNORMAL] = selected[RAW_ABNORMAL].astype("string").str.strip()
    selected[RAW_BUSINESS_DISTRICT] = (
        selected[RAW_BUSINESS_DISTRICT].astype("string").str.strip()
    )
    selected[RAW_MERCHANT_CATEGORY] = selected[RAW_MERCHANT_CATEGORY].astype(int)
    selected[DT] = selected[DT].astype("string").str.strip()
    invalid = (
        selected[RAW_CARD].isna()
        | selected[RAW_CARD].eq("")
        | selected[FLOW_NUMBER].isna()
        | selected[FLOW_NUMBER].eq("")
        | selected[RAW_MERCHANT].isna()
        | selected[RAW_MERCHANT].eq("")
        | selected[REGION].isna()
        | selected[REGION].eq("")
        | selected[DT].isna()
        | selected[DT].eq("")
    )
    if invalid.any():
        examples = selected.loc[invalid].head(5).to_dict(orient="records")
        raise TransactionDataError(
            "Hive 输入表包含空卡号、流水号、商户名、地区或日期: "
            f"table={source_table}, invalid_rows={int(invalid.sum())}, examples={examples}"
        )
    interfere_values = selected[RAW_INTERFERE].fillna("").str.upper()
    interfered_storenames = set(
        selected.loc[
            interfere_values.eq("Y"),
            RAW_MERCHANT,
        ].dropna()
    )
    if interfered_storenames:
        selected = selected.loc[
            ~selected[RAW_MERCHANT].isin(interfered_storenames)
        ].copy()
    selected = keep_first_hive_flow_number_rows(selected)
    selected[TIMESTAMP] = _parse_transaction_time(
        selected[RAW_TIMESTAMP],
        timestamp_formats,
        source_table,
    )
    selected = _parse_coordinates(selected)
    result = selected.rename(
        columns={
            RAW_CARD: CARD,
            RAW_MERCHANT: MERCHANT,
            RAW_MERCHANT_CATEGORY: MERCHANT_CATEGORY,
        }
    )
    return result[
        [
            CARD,
            FLOW_NUMBER,
            MERCHANT,
            MERCHANT_CATEGORY,
            TIMESTAMP,
            LONGITUDE,
            LATITUDE,
            REGION,
            RAW_INTERFERE,
            RAW_ABNORMAL,
            RAW_BUSINESS_DISTRICT,
            DT,
        ]
    ].sort_values([CARD, TIMESTAMP, MERCHANT]).reset_index(drop=True)


def load_source_candidates(
    transactions: pd.DataFrame,
    existing_storenames: Set[str],
    dt_value: str,
) -> List[HiveCandidateMerchant]:
    selected = transactions[[MERCHANT, TIMESTAMP, REGION, MERCHANT_CATEGORY]].copy()
    ordered = selected.sort_values([MERCHANT, TIMESTAMP])
    latest = ordered.drop_duplicates(subset=[MERCHANT], keep="last")

    candidates: List[HiveCandidateMerchant] = []
    for row in latest.to_dict("records"):
        storename = _clean_text(row[MERCHANT])
        if storename in existing_storenames:
            continue
        candidates.append(
            HiveCandidateMerchant(
                storename=storename,
                region=_clean_text(row[REGION]),
                dt=dt_value,
                merchant_category=int(row[MERCHANT_CATEGORY]),
            )
        )
    return candidates


def build_incremental_cooccurrence_config(
    parameters: List[HiveAlgorithmParameter],
    parameter_table: str,
    decay_tau_minutes: float,
) -> CooccurrenceConfig:
    runtime_config = build_runtime_config(
        parameters,
        parameter_table,
        decay_tau_minutes,
    )
    return CooccurrenceConfig(
        window_minutes=runtime_config.window_minutes,
        decay_tau_minutes=runtime_config.decay_tau_minutes,
        minimum_unique_users=runtime_config.minimum_unique_users,
    )


def merge_pair_statistics(
    base_statistics: PairStatistics,
    incremental_statistics: PairStatistics,
) -> PairStatistics:
    strengths: Dict[Tuple[str, str], float] = dict(base_statistics.strengths)
    supports: Dict[Tuple[str, str], int] = dict(base_statistics.supports)
    merchant_visit_counts: Dict[str, int] = dict(base_statistics.merchant_visit_counts)

    for pair, strength in incremental_statistics.strengths.items():
        if pair in strengths:
            if pair not in supports:
                raise TransactionDataError(
                    f"商户对中间文件缺少 support: pair={pair}"
                )
            strengths[pair] = float(strengths[pair]) + float(strength)
        else:
            if pair not in incremental_statistics.supports:
                raise TransactionDataError(
                    f"增量商户对统计缺少 support: pair={pair}"
                )
            strengths[pair] = float(strength)
            supports[pair] = int(incremental_statistics.supports[pair])

    for merchant_id, visit_count in incremental_statistics.merchant_visit_counts.items():
        merchant_visit_counts[merchant_id] = (
            int(merchant_visit_counts.get(merchant_id, 0)) + int(visit_count)
        )

    return PairStatistics(
        strengths=strengths,
        supports=supports,
        merchant_visit_counts=merchant_visit_counts,
    )


def build_source_community_state(
    transactions: pd.DataFrame,
    source_table: str,
) -> SourceCommunityState:
    source = _require_columns(
        transactions,
        {MERCHANT, RAW_BUSINESS_DISTRICT},
        source_table,
    )
    community_ids_by_storename: Dict[str, Set[str]] = {}
    for row in source[[MERCHANT, RAW_BUSINESS_DISTRICT]].to_dict("records"):
        storename = _clean_text(row[MERCHANT])
        community_id = _format_hive_id(row[RAW_BUSINESS_DISTRICT])
        if not storename or not community_id:
            continue
        if storename not in community_ids_by_storename:
            community_ids_by_storename[storename] = set()
        community_ids_by_storename[storename].add(community_id)

    members: Dict[str, CommunityMember] = {}
    skipped_storenames: Set[str] = set()
    for storename, community_ids in community_ids_by_storename.items():
        if len(community_ids) != 1:
            skipped_storenames.add(storename)
            continue
        community_id = sorted(community_ids)[0]
        members[storename] = CommunityMember(
            storename=storename,
            community_id=community_id,
            is_anchor=False,
        )

    if not members:
        raise TransactionDataError(
            "Hive 输入表没有可用存量商圈成员: "
            f"table={source_table}, business_district_column={RAW_BUSINESS_DISTRICT}"
        )

    return SourceCommunityState(
        existing_storenames=frozenset(community_ids_by_storename),
        members=members,
        skipped_multi_community_storenames=frozenset(skipped_storenames),
    )


def _community_sort_key(community_id: str) -> Tuple[int, str]:
    try:
        return int(community_id), community_id
    except ValueError:
        return 0, community_id


def build_latest_merchant_coordinates(
    transactions: pd.DataFrame,
) -> Dict[str, CoordinatePoint]:
    source = _require_columns(
        transactions,
        {MERCHANT, TIMESTAMP, LONGITUDE, LATITUDE},
        "transactions",
    )
    selected = source.loc[
        source[LONGITUDE].notna() & source[LATITUDE].notna()
    ].copy()
    if selected.empty:
        return {}
    latest = (
        selected.sort_values([MERCHANT, TIMESTAMP])
        .drop_duplicates(subset=[MERCHANT], keep="last")
    )
    return {
        _clean_text(row[MERCHANT]): CoordinatePoint(
            item_id=_clean_text(row[MERCHANT]),
            longitude=float(row[LONGITUDE]),
            latitude=float(row[LATITUDE]),
        )
        for row in latest.to_dict("records")
        if _clean_text(row[MERCHANT])
    }


def _strongest_pmi_community_id(
    candidate: HiveCandidateMerchant,
    graph: nx.Graph,
    members: Dict[str, CommunityMember],
) -> Optional[str]:
    if candidate.storename not in graph:
        return None
    candidates: List[Tuple[float, str, str]] = []
    for neighbor in graph.neighbors(candidate.storename):
        member = members.get(str(neighbor))
        if member is None:
            continue
        edge_weight = float(graph[candidate.storename][neighbor].get("weight", 0.0))
        if edge_weight <= 0.0:
            continue
        candidates.append((edge_weight, member.community_id, str(neighbor)))
    if not candidates:
        return None
    selected = sorted(
        candidates,
        key=lambda item: (
            -item[0],
            _community_sort_key(item[1]),
            item[2],
        ),
    )[0]
    return selected[1]


def _nearest_geographic_community_id(
    candidate: HiveCandidateMerchant,
    merchant_coordinates: Dict[str, CoordinatePoint],
    members: Dict[str, CommunityMember],
    assignment_config: AssignmentConfig,
) -> Optional[str]:
    candidate_point = merchant_coordinates.get(candidate.storename)
    if candidate_point is None:
        return None
    candidates: List[Tuple[float, str, str]] = []
    for member_name, member in members.items():
        member_point = merchant_coordinates.get(member_name)
        if member_point is None:
            continue
        distance = haversine_distance_meters(
            candidate_point.longitude,
            candidate_point.latitude,
            member_point.longitude,
            member_point.latitude,
        )
        if distance > assignment_config.community_assignment_distance_meters:
            continue
        candidates.append((distance, member.community_id, member_name))
    if not candidates:
        return None
    selected = sorted(
        candidates,
        key=lambda item: (
            item[0],
            _community_sort_key(item[1]),
            item[2],
        ),
    )[0]
    return selected[1]


IncrementalOutputRow = Dict[str, Union[str, int]]
IncrementalOutputTask = Tuple[
    Tuple[HiveCandidateMerchant, ...],
    nx.Graph,
    Dict[str, CommunityMember],
    Dict[str, CoordinatePoint],
    AssignmentConfig,
    str,
]


def _build_incremental_row(
    candidate: HiveCandidateMerchant,
    graph: nx.Graph,
    members: Dict[str, CommunityMember],
    merchant_coordinates: Dict[str, CoordinatePoint],
    assignment_config: AssignmentConfig,
    timestamp: str,
) -> List[IncrementalOutputRow]:
    if candidate.storename in merchant_coordinates:
        assigned_community_id = _nearest_geographic_community_id(
            candidate,
            merchant_coordinates,
            members,
            assignment_config,
        )
    else:
        assigned_community_id = _strongest_pmi_community_id(
            candidate,
            graph,
            members,
        )
    community_id = assigned_community_id or ""
    status = NORMAL_STATUS if community_id else SUSPECT_ISOLATED_STATUS
    return [
        {
            "storename": candidate.storename,
            "community_id": community_id,
            "previous_community_id": "",
            "region": candidate.region,
            "is_interfere": "N",
            "update_time": timestamp,
            "is_abnormal": format_status_code(status),
            "is_position": 0,
            "dt": candidate.dt,
        }
    ]


def _build_incremental_rows(task: IncrementalOutputTask) -> List[IncrementalOutputRow]:
    (
        candidates,
        graph,
        members,
        merchant_coordinates,
        assignment_config,
        timestamp,
    ) = task
    return [
        row
        for candidate in candidates
        for row in _build_incremental_row(
            candidate,
            graph,
            members,
            merchant_coordinates,
            assignment_config,
            timestamp,
        )
    ]


def build_incremental_output(
    candidates: List[HiveCandidateMerchant],
    graph: nx.Graph,
    members: Dict[str, CommunityMember],
    merchant_coordinates: Dict[str, CoordinatePoint],
    assignment_config: AssignmentConfig,
    update_time: datetime.datetime,
    process_count: int,
) -> pd.DataFrame:
    if process_count < 1:
        raise ValueError(f"进程数必须不小于 1: process_count={process_count}")
    if assignment_config.community_assignment_distance_meters <= 0.0:
        raise TransactionDataError(
            "增量归属地理距离阈值必须大于 0: "
            f"community_assignment_distance_meters="
            f"{assignment_config.community_assignment_distance_meters}"
        )
    timestamp = update_time.strftime("%Y-%m-%d %H:%M:%S")
    chunk_count = min(len(candidates), process_count * 4)
    chunk_size = (
        (len(candidates) + chunk_count - 1) // chunk_count
        if chunk_count > 0
        else 0
    )
    candidate_chunks = (
        tuple(
            tuple(candidates[index : index + chunk_size])
            for index in range(0, len(candidates), chunk_size)
        )
        if chunk_size > 0
        else tuple()
    )
    tasks: List[IncrementalOutputTask] = [
        (
            chunk,
            graph,
            members,
            merchant_coordinates,
            assignment_config,
            timestamp,
        )
        for chunk in candidate_chunks
    ]
    if process_count == 1 or len(tasks) <= 1:
        row_chunks = [_build_incremental_rows(task) for task in tasks]
    else:
        with multiprocessing.Pool(processes=process_count) as pool:
            row_chunks = pool.map(_build_incremental_rows, tasks)
    rows = [row for row_chunk in row_chunks for row in row_chunk]
    return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def insert_new_target_rows(
    sd_module: ModuleType,
    result: pd.DataFrame,
    table_name: str,
    temp_table_name: str,
    output_dt: str,
) -> None:
    if result.empty:
        return
    select_columns = ", ".join(f"source.{column}" for column in TARGET_SELECT_COLUMNS)
    write_df = result[TARGET_SELECT_COLUMNS].reset_index(drop=True)
    sd_module.execute_sql(f"drop table if exists {temp_table_name}")
    try:
        sd_module.write_table(write_df, temp_table_name, debug=False, dt=None)
        sd_module.execute_sql(
            f"""
            insert into table {table_name}
            partition (dt={output_dt})
            select {select_columns}
            from {temp_table_name} source
            """
        )
    finally:
        sd_module.execute_sql(f"drop table if exists {temp_table_name}")


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
            logrecord.log_data(f"incremental task dt={self.dt_var}")
            parameter_dt_list = [self.dt_var]
            parameter_data = sd.read_table(
                self.task_config.parameter_table,
                dt=parameter_dt_list,
            )
            parameters = load_hive_algorithm_parameters(
                parameter_data,
                self.task_config.parameter_table,
            )
            runtime_config = build_runtime_config(
                parameters,
                self.task_config.parameter_table,
                self.task_config.algorithm_config.cooccurrence.decay_tau_minutes,
            )
            algorithm_config = apply_runtime_parameters(
                self.task_config.algorithm_config,
                runtime_config,
            )
            cooccurrence_config = CooccurrenceConfig(
                window_minutes=runtime_config.window_minutes,
                decay_tau_minutes=runtime_config.decay_tau_minutes,
                minimum_unique_users=runtime_config.minimum_unique_users,
            )
            source_dt_list = build_source_dt_list(parameters)
            logrecord.log_data(
                f"incremental task parameter_dt={parameter_dt_list}, "
                f"source_dt={source_dt_list}"
            )
            source_data = sd.read_table(self.task_config.source_table, dt=source_dt_list)
            filtered_source_data = _filter_source_data_by_parameters(
                source_data,
                parameters,
                self.task_config.source_table,
                self.task_config.parameter_table,
            )
            transactions = load_incremental_transactions(
                filtered_source_data,
                self.task_config.timestamp_formats,
                self.dt_var,
                self.task_config.source_table,
            )
            visits = merge_visits(transactions, self.task_config.visit_config)
            incremental_statistics = build_pair_statistics(
                visits,
                cooccurrence_config,
                algorithm_config.runtime.process_count,
            )
            pair_statistics_path = build_pair_statistics_path(
                algorithm_config.output.directory,
                algorithm_config.city.code,
            )
            statistics = merge_pair_statistics(
                read_pair_statistics(pair_statistics_path),
                incremental_statistics,
            )
            write_pair_statistics(statistics, pair_statistics_path)
            graph = build_sparse_graph(
                statistics,
                cooccurrence_config,
                self.task_config.graph_config,
            )
            source_state = build_source_community_state(
                transactions,
                self.task_config.source_table,
            )
            candidates = load_source_candidates(
                transactions,
                set(source_state.existing_storenames),
                self.dt_var,
            )
            coordinates = build_latest_merchant_coordinates(transactions)
            target_output = build_incremental_output(
                candidates,
                graph,
                source_state.members,
                coordinates,
                self.task_config.assignment_config,
                datetime.datetime.now().astimezone(),
                algorithm_config.runtime.process_count,
            )
            insert_new_target_rows(
                sd,
                target_output,
                self.task_config.target_table,
                self.task_config.target_temp_table,
                self.dt_var,
            )
            logrecord.log_data(
                f"incremental taskrun seconds={time.time() - total_start:.2f}, "
                f"candidate_count={len(candidates)}, inserted_rows={len(target_output)}, "
                f"skipped_multi_community_count="
                f"{len(source_state.skipped_multi_community_storenames)}"
            )
            return HiveTaskSummary(
                dt=self.dt_var,
                source_rows=len(filtered_source_data),
                community_rows=len(source_state.members),
                candidate_count=len(candidates),
                inserted_rows=len(target_output),
                target_table=self.task_config.target_table,
            )
        except Exception:
            formattedExc.formatted_exc()
            raise

    def destroy(self) -> None:
        try:
            sd.execute_sql(f"drop table if exists {self.task_config.target_temp_table}")
        except Exception as error:
            raise RuntimeError(
                f"Hive 临时表清理失败: table={self.task_config.target_temp_table}"
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
        "项目不再提供代码内默认配置，请通过 "
        "notebooks/run_hive_business_district.ipynb 构造 HiveTaskConfig 并运行"
    )


if __name__ == "__main__":
    main()
