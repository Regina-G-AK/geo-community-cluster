from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

import networkx as nx
import pandas as pd

from business_district.errors import TransactionDataError
from business_district.transactions import (
    LATITUDE,
    LONGITUDE,
    MERCHANT,
    SOURCE_MERCHANT,
)

EARTH_RADIUS_METERS = 6371008.8


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


def _coordinate_components(
    points: Tuple[CoordinatePoint, ...],
    radius_meters: float,
) -> Tuple[Tuple[str, ...], ...]:
    graph = nx.Graph()
    graph.add_nodes_from(point.item_id for point in points)
    for left_index, left in enumerate(points[:-1]):
        for right in points[left_index + 1 :]:
            distance = haversine_distance_meters(
                left.longitude,
                left.latitude,
                right.longitude,
                right.latitude,
            )
            if distance <= radius_meters:
                graph.add_edge(left.item_id, right.item_id)
    components = tuple(
        tuple(sorted(str(item_id) for item_id in component))
        for component in nx.connected_components(graph)
    )
    return tuple(sorted(components, key=lambda component: (-len(component), component)))


def _entity_id(
    source_merchant_id: str,
    component_index: int,
) -> str:
    return f"{source_merchant_id}#geo{component_index + 1:03d}"


def split_geographic_entities(
    transactions: pd.DataFrame,
    radius_meters: float,
) -> pd.DataFrame:
    normalized = transactions.reset_index(drop=True)
    if SOURCE_MERCHANT not in transactions.columns:
        source = normalized.assign(**{SOURCE_MERCHANT: normalized[MERCHANT]})
    else:
        source = normalized.copy()
    result = source.copy()
    result[MERCHANT] = result[MERCHANT].astype(str)
    result[SOURCE_MERCHANT] = result[SOURCE_MERCHANT].astype(str)
    existing_merchants = set(result[MERCHANT].astype(str))
    entity_ids_by_row: Dict[int, str] = {}

    for source_merchant_id, group in source.groupby(SOURCE_MERCHANT, sort=True):
        positioned = group.loc[_coordinate_mask(group)]
        if positioned.empty:
            continue
        points = tuple(
            CoordinatePoint(
                item_id=str(row_index),
                longitude=float(row[LONGITUDE]),
                latitude=float(row[LATITUDE]),
            )
            for row_index, row in positioned.iterrows()
        )
        components = _coordinate_components(points, radius_meters)
        if len(components) <= 1:
            continue
        generated_ids = tuple(
            _entity_id(str(source_merchant_id), component_index)
            for component_index in range(len(components))
        )
        collisions = sorted(
            entity_id
            for entity_id in generated_ids
            if entity_id in existing_merchants
        )
        if collisions:
            raise TransactionDataError(
                "Generated geographic merchant ids collide with source merchant ids: "
                f"source_merchant_id={source_merchant_id!r}, collisions={collisions}"
            )
        for component_index, component in enumerate(components):
            entity_id = generated_ids[component_index]
            for row_index in component:
                entity_ids_by_row[int(row_index)] = entity_id

    if not entity_ids_by_row:
        return result.reset_index(drop=True)

    assigned = pd.Series(entity_ids_by_row, dtype="string")
    result.loc[assigned.index, MERCHANT] = assigned
    return result.sort_values([MERCHANT]).reset_index(drop=True)


def _merchant_coordinate_points(transactions: pd.DataFrame) -> Tuple[CoordinatePoint, ...]:
    positioned = transactions.loc[_coordinate_mask(transactions)]
    if positioned.empty:
        return tuple()
    grouped = positioned.groupby(MERCHANT, sort=True)[[LONGITUDE, LATITUDE]].median()
    points = tuple(
        CoordinatePoint(
            item_id=str(merchant_id),
            longitude=float(row[LONGITUDE]),
            latitude=float(row[LATITUDE]),
        )
        for merchant_id, row in grouped.iterrows()
    )
    return points


def _component_seed_pairs(
    points_by_id: Dict[str, CoordinatePoint],
    component: Tuple[str, ...],
    radius_meters: float,
) -> Tuple[Tuple[str, str], ...]:
    pairs: List[Tuple[str, str]] = []
    for left_index, left_id in enumerate(component[:-1]):
        left = points_by_id[left_id]
        for right_id in component[left_index + 1 :]:
            right = points_by_id[right_id]
            distance = haversine_distance_meters(
                left.longitude,
                left.latitude,
                right.longitude,
                right.latitude,
            )
            if distance <= radius_meters:
                pairs.append(tuple(sorted((left_id, right_id))))
    return tuple(sorted(set(pairs)))


def build_geographic_seed_pairs(
    transactions: pd.DataFrame,
    radius_meters: float,
) -> Tuple[Tuple[Tuple[str, str], ...], int, int]:
    points = _merchant_coordinate_points(transactions)
    if not points:
        return tuple(), 0, 0
    components = _coordinate_components(points, radius_meters)
    points_by_id = {point.item_id: point for point in points}
    seed_pairs: List[Tuple[str, str]] = []
    seed_cluster_count = 0
    for component in components:
        if len(component) < 2:
            continue
        seed_cluster_count += 1
        seed_pairs.extend(
            _component_seed_pairs(points_by_id, component, radius_meters)
        )
    return tuple(sorted(set(seed_pairs))), len(points), seed_cluster_count


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
    split_transactions = split_geographic_entities(transactions, radius_meters)
    seed_pairs, positioned_merchant_count, seed_cluster_count = (
        build_geographic_seed_pairs(split_transactions, radius_meters)
    )
    positioned_transaction_count = int(_coordinate_mask(split_transactions).sum())
    split_entity_count = int(
        split_transactions.loc[
            split_transactions[MERCHANT].astype(str)
            != split_transactions[SOURCE_MERCHANT].astype(str),
            MERCHANT,
        ].nunique()
    )
    return GeographicPreparation(
        transactions=split_transactions,
        seed_pairs=seed_pairs,
        summary=GeographicSummary(
            positioned_transaction_count=positioned_transaction_count,
            positioned_merchant_count=positioned_merchant_count,
            split_entity_count=split_entity_count,
            seed_cluster_count=seed_cluster_count,
            seed_edge_count=len(seed_pairs),
        ),
    )
