# 商圈初始化聚类

本项目实现基于交易共现关系的商圈初始化聚类。主流程直接读取原始交易数据，在内存中构建商户对、商户图和社区结果；本地运行目录只保留可复用的商户对中间文件，Hive 入口负责写入商圈业务结果表。

## 运行

```powershell
python -m business_district --config configs/shanghai.ini
```

每次运行会直接在 `[output].directory` 下写入 `pair_statistics_{region}.pkl` 商户对中间文件，其中 `region` 使用 `[city].code`。

本地入口会在运行结束后输出资源监测结果：`elapsed_seconds` 表示总耗时，`python_memory_current_mb` 和 `python_memory_peak_mb` 表示 Python 已跟踪内存，`python_memory_peak_phase` 表示 Python 内存峰值出现的阶段；`process_memory_current_mb` 和 `process_memory_peak_mb` 表示进程 RSS 当前值和监控打点中的最高值，`process_memory_peak_phase` 表示进程内存峰值所在阶段。生产 Linux 环境中优先使用 `process_memory_peak_mb` 和 `process_memory_peak_phase` 估算当前配置和数据量需要的内存资源；不支持进程口径的平台会显示 `unavailable`。

Hive 环境可使用旧线上任务风格入口：

```powershell
python -m business_district.hive_task
```

该入口通过 `spdbccc_data.read_table` 普通读取 `dev_icamp.icamp_merchant_cluster_algo_param` 的 T-1 分区，再按参数表中的 `start_date`、`end_date` 和 `region` 读取并过滤 `dev_icamp.icamp_merchant_cluster_algo_input` 对应日期分区。`configs/shanghai.ini` 只保留静态算法配置，交易时间窗口、时间衰减权重、最小交易次数和最小商户数由参数表提供。结果通过临时表覆盖写入 `dev_icamp.icamp_merchant_cluster_algo_output` 的对应 `dt` 分区，不再写入风险商户表。

Hive 初始化入口的交易输入表会按 `/appdata/project/yw061178/tbl/{表名}/dt={日期}/part*` 分片读取 parquet 文件并合并；当分片数据缺少 `dt` 列时会按分区日期自动补齐。

Jupyter 环境可直接打开：

```text
notebooks/run_hive_business_district.ipynb
```

notebook 通过 `HiveTaskConfig` 显式传入配置路径、输入表、参数表、输出表、临时表和 `dt_expression`，先普通读取参数表 T-1 分区，再按 `is_daily` 调度入口：`1` 调用增量归属，`0` 调用初始化聚类。

增量归属 Hive 入口用于把新商户追加归入已有商圈：

```powershell
python -m incremental_assignment.hive_task
```

该入口先读取 `dev_icamp.icamp_merchant_cluster_algo_param` 的 T-1 分区，再按参数表中的 `start_date`、`end_date` 和 `region` 读取并过滤 `dev_icamp.icamp_merchant_cluster_algo_input` 对应日期分区。输入表需要包含 `business_district` 字段，`dt` 作为 Hive 分区字段不要求出现在读取结果中，缺失时会用当前任务分区补齐：同一 `storename` 只要存在任意非空 `business_district`，即视为已在商圈中；全部为空的商户才作为本次待归属新商户。同一 `storename` 对应多个非空 `business_district` 时，该商户不参与增量投票成员计算，也不会作为新商户输出；同一 `storename` 只要任意输入行 `is_interfere=Y`，该商户整组交易不参与增量统计、候选归属或输出。增量入口会读取 `configs/shanghai.ini` 中 `[city].code` 和 `[output].directory` 定位初始化聚类写出的 `pair_statistics_{region}.pkl`，文件不存在时直接报错；本次增量交易先形成新的商户对统计，再合并到该中间文件并立即覆盖写回。合并时已有边 `support` 保持不变、`strength` 累加；新边 `support` 使用本次增量统计值、`strength` 累加；`merchant_visit_counts` 累加。结果通过临时表加 `insert into table` 追加写入 `dev_icamp.icamp_merchant_cluster_algo_output`，写出过程不再读取目标表做去重。交易共现窗口、时间衰减和最小支持人数分别来自参数表的 `max_transaction_time_interval`、`transaction_time_interval_weight` 和 `min_transaction_number`。参数表没有提供但增量计算需要的参数仍使用代码内置默认值：30 分钟到访合并、SPPMI、互为 top-k=10、最小 z-score=0.5、图投票权重 0.6、地理投票权重 0.3、地理匹配距离不超过 3000 米、跨区域距离阈值 50000 米、最高融合分数不低于 0.55、与次高商圈分数差值不低于 0.10。有经纬度的新商户会先与已有商圈成员商户比对最近距离：最近距离超过 50000 米时输出 `is_abnormal=5` 且不再图投票；最近距离大于 3000 米且不超过 50000 米时输出 `is_abnormal=3` 且不再图投票；最近距离不超过 3000 米时，再融合图投票和地理投票。缺少经纬度时只使用图投票，缺少图边时可只使用地理投票；不满足归属阈值时输出 `is_abnormal=3` 且商圈 ID 为空。

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

