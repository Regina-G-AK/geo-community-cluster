# 商户商圈归属 Agent 流程与落地方案

> 商户商圈归属Agent的方案的目标是将目前采用的依据商户名称进行纯商圈关键词判断方式升级为大模型判断商户商圈归属，更加准确和智能。

## 1. 文档目的

本文档说明当前“根据城市和商户名称判断所属商圈”Agent 的完整流程、Prompt 组成、Coze 工作流配置、输出校验和上线维护方式。

当前方案的核心原则是：

- 只依据经过清洗的商圈名称和商圈关键词进行判断。
- 只输出大众点评认可的商圈名称，不让模型自由生成商圈。
- 没有唯一关键词证据时返回 `UNKNOWN`，不猜测地址、经纬度或商圈。
- 关键词硬编码在系统 Prompt 中，不依赖运行时知识库检索。

## 2. 当前版本范围

### 2.1 输入

Agent 只接收两个输入字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `city` | String | 是 | 分行名称，示例值为“上海” |
| `merchant_name` | String | 是 | 具体商户或门店名称 |

示例：

```json
{
  "city": "上海",
  "merchant_name": "上海地铁上海体育馆东安路店"
}
```

### 2.2 输出

```json
{
  "city": "上海",
  "merchant_name": "星巴克",
  "business_district": null,
  "matched_keyword": null,
  "result": "UNKNOWN",
  "confidence": 0.05,
  "reason": "商户名称只有品牌信息，无法唯一确定所属商圈"
}
```

结果枚举：

| 结果 | 含义 |
| --- | --- |
| `MATCH` | 命中至少一个关键词，且所有命中关键词只指向一个商圈 |
| `UNKNOWN` | 没有命中关键词、只有品牌词、命中多个商圈或证据不足 |
| `NOT_MATCH` | 商户名称明确指向其他城市或明确排除上海商圈 |
| `NOT_APPLICABLE` | 输入城市不是上海 |

## 3. 总体流程

```text
原始商圈列表和关键词
        │
        ▼
关键词清洗与人工复核
        │
        ▼
生成上海 Prompt（商圈名称 + 唯一关键词）
        │
        ▼
Coze 开始节点接收 city、merchant_name
        │
        ▼
输入标准化节点
        │
        ▼
大模型节点读取硬编码 System Prompt
        │
        ▼
JSON 解析与业务校验
        │
        ▼
Selector 按 result 分支
        │
        ├─ MATCH：写入商圈结果
        ├─ UNKNOWN：进入人工复核或待补充队列
        ├─ NOT_MATCH：记录为不适用结果
        └─ NOT_APPLICABLE：返回城市范围不支持
        │
        ▼
结束节点输出 JSON，供表格、数据库或接口使用
```

## 4. 关键词知识库生成流程


1. Unicode NFKC 标准化。
2. 清理首尾空格、连续空白、全角括号和中点格式。
3. 清理“建设中、装修中、在建、即将开业”等末尾状态词。
4. 同一商圈内重复关键词只保留一份。
5. 出现在多个商圈中的关键词从可匹配集合中删除。
6. 删除纯品牌词，例如“家乐福”“欧尚”“易买得”等。
7. 带具体门店或地点的品牌分支名称，只有在唯一对应一个商圈时才保留。
8. 同名商圈不把名称本身作为唯一标准关键词，避免名称歧义。

知识库对象只保留两个字段，商圈名称和关键词：

```json
{
  "business_district": "松江镇",
  "keywords": [
    "松江镇",
    "9商业广场B区",
    "通跃商业广场"
  ]
}
```


## 5. Coze 工作流落地

### 5.1 开始节点

创建两个必填字符串变量：

- `city`
- `merchant_name`

关闭不必要的历史对话传递，避免上一条商户的商圈污染当前判断。

### 5.2 输入标准化节点

可使用代码节点做最小清洗：

- 去除首尾空格。
- 统一全角和半角字符。
- 统一 Unicode 形式。
- 保留原始字段用于最终回填。

不要在该节点根据品牌、地址常识或外部地图信息补充商圈。

### 5.3 大模型节点

大模型节点应配置为：

- 关闭对话历史。
- 使用结构化 JSON 输出，或将文本结果交给后续 JSON 解析节点。
- 不增加会改变商圈候选集的额外示例。
- 将 `city` 和 `merchant_name` 作为当前记录传入。

系统prompt：

