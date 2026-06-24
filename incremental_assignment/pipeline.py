from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time

import pandas as pd

from incremental_assignment.archives import (
    apply_assignments,
    build_community_archive,
    load_or_build_merchant_archive,
    write_archives,
)
from incremental_assignment.decision import decide_candidate
from incremental_assignment.experiments import ExperimentContext, append_experiment_record
from incremental_assignment.io import (
    create_run_directory,
    load_algorithm_one_outputs,
    load_candidates,
    read_csv_checked,
    write_csv,
)
from incremental_assignment.models import AppConfig, CandidateMerchant, DecisionResult, MerchantScores, RunSummary
from incremental_assignment.scoring import calculate_scores

OBSERVATION_POOL_COLUMNS = [
    "city_code",
    "merchant_id",
    "first_seen_at",
    "last_seen_at",
    "observation_days",
    "unique_user_count",
    "latest_decision",
    "top_community_id",
    "top_score",
    "reason",
]
DECISION_COLUMNS = [
    "city_code",
    "merchant_id",
    "decision",
    "assigned_community_id",
    "top_community_id",
    "second_community_id",
    "top_score",
    "second_score",
    "score_margin",
    "graph_score",
    "geo_score",
    "customer_score",
    "reason",
    "observation_days",
    "unique_user_count",
]
MANUAL_REVIEW_COLUMNS = [
    "city_code",
    "merchant_id",
    "candidate_communities",
    "top_community_id",
    "second_community_id",
    "top_score",
    "second_score",
    "score_margin",
    "graph_score",
    "geo_score",
    "customer_score",
    "trigger_reason",
    "observation_days",
    "unique_user_count",
]


def _decision_rows(decisions: list[DecisionResult]) -> list[dict[str, object]]:
    return [
        {
            "city_code": decision.city_code,
            "merchant_id": decision.merchant_id,
            "decision": decision.decision,
            "assigned_community_id": "" if decision.assigned_community_id is None else decision.assigned_community_id,
            "top_community_id": "" if decision.top_community_id is None else decision.top_community_id,
            "second_community_id": "" if decision.second_community_id is None else decision.second_community_id,
            "top_score": decision.top_score,
            "second_score": decision.second_score,
            "score_margin": decision.score_margin,
            "graph_score": decision.graph_score,
            "geo_score": decision.geo_score,
            "customer_score": decision.customer_score,
            "reason": decision.reason,
            "observation_days": decision.observation_days,
            "unique_user_count": decision.unique_user_count,
        }
        for decision in decisions
    ]


