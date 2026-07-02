from __future__ import annotations

from incremental_assignment.models import AssignmentConfig, CandidateMerchant, DecisionResult, MerchantScores


def observation_days(candidate: CandidateMerchant) -> int:
    return (candidate.last_seen_at.date() - candidate.first_seen_at.date()).days + 1


def _top_two(scores: dict[int, float]) -> tuple[int | None, int | None, float, float]:
    if not scores:
        return None, None, 0.0, 0.0
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top_community_id, top_score = ordered[0]
    if len(ordered) == 1:
        return top_community_id, None, top_score, 0.0
    second_community_id, second_score = ordered[1]
    return top_community_id, second_community_id, top_score, second_score


def decide_candidate(
    candidate: CandidateMerchant,
    scores: MerchantScores | None,
    config: AssignmentConfig,
) -> DecisionResult:
    days = observation_days(candidate)
    if days < config.min_observation_days:
        return DecisionResult(
            city_code=candidate.city_code,
            merchant_id=candidate.merchant_id,
            decision="observe",
            assigned_community_id=None,
            top_community_id=None,
            second_community_id=None,
            top_score=0.0,
            second_score=0.0,
            score_margin=0.0,
            graph_score=0.0,
            geo_score=0.0,
            customer_score=0.0,
            reason="观察天数不足",
            observation_days=days,
            unique_user_count=candidate.unique_user_count,
        )
    if candidate.unique_user_count < config.min_unique_users:
        return DecisionResult(
            city_code=candidate.city_code,
            merchant_id=candidate.merchant_id,
            decision="observe",
            assigned_community_id=None,
            top_community_id=None,
            second_community_id=None,
            top_score=0.0,
            second_score=0.0,
            score_margin=0.0,
            graph_score=0.0,
            geo_score=0.0,
            customer_score=0.0,
            reason="去重持卡人数不足",
            observation_days=days,
            unique_user_count=candidate.unique_user_count,
        )
    if scores is None:
        raise ValueError(f"证据达标商户必须提供分数: merchant_id={candidate.merchant_id}")

    top_id, second_id, top_score, second_score = _top_two(scores.final_scores)
    margin = top_score - second_score
    graph_score = scores.graph_scores.get(top_id, 0.0) if top_id is not None else 0.0
    geo_score = scores.geo_scores.get(top_id, 0.0) if top_id is not None else 0.0
    customer_score = scores.customer_scores.get(top_id, 0.0) if top_id is not None else 0.0
    if (
        scores.geo_available
        and scores.graph_best_community_id is not None
        and scores.geo_best_community_id is not None
        and scores.graph_best_community_id != scores.geo_best_community_id
    ):
        return DecisionResult(
            city_code=candidate.city_code,
            merchant_id=candidate.merchant_id,
            decision="manual_review",
            assigned_community_id=None,
            top_community_id=top_id,
            second_community_id=second_id,
            top_score=top_score,
            second_score=second_score,
            score_margin=margin,
            graph_score=graph_score,
            geo_score=geo_score,
            customer_score=customer_score,
            reason="图分最高商圈与地理分最高商圈不同",
            observation_days=days,
            unique_user_count=candidate.unique_user_count,
        )
    if top_score < config.theta:
        return DecisionResult(
            city_code=candidate.city_code,
            merchant_id=candidate.merchant_id,
            decision="observe",
            assigned_community_id=None,
            top_community_id=top_id,
            second_community_id=second_id,
            top_score=top_score,
            second_score=second_score,
            score_margin=margin,
            graph_score=graph_score,
            geo_score=geo_score,
            customer_score=customer_score,
            reason="最高融合分数低于归入阈值",
            observation_days=days,
            unique_user_count=candidate.unique_user_count,
        )
    if margin < config.delta:
        return DecisionResult(
            city_code=candidate.city_code,
            merchant_id=candidate.merchant_id,
            decision="manual_review",
            assigned_community_id=None,
            top_community_id=top_id,
            second_community_id=second_id,
            top_score=top_score,
            second_score=second_score,
            score_margin=margin,
            graph_score=graph_score,
            geo_score=geo_score,
            customer_score=customer_score,
            reason="最高分与次高分差值不足",
            observation_days=days,
            unique_user_count=candidate.unique_user_count,
        )
    return DecisionResult(
        city_code=candidate.city_code,
        merchant_id=candidate.merchant_id,
        decision="assigned",
        assigned_community_id=top_id,
        top_community_id=top_id,
        second_community_id=second_id,
        top_score=top_score,
        second_score=second_score,
        score_margin=margin,
        graph_score=graph_score,
        geo_score=geo_score,
        customer_score=customer_score,
        reason="满足自动归入阈值和差值阈值",
        observation_days=days,
        unique_user_count=candidate.unique_user_count,
    )

