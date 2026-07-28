# 商圈初始化聚类

本项目实现基于交易共现关系的商圈初始化聚类。主流程直接读取原始交易数据，在内存中构建商户对、商户图和社区结果；本地运行目录只保留可复用的商户对中间文件，Hive 入口负责写入商圈业务结果表。

## 运行与配置

项目运行环境固定为 Python 3.7.1 至 3.7.x，依赖版本以 `pyproject.toml` 为准。

本项目不再读取 INI、TOML 等配置文件，也不提供带默认参数的命令行任务入口。初始化聚类和增量归属可通过 `notebooks/run_hive_business_district.ipynb` 或等价的 `run_hive_business_district.py` 配置和运行；城市、输入格式、图算法、社区算法、地理参数、锚点、输出目录、增量归属及工作进程数均在入口中显式构造。

`RuntimeConfig.process_count` 配置工作进程数，必须是不小于 `1` 的整数；notebook 当前使用 `4`。初始化聚类会按卡号分片并行计算商户对统计，增量归属会按候选商户分片并行计算图与地理评分。设置为 `1` 时使用相同的串行计算逻辑，适合小数据量运行。

每次运行会直接在 `[output].directory` 下写入 `pair_statistics_{region}.pkl` 商户对中间文件，其中 `region` 使用 `[city].code`。文件使用 Python pickle protocol 4，顶层对象为 `business_district.graph.PairStatistics`，包含以有序商户二元组为键的 `strengths`、同键的 `supports`，以及以商户 ID 为键的 `merchant_visit_counts`；该格式可由项目要求的 Python 3.7 读取。

可以使用独立诊断脚本分析该中间文件。分析逻辑只依赖 Python 标准库，不导入项目业务模块；任务入口使用线上环境提供的 `spdbccc_data` 执行挂载检查、运行、销毁和 `finish_task()` 收尾。脚本会复算最小支持人数、SPPMI、z-score、互为 top-k 和访问量型连锁商户删除等阶段，并直接打印总体摘要、分布、连通分量和重点商户指标。由于中间文件不包含商户分类和坐标，脚本不能复算分类 `2` 商户删除、地理种子边和疑似线上商户识别。pickle 文件只应来自可信任务输出。

```powershell
python scripts/analyze_pair_statistics.py
```

输入路径、图参数、访问量型商户阈值和重点商户输出数量集中定义在 `scripts/analyze_pair_statistics.py` 顶部；默认基于脚本位置读取项目根目录下的 `algorithm_one_output/pair_statistics_shanghai.pkl`，不受任务启动工作目录影响。打印的 JSON 统计覆盖全部商户，每类重点商户默认展示前 `50` 名。分析时直接校验中间文件中的原始字典，只为重点商户生成明细，孤立商户使用计数参与分布统计，避免大规模数据下复制全部商户和商户对。

可以使用独立附件邮件任务读取 Hive 结果表、写出 Excel 文件并发送附件。`scripts/send_attachment_email_task.py` 顶部的 `CONFIG` 当前从 `dev_icamp.icamp_merchant_cluster_algo_output` 读取 `dt=20260720`，只保留 `community_id` 非空且不等于空字符串的行，并写入 `/appdata/project/fid_bg_icmp/community_output.xlsx`。读表使用 `spdbccc_data.read_table("dev_icamp.icamp_merchant_cluster_algo_output", dt=["20260720"])` 的完整表名形式，不再单独传入 `db_name`；运行前应核对分区日期、文件名、收件人和抄送人。

```powershell
python scripts/send_attachment_email_task.py
```

任务严格按 `check()`、`taskrun()`、`destroy()`、`finish_task()` 顺序执行：`check()` 先完成平台挂载，再校验表名、分区、输出目录和邮箱；`taskrun()` 依次读表、拒绝空结果、写出并校验 Excel、发送邮件，读表和发信失败时均按 `maximum_attempts` 和 `retry_delay_seconds` 重试，最终失败会保留原始异常；`destroy()` 只记录清理结果，不删除生成的 Excel；无论挂载、读表、写文件或发送是否成功，最外层都会调用 `finish_task()` 完成平台收尾。

线上 Jupyter 入口和独立 Python 入口当前均不启用资源监测，不会启动 `tracemalloc` 或读取 `/proc/self/status`；资源监测模块保留供本地排查使用。

Hive 任务通过 `spdbccc_data.read_table` 普通读取 `dev_icamp.icamp_merchant_cluster_algo_param` 的 T-1 分区，再根据参数表中的 `start_date`、`end_date` 计算输入分区：起止日期相同时直接读取该日分区，例如 `start_date=end_date=20260115` 时读取 `dt=20260115`；起止日期不同时读取时间范围所涉及月份的月底分区，例如范围跨越 2026 年 1 月和 2 月时读取 `dt=20260131`、`dt=20260228`。初始化任务和增量归属任务对这些分区内的交易都只按 `region` 过滤，不再判断 `transaction_time` 是否位于 `start_date` 和 `end_date` 之间。交易时间窗口、时间衰减权重、最小交易次数和最小商户数由参数表提供，其余静态算法参数由 notebook 提供。初始化结果先通过 `spdbccc_data.write_table` 批量写入 `dev_icamp.icamp_merchant_cluster_algo_output_tmp` 临时表，再通过一条 `INSERT OVERWRITE ... SELECT` SQL 覆盖写入 `dev_icamp.icamp_merchant_cluster_algo_output` 的 T-1 整个分区；任务会在写入前后清理临时表，不保留该分区的历史行，也不再写入风险商户表。

