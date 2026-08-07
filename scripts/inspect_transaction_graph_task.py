from __future__ import annotations

import pickle
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, NamedTuple, Tuple, Type


PROJECT_ROOT = Path("/appdata/project/fid_bg_icmp")
SOURCE_PATH = PROJECT_ROOT / "code" / "pair_statistics_shanghai.pkl"
INPUT_PATH = PROJECT_ROOT / "pair_statistics_shanghai.pkl"

MerchantPair = Tuple[str, str]


class TransactionGraphSummary(NamedTuple):
    path: str
    edge_count: int
    node_count: int
    seconds: float


class TaskPlatform(NamedTuple):
    mount_check: Callable[[], None]
    log_data: Callable[[str], None]
    format_exception: Callable[[], None]
    finish_task: Callable[[], None]


class PairStatisticsPayload:
    pass


class RestrictedPairStatisticsUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Type[object]:
        if module == "business_district.graph" and name == "PairStatistics":
            return PairStatisticsPayload
        raise pickle.UnpicklingError(
            "交易图文件包含不允许加载的全局对象: "
            f"module={module!r}, name={name!r}"
        )


def validate_input_path(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"交易图文件不存在: path={path.resolve()}")
    if path.suffix.lower() != ".pkl":
        raise ValueError(f"交易图文件必须是 .pkl 文件: path={path.resolve()}")


def copy_graph_file(source_path: Path, target_path: Path) -> None:
    if source_path.resolve() == target_path.resolve():
        raise ValueError(
            "交易图源文件和目标文件不能是同一路径: "
            f"source_path={source_path.resolve()}, "
            f"target_path={target_path.resolve()}"
        )
    if not target_path.parent.is_dir():
        raise FileNotFoundError(
            f"交易图目标目录不存在: path={target_path.parent.resolve()}"
        )
    try:
        shutil.copyfile(source_path, target_path)
    except OSError as error:
        raise OSError(
            "交易图文件复制失败: "
            f"source_path={source_path.resolve()}, "
            f"target_path={target_path.resolve()}, reason={error}"
        ) from error


def validate_strengths(value: object, path: Path) -> Dict[MerchantPair, float]:
    if not isinstance(value, dict):
        raise TypeError(
            "交易图 strengths 必须是字典: "
            f"path={path.resolve()}, type={type(value).__name__}"
        )

    strengths: Dict[MerchantPair, float] = {}
    for pair, strength in value.items():
        if (
            not isinstance(pair, tuple)
            or len(pair) != 2
            or not all(isinstance(merchant, str) and merchant for merchant in pair)
        ):
            raise TypeError(
                "交易图 strengths 的键必须是两个非空商户 ID 组成的元组: "
                f"path={path.resolve()}, pair={pair!r}"
            )
        if not isinstance(strength, (int, float)) or isinstance(strength, bool):
            raise TypeError(
                "交易图 strengths 的值必须是数值: "
                f"path={path.resolve()}, pair={pair!r}, strength={strength!r}"
            )
        merchant_pair: MerchantPair = (pair[0], pair[1])
        strengths[merchant_pair] = float(strength)
    return strengths


def validate_visit_counts(value: object, path: Path) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise TypeError(
            "交易图 merchant_visit_counts 必须是字典: "
            f"path={path.resolve()}, type={type(value).__name__}"
        )

    visit_counts: Dict[str, int] = {}
    for merchant_id, visit_count in value.items():
        if not isinstance(merchant_id, str) or not merchant_id:
            raise TypeError(
                "交易图 merchant_visit_counts 的键必须是非空商户 ID: "
                f"path={path.resolve()}, merchant_id={merchant_id!r}"
            )
        if (
            not isinstance(visit_count, int)
            or isinstance(visit_count, bool)
            or visit_count < 0
        ):
            raise TypeError(
                "交易图 merchant_visit_counts 的值必须是非负整数: "
                f"path={path.resolve()}, merchant_id={merchant_id!r}, "
                f"visit_count={visit_count!r}"
            )
        visit_counts[merchant_id] = visit_count
    return visit_counts


def load_graph_size(path: Path) -> Tuple[int, int]:
    try:
        with path.open("rb") as file:
            loaded = RestrictedPairStatisticsUnpickler(file).load()
            trailing = file.read(1)
    except (OSError, EOFError, pickle.PickleError, AttributeError) as error:
        raise ValueError(
            f"交易图文件读取失败: path={path.resolve()}, reason={error}"
        ) from error

    if trailing:
        raise ValueError(f"交易图文件包含多余尾部数据: path={path.resolve()}")
    if not isinstance(loaded, PairStatisticsPayload):
        raise TypeError(
            "交易图文件顶层对象类型错误: "
            f"path={path.resolve()}, type={type(loaded).__name__}"
        )

    strengths = validate_strengths(getattr(loaded, "strengths", None), path)
    visit_counts = validate_visit_counts(
        getattr(loaded, "merchant_visit_counts", None),
        path,
    )
    return len(strengths), len(visit_counts)


def load_task_platform() -> TaskPlatform:
    try:
        from spdbccc_data import formattedExc
        from spdbccc_data import loging as logrecord
        from spdbccc_data import mountCheck
        from spdbccc_data import task as taskfinish
    except ImportError as error:
        raise RuntimeError(
            "无法导入 spdbccc_data 任务组件，请在线上任务环境中运行此脚本"
        ) from error

    return TaskPlatform(
        mount_check=mountCheck.mount_check,
        log_data=logrecord.log_data,
        format_exception=formattedExc.formatted_exc,
        finish_task=taskfinish.finish_task,
    )


class TaskMain:
    def __init__(
        self,
        platform: TaskPlatform,
        source_path: Path,
        input_path: Path,
    ) -> None:
        self.platform = platform
        self.source_path = source_path
        self.input_path = input_path

    def check(self) -> None:
        self.platform.mount_check()
        validate_input_path(self.source_path)
        self.platform.log_data(
            "transaction graph inspection task check success "
            f"source_path={self.source_path}, input_path={self.input_path}"
        )

    def taskrun(self) -> TransactionGraphSummary:
        started_at = time.time()
        self.platform.log_data(
            "transaction graph inspection task start "
            f"source_path={self.source_path}, input_path={self.input_path}"
        )
        try:
            copy_graph_file(self.source_path, self.input_path)
            validate_input_path(self.input_path)
            edge_count, node_count = load_graph_size(self.input_path)
            summary = TransactionGraphSummary(
                path=str(self.input_path.resolve()),
                edge_count=edge_count,
                node_count=node_count,
                seconds=time.time() - started_at,
            )
            print(
                f"交易图文件路径={summary.path}, "
                f"边={summary.edge_count}, 点={summary.node_count}"
            )
            self.platform.log_data(
                "transaction graph inspection task success "
                f"path={summary.path}, edge_count={summary.edge_count}, "
                f"node_count={summary.node_count}, seconds={summary.seconds:.2f}"
            )
            return summary
        except Exception:
            self.platform.format_exception()
            raise

    def destroy(self) -> None:
        self.platform.log_data(
            "transaction graph inspection task destroy success "
            "temporary_resource_count=0"
        )


def run_task(task: TaskMain) -> TransactionGraphSummary:
    try:
        task.check()
        return task.taskrun()
    finally:
        try:
            task.destroy()
        finally:
            task.platform.finish_task()


def main() -> None:
    run_task(TaskMain(load_task_platform(), SOURCE_PATH, INPUT_PATH))


if __name__ == "__main__":
    main()
