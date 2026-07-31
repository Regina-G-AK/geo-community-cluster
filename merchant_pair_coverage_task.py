from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import Callable, List, NamedTuple, Set, Tuple, TypeVar

import pandas as pd

from business_district.config import CooccurrenceConfig, VisitConfig
from business_district.graph import PairStatistics, build_pair_statistics
from business_district.transactions import (
    DT,
    RAW_MERCHANT,
    REGION,
    load_hive_transactions,
    merge_visits,
)


Result = TypeVar("Result")
MerchantPair = Tuple[str, str]
TIMESTAMP_FORMATS = (
    "%Y%m%dT%H%M%S",
    "%Y%m%d%H%M%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
)


class MerchantPairCoverageConfig(NamedTuple):
    hive_table_root: Path
    source_table: str
    source_dt_values: Tuple[str, ...]
    result_table: str
    result_dt: str
    target_table: str
    region: str
    city_name: str
    merge_window_minutes: int
    maximum_daily_merchants_per_card: int
    pair_window_minutes: int
    decay_tau_minutes: float
    minimum_unique_cards: int
    process_count: int
    maximum_attempts: int
    retry_delay_seconds: float


class CoverageMetrics(NamedTuple):
    paired_merchant_count: int
    paired_merchant_with_community_count: int
    paired_merchant_with_community_pct: float
    effective_paired_merchant_count: int
    effective_paired_merchant_with_community_count: int
    effective_paired_merchant_with_community_pct: float
    raw_pair_count: int
    raw_both_have_community_pair_count: int
    raw_both_have_community_pair_pct: float
    raw_any_has_community_pair_count: int
    raw_any_has_community_pair_pct: float
    effective_pair_count: int
    effective_both_have_community_pair_count: int
    effective_both_have_community_pair_pct: float
    effective_any_has_community_pair_count: int
    effective_any_has_community_pair_pct: float


class OutputRow(NamedTuple):
    source_table: str
    source_dt_values: str
    result_table: str
    result_dt: str
    region: str
    city_name: str
    merge_window_minutes: int
    pair_window_minutes: int
    minimum_unique_cards: int
    paired_merchant_count: int
    paired_merchant_with_community_count: int
    paired_merchant_with_community_pct: float
    effective_paired_merchant_count: int
    effective_paired_merchant_with_community_count: int
    effective_paired_merchant_with_community_pct: float
    raw_pair_count: int
    raw_both_have_community_pair_count: int
    raw_both_have_community_pair_pct: float
    raw_any_has_community_pair_count: int
    raw_any_has_community_pair_pct: float
    effective_pair_count: int
    effective_both_have_community_pair_count: int
    effective_both_have_community_pair_pct: float
    effective_any_has_community_pair_count: int
    effective_any_has_community_pair_pct: float
    update_time: str


class TaskSummary(NamedTuple):
    source_row_count: int
    visit_row_count: int
    result_merchant_count: int
    raw_pair_count: int
    effective_pair_count: int
    target_table: str
    seconds: float


class TaskPlatform(NamedTuple):
    mount_check: Callable[[], None]
    read_table: Callable[..., object]
    read_parquet: Callable[[Path], pd.DataFrame]
    write_table: Callable[..., object]
    execute_sql: Callable[[str], object]
    log_data: Callable[[str], None]
    format_exception: Callable[[], None]
    finish_task: Callable[[], None]


class MerchantPairCoverageError(RuntimeError):
    """商户交易对覆盖率任务错误。"""


CONFIG = MerchantPairCoverageConfig(
    hive_table_root=Path("/appdata/project/fid_bg_icmp/tbl"),
    source_table="dev_icamp.icamp_merchant_cluster_algo_input",
    source_dt_values=(
        "20260131",
        "20260228",
        "20260331",
        "20260430",
        "20260531",
        "20260630",
    ),
    result_table="dev_icamp.icamp_merchant_cluster_algo_output",
    result_dt="20260727",
    target_table="dev_icamp.icamp_merchant_pair_coverage_tmp",
    region="上海",
    city_name="上海",
    merge_window_minutes=60,
    maximum_daily_merchants_per_card=100,
    pair_window_minutes=120,
    decay_tau_minutes=60.0,
    minimum_unique_cards=3,
    process_count=4,
    maximum_attempts=3,
    retry_delay_seconds=2.0,
)


