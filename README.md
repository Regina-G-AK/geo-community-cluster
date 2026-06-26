# 商圈初始化聚类

本项目实现基于交易共现关系的商圈初始化聚类。主流程直接读取原始交易数据，在内存中构建商户对、商户图和社区结果，输出业务结果表，并在同一次运行目录保留可复用的商户对中间文件。

## 运行

```powershell
python -m business_district --config configs/shanghai.ini
```

每次运行会在 `[output].directory` 下创建 `{edge_weight_method}_{community_algorithm}_{YYMMDDHHMMSS}` 格式的独立目录，避免覆盖历史结果。成功落盘后会向 `[experiments].path` 追加实验记录。

Hive 环境可使用旧线上任务风格入口：

```powershell
python -m business_district.hive_task
```

该入口读取 `dev_icamp.icamp_merchant_cluster_algo_input` 的 T-1 分区，使用 `configs/shanghai.ini` 中的算法参数运行聚类，并按旧逻辑通过临时表覆盖写入 `dev_icamp.icamp_merchant_cluster_algo_output` 的对应 `dt` 分区。当前算法不生成风险商户结果，`dev_icamp.icamp_merchant_cluster_algo_risk` 保留空结果跳过写入。

Jupyter 环境可直接打开：

```text
notebooks/run_hive_business_district.ipynb
```

notebook 通过 `HiveTaskConfig` 显式传入配置路径、输入表、输出表、临时表和 `dt_expression`，再调用 `run_hive_task(task_config)`，不复制算法逻辑。

## 输入

交易文件需要包含表头，字段如下：

- `account_number`：卡号，用于按持卡人构建共现。
- `global_flow_number`：流水号，必须非空且唯一。
- `storename`：商户名称，用作聚类商户 ID。
- `transaction_time`：交易时间，按 `[input].timestamp_formats` 解析。
- `pos_longitude`：经度，可为空，不参与初始化聚类。
- `pos_latitude`：纬度，可为空，不参与初始化聚类。
- `region`：地区，输出使用该商户最新交易时间对应的值。
- `is_intefere`：输入可为空，初始化聚类忽略该字段。
- `status`：输入可为空，初始化聚类忽略该字段。
- `dt`：日期，不参与算法，输出使用该商户最新交易时间对应的值。

商户对构建只使用 `account_number`、`storename` 和 `transaction_time`。经纬度、人工干预、输入状态和 `dt` 不参与算法。

## 输出

主流程输出业务结果表：

```text
business_district.csv
```

字段如下：

- `storename`
- `primary_community_id`
- `community_id`
- `previous_community_id`
- `region`
- `is_interfere`
- `update_time`
- `status`
- `is_position`
- `community_share`
- `is_primary_community`
- `is_multi_community_member`
- `is_chain_like`
- `chain_reason`
- `chain_visit_count_threshold`
- `connected_community_count`
- `dt`

初始化聚类输出规则：

- `previous_community_id` 固定为空。
- `is_interfere` 固定为 `0`。
- `update_time` 格式为 `%Y-%m-%d %H:%M:%S`。
- `primary_community_id` 表示主社区，`community_id` 表示本行挂靠社区；疑似线上、疑似孤立商户为空。
- `is_position` 表示是否为主社区锚点商户。
- `community_share` 表示商户挂靠到本行商圈的边权占比。
- `is_primary_community` 表示本行是否为商户主社区。
- `is_multi_community_member` 表示商户是否被展开到多个商圈。
- `is_chain_like` 表示是否命中连锁/泛客群规则。
- `chain_reason` 为空、`participation`、`visit_count` 或 `participation|visit_count`。
- `chain_visit_count_threshold` 是本次运行访问量规则使用的阈值。
- `connected_community_count` 是商户在最终清洗图中连接到的社区数。

同一运行目录还会写入商户对中间文件：

```text
pair_statistics.sqlite3
```

该文件包含 `merchant_pairs` 和 `merchant_visits` 两张表，用于复用商户对统计结果重新执行后续聚类实验。文件与业务 CSV 都保留在本地运行目录，不在当前包内写入 Hive。

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
- `dt`

`pos_longitude`、`pos_latitude`、`is_interfere` 和 `is_abnormal` 在输入时允许为空，不参与当前聚类逻辑。

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

其中 `community_id` 为商圈 ID，连锁/泛客群多商圈商户会保留多条挂靠记录；`previous_community_id` 留空，`region` 与输入表保持一致，`is_interfere` 固定为 `0`，`update_time` 为运行时间戳，`is_abnormal` 使用当前算法状态，`is_position` 表示是否为锚点商户，`dt` 与输入表保持一致。

## 商户状态

初始化聚类只输出以下状态：

- `normal`：商户进入有效商圈。
- `suspect_isolated`：商户没有有效边，或所在社区未达到有效商圈规模。
- `suspect_online`：迭代 hub 清洗阶段识别出的高参与度 hub 商户。

`suspect_lost` 不在初始化聚类中输出。

## 测试

```powershell
python -m pytest
```

## 商户对聚类参数

`stuff/cluster_from_pair_statistics.py` 在 SPPMI 建图阶段支持以下额外参数：

- `[graph].degree_penalty_gamma`：按端点候选邻居度数惩罚 SPPMI 边权，当前建议 `0.0`，先退回原始 SPPMI，避免连锁店跨商圈边被过度压低。
- `[graph].jaccard_threshold`：边两端候选邻居集合的 Jaccard 结构门槛，当前建议 `0.0`，先保留低重叠的跨商圈边用于识别多商圈普通成员。
- `[anchors].maximum_participation`：锚点候选最大参与系数，当前建议 `0.1`，超过该阈值的商户不能成为锚点，并会作为普通成员按连接社区展开到多个商圈。
- `[anchors].chain_visit_count_quantile`：访问量型连锁/泛客群规则的分位阈值，当前建议 `0.9`，即取访问量最高的约 10% 商户。
- `[anchors].chain_minimum_visit_count`：访问量型规则的绝对访问次数下限，当前建议 `100`。
