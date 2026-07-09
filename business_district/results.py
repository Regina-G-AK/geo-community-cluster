from __future__ import annotations

import math
from datetime import datetime

import networkx as nx
import pandas as pd

from business_district.community import (
    CleaningResult,
    calculate_community_weight_shares,
    calculate_participation,
)
from business_district.config import AnchorConfig
from business_district.graph import PairStatistics
from business_district.transactions import (
    DT,
    MERCHANT,
    REGION,
    SOURCE_MERCHANT,
    TIMESTAMP,
)

COMMUNITY_COLUMNS = [
    "city_code",
    "community_id",
    "merchant_count",
    "anchor_count",
    "anchor_merchants",
    "peak_hour",
    "hourly_consistency",
]


def _anchor_count(community_size: int, config: AnchorConfig) -> int:
    scaled = math.ceil(community_size / config.merchants_per_anchor)
    return min(
        community_size,
        max(config.minimum_count, min(scaled, config.maximum_count)),
    )


def _membership_community_ids(
    merchant_id: str,
    primary_community_id: int,
    is_chain_like: bool,
    community_shares: dict[str, dict[int, float]],
    candidate_community_shares: dict[str, dict[int, float]],
) -> tuple[int, ...]:
    shares = (
        candidate_community_shares.get(
            merchant_id,
            community_shares.get(merchant_id, {}),
        )
        if is_chain_like
        else community_shares.get(merchant_id, {})
    )
    linked_community_ids = {
        community_id
        for community_id, share in shares.items()
        if share > 0.0
    }
    linked_community_ids.add(primary_community_id)
    return tuple(sorted(linked_community_ids))


def _expand_multi_community_memberships(
    merchants: pd.DataFrame,
    community_shares: dict[str, dict[int, float]],
    candidate_community_shares: dict[str, dict[int, float]],
) -> pd.DataFrame:
    if merchants.empty:
        return merchants.assign(
            primary_community_id=pd.Series(dtype="int64"),
            community_share=pd.Series(dtype="float64"),
            is_primary_community=pd.Series(dtype="int64"),
            is_multi_community_member=pd.Series(dtype="int64"),
        )

    rows: list[dict[str, str | int | float]] = []
    for row in merchants.to_dict("records"):
        merchant_id = str(row["merchant_id"])
        primary_community_id = int(row["community_id"])
        is_chain_like = bool(row["is_chain_like"])
        community_ids = _membership_community_ids(
            merchant_id,
            primary_community_id,
            is_chain_like,
            community_shares,
            candidate_community_shares,
        )
        is_multi_community_member = int(len(community_ids) > 1)
        shares = (
            candidate_community_shares.get(merchant_id, {})
            if is_chain_like
            else community_shares.get(merchant_id, {})
        )
        for community_id in community_ids:
            membership = dict(row)
            membership["primary_community_id"] = primary_community_id
            membership["community_id"] = int(community_id)
            membership["community_share"] = float(shares.get(community_id, 0.0))
            membership["is_primary_community"] = int(
                community_id == primary_community_id
            )
            membership["is_multi_community_member"] = is_multi_community_member
            if community_id != primary_community_id:
                membership["is_anchor_candidate"] = 0
                membership["anchor_score"] = 0.0
            rows.append(membership)
    return pd.DataFrame(rows)


def _visit_count_quantile_threshold(
    visit_counts: list[int],
    quantile: float,
) -> int:
    if not visit_counts:
        return 0
    ordered = sorted(visit_counts)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return int(ordered[index])


def calculate_chain_visit_count_threshold(
    visit_counts: list[int],
    anchor_config: AnchorConfig,
) -> int:
    quantile_threshold = _visit_count_quantile_threshold(
        visit_counts,
        anchor_config.chain_visit_count_quantile,
    )
    return max(anchor_config.chain_minimum_visit_count, quantile_threshold)


def identify_chain_like_merchants(
    merchant_visit_counts: dict[str, int],
    chain_visit_count_threshold: int,
) -> set[str]:
    return {
        merchant_id
        for merchant_id, visit_count in merchant_visit_counts.items()
        if int(visit_count) >= chain_visit_count_threshold
    }


def _connected_community_count(
    merchant_id: str,
    community_shares: dict[str, dict[int, float]],
) -> int:
    return sum(
        1
        for share in community_shares.get(merchant_id, {}).values()
        if share > 0.0
    )


def _chain_reason(
    is_chain_like: bool,
) -> str:
    if is_chain_like:
        return "visit_count"
    return ""