def validate_config(config: MerchantPairCoverageConfig) -> None:
    if not config.hive_table_root.is_absolute():
        raise ValueError(
            "Hive 表根目录必须是绝对路径: "
            f"hive_table_root={str(config.hive_table_root)!r}"
        )
    table_fields = {
        "source_table": config.source_table,
        "result_table": config.result_table,
        "target_table": config.target_table,
    }
    for field_name, table_name in table_fields.items():
        parts = table_name.split(".")
        if (
            len(parts) != 2
            or any(not part for part in parts)
            or table_name != table_name.strip()
        ):
            raise ValueError(
                f"Hive 表名必须是 db.table 格式: "
                f"field={field_name}, table={table_name!r}"
            )
    if not config.source_dt_values:
        raise ValueError("输入分区列表不能为空: field=source_dt_values")
    date_values = config.source_dt_values + (config.result_dt,)
    invalid_dates = tuple(
        date_value
        for date_value in date_values
        if len(date_value) != 8 or not date_value.isdigit()
    )
    if invalid_dates:
        raise ValueError(
            f"Hive 分区必须是 YYYYMMDD 格式: invalid_dates={invalid_dates!r}"
        )
    if not config.region.strip():
        raise ValueError("地区不能为空: field=region")
    if not config.city_name.strip():
        raise ValueError("城市不能为空: field=city_name")
    positive_integer_fields = {
        "merge_window_minutes": config.merge_window_minutes,
        "maximum_daily_merchants_per_card": (
            config.maximum_daily_merchants_per_card
        ),
        "pair_window_minutes": config.pair_window_minutes,
        "minimum_unique_cards": config.minimum_unique_cards,
        "process_count": config.process_count,
        "maximum_attempts": config.maximum_attempts,
    }
    for field_name, value in positive_integer_fields.items():
        if value < 1:
            raise ValueError(
                f"配置必须是正整数: field={field_name}, value={value}"
            )
    if config.decay_tau_minutes <= 0.0:
        raise ValueError(
            "衰减时间必须大于 0: "
            f"field=decay_tau_minutes, value={config.decay_tau_minutes}"
        )
    if config.retry_delay_seconds < 0.0:
        raise ValueError(
            "重试间隔不能小于 0: "
            f"field=retry_delay_seconds, value={config.retry_delay_seconds}"
        )


def load_task_platform() -> TaskPlatform:
    try:
        import spdbccc_data as sd
        from spdbccc_data import formattedExc
        from spdbccc_data import loging as logrecord
        from spdbccc_data import mountCheck
        from spdbccc_data import task as taskfinish
    except ImportError as error:
        raise RuntimeError(
            "无法导入 spdbccc_data 任务组件，请在线上任务环境运行此脚本"
        ) from error
    return TaskPlatform(
        mount_check=mountCheck.mount_check,
        read_table=sd.read_table,
        read_parquet=pd.read_parquet,
        write_table=sd.write_table,
        execute_sql=sd.execute_sql,
        log_data=logrecord.log_data,
        format_exception=formattedExc.formatted_exc,
        finish_task=taskfinish.finish_task,
    )


def _call_external_with_retries(
    platform: TaskPlatform,
    operation_name: str,
    operation: Callable[[], Result],
    maximum_attempts: int,
    retry_delay_seconds: float,
) -> Result:
    for attempt in range(1, maximum_attempts + 1):
        try:
            return operation()
        except MemoryError:
            raise
        except Exception as error:
            if attempt == maximum_attempts:
                raise
            platform.log_data(
                "external call retry warning "
                f"operation={operation_name!r}, "
                f"attempt={attempt}, "
                f"maximum_attempts={maximum_attempts}, "
                f"error_type={type(error).__name__!r}, "
                f"reason={str(error)!r}"
            )
            time.sleep(retry_delay_seconds)
    raise MerchantPairCoverageError(
        "外部调用重试流程异常结束: "
        f"operation={operation_name!r}, "
        f"maximum_attempts={maximum_attempts}"
    )


def _hive_storage_table_name(table_name: str) -> str:
    parts = table_name.split(".")
    if len(parts) != 2 or any(not part for part in parts):
        raise ValueError(
            f"Hive 表名必须是 db.table 格式: table={table_name!r}"
        )
    return parts[1]


def build_partition_path(
    config: MerchantPairCoverageConfig,
    table_name: str,
    dt_value: str,
) -> Path:
    return (
        config.hive_table_root
        / _hive_storage_table_name(table_name)
        / f"dt={dt_value}"
    )


