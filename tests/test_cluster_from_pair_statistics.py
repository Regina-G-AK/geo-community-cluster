import math

import networkx as nx

from stuff.cluster_from_pair_statistics import (
    AnchorConfig,
    CleaningResult,
    CooccurrenceConfig,
    GraphConfig,
    PairStatistics,
    build_merchant_results,
    build_sparse_graph,
    prepare_edge_candidates,
)


def _graph_config(
    edge_weight_method: str,
    degree_penalty_gamma: float,
    jaccard_threshold: float,
) -> GraphConfig:
    return GraphConfig(
        edge_weight_method=edge_weight_method,
        context_smoothing_alpha=0.75,
        sppmi_shift=3.0,
        top_k_neighbors=10,
        minimum_z_score=0.0,
        degree_penalty_gamma=degree_penalty_gamma,
        jaccard_threshold=jaccard_threshold,
    )


def _anchor_config(
    maximum_participation: float,
    chain_visit_count_quantile: float,
    chain_minimum_visit_count: int,
) -> AnchorConfig:
    return AnchorConfig(
        minimum_count=1,
        maximum_count=2,
        merchants_per_anchor=2,
        minimum_community_size=1,
        maximum_participation=maximum_participation,
        chain_visit_count_quantile=chain_visit_count_quantile,
        chain_minimum_visit_count=chain_minimum_visit_count,
    )


def test_sppmi_weight_uses_degree_penalty() -> None:
    prepared = prepare_edge_candidates(
        {
            ("a", "b"): (9.0, 1.0, 3),
            ("a", "c"): (4.0, 1.0, 3),
            ("b", "c"): (4.0, 1.0, 3),
            ("a", "d"): (1.0, 1.0, 3),
        },
        _graph_config("sppmi", 0.5, 0.0),
    )

    graph_weight, source_weight, _, _, _ = prepared[("a", "b")]

    assert round(graph_weight, 6) == round(9.0 / math.sqrt(3 * 2), 6)
    assert source_weight == 9.0


def test_sparse_graph_filters_edges_by_jaccard() -> None:
    statistics = PairStatistics(
        strengths={
            ("a", "b"): 5.0,
            ("a", "c"): 5.0,
            ("b", "c"): 5.0,
            ("a", "d"): 5.0,
        },
        supports={
            ("a", "b"): 2,
            ("a", "c"): 2,
            ("b", "c"): 2,
            ("a", "d"): 2,
        },
        merchant_visit_counts={"a": 5, "b": 5, "c": 5, "d": 5},
    )

    graph = build_sparse_graph(
        statistics,
        CooccurrenceConfig(
            window_minutes=120,
            decay_tau_minutes=60.0,
            minimum_unique_users=2,
        ),
        _graph_config("transaction_count", 0.5, 0.2),
    )

    assert set(graph.edges) == {("a", "b"), ("a", "c"), ("b", "c")}
    assert graph.edges["a", "b"]["jaccard"] == 0.25


def test_high_participation_merchants_are_not_anchor_candidates() -> None:
    graph = nx.Graph()
    graph.add_edge("a", "b", weight=10.0, support=3)
    graph.add_edge("c", "d", weight=10.0, support=3)
    graph.add_edge("chain", "a", weight=1.0, support=3)
    graph.add_edge("chain", "c", weight=1.0, support=3)
    cleaning = CleaningResult(
        graph=graph,
        partition={"a": 0, "b": 0, "chain": 0, "c": 1, "d": 1},
        statuses={
            "a": "active",
            "b": "active",
            "chain": "active",
            "c": "active",
            "d": "active",
        },
        cleaning_rounds=0,
    )

    merchants = build_merchant_results(
        cleaning,
        PairStatistics(
            strengths={},
            supports={},
            merchant_visit_counts={"a": 5, "b": 5, "chain": 10, "c": 5, "d": 5},
        ),
        _anchor_config(0.3, 1.0, 100),
        {},
        set(),
        100,
        "test",
    )
    chain_rows = merchants.loc[merchants["merchant_id"] == "chain"]

    assert set(chain_rows["community_id"].astype(int)) == {0}
    assert int(chain_rows["is_chain_like"].max()) == 0
    assert int(chain_rows["is_multi_community_member"].max()) == 0
    assert int(chain_rows["is_anchor_candidate"].sum()) == 0


def test_high_visit_count_merchants_are_chain_like_without_community_count_gate() -> None:
    graph = nx.Graph()
    graph.add_edge("a", "b", weight=10.0, support=3)
    graph.add_edge("c", "d", weight=10.0, support=3)
    cleaning = CleaningResult(
        graph=graph,
        partition={"a": 0, "b": 0, "c": 1, "d": 1},
        statuses={
            "a": "active",
            "b": "active",
            "c": "active",
            "d": "active",
        },
        cleaning_rounds=0,
    )

    merchants = build_merchant_results(
        cleaning,
        PairStatistics(
            strengths={},
            supports={},
            merchant_visit_counts={"a": 5, "b": 5, "chain": 200, "c": 5, "d": 5},
        ),
        _anchor_config(0.3, 0.8, 100),
        {"chain": {0: 1.0}},
        {"chain"},
        100,
        "test",
    )
    chain_rows = merchants.loc[merchants["merchant_id"] == "chain"]

    assert set(chain_rows["community_id"].astype(int)) == {0}
    assert int(chain_rows["is_chain_like"].max()) == 1
    assert set(chain_rows["chain_reason"].astype(str)) == {"visit_count"}
    assert int(chain_rows["is_anchor_candidate"].sum()) == 0


def test_high_visit_count_merchants_use_candidate_edges_for_multi_community_membership() -> None:
    graph = nx.Graph()
    graph.add_edge("a", "b", weight=10.0, support=3)
    graph.add_edge("c", "d", weight=10.0, support=3)
    cleaning = CleaningResult(
        graph=graph,
        partition={"a": 0, "b": 0, "c": 1, "d": 1},
        statuses={
            "a": "active",
            "b": "active",
            "c": "active",
            "d": "active",
        },
        cleaning_rounds=0,
    )

    merchants = build_merchant_results(
        cleaning,
        PairStatistics(
            strengths={},
            supports={},
            merchant_visit_counts={"a": 5, "b": 5, "chain": 200, "c": 5, "d": 5},
        ),
        _anchor_config(0.3, 0.8, 100),
        {"chain": {0: 0.9, 1: 0.1}},
        {"chain"},
        100,
        "test",
    )
    chain_rows = merchants.loc[merchants["merchant_id"] == "chain"]

    assert set(chain_rows["community_id"].astype(int)) == {0, 1}
    assert int(chain_rows["is_multi_community_member"].max()) == 1
    assert int(chain_rows["is_anchor_candidate"].sum()) == 0
