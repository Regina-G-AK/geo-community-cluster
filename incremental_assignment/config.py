from __future__ import annotations

import configparser
from pathlib import Path

from business_district.errors import ConfigurationError

from incremental_assignment.models import AppConfig, AssignmentConfig, PathConfig


def _read_config_parser(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        with path.open("r", encoding="utf-8") as file:
            parser.read_file(file)
    except configparser.Error as error:
        raise ConfigurationError(f"配置文件格式错误: path={path}, reason={error}") from error
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


def _optional_string(
    data: configparser.SectionProxy,
    key: str,
) -> str | None:
    value = data.get(key)
    if value is None or not value.strip():
        return None
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
        raise ConfigurationError(f"配置项 {section}.{key} 必须是不小于 {minimum} 的整数") from error
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
        raise ConfigurationError(f"配置项 {section}.{key} 必须是不小于 {minimum} 的数值") from error
    if value < minimum:
        raise ConfigurationError(f"配置项 {section}.{key} 必须是不小于 {minimum} 的数值")
    return value


def _resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def _optional_resolve_path(config_path: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    return _resolve_path(config_path, value)


def _validate_weight_sum(config: AssignmentConfig) -> None:
    total = config.graph_weight + config.geo_weight + config.customer_weight
    if abs(total - 1.0) > 0.000001:
        raise ConfigurationError(
            "配置项 assignment.graph_weight、geo_weight、customer_weight 之和必须等于 1"
        )


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigurationError(f"配置文件不存在: {path}")

    parser = _read_config_parser(path)
    paths = _require_table(parser, "paths")
    assignment = _require_table(parser, "assignment")

    app_config = AppConfig(
        paths=PathConfig(
            algorithm_one_directory=_resolve_path(
                path,
                _require_string(paths, "algorithm_one_directory", "paths"),
            ),
            candidate_merchants_path=_resolve_path(
                path,
                _require_string(paths, "candidate_merchants_path", "paths"),
            ),
            output_directory=_resolve_path(
                path,
                _require_string(paths, "output_directory", "paths"),
            ),
            observation_pool_path=_resolve_path(
                path,
                _require_string(paths, "observation_pool_path", "paths"),
            ),
            merchant_archive_path=_resolve_path(
                path,
                _require_string(paths, "merchant_archive_path", "paths"),
            ),
            community_archive_path=_resolve_path(
                path,
                _require_string(paths, "community_archive_path", "paths"),
            ),
            experiments_path=_resolve_path(
                path,
                _require_string(paths, "experiments_path", "paths"),
            ),
            coordinates_path=_optional_resolve_path(
                path,
                _optional_string(paths, "coordinates_path"),
            ),
        ),
        assignment=AssignmentConfig(
            min_observation_days=_require_int(
                assignment,
                "min_observation_days",
                "assignment",
                1,
            ),
            min_unique_users=_require_int(
                assignment,
                "min_unique_users",
                "assignment",
                1,
            ),
            top_k_neighbors=_require_int(
                assignment,
                "top_k_neighbors",
                "assignment",
                1,
            ),
            anchor_vote_weight=_require_float(
                assignment,
                "anchor_vote_weight",
                "assignment",
                1.0,
            ),
            theta=_require_float(assignment, "theta", "assignment", 0.0),
            delta=_require_float(assignment, "delta", "assignment", 0.0),
            graph_weight=_require_float(
                assignment,
                "graph_weight",
                "assignment",
                0.0,
            ),
            geo_weight=_require_float(
                assignment,
                "geo_weight",
                "assignment",
                0.0,
            ),
            customer_weight=_require_float(
                assignment,
                "customer_weight",
                "assignment",
                0.0,
            ),
            minimum_sigma_meters=_require_float(
                assignment,
                "minimum_sigma_meters",
                "assignment",
                0.000001,
            ),
            community_assignment_distance_meters=_require_float(
                assignment,
                "community_assignment_distance_meters",
                "assignment",
                0.000001,
            ),
            city_maximum_distance_meters=_require_float(
                assignment,
                "city_maximum_distance_meters",
                "assignment",
                0.000001,
            ),
        ),
    )
    if app_config.assignment.theta > 1.0:
        raise ConfigurationError("配置项 assignment.theta 不能大于 1")
    if app_config.assignment.delta > 1.0:
        raise ConfigurationError("配置项 assignment.delta 不能大于 1")
    if (
        app_config.assignment.community_assignment_distance_meters
        > app_config.assignment.city_maximum_distance_meters
    ):
        raise ConfigurationError(
            "配置项 assignment.community_assignment_distance_meters "
            "不能大于 assignment.city_maximum_distance_meters"
        )
    _validate_weight_sum(app_config.assignment)
    return app_config
