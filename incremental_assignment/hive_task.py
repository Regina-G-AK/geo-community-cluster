from __future__ import annotations

import datetime
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import networkx as nx
import pandas as pd
import spdbccc_data as sd
from spdbccc_data import dtDate
from spdbccc_data import formattedExc
from spdbccc_data import loging as logrecord
from spdbccc_data import mountCheck
from spdbccc_data import task as taskfinish

from business_district.config import load_config as load_algorithm_config
from business_district.errors import TransactionDataError
from business_district.graph import build_sparse_graph, build_pair_statistics
from business_district.transactions import (
    CARD,
    DT,
    FLOW_NUMBER,
    MERCHANT,
    RAW_CARD,
    RAW_MERCHANT,
    RAW_TIMESTAMP,
    REGION,
    TIMESTAMP,
    keep_first_hive_flow_number_rows,
    merge_visits,
)
from incremental_assignment.config import load_config as load_assignment_config
from incremental_assignment.models import AssignmentConfig

SOURCE_TABLE = "dev_icamp.icamp_merchant_cluster_algo_input"
COMMUNITY_TABLE = "dev_icamp.icamp_cluster_algo_output"
TARGET_TABLE = "dev_icamp.icamp_cluster_algo_output"
TARGET_TEMP_TABLE = "dev_icamp.icamp_cluster_algo_output_incremental_tmp"
DEFAULT_ASSIGNMENT_CONFIG_PATH = Path("configs/incremental_shanghai.ini")
DEFAULT_ALGORITHM_CONFIG_PATH = Path("configs/shanghai.ini")
DEFAULT_DT_EXPRESSION = "T-1"
NORMAL_STATUS = "normal"
SUSPECT_ISOLATED_STATUS = "suspect_isolated"
SUSPECT_CROSS_REGION_STATUS = "suspect_cross_region"
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
SOURCE_REQUIRED_COLUMNS = {
    RAW_CARD,
    FLOW_NUMBER,
    RAW_MERCHANT,
    RAW_TIMESTAMP,
    REGION,
    DT,
}
COMMUNITY_REQUIRED_COLUMNS = {
    "storename",
    "community_id",
    "is_position",
    "is_abnormal",
}


@dataclass(frozen=True)
class CommunityMember:
    storename: str
    community_id: str
    is_anchor: bool


@dataclass(frozen=True)
class HiveCandidateMerchant:
    storename: str
    region: str
    dt: str


@dataclass(frozen=True)
class HiveTaskConfig:
    assignment_config_path: Path
    algorithm_config_path: Path
    source_table: str
    community_table: str
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


def build_default_hive_task_config() -> HiveTaskConfig:
    return HiveTaskConfig(
        assignment_config_path=DEFAULT_ASSIGNMENT_CONFIG_PATH,
        algorithm_config_path=DEFAULT_ALGORITHM_CONFIG_PATH,
        source_table=SOURCE_TABLE,
        community_table=COMMUNITY_TABLE,
        target_table=TARGET_TABLE,
        target_temp_table=TARGET_TEMP_TABLE,
        dt_expression=DEFAULT_DT_EXPRESSION,
    )


def _require_columns(
    dataframe: pd.DataFrame,
    required_columns: set[str],
    table_name: str,
) -> pd.DataFrame:
    result = dataframe.copy()
    result.columns = result.columns.astype("string").str.strip()
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


def _parse_position(value: object, storename: str, table_name: str) -> int:
    text = _clean_text(value)
    if not text:
        return 0
    try:
        parsed = int(text)
    except ValueError as error:
        raise TransactionDataError(
            "Hive 商圈表 is_position 字段格式错误: "
            f"table={table_name}, storename={storename}, value={text}"
        ) from error
    if parsed not in {0, 1}:
        raise TransactionDataError(
            "Hive 商圈表 is_position 字段只能是 0 或 1: "
            f"table={table_name}, storename={storename}, value={text}"
        )
    return parsed


