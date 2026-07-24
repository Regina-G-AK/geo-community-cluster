from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, NamedTuple, Tuple, TypeVar

import pandas as pd


Result = TypeVar("Result")


class TableExportEmailConfig(NamedTuple):
    root_path: Path
    source_table: str
    dt_values: Tuple[str, ...]
    output_filename: str
    title: str
    content: str
    recipients: Tuple[str, ...]
    copy_recipients: Tuple[str, ...]
    maximum_attempts: int
    retry_delay_seconds: float


class TableExportEmailSummary(NamedTuple):
    source_row_count: int
    source_column_count: int
    recipient_count: int
    copy_recipient_count: int
    output_path: Path
    seconds: float


class TaskPlatform(NamedTuple):
    mount_check: Callable[[], None]
    read_table: Callable[..., object]
    send_email: Callable[..., object]
    log_data: Callable[[str], None]
    format_exception: Callable[[], None]
    finish_task: Callable[[], None]


class TableExportEmailConfigError(ValueError):
    """读表、导出和邮件任务配置错误。"""


class TableExportError(RuntimeError):
    """表数据导出错误。"""


CONFIG = TableExportEmailConfig(
    root_path=Path("/appdata/project/fid_bg_icmp"),
    source_table="dev_icamp.icamp_merchant_cluster_algo_output",
    dt_values=("20260720",),
    output_filename="community_output.xlsx",
    title="商圈任务结果",
    content="商圈任务已完成，结果文件见附件。",
    recipients=("uatvv001518@icccuat.com",),
    copy_recipients=(),
    maximum_attempts=3,
    retry_delay_seconds=2.0,
)


def _validate_email_addresses(
    field_name: str,
    addresses: Tuple[str, ...],
) -> None:
    invalid_addresses = tuple(
        address
        for address in addresses
        if (
            address != address.strip()
            or address.count("@") != 1
            or not address.split("@", 1)[0]
            or not address.split("@", 1)[1]
        )
    )
    if invalid_addresses:
        raise TableExportEmailConfigError(
            "邮箱地址格式错误: "
            f"field={field_name}, invalid_addresses={invalid_addresses!r}"
        )


def build_output_path(config: TableExportEmailConfig) -> Path:
    return config.root_path / config.output_filename


def validate_config(config: TableExportEmailConfig) -> None:
    if not config.root_path.is_absolute():
        raise TableExportEmailConfigError(
            "root_path 必须是挂载目录的绝对路径: "
            f"root_path={config.root_path!r}"
        )
    if not config.root_path.is_dir():
        raise FileNotFoundError(
            "挂载根目录不存在或不是目录，请检查 mount_check 结果: "
            f"root_path={config.root_path!r}"
        )

    table_name_parts = config.source_table.split(".")
    if (
        len(table_name_parts) != 2
        or any(not part.strip() for part in table_name_parts)
        or any(part != part.strip() for part in table_name_parts)
    ):
        raise TableExportEmailConfigError(
            "source_table 必须使用 db.table 完整表名: "
            f"source_table={config.source_table!r}"
        )
    if not config.dt_values:
        raise TableExportEmailConfigError(
            "读表分区不能为空: field=dt_values"
        )
    invalid_dt_values = tuple(
        dt_value
        for dt_value in config.dt_values
        if len(dt_value) != 8 or not dt_value.isdigit()
    )
    if invalid_dt_values:
        raise TableExportEmailConfigError(
            "读表分区必须是 YYYYMMDD 格式: "
            f"invalid_dt_values={invalid_dt_values!r}"
        )

    filename_path = Path(config.output_filename)
    if (
        not config.output_filename
        or filename_path.name != config.output_filename
        or filename_path.suffix.lower() != ".xlsx"
    ):
        raise TableExportEmailConfigError(
            "output_filename 必须是根目录下的 .xlsx 文件名: "
            f"output_filename={config.output_filename!r}"
        )
    if not config.title.strip():
        raise TableExportEmailConfigError("邮件标题不能为空: field=title")
    if not config.content.strip():
        raise TableExportEmailConfigError("邮件正文不能为空: field=content")
    if not config.recipients:
        raise TableExportEmailConfigError("收件人不能为空: field=recipients")
    _validate_email_addresses("recipients", config.recipients)
    _validate_email_addresses("copy_recipients", config.copy_recipients)
    if config.maximum_attempts < 1:
        raise TableExportEmailConfigError(
            "外部调用次数必须不小于 1: "
            f"maximum_attempts={config.maximum_attempts}"
        )
    if config.retry_delay_seconds < 0:
        raise TableExportEmailConfigError(
            "外部调用重试间隔不能小于 0: "
            f"retry_delay_seconds={config.retry_delay_seconds}"
        )


def load_task_platform() -> TaskPlatform:
    try:
        import spdbccc_data as sd
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
        read_table=sd.read_table,
        send_email=sd.send_email,
        log_data=logrecord.log_data,
        format_exception=formattedExc.formatted_exc,
        finish_task=taskfinish.finish_task,
    )


