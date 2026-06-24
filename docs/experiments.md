# 实验记录

## 结果记录

1. Louvain聚类+PMI指标
    - 参数：
    ```python
    VISIT_MERGE_WINDOW_MINUTES = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 5
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    ANCHOR_TOP_N = 3
    COMMUNITY_RESOLUTION = 1.0
    ```
    - 结果：790个商圈（商户>2的类即为商圈）

2. 从商户对中间数据执行Leiden聚类
    - 参数：
    ```python
    MIN_EDGE_SUPPORT = 5
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 8
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ```
    - 结果：
        - 商户对345512个，商户192546个，边3720条
        - 全部社区189969个，其中孤立商户189178个
        - 有效社区305个（商户数>=3），较大社区73个（商户数>=10）
        - 候选锚点191065个，孤立商户和二商户社区也会产生候选锚点，不符合预期
        - 从中间数据加载到结果落盘耗时约3分7秒

3. 限制有效社区锚点数量后执行Leiden聚类
    - 参数：
    ```python
    MIN_EDGE_SUPPORT = 5
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 3
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    ```
    - 结果：
        - 商户对345512个，商户192546个，边3720条
        - 全部社区189969个，其中孤立商户189178个
        - 有效社区305个（商户数>=3），较大社区73个（商户数>=10）
        - 候选锚点915个，每个有效社区3个；孤立商户和二商户社区不产生锚点
        - 从中间数据加载到结果落盘耗时约2分48秒


4. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T14:22:31+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\sppmi_leiden_260622142231
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：56.09秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 3
    EDGE_WEIGHT_METHOD = 'sppmi'
    SPPMI_PARAMETERS_ACTIVE = True
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 3
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=3：14230对，覆盖商户10655个
        - 通过SPPMI与显著性过滤：14230对，覆盖商户10655个
        - 通过互为top-k：13214条边，覆盖商户10654个
        - 迭代hub清洗后：13214条边，覆盖商户10654个
    - 聚类结果：
        - 全部社区：183791个，孤立商户181892个
        - 有效社区：707个（商户数>=3）
        - 较大社区：191个（商户数>=10）
        - 有效社区商户：8270个，占全部商户4.30%
        - 候选锚点：2121个，单社区最多3个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的4.12%
        - 有效社区覆盖率为4.30%，未进入有效社区的商户为184276个


5. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T14:43:05+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\sppmi_leiden_260622144305
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：61.84秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 3
    EDGE_WEIGHT_METHOD = 'sppmi'
    SPPMI_PARAMETERS_ACTIVE = True
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 3
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=3：14230对，覆盖商户10655个
        - 通过SPPMI与显著性过滤：14230对，覆盖商户10655个
        - 通过互为top-k：13214条边，覆盖商户10654个
        - 迭代hub清洗后：13214条边，覆盖商户10654个
    - 聚类结果：
        - 全部社区：183791个，孤立商户181892个
        - 有效社区：707个（商户数>=3）
        - 较大社区：191个（商户数>=10）
        - 有效社区商户：8270个，占全部商户4.30%
        - 候选锚点：2121个，单社区最多3个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的4.12%
        - 有效社区覆盖率为4.30%，未进入有效社区的商户为184276个


6. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T15:05:06+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\transaction_count_leiden_260622150506
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：68.87秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 3
    EDGE_WEIGHT_METHOD = 'transaction_count'
    SPPMI_PARAMETERS_ACTIVE = False
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 3
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=3：14230对，覆盖商户10655个
        - SPPMI阶段（已跳过）：14230对，覆盖商户10655个
        - 通过互为top-k：13053条边，覆盖商户10464个
        - 迭代hub清洗后：13053条边，覆盖商户10464个
    - 聚类结果：
        - 全部社区：183990个，孤立商户182082个
        - 有效社区：708个（商户数>=3）
        - 较大社区：190个（商户数>=10）
        - 有效社区商户：8064个，占全部商户4.19%
        - 候选锚点：2124个，单社区最多3个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的4.12%
        - 有效社区覆盖率为4.19%，未进入有效社区的商户为184482个


7. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T15:15:29+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\transaction_count_leiden_260622151529
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：70.91秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 2
    EDGE_WEIGHT_METHOD = 'transaction_count'
    SPPMI_PARAMETERS_ACTIVE = False
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 10
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=2：41625对，覆盖商户24941个
        - SPPMI阶段（已跳过）：41625对，覆盖商户24941个
        - 通过互为top-k：34291条边，覆盖商户24147个
        - 迭代hub清洗后：34291条边，覆盖商户24147个
    - 聚类结果：
        - 全部社区：171813个，孤立商户168399个
        - 有效社区：1296个（商户数>=3）
        - 较大社区：323个（商户数>=10）
        - 有效社区商户：19911个，占全部商户10.34%
        - 候选锚点：4164个，单社区最多10个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的12.05%
        - 有效社区覆盖率为10.34%，未进入有效社区的商户为172635个


8. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T15:18:29+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\sppmi_leiden_260622151829
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：66.39秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 2
    EDGE_WEIGHT_METHOD = 'sppmi'
    SPPMI_PARAMETERS_ACTIVE = True
    PMI_SHIFT_K = 3.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 10
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=2：41625对，覆盖商户24941个
        - 通过SPPMI与显著性过滤：41624对，覆盖商户24941个
        - 通过互为top-k：35596条边，覆盖商户24933个
        - 迭代hub清洗后：35596条边，覆盖商户24933个
    - 聚类结果：
        - 全部社区：170944个，孤立商户167613个
        - 有效社区：1252个（商户数>=3）
        - 较大社区：303个（商户数>=10）
        - 有效社区商户：20775个，占全部商户10.79%
        - 候选锚点：4062个，单社区最多10个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的12.05%
        - 有效社区覆盖率为10.79%，未进入有效社区的商户为171771个


9. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T15:40:49+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\sppmi_leiden_260622154049
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：84.49秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 2
    EDGE_WEIGHT_METHOD = 'sppmi'
    SPPMI_PARAMETERS_ACTIVE = True
    PMI_SHIFT_K = 100.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 10
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=2：41625对，覆盖商户24941个
        - 通过SPPMI与显著性过滤：37782对，覆盖商户24937个
        - 通过互为top-k：35137条边，覆盖商户24929个
        - 迭代hub清洗后：35137条边，覆盖商户24929个
    - 聚类结果：
        - 全部社区：170960个，孤立商户167617个
        - 有效社区：1261个（商户数>=3）
        - 较大社区：311个（商户数>=10）
        - 有效社区商户：20765个，占全部商户10.78%
        - 候选锚点：4088个，单社区最多10个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的12.05%
        - 有效社区覆盖率为10.78%，未进入有效社区的商户为171781个


10. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T15:42:57+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\sppmi_leiden_260622154257
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：66.62秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 2
    EDGE_WEIGHT_METHOD = 'sppmi'
    SPPMI_PARAMETERS_ACTIVE = True
    PMI_SHIFT_K = 300.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 10
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=2：41625对，覆盖商户24941个
        - 通过SPPMI与显著性过滤：30842对，覆盖商户24828个
        - 通过互为top-k：30395条边，覆盖商户24820个
        - 迭代hub清洗后：30395条边，覆盖商户24820个
    - 聚类结果：
        - 全部社区：171146个，孤立商户167726个
        - 有效社区：1308个（商户数>=3）
        - 较大社区：325个（商户数>=10）
        - 有效社区商户：20596个，占全部商户10.70%
        - 候选锚点：4211个，单社区最多10个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的12.05%
        - 有效社区覆盖率为10.70%，未进入有效社区的商户为171950个


11. 自动实验：从商户对中间数据执行Leiden聚类
    - 运行信息：
        - 时间：2026-06-22T15:44:51+08:00
        - 来源：E:\work\algorithm_one_output\shanghai\pair_statistics.sqlite3
        - 输出目录：E:\work\algorithm_one_output\sppmi_leiden_260622154451
        - 原始交易行数：未读取
        - 清洗后到访行数：未读取
        - 运行耗时：63.02秒
    - 参数：
    ```python
    CITY_CODE = 'shanghai'
    TRANSACTIONS_PATH = 'E:\\work\\data.txt'
    CARD_COLUMN = 'cardno'
    MERCHANT_COLUMN = 'merchid'
    TIMESTAMP_COLUMN = 'date'
    TIMESTAMP_FORMATS = ('%Y%m%dT%H%M%S', '%Y%m%d%H%M%S', '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')
    VISIT_MERGE_WINDOW_MINUTES = 30
    MAXIMUM_DAILY_MERCHANTS_PER_CARD = 30
    COOC_WINDOW_MINUTES = 120
    TIME_DECAY_TAU_MINUTES = 60.0
    MIN_EDGE_SUPPORT = 2
    EDGE_WEIGHT_METHOD = 'sppmi'
    SPPMI_PARAMETERS_ACTIVE = True
    PMI_SHIFT_K = 1000.0
    PMI_ALPHA = 0.75
    TOP_K_NEIGHBORS = 15
    MINIMUM_Z_SCORE = 0.0
    COMMUNITY_ALGORITHM = 'leiden'
    COMMUNITY_RANDOM_SEED = 42
    MAX_CLEANING_ROUNDS = 3
    MIN_HUB_DEGREE = 8
    PARTICIPATION_THRESHOLD = 0.75
    COMMUNITY_RESOLUTION = 1.0
    ANCHOR_MIN_N = 3
    ANCHOR_MAX_N = 10
    ANCHOR_MERCHANTS_PER_COUNT = 20
    ANCHOR_MIN_COMMUNITY_SIZE = 3
    OUTPUT_ROOT = 'E:\\work\\algorithm_one_output'
    EXPERIMENT_PATH = 'E:\\work\\docs\\experiments.md'
    ```
    - 数据与过滤漏斗：
        - 全部商户：192546个
        - 原始商户对：345512对，覆盖商户99022个
        - 支持人数>=2：41625对，覆盖商户24941个
        - 通过SPPMI与显著性过滤：20894对，覆盖商户23469个
        - 通过互为top-k：20871条边，覆盖商户23464个
        - 迭代hub清洗后：20871条边，覆盖商户23464个
    - 聚类结果：
        - 全部社区：173655个，孤立商户169082个
        - 有效社区：1993个（商户数>=3）
        - 较大社区：411个（商户数>=10）
        - 有效社区商户：18304个，占全部商户9.51%
        - 候选锚点：6064个，单社区最多9个
        - 无效社区锚点：0个
        - 清洗轮数：0
    - 自动分析：
        - 最大商户损失阶段：未形成时间窗商户对，减少93524个商户
        - 最小支持人数过滤保留原始商户对的12.05%
        - 有效社区覆盖率为9.51%，未进入有效社区的商户为174242个
