from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Callable, List, Optional, Tuple


MEBIBYTE_BYTES = 1024 * 1024
MemoryReader = Callable[[], int]


class MemoryMonitoringError(RuntimeError):
    """内存资源监测失败。"""


@dataclass(frozen=True)
class CodeLocation:
    filename: str
    line_number: int
    function_name: str


@dataclass(frozen=True)
class MemoryReport:
    scope: str
    sample_interval_seconds: float
    elapsed_seconds: float
    sample_count: int
    peak_rss_bytes: int
    mean_rss_bytes: float
    peak_location: CodeLocation


@dataclass(frozen=True)
class ProcessMemoryUsage:
    scope: str
    rss_bytes: int


def _parse_linux_statm_rss_bytes(
    statm_text: str,
    page_size_bytes: int,
    process_id: int,
) -> int:
    fields = statm_text.split()
    if len(fields) < 2:
        raise MemoryMonitoringError(
            "Linux 进程内存文件格式错误: "
            f"process_id={process_id}, content={statm_text!r}"
        )
    try:
        resident_pages = int(fields[1])
    except ValueError as error:
        raise MemoryMonitoringError(
            "Linux 进程常驻页数不是整数: "
            f"process_id={process_id}, resident_pages={fields[1]!r}"
        ) from error
    if resident_pages < 0:
        raise MemoryMonitoringError(
            "Linux 进程常驻页数不能为负数: "
            f"process_id={process_id}, resident_pages={resident_pages}"
        )
    return resident_pages * page_size_bytes


def _read_linux_process_rss_bytes(
    process_id: int,
    page_size_bytes: int,
) -> int:
    statm_path = Path("/proc") / str(process_id) / "statm"
    try:
        statm_text = statm_path.read_text(encoding="ascii")
    except OSError as error:
        raise MemoryMonitoringError(
            "Linux 进程内存文件读取失败: "
            f"process_id={process_id}, path={statm_path}, "
            f"type={type(error).__name__}, reason={error}"
        ) from error
    return _parse_linux_statm_rss_bytes(
        statm_text,
        page_size_bytes,
        process_id,
    )


def _read_linux_child_process_ids(process_id: int) -> List[int]:
    children_path = (
        Path("/proc")
        / str(process_id)
        / "task"
        / str(process_id)
        / "children"
    )
    try:
        children_text = children_path.read_text(encoding="ascii")
    except OSError as error:
        raise MemoryMonitoringError(
            "Linux 子进程列表读取失败: "
            f"process_id={process_id}, path={children_path}, "
            f"type={type(error).__name__}, reason={error}"
        ) from error
    try:
        return [int(value) for value in children_text.split()]
    except ValueError as error:
        raise MemoryMonitoringError(
            "Linux 子进程列表包含无效进程号: "
            f"process_id={process_id}, content={children_text!r}"
        ) from error


def _try_read_linux_descendant(
    process_id: int,
    page_size_bytes: int,
) -> Optional[Tuple[int, List[int]]]:
    try:
        rss_bytes = _read_linux_process_rss_bytes(
            process_id,
            page_size_bytes,
        )
        child_process_ids = _read_linux_child_process_ids(process_id)
    except MemoryMonitoringError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise
    return rss_bytes, child_process_ids


def _read_linux_process_tree_rss_bytes(process_id: int) -> int:
    try:
        page_size_bytes = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError) as error:
        raise MemoryMonitoringError(
            "Linux 内存页大小读取失败: "
            f"type={type(error).__name__}, reason={error}"
        ) from error
    root_rss_bytes = _read_linux_process_rss_bytes(
        process_id,
        page_size_bytes,
    )
    pending_process_ids = _read_linux_child_process_ids(process_id)
    visited_process_ids = {process_id}
    total_rss_bytes = root_rss_bytes
    while pending_process_ids:
        child_process_id = pending_process_ids.pop()
        if child_process_id in visited_process_ids:
            continue
        visited_process_ids.add(child_process_id)
        descendant = _try_read_linux_descendant(
            child_process_id,
            page_size_bytes,
        )
        if descendant is None:
            continue
        rss_bytes, child_process_ids = descendant
        total_rss_bytes += rss_bytes
        pending_process_ids.extend(child_process_ids)
    return total_rss_bytes


class _WindowsProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("page_fault_count", ctypes.c_ulong),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
    ]


