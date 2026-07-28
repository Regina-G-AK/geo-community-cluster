from __future__ import annotations

import datetime
import time
from typing import NamedTuple, Tuple

import pandas as pd
import spdbccc_data as sd
from spdbccc_data import dtDate
from spdbccc_data import loging as logrecord
from spdbccc_data import mountCheck
from spdbccc_data import task as taskfinish


TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output"
TEMP_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output_tmp"
DT_EXPRESSION = "T-1"
TEST_REGION = "write_test"
TEST_COMMUNITY_ID = "9"


class OutputRow(NamedTuple):
    storename: str
    community_id: str
    previous_community_id: str
    region: str
    is_interfere: str
    update_time: str
    is_abnormal: str
    is_position: int


class WriteTaskSummary(NamedTuple):
    target_table: str
    temp_table: str
    output_dt: str
    written_rows: int
    seconds: float


def build_output_rows(update_time: datetime.datetime) -> Tuple[OutputRow, ...]:
    timestamp = update_time.strftime("%Y-%m-%d %H:%M:%S")
    return tuple(
        OutputRow(
            storename=f"write_table_smoke_test_{index:03d}",
            community_id=TEST_COMMUNITY_ID,
            previous_community_id="",
            region=TEST_REGION,
            is_interfere="N",
            update_time=timestamp,
            is_abnormal="1",
            is_position=1 if index == 1 else 0,
        )
        for index in range(1, 6)
    )


def build_overwrite_sql(
    target_table: str,
    temp_table: str,
    output_dt: str,
) -> str:
    select_columns = ", ".join(OutputRow._fields)
    return (
        f"insert overwrite table {target_table}\n"
        f"partition (dt='{output_dt}')\n"
        f"select {select_columns}\n"
        f"from {temp_table}"
    )


class TaskMain:
    def __init__(
        self,
        target_table: str,
        temp_table: str,
        dt_expression: str,
    ) -> None:
        self.target_table = target_table
        self.temp_table = temp_table
        self.dt_expression = dt_expression

    def check(self) -> None:
        mountCheck.mount_check()

    def taskrun(self) -> WriteTaskSummary:
        started_at = time.time()
        output_dt = str(dtDate.dt_date(self.dt_expression))
        rows = build_output_rows(datetime.datetime.now().astimezone())
        write_df = pd.DataFrame(rows, columns=OutputRow._fields)
        sql = build_overwrite_sql(
            self.target_table,
            self.temp_table,
            output_dt,
        )
        logrecord.log_data(
            "Hive 临时表覆盖测试开始: "
            f"target_table={self.target_table}, temp_table={self.temp_table}, "
            f"output_dt={output_dt}, "
            f"row_count={len(rows)}"
        )
        sd.execute_sql(f"drop table if exists {self.temp_table}")
        sd.write_table(write_df, self.temp_table, debug=False, dt=None)
        sd.execute_sql(sql)
        seconds = time.time() - started_at
        logrecord.log_data(
            "Hive 临时表覆盖测试完成: "
            f"target_table={self.target_table}, temp_table={self.temp_table}, "
            f"output_dt={output_dt}, "
            f"row_count={len(rows)}, seconds={seconds:.2f}"
        )
        return WriteTaskSummary(
            target_table=self.target_table,
            temp_table=self.temp_table,
            output_dt=output_dt,
            written_rows=len(rows),
            seconds=seconds,
        )

    def destroy(self) -> None:
        sd.execute_sql(f"drop table if exists {self.temp_table}")


def run_task(task: TaskMain) -> WriteTaskSummary:
    try:
        task.check()
        return task.taskrun()
    finally:
        task.destroy()
        taskfinish.finish_task()


def main() -> None:
    task = TaskMain(TARGET_TABLE, TEMP_TABLE, DT_EXPRESSION)
    summary = run_task(task)
    print(
        "write_success "
        f"target_table={summary.target_table} "
        f"temp_table={summary.temp_table} "
        f"output_dt={summary.output_dt} "
        f"written_rows={summary.written_rows} "
        f"seconds={summary.seconds:.2f}"
    )


if __name__ == "__main__":
    main()
