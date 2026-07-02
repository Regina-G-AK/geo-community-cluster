from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from business_district.errors import TransactionDataError

from incremental_assignment.io import read_csv_checked, read_optional_coordinates, write_csv
from incremental_assignment.models import CandidateMerchant, DecisionResult

MERCHANT_ARCHIVE_COLUMNS = [
    "city_code",
    "merchant_id",
    "community_id",
    "is_anchor",
    "assignment_confidence",
    "assignment_source",
    "latitude",
    "longitude",
    "customer_ids",
    "hourly_profile",
]
COMMUNITY_ARCHIVE_COLUMNS = [
    "city_code",
    "community_id",
    "merchant_count",
    "anchor_count",
    "anchor_merchants",
    "customer_ids",
    "centroid_latitude",
    "centroid_longitude",
    "sigma_meters",
    "hourly_profile",
]


def _to_pipe(values: list[str]) -> str:
    return "|".join(sorted(value for value in values if value))


def _parse_pipe_set(value: object) -> set[str]:
    if pd.isna(value):
        return set()
    return {item.strip() for item in str(value).split("|") if item.strip()}


def _parse_hourly_profile(value: object) -> tuple[int, ...]:
    if pd.isna(value) or not str(value).strip():
        return tuple(0 for _ in range(24))
    parts = [item.strip() for item in str(value).split("|")]
    if len(parts) != 24:
        raise TransactionDataError(f"档案 hourly_profile 必须包含 24 个数值: value={value}")
    return tuple(int(item) for item in parts)


def _format_hourly_profile(values: tuple[int, ...]) -> str:
    if len(values) != 24:
        raise TransactionDataError(f"小时画像必须包含 24 个数值: length={len(values)}")
    return "|".join(str(value) for value in values)


def _haversine_meters(
    left_latitude: float,
    left_longitude: float,
    right_latitude: float,
    right_longitude: float,
) -> float:
    radius = 6371000.0
    left_lat = math.radians(left_latitude)
    right_lat = math.radians(right_latitude)
    delta_lat = math.radians(right_latitude - left_latitude)
    delta_lon = math.radians(right_longitude - left_longitude)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(left_lat)
        * math.cos(right_lat)
        * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * radius * math.atan2(math.sqrt(value), math.sqrt(1.0 - value))


def _valid_coordinate(latitude: object, longitude: object) -> bool:
    if pd.isna(latitude) or pd.isna(longitude):
        return False
    return str(latitude).strip() != "" and str(longitude).strip() != ""


def _with_coordinates(
    merchants: pd.DataFrame,
    coordinates_path: Path | None,
) -> pd.DataFrame:
    coordinates = read_optional_coordinates(coordinates_path)
    if coordinates.empty:
        result = merchants.copy()
        result["latitude"] = ""
        result["longitude"] = ""
        return result
    coordinates = coordinates[["city_code", "merchant_id", "latitude", "longitude"]]
    merged = merchants.merge(
        coordinates,
        on=["city_code", "merchant_id"],
        how="left",
    )
    return merged


def build_initial_merchant_archive(
    algorithm_merchants: pd.DataFrame,
    coordinates_path: Path | None,
) -> pd.DataFrame:
    enriched = _with_coordinates(algorithm_merchants, coordinates_path)
    result = pd.DataFrame(
        {
            "city_code": enriched["city_code"].astype(str),
            "merchant_id": enriched["merchant_id"].astype(str),
            "community_id": enriched["community_id"].astype(int),
            "is_anchor": enriched["is_anchor_candidate"].astype(int),
            "assignment_confidence": 1.0,
            "assignment_source": "algorithm_one",
            "latitude": enriched["latitude"],
            "longitude": enriched["longitude"],
            "customer_ids": "",
            "hourly_profile": _format_hourly_profile(tuple(0 for _ in range(24))),
        }
    )
    return result[MERCHANT_ARCHIVE_COLUMNS]


def load_or_build_merchant_archive(
    path: Path,
    algorithm_merchants: pd.DataFrame,
    coordinates_path: Path | None,
) -> pd.DataFrame:
    if path.exists():
        return read_csv_checked(path, set(MERCHANT_ARCHIVE_COLUMNS), "商户档案")
    return build_initial_merchant_archive(algorithm_merchants, coordinates_path)