Hive 初始化入口的交易输入表会按 `/appdata/project/fid_bg_icmp/tbl/{表名}/dt={日期}/part*` 分片读取 parquet 文件；每个分片会先按参数表的 `region` 筛选，只有命中行才参与最终合并，以降低合并时的峰值内存。`dt` 统一使用分区路径中的日期，即使分片内自带 `dt` 列也会覆盖。日期范围内缺少目录、没有 `part*` 文件或分片全部为空的分区会被跳过；如果全部日期均无有效数据，或所有分片均没有匹配 `region` 的交易，任务会明确报错。

可以使用 `scripts/write_hive_output_smoke_task.py` 验证临时表覆盖写入链路。脚本按线上 task 生命周期运行，先将 5 条 `region=write_test` 的测试记录写入 `dev_icamp.icamp_merchant_cluster_algo_output_smoke_tmp`，再通过 `INSERT OVERWRITE` 将 `dev_icamp.icamp_merchant_cluster_algo_output` 的平台 T-1 整个分区替换为这 5 条记录，最后删除临时表。执行会删除目标分区原有的全部地区数据，运行前必须确认目标环境和实际分区日期。

```bash
python scripts/write_hive_output_smoke_task.py
```

可以使用独立脚本验证挂载目录和 Hive parquet 分片读取，不运行聚类和写表逻辑。脚本会在读表成功或失败后执行 `taskfinish.finish_task()` 完成平台任务收尾。`scripts/test_hive_partition_read.py` 默认自动读取最近一个已结束月份的最后一天分区，同时可在顶部 `CONFIG` 中修改表根目录、表名、分区日期和预览行数。

```bash
python scripts/test_hive_partition_read.py
```

脚本会依次输出分区目录是否存在、`part*` 文件数量、每个分片的行列数和耗时；读取成功后输出总行数、字段列表和指定行数的数据预览，最后输出 `taskfinish_start` 和 `taskfinish_success`。脚本只依赖 `pandas`、parquet 读取引擎以及线上环境提供的 `spdbccc_data.mountCheck` 和 `spdbccc_data.task`。

Jupyter 环境可直接打开：

```text
notebooks/run_hive_business_district.ipynb
```

不使用 Jupyter 时，可在项目根目录直接运行等价的 Python 入口：

```powershell
python run_hive_business_district.py
```

Python 入口与 notebook 使用相同的静态算法参数、Hive 表和任务生命周期，也会按参数表 `is_daily` 自动选择增量归属或初始化聚类。

notebook 通过 `HiveTaskConfig` 显式传入强类型算法配置、输入表、参数表、输出表和 `dt_expression`，增量配置额外传入临时表；入口先普通读取参数表 T-1 分区，再按 `is_daily` 调度：`1` 调用增量归属，`0` 调用初始化聚类。

增量归属用于把新商户追加归入已有商圈。任务先读取参数表和输入表；输入表需要包含 `business_district` 字段。增量任务根据 notebook 中 `algorithm_config.city.code` 和 `algorithm_config.output.directory` 定位初始化聚类写出的 `pair_statistics_{region}.pkl`，文件不存在时直接报错。到访合并、图构建、图与地理投票、距离阈值和归属阈值均在 notebook 配置单元中显式设置，不再使用代码内置默认参数。

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
- `pos_longitude`：经度，可为空；非空时参与 1000 米地理种子聚类。
- `pos_latitude`：纬度，可为空；非空时参与 1000 米地理种子聚类。
- `region`：地区，输出使用该商户最新交易时间对应的值。
- `is_intefere`：输入可为空，初始化聚类忽略该字段。
- `status`：输入可为空，初始化聚类忽略该字段。
- `dt`：日期，不参与算法，输出使用该商户最新交易时间对应的值。

算法会先读取经纬度并构建地理种子：每个 `storename` 只提取一组有效数值坐标，同一商户存在多组不同有效坐标时会明确报错并给出商户名和坐标样例；随后按地理距离把有坐标商户聚成种子社区，再用交易共现 PMI/交易次数边把无坐标或未进入地理簇的商户接入这些社区。人工干预、输入状态和 `dt` 不参与算法。

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

`merchant_category` 必须是 `0`（线上）、`1`（线下）、`2`（线下连锁店）或 `3`（线下个体户）；同一 `storename` 对应多个分类会直接报错。初始化和增量任务只保留分类 `1`、`2` 的交易进入访问合并、候选边构建和聚类。分类 `2` 不进入社区发现且永远不能成为锚点，但会按正权重候选社区展开为一个或多个普通商圈成员；增量任务同样允许分类 `2` 输出多个商圈归属。`pos_longitude`、`pos_latitude`、`is_interfere`、`is_abnormal` 和 `business_district` 在输入时允许为空。`dt` 是 Hive 分区和输出字段，不要求读取结果包含该列；缺失时入口会按分区或任务日期补齐。经纬度只空一列、格式非法或越界时按无坐标处理；同一 `storename` 的有效数值经纬度必须一致，有效经纬度会参与初始化地理种子聚类。

