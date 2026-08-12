from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import networkx as nx
import pandas as pd

from business_district.errors import TransactionDataError
from business_district.probes import print_probe
from business_district.transactions import (
    LATITUDE,
    LONGITUDE,
    MERCHANT,
    SOURCE_MERCHANT,
)

EARTH_RADIUS_METERS = 6371008.8
SEED_PAIR_POINT_PROGRESS_INTERVAL = 1000
SEED_PAIR_COUNT_PROGRESS_START = 1000000


@dataclass(frozen=True)
class GeographicSummary:
    positioned_transaction_count: int
    positioned_merchant_count: int
    split_entity_count: int
    seed_cluster_count: int
    seed_edge_count: int


@dataclass(frozen=True)
class GeographicPreparation:
    transactions: pd.DataFrame
    seed_pairs: Tuple[Tuple[str, str], ...]
    summary: GeographicSummary


@dataclass(frozen=True)
class CoordinatePoint:
    item_id: str
    longitude: float
    latitude: float


def haversine_distance_meters(
    left_longitude: float,
    left_latitude: float,
    right_longitude: float,
    right_latitude: float,
) -> float:
    left_latitude_radians = math.radians(left_latitude)
    right_latitude_radians = math.radians(right_latitude)
    latitude_delta = math.radians(right_latitude - left_latitude)
    longitude_delta = math.radians(right_longitude - left_longitude)
    area = (
        math.sin(latitude_delta / 2.0) ** 2
        + math.cos(left_latitude_radians)
        * math.cos(right_latitude_radians)
        * math.sin(longitude_delta / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_METERS * math.asin(math.sqrt(area))


def _coordinate_mask(dataframe: pd.DataFrame) -> pd.Series:
    return dataframe[LONGITUDE].notna() & dataframe[LATITUDE].notna()


def _spatial_cell(
    point: CoordinatePoint,
    radius_meters: float,
) -> Tuple[int, int, int]:
    longitude_radians = math.radians(point.longitude)
    latitude_radians = math.radians(point.latitude)
    latitude_cosine = math.cos(latitude_radians)
    x = EARTH_RADIUS_METERS * latitude_cosine * math.cos(longitude_radians)
    y = EARTH_RADIUS_METERS * latitude_cosine * math.sin(longitude_radians)
    z = EARTH_RADIUS_METERS * math.sin(latitude_radians)
    return (
        math.floor(x / radius_meters),
        math.floor(y / radius_meters),
        math.floor(z / radius_meters),
    )


def _nearby_coordinate_pairs(
    points: Tuple[CoordinatePoint, ...],
    radius_meters: float,
) -> Tuple[Tuple[str, str], ...]:
    points_by_cell: Dict[Tuple[int, int, int], List[CoordinatePoint]] = {}
    pairs: List[Tuple[str, str]] = []
    candidate_comparison_count = 0
    maximum_cell_merchant_count = 0
    next_pair_probe_count = SEED_PAIR_COUNT_PROGRESS_START
    for point_index, point in enumerate(points):
        cell = _spatial_cell(point, radius_meters)
        for x_offset in (-1, 0, 1):
            for y_offset in (-1, 0, 1):
                for z_offset in (-1, 0, 1):
                    neighbor_cell = (
                        cell[0] + x_offset,
                        cell[1] + y_offset,
                        cell[2] + z_offset,
                    )
                    for neighbor in points_by_cell.get(neighbor_cell, []):
                        candidate_comparison_count += 1
                        distance = haversine_distance_meters(
                            point.longitude,
                            point.latitude,
                            neighbor.longitude,
                            neighbor.latitude,
                        )
                        if distance <= radius_meters:
                            pairs.append(
                                tuple(sorted((point.item_id, neighbor.item_id)))
                            )
        points_by_cell.setdefault(cell, []).append(point)
        current_cell_merchant_count = len(points_by_cell[cell])
        maximum_cell_merchant_count = max(
            maximum_cell_merchant_count,
            current_cell_merchant_count,
        )
        processed_point_count = point_index + 1
        pair_probe_reached = len(pairs) >= next_pair_probe_count
        if pair_probe_reached:
            while len(pairs) >= next_pair_probe_count:
                next_pair_probe_count *= 2
        if (
            processed_point_count % SEED_PAIR_POINT_PROGRESS_INTERVAL == 0
            or pair_probe_reached
            or processed_point_count == len(points)
        ):
            print_probe(
                "initial.geo.seed_pair_progress",
                f"processed_point_count={processed_point_count}, "
                f"total_point_count={len(points)}, "
                f"candidate_comparison_count={candidate_comparison_count}, "
                f"accepted_pair_count={len(pairs)}, "
                f"spatial_cell_count={len(points_by_cell)}, "
                f"maximum_cell_merchant_count={maximum_cell_merchant_count}",
            )
    print_probe(
        "initial.geo.seed_pair_sort_started",
        f"pair_count={len(pairs)}",
    )
    ordered_pairs = tuple(sorted(pairs))
    print_probe(
        "initial.geo.seed_pair_sort_ready",
        f"pair_count={len(ordered_pairs)}",
    )
    return ordered_pairs


def _validated_merchant_coordinate_rows(
    transactions: pd.DataFrame,
) -> pd.DataFrame:
    print_probe(
        "initial.geo.coordinate_row_selection_started",
        f"transaction_rows={len(transactions)}",
    )
    positioned = transactions.loc[
        _coordinate_mask(transactions),
        [MERCHANT, LONGITUDE, LATITUDE],
    ].copy()
    print_probe(
        "initial.geo.positioned_rows_ready",
        f"positioned_transaction_rows={len(positioned)}",
    )
    if positioned.empty:
        return positioned
    positioned[MERCHANT] = positioned[MERCHANT].astype(str)
    distinct = positioned.drop_duplicates(
        subset=[MERCHANT, LONGITUDE, LATITUDE]
    )
    coordinate_group_sizes = distinct.groupby(
        [LONGITUDE, LATITUDE],
        sort=False,
    ).size()
    maximum_merchants_at_same_coordinate = int(coordinate_group_sizes.max())
    shared_coordinate_count = int((coordinate_group_sizes > 1).sum())
    print_probe(
        "initial.geo.distinct_coordinate_rows_ready",
        f"distinct_coordinate_rows={len(distinct)}, "
        f"coordinate_count={len(coordinate_group_sizes)}, "
        f"shared_coordinate_count={shared_coordinate_count}, "
        f"maximum_merchants_at_same_coordinate="
        f"{maximum_merchants_at_same_coordinate}",
    )
    coordinate_counts = distinct.groupby(MERCHANT, sort=True).size()
    conflicted_merchants = coordinate_counts.loc[coordinate_counts > 1].index.tolist()
    if conflicted_merchants:
        conflicts: List[str] = []
        for merchant_id in conflicted_merchants[:10]:
            coordinates = distinct.loc[
                distinct[MERCHANT].eq(merchant_id),
                [LONGITUDE, LATITUDE],
            ]
            coordinate_samples = [
                (
                    float(longitude),
                    float(latitude),
                )
                for longitude, latitude in coordinates.head(5).itertuples(
                    index=False,
                    name=None,
                )
            ]
            conflicts.append(
                f"merchant_id={str(merchant_id)!r}, "
                f"coordinates={coordinate_samples}"
            )
        raise TransactionDataError(
            "同一商户存在不一致的有效经纬度: "
            f"conflicted_merchant_count={len(conflicted_merchants)}, "
            f"examples={conflicts}"
        )
    return distinct.sort_values(MERCHANT).reset_index(drop=True)


def _merchant_coordinate_points(transactions: pd.DataFrame) -> Tuple[CoordinatePoint, ...]:
    coordinate_rows = _validated_merchant_coordinate_rows(transactions)
    if coordinate_rows.empty:
        return tuple()
    points = tuple(
        CoordinatePoint(
            item_id=str(row[MERCHANT]),
            longitude=float(row[LONGITUDE]),
            latitude=float(row[LATITUDE]),
        )
        for _, row in coordinate_rows.iterrows()
    )
    return points


def build_merchant_coordinates(
    transactions: pd.DataFrame,
) -> Dict[str, CoordinatePoint]:
    return {
        point.item_id: point
        for point in _merchant_coordinate_points(transactions)
    }


def filter_reliable_merchant_coordinates(
    merchant_coordinates: Dict[str, CoordinatePoint],
    maximum_merchants_per_coordinate: int,
) -> Dict[str, CoordinatePoint]:
    if maximum_merchants_per_coordinate < 1:
        raise ValueError(
            "同一坐标最大商户数必须不小于 1: "
            f"maximum_merchants_per_coordinate="
            f"{maximum_merchants_per_coordinate}"
        )
    coordinate_counts: Dict[Tuple[float, float], int] = {}
    for point in merchant_coordinates.values():
        coordinate = (point.longitude, point.latitude)
        coordinate_counts[coordinate] = coordinate_counts.get(coordinate, 0) + 1
    unreliable_coordinates: Set[Tuple[float, float]] = {
        coordinate
        for coordinate, merchant_count in coordinate_counts.items()
        if merchant_count > maximum_merchants_per_coordinate
    }
    reliable_coordinates: Dict[str, CoordinatePoint] = {
        merchant_id: point
        for merchant_id, point in merchant_coordinates.items()
        if (point.longitude, point.latitude) not in unreliable_coordinates
    }
    maximum_coordinate_merchant_count = (
        max(coordinate_counts.values()) if coordinate_counts else 0
    )
    print_probe(
        "coordinate_reliability_ready",
        f"coordinate_merchant_count={len(merchant_coordinates)}, "
        f"reliable_coordinate_merchant_count={len(reliable_coordinates)}, "
        f"excluded_coordinate_merchant_count="
        f"{len(merchant_coordinates) - len(reliable_coordinates)}, "
        f"unreliable_coordinate_count={len(unreliable_coordinates)}, "
        f"maximum_coordinate_merchant_count="
        f"{maximum_coordinate_merchant_count}, "
        f"maximum_merchants_per_coordinate="
        f"{maximum_merchants_per_coordinate}",
    )
    return reliable_coordinates


def is_suspect_online_merchant(
    merchant_id: str,
    graph: nx.Graph,
    merchant_coordinates: Dict[str, CoordinatePoint],
    minimum_neighbor_count: int,
    distance_threshold_meters: float,
) -> bool:
    if minimum_neighbor_count < 2:
        raise ValueError(
            "疑似线上商户最少关联坐标商户数必须不小于 2: "
            f"minimum_neighbor_count={minimum_neighbor_count}"
        )
    if distance_threshold_meters <= 0.0:
        raise ValueError(
            "疑似线上商户距离阈值必须大于 0: "
            f"distance_threshold_meters={distance_threshold_meters}"
        )
    if merchant_id in merchant_coordinates or merchant_id not in graph:
        return False
    linked_points = tuple(
        merchant_coordinates[str(neighbor)]
        for neighbor in graph.neighbors(merchant_id)
        if str(neighbor) in merchant_coordinates
        and float(graph[merchant_id][neighbor].get("weight", 0.0)) > 0.0
    )
    if len(linked_points) < minimum_neighbor_count:
        return False
    for left_index, left in enumerate(linked_points[:-1]):
        for right in linked_points[left_index + 1 :]:
            if (
                haversine_distance_meters(
                    left.longitude,
                    left.latitude,
                    right.longitude,
                    right.latitude,
                )
                > distance_threshold_meters
            ):
                return True
    return False


def build_geographic_seed_pairs(
    transactions: pd.DataFrame,
    radius_meters: float,
) -> Tuple[Tuple[Tuple[str, str], ...], int, int]:
    points = _merchant_coordinate_points(transactions)
    print_probe(
        "initial.geo.coordinate_points_ready",
        f"positioned_merchant_count={len(points)}",
    )
    if not points:
        return tuple(), 0, 0
    seed_pairs = _nearby_coordinate_pairs(points, radius_meters)
    seed_graph = nx.Graph()
    seed_graph.add_nodes_from(point.item_id for point in points)
    print_probe(
        "initial.geo.seed_graph_build_started",
        f"node_count={len(points)}, edge_count={len(seed_pairs)}",
    )
    seed_graph.add_edges_from(seed_pairs)
    print_probe(
        "initial.geo.seed_graph_ready",
        f"node_count={seed_graph.number_of_nodes()}, "
        f"edge_count={seed_graph.number_of_edges()}",
    )
    seed_cluster_count = sum(
        1
        for component in nx.connected_components(seed_graph)
        if len(component) >= 2
    )
    return seed_pairs, len(points), seed_cluster_count


def add_geographic_seed_edges(
    graph: nx.Graph,
    seed_pairs: Tuple[Tuple[str, str], ...],
) -> nx.Graph:
    seeded_graph = graph.copy()
    if not seed_pairs:
        return seeded_graph
    existing_weights = [
        float(edge_data["weight"])
        for _, _, edge_data in seeded_graph.edges(data=True)
    ]
    seed_weight = (max(existing_weights) * 10.0) if existing_weights else 1.0
    for left, right in seed_pairs:
        if seeded_graph.has_edge(left, right):
            edge_data = seeded_graph.edges[left, right]
            edge_data["weight"] = float(edge_data["weight"]) + seed_weight
            edge_data["geo_seed"] = 1
            continue
        seeded_graph.add_edge(left, right, weight=seed_weight, support=0, geo_seed=1)
    return seeded_graph


def prepare_geographic_transactions(
    transactions: pd.DataFrame,
    radius_meters: float,
) -> GeographicPreparation:
    reset_transactions = transactions.reset_index(drop=True)
    print_probe(
        "initial.geo.index_reset_ready",
        f"row_count={len(reset_transactions)}, "
        f"column_count={len(reset_transactions.columns)}",
    )
    normalized = reset_transactions.copy()
    print_probe(
        "initial.geo.normalized_copy_ready",
        f"row_count={len(normalized)}, column_count={len(normalized.columns)}",
    )
    del reset_transactions
    normalized[MERCHANT] = normalized[MERCHANT].astype(str)
    print_probe(
        "initial.geo.merchant_id_ready",
        f"row_count={len(normalized)}",
    )
    if SOURCE_MERCHANT not in normalized.columns:
        prepared_transactions = normalized.assign(
            **{SOURCE_MERCHANT: normalized[MERCHANT]}
        )
    else:
        prepared_transactions = normalized.copy()
        print_probe(
            "initial.geo.prepared_copy_ready",
            f"row_count={len(prepared_transactions)}, "
            f"column_count={len(prepared_transactions.columns)}",
        )
        prepared_transactions[SOURCE_MERCHANT] = prepared_transactions[
            SOURCE_MERCHANT
        ].astype(str)
    print_probe(
        "initial.geo.prepared_transactions_ready",
        f"row_count={len(prepared_transactions)}, "
        f"column_count={len(prepared_transactions.columns)}",
    )
    seed_pairs, positioned_merchant_count, seed_cluster_count = (
        build_geographic_seed_pairs(prepared_transactions, radius_meters)
    )
    positioned_transaction_count = int(
        _coordinate_mask(prepared_transactions).sum()
    )
    return GeographicPreparation(
        transactions=prepared_transactions,
        seed_pairs=seed_pairs,
        summary=GeographicSummary(
            positioned_transaction_count=positioned_transaction_count,
            positioned_merchant_count=positioned_merchant_count,
            split_entity_count=0,
            seed_cluster_count=seed_cluster_count,
            seed_edge_count=len(seed_pairs),
        ),
    )
