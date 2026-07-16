from __future__ import annotations

import pytest

from business_district.resource_usage import (
    capture_resource_usage,
    format_resource_usage_lines,
    record_resource_phase,
    start_resource_tracking,
    stop_resource_tracking,
)


def test_resource_usage_captures_elapsed_time_and_memory() -> None:
    started_at = start_resource_tracking()
    values: list[str] = []
    try:
        values = ["x" * 1024 for _ in range(100)]
        record_resource_phase("构造测试数据")
        usage = capture_resource_usage(started_at)
    finally:
        stop_resource_tracking()

    assert values
    assert usage.elapsed_seconds >= 0.0
    assert usage.python_memory_current_mb >= 0.0
    assert usage.python_memory_peak_mb >= usage.python_memory_current_mb
    assert usage.python_memory_peak_phase in {"构造测试数据", "资源监测结束"}
    assert any(
        line.startswith("python_memory_peak_phase=")
        for line in format_resource_usage_lines(usage)
    )


def test_capture_resource_usage_requires_start() -> None:
    with pytest.raises(RuntimeError, match="资源监测尚未启动"):
        capture_resource_usage(0.0)
