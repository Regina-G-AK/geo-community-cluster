from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

import spdbccc_data as sd
from spdbccc_data import dtDate
from spdbccc_data import task as taskfinish

from business_district.config import (
    AnchorConfig,
    AppConfig,
    CityConfig,
    CommunityConfig,
    CooccurrenceConfig,
    GeoConfig,
    GraphConfig,
    InputConfig,
    OutputConfig,
    RuntimeConfig,
    VisitConfig,
)
from business_district.hive_task import (
    HiveTaskConfig as InitialHiveTaskConfig,
    HiveTaskSummary as InitialHiveTaskSummary,
    TaskMain as InitialTaskMain,
    load_hive_algorithm_parameters,
)
# 资源检测已停用，保留原导入代码便于恢复
# from business_district.memory_monitor import (
#     create_process_memory_monitor,
#     format_memory_report,
# )
from incremental_assignment.hive_task import (
    HiveTaskConfig as IncrementalHiveTaskConfig,
    HiveTaskSummary as IncrementalHiveTaskSummary,
    TaskMain as IncrementalTaskMain,
)
from incremental_assignment.models import AssignmentConfig

TimestampFormats = Tuple[str, ...]
HiveTask = Union[InitialTaskMain, IncrementalTaskMain]
HiveTaskSummary = Union[InitialHiveTaskSummary, IncrementalHiveTaskSummary]
# 资源检测已停用，保留原采样间隔便于恢复
# MEMORY_SAMPLE_INTERVAL_SECONDS = 0.2


def build_timestamp_formats() -> TimestampFormats:
    return (
        "%Y%m%dT%H%M%S",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    )


def build_visit_config() -> VisitConfig:
    return VisitConfig(
        merge_window_minutes=30,
        maximum_daily_merchants_per_card=100,
    )


def build_graph_config() -> GraphConfig:
    return GraphConfig(
        edge_weight_method="sppmi",
        context_smoothing_alpha=0.75,
        sppmi_shift=3.0,
        top_k_neighbors=15,
        minimum_z_score=0.5,
    )


def build_algorithm_config(
    project_root: Path,
    timestamp_formats: TimestampFormats,
    visit_config: VisitConfig,
    graph_config: GraphConfig,
) -> AppConfig:
    return AppConfig(
        city=CityConfig(code="shanghai", name="上海市"),
        input=InputConfig(
            transactions_path=project_root / "data.txt",
            timestamp_formats=timestamp_formats,
        ),
        visits=visit_config,
        cooccurrence=CooccurrenceConfig(
            window_minutes=120,
            decay_tau_minutes=60.0,
            minimum_unique_users=3,
        ),
        graph=graph_config,
        community=CommunityConfig(
            algorithm="leiden",
            resolution=1.0,
            random_seed=42,
            minimum_online_neighbor_count=30,
        ),
        geo=GeoConfig(
            cluster_radius_meters=1000.0,
            maximum_merchants_per_coordinate=100,
        ),
        anchors=AnchorConfig(
            minimum_count=3,
            maximum_count=10,
            merchants_per_anchor=20,
            minimum_community_size=5,
            maximum_participation=0.1,
            chain_visit_count_quantile=0.9,
            chain_minimum_visit_count=100,
        ),
        output=OutputConfig(directory=project_root / "code"),
        runtime=RuntimeConfig(process_count=4),
    )


def build_assignment_config() -> AssignmentConfig:
    return AssignmentConfig(
        community_assignment_distance_meters=3000.0,
    )


def build_initial_task_config(
    algorithm_config: AppConfig,
    assignment_config: AssignmentConfig,
) -> InitialHiveTaskConfig:
    return InitialHiveTaskConfig(
        algorithm_config=algorithm_config,
        assignment_config=assignment_config,
        source_table="dev_icamp.icamp_merchant_cluster_algo_input",
        parameter_table="dev_icamp.icamp_merchant_cluster_algo_param",
        target_table="dev_icamp.icamp_merchant_cluster_algo_output",
        target_temp_table="dev_icamp.icamp_merchant_cluster_algo_output_tmp",
        dt_expression="T-1",
    )


def build_incremental_task_config(
    algorithm_config: AppConfig,
    timestamp_formats: TimestampFormats,
    visit_config: VisitConfig,
    graph_config: GraphConfig,
    assignment_config: AssignmentConfig,
) -> IncrementalHiveTaskConfig:
    return IncrementalHiveTaskConfig(
        algorithm_config=algorithm_config,
        timestamp_formats=timestamp_formats,
        visit_config=visit_config,
        graph_config=graph_config,
        assignment_config=assignment_config,
        source_table="dev_icamp.icamp_merchant_cluster_algo_input",
        parameter_table="dev_icamp.icamp_merchant_cluster_algo_param",
        target_table="dev_icamp.icamp_merchant_cluster_algo_output",
        target_temp_table=(
            "dev_icamp.icamp_merchant_cluster_algo_output_incremental_tmp"
        ),
        dt_expression="T-1",
    )


def load_task_mode(initial_task_config: InitialHiveTaskConfig) -> Tuple[str, bool]:
    parameter_dt = dtDate.dt_date(initial_task_config.dt_expression)
    parameter_data = sd.read_table(
        initial_task_config.parameter_table,
        dt=[parameter_dt],
    )
    parameters = load_hive_algorithm_parameters(
        parameter_data,
        initial_task_config.parameter_table,
    )
    if len(parameters) != 1:
        raise ValueError(
            "参数表 T-1 分区必须只有一条参数记录: "
            f"table={initial_task_config.parameter_table}, "
            f"dt={parameter_dt}, row_count={len(parameters)}"
        )
    return parameter_dt, parameters[0].is_daily


def run_task(task: HiveTask) -> HiveTaskSummary:
    # 资源检测已停用，保留原启动代码便于恢复
    # memory_monitor = create_process_memory_monitor(
    #     MEMORY_SAMPLE_INTERVAL_SECONDS,
    # )
    # memory_monitor.start()
    try:
        task.check()
        summary = task.taskrun()
    finally:
        task.destroy()
        taskfinish.finish_task()
        # 资源检测已停用，保留原停止和报告代码便于恢复
        # memory_report = memory_monitor.stop()
        # print(format_memory_report(memory_report), flush=True)

    return summary


def main() -> None:
    project_root = Path("/app/project/fid_bg_icmp")
    timestamp_formats = build_timestamp_formats()
    visit_config = build_visit_config()
    graph_config = build_graph_config()
    algorithm_config = build_algorithm_config(
        project_root,
        timestamp_formats,
        visit_config,
        graph_config,
    )
    assignment_config = build_assignment_config()
    initial_task_config = build_initial_task_config(
        algorithm_config,
        assignment_config,
    )
    incremental_task_config = build_incremental_task_config(
        algorithm_config,
        timestamp_formats,
        visit_config,
        graph_config,
        assignment_config,
    )
    parameter_dt, is_daily = load_task_mode(initial_task_config)
    task_mode = "incremental_assignment" if is_daily else "initial_clustering"
    print(f"task_mode={task_mode}, parameter_dt={parameter_dt}, is_daily={is_daily}")

    task: HiveTask
    if is_daily:
        task = IncrementalTaskMain(incremental_task_config)
    else:
        task = InitialTaskMain(initial_task_config)
    summary = run_task(task)
    print(summary)


if __name__ == "__main__":
    main()
