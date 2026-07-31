# 商户边权重计算方式

## Overview

- 为商圈聚类新增基于共同交易次数的商户边权重计算方式，并与现有 SPPMI 方式并行存在。
- 通过统一配置选择边权重计算方式，默认保持现有 SPPMI 行为。
- 统一聚类运行输出目录命名，使计算方式、社区算法和运行时间可直接识别。

## Requirements

- 系统 MUST 提供 `edge_weight_method` 配置，允许值为 `sppmi` 和 `transaction_count`，默认值为 `sppmi`。
- 所有商圈聚类运行入口 MUST 支持 `edge_weight_method`，包括命令行、配置文件和程序调用。
- `sppmi` 模式 MUST 保持现有边权重计算和过滤行为。
- `transaction_count` 模式 MUST 直接使用原始商户对阶段已有的共同交易次数字段作为边权重。
- `transaction_count` 模式 MUST 保留现有最小支持人数过滤。
- `transaction_count` 模式 MUST 完全跳过 SPPMI 计算及 SPPMI 阈值过滤。
- `transaction_count` 模式 MUST 使用未经归一化、对数变换或其他缩放的原始共同交易次数。
- 两种模式 MUST 继续使用现有互为 top-k、迭代 hub 清洗、社区发现和有效社区规模过滤流程。
- 边结果 MUST 保持现有字段结构；`transaction_count` 模式的 SPPMI 字段 MUST 使用标准空值，CSV 写为空单元格，Parquet 和内存数据写为 `null`，不得写为字符串或数值零。
- 完整参数快照 MUST 保留 SPPMI 相关参数；`transaction_count` 模式 MUST 明确标记这些参数未生效。
- 输出目录配置 MUST 表示输出根目录，系统 MUST 在其下创建 `{edge_weight_method}_{community_algorithm}_{YYMMDDHHMMSS}` 格式的运行目录。
- 输出目录时间戳 MUST 使用运行机器本地时区的本次运行开始时间。
- 目录名中的社区算法标识 MUST 来自配置并统一转为小写；标识包含不适合目录名的字符时 MUST 抛出明确的配置错误，不得自动改写。
- 目标运行目录已存在时 MUST 明确报错，不得覆盖。
- 运行失败时 MUST 保留已创建的运行目录及部分结果以供排查。
- 参数实验 MUST 使用新的自动命名运行目录并保留历史结果。
- README 和测试 MUST 随实现同步更新。

## Design

- 在统一运行配置模型中增加严格校验的 `edge_weight_method` 枚举，并由所有入口映射到同一配置模型。
- 在最小支持人数过滤后按配置选择单一边权来源：`sppmi` 使用现有计算结果，`transaction_count` 使用原始商户对的共同交易次数。
- 后续图过滤和社区算法统一消费选定的边权重，避免复制聚类流程。
- 输出根目录解析、运行目录命名和冲突校验由共享的纯函数处理；运行入口负责创建目录。
- 继续使用项目现有依赖和数据格式，不新增外部依赖。
