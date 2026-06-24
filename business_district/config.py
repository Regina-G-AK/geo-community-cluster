from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from business_district.errors import ConfigurationError


@dataclass(frozen=True)
class CityConfig:
    code: str
    name: str


@dataclass(frozen=True)
class InputConfig:
    transactions_path: Path
    timestamp_formats: tuple[str, ...]


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
    maximum_cleaning_rounds: int
    minimum_hub_degree: int
    participation_threshold: float


@dataclass(frozen=True)
class AnchorConfig:
    minimum_count: int
    maximum_count: int
    merchants_per_anchor: int
    minimum_community_size: int


@dataclass(frozen=True)
class OutputConfig:
    directory: Path


@dataclass(frozen=True)
class ExperimentConfig:
    path: Path


@dataclass(frozen=True)
class AppConfig:
    city: CityConfig
    input: InputConfig
    visits: VisitConfig
    cooccurrence: CooccurrenceConfig
    graph: GraphConfig
    community: CommunityConfig
    anchors: AnchorConfig
    output: OutputConfig
    experiments: ExperimentConfig


def _read_config_parser(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        with path.open("r", encoding="utf-8") as file:
            parser.read_file(file)
    except configparser.Error as error:
        raise ConfigurationError(
            f"配置文件格式错误: path={path}, reason={error}"
        ) from error
    return parser


def _require_table(
    parser: configparser.ConfigParser,
    key: str,
) -> configparser.SectionProxy:
    if not parser.has_section(key):
        raise ConfigurationError(f"配置节 [{key}] 缺失")
    return parser[key]


def _strip_optional_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1].strip()
    return stripped


def _require_string(
    data: configparser.SectionProxy,
    key: str,
    section: str,
) -> str:
    value = data.get(key)
    if value is None or not value.strip():
        raise ConfigurationError(f"配置项 {section}.{key} 必须是非空字符串")
    return _strip_optional_quotes(value)


def _require_int(
    data: configparser.SectionProxy,
    key: str,
    section: str,
    minimum: int,
) -> int:
    raw_value = _require_string(data, key, section)
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(
            f"配置项 {section}.{key} 必须是不小于 {minimum} 的整数"
        ) from error
    if value < minimum:
        raise ConfigurationError(f"配置项 {section}.{key} 必须是不小于 {minimum} 的整数")
    return value


def _require_float(
    data: configparser.SectionProxy,
    key: str,
    section: str,
    minimum: float,
) -> float:
    raw_value = _require_string(data, key, section)
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ConfigurationError(
            f"配置项 {section}.{key} 必须是不小于 {minimum} 的数值"
        ) from error
    if value < minimum:
        raise ConfigurationError(f"配置项 {section}.{key} 必须是不小于 {minimum} 的数值")
    return value


def _require_string_list(
    data: configparser.SectionProxy,
    key: str,
    section: str,
) -> tuple[str, ...]:
    raw_value = data.get(key)
    if raw_value is None or not raw_value.strip():
        raise ConfigurationError(f"配置项 {section}.{key} 必须是非空逗号分隔字符串")
    value = tuple(
        _strip_optional_quotes(item)
        for item in raw_value.split(",")
        if item.strip()
    )
    if not value:
        raise ConfigurationError(f"配置项 {section}.{key} 必须是非空逗号分隔字符串")
    if any(not item for item in value):
        raise ConfigurationError(f"配置项 {section}.{key} 包含无效格式")
    return value


def _edge_weight_method(
    data: configparser.SectionProxy,
) -> str:
    value = _strip_optional_quotes(data.get("edge_weight_method", "sppmi"))
    if value not in {"sppmi", "transaction_count"}:
        raise ConfigurationError(
            "配置项 graph.edge_weight_method 必须是 sppmi 或 transaction_count"
        )
    return value


def _community_algorithm(data: configparser.SectionProxy) -> str:
    value = _require_string(data, "algorithm", "community").lower()
    if not value.replace("_", "").isalnum() or not value.isascii():
        raise ConfigurationError(
            "配置项 community.algorithm 只能包含 ASCII 小写字母、数字和下划线"
        )
    if value != "leiden":
        raise ConfigurationError(
            f"配置项 community.algorithm 暂不支持: {value}; 当前仅支持 leiden"
        )
    return value


