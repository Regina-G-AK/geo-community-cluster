# 商圈初始化聚类

本项目实现基于交易共现关系的商圈初始化聚类。主流程直接读取原始交易数据，在内存中构建商户对、商户图和社区结果；本地运行目录只保留可复用的商户对中间文件，Hive 入口负责写入商圈业务结果表。

## 运行与配置

项目运行环境固定为 Python 3.7.1 至 3.7.x，依赖版本以 `pyproject.toml` 为准。

本项目不再读取 INI、TOML 等配置文件，也不提供带默认参数的命令行任务入口。初始化聚类和增量归属统一通过项目根目录的 `run_hive_business_district.py` 配置；可以直接执行该 Python 文件，也可以在同目录的 `run_hive_business_district.ipynb` 中运行全部单元。Notebook 直接调用 Python 入口的 `main()`，两种入口使用完全相同的配置和执行逻辑。城市、输入格式、图算法、社区算法、地理参数、锚点、输出目录、增量归属及工作进程数均在 Python 入口中显式构造。

`RuntimeConfig.process_count` 配置工作进程数，必须是不小于 `1` 的整数；主入口当前使用 `4`。初始化聚类会按卡号分片并行计算商户对统计，增量归属会按候选商户分片并行计算图与地理评分。设置为 `1` 时使用相同的串行计算逻辑，适合小数据量运行。

每次运行会在全部商户对统计计算完成后，直接向项目根目录 `code` 文件夹下的 `pair_statistics_{region}.pkl` 写入商户对中间数据，不使用 `.tmp` 临时文件，其中 `region` 使用 `[city].code`。文件使用 Python pickle protocol 4，顶层对象为 `business_district.graph.PairStatistics`，包含以有序商户二元组为键的 `strengths`、同键的 `supports`，以及以商户 ID 为键的 `merchant_visit_counts`；该格式可由项目要求的 Python 3.7 读取。

可以使用独立诊断脚本分析该中间文件。分析逻辑只依赖 Python 标准库，不导入项目业务模块；任务入口使用线上环境提供的 `spdbccc_data` 执行挂载检查、运行、销毁和 `finish_task()` 收尾。脚本会复算最小支持人数、SPPMI、z-score、互为 top-k 和访问量型连锁商户删除等阶段，并直接打印总体摘要、分布、连通分量和重点商户指标。由于中间文件不包含商户分类和坐标，脚本不能复算分类 `2` 商户删除和疑似线上商户识别。pickle 文件只应来自可信任务输出。

```powershell
python scripts/analyze_pair_statistics.py
```

可以使用 `scripts/inspect_transaction_graph_task.py` 把项目根目录下的 `code/pair_statistics_shanghai.pkl` 复制为项目根目录的 `pair_statistics_shanghai.pkl`，再读取根目录副本并打印其绝对路径、边数和点数。边数取 `strengths` 的商户对数量，点数取 `merchant_visit_counts` 的商户数量；脚本遵循线上 task 的挂载、检查、运行、销毁和 `finish_task()` 生命周期。

```powershell
python scripts/inspect_transaction_graph_task.py
```

输入路径、图参数、访问量型商户阈值和重点商户输出数量集中定义在 `scripts/analyze_pair_statistics.py` 顶部；默认基于脚本位置读取项目根目录下的 `code/pair_statistics_shanghai.pkl`，不受任务启动工作目录影响。打印的 JSON 统计覆盖全部商户，每类重点商户默认展示前 `50` 名。分析时直接校验中间文件中的原始字典，只为重点商户生成明细，孤立商户使用计数参与分布统计，避免大规模数据下复制全部商户和商户对。

可以使用独立附件邮件任务读取 Hive 结果表、写出 Excel 文件并发送附件。`scripts/send_attachment_email_task.py` 顶部的 `CONFIG` 当前从 `dev_icamp.icamp_merchant_cluster_algo_output` 读取 `dt=20260720`，只保留 `community_id` 非空且不等于空字符串的行，并写入 `/appdata/project/fid_bg_icmp/community_output.xlsx`。读表使用 `spdbccc_data.read_table("dev_icamp.icamp_merchant_cluster_algo_output", dt=["20260720"])` 的完整表名形式，不再单独传入 `db_name`；运行前应核对分区日期、文件名、收件人和抄送人。