def _add_chain_like_flags(
    merchants: pd.DataFrame,
    community_shares: dict[str, dict[int, float]],
    chain_like_merchant_ids: set[str],
    chain_visit_count_threshold: int,
) -> pd.DataFrame:
    if merchants.empty:
        return merchants.assign(
            connected_community_count=pd.Series(dtype="int64"),
            chain_visit_count_threshold=pd.Series(dtype="int64"),
            is_chain_like=pd.Series(dtype="int64"),
            chain_reason=pd.Series(dtype="object"),
        )

    enriched = merchants.copy()
    enriched["connected_community_count"] = [
        _connected_community_count(str(merchant_id), community_shares)
        for merchant_id in enriched["merchant_id"].tolist()
    ]
    enriched["chain_visit_count_threshold"] = chain_visit_count_threshold
    is_chain_like = [
        str(merchant_id) in chain_like_merchant_ids
        for merchant_id in enriched["merchant_id"].tolist()
    ]
    enriched["is_chain_like"] = pd.Series(is_chain_like, index=enriched.index).astype(
        int
    )
    enriched["chain_reason"] = [
        _chain_reason(chain_flag)
        for chain_flag in is_chain_like
    ]
    return enriched


def _primary_community_id(shares: dict[int, float]) -> int:
    if not shares:
        return -1
    return sorted(shares.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _chain_participation(shares: dict[int, float]) -> float:
    if not shares:
        return 0.0
    return 1.0 - sum(share**2 for share in shares.values())


def _build_chain_membership_rows(
    chain_like_merchant_ids: set[str],
    statistics: PairStatistics,
    candidate_community_shares: dict[str, dict[int, float]],
    city_code: str,
    chain_visit_count_threshold: int,
) -> list[dict[str, str | int | float]]:
    rows: list[dict[str, str | int | float]] = []
    for merchant_id in sorted(chain_like_merchant_ids):
        shares = candidate_community_shares.get(merchant_id, {})
        primary_community_id = _primary_community_id(shares)
        community_ids = sorted(shares) if shares else [-1]
        is_multi_community_member = int(len(community_ids) > 1)
        connected_community_count = len(shares)
        merchant_status = (
            "active"
            if primary_community_id >= 0
            else "suspect_isolated"
        )
        for community_id in community_ids:
            rows.append(
                {
                    "city_code": city_code,
                    "merchant_id": merchant_id,
                    "primary_community_id": primary_community_id,
                    "community_id": int(community_id),
                    "merchant_status": merchant_status,
                    "is_anchor_candidate": 0,
                    "anchor_score": 0.0,
                    "pagerank": 0.0,
                    "participation": _chain_participation(shares),
                    "weighted_degree": 0.0,
                    "community_share": float(shares.get(community_id, 0.0)),
                    "is_primary_community": int(community_id == primary_community_id),
                    "is_multi_community_member": is_multi_community_member,
                    "connected_community_count": connected_community_count,
                    "chain_visit_count_threshold": chain_visit_count_threshold,
                    "is_chain_like": 1,
                    "chain_reason": "visit_count",
                    "visit_count": int(
                        statistics.merchant_visit_counts.get(merchant_id, 0)
                    ),
                }
            )
    return rows


def build_merchant_results(
    cleaning: CleaningResult,
    statistics: PairStatistics,
    anchor_config: AnchorConfig,
    candidate_community_shares: dict[str, dict[int, float]],
    chain_like_merchant_ids: set[str],
    chain_visit_count_threshold: int,
    city_code: str,
) -> pd.DataFrame:
    participation = calculate_participation(cleaning.graph, cleaning.partition)
    community_shares = calculate_community_weight_shares(
        cleaning.graph,
        cleaning.partition,
    )
    communities: dict[int, list[str]] = {}
    for merchant_id, community_id in cleaning.partition.items():
        if merchant_id in chain_like_merchant_ids:
            continue
        communities.setdefault(community_id, []).append(merchant_id)

    centrality_by_merchant: dict[str, float] = {}
    for community_nodes in communities.values():
        subgraph = cleaning.graph.subgraph(community_nodes)
        if subgraph.number_of_edges() > 0:
            centrality_by_merchant.update(nx.pagerank(subgraph, weight="weight"))
        else:
            equal_score = 1.0 / len(community_nodes)
            centrality_by_merchant.update(
                {node: equal_score for node in community_nodes}
            )

    rows: list[dict[str, str | int | float]] = []

    for merchant_id, community_id in cleaning.partition.items():
        if merchant_id in chain_like_merchant_ids:
            continue
        centrality = centrality_by_merchant[merchant_id]
        anchor_score = centrality * (
            1.0 - participation.get(merchant_id, 0.0)
        )
        rows.append(
            {
                "city_code": city_code,
                "merchant_id": merchant_id,
                "community_id": int(community_id),
                "merchant_status": cleaning.statuses[merchant_id],
                "is_anchor_candidate": 0,
                "anchor_score": float(anchor_score),
                "pagerank": float(centrality),
                "participation": float(participation.get(merchant_id, 0.0)),
                "weighted_degree": float(
                    cleaning.graph.degree(merchant_id, weight="weight")
                ),
                "visit_count": int(
                    statistics.merchant_visit_counts.get(merchant_id, 0)
                ),
            }
        )

    result = pd.DataFrame(rows)
    result = _add_chain_like_flags(
        result,
        community_shares,
        chain_like_merchant_ids,
        chain_visit_count_threshold,
    )
    if not result.empty:
        anchor_indices: list[int] = []
        for _, group in result.groupby("community_id", sort=True):
            if len(group) < anchor_config.minimum_community_size:
                continue
            count = _anchor_count(len(group), anchor_config)
            eligible = group.loc[
                (group["is_chain_like"] == 0)
                & (
                    group["participation"].astype(float)
                    <= anchor_config.maximum_participation
                )
            ]
            ordered = eligible.sort_values(
                ["anchor_score", "weighted_degree", "visit_count", "merchant_id"],
                ascending=[False, False, False, True],
            )
            anchor_indices.extend(ordered.head(count).index.tolist())
        result.loc[anchor_indices, "is_anchor_candidate"] = 1

    result = _expand_multi_community_memberships(
        result,
        community_shares,
        candidate_community_shares,
    )

    chain_rows = _build_chain_membership_rows(
        chain_like_merchant_ids,
        statistics,
        candidate_community_shares,
        city_code,
        chain_visit_count_threshold,
    )
    if chain_rows:
        result = pd.concat([result, pd.DataFrame(chain_rows)], ignore_index=True)

    removed_rows = [
        {
            "city_code": city_code,
            "merchant_id": merchant_id,
            "primary_community_id": -1,
            "community_id": -1,
            "merchant_status": status,
            "is_anchor_candidate": 0,
            "anchor_score": 0.0,
            "pagerank": 0.0,
            "participation": 0.0,
            "weighted_degree": 0.0,
            "community_share": 0.0,
            "is_primary_community": 0,
            "is_multi_community_member": 0,
            "connected_community_count": 0,
            "chain_visit_count_threshold": chain_visit_count_threshold,
            "is_chain_like": 0,
            "chain_reason": "",
            "visit_count": int(
                statistics.merchant_visit_counts.get(merchant_id, 0)
            ),
        }
        for merchant_id, status in cleaning.statuses.items()
        if status == "suspect_online" and merchant_id not in chain_like_merchant_ids
    ]
    if removed_rows:
        result = pd.concat([result, pd.DataFrame(removed_rows)], ignore_index=True)

    return result.sort_values(
        ["community_id", "merchant_status", "anchor_score", "merchant_id"],
        ascending=[True, True, False, True],
    ).reset_index(drop=True)


def filter_merchants_by_community_size(
    merchants: pd.DataFrame,
    minimum_community_size: int,
) -> pd.DataFrame:
    active = merchants.loc[merchants["community_id"] >= 0]
    community_sizes = active.groupby("community_id").size()
    kept_community_ids = set(
        community_sizes.loc[
            community_sizes >= minimum_community_size
        ].index
    )
    filtered = merchants.loc[
        merchants["community_id"].isin(kept_community_ids)
    ]
    return filtered.copy().reset_index(drop=True)


def build_business_results(
    merchants: pd.DataFrame,
    merchant_metadata: pd.DataFrame,
    update_time: datetime,
    minimum_community_size: int,
) -> pd.DataFrame:
    active = merchants.loc[merchants["community_id"] >= 0]
    community_sizes = active.groupby("community_id").size()
    valid_community_ids = set(
        community_sizes.loc[
            community_sizes >= minimum_community_size
        ].index
    )
    enriched = merchant_metadata.merge(
        merchants[
            [
                "merchant_id",
                "primary_community_id",
                "community_id",
                "merchant_status",
                "is_anchor_candidate",
                "community_share",
                "is_primary_community",
                "is_multi_community_member",
                "connected_community_count",
                "chain_visit_count_threshold",
                "is_chain_like",
                "chain_reason",
            ]
        ],
        on="merchant_id",
        how="left",
    )
    timestamp = update_time.strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, str | int | float]] = []
    output_merchant_column = (
        SOURCE_MERCHANT
        if SOURCE_MERCHANT in enriched.columns
        else MERCHANT
    )
    for row in enriched.itertuples(index=False):
        merchant_id = str(getattr(row, MERCHANT))
        raw_community_id = getattr(row, "community_id")
        raw_primary_community_id = getattr(row, "primary_community_id")
        raw_status = getattr(row, "merchant_status")
        is_chain_like = (
            int(getattr(row, "is_chain_like"))
            if pd.notna(getattr(row, "is_chain_like"))
            else 0
        )
        community_id = (
            int(raw_community_id)
            if pd.notna(raw_community_id)
            and int(raw_community_id) in valid_community_ids
            and str(raw_status) != "suspect_isolated"
            else ""
        )
        status = "normal" if community_id != "" else "suspect_isolated"
        if pd.notna(raw_status) and str(raw_status) == "suspect_online":
            status = "suspect_online"
            community_id = ""
        elif community_id != "" and is_chain_like == 1:
            status = "suspect_chain_store"
        primary_community_id = (
            int(raw_primary_community_id)
            if community_id != ""
            and pd.notna(raw_primary_community_id)
            and int(raw_primary_community_id) in valid_community_ids
            else ""
        )
        rows.append(
            {
                "storename": str(getattr(row, output_merchant_column)),
                "primary_community_id": primary_community_id,
                "community_id": community_id,
                "previous_community_id": "",
                "region": str(getattr(row, REGION)),
                "is_interfere": "N",
                "update_time": timestamp,
                "status": status,
                "is_position": (
                    int(getattr(row, "is_anchor_candidate"))
                    if community_id != ""
                    and pd.notna(getattr(row, "is_anchor_candidate"))
                    else 0
                ),
                "community_share": (
                    float(getattr(row, "community_share"))
                    if community_id != ""
                    and pd.notna(getattr(row, "community_share"))
                    else 0.0
                ),
                "is_primary_community": (
                    int(getattr(row, "is_primary_community"))
                    if community_id != ""
                    and pd.notna(getattr(row, "is_primary_community"))
                    else 0
                ),
                "is_multi_community_member": (
                    int(getattr(row, "is_multi_community_member"))
                    if community_id != ""
                    and pd.notna(getattr(row, "is_multi_community_member"))
                    else 0
                ),
                "is_chain_like": is_chain_like,
                "chain_reason": (
                    str(getattr(row, "chain_reason"))
                    if pd.notna(getattr(row, "chain_reason"))
                    else ""
                ),
                "chain_visit_count_threshold": (
                    int(getattr(row, "chain_visit_count_threshold"))
                    if pd.notna(getattr(row, "chain_visit_count_threshold"))
                    else 0
                ),
                "connected_community_count": (
                    int(getattr(row, "connected_community_count"))
                    if pd.notna(getattr(row, "connected_community_count"))
                    else 0
                ),
                "dt": str(getattr(row, DT)),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "storename",
            "primary_community_id",
            "community_id",
            "previous_community_id",
            "region",
            "is_interfere",
            "update_time",
            "status",
            "is_position",
            "community_share",
            "is_primary_community",
            "is_multi_community_member",
            "is_chain_like",
            "chain_reason",
            "chain_visit_count_threshold",
            "connected_community_count",
            "dt",
        ],
    ).sort_values(["status", "community_id", "storename"]).reset_index(drop=True)


