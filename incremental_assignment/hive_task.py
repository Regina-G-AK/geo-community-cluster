from __future__ import annotations

import datetime
import gc
import multiprocessing
import time
from dataclasses import dataclass
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
from business_district.cross_region import (
    append_cross_region_target_output,
    build_cross_region_target_output,
)
from business_district.errors import TransactionDataError
from business_district.graph import (
    build_pair_statistics,
    build_sparse_graph,
)
from business_district.geo import (
    CoordinatePoint,
    filter_reliable_merchant_coordinates,
    haversine_distance_meters,
)
from business_district.hive_task import (
    HiveAlgorithmParameter,
    PARAMETER_TABLE,
    SourceDataSelection,
    TARGET_COLUMNS,
    build_target_output_dt,
    build_runtime_config,
    build_source_dt_list,
    filter_source_data_by_parameters as filter_initial_source_data_by_parameters,
    load_hive_algorithm_parameters,
    overwrite_target_table,
    split_source_data_by_parameters,
)
from business_district.probes import print_probe
from business_district.status_codes import format_status_code
from business_district.transactions import (
    DT,
    LATITUDE,
    LONGITUDE,
    MERCHANT,
    MERCHANT_CATEGORY,
    RAW_ABNORMAL,
    RAW_BUSINESS_DISTRICT,
    RAW_INTERFERE,
    RAW_MERCHANT,
    REGION,
    TIMESTAMP,
    load_hive_transactions,
    merge_visits,
    preserve_storename,
)
from incremental_assignment.models import AssignmentConfig

SOURCE_TABLE = "dev_icamp.icamp_merchant_cluster_algo_input"
TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output"
TARGET_TEMP_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output_incremental_tmp"
NORMAL_STATUS = "normal"
SUSPECT_ISOLATED_STATUS = "suspect_isolated"
# 暂停疑似跨区域判断，保留状态名供后续恢复
# SUSPECT_CROSS_REGION_STATUS = "suspect_cross_region"
EXCLUDED_INCREMENTAL_ABNORMAL_STATUSES: FrozenSet[str] = frozenset(
    {"2", "5", "6"}
)


@dataclass(frozen=True)
class CommunityMember:
    storename: str
    community_id: str
    is_anchor: bool


@dataclass(frozen=True)
class SourceCommunityState:
    existing_storenames: FrozenSet[str]
    members: Dict[str, CommunityMember]


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
    missing_columns = sorted(required_columns.difference(set(dataframe.columns)))
    if missing_columns:
        raise TransactionDataError(
            "Hive 表缺少必要字段: "
            f"table={table_name}, missing_columns={missing_columns}"
        )
    return dataframe


def _clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_incremental_abnormal_statuses(values: pd.Series) -> pd.Series:
    normalized = values.astype("string").str.strip().fillna("")
    numeric_statuses = pd.to_numeric(normalized, errors="coerce")
    for status_code in ("1", "2", "3", "4", "5", "6", "7"):
        numeric_status = normalized.ne("") & numeric_statuses.eq(int(status_code))
        normalized.loc[numeric_status] = status_code
    return normalized


