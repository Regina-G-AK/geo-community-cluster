from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Set

import igraph as ig
import leidenalg as la
import networkx as nx

from business_district.config import CommunityConfig
from business_district.geo import CoordinatePoint, is_suspect_online_merchant


@dataclass(frozen=True)
class CleaningResult:
    graph: nx.Graph
    partition: Dict[str, int]
    statuses: Dict[str, str]


def detect_communities(
    graph: nx.Graph,
    resolution: float,
    random_seed: int,
) -> Dict[str, int]:
    connected_nodes = sorted(node for node in graph if graph.degree(node) > 0)
    isolated_nodes = sorted(node for node in graph if graph.degree(node) == 0)
    connected_graph = graph.subgraph(connected_nodes)

    communities: List[Set[str]] = []
    if connected_graph.number_of_nodes() > 0:
        node_ids = [str(node) for node in connected_nodes]
        node_indices = {
            node_id: node_index
            for node_index, node_id in enumerate(node_ids)
        }
        networkx_edges = list(connected_graph.edges(data=True))
        igraph_graph = ig.Graph(
            n=len(node_ids),
            edges=[
                (node_indices[str(left)], node_indices[str(right)])
                for left, right, _ in networkx_edges
            ],
            directed=False,
        )
        igraph_graph.vs["name"] = node_ids
        weights = [float(edge_data["weight"]) for _, _, edge_data in networkx_edges]
        partition = la.find_partition(
            igraph_graph,
            la.RBConfigurationVertexPartition,
            weights=weights,
            resolution_parameter=resolution,
            seed=random_seed,
        )
        communities = [
            {node_ids[node_index] for node_index in community}
            for community in partition
        ]
    communities.extend({node} for node in isolated_nodes)
    ordered = sorted(
        communities,
        key=lambda members: (-len(members), min(str(member) for member in members)),
    )
    return {
        str(node): community_id
        for community_id, members in enumerate(ordered)
        for node in members
    }


def calculate_participation(
    graph: nx.Graph,
    partition: Dict[str, int],
) -> Dict[str, float]:
    community_shares = calculate_community_weight_shares(graph, partition)
    return {
        merchant_id: 1.0 - sum(share**2 for share in shares.values())
        for merchant_id, shares in community_shares.items()
    }


def calculate_community_weight_shares(
    graph: nx.Graph,
    partition: Dict[str, int],
) -> Dict[str, Dict[int, float]]:
    shares_by_merchant: Dict[str, Dict[int, float]] = {}
    for node in graph:
        weight_by_community: Dict[int, float] = defaultdict(float)
        total_weight = 0.0
        for neighbor, edge_data in graph[node].items():
            weight = float(edge_data["weight"])
            total_weight += weight
            weight_by_community[partition[neighbor]] += weight
        if total_weight == 0:
            shares_by_merchant[str(node)] = {partition[str(node)]: 1.0}
            continue
        shares_by_merchant[str(node)] = {
            community_id: weight / total_weight
            for community_id, weight in weight_by_community.items()
        }
    return shares_by_merchant


def clean_graph(
    graph: nx.Graph,
    config: CommunityConfig,
    merchant_coordinates: Dict[str, CoordinatePoint],
    online_distance_threshold_meters: float,
) -> CleaningResult:
    working_graph = graph.copy()
    statuses = {
        str(node): "suspect_isolated" if graph.degree(node) == 0 else "active"
        for node in graph
    }
    suspect_online_merchants = [
        str(node)
        for node in working_graph
        if is_suspect_online_merchant(
            str(node),
            working_graph,
            merchant_coordinates,
            config.minimum_online_neighbor_count,
            online_distance_threshold_meters,
        )
    ]
    working_graph.remove_nodes_from(suspect_online_merchants)
    for merchant_id in suspect_online_merchants:
        statuses[merchant_id] = "suspect_online"

    final_partition = detect_communities(
        working_graph,
        config.resolution,
        config.random_seed,
    )
    return CleaningResult(
        graph=working_graph,
        partition=final_partition,
        statuses=statuses,
    )