def build_community_archive(
    merchants: pd.DataFrame,
    minimum_sigma_meters: float,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    active = merchants.loc[merchants["community_id"].astype(int) >= 0]
    for (city_code, community_id), group in active.groupby(["city_code", "community_id"], sort=True):
        customer_ids: set[str] = set()
        hourly = [0 for _ in range(24)]
        for row in group.to_dict("records"):
            customer_ids.update(_parse_pipe_set(row["customer_ids"]))
            profile = _parse_hourly_profile(row["hourly_profile"])
            hourly = [left + right for left, right in zip(hourly, profile)]

        coordinates = [
            (float(row["latitude"]), float(row["longitude"]))
            for row in group.to_dict("records")
            if _valid_coordinate(row["latitude"], row["longitude"])
        ]
        centroid_latitude = ""
        centroid_longitude = ""
        sigma_meters = minimum_sigma_meters
        if coordinates:
            centroid_latitude = sum(latitude for latitude, _ in coordinates) / len(coordinates)
            centroid_longitude = sum(longitude for _, longitude in coordinates) / len(coordinates)
        anchor_group = group.loc[group["is_anchor"].astype(int) == 1]
        anchor_coordinates = [
            (float(row["latitude"]), float(row["longitude"]))
            for row in anchor_group.to_dict("records")
            if _valid_coordinate(row["latitude"], row["longitude"])
        ]
        if len(anchor_coordinates) >= 2:
            distances = [
                _haversine_meters(left[0], left[1], right[0], right[1])
                for index, left in enumerate(anchor_coordinates)
                for right in anchor_coordinates[index + 1 :]
            ]
            distances.sort()
            midpoint = len(distances) // 2
            if len(distances) % 2 == 1:
                sigma_meters = max(minimum_sigma_meters, distances[midpoint])
            else:
                sigma_meters = max(
                    minimum_sigma_meters,
                    (distances[midpoint - 1] + distances[midpoint]) / 2.0,
                )
        rows.append(
            {
                "city_code": str(city_code),
                "community_id": int(community_id),
                "merchant_count": int(len(group)),
                "anchor_count": int(anchor_group.shape[0]),
                "anchor_merchants": _to_pipe(anchor_group["merchant_id"].astype(str).tolist()),
                "customer_ids": _to_pipe(list(customer_ids)),
                "centroid_latitude": centroid_latitude,
                "centroid_longitude": centroid_longitude,
                "sigma_meters": sigma_meters,
                "hourly_profile": _format_hourly_profile(tuple(hourly)),
            }
        )
    return pd.DataFrame(rows, columns=COMMUNITY_ARCHIVE_COLUMNS)


def apply_assignments(
    merchants: pd.DataFrame,
    decisions: list[DecisionResult],
    candidates_by_key: dict[tuple[str, str], CandidateMerchant],
) -> pd.DataFrame:
    assigned = [decision for decision in decisions if decision.decision == "assigned"]
    if not assigned:
        return merchants.copy()
    existing_keys = {
        (str(row["city_code"]), str(row["merchant_id"]))
        for row in merchants.to_dict("records")
    }
    rows: list[dict[str, object]] = []
    for decision in assigned:
        key = (decision.city_code, decision.merchant_id)
        if key in existing_keys:
            raise TransactionDataError(
                f"待归入商户已存在于商户档案: city_code={decision.city_code}, merchant_id={decision.merchant_id}"
            )
        candidate = candidates_by_key[key]
        rows.append(
            {
                "city_code": candidate.city_code,
                "merchant_id": candidate.merchant_id,
                "community_id": int(decision.assigned_community_id),
                "is_anchor": 0,
                "assignment_confidence": float(decision.top_score),
                "assignment_source": "automatic_incremental",
                "latitude": "" if candidate.latitude is None else candidate.latitude,
                "longitude": "" if candidate.longitude is None else candidate.longitude,
                "customer_ids": _to_pipe(list(candidate.customer_ids)),
                "hourly_profile": _format_hourly_profile(candidate.hourly_profile),
            }
        )
    updated = pd.concat([merchants, pd.DataFrame(rows)], ignore_index=True)
    return updated[MERCHANT_ARCHIVE_COLUMNS]


def write_archives(
    merchant_archive: pd.DataFrame,
    community_archive: pd.DataFrame,
    merchant_archive_path: Path,
    community_archive_path: Path,
    run_directory: Path,
) -> None:
    write_csv(merchant_archive, merchant_archive_path)
    write_csv(community_archive, community_archive_path)
    write_csv(merchant_archive, run_directory / "merchant_archive.csv")
    write_csv(community_archive, run_directory / "community_archive.csv")