```powershell
python scripts/send_attachment_email_task.py
```

可以使用项目根目录的独立任务脚本统计输入交易形成的商户对，以及这些商户对在商圈结果表中的覆盖情况。`merchant_pair_coverage_task.py` 固定读取 `20260131` 至 `20260630` 的 6 个输入分区，逐个读取 Hive `part*` 分片并立即筛选上海地区和上海城市名称规则，避免在内存中同时保留未过滤的完整分区；随后按 60 分钟合并同卡同商户连续交易，并使用 120 分钟交易对窗口和 3 人最小共同持卡人数。任务读取结果表 `dt=20260727`，将原始交易对、有效交易对、参与商户及非空 `community_id` 覆盖率的一行汇总写入 `dev_icamp.icamp_merchant_pair_coverage_tmp`。任务会记录分区挂载、分片读取、到访合并、交易对计算、结果读取和写表阶段，失败时明确记录异常类型和原因；每次运行会替换该临时表，成功结束后保留表供查询。

```powershell
python merchant_pair_coverage_task.py
```

可以使用项目根目录的 `draw_merchant_pair_funnel.py`，根据交易对统计结果生成三组并列口径的 SVG 漏斗图：交易对有效率、有效交易对商户覆盖率和聚类商户覆盖率。脚本使用 Python 标准库，不需要安装绘图库；指标和 `support` 门槛在脚本顶部显式配置，输出文件为项目根目录的 `merchant_pair_funnel.svg`。

```powershell
python draw_merchant_pair_funnel.py
```

可以使用 `scripts/draw_merchant_pair_sankey.py` 生成上海 1—6 月商户聚类全流程桑基图。商户主链路展示“上海总商户 → 线下商户 → 线下非个体户 → 能形成交易对的商户 → 有效交易对商户 → 能够形成商圈的商户”，并将能够形成商圈的商户拆分为经纬度直接添加和交易对添加；交易量链路展示总交易、线下交易、剔除个体户后的可聚类交易、匹配商圈有效交易，并将匹配商圈有效交易拆分为非点评商圈交易和点评商圈交易；交易对数量因单位不同，使用独立桑基流。输出文件为 `scripts/merchant_pair_sankey.svg`。

```powershell
python scripts/draw_merchant_pair_sankey.py
```

任务严格按 `check()`、`taskrun()`、`destroy()`、`finish_task()` 顺序执行：`check()` 先完成平台挂载，再校验表名、分区、输出目录和邮箱；`taskrun()` 依次读表、拒绝空结果、写出并校验 Excel、发送邮件，读表和发信失败时均按 `maximum_attempts` 和 `retry_delay_seconds` 重试，最终失败会保留原始异常；`destroy()` 只记录清理结果，不删除生成的 Excel；无论挂载、读表、写文件或发送是否成功，最外层都会调用 `finish_task()` 完成平台收尾。

两个等价入口中的 RSS 资源检测当前已注释停用，不再周期采样进程内存，也不再输出 `[memory]` 报告。原资源检测实现、入口调用和测试代码均以注释形式保留，便于需要时恢复。

