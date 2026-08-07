from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import sys
from pathlib import Path

from business_district.graph import PairStatistics


def write_platform_stub(directory: Path) -> None:
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


def build_script_environment(directory: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(directory)
    return environment


def test_script_prints_transaction_graph_path_edge_count_and_node_count(
    tmp_path: Path,
) -> None:
    graph_directory = tmp_path / "code"
    graph_directory.mkdir()
    source_graph_path = graph_directory / "pair_statistics_shanghai.pkl"
    input_path = tmp_path / "pair_statistics_shanghai.pkl"
    statistics = PairStatistics(
        strengths={("a", "b"): 2.0, ("b", "c"): 1.0},
        supports={("a", "b"): 2, ("b", "c"): 1},
        merchant_visit_counts={"a": 3, "b": 4, "c": 2, "isolated": 1},
    )
    with source_graph_path.open("wb") as file:
        pickle.dump(statistics, file, protocol=4)

    write_platform_stub(tmp_path)
    script_directory = tmp_path / "scripts"
    script_directory.mkdir()
    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "inspect_transaction_graph_task.py"
    )
    script_path = script_directory / source_path.name
    shutil.copyfile(source_path, script_path)

    completed = subprocess.run(
        [sys.executable, str(script_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=build_script_environment(tmp_path),
    )

    assert completed.stdout.strip() == (
        f"交易图文件路径={input_path.resolve()}, 边=2, 点=4"
    )
    assert input_path.read_bytes() == source_graph_path.read_bytes()
    events = (tmp_path / "events.txt").read_text(encoding="utf-8").splitlines()
    assert events[:3] == [
        "mount",
        (
            "log:transaction graph inspection task check success "
            f"source_path={source_graph_path}, input_path={input_path}"
        ),
        (
            "log:transaction graph inspection task start "
            f"source_path={source_graph_path}, input_path={input_path}"
        ),
    ]
    assert events[3].startswith(
        "log:transaction graph inspection task success "
        f"path={input_path.resolve()}, edge_count=2, node_count=4, seconds="
    )
    assert events[4:] == [
        (
            "log:transaction graph inspection task destroy success "
            "temporary_resource_count=0"
        ),
        "finish",
    ]
