from __future__ import annotations

import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResourceUsage:
    elapsed_seconds: float
    python_memory_current_mb: float
    python_memory_peak_mb: float
    python_memory_peak_phase: str
    process_memory_current_mb: float | None
    process_memory_peak_mb: float | None
    process_memory_peak_phase: str | None


@dataclass(frozen=True)
class ResourceSample:
    phase: str
    elapsed_seconds: float
    python_memory_current_mb: float
    python_memory_peak_mb: float
    process_memory_current_mb: float | None


@dataclass(frozen=True)
class ProcessMemoryUsage:
    current_mb: float | None


_RESOURCE_SAMPLES: list[ResourceSample] | None = None
_RESOURCE_STARTED_AT: float | None = None


def _bytes_to_mb(value: int) -> float:
    return value / 1024 / 1024


def _kilobytes_to_mb(value: int) -> float:
    return value / 1024


def _parse_linux_memory_value(line: str) -> int:
    parts = line.split()
    if len(parts) < 2:
        raise RuntimeError(f"Linux 内存状态行格式错误: line={line!r}")
    return int(parts[1])


def _read_linux_process_memory(status_path: Path) -> ProcessMemoryUsage:
    current_kb: int | None = None
    for line in status_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            current_kb = _parse_linux_memory_value(line)
    current_mb = None if current_kb is None else _kilobytes_to_mb(current_kb)
    return ProcessMemoryUsage(current_mb=current_mb)


def _read_process_memory_usage() -> ProcessMemoryUsage:
    status_path = Path("/proc/self/status")
    if status_path.exists():
        return _read_linux_process_memory(status_path)
    return ProcessMemoryUsage(current_mb=None)


def _format_optional_mb(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.2f}"


def _capture_resource_sample(started_at: float, phase: str) -> ResourceSample:
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    process_memory = _read_process_memory_usage()
    return ResourceSample(
        phase=phase,
        elapsed_seconds=time.perf_counter() - started_at,
        python_memory_current_mb=_bytes_to_mb(current_bytes),
        python_memory_peak_mb=_bytes_to_mb(peak_bytes),
        process_memory_current_mb=process_memory.current_mb,
    )


def _peak_python_sample(samples: list[ResourceSample]) -> ResourceSample:
    return max(samples, key=lambda sample: sample.python_memory_peak_mb)


def _peak_process_sample(samples: list[ResourceSample]) -> ResourceSample | None:
    available_samples = [
        sample
        for sample in samples
        if sample.process_memory_current_mb is not None
    ]
    if not available_samples:
        return None
    return max(
        available_samples,
        key=lambda sample: float(sample.process_memory_current_mb),
    )


def format_resource_usage_lines(resource_usage: ResourceUsage) -> list[str]:
    return [
        f"elapsed_seconds={resource_usage.elapsed_seconds:.2f}",
        f"python_memory_current_mb={resource_usage.python_memory_current_mb:.2f}",
        f"python_memory_peak_mb={resource_usage.python_memory_peak_mb:.2f}",
        f"python_memory_peak_phase={resource_usage.python_memory_peak_phase}",
        (
            "process_memory_current_mb="
            f"{_format_optional_mb(resource_usage.process_memory_current_mb)}"
        ),
        (
            "process_memory_peak_mb="
            f"{_format_optional_mb(resource_usage.process_memory_peak_mb)}"
        ),
        (
            "process_memory_peak_phase="
            f"{resource_usage.process_memory_peak_phase or 'unavailable'}"
        ),
    ]


def print_resource_usage(resource_usage: ResourceUsage) -> None:
    for line in format_resource_usage_lines(resource_usage):
        print(line)


def start_resource_tracking() -> float:
    global _RESOURCE_SAMPLES
    global _RESOURCE_STARTED_AT
    if tracemalloc.is_tracing():
        tracemalloc.reset_peak()
    else:
        tracemalloc.start()
    started_at = time.perf_counter()
    _RESOURCE_STARTED_AT = started_at
    _RESOURCE_SAMPLES = [
        _capture_resource_sample(started_at, "资源监测启动")
    ]
    return started_at


def record_resource_phase(phase: str) -> None:
    if (
        not tracemalloc.is_tracing()
        or _RESOURCE_SAMPLES is None
        or _RESOURCE_STARTED_AT is None
    ):
        return
    phase_name = phase.strip()
    if not phase_name:
        raise RuntimeError("资源监测阶段名称不能为空")
    _RESOURCE_SAMPLES.append(_capture_resource_sample(_RESOURCE_STARTED_AT, phase_name))


def capture_resource_usage(started_at: float) -> ResourceUsage:
    if not tracemalloc.is_tracing():
        raise RuntimeError("资源监测尚未启动，请先调用 start_resource_tracking")
    final_sample = _capture_resource_sample(started_at, "资源监测结束")
    samples = [*(_RESOURCE_SAMPLES or []), final_sample]
    python_peak_sample = _peak_python_sample(samples)
    process_peak_sample = _peak_process_sample(samples)
    return ResourceUsage(
        elapsed_seconds=final_sample.elapsed_seconds,
        python_memory_current_mb=final_sample.python_memory_current_mb,
        python_memory_peak_mb=final_sample.python_memory_peak_mb,
        python_memory_peak_phase=python_peak_sample.phase,
        process_memory_current_mb=final_sample.process_memory_current_mb,
        process_memory_peak_mb=(
            None
            if process_peak_sample is None
            else process_peak_sample.process_memory_current_mb
        ),
        process_memory_peak_phase=(
            None if process_peak_sample is None else process_peak_sample.phase
        ),
    )


def stop_resource_tracking() -> None:
    global _RESOURCE_SAMPLES
    global _RESOURCE_STARTED_AT
    if not tracemalloc.is_tracing():
        raise RuntimeError("资源监测尚未启动，无法停止")
    tracemalloc.stop()
    _RESOURCE_SAMPLES = None
    _RESOURCE_STARTED_AT = None
