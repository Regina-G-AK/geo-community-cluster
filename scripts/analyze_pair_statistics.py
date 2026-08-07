from __future__ import annotations

import hashlib
import heapq
import json
import math
import pickle
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import (
    DefaultDict,
    Callable,
    Collection,
    Dict,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_ROOT / "code" / "pair_statistics_shanghai.pkl"
MINIMUM_SUPPORT = 3
CONTEXT_SMOOTHING_ALPHA = 0.75
SPPMI_SHIFT = 3.0
MINIMUM_Z_SCORE = 0.5
TOP_K_NEIGHBORS = 15
CHAIN_VISIT_COUNT_QUANTILE = 0.9
CHAIN_MINIMUM_VISIT_COUNT = 100
TOP_MERCHANT_COUNT = 50


MerchantPair = Tuple[str, str]
SppmiMetric = Tuple[float, float, int]
GraphEdge = Tuple[str, str, float, int]
Number = Union[int, float]
ReportValue = Union[str, int, float]


class PairStatisticsData(NamedTuple):
    strengths: Dict[MerchantPair, float]
    supports: Dict[MerchantPair, int]
    merchant_visit_counts: Dict[str, int]


class Distribution(NamedTuple):
    count: int
    minimum: Optional[float]
    p25: Optional[float]
    median: Optional[float]
    p75: Optional[float]
    p90: Optional[float]
    p95: Optional[float]
    p99: Optional[float]
    maximum: Optional[float]
    mean: Optional[float]


class ComponentData(NamedTuple):
    component_id_by_merchant: Dict[str, int]
    component_size_by_merchant: Dict[str, int]
    component_members: Dict[int, Tuple[str, ...]]
    isolated_merchant_count: int


class NodeMetrics(NamedTuple):
    degree: Dict[str, int]
    weight_sum: Dict[str, float]
    support_sum: Dict[str, int]


class MerchantMetricSources(NamedTuple):
    raw: NodeMetrics
    support: NodeMetrics
    candidate: NodeMetrics
    graph: NodeMetrics
    after_chain: NodeMetrics


class TaskPlatform(NamedTuple):
    mount_check: Callable[[], None]
    log_data: Callable[[str], None]
    format_exception: Callable[[], None]
    finish_task: Callable[[], None]


class PairStatisticsPayload:
    pass


class RestrictedPairStatisticsUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Type[object]:
        if module == "business_district.graph" and name == "PairStatistics":
            return PairStatisticsPayload
        raise pickle.UnpicklingError(
            f"中间文件包含不允许加载的全局对象: module={module!r}, name={name!r}"
        )


def validate_settings(
    input_path: Path,
    minimum_support: int,
    context_smoothing_alpha: float,
    sppmi_shift: float,
    minimum_z_score: float,
    top_k_neighbors: int,
    chain_visit_count_quantile: float,
    chain_minimum_visit_count: int,
    top_merchant_count: int,
) -> None:
    if not input_path.is_file():
        raise FileNotFoundError(
            "中间数据文件不存在，请修改脚本顶部的 INPUT_PATH: "
            f"path={input_path.resolve()}"
        )
    if input_path.suffix.lower() != ".pkl":
        raise ValueError(f"中间数据文件必须是 .pkl: path={input_path}")
    if minimum_support < 1:
        raise ValueError(
            f"最小支持人数必须不小于 1: minimum_support={minimum_support}"
        )
    if not 0.0 <= context_smoothing_alpha <= 1.0:
        raise ValueError(
            "上下文平滑参数必须位于 0 到 1 之间: "
            f"context_smoothing_alpha={context_smoothing_alpha}"
        )
    if sppmi_shift < 1.0:
        raise ValueError(
            f"SPPMI 平移参数必须不小于 1: sppmi_shift={sppmi_shift}"
        )
    if minimum_z_score < 0.0:
        raise ValueError(
            f"最小 z-score 必须不小于 0: minimum_z_score={minimum_z_score}"
        )
    if top_k_neighbors < 1:
        raise ValueError(
            f"top-k 邻居数必须不小于 1: top_k_neighbors={top_k_neighbors}"
        )
    if not 0.0 <= chain_visit_count_quantile <= 1.0:
        raise ValueError(
            "访问量型连锁商户分位阈值必须位于 0 到 1 之间: "
            f"chain_visit_count_quantile={chain_visit_count_quantile}"
        )
    if chain_minimum_visit_count < 1:
        raise ValueError(
            "访问量型连锁商户绝对访问量下限必须不小于 1: "
            f"chain_minimum_visit_count={chain_minimum_visit_count}"
        )
    if top_merchant_count < 1:
        raise ValueError(
            f"重点商户输出数量必须不小于 1: top_merchant_count={top_merchant_count}"
        )


def load_pair_statistics(path: Path) -> PairStatisticsData:
    try:
        with path.open("rb") as file:
            header = file.read(2)
            file.seek(0)
            if (
                len(header) == 2
                and header[0] == 0x80
                and header[1] > pickle.HIGHEST_PROTOCOL
            ):
                raise ValueError(
                    "中间文件使用了当前 Python 无法读取的 pickle 协议: "
                    f"path={path}, file_protocol={header[1]}, "
                    f"supported_protocol={pickle.HIGHEST_PROTOCOL}。"
                    "请使用 Python 3.7 兼容的 protocol 4 重新生成中间文件"
                )
            loaded = RestrictedPairStatisticsUnpickler(file).load()
            trailing = file.read(1)
    except (OSError, EOFError, pickle.PickleError, AttributeError) as error:
        raise ValueError(
            f"商户对中间文件读取失败: path={path}, reason={error}"
        ) from error
    if trailing:
        raise ValueError(f"商户对中间文件包含多余尾部数据: path={path}")
    if not isinstance(loaded, PairStatisticsPayload):
        raise TypeError(
            "商户对中间文件顶层对象类型错误: "
            f"path={path}, type={type(loaded).__name__}"
        )
    strengths = validate_strengths(getattr(loaded, "strengths", None), path)
    supports = validate_supports(getattr(loaded, "supports", None), path)
    visit_counts = validate_visit_counts(
        getattr(loaded, "merchant_visit_counts", None),
        path,
    )
    missing_support_count = sum(pair not in supports for pair in strengths)
    extra_support_count = sum(pair not in strengths for pair in supports)
    if missing_support_count or extra_support_count:
        raise ValueError(
            "商户对强度和支持人数的键不一致: "
            f"path={path}, missing_supports={missing_support_count}, "
            f"extra_supports={extra_support_count}"
        )
    missing_visit_count = 0
    missing_visit_examples: List[str] = []
    for left, right in strengths:
        for merchant_id in (left, right):
            if merchant_id in visit_counts:
                continue
            missing_visit_count += 1
            if len(missing_visit_examples) < 10:
                missing_visit_examples.append(merchant_id)
    if missing_visit_count:
        raise ValueError(
            "部分商户对端点缺少访问量: "
            f"path={path}, missing_count={missing_visit_count}, "
            f"examples={sorted(set(missing_visit_examples))}"
        )
    return PairStatisticsData(strengths, supports, visit_counts)


def validate_pair(value: object, field: str, path: Path) -> MerchantPair:
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError(
            f"{field} 的键必须是长度为 2 的元组: path={path}, value={value!r}"
        )
    left, right = value
    if not isinstance(left, str) or not left:
        raise TypeError(
            f"{field} 的左商户必须是非空字符串: path={path}, value={value!r}"
        )
    if not isinstance(right, str) or not right:
        raise TypeError(
            f"{field} 的右商户必须是非空字符串: path={path}, value={value!r}"
        )
    if left >= right:
        raise ValueError(
            f"{field} 的商户对必须按升序保存且不能自环: path={path}, value={value!r}"
        )
    return left, right


def validate_strengths(value: object, path: Path) -> Dict[MerchantPair, float]:
    if not isinstance(value, dict):
        raise TypeError(
            f"strengths 必须是字典: path={path}, type={type(value).__name__}"
        )
    for raw_pair, raw_strength in cast(Dict[object, object], value).items():
        pair = validate_pair(raw_pair, "strengths", path)
        if isinstance(raw_strength, bool) or not isinstance(raw_strength, (int, float)):
            raise TypeError(
                "商户对强度必须是数值: "
                f"path={path}, pair={pair}, value={raw_strength!r}"
            )
        strength = float(raw_strength)
        if not math.isfinite(strength) or strength <= 0.0:
            raise ValueError(
                "商户对强度必须是有限正数: "
                f"path={path}, pair={pair}, value={raw_strength!r}"
            )
    return cast(Dict[MerchantPair, float], value)


def validate_supports(value: object, path: Path) -> Dict[MerchantPair, int]:
    if not isinstance(value, dict):
        raise TypeError(
            f"supports 必须是字典: path={path}, type={type(value).__name__}"
        )
    for raw_pair, raw_support in cast(Dict[object, object], value).items():
        pair = validate_pair(raw_pair, "supports", path)
        if isinstance(raw_support, bool) or not isinstance(raw_support, int):
            raise TypeError(
                "商户对支持人数必须是整数: "
                f"path={path}, pair={pair}, value={raw_support!r}"
            )
        if raw_support < 1:
            raise ValueError(
                "商户对支持人数必须不小于 1: "
                f"path={path}, pair={pair}, value={raw_support}"
            )
    return cast(Dict[MerchantPair, int], value)


def validate_visit_counts(value: object, path: Path) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise TypeError(
            "merchant_visit_counts 必须是字典: "
            f"path={path}, type={type(value).__name__}"
        )
    for raw_merchant, raw_count in cast(Dict[object, object], value).items():
        if not isinstance(raw_merchant, str) or not raw_merchant:
            raise TypeError(
                "访问量商户必须是非空字符串: "
                f"path={path}, merchant={raw_merchant!r}"
            )
        if isinstance(raw_count, bool) or not isinstance(raw_count, int):
            raise TypeError(
                "商户访问量必须是整数: "
                f"path={path}, merchant={raw_merchant!r}, value={raw_count!r}"
            )
        if raw_count < 1:
            raise ValueError(
                "商户访问量必须不小于 1: "
                f"path={path}, merchant={raw_merchant!r}, value={raw_count}"
            )
    if not value:
        raise ValueError(f"merchant_visit_counts 不能为空: path={path}")
    return cast(Dict[str, int], value)


def nearest_rank(ordered: Sequence[float], quantile: float) -> float:
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def calculate_distribution(values: Sequence[Number]) -> Distribution:
    if not values:
        return Distribution(0, None, None, None, None, None, None, None, None, None)
    ordered = sorted(float(value) for value in values)
    return Distribution(
        count=len(ordered),
        minimum=ordered[0],
        p25=nearest_rank(ordered, 0.25),
        median=nearest_rank(ordered, 0.50),
        p75=nearest_rank(ordered, 0.75),
        p90=nearest_rank(ordered, 0.90),
        p95=nearest_rank(ordered, 0.95),
        p99=nearest_rank(ordered, 0.99),
        maximum=ordered[-1],
        mean=sum(ordered) / len(ordered),
    )


def calculate_distribution_with_repeated_minimum(
    values: Iterable[Number],
    repeated_minimum: float,
    repeated_count: int,
) -> Distribution:
    ordered = sorted(float(value) for value in values)
    if repeated_count < 0:
        raise ValueError(
            f"重复最小值数量不能小于 0: repeated_count={repeated_count}"
        )
    if ordered and ordered[0] < repeated_minimum:
        raise ValueError(
            "重复值必须不大于其他分布值: "
            f"repeated_minimum={repeated_minimum}, actual_minimum={ordered[0]}"
        )
    total_count = repeated_count + len(ordered)
    if total_count == 0:
        return Distribution(0, None, None, None, None, None, None, None, None, None)

    def value_at_quantile(quantile: float) -> float:
        rank = max(1, min(total_count, math.ceil(total_count * quantile)))
        if rank <= repeated_count:
            return repeated_minimum
        return ordered[rank - repeated_count - 1]

    minimum = repeated_minimum if repeated_count else ordered[0]
    maximum = ordered[-1] if ordered else repeated_minimum
    return Distribution(
        count=total_count,
        minimum=minimum,
        p25=value_at_quantile(0.25),
        median=value_at_quantile(0.50),
        p75=value_at_quantile(0.75),
        p90=value_at_quantile(0.90),
        p95=value_at_quantile(0.95),
        p99=value_at_quantile(0.99),
        maximum=maximum,
        mean=(sum(ordered) + repeated_minimum * repeated_count) / total_count,
    )


def calculate_sppmi_metrics(
    statistics: PairStatisticsData,
    minimum_support: int,
    context_smoothing_alpha: float,
    sppmi_shift: float,
) -> Dict[MerchantPair, SppmiMetric]:
    marginals: DefaultDict[str, float] = defaultdict(float)
    total_strength = 0.0
    eligible_pair_count = 0
    for (left, right), strength in statistics.strengths.items():
        if statistics.supports[(left, right)] < minimum_support:
            continue
        marginals[left] += strength
        marginals[right] += strength
        total_strength += strength
        eligible_pair_count += 1
    if eligible_pair_count == 0:
        return {}
    smoothed_total = (
        sum(
            strength**context_smoothing_alpha
            for strength in marginals.values()
        )
        / 2.0
    )
    metrics: Dict[MerchantPair, SppmiMetric] = {}
    for pair, strength in statistics.strengths.items():
        if statistics.supports[pair] < minimum_support:
            continue
        left, right = pair
        left_context_pmi = math.log(
            strength
            * smoothed_total
            / (
                marginals[left]
                * marginals[right] ** context_smoothing_alpha
            )
        )
        right_context_pmi = math.log(
            strength
            * smoothed_total
            / (
                marginals[right]
                * marginals[left] ** context_smoothing_alpha
            )
        )
        pmi = (left_context_pmi + right_context_pmi) / 2.0
        sppmi = max(pmi - math.log(sppmi_shift), 0.0)
        expected = marginals[left] * marginals[right] / total_strength
        z_score = (
            (strength - expected) / math.sqrt(expected)
            if expected > 0.0
            else 0.0
        )
        metrics[pair] = (
            sppmi,
            z_score,
            statistics.supports[pair],
        )
    return metrics


def is_sppmi_candidate(metric: SppmiMetric, minimum_z_score: float) -> bool:
    return metric[0] > 0.0 and metric[1] >= minimum_z_score


def build_mutual_top_k_edges(
    sppmi_metrics: Mapping[MerchantPair, SppmiMetric],
    minimum_z_score: float,
    top_k_neighbors: int,
) -> List[GraphEdge]:
    neighbors: DefaultDict[str, List[Tuple[str, float, int]]] = defaultdict(list)
    for (left, right), metric in sppmi_metrics.items():
        if not is_sppmi_candidate(metric, minimum_z_score):
            continue
        weight, _, support = metric
        neighbors[left].append((right, weight, support))
        neighbors[right].append((left, weight, support))
    top_neighbors: Dict[str, Set[str]] = {}
    for merchant_id, merchant_neighbors in neighbors.items():
        ordered = sorted(
            merchant_neighbors,
            key=lambda item: (-item[1], -item[2], item[0]),
        )[:top_k_neighbors]
        top_neighbors[merchant_id] = {neighbor for neighbor, _, _ in ordered}
    edges: List[GraphEdge] = []
    for pair, metric in sppmi_metrics.items():
        if not is_sppmi_candidate(metric, minimum_z_score):
            continue
        left, right = pair
        if (
            right in top_neighbors.get(left, set())
            and left in top_neighbors.get(right, set())
        ):
            edges.append((left, right, metric[0], metric[2]))
    return edges


def calculate_chain_threshold(
    visit_counts: Iterable[int],
    quantile: float,
    minimum_visit_count: int,
) -> int:
    ordered = sorted(float(value) for value in visit_counts)
    quantile_threshold = int(nearest_rank(ordered, quantile))
    return max(minimum_visit_count, quantile_threshold)


def calculate_components(
    merchants: Collection[str],
    edges: Sequence[GraphEdge],
) -> ComponentData:
    adjacency: DefaultDict[str, Set[str]] = defaultdict(set)
    for left, right, _, _ in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    components: List[Tuple[str, ...]] = []
    unseen = set(adjacency)
    while unseen:
        start = min(unseen)
        stack = [start]
        unseen.remove(start)
        members: List[str] = []
        while stack:
            merchant_id = stack.pop()
            members.append(merchant_id)
            new_neighbors = sorted(adjacency[merchant_id].intersection(unseen))
            unseen.difference_update(new_neighbors)
            stack.extend(reversed(new_neighbors))
        components.append(tuple(sorted(members)))
    ordered_components = sorted(
        components,
        key=lambda members: (-len(members), members[0]),
    )
    component_id_by_merchant: Dict[str, int] = {}
    component_size_by_merchant: Dict[str, int] = {}
    component_members: Dict[int, Tuple[str, ...]] = {}
    for component_id, members in enumerate(ordered_components):
        component_members[component_id] = members
        for merchant_id in members:
            component_id_by_merchant[merchant_id] = component_id
            component_size_by_merchant[merchant_id] = len(members)
    return ComponentData(
        component_id_by_merchant,
        component_size_by_merchant,
        component_members,
        len(merchants) - len(adjacency),
    )


def accumulate_node_metrics(
    pairs: Iterable[Tuple[MerchantPair, float, int]],
) -> NodeMetrics:
    degree: DefaultDict[str, int] = defaultdict(int)
    weight_sum: DefaultDict[str, float] = defaultdict(float)
    support_sum: DefaultDict[str, int] = defaultdict(int)
    for (left, right), weight, support in pairs:
        degree[left] += 1
        degree[right] += 1
        weight_sum[left] += weight
        weight_sum[right] += weight
        support_sum[left] += support
        support_sum[right] += support
    return NodeMetrics(dict(degree), dict(weight_sum), dict(support_sum))


def graph_edges_as_pair_metrics(
    edges: Sequence[GraphEdge],
) -> Iterable[Tuple[MerchantPair, float, int]]:
    return (
        ((left, right), weight, support)
        for left, right, weight, support in edges
    )


def calculate_stage_summary(
    merchant_ids: Set[str],
    pairs: Iterable[MerchantPair],
) -> Dict[str, Number]:
    pair_count = 0
    covered: Set[str] = set()
    for left, right in pairs:
        pair_count += 1
        covered.add(left)
        covered.add(right)
    merchant_count = len(merchant_ids)
    return {
        "pair_count": pair_count,
        "covered_merchant_count": len(covered),
        "isolated_merchant_count": merchant_count - len(covered),
        "merchant_coverage_rate": len(covered) / merchant_count,
    }


def calculate_support_thresholds(
    statistics: PairStatisticsData,
    configured_minimum_support: int,
) -> List[Dict[str, Number]]:
    merchant_ids = set(statistics.merchant_visit_counts)
    thresholds = sorted(
        {1, 2, 3, 5, 10, 20, 50, 100, configured_minimum_support}
    )
    rows: List[Dict[str, Number]] = []
    for threshold in thresholds:
        row = {"minimum_support": threshold}
        row.update(
            calculate_stage_summary(
                merchant_ids,
                (
                    pair
                    for pair, support in statistics.supports.items()
                    if support >= threshold
                ),
            )
        )
        rows.append(row)
    return rows


def build_merchant_metric_sources(
    statistics: PairStatisticsData,
    minimum_support: int,
    sppmi_metrics: Mapping[MerchantPair, SppmiMetric],
    minimum_z_score: float,
    graph_edges: Sequence[GraphEdge],
    graph_edges_after_chain: Sequence[GraphEdge],
) -> MerchantMetricSources:
    raw_metrics = accumulate_node_metrics(
        (
            (pair, strength, statistics.supports[pair])
            for pair, strength in statistics.strengths.items()
        )
    )
    support_metrics = accumulate_node_metrics(
        (
            (pair, strength, statistics.supports[pair])
            for pair, strength in statistics.strengths.items()
            if statistics.supports[pair] >= minimum_support
        )
    )
    candidate_metrics = accumulate_node_metrics(
        (
            (pair, metric[0], metric[2])
            for pair, metric in sppmi_metrics.items()
            if is_sppmi_candidate(metric, minimum_z_score)
        )
    )
    graph_metrics = accumulate_node_metrics(
        graph_edges_as_pair_metrics(graph_edges)
    )
    after_chain_metrics = accumulate_node_metrics(
        graph_edges_as_pair_metrics(graph_edges_after_chain)
    )
    return MerchantMetricSources(
        raw_metrics,
        support_metrics,
        candidate_metrics,
        graph_metrics,
        after_chain_metrics,
    )


def build_merchant_metrics(
    statistics: PairStatisticsData,
    merchant_ids: Iterable[str],
    metric_sources: MerchantMetricSources,
    graph_components: ComponentData,
    chain_merchants: Set[str],
    components_after_chain: ComponentData,
) -> Dict[str, Dict[str, ReportValue]]:
    metrics: Dict[str, Dict[str, ReportValue]] = {}
    for merchant_id in merchant_ids:
        visit_count = statistics.merchant_visit_counts[merchant_id]
        is_chain = merchant_id in chain_merchants
        graph_degree = metric_sources.graph.degree.get(merchant_id, 0)
        after_chain_degree = metric_sources.after_chain.degree.get(merchant_id, 0)
        metrics[merchant_id] = {
            "merchant_id": merchant_id,
            "visit_count": visit_count,
            "raw_degree": metric_sources.raw.degree.get(merchant_id, 0),
            "raw_strength_sum": metric_sources.raw.weight_sum.get(merchant_id, 0.0),
            "raw_support_sum": metric_sources.raw.support_sum.get(merchant_id, 0),
            "minimum_support_degree": metric_sources.support.degree.get(
                merchant_id,
                0,
            ),
            "sppmi_candidate_degree": metric_sources.candidate.degree.get(
                merchant_id,
                0,
            ),
            "sppmi_candidate_weight_sum": metric_sources.candidate.weight_sum.get(
                merchant_id,
                0.0,
            ),
            "mutual_top_k_degree": graph_degree,
            "mutual_top_k_weight_sum": metric_sources.graph.weight_sum.get(
                merchant_id,
                0.0,
            ),
            "component_id": graph_components.component_id_by_merchant.get(
                merchant_id,
                -1,
            ),
            "component_size": graph_components.component_size_by_merchant.get(
                merchant_id,
                1,
            ),
            "is_graph_isolated": int(graph_degree == 0),
            "is_visit_count_chain_candidate": int(is_chain),
            "degree_after_chain_removal": (
                0 if is_chain else after_chain_degree
            ),
            "component_size_after_chain_removal": (
                0
                if is_chain
                else components_after_chain.component_size_by_merchant.get(
                    merchant_id,
                    1,
                )
            ),
            "is_isolated_after_chain_removal": (
                -1 if is_chain else int(after_chain_degree == 0)
            ),
            "lost_edge_count_from_chain_removal": (
                graph_degree
                if is_chain
                else graph_degree - after_chain_degree
            ),
        }
    return metrics


def select_top_merchant_ids(
    merchant_ids: Iterable[str],
    values: Mapping[str, Number],
    count: int,
) -> List[str]:
    return heapq.nsmallest(
        count,
        merchant_ids,
        key=lambda merchant_id: (
            -float(values.get(merchant_id, 0)),
            merchant_id,
        ),
    )


def select_top_merchants(
    merchant_metrics: Mapping[str, Mapping[str, ReportValue]],
    merchant_ids: Sequence[str],
) -> List[Mapping[str, ReportValue]]:
    return [merchant_metrics[merchant_id] for merchant_id in merchant_ids]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            block = file.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def analyze_pair_statistics(
    input_path: Path,
    minimum_support: int,
    context_smoothing_alpha: float,
    sppmi_shift: float,
    minimum_z_score: float,
    top_k_neighbors: int,
    chain_visit_count_quantile: float,
    chain_minimum_visit_count: int,
    top_merchant_count: int,
) -> Dict[str, object]:
    validate_settings(
        input_path,
        minimum_support,
        context_smoothing_alpha,
        sppmi_shift,
        minimum_z_score,
        top_k_neighbors,
        chain_visit_count_quantile,
        chain_minimum_visit_count,
        top_merchant_count,
    )
    statistics = load_pair_statistics(input_path)
    merchant_ids = set(statistics.merchant_visit_counts)
    sppmi_metrics = calculate_sppmi_metrics(
        statistics,
        minimum_support,
        context_smoothing_alpha,
        sppmi_shift,
    )
    graph_edges = build_mutual_top_k_edges(
        sppmi_metrics,
        minimum_z_score,
        top_k_neighbors,
    )
    graph_components = calculate_components(merchant_ids, graph_edges)
    chain_threshold = calculate_chain_threshold(
        statistics.merchant_visit_counts.values(),
        chain_visit_count_quantile,
        chain_minimum_visit_count,
    )
    chain_merchants = {
        merchant_id
        for merchant_id, visit_count in statistics.merchant_visit_counts.items()
        if visit_count >= chain_threshold
    }
    graph_edges_after_chain = [
        edge
        for edge in graph_edges
        if edge[0] not in chain_merchants and edge[1] not in chain_merchants
    ]
    remaining_merchants = merchant_ids.difference(chain_merchants)
    components_after_chain = calculate_components(
        remaining_merchants,
        graph_edges_after_chain,
    )
    metric_sources = build_merchant_metric_sources(
        statistics,
        minimum_support,
        sppmi_metrics,
        minimum_z_score,
        graph_edges,
        graph_edges_after_chain,
    )
    lost_edge_counts = {
        merchant_id: degree
        - metric_sources.after_chain.degree.get(merchant_id, 0)
        for merchant_id, degree in metric_sources.graph.degree.items()
    }
    top_merchant_ids = {
        "by_visit_count": select_top_merchant_ids(
            merchant_ids,
            statistics.merchant_visit_counts,
            top_merchant_count,
        ),
        "by_raw_degree": select_top_merchant_ids(
            merchant_ids,
            metric_sources.raw.degree,
            top_merchant_count,
        ),
        "by_minimum_support_degree": select_top_merchant_ids(
            merchant_ids,
            metric_sources.support.degree,
            top_merchant_count,
        ),
        "by_sppmi_candidate_degree": select_top_merchant_ids(
            merchant_ids,
            metric_sources.candidate.degree,
            top_merchant_count,
        ),
        "by_mutual_top_k_degree": select_top_merchant_ids(
            merchant_ids,
            metric_sources.graph.degree,
            top_merchant_count,
        ),
        "by_lost_edge_count_from_chain_removal": select_top_merchant_ids(
            merchant_ids,
            lost_edge_counts,
            top_merchant_count,
        ),
    }
    selected_merchant_ids = {
        merchant_id
        for selected_ids in top_merchant_ids.values()
        for merchant_id in selected_ids
    }
    merchant_metrics = build_merchant_metrics(
        statistics,
        selected_merchant_ids,
        metric_sources,
        graph_components,
        chain_merchants,
        components_after_chain,
    )
    graph_component_sizes = [
        len(members)
        for members in graph_components.component_members.values()
    ]
    after_chain_component_sizes = [
        len(members)
        for members in components_after_chain.component_members.values()
    ]
    graph_pairs = [
        (left, right)
        for left, right, _, _ in graph_edges
    ]
    after_chain_pairs = [
        (left, right)
        for left, right, _, _ in graph_edges_after_chain
    ]
    after_chain_summary = calculate_stage_summary(
        remaining_merchants,
        after_chain_pairs,
    )
    after_chain_summary.update(
        {
            "removed_merchant_count": len(chain_merchants),
            "remaining_merchant_count": len(remaining_merchants),
            "removed_edge_count": len(graph_edges) - len(graph_edges_after_chain),
            "edge_retention_rate": (
                len(graph_edges_after_chain) / len(graph_edges)
                if graph_edges
                else 0.0
            ),
        }
    )
    return {
        # "generated_at": datetime.now().astimezone().isoformat(),
        # "source": {
        #     "path": str(input_path.resolve()),
        #     "size_bytes": input_path.stat().st_size,
        #     "sha256": file_sha256(input_path),
        # },
        # "parameters": {
        #     "minimum_support": minimum_support,
        #     "context_smoothing_alpha": context_smoothing_alpha,
        #     "sppmi_shift": sppmi_shift,
        #     "minimum_z_score": minimum_z_score,
        #     "top_k_neighbors": top_k_neighbors,
        #     "chain_visit_count_quantile": chain_visit_count_quantile,
        #     "chain_minimum_visit_count": chain_minimum_visit_count,
        #     "calculated_chain_visit_count_threshold": chain_threshold,
        #     "top_merchant_count": top_merchant_count,
        # },
        "data": {
            "merchant": len(merchant_ids),
            "pair": len(statistics.strengths),
            # "visit_count_distribution": calculate_distribution(
            #     list(statistics.merchant_visit_counts.values())
            # )._asdict(),
            # "pair_strength_distribution": calculate_distribution(
            #     list(statistics.strengths.values())
            # )._asdict(),
            # "pair_support_distribution": calculate_distribution(
            #     list(statistics.supports.values())
            # )._asdict(),
        },
        "support": calculate_support_thresholds(
            statistics,
            minimum_support,
        ),
        "filter": {
            "pairs": calculate_stage_summary(
                merchant_ids,
                statistics.strengths,
            ),
            "support": calculate_stage_summary(
                merchant_ids,
                sppmi_metrics,
            ),
            "sppmi_and_z_score": calculate_stage_summary(
                merchant_ids,
                (
                    pair
                    for pair, metric in sppmi_metrics.items()
                    if is_sppmi_candidate(metric, minimum_z_score)
                ),
            ),
            "mutual_top_k": calculate_stage_summary(
                merchant_ids,
                graph_pairs,
            ),
        },
        "mutual_top_k_graph": {
            "component_count": (
                len(graph_components.component_members)
                + graph_components.isolated_merchant_count
            ),
            "isolated_component_count": graph_components.isolated_merchant_count,
            "largest_component_size": (
                max(graph_component_sizes)
                if graph_component_sizes
                else 1
            ),
            "component_size_distribution": calculate_distribution_with_repeated_minimum(
                graph_component_sizes,
                1.0,
                graph_components.isolated_merchant_count,
            )._asdict(),
            "degree_distribution": calculate_distribution_with_repeated_minimum(
                metric_sources.graph.degree.values(),
                0.0,
                len(merchant_ids) - len(metric_sources.graph.degree),
            )._asdict(),
        },
        "visit_count_chain_removal": {
            **after_chain_summary,
            "component_count": (
                len(components_after_chain.component_members)
                + components_after_chain.isolated_merchant_count
            ),
            "isolated_component_count": components_after_chain.isolated_merchant_count,
            "largest_component_size": (
                max(after_chain_component_sizes)
                if after_chain_component_sizes
                else 0
            ),
            "component_size_distribution": calculate_distribution_with_repeated_minimum(
                after_chain_component_sizes,
                1.0,
                components_after_chain.isolated_merchant_count,
            )._asdict(),
            "degree_distribution": calculate_distribution_with_repeated_minimum(
                metric_sources.after_chain.degree.values(),
                0.0,
                len(remaining_merchants) - len(metric_sources.after_chain.degree),
            )._asdict(),
        },
        "merchant_metric_definitions": {
            "raw_degree": "未过滤商户对数量",
            "minimum_support_degree": "通过最小支持人数后的商户对数量",
            "sppmi_candidate_degree": "通过 SPPMI 和 z-score 后的候选邻居数",
            "mutual_top_k_degree": "互为 top-k 后的实际交易图度数",
            "component_size": "访问量型商户删除前所在连通分量商户数",
            "degree_after_chain_removal": "访问量型商户删除后的度数",
            "component_size_after_chain_removal": (
                "访问量型商户删除后所在连通分量商户数"
            ),
            "lost_edge_count_from_chain_removal": (
                "访问量型商户删除导致该商户损失的边数"
            ),
        },
        "top_merchants": {
            "by_visit_count": select_top_merchants(
                merchant_metrics,
                top_merchant_ids["by_visit_count"],
            ),
            "by_raw_degree": select_top_merchants(
                merchant_metrics,
                top_merchant_ids["by_raw_degree"],
            ),
            "by_minimum_support_degree": select_top_merchants(
                merchant_metrics,
                top_merchant_ids["by_minimum_support_degree"],
            ),
            "by_sppmi_candidate_degree": select_top_merchants(
                merchant_metrics,
                top_merchant_ids["by_sppmi_candidate_degree"],
            ),
            "by_mutual_top_k_degree": select_top_merchants(
                merchant_metrics,
                top_merchant_ids["by_mutual_top_k_degree"],
            ),
            "by_lost_edge_count_from_chain_removal": select_top_merchants(
                merchant_metrics,
                top_merchant_ids[
                    "by_lost_edge_count_from_chain_removal"
                ],
            ),
        },
        "limitations": [
            "中间文件不包含商户分类，无法复算分类 2 商户删除。",
            "中间文件不包含坐标，无法复算地理种子边和疑似线上商户删除。",
            "本脚本分析到 Leiden 之前的交易图，不执行社区发现。",
            "pickle 文件必须来自可信任务输出。",
        ],
    }


def load_task_platform() -> TaskPlatform:
    try:
        from spdbccc_data import formattedExc
        from spdbccc_data import loging as logrecord
        from spdbccc_data import mountCheck
        from spdbccc_data import task as taskfinish
    except ImportError as error:
        raise RuntimeError(
            "无法导入 spdbccc_data 任务组件，请在线上任务环境中运行此脚本"
        ) from error
    return TaskPlatform(
        mount_check=mountCheck.mount_check,
        log_data=logrecord.log_data,
        format_exception=formattedExc.formatted_exc,
        finish_task=taskfinish.finish_task,
    )


class TaskMain:
    def __init__(
        self,
        platform: TaskPlatform,
        input_path: Path,
        minimum_support: int,
        context_smoothing_alpha: float,
        sppmi_shift: float,
        minimum_z_score: float,
        top_k_neighbors: int,
        chain_visit_count_quantile: float,
        chain_minimum_visit_count: int,
        top_merchant_count: int,
    ) -> None:
        self.platform = platform
        self.input_path = input_path
        self.minimum_support = minimum_support
        self.context_smoothing_alpha = context_smoothing_alpha
        self.sppmi_shift = sppmi_shift
        self.minimum_z_score = minimum_z_score
        self.top_k_neighbors = top_k_neighbors
        self.chain_visit_count_quantile = chain_visit_count_quantile
        self.chain_minimum_visit_count = chain_minimum_visit_count
        self.top_merchant_count = top_merchant_count

    def check(self) -> None:
        self.platform.mount_check()

    def taskrun(self) -> Dict[str, object]:
        started_at = time.time()
        self.platform.log_data(
            f"pair statistics analysis task start input_path={self.input_path}"
        )
        try:
            report = analyze_pair_statistics(
                self.input_path,
                self.minimum_support,
                self.context_smoothing_alpha,
                self.sppmi_shift,
                self.minimum_z_score,
                self.top_k_neighbors,
                self.chain_visit_count_quantile,
                self.chain_minimum_visit_count,
                self.top_merchant_count,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            data_summary = cast(Mapping[str, object], report["data"])
            self.platform.log_data(
                "pair statistics analysis task success "
                f"seconds={time.time() - started_at:.2f}, "
                f"merchant_count={data_summary['merchant']}, "
                f"pair_count={data_summary['pair']}"
            )
            return report
        except Exception:
            self.platform.format_exception()
            raise

    def destroy(self) -> None:
        self.platform.log_data(
            "pair statistics analysis task destroy success temporary_resource_count=0"
        )


def run_task(task: TaskMain) -> Dict[str, object]:
    try:
        task.check()
        return task.taskrun()
    finally:
        try:
            task.destroy()
        finally:
            task.platform.finish_task()


def main() -> None:
    task = TaskMain(
        load_task_platform(),
        INPUT_PATH,
        MINIMUM_SUPPORT,
        CONTEXT_SMOOTHING_ALPHA,
        SPPMI_SHIFT,
        MINIMUM_Z_SCORE,
        TOP_K_NEIGHBORS,
        CHAIN_VISIT_COUNT_QUANTILE,
        CHAIN_MINIMUM_VISIT_COUNT,
        TOP_MERCHANT_COUNT,
    )
    run_task(task)


if __name__ == "__main__":
    main()