def prepare_partition(
    platform: TaskPlatform,
    config: MerchantPairCoverageConfig,
    table_name: str,
    dt_value: str,
) -> Tuple[Path, ...]:
    platform.log_data(
        "merchant pair coverage partition mount start "
        f"table={table_name!r}, dt={dt_value!r}"
    )
    _call_external_with_retries(
        platform,
        f"mount_partition:{table_name}:{dt_value}",
        lambda: platform.read_table(table_name, dt=[dt_value]),
        config.maximum_attempts,
        config.retry_delay_seconds,
    )
    partition_path = build_partition_path(config, table_name, dt_value)
    if not partition_path.is_dir():
        raise FileNotFoundError(
            "Hive 分区挂载后目录不存在: "
            f"table={table_name!r}, dt={dt_value!r}, "
            f"path={str(partition_path)!r}"
        )
    part_files = tuple(
        path
        for path in sorted(partition_path.rglob("part*"))
        if path.is_file()
    )
    if not part_files:
        raise MerchantPairCoverageError(
            "Hive 分区目录没有 part* 数据文件: "
            f"table={table_name!r}, dt={dt_value!r}, "
            f"path={str(partition_path)!r}"
        )
    platform.log_data(
        "merchant pair coverage partition mount success "
        f"table={table_name!r}, dt={dt_value!r}, "
        f"part_file_count={len(part_files)}, "
        f"path={str(partition_path)!r}"
    )
    return part_files


def read_part_file(
    platform: TaskPlatform,
    table_name: str,
    dt_value: str,
    file_path: Path,
) -> pd.DataFrame:
    platform.log_data(
        "merchant pair coverage part read start "
        f"table={table_name!r}, dt={dt_value!r}, "
        f"path={str(file_path)!r}"
    )
    try:
        dataframe = platform.read_parquet(file_path)
    except (OSError, ValueError, ImportError) as error:
        raise MerchantPairCoverageError(
            "Hive Parquet 分片读取失败: "
            f"table={table_name!r}, dt={dt_value!r}, "
            f"path={str(file_path)!r}, reason={str(error)!r}"
        ) from error
    if not isinstance(dataframe, pd.DataFrame):
        raise TypeError(
            "Parquet 读取结果类型错误: "
            f"table={table_name!r}, dt={dt_value!r}, "
            f"path={str(file_path)!r}, "
            f"actual_type={type(dataframe).__name__!r}"
        )
    platform.log_data(
        "merchant pair coverage part read success "
        f"table={table_name!r}, dt={dt_value!r}, "
        f"path={str(file_path)!r}, row_count={len(dataframe)}"
    )
    return dataframe


def read_source_partitions(
    platform: TaskPlatform,
    config: MerchantPairCoverageConfig,
) -> pd.DataFrame:
    selected_parts: List[pd.DataFrame] = []
    source_row_count = 0
    for dt_value in config.source_dt_values:
        partition_source_row_count = 0
        partition_selected_row_count = 0
        for file_path in prepare_partition(
            platform,
            config,
            config.source_table,
            dt_value,
        ):
            dataframe = read_part_file(
                platform,
                config.source_table,
                dt_value,
                file_path,
            )
            if dataframe.empty:
                continue
            partition_source_row_count += len(dataframe)
            source_row_count += len(dataframe)
            dataframe = dataframe.copy()
            dataframe[DT] = dt_value
            selected = filter_source_rows(dataframe, config)
            partition_selected_row_count += len(selected)
            if not selected.empty:
                selected_parts.append(selected)
        platform.log_data(
            "merchant pair coverage source partition success "
            f"table={config.source_table!r}, dt={dt_value!r}, "
            f"source_row_count={partition_source_row_count}, "
            f"selected_row_count={partition_selected_row_count}"
        )
    if source_row_count == 0:
        raise MerchantPairCoverageError(
            "所有指定 Hive 输入分区均没有数据: "
            f"table={config.source_table!r}, "
            f"dt_values={config.source_dt_values!r}"
        )
    if not selected_parts:
        raise MerchantPairCoverageError(
            "所有指定 Hive 输入分区均没有符合上海筛选规则的交易: "
            f"table={config.source_table!r}, "
            f"dt_values={config.source_dt_values!r}, "
            f"region={config.region!r}, city_name={config.city_name!r}"
        )
    selected_source = pd.concat(
        selected_parts,
        ignore_index=True,
        copy=False,
    )
    platform.log_data(
        "merchant pair coverage source read success "
        f"table={config.source_table!r}, "
        f"source_row_count={source_row_count}, "
        f"selected_row_count={len(selected_source)}, "
        f"selected_part_count={len(selected_parts)}"
    )
    return selected_source


