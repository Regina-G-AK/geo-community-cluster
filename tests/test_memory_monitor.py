from __future__ import annotations

import time

import pytest

from business_district.memory_monitor import (
    CodeLocation,
    MemoryMonitoringError,
    MemoryReport,
    ProcessMemoryUsage,
    ProcessMemoryMonitor,
    create_process_memory_monitor,
    format_memory_report,
    read_process_memory_usage,
)
from business_district.probes import print_probe


def test_process_memory_monitor_samples_real_process_memory() -> None:
    monitor = create_process_memory_monitor(0.01)

    monitor.start()
    allocation = bytearray(1024 * 1024)
    time.sleep(0.03)
    report = monitor.stop()

    assert allocation
    assert report.sample_count >= 3
    assert report.peak_rss_bytes > 0
    assert 0 < report.mean_rss_bytes <= report.peak_rss_bytes
    assert report.elapsed_seconds >= 0.03
    assert report.peak_location.filename
    assert report.peak_location.line_number > 0
    assert report.peak_location.function_name


def test_read_process_memory_usage_returns_current_rss() -> None:
    usage = read_process_memory_usage()

    assert isinstance(usage, ProcessMemoryUsage)
    assert usage.scope in {"main_process", "main_process_and_descendants"}
    assert usage.rss_bytes > 0


def test_print_probe_includes_stage_and_current_memory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_probe("source_ready", "row_count=10")

    output = capsys.readouterr().out
    assert output.startswith("[stage] stage=source_ready, memory_scope=")
    assert ", rss_mib=" in output
    assert output.endswith(", row_count=10\n")


def test_process_memory_monitor_rejects_repeated_start() -> None:
    monitor = ProcessMemoryMonitor(
        lambda: 1024,
        "test_process",
        1.0,
        1,
    )

    monitor.start()
    with pytest.raises(
        MemoryMonitoringError,
        match="不能重复启动",
    ):
        monitor.start()
    report = monitor.stop()

    assert report.sample_count == 2


def test_format_memory_report_includes_peak_mean_and_location() -> None:
    report = MemoryReport(
        scope="main_process_and_descendants",
        sample_interval_seconds=0.2,
        elapsed_seconds=1.25,
        sample_count=7,
        peak_rss_bytes=10 * 1024 * 1024,
        mean_rss_bytes=8.5 * 1024 * 1024,
        peak_location=CodeLocation(
            filename="/app/project/business_district/pipeline.py",
            line_number=80,
            function_name="run_algorithm_one_from_transactions",
        ),
    )

    result = format_memory_report(report)

    assert result == (
        "[memory] scope=main_process_and_descendants, "
        "sample_interval_seconds=0.200, elapsed_seconds=1.250, "
        "sample_count=7, peak_rss_mib=10.00, mean_rss_mib=8.50, "
        "peak_location=/app/project/business_district/pipeline.py:80 "
        "(run_algorithm_one_from_transactions)"
    )