def split_incremental_abnormal_statuses(
    source_data: pd.DataFrame,
    source_table: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    source = _require_columns(
        source_data,
        {RAW_ABNORMAL},
        source_table,
    )
    abnormal_statuses = normalize_incremental_abnormal_statuses(
        source[RAW_ABNORMAL]
    )
    eligible = ~abnormal_statuses.isin(EXCLUDED_INCREMENTAL_ABNORMAL_STATUSES)
    included = source.loc[eligible].copy()
    excluded = source.loc[~eligible].copy()
    included[RAW_ABNORMAL] = abnormal_statuses.loc[eligible]
    excluded[RAW_ABNORMAL] = abnormal_statuses.loc[~eligible]
    return included, excluded


def filter_incremental_abnormal_statuses(
    source_data: pd.DataFrame,
    source_table: str,
) -> pd.DataFrame:
    source = _require_columns(
        source_data,
        {RAW_ABNORMAL},
        source_table,
    )
    abnormal_statuses = normalize_incremental_abnormal_statuses(
        source[RAW_ABNORMAL]
    )
    eligible = ~abnormal_statuses.isin(EXCLUDED_INCREMENTAL_ABNORMAL_STATUSES)
    included = source.loc[eligible].copy()
    included[RAW_ABNORMAL] = abnormal_statuses.loc[eligible]
    return included


def preserve_existing_merchants_in_source_selection(
    source_selection: SourceDataSelection,
) -> SourceDataSelection:
    existing_cross_region = (
        source_selection.cross_region[RAW_BUSINESS_DISTRICT]
        .astype("string")
        .str.strip()
        .fillna("")
        .ne("")
    )
    included = pd.concat(
        [
            source_selection.included,
            source_selection.cross_region.loc[existing_cross_region],
        ],
        ignore_index=False,
        copy=False,
    ).sort_index()
    cross_region = source_selection.cross_region.loc[
        ~existing_cross_region
    ]
    return SourceDataSelection(
        included=included,
        cross_region=cross_region,
    )


def _filter_source_data_by_parameters(
    source_data: pd.DataFrame,
    parameters: List[HiveAlgorithmParameter],
    source_table: str,
    parameter_table: str,
) -> pd.DataFrame:
    return filter_initial_source_data_by_parameters(
        source_data,
        parameters,
        source_table,
        parameter_table,
    )


def load_incremental_transactions(
    source_data: pd.DataFrame,
    timestamp_formats: Tuple[str, ...],
    dt_value: str,
    source_table: str,
) -> pd.DataFrame:
    source = _require_columns(
        source_data,
        {RAW_MERCHANT, RAW_INTERFERE, RAW_ABNORMAL},
        source_table,
    )
    source = filter_incremental_abnormal_statuses(source, source_table)
    if DT not in source.columns:
        source[DT] = dt_value
    source[RAW_MERCHANT] = source[RAW_MERCHANT].astype("string")
    source[RAW_INTERFERE] = source[RAW_INTERFERE].astype("string").str.strip()
    interfere_values = source[RAW_INTERFERE].fillna("").str.upper()
    interfered_storenames = set(
        source.loc[
            interfere_values.eq("Y"),
            RAW_MERCHANT,
        ].dropna()
    )
    if interfered_storenames:
        source = source.loc[
            ~source[RAW_MERCHANT].isin(interfered_storenames)
        ]
    transactions = load_hive_transactions(
        source,
        timestamp_formats,
        dt_value,
        source_table,
    )
    return transactions


def load_source_candidates(
    transactions: pd.DataFrame,
    existing_storenames: Set[str],
    dt_value: str,
) -> List[HiveCandidateMerchant]:
    selected = transactions[[MERCHANT, TIMESTAMP, REGION, MERCHANT_CATEGORY]]
    ordered = selected.sort_values([MERCHANT, TIMESTAMP])
    latest = ordered.drop_duplicates(subset=[MERCHANT], keep="last")

    candidates: List[HiveCandidateMerchant] = []
    for storename_value, _, region_value, merchant_category in latest.itertuples(
        index=False,
        name=None,
    ):
        storename = preserve_storename(storename_value)
        if storename in existing_storenames:
            continue
        candidates.append(
            HiveCandidateMerchant(
                storename=storename,
                region=_clean_text(region_value),
                dt=dt_value,
                merchant_category=int(merchant_category),
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
    for storename_value, community_id_value in source[
        [MERCHANT, RAW_BUSINESS_DISTRICT]
    ].itertuples(index=False, name=None):
        storename = preserve_storename(storename_value)
        community_id = _clean_text(community_id_value)
        if not storename or not community_id:
            continue
        if storename not in community_ids_by_storename:
            community_ids_by_storename[storename] = set()
        community_ids_by_storename[storename].add(community_id)

    conflicts = {
        storename: sorted(community_ids)
        for storename, community_ids in community_ids_by_storename.items()
        if len(community_ids) > 1
    }
    if conflicts:
        examples = list(sorted(conflicts.items()))[:10]
        raise TransactionDataError(
            "Hive 增量输入的同一商户对应多个已有商圈 ID: "
            f"table={source_table}, conflicted_merchants={len(conflicts)}, "
            f"examples={examples}"
        )

    members = {
        storename: CommunityMember(
            storename=storename,
            community_id=next(iter(community_ids)),
            is_anchor=False,
        )
        for storename, community_ids in community_ids_by_storename.items()
    }

    if not members:
        raise TransactionDataError(
            "Hive 输入表没有可用存量商圈成员: "
            f"table={source_table}, business_district_column={RAW_BUSINESS_DISTRICT}"
        )

    return SourceCommunityState(
        existing_storenames=frozenset(community_ids_by_storename),
        members=members,
    )


def build_incremental_source_community_state(
    transactions: pd.DataFrame,
    source_table: str,
) -> SourceCommunityState:
    source = _require_columns(
        transactions,
        {MERCHANT, RAW_ABNORMAL, RAW_BUSINESS_DISTRICT},
        source_table,
    )
    source = filter_incremental_abnormal_statuses(source, source_table)
    return build_source_community_state(source, source_table)


def _community_sort_key(community_id: str) -> Tuple[int, str]:
    try:
        return int(community_id), community_id
    except ValueError:
        return 0, community_id


def build_latest_merchant_coordinates(
    transactions: pd.DataFrame,
    maximum_merchants_per_coordinate: int,
) -> Dict[str, CoordinatePoint]:
    source = _require_columns(
        transactions,
        {MERCHANT, TIMESTAMP, LONGITUDE, LATITUDE},
        "transactions",
    )
    selected = source.loc[
        source[LONGITUDE].notna() & source[LATITUDE].notna(),
        [MERCHANT, TIMESTAMP, LONGITUDE, LATITUDE],
    ]
    if selected.empty:
        return {}
    latest = (
        selected.sort_values([MERCHANT, TIMESTAMP])
        .drop_duplicates(subset=[MERCHANT], keep="last")
    )
    merchant_coordinates: Dict[str, CoordinatePoint] = {}
    for storename_value, _, longitude, latitude in latest.itertuples(
        index=False,
        name=None,
    ):
        storename = preserve_storename(storename_value)
        if not storename:
            continue
        merchant_coordinates[storename] = CoordinatePoint(
            item_id=storename,
            longitude=float(longitude),
            latitude=float(latitude),
        )
    return filter_reliable_merchant_coordinates(
        merchant_coordinates,
        maximum_merchants_per_coordinate,
    )


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
    if process_count == 1:
        rows = _build_incremental_rows(
            (
                tuple(candidates),
                graph,
                members,
                merchant_coordinates,
                assignment_config,
                timestamp,
            )
        )
        return pd.DataFrame(rows, columns=TARGET_COLUMNS)

    chunk_count = min(len(candidates), process_count * 4)
    chunk_size = (
        (len(candidates) + chunk_count - 1) // chunk_count
        if chunk_count > 0
        else 0
    )
    candidate_chunks: Tuple[Tuple[HiveCandidateMerchant, ...], ...]
    if chunk_size > 0:
        candidate_chunks = tuple(
            tuple(candidates[index : index + chunk_size])
            for index in range(0, len(candidates), chunk_size)
        )
    else:
        candidate_chunks = tuple()
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
    if len(tasks) <= 1:
        row_chunks = [_build_incremental_rows(task) for task in tasks]
    else:
        with multiprocessing.Pool(processes=process_count) as pool:
            row_chunks = pool.map(_build_incremental_rows, tasks)
    rows = [row for row_chunk in row_chunks for row in row_chunk]
    return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def build_existing_merchant_output(
    transactions: pd.DataFrame,
    members: Dict[str, CommunityMember],
    output_dt: str,
    update_time: datetime.datetime,
) -> pd.DataFrame:
    source = _require_columns(
        transactions,
        {MERCHANT, TIMESTAMP, REGION},
        "transactions",
    )
    latest = (
        source[[MERCHANT, TIMESTAMP, REGION]]
        .sort_values([MERCHANT, TIMESTAMP, REGION])
        .drop_duplicates(subset=[MERCHANT], keep="last")
    )
    existing = latest.loc[latest[MERCHANT].isin(members)]
    missing_storenames = sorted(set(members).difference(set(existing[MERCHANT])))
    if missing_storenames:
        raise TransactionDataError(
            "增量输入缺少已有商圈成员的商户信息: "
            f"missing_storenames={missing_storenames[:10]}"
        )

    timestamp = update_time.strftime("%Y-%m-%d %H:%M:%S")
    rows: List[IncrementalOutputRow] = []
    for storename_value, _, region_value in existing.itertuples(
        index=False,
        name=None,
    ):
        storename = preserve_storename(storename_value)
        rows.append(
            {
                "storename": storename,
                "community_id": members[storename].community_id,
                "previous_community_id": "",
                "region": _clean_text(region_value),
                "is_interfere": "N",
                "update_time": timestamp,
                "is_abnormal": format_status_code(NORMAL_STATUS),
                "is_position": 0,
                "dt": str(output_dt),
            }
        )
    return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def build_excluded_status_output(
    source_data: pd.DataFrame,
    timestamp_formats: Tuple[str, ...],
    source_table: str,
    output_dt: str,
    update_time: datetime.datetime,
) -> pd.DataFrame:
    if source_data.empty:
        return pd.DataFrame(columns=TARGET_COLUMNS)

    transactions = load_hive_transactions(
        source_data,
        timestamp_formats,
        output_dt,
        source_table,
    )
    source = _require_columns(
        transactions,
        {
            MERCHANT,
            TIMESTAMP,
            REGION,
            RAW_ABNORMAL,
            RAW_BUSINESS_DISTRICT,
        },
        source_table,
    )
    source[RAW_ABNORMAL] = normalize_incremental_abnormal_statuses(
        source[RAW_ABNORMAL]
    )
    latest = (
        source[
            [
                MERCHANT,
                TIMESTAMP,
                REGION,
                RAW_ABNORMAL,
                RAW_BUSINESS_DISTRICT,
            ]
        ]
        .sort_values([MERCHANT, TIMESTAMP], kind="stable")
        .drop_duplicates(subset=[MERCHANT], keep="last")
    )
    excluded = latest.loc[
        latest[RAW_ABNORMAL].isin(EXCLUDED_INCREMENTAL_ABNORMAL_STATUSES)
    ]
    timestamp = update_time.strftime("%Y-%m-%d %H:%M:%S")
    rows: List[IncrementalOutputRow] = []
    for (
        storename_value,
        _,
        region_value,
        abnormal_status,
        community_id,
    ) in excluded.itertuples(index=False, name=None):
        rows.append(
            {
                "storename": preserve_storename(storename_value),
                "community_id": _clean_text(community_id),
                "previous_community_id": "",
                "region": _clean_text(region_value),
                "is_interfere": "N",
                "update_time": timestamp,
                "is_abnormal": _clean_text(abnormal_status),
                "is_position": 0,
                "dt": str(output_dt),
            }
        )
    return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def apply_excluded_status_output(
    target_output: pd.DataFrame,
    excluded_status_output: pd.DataFrame,
) -> pd.DataFrame:
    if excluded_status_output.empty:
        return target_output.reset_index(drop=True)

    excluded_storenames: Set[str] = set(
        excluded_status_output["storename"].astype(str)
    )
    retained = target_output.loc[
        ~target_output["storename"].isin(excluded_storenames)
    ]
    return (
        pd.concat(
            [retained, excluded_status_output],
            ignore_index=True,
            copy=False,
        )[TARGET_COLUMNS]
        .sort_values("storename")
        .reset_index(drop=True)
    )


def combine_incremental_output(
    candidate_output: pd.DataFrame,
    existing_output: pd.DataFrame,
    transactions: pd.DataFrame,
    source_table: str,
) -> pd.DataFrame:
    expected_storenames = set(transactions[MERCHANT].astype(str))
    combined = pd.concat(
        [candidate_output, existing_output],
        ignore_index=True,
        copy=False,
    )
    duplicated = combined["storename"].duplicated(keep=False)
    if duplicated.any():
        examples = sorted(
            set(combined.loc[duplicated, "storename"].astype(str))
        )[:10]
        raise TransactionDataError(
            "增量结果同一商户存在多条记录: "
            f"table={source_table}, examples={examples}"
        )
    actual_storenames = set(combined["storename"].astype(str))
    missing_storenames = sorted(expected_storenames.difference(actual_storenames))
    unexpected_storenames = sorted(actual_storenames.difference(expected_storenames))
    if missing_storenames or unexpected_storenames:
        raise TransactionDataError(
            "增量结果未完整覆盖输入商户: "
            f"table={source_table}, missing_storenames={missing_storenames[:10]}, "
            f"unexpected_storenames={unexpected_storenames[:10]}"
        )
    return combined[TARGET_COLUMNS].sort_values("storename").reset_index(drop=True)


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
            del parameter_data
            output_dt = build_target_output_dt(
                parameters,
                self.task_config.parameter_table,
            )
            print_probe(
                "incremental_hive.parameters_ready",
                f"parameter_rows={len(parameters)}, output_dt={output_dt}",
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
            print_probe(
                "incremental_hive.source_read_started",
                f"partition_count={len(source_dt_list)}",
            )
            raw_source_data = sd.read_table(
                self.task_config.source_table,
                dt=source_dt_list,
            )
            raw_source_rows = len(raw_source_data)
            parameter_selection = split_source_data_by_parameters(
                raw_source_data,
                parameters,
                self.task_config.source_table,
                self.task_config.parameter_table,
            )
            del raw_source_data
            gc.collect()
            parameter_source_data = pd.concat(
                [
                    parameter_selection.included,
                    parameter_selection.cross_region,
                ],
                ignore_index=False,
                copy=False,
            ).sort_index()
            if parameter_source_data.empty:
                raise TransactionDataError(
                    "Hive 参数表没有匹配到输入交易: "
                    f"source_table={self.task_config.source_table}, "
                    f"parameter_table={self.task_config.parameter_table}, "
                    f"parameter_count={len(parameters)}, "
                    f"source_rows={raw_source_rows}"
                )
            update_time = datetime.datetime.now().astimezone()
            excluded_status_output = build_excluded_status_output(
                parameter_source_data,
                self.task_config.timestamp_formats,
                self.task_config.source_table,
                output_dt,
                update_time,
            )
            del parameter_source_data
            gc.collect()
            parameter_source_rows = (
                len(parameter_selection.included)
                + len(parameter_selection.cross_region)
            )
            included_source_data = filter_incremental_abnormal_statuses(
                parameter_selection.included,
                self.task_config.source_table,
            )
            cross_region_source_data = filter_incremental_abnormal_statuses(
                parameter_selection.cross_region,
                self.task_config.source_table,
            )
            excluded_status_rows = parameter_source_rows - (
                len(included_source_data) + len(cross_region_source_data)
            )
            del parameter_selection
            gc.collect()
            source_selection = preserve_existing_merchants_in_source_selection(
                SourceDataSelection(
                    included=included_source_data,
                    cross_region=cross_region_source_data,
                )
            )
            del included_source_data
            del cross_region_source_data
            filtered_source_data = source_selection.included
            source_rows = len(filtered_source_data)
            cross_region_rows = len(source_selection.cross_region)
            print_probe(
                "incremental_hive.source_ready",
                f"included_rows={source_rows}, "
                f"cross_region_rows={cross_region_rows}, "
                f"excluded_status_rows={excluded_status_rows}",
            )
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
                community_rows = 0
                candidate_count = 0
                del filtered_source_data
                gc.collect()
            else:
                transactions = load_incremental_transactions(
                    filtered_source_data,
                    self.task_config.timestamp_formats,
                    self.dt_var,
                    self.task_config.source_table,
                )
                del filtered_source_data
                gc.collect()
                visits = merge_visits(transactions, self.task_config.visit_config)
                print_probe(
                    "incremental_hive.pair_statistics_started",
                    f"transaction_rows={len(transactions)}, visit_rows={len(visits)}",
                )
                statistics = build_pair_statistics(
                    visits,
                    cooccurrence_config,
                    algorithm_config.runtime.process_count,
                )
                del visits
                gc.collect()
                print_probe(
                    "incremental_hive.pair_statistics_ready",
                    f"pair_count={len(statistics.strengths)}, "
                    f"merchant_count={len(statistics.merchant_visit_counts)}",
                )
                print_probe("incremental_hive.graph_started", "")
                graph = build_sparse_graph(
                    statistics,
                    cooccurrence_config,
                    self.task_config.graph_config,
                )
                print_probe(
                    "incremental_hive.graph_ready",
                    f"node_count={graph.number_of_nodes()}, "
                    f"edge_count={graph.number_of_edges()}",
                )
                del statistics
                gc.collect()
                source_state = build_incremental_source_community_state(
                    transactions,
                    self.task_config.source_table,
                )
                candidates = load_source_candidates(
                    transactions,
                    set(source_state.existing_storenames),
                    output_dt,
                )
                coordinates = build_latest_merchant_coordinates(
                    transactions,
                    algorithm_config.geo.maximum_merchants_per_coordinate,
                )
                community_rows = len(source_state.members)
                candidate_count = len(candidates)
                print_probe(
                    "incremental_hive.assignment_started",
                    f"candidate_count={candidate_count}, "
                    f"community_member_count={community_rows}",
                )
                candidate_output = build_incremental_output(
                    candidates,
                    graph,
                    source_state.members,
                    coordinates,
                    self.task_config.assignment_config,
                    update_time,
                    algorithm_config.runtime.process_count,
                )
                del candidates
                del coordinates
                del graph
                gc.collect()
                existing_output = build_existing_merchant_output(
                    transactions,
                    source_state.members,
                    output_dt,
                    update_time,
                )
                target_output = combine_incremental_output(
                    candidate_output,
                    existing_output,
                    transactions,
                    self.task_config.source_table,
                )
                del candidate_output
                del existing_output
                del source_state
                del transactions
                gc.collect()
                print_probe(
                    "incremental_hive.assignment_ready",
                    f"output_rows={len(target_output)}",
                )
            target_output = append_cross_region_target_output(
                target_output,
                cross_region_output,
            )
            target_output = apply_excluded_status_output(
                target_output,
                excluded_status_output,
            )
            del cross_region_output
            del excluded_status_output
            gc.collect()
            if target_output.empty:
                raise TransactionDataError(
                    "参数匹配交易中没有可输出的分类 1、2 商户: "
                    f"source_table={self.task_config.source_table}, "
                    f"included_rows={source_rows}, "
                    f"cross_region_rows={cross_region_rows}"
                )
            print_probe(
                "incremental_hive.target_write_started",
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
                "incremental_hive.target_write_complete",
                f"output_rows={len(target_output)}",
            )
            logrecord.log_data(
                f"incremental taskrun seconds={time.time() - total_start:.2f}, "
                f"candidate_count={candidate_count}, "
                f"inserted_rows={len(target_output)}"
            )
            return HiveTaskSummary(
                dt=output_dt,
                source_rows=source_rows,
                community_rows=community_rows,
                candidate_count=candidate_count,
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
        "项目不再提供代码内默认配置，请运行项目根目录的 "
        "run_hive_business_district.py"
    )


if __name__ == "__main__":
    main()