def build_city_mask(
    storenames: pd.Series,
    city_name: str,
) -> pd.Series:
    city_text = city_name.strip()
    city_keyword = city_text if city_text.endswith("市") else f"{city_text}市"
    names = storenames.astype("string").str.strip()
    contains_city = names.str.contains("市", regex=False, na=False)
    contains_target_city = names.str.contains(
        city_keyword,
        regex=False,
        na=False,
    )
    return ~contains_city | contains_target_city


def filter_source_rows(
    source: pd.DataFrame,
    config: MerchantPairCoverageConfig,
) -> pd.DataFrame:
    required_columns = {REGION, RAW_MERCHANT}
    missing_columns = sorted(required_columns.difference(source.columns))
    if missing_columns:
        raise MerchantPairCoverageError(
            "Hive 输入表缺少地区或商户字段: "
            f"table={config.source_table!r}, "
            f"missing_columns={missing_columns!r}"
        )
    region_values = source[REGION].astype("string").str.strip()
    region_mask = region_values.eq(config.region)
    city_mask = build_city_mask(source[RAW_MERCHANT], config.city_name)
    return source.loc[region_mask & city_mask].copy().reset_index(drop=True)


def prepare_visits(
    source: pd.DataFrame,
    config: MerchantPairCoverageConfig,
) -> pd.DataFrame:
    transactions = load_hive_transactions(
        source,
        TIMESTAMP_FORMATS,
        config.source_dt_values[0],
        config.source_table,
    )
    return merge_visits(
        transactions,
        VisitConfig(
            merge_window_minutes=config.merge_window_minutes,
            maximum_daily_merchants_per_card=(
                config.maximum_daily_merchants_per_card
            ),
        ),
    )


def build_statistics(
    visits: pd.DataFrame,
    config: MerchantPairCoverageConfig,
) -> PairStatistics:
    return build_pair_statistics(
        visits,
        CooccurrenceConfig(
            window_minutes=config.pair_window_minutes,
            decay_tau_minutes=config.decay_tau_minutes,
            minimum_unique_users=config.minimum_unique_cards,
        ),
        config.process_count,
    )


def load_assigned_merchants(
    platform: TaskPlatform,
    config: MerchantPairCoverageConfig,
) -> Set[str]:
    assigned_merchants: Set[str] = set()
    result_row_count = 0
    for file_path in prepare_partition(
        platform,
        config,
        config.result_table,
        config.result_dt,
    ):
        result = read_part_file(
            platform,
            config.result_table,
            config.result_dt,
            file_path,
        )
        if result.empty:
            continue
        result_row_count += len(result)
        required_columns = {RAW_MERCHANT, REGION, "community_id"}
        missing_columns = sorted(required_columns.difference(result.columns))
        if missing_columns:
            raise MerchantPairCoverageError(
                "Hive 结果表缺少商户、地区或商圈字段: "
                f"table={config.result_table!r}, "
                f"dt={config.result_dt!r}, "
                f"path={str(file_path)!r}, "
                f"missing_columns={missing_columns!r}"
            )
        result_region = result[REGION].astype("string").str.strip()
        merchant_ids = result[RAW_MERCHANT].astype("string").str.strip()
        community_ids = result["community_id"].astype("string").str.strip()
        selected = (
            result_region.eq(config.region)
            & merchant_ids.notna()
            & merchant_ids.ne("")
            & community_ids.notna()
            & community_ids.ne("")
        )
        assigned_merchants.update(
            merchant_ids.loc[selected].astype(str).tolist()
        )
    if result_row_count == 0:
        raise MerchantPairCoverageError(
            "Hive 结果分区没有数据: "
            f"table={config.result_table!r}, dt={config.result_dt!r}"
        )
    platform.log_data(
        "merchant pair coverage result read success "
        f"table={config.result_table!r}, dt={config.result_dt!r}, "
        f"result_row_count={result_row_count}, "
        f"assigned_merchant_count={len(assigned_merchants)}"
    )
    return assigned_merchants


def _percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(100.0 * float(numerator) / float(denominator), 2)


def _pair_merchants(pairs: Set[MerchantPair]) -> Set[str]:
    return {
        merchant_id
        for pair in pairs
        for merchant_id in pair
    }