def _hourly_consistency(
    community_merchants: set[str],
    visits: pd.DataFrame,
) -> tuple[int, float]:
    selected = visits[visits[MERCHANT].isin(community_merchants)].copy()
    hourly = pd.crosstab(selected[MERCHANT], selected[TIMESTAMP].dt.hour)
    hourly = hourly.reindex(columns=range(24), fill_value=0).astype(float)
    community_profile = hourly.sum(axis=0)
    peak_hour = int(community_profile.idxmax())
    profile_norm = float(math.sqrt((community_profile**2).sum()))
    similarities: list[float] = []
    for _, merchant_profile in hourly.iterrows():
        merchant_norm = float(math.sqrt((merchant_profile**2).sum()))
        if merchant_norm == 0 or profile_norm == 0:
            continue
        similarity = float(
            merchant_profile.dot(community_profile)
            / (merchant_norm * profile_norm)
        )
        similarities.append(similarity)
    consistency = sum(similarities) / len(similarities) if similarities else 0.0
    return peak_hour, consistency


def build_community_results(
    merchants: pd.DataFrame,
    visits: pd.DataFrame,
    city_code: str,
) -> pd.DataFrame:
    rows: list[dict[str, str | int | float]] = []
    active = merchants[merchants["community_id"] >= 0]
    for community_id, group in active.groupby("community_id", sort=True):
        merchant_ids = set(group["merchant_id"].astype(str))
        peak_hour, consistency = _hourly_consistency(merchant_ids, visits)
        anchors = group.loc[
            group["is_anchor_candidate"] == 1,
            "merchant_id",
        ].astype(str)
        rows.append(
            {
                "city_code": city_code,
                "community_id": int(community_id),
                "merchant_count": int(len(group)),
                "anchor_count": int(group["is_anchor_candidate"].sum()),
                "anchor_merchants": "|".join(anchors),
                "peak_hour": peak_hour,
                "hourly_consistency": float(consistency),
            }
        )
    return pd.DataFrame(rows, columns=COMMUNITY_COLUMNS)