Hive 任务通过 `spdbccc_data.read_table` 普通读取 `dev_icamp.icamp_merchant_cluster_algo_param` 的 T-1 分区，再根据参数表中的 `start_date`、`end_date` 计算输入分区：起止日期相同时直接读取该日分区，例如 `start_date=end_date=20260115` 时读取 `dt=20260115`；起止日期不同时读取时间范围所涉及月份的月底分区，例如范围跨越 2026 年 1 月和 2 月时读取 `dt=20260131`、`dt=20260228`。初始化任务和增量归属任务先按参数表 `region` 关联交易，再使用分行—城市关系筛选 `storename`：不含“市”的名称保留；含“市”的名称必须包含该分行允许的城市，例如上海仅允许“上海市”，兰州允许“兰州市”或“酒泉市”。未配置分行—城市关系的 `region` 会明确报错。被城市规则排除的分类 `1`、`2` 商户不参与聚类，但仍写入最终结果，`community_id` 留空，状态使用 `suspect_cross_region` 并通过 `status_codes` 转换为 `is_abnormal=5`；同一商户若另有符合规则的交易，则以正常聚类结果为准。任务不再判断 `transaction_time` 是否位于 `start_date` 和 `end_date` 之间。交易时间窗口、时间衰减权重、最小交易次数和最小商户数由参数表提供，其余静态算法参数由主入口提供。初始化任务会读取 `business_district`：已有非空 ID 不限制格式，同一商户不得对应多个已有 ID；已有商户原样保留该 ID，其他商户先按增量归属规则尝试加入已有商圈，未成功加入的商户才保留初始化聚类产生的纯数字字符串 ID。已有商圈不受最小社区规模过滤影响，新聚类仍按 `min_merchant_count` 过滤。初始化和增量结果都先通过 `spdbccc_data.write_table` 批量写入各自临时表，再通过一条 `INSERT OVERWRITE ... SELECT` SQL 覆盖写入 `dev_icamp.icamp_merchant_cluster_algo_output` 的 T-1 整个分区；任务会在写入前后清理临时表，不保留该分区的历史行，也不再写入风险商户表。

Hive 初始化入口的交易输入表会按 `/appdata/project/fid_bg_icmp/tbl/{表名}/dt={日期}/part*` 分片读取 parquet 文件；每个分片会先按参数表的 `region` 和对应的 `storename` 城市规则筛选，只有命中行才参与最终合并，以降低合并时的峰值内存。`dt` 统一使用分区路径中的日期，即使分片内自带 `dt` 列也会覆盖。日期范围内缺少目录、没有 `part*` 文件或分片全部为空的分区会被跳过；如果全部日期均无有效数据，或所有分片均没有匹配参数规则的交易，任务会明确报错。

可以使用 `scripts/write_hive_output_smoke_task.py` 验证临时表覆盖写入链路。脚本按线上 task 生命周期运行，先将 5 条 `region=write_test` 的测试记录写入 `dev_icamp.icamp_merchant_cluster_algo_output_smoke_tmp`，再通过 `INSERT OVERWRITE` 将 `dev_icamp.icamp_merchant_cluster_algo_output` 的平台 T-1 整个分区替换为这 5 条记录，最后删除临时表。执行会删除目标分区原有的全部地区数据，运行前必须确认目标环境和实际分区日期。

```bash
python scripts/write_hive_output_smoke_task.py
```

可以使用独立脚本验证挂载目录和 Hive parquet 分片读取，不运行聚类和写表逻辑。脚本会在读表成功或失败后执行 `taskfinish.finish_task()` 完成平台任务收尾。`scripts/test_hive_partition_read.py` 默认自动读取最近一个已结束月份的最后一天分区，同时可在顶部 `CONFIG` 中修改表根目录、表名、分区日期和预览行数。

```bash
python scripts/test_hive_partition_read.py
```

脚本会依次输出分区目录是否存在、`part*` 文件数量、每个分片的行列数和耗时；读取成功后输出总行数、字段列表和指定行数的数据预览，最后输出 `taskfinish_start` 和 `taskfinish_success`。脚本只依赖 `pandas`、parquet 读取引擎以及线上环境提供的 `spdbccc_data.mountCheck` 和 `spdbccc_data.task`。

在项目根目录可以直接运行 Python 入口：

```powershell
python run_hive_business_district.py
```

也可以打开同目录的 `run_hive_business_district.ipynb` 并运行全部单元，将 Notebook 作为程序入口。Notebook 不复制任务配置，只导入并调用 Python 入口的 `main()`，因此配置只需在 `run_hive_business_district.py` 中维护。