def calculate_coverage_metrics(
    statistics: PairStatistics,
    assigned_merchants: Set[str],
    minimum_unique_cards: int,
) -> CoverageMetrics:
    raw_pairs = set(statistics.supports)
    effective_pairs = {
        pair
        for pair, support in statistics.supports.items()
        if support >= minimum_unique_cards
    }
    raw_merchants = _pair_merchants(raw_pairs)
    effective_merchants = _pair_merchants(effective_pairs)
    raw_assigned_merchants = raw_merchants & assigned_merchants
    effective_assigned_merchants = effective_merchants & assigned_merchants

    raw_both_count = sum(
        left in assigned_merchants and right in assigned_merchants
        for left, right in raw_pairs
    )
    raw_any_count = sum(
        left in assigned_merchants or right in assigned_merchants
        for left, right in raw_pairs
    )
    effective_both_count = sum(
        left in assigned_merchants and right in assigned_merchants
        for left, right in effective_pairs
    )
    effective_any_count = sum(
        left in assigned_merchants or right in assigned_merchants
        for left, right in effective_pairs
    )

    return CoverageMetrics(
        paired_merchant_count=len(raw_merchants),
        paired_merchant_with_community_count=len(raw_assigned_merchants),
        paired_merchant_with_community_pct=_percentage(
            len(raw_assigned_merchants),
            len(raw_merchants),
        ),
        effective_paired_merchant_count=len(effective_merchants),
        effective_paired_merchant_with_community_count=(
            len(effective_assigned_merchants)
        ),
        effective_paired_merchant_with_community_pct=_percentage(
            len(effective_assigned_merchants),
            len(effective_merchants),
        ),
        raw_pair_count=len(raw_pairs),
        raw_both_have_community_pair_count=raw_both_count,
        raw_both_have_community_pair_pct=_percentage(
            raw_both_count,
            len(raw_pairs),
        ),
        raw_any_has_community_pair_count=raw_any_count,
        raw_any_has_community_pair_pct=_percentage(
            raw_any_count,
            len(raw_pairs),
        ),
        effective_pair_count=len(effective_pairs),
        effective_both_have_community_pair_count=effective_both_count,
        effective_both_have_community_pair_pct=_percentage(
            effective_both_count,
            len(effective_pairs),
        ),
        effective_any_has_community_pair_count=effective_any_count,
        effective_any_has_community_pair_pct=_percentage(
            effective_any_count,
            len(effective_pairs),
        ),
    )