def _format_hive_id(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except ValueError as error:
        raise TransactionDataError(f"Hive 商圈 ID 格式错误: value={text}") from error


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


def load_incremental_transactions(
    source_data: pd.DataFrame,
    timestamp_formats: tuple[str, ...],
    source_table: str,
) -> pd.DataFrame:
    source = _require_columns(source_data, SOURCE_REQUIRED_COLUMNS, source_table)
    selected = source[
        [RAW_CARD, FLOW_NUMBER, RAW_MERCHANT, RAW_TIMESTAMP, REGION, DT]
    ].copy()
    selected[RAW_CARD] = selected[RAW_CARD].astype("string").str.strip()
    selected[FLOW_NUMBER] = selected[FLOW_NUMBER].astype("string").str.strip()
    selected[RAW_MERCHANT] = selected[RAW_MERCHANT].astype("string").str.strip()
    selected[RAW_TIMESTAMP] = selected[RAW_TIMESTAMP].astype("string").str.strip()
    selected[REGION] = selected[REGION].astype("string").str.strip()
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
    selected = keep_first_hive_flow_number_rows(selected)
    selected[TIMESTAMP] = _parse_transaction_time(
        selected[RAW_TIMESTAMP],
        timestamp_formats,
        source_table,
    )
    result = selected.rename(
        columns={
            RAW_CARD: CARD,
            RAW_MERCHANT: MERCHANT,
        }
    )
    return result[[CARD, FLOW_NUMBER, MERCHANT, TIMESTAMP, REGION, DT]].sort_values(
        [CARD, TIMESTAMP, MERCHANT],
    ).reset_index(drop=True)


def load_source_candidates(
    transactions: pd.DataFrame,
    existing_storenames: set[str],
    dt_value: str,
) -> list[HiveCandidateMerchant]:
    selected = transactions[[MERCHANT, TIMESTAMP, REGION]].copy()
    ordered = selected.sort_values([MERCHANT, TIMESTAMP])
    latest = ordered.drop_duplicates(subset=[MERCHANT], keep="last")

    candidates: list[HiveCandidateMerchant] = []
    for row in latest.to_dict("records"):
        storename = _clean_text(row[MERCHANT])
        if storename in existing_storenames:
            continue
        candidates.append(
            HiveCandidateMerchant(
                storename=storename,
                region=_clean_text(row[REGION]),
                dt=dt_value,
            )
        )
    return candidates


def _existing_storenames(community_data: pd.DataFrame) -> set[str]:
    return {
        _clean_text(value)
        for value in community_data["storename"].tolist()
        if _clean_text(value)
    }


def build_community_members(
    community_data: pd.DataFrame,
    community_table: str,
) -> dict[str, CommunityMember]:
    community = _require_columns(community_data, COMMUNITY_REQUIRED_COLUMNS, community_table)
    members: dict[str, CommunityMember] = {}
    for row in community.to_dict("records"):
        storename = _clean_text(row["storename"])
        community_id = _format_hive_id(row["community_id"])
        if not storename or not community_id:
            continue
        if _clean_text(row["is_abnormal"]) != NORMAL_STATUS:
            continue
        is_anchor = _parse_position(row["is_position"], storename, community_table) == 1
        members[storename] = CommunityMember(
            storename=storename,
            community_id=community_id,
            is_anchor=is_anchor,
        )
    if not members:
        raise TransactionDataError(
            "Hive 商圈表没有可用存量商户标签: "
            f"table={community_table}, required=community_id not empty and is_abnormal normal"
        )
    return members


def _community_sort_key(community_id: str) -> tuple[int, str]:
    try:
        return int(community_id), community_id
    except ValueError:
        return 0, community_id


def _candidate_scores(
    candidate: HiveCandidateMerchant,
    graph: nx.Graph,
    members: dict[str, CommunityMember],
    assignment_config: AssignmentConfig,
) -> dict[str, float]:
    if candidate.storename not in graph:
        return {}
    weighted_votes: dict[str, float] = {}
    neighbors: list[tuple[str, float]] = []
    for neighbor in graph.neighbors(candidate.storename):
        member = members.get(str(neighbor))
        if member is None:
            continue
        edge_weight = float(graph[candidate.storename][neighbor].get("weight", 0.0))
        if edge_weight <= 0.0:
            continue
        vote_weight = (
            edge_weight * assignment_config.anchor_vote_weight
            if member.is_anchor
            else edge_weight
        )
        neighbors.append((str(neighbor), vote_weight))
    selected = sorted(neighbors, key=lambda item: (-item[1], item[0]))[
        : assignment_config.top_k_neighbors
    ]
    total_weight = sum(weight for _, weight in selected)
    if total_weight <= 0.0:
        return {}
    for neighbor, weight in selected:
        community_id = members[neighbor].community_id
        weighted_votes[community_id] = weighted_votes.get(community_id, 0.0) + weight
    return {
        community_id: weight / total_weight
        for community_id, weight in weighted_votes.items()
    }


def _top_two(scores: dict[str, float]) -> tuple[str, str | None, float, float]:
    ordered = sorted(
        scores.items(),
        key=lambda item: (-item[1], _community_sort_key(item[0])),
    )
    top_id, top_score = ordered[0]
    if len(ordered) == 1:
        return top_id, None, top_score, 0.0
    second_id, second_score = ordered[1]
    return top_id, second_id, top_score, second_score


def build_incremental_output(
    candidates: list[HiveCandidateMerchant],
    graph: nx.Graph,
    members: dict[str, CommunityMember],
    assignment_config: AssignmentConfig,
    update_time: datetime.datetime,
) -> pd.DataFrame:
    timestamp = update_time.strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, str | int]] = []
    for candidate in candidates:
        scores = _candidate_scores(candidate, graph, members, assignment_config)
        if scores:
            top_id, _, top_score, second_score = _top_two(scores)
            if (
                top_score >= assignment_config.theta
                and top_score - second_score >= assignment_config.delta
            ):
                assigned_community_id = top_id
                status = NORMAL_STATUS
            else:
                assigned_community_id = ""
                status = SUSPECT_ISOLATED_STATUS
        else:
            assigned_community_id = ""
            status = SUSPECT_ISOLATED_STATUS
        rows.append(
            {
                "storename": candidate.storename,
                "community_id": assigned_community_id,
                "previous_community_id": "",
                "region": candidate.region,
                "is_interfere": 0,
                "update_time": timestamp,
                "is_abnormal": status,
                "is_position": 0,
                "dt": candidate.dt,
            }
        )
    return pd.DataFrame(rows, columns=TARGET_COLUMNS)


