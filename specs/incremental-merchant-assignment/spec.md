# 新商户增量归属

## Overview

- 本功能用于在初始化聚类后，将交易输入表中新出现且尚无 `community_id` 的商户归入已有商圈，并写出本次有效输入商户的完整快照。
- 当前实现仅保留 Hive 入口，不再支持本地 CSV 增量入口、观察池、人工工单、客群分或本地档案文件。
- 增量归属根据本次有效输入重新计算 `pair_statistics_{region}.pkl`，不复用历史统计。

## Requirements

- 系统 MUST 通过 `python run_hive_business_district.py` 执行任务，并由入口根据 `is_daily` 选择增量归属。
- 系统 MUST 从 Hive 参数表读取 `start_date`、`end_date`、`region`、交易窗口、时间衰减、最小支持人数和最小商户数参数。
- 系统 MUST 从 Hive 输入表读取交易数据，输入表 MUST 包含卡号、流水号、商户名、商户分类、交易时间、经纬度、地区、干预状态、异常状态和 `business_district`。
- 系统 MUST 在参数地区匹配和跨区域拆分之前只保留 `is_abnormal=1`、空字符串或空值的交易，其他状态不得参与统计、归属或输出。
- 系统 MUST 将 `is_abnormal=1` 的商户视为已有商圈商户，并要求其具有唯一非空 `business_district`。
- 系统 MUST 将空状态商户视为待增量添加商户，并要求其 `business_district` 为空。
- 同一 `storename` 同时存在 `is_abnormal=1` 和空状态时，系统 MUST 明确报错。
- `is_abnormal=1` 的已有商户 MUST 优先保留原商圈且不得被改写为跨区域状态；只有空状态商户参与跨区域分类。
- 同一 `storename` 对应多个非空 `business_district` 时，系统 MUST 明确报错。
- 同一 `storename` 只要任意输入行 `is_interfere=Y`，系统 MUST 将该商户整组交易排除在增量统计、候选归属和输出之外。
- 系统 MUST 使用本次有效输入重新计算完整商户对统计并覆盖 `pair_statistics_{region}.pkl`，不得读取或合并历史中间文件。
- 系统 MUST 使用输入表中每个商户最新一条有效经纬度，计算新商户到已有商圈成员的地理距离。
- 有有效经纬度的新商户 MUST 直接继承配置半径内距离最近的已有商圈成员 ID，不得执行地理投票或 PMI 归属。
- 有有效经纬度但配置半径内没有已有商圈成员的新商户 MUST 写出空 `community_id` 和 `is_abnormal=3`。
- 没有有效经纬度的新商户 MUST 直接继承正 PMI/SPPMI 边权最大的已有商圈邻居 ID，不得按商圈累计边权或执行投票。
- 没有有效经纬度且没有指向已有商圈成员的正 PMI/SPPMI 边时，系统 MUST 写出空 `community_id` 和 `is_abnormal=3`。
- 距离或 PMI/SPPMI 边权相同时，系统 MUST 按商圈 ID 和已有成员名稳定排序，保证结果可复现。
- 分类 `2` 的待归属商户 MUST 与其他分类一样只输出一个已有商圈 ID。
- 归属成功的新商户 MUST 写出目标商圈 ID 和 `is_abnormal=1`。
- 已有商圈商户 MUST 原样保留输入 `business_district`，并写出 `is_abnormal=1` 和 `is_position=0`。
- 系统 MUST 保证每个有效输入商户恰好对应一条增量结果。
- 系统 MUST 通过临时表覆盖写入 `dev_icamp.icamp_merchant_cluster_algo_output` 的目标分区。
- 系统 MUST 对缺失必需字段、参数异常、中间文件缺失和临时表清理失败抛出明确错误。

## Design

- `run_hive_business_district.py` 是唯一主入口，负责构造配置并根据 `is_daily` 调度；`incremental_assignment.hive_task` 负责读取 Hive 表、过滤参数日期范围、转换交易、重算中间图统计、合并新商户归属与已有商户结果并覆盖写入 Hive。
- 增量状态和人工干预预筛选之后的 DataFrame 格式校验与转换 MUST 复用初始化入口的 `business_district.transactions.load_hive_transactions`。
- `incremental_assignment.models` 只保留 Hive 入口需要的 `AssignmentConfig`。
- 地理直接归属使用 haversine 距离选择配置半径内最近的已有商圈成员。
- PMI 直接归属只在新商户没有有效经纬度时执行，并选择更新后稀疏图中正边权最大的已有商圈邻居。
- `AssignmentConfig` 只保留 `community_assignment_distance_meters`。
- 输出字段沿用目标 Hive 表结构：`storename`、`community_id`、`previous_community_id`、`region`、`is_interfere`、`update_time`、`is_abnormal`、`is_position`、`dt`，其中 `is_interfere` 固定写出 `N`。