def build_output_dataframe(
    config: MerchantPairCoverageConfig,
    metrics: CoverageMetrics,
    update_time: datetime.datetime,
) -> pd.DataFrame:
    row = OutputRow(
        source_table=config.source_table,
        source_dt_values=",".join(config.source_dt_values),
        result_table=config.result_table,
        result_dt=config.result_dt,
        region=config.region,
        city_name=config.city_name,
        merge_window_minutes=config.merge_window_minutes,
        pair_window_minutes=config.pair_window_minutes,
        minimum_unique_cards=config.minimum_unique_cards,
        paired_merchant_count=metrics.paired_merchant_count,
        paired_merchant_with_community_count=(
            metrics.paired_merchant_with_community_count
        ),
        paired_merchant_with_community_pct=(
            metrics.paired_merchant_with_community_pct
        ),
        effective_paired_merchant_count=(
            metrics.effective_paired_merchant_count
        ),
        effective_paired_merchant_with_community_count=(
            metrics.effective_paired_merchant_with_community_count
        ),
        effective_paired_merchant_with_community_pct=(
            metrics.effective_paired_merchant_with_community_pct
        ),
        raw_pair_count=metrics.raw_pair_count,
        raw_both_have_community_pair_count=(
            metrics.raw_both_have_community_pair_count
        ),
        raw_both_have_community_pair_pct=(
            metrics.raw_both_have_community_pair_pct
        ),
        raw_any_has_community_pair_count=(
            metrics.raw_any_has_community_pair_count
        ),
        raw_any_has_community_pair_pct=(
            metrics.raw_any_has_community_pair_pct
        ),
        effective_pair_count=metrics.effective_pair_count,
        effective_both_have_community_pair_count=(
            metrics.effective_both_have_community_pair_count
        ),
        effective_both_have_community_pair_pct=(
            metrics.effective_both_have_community_pair_pct
        ),
        effective_any_has_community_pair_count=(
            metrics.effective_any_has_community_pair_count
        ),
        effective_any_has_community_pair_pct=(
            metrics.effective_any_has_community_pair_pct
        ),
        update_time=update_time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    return pd.DataFrame([row], columns=OutputRow._fields)


def replace_target_table(
    platform: TaskPlatform,
    config: MerchantPairCoverageConfig,
    output: pd.DataFrame,
) -> None:
    def replace() -> object:
        platform.execute_sql(f"drop table if exists {config.target_table}")
        return platform.write_table(
            output,
            config.target_table,
            debug=False,
            dt=None,
        )

    _call_external_with_retries(
        platform,
        "replace_target_table",
        replace,
        config.maximum_attempts,
        config.retry_delay_seconds,
    )


class TaskMain:
    def __init__(
        self,
        platform: TaskPlatform,
        config: MerchantPairCoverageConfig,
    ) -> None:
        self.platform = platform
        self.config = config

    def check(self) -> None:
        self.platform.mount_check()
        validate_config(self.config)
        self.platform.log_data(
            "merchant pair coverage task check success "
            f"target_table={self.config.target_table!r}"
        )

    def taskrun(self) -> TaskSummary:
        started_at = time.time()
        self.platform.log_data(
            "merchant pair coverage task start "
            f"source_table={self.config.source_table!r}, "
            f"source_dt_values={self.config.source_dt_values!r}, "
            f"result_table={self.config.result_table!r}, "
            f"result_dt={self.config.result_dt!r}, "
            f"region={self.config.region!r}"
        )
        try:
            source = read_source_partitions(self.platform, self.config)
            self.platform.log_data(
                "merchant pair coverage transaction prepare start "
                f"row_count={len(source)}"
            )
            visits = prepare_visits(source, self.config)
            self.platform.log_data(
                "merchant pair coverage visit merge success "
                f"source_row_count={len(source)}, "
                f"visit_row_count={len(visits)}, "
                f"merge_window_minutes={self.config.merge_window_minutes}"
            )
            self.platform.log_data(
                "merchant pair coverage pair calculation start "
                f"visit_row_count={len(visits)}, "
                f"pair_window_minutes={self.config.pair_window_minutes}, "
                f"process_count={self.config.process_count}"
            )
            statistics = build_statistics(visits, self.config)
            self.platform.log_data(
                "merchant pair coverage pair calculation success "
                f"raw_pair_count={len(statistics.supports)}"
            )
            assigned_merchants = load_assigned_merchants(
                self.platform,
                self.config,
            )
            metrics = calculate_coverage_metrics(
                statistics,
                assigned_merchants,
                self.config.minimum_unique_cards,
            )
            output = build_output_dataframe(
                self.config,
                metrics,
                datetime.datetime.now().astimezone(),
            )
            self.platform.log_data(
                "merchant pair coverage target replace start "
                f"target_table={self.config.target_table!r}, "
                f"row_count={len(output)}"
            )
            replace_target_table(self.platform, self.config, output)
            self.platform.log_data(
                "merchant pair coverage target replace success "
                f"target_table={self.config.target_table!r}, "
                f"row_count={len(output)}"
            )
            seconds = time.time() - started_at
            summary = TaskSummary(
                source_row_count=len(source),
                visit_row_count=len(visits),
                result_merchant_count=len(assigned_merchants),
                raw_pair_count=metrics.raw_pair_count,
                effective_pair_count=metrics.effective_pair_count,
                target_table=self.config.target_table,
                seconds=seconds,
            )
            self.platform.log_data(
                "merchant pair coverage task success "
                f"source_row_count={summary.source_row_count}, "
                f"visit_row_count={summary.visit_row_count}, "
                f"result_merchant_count={summary.result_merchant_count}, "
                f"raw_pair_count={summary.raw_pair_count}, "
                f"effective_pair_count={summary.effective_pair_count}, "
                f"target_table={summary.target_table!r}, "
                f"seconds={summary.seconds:.2f}"
            )
            return summary
        except Exception as error:
            self.platform.log_data(
                "merchant pair coverage task failed "
                f"error_type={type(error).__name__!r}, "
                f"reason={str(error)!r}"
            )
            self.platform.format_exception()
            raise

    def destroy(self) -> None:
        self.platform.log_data(
            "merchant pair coverage task destroy success "
            "temporary_resource_count=0, target_table_preserved=1"
        )


def run_task(task: TaskMain) -> TaskSummary:
    try:
        task.check()
        return task.taskrun()
    finally:
        try:
            task.destroy()
        finally:
            task.platform.finish_task()


def main() -> None:
    summary = run_task(TaskMain(load_task_platform(), CONFIG))
    print(summary)


if __name__ == "__main__":
    main()