算法会先读取经纬度并构建地理种子：同一 `storename` 的有坐标交易如果相距超过 `[geo].cluster_radius_meters`，会在内部拆成多个门店实体；随后按地理距离把有坐标门店聚成种子社区，再用交易共现 PMI/交易次数边把无坐标或未进入地理簇的商户接入这些社区。人工干预、输入状态和 `dt` 不参与算法。

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
- `transaction_time`
- `pos_longitude`
- `pos_latitude`
- `region`
- `is_interfere`
- `is_abnormal`
- `business_district`

`pos_longitude`、`pos_latitude`、`is_interfere`、`is_abnormal` 和 `business_district` 在输入时允许为空。`dt` 是 Hive 分区和输出字段，不要求读取结果包含该列；缺失时入口会按分区或任务日期补齐。经纬度只空一列、格式非法或越界时按无坐标处理；有效经纬度会参与初始化地理种子聚类，输出字段保持不变，不额外暴露内部拆分门店 ID。

Hive 参数表 `dev_icamp.icamp_merchant_cluster_algo_param` 必须包含：

- `start_date`
- `end_date`
- `region`
- `max_transaction_time_interval`
- `transaction_time_interval_weight`
- `min_transaction_number`
- `min_merchant_count`
- `is_daily`

入口会用参数表的 `region` 和交易表 `region` 关联，并用 `transaction_time` 落在 `[start_date, end_date]` 的记录作为本次算法输入。`is_daily` 取值只能是 `0` 或 `1`，其中 `1` 表示增量归属，`0` 表示初始化聚类。`max_transaction_time_interval` 映射到共现窗口分钟数，`transaction_time_interval_weight` 映射到交易时间衰减参数，`min_transaction_number` 映射到最小支持交易人数，`min_merchant_count` 映射到有效商圈最小商户数。同一任务分区内多行参数必须使用相同算法参数，否则任务会报错。

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

其中 `community_id` 为商圈 ID，连锁/泛客群多商圈商户会保留多条挂靠记录；`previous_community_id` 留空，`region` 与输入表保持一致，`is_interfere` 固定为 `N`，`update_time` 为运行时间戳，`is_abnormal` 使用商户状态码，`is_position` 表示是否为锚点商户，`dt` 使用输入分区或当前任务分区。

## 商户状态

Hive 目标表 `is_abnormal` 输出以下商户状态码：

- `1`：正常，商户进入有效商圈。
- `2`：疑似线上，迭代 hub 清洗阶段识别出的高参与度 hub 商户。
- `3`：疑似孤立，初始化聚类时商户没有有效边或所在社区未达到配置项 `anchors.minimum_community_size` 定义的有效商圈规模；增量归属时商户没有指向存量商圈成员的有效 SPPMI 边，或图投票分数未达到归入阈值。
- `4`：疑似消逝，当前初始化聚类和增量归属入口不会产出该状态。
- `5`：疑似跨区域，增量归属时有经纬度的新商户与已有商圈成员商户最近距离超过跨区域距离阈值时产出。
- `6`：疑似连锁店，初始化聚类识别出连锁/泛客群商户且仍挂靠有效商圈时产出。
- `7`：已删除，当前初始化聚类和增量归属入口不会产出该状态。

内部算法仍使用 `normal`、`suspect_isolated`、`suspect_online` 等状态名，写入 Hive 目标表前统一转换为上述状态码。

## 测试

```powershell
python -m pytest
```

## 地理种子参数

- `[geo].cluster_radius_meters`：地理种子聚类半径，当前配置为 `1000.0` 米。同名商户的有坐标交易会先按该半径拆成内部门店实体，再参与全局地理种子聚类。

## 商户对聚类参数

`stuff/cluster_from_pair_statistics.py` 在 SPPMI 建图阶段支持以下额外参数：

- `[graph].degree_penalty_gamma`：按端点候选邻居度数惩罚 SPPMI 边权，当前建议 `0.0`，先退回原始 SPPMI，避免连锁店跨商圈边被过度压低。
- `[graph].jaccard_threshold`：边两端候选邻居集合的 Jaccard 结构门槛，当前建议 `0.0`，先保留低重叠的跨商圈边用于识别多商圈普通成员。
- `[anchors].maximum_participation`：锚点候选最大参与系数，当前建议 `0.1`，超过该阈值的非连锁商户不能成为锚点。
- `[anchors].chain_visit_count_quantile`：聚类前访问量型连锁/泛客群规则的分位阈值，当前建议 `0.9`，即取访问量最高的约 10% 商户。
- `[anchors].chain_minimum_visit_count`：聚类前访问量型规则的绝对访问次数下限，当前建议 `100`。
