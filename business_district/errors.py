class ConfigurationError(ValueError):
    """配置文件内容无效。"""


class TransactionDataError(ValueError):
    """交易数据不符合输入约束。"""


class AlgorithmError(RuntimeError):
    """算法执行失败。"""

