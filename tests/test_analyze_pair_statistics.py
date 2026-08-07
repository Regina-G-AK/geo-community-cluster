from __future__ import annotations

import json
import os
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

from business_district.graph import PairStatistics


def _write_platform_stub(directory: Path) -> None:
    (directory / "spdbccc_data.py").write_text(
        """from pathlib import Path
from types import SimpleNamespace


EVENT_PATH = Path("events.txt")


def record(value):
    with EVENT_PATH.open("a", encoding="utf-8") as file:
        file.write(value + "\\n")


mountCheck = SimpleNamespace(mount_check=lambda: record("mount"))
loging = SimpleNamespace(log_data=lambda message: record("log:" + message))
formattedExc = SimpleNamespace(formatted_exc=lambda: record("formatted"))
task = SimpleNamespace(finish_task=lambda: record("finish"))
""",
        encoding="utf-8",
    )


def _script_environment(directory: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(directory)
    return environment


def test_analyze_pair_statistics_script_runs_platform_lifecycle_and_prints_report(
    tmp_path: Path,
) -> None:
    pair_directory = tmp_path / "code"
    pair_directory.mkdir()
    input_path = pair_directory / "pair_statistics_shanghai.pkl"
    statistics = PairStatistics(
        strengths={
            ("a", "b"): 4.0,
            ("a", "c"): 1.0,
            ("b", "c"): 2.0,
            ("c", "d"): 0.5,
        },
        supports={
            ("a", "b"): 4,
            ("a", "c"): 1,
            ("b", "c"): 3,
            ("c", "d"): 1,
        },
        merchant_visit_counts={
            "a": 10,
            "b": 8,
            "c": 5,
            "d": 1,
            "e": 1,
        },
    )
    with input_path.open("wb") as file:
        pickle.dump(statistics, file, protocol=pickle.HIGHEST_PROTOCOL)

    _write_platform_stub(tmp_path)
    script_directory = tmp_path / "scripts"
    script_directory.mkdir()
    source_script_path = (
        Path(__file__).parents[1] / "scripts" / "analyze_pair_statistics.py"
    )
    script_path = script_directory / "analyze_pair_statistics.py"
    shutil.copyfile(source_script_path, script_path)
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_script_environment(tmp_path),
    )

    summary = json.loads(completed.stdout)
    assert summary["filter"]["pairs"]["pair_count"] == 4
    assert summary["filter"]["support"]["pair_count"] == 2
    assert summary["visit_count_chain_removal"]["removed_merchant_count"] == 0
    top_visit_merchants = {
        row["merchant_id"]: row
        for row in summary["top_merchants"]["by_visit_count"]
    }
    assert set(top_visit_merchants) == {"a", "b", "c", "d", "e"}
    assert top_visit_merchants["a"]["raw_degree"] == 2
    assert top_visit_merchants["e"]["raw_degree"] == 0
    assert top_visit_merchants["e"]["is_graph_isolated"] == 1
    assert not (tmp_path / "summary.json").exists()
    events = (tmp_path / "events.txt").read_text(encoding="utf-8").splitlines()
    assert events[0] == "mount"
    assert events[1].startswith("log:pair statistics analysis task start")
    assert events[2].startswith("log:pair statistics analysis task success")
    assert events[3] == (
        "log:pair statistics analysis task destroy success "
        "temporary_resource_count=0"
    )
    assert events[4] == "finish"


def test_analyze_pair_statistics_script_finishes_task_after_analysis_failure(
    tmp_path: Path,
) -> None:
    pair_directory = tmp_path / "code"
    pair_directory.mkdir()
    (pair_directory / "pair_statistics_shanghai.pkl").write_bytes(b"invalid")
    _write_platform_stub(tmp_path)
    script_directory = tmp_path / "scripts"
    script_directory.mkdir()
    source_script_path = (
        Path(__file__).parents[1] / "scripts" / "analyze_pair_statistics.py"
    )
    script_path = script_directory / "analyze_pair_statistics.py"
    shutil.copyfile(source_script_path, script_path)

    completed = subprocess.run(
        [sys.executable, str(script_path)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_script_environment(tmp_path),
    )

    assert completed.returncode != 0
    events = (tmp_path / "events.txt").read_text(encoding="utf-8").splitlines()
    assert events[0] == "mount"
    assert events[1].startswith("log:pair statistics analysis task start")
    assert events[2] == "formatted"
    assert events[3] == (
        "log:pair statistics analysis task destroy success "
        "temporary_resource_count=0"
    )
    assert events[4] == "finish"
