# 新商户增量归属

## Overview

- 本功能用于在初始化聚类后，将交易输入表中新出现且尚无 `community_id` 的商户追加归入已有商圈。
- 当前实现仅保留 Hive 入口，不再支持本地 CSV 增量入口、观察池、人工工单、客群分或本地档案文件。
- 增量归属复用初始化聚类写出的 `pair_statistics_{region}.pkl`，并在本次增量交易统计合并后覆盖写回该中间文件。

## Requirements

- 系统 MUST 通过 `python -m incremental_assignment.hive_task` 执行增量归属。
- 系统 MUST 从 Hive 参数表读取 `start_date`、`end_date`、`region`、交易窗口、时间衰减、最小支持人数和最小商户数参数。
- 系统 MUST 从 Hive 输入表读取交易数据，输入表 MUST 包含卡号、流水号、商户名、商户分类、交易时间、经纬度、地区、干预状态、异常状态和 `business_district`。
- 系统 MUST 将存在任意非空 `business_district` 的 `storename` 视为已有商圈成员，将全部为空的 `storename` 视为待归属新商户。
- 同一 `storename` 对应多个非空 `business_district` 时，系统 MUST 跳过该商户的已有商圈成员身份，且不得将其作为新商户输出。
- 同一 `storename` 只要任意输入行 `is_interfere=Y`，系统 MUST 将该商户整组交易排除在增量统计、候选归属和输出之外。
- 系统 MUST 使用增量交易更新 `pair_statistics_{region}.pkl`，已有边保留原 `support` 并累加 `strength`，新边写入本次 `support` 和 `strength`。
- 系统 MUST 使用输入表中每个商户最新一条有效经纬度，计算新商户到已有商圈成员的地理距离。
- 有有效经纬度的新商户 MUST 直接继承配置半径内距离最近的已有商圈成员 ID，不得执行地理投票或 PMI 归属。
- 有有效经纬度但配置半径内没有已有商圈成员的新商户 MUST 写出空 `community_id` 和 `is_abnormal=3`。
- 没有有效经纬度的新商户 MUST 直接继承正 PMI/SPPMI 边权最大的已有商圈邻居 ID，不得按商圈累计边权或执行投票。
- 没有有效经纬度且没有指向已有商圈成员的正 PMI/SPPMI 边时，系统 MUST 写出空 `community_id` 和 `is_abnormal=3`。
- 距离或 PMI/SPPMI 边权相同时，系统 MUST 按商圈 ID 和已有成员名稳定排序，保证结果可复现。
- 分类 `2` 的待归属商户 MUST 与其他分类一样只输出一个已有商圈 ID。
- 归属成功的新商户 MUST 写出目标商圈 ID 和 `is_abnormal=1`。
- 系统 MUST 通过临时表向 `dev_icamp.icamp_merchant_cluster_algo_output` 追加写入，不得读取目标表做去重。
- 系统 MUST 对缺失必需字段、参数异常、中间文件缺失和临时表清理失败抛出明确错误。

## Design

- `incremental_assignment.hive_task` 是唯一运行入口，负责读取 Hive 表、过滤参数日期范围、转换交易、更新中间图统计、构建增量输出并写入 Hive。
- `incremental_assignment.models` 只保留 Hive 入口需要的 `AssignmentConfig`。
- 地理直接归属使用 haversine 距离选择配置半径内最近的已有商圈成员。
- PMI 直接归属只在新商户没有有效经纬度时执行，并选择更新后稀疏图中正边权最大的已有商圈邻居。
- `AssignmentConfig` 只保留 `community_assignment_distance_meters`。
- 输出字段沿用目标 Hive 表结构：`storename`、`community_id`、`previous_community_id`、`region`、`is_interfere`、`update_time`、`is_abnormal`、`is_position`、`dt`，其中 `is_interfere` 固定写出 `N`。
