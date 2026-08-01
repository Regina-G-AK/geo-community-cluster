from __future__ import annotations

import pytest

from scripts.draw_merchant_pair_sankey import (
    DATA,
    SankeyInput,
    calculate_indicators,
)


def test_calculate_indicators_preserves_sankey_flows() -> None:
    indicators = calculate_indicators(DATA)

    assert (
        DATA.offline_merchant_count
        + indicators.non_offline_merchant_count
        == DATA.total_merchant_count
    )
    assert (
        DATA.offline_non_individual_merchant_count
        + indicators.individual_merchant_count
        == DATA.offline_merchant_count
    )
    assert (
        DATA.paired_merchant_count
        + indicators.unpaired_merchant_count
        == DATA.offline_non_individual_merchant_count
    )
    assert (
        DATA.effective_merchant_count
        + indicators.low_support_merchant_count
        == DATA.paired_merchant_count
    )
    assert (
        DATA.circle_capable_merchant_count
        + indicators.effective_without_circle_count
        == DATA.effective_merchant_count
    )
    assert (
        DATA.geographic_assigned_merchant_count
        + DATA.pair_assigned_merchant_count
        == DATA.circle_capable_merchant_count
    )
    assert (
        DATA.effective_pair_count
        + indicators.ineffective_pair_count
        == DATA.pair_count
    )


def test_calculate_indicators_matches_expected_rates() -> None:
    indicators = calculate_indicators(DATA)

    assert indicators.offline_merchant_pct == pytest.approx(70.216_372)
    assert (
        indicators.non_individual_merchant_pct
        == pytest.approx(56.479_307)
    )
    assert indicators.paired_merchant_pct == pytest.approx(16.862_791)
    assert indicators.effective_merchant_pct == pytest.approx(17.858_498)
    assert (
        indicators.circle_capable_of_effective_pct
        == pytest.approx(66.583_278)
    )
    assert (
        indicators.circle_capable_of_all_pct
        == pytest.approx(2.005_116)
    )
    assert (
        indicators.geographic_of_circle_pct
        == pytest.approx(79.464_872)
    )
    assert indicators.pair_of_circle_pct == pytest.approx(20.535_128)
    assert indicators.offline_transaction_pct == pytest.approx(19.165_644)
    assert (
        indicators.non_individual_transaction_pct
        == pytest.approx(13.907_975)
    )
    assert (
        indicators.matched_circle_transaction_pct
        == pytest.approx(4.926_380)
    )
    assert (
        indicators.filtered_circle_transaction_pct
        == pytest.approx(2.662_577)
    )
    assert (
        indicators.final_circle_transaction_pct
        == pytest.approx(2.263_804)
    )
    assert indicators.effective_pair_pct == pytest.approx(6.446_045)


def test_calculate_indicators_rejects_non_nested_merchant_counts() -> None:
    data = DATA._replace(
        effective_merchant_count=60_000,
        circle_capable_merchant_count=70_000,
        geographic_assigned_merchant_count=50_000,
        pair_assigned_merchant_count=20_000,
    )

    with pytest.raises(ValueError, match="商户桑基流各阶段数量必须依次不增加"):
        calculate_indicators(data)


def test_calculate_indicators_rejects_incomplete_assignment_split() -> None:
    data = DATA._replace(pair_assigned_merchant_count=11_258)

    with pytest.raises(
        ValueError,
        match="能够形成商圈的商户必须完整拆分",
    ):
        calculate_indicators(data)


def test_calculate_indicators_rejects_non_nested_transaction_counts() -> None:
    data = SankeyInput(
        total_merchant_count=100,
        offline_merchant_count=90,
        offline_non_individual_merchant_count=80,
        paired_merchant_count=70,
        effective_merchant_count=60,
        circle_capable_merchant_count=50,
        geographic_assigned_merchant_count=40,
        pair_assigned_merchant_count=10,
        total_transaction_count=100,
        offline_transaction_count=80,
        non_individual_transaction_count=90,
        matched_circle_transaction_count=50,
        filtered_circle_transaction_count=40,
        final_circle_transaction_count=30,
        pair_count=50,
        effective_pair_count=10,
        support_threshold=2,
    )

    with pytest.raises(ValueError, match="交易桑基流各阶段数量必须依次不增加"):
        calculate_indicators(data)
