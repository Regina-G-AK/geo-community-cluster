# 连锁/泛客群商户识别

## Overview

- 本功能用于在商圈初始化聚类结果中识别不适合作为锚点的连锁/泛客群商户。
- 识别结果不依赖商户名称、品牌词或分店名解析，只使用图结构参与系数和商户访问量。
- 命中的商户应保留为普通成员；当候选边证据连接到多个最终社区时，应同时挂靠多个商圈。

## Requirements

- 系统 MUST 为每个商户输出 `is_chain_like`，表示是否命中连锁/泛客群规则。
- 系统 MUST 为每个命中商户输出 `chain_reason`，取值 MUST 能区分 `participation`、`visit_count` 和 `participation|visit_count`。
- 系统 MUST 使用参与系数规则识别跨社区边分散商户：当 `participation > anchors.maximum_participation` 时，商户 MUST 被标记为 `is_chain_like=1`。
- 系统 MUST 使用访问量规则识别高访问量商户：当 `visit_count >= max(访问量分位阈值, anchors.chain_minimum_visit_count)` 时，商户 MUST 被标记为 `is_chain_like=1`。
- 访问量分位阈值 MUST 由本次结果中的商户 `visit_count` 按 `anchors.chain_visit_count_quantile` 计算；`0.9` 表示约访问量前 10% 的商户。
- 访问量规则 MUST 直接参与 OR 判断，不得要求商户先连接到多个社区。
- `is_chain_like=1` 的商户 MUST 不得成为锚点候选。
- 多商圈挂靠 MUST 使用候选边对最终社区投票，而不是只依赖清洗后的最终图边。
- 候选边投票 MUST 将候选边另一端商户的最终 `community_id` 作为投票社区，候选边权作为投票权重。
- 候选边投票 MUST 归一化为 `community_share`，用于解释商户挂靠到各商圈的权重占比。
- 当 `is_chain_like=1` 商户的候选边投票覆盖多个最终社区时，系统 MUST 为该商户输出多条成员记录，并设置 `is_multi_community_member=1`。
- 当 `is_chain_like=1` 商户的候选边投票只覆盖一个最终社区时，系统 MUST 保留其单社区普通成员记录，并设置 `is_multi_community_member=0`。
- 普通商户的多商圈展开 MUST 继续使用最终清洗图的社区边权分布，不得被访问量规则影响。
- 系统 MUST 输出 `chain_visit_count_threshold` 和 `connected_community_count` 作为排查字段。
- 运行摘要 MUST 记录连锁/泛客群商户总数，以及由访问量规则命中的商户数量。

## Design

- 在 `AnchorConfig` 中保留 `maximum_participation`、`chain_visit_count_quantile` 和 `chain_minimum_visit_count` 三个参数。
- 在商户结果构建阶段先计算最终图参与系数、最终图社区权重占比和商户访问量分位阈值。
- 使用 `is_chain_like = high_participation OR high_visit_count` 生成连锁/泛客群标记。
- 锚点候选筛选仅从 `is_chain_like=0` 的商户中选择。
- 在聚类运行阶段根据完整候选边和最终社区分区构建 `candidate_community_shares`。
- 在成员展开阶段，`is_chain_like=1` 商户优先使用 `candidate_community_shares`；其他商户使用最终图社区权重占比。
- 输出 CSV 保留主社区、挂靠社区、社区权重占比、是否主社区、是否多商圈成员、连锁/泛客群标记和命中原因。
- 本功能不实现品牌名归一化、门店名解析或外部连锁品牌库匹配。