def _resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigurationError(f"配置文件不存在: {path}")

    parser = _read_config_parser(path)
    city = _require_table(parser, "city")
    input_data = _require_table(parser, "input")
    visits = _require_table(parser, "visits")
    cooccurrence = _require_table(parser, "cooccurrence")
    graph = _require_table(parser, "graph")
    community = _require_table(parser, "community")
    anchors = _require_table(parser, "anchors")
    output = _require_table(parser, "output")
    experiments = _require_table(parser, "experiments")

    minimum_anchor_count = _require_int(anchors, "minimum_count", "anchors", 1)
    maximum_anchor_count = _require_int(anchors, "maximum_count", "anchors", 1)
    if minimum_anchor_count > maximum_anchor_count:
        raise ConfigurationError("配置项 anchors.minimum_count 不能大于 anchors.maximum_count")

    smoothing_alpha = _require_float(graph, "context_smoothing_alpha", "graph", 0.0)
    if smoothing_alpha > 1.0:
        raise ConfigurationError("配置项 graph.context_smoothing_alpha 不能大于 1")

    participation_threshold = _require_float(
        community,
        "participation_threshold",
        "community",
        0.0,
    )
    if participation_threshold > 1.0:
        raise ConfigurationError("配置项 community.participation_threshold 不能大于 1")

    return AppConfig(
        city=CityConfig(
            code=_require_string(city, "code", "city"),
            name=_require_string(city, "name", "city"),
        ),
        input=InputConfig(
            transactions_path=_resolve_path(
                path,
                _require_string(input_data, "transactions_path", "input"),
            ),
            timestamp_formats=_require_string_list(
                input_data,
                "timestamp_formats",
                "input",
            ),
        ),
        visits=VisitConfig(
            merge_window_minutes=_require_int(
                visits,
                "merge_window_minutes",
                "visits",
                1,
            ),
            maximum_daily_merchants_per_card=_require_int(
                visits,
                "maximum_daily_merchants_per_card",
                "visits",
                1,
            ),
        ),
        cooccurrence=CooccurrenceConfig(
            window_minutes=_require_int(
                cooccurrence,
                "window_minutes",
                "cooccurrence",
                1,
            ),
            decay_tau_minutes=_require_float(
                cooccurrence,
                "decay_tau_minutes",
                "cooccurrence",
                0.000001,
            ),
            minimum_unique_users=_require_int(
                cooccurrence,
                "minimum_unique_users",
                "cooccurrence",
                1,
            ),
        ),
        graph=GraphConfig(
            edge_weight_method=_edge_weight_method(graph),
            context_smoothing_alpha=smoothing_alpha,
            sppmi_shift=_require_float(graph, "sppmi_shift", "graph", 1.0),
            top_k_neighbors=_require_int(graph, "top_k_neighbors", "graph", 1),
            minimum_z_score=_require_float(graph, "minimum_z_score", "graph", 0.0),
        ),
        community=CommunityConfig(
            algorithm=_community_algorithm(community),
            resolution=_require_float(community, "resolution", "community", 0.000001),
            random_seed=_require_int(community, "random_seed", "community", 0),
            maximum_cleaning_rounds=_require_int(
                community,
                "maximum_cleaning_rounds",
                "community",
                1,
            ),
            minimum_hub_degree=_require_int(
                community,
                "minimum_hub_degree",
                "community",
                1,
            ),
            participation_threshold=participation_threshold,
        ),
        anchors=AnchorConfig(
            minimum_count=minimum_anchor_count,
            maximum_count=maximum_anchor_count,
            merchants_per_anchor=_require_int(
                anchors,
                "merchants_per_anchor",
                "anchors",
                1,
            ),
            minimum_community_size=_require_int(
                anchors,
                "minimum_community_size",
                "anchors",
                1,
            ),
        ),
        output=OutputConfig(
            directory=_resolve_path(
                path,
                _require_string(output, "directory", "output"),
            ),
        ),
        experiments=ExperimentConfig(
            path=_resolve_path(
                path,
                _require_string(experiments, "path", "experiments"),
            ),
        ),
    )