Python 入口包含静态算法参数、Hive 表和任务生命周期配置，并按参数表 `is_daily` 自动选择增量归属或初始化聚类。

`run_hive_business_district.py` 通过 `HiveTaskConfig` 显式传入强类型算法配置、归属配置、输入表、参数表、输出表、临时表和 `dt_expression`；初始化与增量任务共用同一份 `AssignmentConfig`，入口先普通读取参数表 T-1 分区，再按 `is_daily` 调度：`1` 调用增量归属，`0` 调用初始化聚类。

初始化与增量任务会在源数据读取、交易对统计、图构建、聚类或归属、结果写入等关键阶段输出 `[stage]` 日志。每条日志包含阶段名和阶段数据量，不再读取或输出当前进程 RSS；输出使用即时刷新，任务运行期间可直接观察进度。交易对统计会继续输出按卡分组、各工作分片的访问处理量和候选比较次数，以及父进程合并进度；每个工作进程只处理一个分片后退出，生产入口只维护一份累计商户对字典，不为每个分片复制完整快照。坐标可靠性日志会输出有坐标商户数、被异常共享坐标排除的商户数以及单一坐标最大商户数。任务结束时不再输出峰值和平均 RSS 汇总。

增量归属用于把新商户归入已有商圈，并写出本次有效输入中的全部商户。任务先读取参数表和输入表；输入表需要包含 `business_district` 字段。增量任务将 `is_abnormal` 状态 `2`、`5`、`6` 的交易排除在商户对统计和重新归属之外，但仍按商户最新输入记录把原状态和 `business_district` 写回结果表；空值、状态 `1`、`3`、`4`、`7` 及其他状态均保留并参与商户对统计、归属和输出。同一商户可在输入中重新修改状态，不要求各行状态一致，最终是否透传排除状态由最新输入记录决定。商户是否已有商圈仅由非空 `business_district` 判断，与状态无关。带有非空 `business_district` 的已有商户优先保留原商圈，即使商户名命中跨区域规则也不改写为跨区域状态；没有商圈 ID 的商户参与跨区域分类。状态和人工干预预筛选完成后，增量任务复用初始化入口的 `load_hive_transactions`，统一执行必需列、商户分类、关键字段、流水号、坐标和交易时间格式校验。分类 `1`、`2` 且未被人工干预排除的已有商户原样保留输入商圈 ID，新商户写出本次归属结果；写出前会校验每个有效输入商户恰好对应一条结果。同一商户存在多个非空 `business_district` 时直接报错。增量任务根据本次有效输入重新计算完整商户对统计，直接覆盖主入口中的 `algorithm_config.city.code` 和 `algorithm_config.output.directory` 对应的 `pair_statistics_{region}.pkl`，不读取或合并历史中间文件；随后使用本次统计构建稀疏图。有可靠经纬度的新商户直接继承距离最近且位于配置半径内的已有商圈成员 ID，不进行地理投票；没有经纬度或坐标共享商户数超过可靠性上限时，直接继承正 PMI/SPPMI 边权最大的已有商圈邻居 ID，不进行图投票。距离阈值由 `AssignmentConfig` 显式设置，坐标共享商户数上限由 `GeoConfig` 显式设置。

## 商圈图生成

独立画图脚本读取 `merchants.csv` 并按 `community_id` 为每个商圈生成一张 SVG 图。输入文件需要包含以下字段：

- `merchant_id`：商户名称。
- `community_id`：商圈 ID。
- `is_anchor_candidate`：是否为锚点商户，`0` 表示普通商户，`1` 表示锚点商户。

在 `merchants.csv` 所在目录运行默认命令：

```powershell
python scripts/draw_community_graphs.py
```

默认会生成 `community_graphs_YYYYMMDD_HHMMSS` 输出目录。也可以显式指定输入文件和输出目录：

```powershell
python scripts/draw_community_graphs.py merchants.csv community_graphs
```

## 输入

交易文件需要包含表头，字段如下：

