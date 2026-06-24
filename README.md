# 商圈聚类

本项目实现 `docs/README.md` 中的算法一：根据持卡人短时间内的商户到访共现，构建商户关系图并生成初始商圈和候选锚点。

## 运行

```powershell
python -m business_district --config configs/shanghai.ini
```

所有城市相关信息、输入文件和算法参数均由 INI 风格配置提供，使用 Python 标准库 `configparser` 读取。新增城市时复制 `configs/shanghai.ini`，修改城市标识、输入文件和输出根目录，不需要修改算法代码。每次运行会在输出根目录下创建 `{edge_weight_method}_{community_algorithm}_{YYMMDDHHMMSS}` 格式的独立目录，已有目录不会被覆盖，失败运行的目录会保留以便排查。

当前交易数据使用无表头 `|` 分隔文本，每行格式为 `卡号|流水单号|店名|时间戳|||地区|||`。程序使用卡号、店名和时间戳构建到访与商户边，店名会作为输出中的 `merchant_id`。因此本版实现以下可落地流程：

- 严格校验并清洗交易数据
- 合并同卡同商户的短时重复交易
- 剔除单日到访商户数异常的卡
- 用户级共现贡献封顶和时间衰减
- 可配置 SPPMI 或原始共同交易强度作为边权重，并执行互为 top-k 近邻建图
- Leiden 社区发现和高参与度 hub 迭代清洗
- 输出前删除少于 3 个商户的商圈，并同步裁剪商户、边和图文件
- 基于社区内 PageRank 和参与系数的候选锚点评分
- 输出商户、商圈、边、图文件和运行摘要

方案中的城市分布熵、MCC/渠道规则、退款冲正、跨期稳定性和坐标校验需要额外输入字段。缺少这些数据时程序不会伪造对应结果。

## 测试

```powershell
python -m pytest
```

## 新商户增量归属

算法二的增量归属代码放在独立包 `incremental_assignment` 中，不放入 `business_district` 目录。示例配置为：

```powershell
python -m incremental_assignment --config configs/incremental_shanghai.ini
```

待判定商户输入表为 CSV，必须包含以下字段：

- `city_code`
- `merchant_id`
- `first_seen_at`
- `last_seen_at`
- `unique_user_count`
- `customer_ids`，多个持卡人 ID 使用 `|` 分隔

可选字段：

- `latitude`
- `longitude`
- `hourly_profile`，24 个小时桶计数使用 `|` 分隔

程序读取算法一输出目录中的 `merchants.csv`、`communities.csv`、`edges.csv` 和 `summary.json`，复用 `edges.csv` 中的 SPPMI 边权进行图投票。观察池、商户档案和商圈档案先写入本地 CSV；每次成功运行会创建独立输出目录，并写出 `decisions.csv`、`manual_review.csv`、`observation_pool.csv`、`merchant_archive.csv`、`community_archive.csv` 和 `summary.json`。增量运行记录写入 `docs/incremental_experiments.md`。

初始阈值为 `min_observation_days=28`、`min_unique_users=5`、`theta=0.55`、`delta=0.10`。地理分数已实现；当新商户或商圈锚点缺少可用坐标时，地理权重会按比例分摊给图分数和客群分数。

## 边权重计算方式

`[graph].edge_weight_method` 支持以下值：

- `sppmi`：默认方式，执行 CDS 平滑、SPPMI 和显著性过滤。
- `transaction_count`：保留最小支持人数过滤，直接使用原始商户对阶段的 `strength` 作为边权重，不计算 SPPMI 和 z-score，也不执行对应阈值过滤。

两种方式共用互为 top-k、迭代 hub 清洗、Leiden 社区发现和有效社区过滤。`edges.csv` 始终包含 `sppmi` 和 `z_score` 字段；`transaction_count` 模式下这两个字段为空。

## 商户对中间数据

可单独生成商户对统计文件，后续建图和性能测试可直接读取该文件，避免重复清洗和扫描原始交易：

```powershell
python -m business_district.pair_cli --config configs/shanghai.ini --output algorithm_one_output/pair_statistics.sqlite3
```

SQLite 文件包含 `merchant_pairs` 和 `merchant_visits` 两张表。前者保存商户对强度和支持用户数，后者保留所有商户的到访次数，包括没有形成商户对的孤立商户。

可直接读取中间文件执行建图和聚类，不重复处理原始交易：

```powershell
python -m business_district.cached_cluster_cli --config configs/shanghai.ini --pairs algorithm_one_output/pair_statistics.sqlite3 --output algorithm_one_output
```

该入口不读取到访明细，因此基础社区结果不包含高峰小时和小时一致性字段。

输出结果只保留至少包含 3 个商户的商圈；少于 3 个商户的商圈不会写入 `communities.csv`，对应商户也不会写入 `merchants.csv`。社区统计中，`community_count_ge_3` 表示至少包含 3 个商户的有效社区，`community_count_ge_10` 表示至少包含 10 个商户的较大社区。锚点只从达到 `anchors.minimum_community_size` 的社区中选择，每个社区的锚点数量不超过 `anchors.maximum_count`。

聚类结果成功写出后，程序会自动向 `[experiments].path` 指定的 Markdown 文件追加实验记录，包括实际输出目录、完整参数、边过滤漏斗、社区规模、有效商户覆盖率、锚点约束、清洗轮数、耗时和主要损失阶段分析。`transaction_count` 模式会将 SPPMI 阶段标记为已跳过，并在最大损失阶段分析中排除该阶段。