Hive 参数表 `dev_icamp.icamp_merchant_cluster_algo_param` 必须包含：

- `start_date`
- `end_date`
- `region`
- `max_transaction_time_interval`
- `min_transaction_number`
- `min_merchant_count`
- `is_daily`

初始化入口和增量归属入口都只用参数表的 `region` 和交易表 `region` 关联，不再根据 `transaction_time`、`start_date` 和 `end_date` 筛选行。`is_daily` 取值只能是 `0` 或 `1`，其中 `1` 表示增量归属，`0` 表示初始化聚类。`max_transaction_time_interval` 映射到共现窗口分钟数，`min_transaction_number` 映射到最小支持交易人数，`min_merchant_count` 映射到有效商圈最小商户数。同一任务分区内多行参数必须使用相同算法参数，否则任务会报错。交易时间衰减参数不再从参数表读取，初始化和增量任务统一使用 notebook 中的 `algorithm_config.cooccurrence.decay_tau_minutes`。

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

其中 `community_id` 为商圈 ID，分类 `2` 的线下连锁店及访问量规则识别出的连锁/泛客群商户可保留多条普通成员挂靠记录且 `is_position` 恒为 `0`；写入 Hive 前按最终输出的 `storename` 去重统计各商圈商户数，低于参数表 `min_merchant_count` 的商圈不输出商圈 ID，并按疑似孤立商户处理；`previous_community_id` 留空，`region` 与输入表保持一致，`is_interfere` 固定为 `N`，`is_abnormal` 使用商户状态码，`update_time` 为运行时间戳，`is_position` 表示是否为锚点商户，`dt` 固定使用任务运行时计算出的 T-1 分区。

## 商户状态

Hive 目标表 `is_abnormal` 输出以下商户状态码：

- `1`：正常，商户进入有效商圈。
- `2`：疑似线上，商户自身无经纬度，且关联的多个有坐标商户中存在距离超过地理阈值的商户对。
- `3`：疑似孤立，初始化聚类时商户没有有效边或所在社区未达到配置项 `anchors.minimum_community_size` 定义的有效商圈规模；增量归属时商户没有指向存量商圈成员的有效 SPPMI 边，或图投票分数未达到归入阈值。
- `4`：疑似消逝，当前初始化聚类和增量归属入口不会产出该状态。
- `5`：疑似跨区域，当前已暂停判断，初始化聚类和增量归属均不会产出该状态。
- `6`：疑似连锁店；初始化聚类会对命中访问量规则且已挂靠有效商圈的非分类 `2` 商户输出该状态，增量归属当前不会产出该状态。
- `7`：已删除，当前初始化聚类和增量归属入口不会产出该状态。

内部算法仍使用 `normal`、`suspect_isolated`、`suspect_online` 等状态名，写入 Hive 目标表前统一转换为上述状态码。

## 测试

```powershell
python -m pytest
```

## 地理种子参数

- `[geo].cluster_radius_meters`：地理种子聚类半径，当前配置为 `1000.0` 米。每个商户只使用一组经过一致性校验的有效坐标参与地理种子聚类；地理邻居通过空间网格筛选后再计算精确球面距离。
- `[community].minimum_online_neighbor_count`：疑似线上商户至少需要关联的有坐标商户数；初始化流程使用 `[geo].cluster_radius_meters` 判断这些关联商户是否距离较远。
- 增量归属使用 `AssignmentConfig.minimum_online_neighbor_count` 和 `community_assignment_distance_meters` 执行同一判定；分类 `2` 商户不参与疑似线上识别。`city_maximum_distance_meters` 对应的疑似跨区域判断当前已暂停，配置暂不生效。

## 商户对聚类参数

`stuff/cluster_from_pair_statistics.py` 在 SPPMI 建图阶段支持以下额外参数：

- `[graph].degree_penalty_gamma`：按端点候选邻居度数惩罚 SPPMI 边权，当前建议 `0.0`，先退回原始 SPPMI，避免连锁店跨商圈边被过度压低。
- `[graph].jaccard_threshold`：边两端候选邻居集合的 Jaccard 结构门槛，当前建议 `0.0`，先保留低重叠的跨商圈边用于识别多商圈普通成员。
- `[anchors].maximum_participation`：锚点候选最大参与系数，当前建议 `0.1`，超过该阈值的非连锁商户不能成为锚点。
- `[anchors].chain_visit_count_quantile`：访问量型疑似连锁规则的分位阈值；初始化聚类使用本次全部商户访问量计算分位值。
- `[anchors].chain_minimum_visit_count`：访问量型疑似连锁规则的绝对访问次数下限；实际阈值取分位值与该值中的较大者。