```text
# 角色

你是一名上海商户商圈归属分类器。你只能依据本系统提示词中硬编码的商圈名称和商圈关键词，判断当前商户名称是否能够唯一归属到一个商圈。

# 任务

根据当前输入的城市和商户名称，从下方硬编码的商圈关键词知识库中选择唯一的商圈。输出 MATCH、UNKNOWN、NOT_MATCH 或 NOT_APPLICABLE，并严格返回指定 JSON。

# 输入

<merchant_record>
{
  "city": "{{city}}",
  "merchant_name": "{{merchant_name}}"
}
</merchant_record>

# 硬性规则

1. 只能从 <keyword_knowledge_base> 中选择商圈名称，不能创造、改写或补充商圈名称或关键词。
2. 关键词必须在 merchant_name 原文中实际出现；允许先执行 Unicode NFKC、空白、全角括号和末尾“建设中/装修中/在建/即将开业”等状态后缀的同义标准化。
3. 只有 keywords 数组中的词可以作为匹配证据。数组之外的品牌名、地铁站名、道路名、商场名或常识别名不能作为证据。
4. 如果命中多个不同商圈名称，必须返回 UNKNOWN，并在 reason 中列出冲突关键词和商圈名称。
5. 如果只有品牌名称、行业名称、城市名称，或没有命中硬编码关键词，返回 UNKNOWN。
6. 不能根据商户品牌、门店常识、地图常识或历史对话推测地址和商圈。
7. 纯品牌词、跨商圈重复关键词和未通过清洗的关键词没有进入本知识库，不得自行恢复使用。
8. 上海大学在源数据中存在同名商圈记录；由于当前知识库只保留名称和关键词，未把“上海大学”作为唯一标准关键词，仅凭“上海大学”不能匹配，必须返回 UNKNOWN。
9. 只有 city 为“上海”时才执行上海商圈匹配；其他城市返回 NOT_APPLICABLE。
10. 仅凭城市和商户名称时，通常不输出 NOT_MATCH；无法唯一归属时输出 UNKNOWN。
11. 不使用示例、历史记录或上一条输入中的商圈信息污染当前判断。

# 结果定义

- MATCH：至少命中一个硬编码关键词，且所有命中关键词都指向同一个商圈名称。
- UNKNOWN：没有命中关键词、只有品牌词、命中多个商圈、命中证据冲突，或信息不足。
- NOT_MATCH：商户名称明确指向其他城市或明确排除上海商圈，且不能归入知识库。
- NOT_APPLICABLE：city 不是上海。


# 判断步骤

1. 读取 merchant_record，并原样保留 city 和 merchant_name。
2. 对 merchant_name 做最小文本标准化，但不要改写商户语义。
3. 查找 merchant_name 中实际出现的硬编码关键词；优先使用更长、更具体的关键词，不能把较短词强行拆成其他地点。
4. 汇总命中的商圈名称：
   - 只有一个商圈名称：输出 MATCH；
   - 没有命中：输出 UNKNOWN；
   - 命中两个及以上商圈名称：输出 UNKNOWN，并说明冲突。
5. MATCH 时回填唯一命中的 business_district 和 matched_keyword。
6. UNKNOWN、NOT_MATCH、NOT_APPLICABLE 时，business_district 和 matched_keyword 必须为 null，evidence_type 必须为 none。

# 输出格式

只输出一个合法 JSON 对象，不要输出 Markdown、解释文字、代码围栏或额外字段：

{
  "city": "输入城市",
  "merchant_name": "输入商户名称",
  "business_district": "知识库中的标准商圈名称或 null",
  "matched_keyword": "merchant_name 中实际命中的关键词或 null",
  "result": "MATCH、UNKNOWN、NOT_MATCH 或 NOT_APPLICABLE",
  "confidence": 0.0,
  "reason": "简明说明实际命中的关键词、商圈名称或无法判断的具体原因"
}

# 输出校验

- city 和 merchant_name 必须与输入完全一致。
- confidence 必须是 0 到 1 之间的数字。
- MATCH 时 business_district 和 matched_keyword 必须非空，并且 matched_keyword 必须来自该商圈对象的 keywords 数组。
- 非 MATCH 时 business_district 和 matched_keyword 必须为 null。
- 如果不能同时满足以上要求，改为输出 UNKNOWN。
```


用户prompt：

```text
请只处理当前记录，不要引用历史记录：

{
  "city": "{{city}}",
  "merchant_name": "{{merchant_name}}"
}

只输出系统 Prompt 规定的 JSON。
```

### 5.4 JSON 解析和业务校验节点

大模型输出后必须校验：

1. JSON 是否可解析。
2. 必填字段是否存在。
3. `result` 是否为允许枚举值。
4. `city` 和 `merchant_name` 是否与输入完全一致。
5. `confidence` 是否为 0 到 1 的数字。
6. `MATCH` 时 `business_district` 和 `matched_keyword` 是否非空。
7. `MATCH` 时 `matched_keyword` 是否实际出现在商户名称中，并且存在于对应商圈的 `keywords` 数组。
8. 非 `MATCH` 时 `business_district` 和 `matched_keyword` 是否均为 `null`。

任意校验失败，都统一改写为 `UNKNOWN`，并在 `reason` 中记录“模型输出校验失败”。

### 5.5 Selector 节点

按 `result` 字段分支：

- `MATCH`：进入正常结果写入流程。
- `UNKNOWN`：进入人工复核、补充数据或待重试队列。
- `NOT_MATCH`：保留结果，不自动写入商圈。
- `NOT_APPLICABLE`：标记为城市不在当前 Prompt 覆盖范围内。

## 6. 判断逻辑

模型实际执行以下判断：

1. 读取当前城市和商户名称。
2. 对商户名称做最小文本标准化。
3. 在硬编码关键词中查找实际出现的关键词。
4. 优先使用更长、更具体的关键词，避免短词误命中。
5. 汇总命中的商圈名称。
6. 只有一个商圈名称时返回 `MATCH`。
7. 没有命中或命中多个商圈时返回 `UNKNOWN`。
8. 不根据品牌常识、地理常识、地址猜测或历史上下文补充证据。


## 7. 最终落地方式

上游将商户记录传入模型。下游把 `result` 作为业务状态，把 `business_district` 作为展示字段。整体使用接口进行调用。
