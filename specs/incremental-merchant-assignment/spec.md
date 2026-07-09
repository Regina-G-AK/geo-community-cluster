# 新商户增量归属

## Overview

- 本功能用于在初始化聚类后，将交易输入表中新出现且尚无 `community_id` 的商户追加归入已有商圈。
- 当前实现仅保留 Hive 入口，不再支持本地 CSV 增量入口、观察池、人工工单、客群分或本地档案文件。
- 增量归属复用初始化聚类写出的 `pair_statistics_{region}.pkl`，并在本次增量交易统计合并后覆盖写回该中间文件。

## Requirements

- 系统 MUST 通过 `python -m incremental_assignment.hive_task` 执行增量归属。
- 系统 MUST 从 Hive 参数表读取 `start_date`、`end_date`、`region`、交易窗口、时间衰减、最小支持人数和最小商户数参数。
- 系统 MUST 从 Hive 输入表读取交易数据，输入表 MUST 包含卡号、流水号、商户名、交易时间、经纬度、地区、日期和 `community_id`。
- 系统 MUST 将存在任意非空 `community_id` 的 `storename` 视为已有商圈成员，将全部为空的 `storename` 视为待归属新商户。
- 同一 `storename` 对应多个非空 `community_id` 时，系统 MUST 跳过该商户的存量投票成员身份，且不得将其作为新商户输出。
- 同一 `storename` 只要任意输入行 `is_interfere=Y`，系统 MUST 将该商户整组交易排除在增量统计、候选归属和输出之外。
- 系统 MUST 使用增量交易更新 `pair_statistics_{region}.pkl`，已有边保留原 `support` 并累加 `strength`，新边写入本次 `support` 和 `strength`。
- 系统 MUST 基于更新后的商户图，对新商户连接到的已有商圈成员按边权进行图投票。
- 系统 MUST 使用输入表中有效经纬度，计算新商户到已有商圈成员商户的地理距离。
- 有经纬度的新商户最近距离超过 50000 米时，系统 MUST 判定为疑似跨区域，写出 `is_abnormal=5`，且不得继续执行图投票归属。
- 有经纬度的新商户最近距离大于 3000 米且不超过 50000 米时，系统 MUST 判定为疑似孤立，写出 `is_abnormal=3`，且不得继续执行图投票归属。
- 有经纬度的新商户最近距离不超过 3000 米时，系统 MUST 对距离不超过 3000 米的商圈成员进行地理投票。
- 系统 MUST 将图投票和地理投票按权重融合；只有一类分数可用时，系统 MUST 只使用可用分数。
- 系统 MUST 在最高融合分数不低于 `theta=0.55` 且与次高分差值不低于 `delta=0.10` 时写出正常归属。
- 不满足归属条件的新商户 MUST 写出空 `community_id` 和 `is_abnormal=3`。
- 归属成功的新商户 MUST 写出目标商圈 ID 和 `is_abnormal=1`。
- 系统 MUST 通过临时表向 `dev_icamp.icamp_merchant_cluster_algo_output` 追加写入，不得读取目标表做去重。
- 系统 MUST 对缺失必需字段、参数异常、中间文件缺失和临时表清理失败抛出明确错误。

## Design

- `incremental_assignment.hive_task` 是唯一运行入口，负责读取 Hive 表、过滤参数日期范围、转换交易、更新中间图统计、构建增量输出并写入 Hive。
- `incremental_assignment.models` 只保留 Hive 入口需要的 `AssignmentConfig`。
- 图投票使用更新后的稀疏图，取新商户 top-k 邻居中已有商圈成员的边权，并按商圈归一化。
- 地理投票使用每个商户最新一条有效经纬度，按 haversine 距离与已有商圈成员比对，距离越近投票权重越高。
- 分数融合只使用图分和地理分，不引入客群分、观察期阈值、人工审核状态或锚点票权放大。
- 输出字段沿用目标 Hive 表结构：`storename`、`community_id`、`previous_community_id`、`region`、`is_interfere`、`update_time`、`is_abnormal`、`is_position`、`dt`，其中 `is_interfere` 固定写出 `N`。