def insert_new_target_rows(
    sd_module: ModuleType,
    result: pd.DataFrame,
    table_name: str,
    temp_table_name: str,
) -> None:
    if result.empty:
        return
    select_columns = ", ".join(f"source.{column}" for column in TARGET_SELECT_COLUMNS)
    for dt_value, partition_df in result.groupby("dt", sort=True):
        write_df = partition_df.drop(columns=["dt"]).reset_index(drop=True)
        sd_module.execute_sql(f"drop table if exists {temp_table_name}")
        try:
            sd_module.write_table(write_df, temp_table_name, debug=False, dt=None)
            sd_module.execute_sql(
                f"""
                insert into table {table_name}
                partition (dt={dt_value})
                select {select_columns}
                from {temp_table_name} source
                left join {table_name} target
                  on target.dt = '{dt_value}'
                 and target.storename = source.storename
                where target.storename is null
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
            dt_list = [self.dt_var]
            source_data = sd.read_table(self.task_config.source_table, dt=dt_list)
            community_data = sd.read_table(self.task_config.community_table, dt=dt_list)
            algorithm_config = load_algorithm_config(self.task_config.algorithm_config_path)
            assignment_config = load_assignment_config(self.task_config.assignment_config_path)
            transactions = load_incremental_transactions(
                source_data,
                algorithm_config.input.timestamp_formats,
                self.task_config.source_table,
            )
            visits = merge_visits(transactions, algorithm_config.visits)
            graph = build_sparse_graph(
                build_pair_statistics(visits, algorithm_config.cooccurrence),
                algorithm_config.cooccurrence,
                algorithm_config.graph,
            )
            members = build_community_members(
                community_data,
                self.task_config.community_table,
            )
            candidates = load_source_candidates(
                transactions,
                _existing_storenames(community_data),
                self.dt_var,
            )
            target_output = build_incremental_output(
                candidates,
                graph,
                members,
                assignment_config.assignment,
                datetime.datetime.now().astimezone(),
            )
            insert_new_target_rows(
                sd,
                target_output,
                self.task_config.target_table,
                self.task_config.target_temp_table,
            )
            logrecord.log_data(
                f"incremental taskrun seconds={time.time() - total_start:.2f}, "
                f"candidate_count={len(candidates)}, inserted_rows={len(target_output)}"
            )
            return HiveTaskSummary(
                dt=self.dt_var,
                source_rows=len(source_data),
                community_rows=len(community_data),
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
    tracemalloc.start()
    start_time = datetime.datetime.now()
    run_hive_task(build_default_hive_task_config())

    end_time = datetime.datetime.now()
    time_difference = end_time - start_time
    logrecord.log_data(f"incremental task use time {time_difference}")
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    print(
        f"memory_current_mb = {current_memory / 1024 / 1024:.2f},"
        f"memory_peak_mb = {peak_memory / 1024 / 1024:.2f}"
    )
    taskfinish.finish_task()


if __name__ == "__main__":
    main()