- `account_number`：卡号，用于按持卡人构建共现。
- `global_flow_number`：流水号，必须非空且唯一。
- `storename`：商户名称，用作聚类商户 ID。
- `transaction_time`：交易时间，按 `[input].timestamp_formats` 解析。
- `pos_longitude`：经度，可为空；可靠坐标用于疑似线上识别和聚类后的已有商圈归属。
- `pos_latitude`：纬度，可为空；可靠坐标用于疑似线上识别和聚类后的已有商圈归属。
- `region`：地区，输出使用该商户最新交易时间对应的值。
- `is_intefere`：输入可为空，初始化聚类忽略该字段。
- `status`：输入可为空，初始化聚类忽略该字段。
- `dt`：日期，不参与算法，输出使用该商户最新交易时间对应的值。

初始化聚类只使用交易共现 PMI/交易次数边，不构建地理商户对。每个 `storename` 只提取一组有效数值坐标，同一商户存在多组不同有效坐标时会明确报错并给出商户名和坐标样例；同一坐标关联商户数超过配置上限时，整组坐标不参与疑似线上识别或已有商圈地理归属，这些商户按无可靠坐标处理。人工干预、输入状态和 `dt` 不参与算法。

## 输出

主流程本地运行只在 `[output].directory` 下写入商户对中间文件：

```text
pair_statistics_{region}.pkl
```

该文件是 pickle 格式的 `PairStatistics` 对象，包含 `strengths`、`supports` 和 `merchant_visit_counts`，用于复用商户对统计结果重新执行后续聚类实验。本地运行不再写出业务 CSV 或实验记录文件。

Hive 入口输入表必须包含：

- `account_number`
- `global_flow_number`
- `storename`
- `merchant_category`
- `transaction_time`
- `pos_longitude`
- `pos_latitude`
- `region`
- `is_interfere`
- `is_abnormal`
- `business_district`

`merchant_category` 必须是 `0`（线上）、`1`（线下）、`2`（线下连锁店）或 `3`（线下个体户）；同一 `storename` 对应多个分类会直接报错。初始化和增量任务只保留分类 `1`、`2` 的交易进入访问合并、候选边构建和聚类。分类 `2` 不进入社区发现且永远不能成为锚点；无法加入已有商圈而进入新聚类时，可按正权重候选社区展开为一个或多个普通商圈成员；直接加入已有商圈时与其他分类一样只输出一个商圈 ID。`pos_longitude`、`pos_latitude`、`is_interfere`、`is_abnormal` 和 `business_district` 在输入时允许为空；但同一 `storename` 一旦提供 `business_district`，其所有非空值必须一致。`dt` 是 Hive 分区和输出字段，不要求读取结果包含该列；缺失时入口会按分区或任务日期补齐。经纬度只空一列、格式非法或越界时按无坐标处理；同一 `storename` 的有效数值经纬度必须一致，可靠经纬度只用于疑似线上识别和聚类后的已有商圈归属。

Hive 参数表 `dev_icamp.icamp_merchant_cluster_algo_param` 必须包含：

- `start_date`
- `end_date`
- `region`
- `max_transaction_time_interval`
- `min_transaction_number`
- `min_merchant_count`
- `is_daily`

初始化入口和增量归属入口都先用参数表的 `region` 和交易表 `region` 关联，再按对应分行允许的城市筛选 `storename`，不再根据 `transaction_time`、`start_date` 和 `end_date` 筛选行。`is_daily` 取值只能是 `0` 或 `1`，其中 `1` 表示增量归属，`0` 表示初始化聚类。`max_transaction_time_interval` 映射到共现窗口分钟数，`min_transaction_number` 映射到最小支持交易人数，`min_merchant_count` 映射到有效商圈最小商户数。同一任务分区内多行参数必须使用相同算法参数，否则任务会报错。交易时间衰减参数不再从参数表读取，初始化和增量任务统一使用主入口中的 `algorithm_config.cooccurrence.decay_tau_minutes`。

Hive 入口写入目标表字段为：

- `storename`
- `community_id`
- `previous_community_id`
- `region`
- `is_interfere`
- `update_time`
- `is_abnormal`
- `is_position`
- `dt`