def _call_external_with_retries(
    platform: TaskPlatform,
    operation_name: str,
    operation: Callable[[], Result],
    maximum_attempts: int,
    retry_delay_seconds: float,
) -> Result:
    for attempt in range(1, maximum_attempts + 1):
        try:
            return operation()
        except Exception as error:
            if attempt == maximum_attempts:
                raise
            platform.log_data(
                "external call retry warning "
                f"operation={operation_name!r}, "
                f"attempt={attempt}, "
                f"maximum_attempts={maximum_attempts}, "
                f"error_type={type(error).__name__!r}, "
                f"reason={str(error)!r}"
            )
            time.sleep(retry_delay_seconds)

    raise RuntimeError(
        "外部调用重试流程异常结束: "
        f"operation={operation_name!r}, maximum_attempts={maximum_attempts}"
    )


def read_source_table(
    platform: TaskPlatform,
    config: TableExportEmailConfig,
) -> pd.DataFrame:
    result = _call_external_with_retries(
        platform,
        "read_table",
        lambda: platform.read_table(
            config.source_table,
            dt=list(config.dt_values),
        ),
        config.maximum_attempts,
        config.retry_delay_seconds,
    )
    if not isinstance(result, pd.DataFrame):
        raise TypeError(
            "spdbccc_data.read_table 返回类型错误: "
            f"table={config.source_table!r}, "
            f"dt_values={config.dt_values!r}, "
            f"actual_type={type(result).__name__!r}"
        )
    if result.empty:
        raise TableExportError(
            "目标分区没有数据，不生成和发送空附件: "
            f"table={config.source_table!r}, dt_values={config.dt_values!r}"
        )
    return result


def write_excel_file(
    dataframe: pd.DataFrame,
    output_path: Path,
) -> None:
    try:
        dataframe.to_excel(output_path, index=False)
    except ImportError as error:
        raise TableExportError(
            "Excel 文件写出失败，当前环境缺少 openpyxl: "
            f"output_path={output_path!r}"
        ) from error
    except (OSError, ValueError) as error:
        raise TableExportError(
            "Excel 文件写出失败: "
            f"output_path={output_path!r}, "
            f"error_type={type(error).__name__!r}, reason={str(error)!r}"
        ) from error

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise TableExportError(
            "Excel 写出完成后未找到有效文件: "
            f"output_path={output_path!r}"
        )


def send_output_email(
    platform: TaskPlatform,
    config: TableExportEmailConfig,
    output_path: Path,
) -> None:
    def send_email() -> object:
        if not config.copy_recipients:
            return platform.send_email(
                title=config.title,
                content=config.content,
                to=list(config.recipients),
                files=[str(output_path)],
            )
        return platform.send_email(
            title=config.title,
            content=config.content,
            to=list(config.recipients),
            cc=list(config.copy_recipients),
            files=[str(output_path)],
        )

    _call_external_with_retries(
        platform,
        "send_email",
        send_email,
        config.maximum_attempts,
        config.retry_delay_seconds,
    )


class TaskMain:
    def __init__(
        self,
        platform: TaskPlatform,
        config: TableExportEmailConfig,
    ) -> None:
        self.platform = platform
        self.config = config

    def check(self) -> None:
        self.platform.mount_check()
        validate_config(self.config)
        self.platform.log_data(
            "table export email task mount and check success "
            f"root_path={str(self.config.root_path)!r}"
        )

    def taskrun(self) -> TableExportEmailSummary:
        started_at = time.time()
        output_path = build_output_path(self.config)
        self.platform.log_data(
            "table export email task start "
            f"table={self.config.source_table!r}, "
            f"dt_values={self.config.dt_values!r}, "
            f"output_path={str(output_path)!r}"
        )
        try:
            dataframe = read_source_table(self.platform, self.config)
            write_excel_file(dataframe, output_path)
            self.platform.log_data(
                "table export email task file write success "
                f"row_count={len(dataframe)}, "
                f"column_count={len(dataframe.columns)}, "
                f"output_path={str(output_path)!r}"
            )
            send_output_email(self.platform, self.config, output_path)
            seconds = time.time() - started_at
            summary = TableExportEmailSummary(
                source_row_count=len(dataframe),
                source_column_count=len(dataframe.columns),
                recipient_count=len(self.config.recipients),
                copy_recipient_count=len(self.config.copy_recipients),
                output_path=output_path,
                seconds=seconds,
            )
            self.platform.log_data(
                "table export email task success "
                f"row_count={summary.source_row_count}, "
                f"column_count={summary.source_column_count}, "
                f"recipient_count={summary.recipient_count}, "
                f"copy_recipient_count={summary.copy_recipient_count}, "
                f"seconds={summary.seconds:.2f}"
            )
            return summary
        except Exception:
            self.platform.format_exception()
            raise

    def destroy(self) -> None:
        self.platform.log_data(
            "table export email task destroy success "
            "temporary_resource_count=0, generated_file_preserved=1"
        )


def run_task(task: TaskMain) -> TableExportEmailSummary:
    try:
        task.check()
        return task.taskrun()
    finally:
        try:
            task.destroy()
        finally:
            task.platform.finish_task()


def main() -> None:
    task = TaskMain(load_task_platform(), CONFIG)
    summary = run_task(task)
    print(summary)


if __name__ == "__main__":
    main()
