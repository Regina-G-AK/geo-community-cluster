from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Tuple

from business_district.errors import ConfigurationError


@dataclass(frozen=True)
class CityConfig:
    code: str
    name: str


@dataclass(frozen=True)
class InputConfig:
    transactions_path: Path
    timestamp_formats: Tuple[str, ...]


@dataclass(frozen=True)
class VisitConfig:
    merge_window_minutes: int
    maximum_daily_merchants_per_card: int


@dataclass(frozen=True)
class CooccurrenceConfig:
    window_minutes: int
    decay_tau_minutes: float
    minimum_unique_users: int


@dataclass(frozen=True)
class GraphConfig:
    edge_weight_method: str
    context_smoothing_alpha: float
    sppmi_shift: float
    top_k_neighbors: int
    minimum_z_score: float


@dataclass(frozen=True)
class CommunityConfig:
    algorithm: str
    resolution: float
    random_seed: int
    minimum_online_neighbor_count: int


@dataclass(frozen=True)
class GeoConfig:
    cluster_radius_meters: float
    maximum_merchants_per_coordinate: int


@dataclass(frozen=True)
class AnchorConfig:
    minimum_count: int
    maximum_count: int
    merchants_per_anchor: int
    minimum_community_size: int
    maximum_participation: float
    chain_visit_count_quantile: float
    chain_minimum_visit_count: int


@dataclass(frozen=True)
class OutputConfig:
    directory: Path


@dataclass(frozen=True)
class RuntimeConfig:
    process_count: int


@dataclass(frozen=True)
class AppConfig:
    city: CityConfig
    input: InputConfig
    visits: VisitConfig
    cooccurrence: CooccurrenceConfig
    graph: GraphConfig
    community: CommunityConfig
    geo: GeoConfig
    anchors: AnchorConfig
    output: OutputConfig
    runtime: RuntimeConfig


@dataclass(frozen=True)
class AlgorithmRuntimeConfig:
    window_minutes: int
    decay_tau_minutes: float
    minimum_unique_users: int
    minimum_community_size: int


def validate_app_config(config: AppConfig) -> AppConfig:
    if not config.city.code.strip():
        raise ConfigurationError("城市编码不能为空")
    if not config.city.name.strip():
        raise ConfigurationError("城市名称不能为空")
    if not config.input.timestamp_formats:
        raise ConfigurationError("交易时间格式不能为空")
    if config.visits.merge_window_minutes < 1:
        raise ConfigurationError("到访合并窗口必须不小于 1 分钟")
    if config.visits.maximum_daily_merchants_per_card < 1:
        raise ConfigurationError("单卡每日最大商户数必须不小于 1")
    if config.cooccurrence.window_minutes < 1:
        raise ConfigurationError("共现窗口必须不小于 1 分钟")
    if config.cooccurrence.decay_tau_minutes <= 0.0:
        raise ConfigurationError("时间衰减参数必须大于 0")
    if config.cooccurrence.minimum_unique_users < 1:
        raise ConfigurationError("最小支持人数必须不小于 1")
    if config.graph.edge_weight_method not in {"sppmi", "transaction_count"}:
        raise ConfigurationError(
            "边权重计算方式必须是 sppmi 或 transaction_count"
        )
    if not 0.0 <= config.graph.context_smoothing_alpha <= 1.0:
        raise ConfigurationError("上下文平滑参数必须位于 0 到 1 之间")
    if config.graph.sppmi_shift < 1.0:
        raise ConfigurationError("SPPMI 平移参数必须不小于 1")
    if config.graph.top_k_neighbors < 1:
        raise ConfigurationError("top-k 邻居数必须不小于 1")
    if config.graph.minimum_z_score < 0.0:
        raise ConfigurationError("最小 z-score 必须不小于 0")
    if config.community.algorithm != "leiden":
        raise ConfigurationError("社区算法当前仅支持 leiden")
    if config.community.resolution <= 0.0:
        raise ConfigurationError("社区分辨率必须大于 0")
    if config.community.random_seed < 0:
        raise ConfigurationError("随机种子必须不小于 0")
    if config.community.minimum_online_neighbor_count < 2:
        raise ConfigurationError("疑似线上商户最少关联坐标商户数必须不小于 2")
    if config.geo.cluster_radius_meters <= 0.0:
        raise ConfigurationError("地理距离阈值必须大于 0")
    if config.geo.maximum_merchants_per_coordinate < 1:
        raise ConfigurationError("同一坐标最大商户数必须不小于 1")
    if config.anchors.minimum_count < 1:
        raise ConfigurationError("最小锚点数必须不小于 1")
    if config.anchors.maximum_count < config.anchors.minimum_count:
        raise ConfigurationError("最大锚点数不能小于最小锚点数")
    if config.anchors.merchants_per_anchor < 1:
        raise ConfigurationError("每锚点商户数必须不小于 1")
    if config.anchors.minimum_community_size < 1:
        raise ConfigurationError("有效社区最小商户数必须不小于 1")
    if not 0.0 <= config.anchors.maximum_participation <= 1.0:
        raise ConfigurationError("锚点最大参与系数必须位于 0 到 1 之间")
    if not 0.0 <= config.anchors.chain_visit_count_quantile <= 1.0:
        raise ConfigurationError("连锁商户到访分位数必须位于 0 到 1 之间")
    if config.anchors.chain_minimum_visit_count < 1:
        raise ConfigurationError("连锁商户最小到访数必须不小于 1")
    if config.runtime.process_count < 1:
        raise ConfigurationError("工作进程数必须不小于 1")
    return config


def apply_runtime_parameters(
    config: AppConfig,
    parameters: AlgorithmRuntimeConfig,
) -> AppConfig:
    updated = replace(
        config,
        cooccurrence=CooccurrenceConfig(
            window_minutes=parameters.window_minutes,
            decay_tau_minutes=parameters.decay_tau_minutes,
            minimum_unique_users=parameters.minimum_unique_users,
        ),
        anchors=replace(
            config.anchors,
            minimum_community_size=parameters.minimum_community_size,
        ),
    )
    return validate_app_config(updated)