其中 `community_id` 始终按字符串写出：输入已有商圈使用原字母数字 ID，初始化新聚类使用纯数字字符串 ID。分类 `2` 的线下连锁店及访问量规则识别出的连锁/泛客群商户在新聚类结果中可保留多条普通成员挂靠记录且 `is_position` 恒为 `0`；直接加入已有商圈时只保留一条归属。写入 Hive 前按最终输出的 `storename` 去重统计各个新聚类商圈的商户数，低于参数表 `min_merchant_count` 的新商圈不输出商圈 ID，并按疑似孤立商户处理；城市规则排除的商户按疑似跨区域商户处理并写出状态码 `5`。`previous_community_id` 留空，`region` 与输入表保持一致，`is_interfere` 固定为 `N`，`is_abnormal` 使用商户状态码，`update_time` 为运行时间戳，`is_position` 表示是否为锚点商户，`dt` 固定使用任务运行时计算出的 T-1 分区。

## 商户状态

Hive 目标表 `is_abnormal` 输出以下商户状态码：

- `1`：正常，商户进入有效商圈。
- `2`：疑似线上，商户自身无经纬度，且关联的多个有坐标商户中存在距离超过地理阈值的商户对。
- `3`：疑似孤立，初始化聚类时商户没有有效边或所在社区未达到配置项 `anchors.minimum_community_size` 定义的有效商圈规模；直接归属时，有坐标商户在配置半径内没有已有商圈成员，或无坐标商户没有指向已有商圈成员的正 PMI/SPPMI 边。
- `4`：疑似消逝，当前初始化聚类和增量归属入口不会产出该状态。
- `5`：疑似跨区域，当前已暂停判断，初始化聚类和增量归属均不会产出该状态。
- `6`：疑似连锁店；初始化聚类会对命中访问量规则且已挂靠有效商圈的非分类 `2` 商户输出该状态，增量归属当前不会产出该状态。
- `7`：已删除，当前初始化聚类和增量归属入口不会产出该状态。

内部算法仍使用 `normal`、`suspect_isolated`、`suspect_online` 等状态名，写入 Hive 目标表前统一转换为上述状态码。

## 测试

```powershell
python -m pytest
```

## 地理归属参数

- `[geo].cluster_radius_meters`：疑似线上识别使用的关联商户距离阈值，当前配置为 `1000.0` 米；初始化聚类不使用该值生成地理边。
- `[geo].maximum_merchants_per_coordinate`：同一坐标可用于地理判断和归属的最大商户数，当前配置为 `100`；超过后整组坐标按不可靠坐标处理。
- `[community].minimum_online_neighbor_count`：疑似线上商户至少需要关联的可靠坐标商户数。
- `AssignmentConfig.community_assignment_distance_meters`：限制可靠坐标商户可继承的最近已有商圈成员，当前配置为 `3000.0` 米；没有可靠坐标的商户使用最强正 PMI/SPPMI 边。

## 商户对聚类参数

`stuff/cluster_from_pair_statistics.py` 在 SPPMI 建图阶段支持以下额外参数：

- `[graph].degree_penalty_gamma`：按端点候选邻居度数惩罚 SPPMI 边权，当前建议 `0.0`，先退回原始 SPPMI，避免连锁店跨商圈边被过度压低。
- `[graph].jaccard_threshold`：边两端候选邻居集合的 Jaccard 结构门槛，当前建议 `0.0`，先保留低重叠的跨商圈边用于识别多商圈普通成员。
- `[anchors].maximum_participation`：锚点候选最大参与系数，当前建议 `0.1`，超过该阈值的非连锁商户不能成为锚点。
- `[anchors].chain_visit_count_quantile`：访问量型疑似连锁规则的分位阈值；初始化聚类使用本次全部商户访问量计算分位值。
- `[anchors].chain_minimum_visit_count`：访问量型疑似连锁规则的绝对访问次数下限；实际阈值取分位值与该值中的较大者。