def _manual_review_rows(decisions: list[DecisionResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for decision in decisions:
        if decision.decision != "manual_review":
            continue
        communities = [
            str(community_id)
            for community_id in [decision.top_community_id, decision.second_community_id]
            if community_id is not None
        ]
        rows.append(
            {
                "city_code": decision.city_code,
                "merchant_id": decision.merchant_id,
                "candidate_communities": "|".join(communities),
                "top_community_id": "" if decision.top_community_id is None else decision.top_community_id,
                "second_community_id": "" if decision.second_community_id is None else decision.second_community_id,
                "top_score": decision.top_score,
                "second_score": decision.second_score,
                "score_margin": decision.score_margin,
                "graph_score": decision.graph_score,
                "geo_score": decision.geo_score,
                "customer_score": decision.customer_score,
                "trigger_reason": decision.reason,
                "observation_days": decision.observation_days,
                "unique_user_count": decision.unique_user_count,
            }
        )
    return rows


def _candidate_by_key(
    candidates: list[CandidateMerchant],
) -> dict[tuple[str, str], CandidateMerchant]:
    return {
        (candidate.city_code, candidate.merchant_id): candidate
        for candidate in candidates
    }


def _load_observation_pool(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=OBSERVATION_POOL_COLUMNS)
    return read_csv_checked(path, set(OBSERVATION_POOL_COLUMNS), "观察池")


def _pool_rows(
    candidates: list[CandidateMerchant],
    decisions: list[DecisionResult],
) -> list[dict[str, object]]:
    candidates_by_key = _candidate_by_key(candidates)
    rows: list[dict[str, object]] = []
    for decision in decisions:
        candidate = candidates_by_key[(decision.city_code, decision.merchant_id)]
        rows.append(
            {
                "city_code": decision.city_code,
                "merchant_id": decision.merchant_id,
                "first_seen_at": candidate.first_seen_at.isoformat(timespec="seconds"),
                "last_seen_at": candidate.last_seen_at.isoformat(timespec="seconds"),
                "observation_days": decision.observation_days,
                "unique_user_count": decision.unique_user_count,
                "latest_decision": decision.decision,
                "top_community_id": "" if decision.top_community_id is None else decision.top_community_id,
                "top_score": decision.top_score,
                "reason": decision.reason,
            }
        )
    return rows


def _update_observation_pool(
    existing_pool: pd.DataFrame,
    candidates: list[CandidateMerchant],
    decisions: list[DecisionResult],
) -> pd.DataFrame:
    if existing_pool.empty:
        return pd.DataFrame(_pool_rows(candidates, decisions), columns=OBSERVATION_POOL_COLUMNS)
    batch_keys = {
        (candidate.city_code, candidate.merchant_id)
        for candidate in candidates
    }
    kept = existing_pool.loc[
        ~existing_pool.apply(
            lambda row: (str(row["city_code"]), str(row["merchant_id"])) in batch_keys,
            axis=1,
        )
    ]
    updated = pd.concat(
        [kept, pd.DataFrame(_pool_rows(candidates, decisions))],
        ignore_index=True,
    )
    return updated[OBSERVATION_POOL_COLUMNS]


def _score_or_none(
    candidate: CandidateMerchant,
    edges: pd.DataFrame,
    merchant_archive: pd.DataFrame,
    community_archive: pd.DataFrame,
    config: AppConfig,
) -> MerchantScores | None:
    days = (candidate.last_seen_at.date() - candidate.first_seen_at.date()).days + 1
    if days < config.assignment.min_observation_days:
        return None
    if candidate.unique_user_count < config.assignment.min_unique_users:
        return None
    return calculate_scores(
        candidate,
        edges,
        merchant_archive,
        community_archive,
        config.assignment,
    )


def run_incremental_assignment(config: AppConfig) -> RunSummary:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    run_directory = create_run_directory(config.paths.output_directory, started_at)
    algorithm_merchants, _, edges, algorithm_summary = load_algorithm_one_outputs(
        config.paths.algorithm_one_directory,
    )
    candidates = load_candidates(config.paths.candidate_merchants_path)
    merchant_archive = load_or_build_merchant_archive(
        config.paths.merchant_archive_path,
        algorithm_merchants,
        config.paths.coordinates_path,
    )
    community_archive = build_community_archive(
        merchant_archive,
        config.assignment.minimum_sigma_meters,
    )

    decisions: list[DecisionResult] = []
    for candidate in candidates:
        scores = _score_or_none(
            candidate,
            edges,
            merchant_archive,
            community_archive,
            config,
        )
        decisions.append(decide_candidate(candidate, scores, config.assignment))

    candidates_by_key = _candidate_by_key(candidates)
    updated_merchants = apply_assignments(merchant_archive, decisions, candidates_by_key)
    updated_communities = build_community_archive(
        updated_merchants,
        config.assignment.minimum_sigma_meters,
    )
    existing_pool = _load_observation_pool(config.paths.observation_pool_path)
    updated_pool = _update_observation_pool(existing_pool, candidates, decisions)

    decisions_frame = pd.DataFrame(_decision_rows(decisions), columns=DECISION_COLUMNS)
    manual_frame = pd.DataFrame(_manual_review_rows(decisions), columns=MANUAL_REVIEW_COLUMNS)
    write_csv(decisions_frame, run_directory / "decisions.csv")
    write_csv(manual_frame, run_directory / "manual_review.csv")
    write_csv(updated_pool, config.paths.observation_pool_path)
    write_csv(updated_pool, run_directory / "observation_pool.csv")
    write_archives(
        updated_merchants,
        updated_communities,
        config.paths.merchant_archive_path,
        config.paths.community_archive_path,
        run_directory,
    )

    duration = time.perf_counter() - started
    summary = RunSummary(
        output_directory=str(run_directory),
        candidate_count=len(candidates),
        assigned_count=sum(1 for decision in decisions if decision.decision == "assigned"),
        observation_count=sum(1 for decision in decisions if decision.decision == "observe"),
        manual_review_count=sum(1 for decision in decisions if decision.decision == "manual_review"),
        duration_seconds=duration,
    )
    with (run_directory / "summary.json").open("w", encoding="utf-8") as file:
        json.dump(
            {
                "candidate_count": summary.candidate_count,
                "assigned_count": summary.assigned_count,
                "observation_count": summary.observation_count,
                "manual_review_count": summary.manual_review_count,
                "duration_seconds": summary.duration_seconds,
                "algorithm_one_directory": str(config.paths.algorithm_one_directory),
                "candidate_merchants_path": str(config.paths.candidate_merchants_path),
            },
            file,
            ensure_ascii=False,
            indent=2,
        )
    append_experiment_record(
        config.paths.experiments_path,
        config,
        summary,
        ExperimentContext(
            started_at=started_at,
            algorithm_one_summary=algorithm_summary,
            output_directory=run_directory,
        ),
    )
    return summary
