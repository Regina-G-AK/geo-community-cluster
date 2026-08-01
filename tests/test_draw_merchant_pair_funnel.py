from __future__ import annotations

import pytest

from draw_merchant_pair_funnel import (
    FunnelMetric,
    METRICS,
    calculate_indicators,
)


def test_calculate_indicators_returns_expected_rates() -> None:
    pair_indicators = calculate_indicators(METRICS[0])
    merchant_indicators = calculate_indicators(METRICS[1])
    cluster_indicators = calculate_indicators(METRICS[2])

    assert pair_indicators.retention_pct == pytest.approx(6.446_045)
    assert pair_indicators.reduction_count == 1_402_095
    assert merchant_indicators.retention_pct == pytest.approx(17.858_498)
    assert merchant_indicators.reduction_count == 378_752
    assert cluster_indicators.retention_pct == pytest.approx(11.478_930)
    assert cluster_indicators.reduction_count == 408_168


def test_calculate_indicators_rejects_non_funnel_values() -> None:
    metric = FunnelMetric(
        title="无效漏斗",
        source_label="首层",
        source_value=10,
        target_label="目标层",
        target_value=11,
        unit="户",
        gradient_id="invalid",
        start_color="#000000",
        end_color="#000000",
    )

    with pytest.raises(ValueError, match="目标数量不能大于首层数量"):
        calculate_indicators(metric)