def _read_windows_process_rss_bytes() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = ctypes.c_void_p
    get_process_memory_info = psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WindowsProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    get_process_memory_info.restype = ctypes.c_int
    counters = _WindowsProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    succeeded = get_process_memory_info(
        get_current_process(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not succeeded:
        error_code = ctypes.get_last_error()
        raise MemoryMonitoringError(
            "Windows 进程内存读取失败: "
            f"error_code={error_code}, reason={ctypes.FormatError(error_code)}"
        )
    return int(counters.working_set_size)


def _build_process_memory_reader() -> Tuple[MemoryReader, str]:
    if sys.platform.startswith("linux"):
        process_id = os.getpid()

        def read_linux_memory() -> int:
            return _read_linux_process_tree_rss_bytes(process_id)

        return read_linux_memory, "main_process_and_descendants"
    if sys.platform == "win32":
        return _read_windows_process_rss_bytes, "main_process"
    raise MemoryMonitoringError(
        "当前操作系统不支持 RSS 内存监测: "
        f"platform={sys.platform!r}, supported=['linux', 'win32']"
    )


def _validate_rss_bytes(rss_bytes: int) -> int:
    if rss_bytes < 0:
        raise MemoryMonitoringError(
            f"RSS 内存不能为负数: rss_bytes={rss_bytes}"
        )
    return rss_bytes


def read_process_memory_usage() -> ProcessMemoryUsage:
    memory_reader, scope = _build_process_memory_reader()
    return ProcessMemoryUsage(
        scope=scope,
        rss_bytes=_validate_rss_bytes(memory_reader()),
    )


def _build_code_location(frame: FrameType) -> CodeLocation:
    return CodeLocation(
        filename=str(Path(frame.f_code.co_filename).resolve()),
        line_number=frame.f_lineno,
        function_name=frame.f_code.co_name,
    )


def _read_thread_location(thread_id: int) -> CodeLocation:
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        raise MemoryMonitoringError(
            f"目标执行线程不存在: thread_id={thread_id}"
        )
    return _build_code_location(frame)


class ProcessMemoryMonitor:
    """周期采样进程 RSS 并记录峰值执行位置。"""

    def __init__(
        self,
        memory_reader: MemoryReader,
        scope: str,
        sample_interval_seconds: float,
        target_thread_id: int,
    ) -> None:
        if sample_interval_seconds <= 0:
            raise ValueError(
                "内存采样间隔必须大于 0: "
                f"sample_interval_seconds={sample_interval_seconds}"
            )
        self._memory_reader = memory_reader
        self._scope = scope
        self._sample_interval_seconds = sample_interval_seconds
        self._target_thread_id = target_thread_id
        self._stop_event = threading.Event()
        self._sampling_thread: Optional[threading.Thread] = None
        self._sampling_error: Optional[Exception] = None
        self._started_at: Optional[float] = None
        self._sample_count = 0
        self._rss_bytes_sum = 0
        self._peak_rss_bytes = -1
        self._peak_location: Optional[CodeLocation] = None

    def _record_sample(self, location: CodeLocation) -> None:
        rss_bytes = _validate_rss_bytes(self._memory_reader())
        self._sample_count += 1
        self._rss_bytes_sum += rss_bytes
        if rss_bytes > self._peak_rss_bytes:
            self._peak_rss_bytes = rss_bytes
            self._peak_location = location

    def _sample_until_stopped(self) -> None:
        try:
            while not self._stop_event.wait(self._sample_interval_seconds):
                location = _read_thread_location(self._target_thread_id)
                self._record_sample(location)
        except Exception as error:
            self._sampling_error = error
            self._stop_event.set()

    def start(self) -> None:
        if self._sampling_thread is not None:
            raise MemoryMonitoringError("同一个内存监测器不能重复启动")
        caller_frame = sys._getframe(1)
        self._started_at = time.monotonic()
        self._record_sample(_build_code_location(caller_frame))
        sampling_thread = threading.Thread(
            target=self._sample_until_stopped,
            name="process-memory-monitor",
        )
        sampling_thread.daemon = True
        self._sampling_thread = sampling_thread
        sampling_thread.start()

    def stop(self) -> MemoryReport:
        if self._sampling_thread is None or self._started_at is None:
            raise MemoryMonitoringError("内存监测器尚未启动")
        self._stop_event.set()
        self._sampling_thread.join()
        if self._sampling_error is not None:
            raise MemoryMonitoringError(
                "内存采样线程执行失败: "
                f"type={type(self._sampling_error).__name__}, "
                f"reason={self._sampling_error}"
            ) from self._sampling_error
        caller_frame = sys._getframe(1)
        self._record_sample(_build_code_location(caller_frame))
        elapsed_seconds = time.monotonic() - self._started_at
        if self._peak_location is None or self._sample_count < 1:
            raise MemoryMonitoringError("内存监测结束时没有任何有效采样")
        return MemoryReport(
            scope=self._scope,
            sample_interval_seconds=self._sample_interval_seconds,
            elapsed_seconds=elapsed_seconds,
            sample_count=self._sample_count,
            peak_rss_bytes=self._peak_rss_bytes,
            mean_rss_bytes=self._rss_bytes_sum / self._sample_count,
            peak_location=self._peak_location,
        )


def create_process_memory_monitor(
    sample_interval_seconds: float,
) -> ProcessMemoryMonitor:
    memory_reader, scope = _build_process_memory_reader()
    return ProcessMemoryMonitor(
        memory_reader,
        scope,
        sample_interval_seconds,
        threading.get_ident(),
    )


def format_memory_report(report: MemoryReport) -> str:
    peak_rss_mib = report.peak_rss_bytes / MEBIBYTE_BYTES
    mean_rss_mib = report.mean_rss_bytes / MEBIBYTE_BYTES
    location = report.peak_location
    return (
        "[memory] "
        f"scope={report.scope}, "
        f"sample_interval_seconds={report.sample_interval_seconds:.3f}, "
        f"elapsed_seconds={report.elapsed_seconds:.3f}, "
        f"sample_count={report.sample_count}, "
        f"peak_rss_mib={peak_rss_mib:.2f}, "
        f"mean_rss_mib={mean_rss_mib:.2f}, "
        f"peak_location={location.filename}:{location.line_number} "
        f"({location.function_name})"
    )
