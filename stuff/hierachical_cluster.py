import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import networkx as nx
from community import community_louvain
import os
import csv
import shutil
import dask.dataframe as dd
import pickle
from collections import Counter
from sklearn.cluster import AgglomerativeClustering

OUTPUT_DIR = (
    '/home/uatvv001504@uos/Downloads/'
    'jhj_jy_shanghai_online_20260401_20260414_简单处理/'
    'shanghai_202604_空间层次聚类/'
)
INCREMENTAL_OUTPUT_DIR = (
    '/home/uatvv001504@uos/Downloads/'
    'jhj_jy_shanghai_online_20260401_20260414_简单处理/'
    'shanghai_202604_空间层次聚类_增量更新/'
)
MIN_GEO_CLUSTER_MERCHANTS = 3
MIN_KEYWORD_MATCH_SCORE = 6
KEYWORD_AMBIGUITY_RATIO = 0.2
ACCOUNT_COLUMN = 'account_number'
GLOBAL_FLOW_COLUMN = 'global_flow_number'
STORE_COLUMN = 'storename'
TIME_COLUMN = 'transaction_time'
REGION_COLUMN = 'region'
INTERFERE_COLUMN = 'is_interfere'
ABNORMAL_COLUMN = 'is_abnormal'
LONGITUDE_COLUMN = 'pos_longitude'
LATITUDE_COLUMN = 'pos_latitude'
NORMAL_STATUS = 'normal'
INACTIVE_STATUS = 'suspect_inactive'
ONLINE_STATUS = 'suspect_online'
ISOLATED_STATUS = 'suspect_isolated'
INPUT_COLUMNS = [
    ACCOUNT_COLUMN,
    GLOBAL_FLOW_COLUMN,
    STORE_COLUMN,
    TIME_COLUMN,
    LONGITUDE_COLUMN,
    LATITUDE_COLUMN,
    REGION_COLUMN,
    INTERFERE_COLUMN,
    ABNORMAL_COLUMN
]
VALID_ABNORMAL_STATUSES = {
    NORMAL_STATUS,
    INACTIVE_STATUS,
    ONLINE_STATUS,
    ISOLATED_STATUS
}
FINAL_ASSIGNMENT_COLUMNS = [
    'storename',
    'community_id',
    'previous_community_id',
    'region',
    'is_interfere',
    'update_time',
    'is_abnormal',
    'is_position'
]

SHANGHAI_LOCAL_KEYWORDS = {
    '上海', '浦东', '浦东新区', '黄浦', '黄浦区', '徐汇', '徐汇区',
    '长宁', '长宁区', '静安', '静安区', '普陀', '普陀区',
    '虹口', '虹口区', '杨浦', '杨浦区', '闵行', '闵行区',
    '宝山', '宝山区', '嘉定', '嘉定区', '金山', '金山区',
    '松江', '松江区', '青浦', '青浦区', '奉贤', '奉贤区',
    '崇明', '崇明区', '陆家嘴', '人民广场', '南京东路',
    '南京西路', '南京路', '外滩', '豫园', '城隍庙', '新天地',
    '淮海路', '淮海中路', '徐家汇', '衡山路', '打浦桥',
    '五角场', '中山公园', '静安寺', '曹家渡', '大宁',
    '大悦城', '北外滩', '四川北路', '七宝', '莘庄', '虹桥',
    '虹桥天地', '古北', '龙柏', '漕河泾', '张江', '金桥',
    '川沙', '周浦', '康桥', '三林', '前滩', '世纪大道',
    '世纪公园', '八佰伴', '陆家浜路', '杨高中路', '龙阳路',
    '花木', '唐镇', '临港', '滴水湖', '合生汇'
}

SHANGHAI_ROAD_KEYWORDS = {
    '南京路', '南京东路', '南京西路', '北京东路', '北京西路',
    '西藏中路', '西藏南路', '河南中路', '河南南路', '四川北路',
    '四川中路', '四川南路', '广东路', '广西北路', '浙江中路',
    '浙江南路', '福建中路', '福建南路', '江西中路', '江西南路',
    '云南南路', '贵州路', '山西南路', '山东中路', '山东南路',
    '湖北路', '湖南路', '重庆南路', '成都北路', '成都南路',
    '陕西北路', '陕西南路', '乌鲁木齐中路', '乌鲁木齐南路',
    '常德路', '武康路', '淮海中路', '淮海西路', '肇嘉浜路',
    '延安东路', '延安中路', '延安西路', '中山北路', '中山西路',
    '中山南路', '大学路', '国定路', '淞沪路'
}

CUISINE_BRAND_WHITELIST = {
    '兰州牛肉面', '兰州拉面', '沙县小吃', '重庆小面', '重庆火锅',
    '成都串串', '成都冒菜', '桂林米粉', '柳州螺蛳粉',
    '淮南牛肉汤', '黄焖鸡米饭', '南京大牌档', '北京烤鸭',
    '新疆炒米粉', '新疆羊肉串', '西安肉夹馍', '陕西面馆',
    '山西刀削面', '老北京炸酱面', '东北饺子', '东北菜',
    '湖南米粉', '长沙臭豆腐', '武汉热干面', '潮汕牛肉火锅',
    '潮汕砂锅粥', '广式烧腊', '港式茶餐厅', '云南过桥米线',
    '贵州酸汤鱼', '台湾卤肉饭', '澳门豆捞', '扬州炒饭'
}

NON_SHANGHAI_PLACE_KEYWORDS = {
    '北京', '天津', '重庆', '广州', '深圳', '杭州', '南京', '苏州',
    '无锡', '常州', '宁波', '温州', '绍兴', '嘉兴', '湖州',
    '合肥', '芜湖', '武汉', '长沙', '南昌', '福州', '厦门',
    '泉州', '济南', '青岛', '郑州', '洛阳', '西安', '成都',
    '昆明', '贵阳', '南宁', '海口', '三亚', '哈尔滨', '沈阳',
    '大连', '长春', '太原', '石家庄', '兰州', '西宁', '银川',
    '乌鲁木齐', '拉萨', '呼和浩特', '香港', '澳门', '台北'
}

MANUAL_AREA_KEYWORD_GROUPS = [
    {'人民广场', '静安大悦城', '新世界', '世茂'},
    {'新天地', '凯德晶萃', '思南公馆'},
    {'世纪汇', '大都会'},
    {'大宁', '共和新路'},
    {'五角场', '合生汇', '大学路', '复旦大学'},
    {'淮海中路', '环贸广场', 'CP静安'},
    {'南洋1931', '飞洲国际', '上海体育馆'},
    {'莲花路', '中庚', '闵行百联', '百联南方商城'},
    {'江浦路', '环球超市', '紫荆广场', '鞍山'},
    {'漕河泾'},
    {'静安'},
    {'维璟'},
    {'豫园'},
    {'吴江路', '恒基'},
    {'肇嘉浜路', '零陵路'},
    {'宜山路', '光启'},
    {'康定路', '长寿路', '武宁路'},
    {'长宁大融城', '荟聚'},
    {'武康大楼', '港汇', '徐家汇', 'ITC', '恒隆', '美罗城'},
    {'同济大学', '四平路'},
    {'世博'},
    {'古美路', '一品名仕'},
    {'杨树浦路', '渔人码头', '平凉路', '双阳路'},
    {'万象城', '虹桥'},
    {'顾村'},
    {'沪太', '大华'},
    {'前滩', '太古里'},
    {'中山公园', '长宁来福士', '长宁龙之梦', '兆丰'},
    {'宝杨'},
    {'周浦', '康桥', '绿地缤纷'},
    {'环宇', '真如'},
    {'塘桥', '浦建', '蓝村'},
    {'龙华', '徐汇日月光'},
    {'前湾'},
    {'南翔印象'},
    {'杨浦悠方', '新江湾城'},
    {'满天星', '马桥', '迎春路', '星悦荟'},
    {'丁香', '联洋', '天物空间'},
    {'田林', '鑫耀', '桂林公园'},
    {'环球港', '金沙江路'},
    {'今雨荟'},
    {'海梦一方', '闻喜'},
    {'宝山万达', '中治', '共康路'},
    {'陆家嘴', '国金', '正大广场'},
    {'陆家嘴中心', '八佰伴', '新梅联合广场'},
    {'LCM', '置汇旭辉'},
    {'七宝'},
    {'奉贤宝龙'},
    {'吾悦'},
    {'南桥百联', '奉贤'},
    {'南汇', '通济', '惠南'},
    {'三林', '灵岩'},
    {'太阳宫', '月亮湾'},
    {'虹口龙之梦', '鲁迅公园', '瑞虹', '和平公园'},
    {'美兰湖'},
    {'南码头', '百联临沂'},
    {'汾西路', '彭浦新村'},
    {'金山万达', '金山欧尚'},
    {'南桥'},
    {'莘庄龙之梦'},
    {'五彩城'},
    {'金桥国际'},
    {'虹桥天地', '虹桥龙湖'},
    {'松江大学'},
    {'佘山', '宝乐汇'},
    {'森兰'},
    {'古北'},
    {'崇明万达'},
    {'江桥万达'},
    {'长风公园', '长风大悦城'},
    {'唐镇'},
    {'临港'},
    {'白玉兰广场', '北外滩'},
    {'宝山花园'},
    {'松江印象城'}
]

GENERIC_KEYWORDS = {
    '上海', '店', '分店', '门店', '餐饮', '食品', '小吃', '便利',
    '超市', '公司', '服务', '支付', '平台', '商贸', '管理',
    '有限', '有限公司', '广场店'
}

# -------------------- 数据加载与预处理 --------------------
def detect_transaction_has_header(file_path, delimiter='US'):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        first_line = f.readline().strip()

    if not first_line:
        return False

    fields = [field.strip() for field in first_line.split(delimiter)]
    normalized_fields = {
        field.lower()
        for field in fields
    }
    expected_fields = {
        column.lower()
        for column in INPUT_COLUMNS
    }
    legacy_fields = {'account_number', 'cardno', 'storename', 'transaction_time'}
    return bool(normalized_fields & (expected_fields | legacy_fields))


def read_transaction_ddf(file_path, blocksize='256MB'):
    has_header = detect_transaction_has_header(file_path)
    read_kwargs = {
        'blocksize': blocksize,
        'delimiter': 'US',
        'dtype': str
    }
    if has_header:
        ddf = dd.read_csv(file_path, **read_kwargs)
    else:
        ddf = dd.read_csv(
            file_path,
            header=None,
            names=INPUT_COLUMNS,
            **read_kwargs
        )

    return ddf.rename(columns={
        'ACCOUNT_NUMBER': ACCOUNT_COLUMN,
        'cardno': ACCOUNT_COLUMN,
        'posLong': LONGITUDE_COLUMN,
        'posLat': LATITUDE_COLUMN
    })


def read_large_data(file_path, blocksize='256MB'):
    try:
        if not os.path.exists(file_path):
            print(f"错误: 文件 {file_path} 不存在")
            return None

        ddf = read_transaction_ddf(file_path, blocksize=blocksize)

        required_columns = [ACCOUNT_COLUMN, TIME_COLUMN, STORE_COLUMN]
        for col in required_columns:
            if col not in ddf.columns:
                print(f"错误: 文件中缺少必要的列 '{col}'")
                return None

        return ddf
    except Exception as e:
        print(f"读取文件时发生错误: {e}")
        return None


def preprocess_chunk(chunk):
    chunk = chunk.rename(columns={
        'ACCOUNT_NUMBER': ACCOUNT_COLUMN,
        'cardno': ACCOUNT_COLUMN,
        'posLong': LONGITUDE_COLUMN,
        'posLat': LATITUDE_COLUMN
    })
    chunk['datetime'] = pd.to_datetime(chunk[TIME_COLUMN], format='%Y-%m-%d %H:%M:%S', errors='coerce')
    chunk = chunk.dropna(subset=['datetime'])  # 移除时间解析失败的行
    chunk = chunk.drop_duplicates(
        subset=[ACCOUNT_COLUMN, TIME_COLUMN, STORE_COLUMN],
        keep='first'
    )
    chunk = chunk.copy()
    chunk[ACCOUNT_COLUMN] = chunk[ACCOUNT_COLUMN].astype(str)
    chunk[STORE_COLUMN] = chunk[STORE_COLUMN].astype(str)
    if REGION_COLUMN not in chunk.columns:
        chunk[REGION_COLUMN] = ''
    chunk[REGION_COLUMN] = chunk[REGION_COLUMN].fillna('').astype(str)
    if INTERFERE_COLUMN not in chunk.columns:
        chunk[INTERFERE_COLUMN] = 0
    chunk[INTERFERE_COLUMN] = (
        pd.to_numeric(chunk[INTERFERE_COLUMN], errors='coerce')
        .fillna(0)
        .astype(int)
        .eq(1)
        .astype(int)
    )
    if ABNORMAL_COLUMN not in chunk.columns:
        chunk[ABNORMAL_COLUMN] = NORMAL_STATUS
    chunk[ABNORMAL_COLUMN] = (
        chunk[ABNORMAL_COLUMN]
        .fillna(NORMAL_STATUS)
        .astype(str)
        .str.strip()
        .str.lower()
    )
    chunk.loc[
        ~chunk[ABNORMAL_COLUMN].isin(VALID_ABNORMAL_STATUSES),
        ABNORMAL_COLUMN
    ] = NORMAL_STATUS
    for geo_column in [LONGITUDE_COLUMN, LATITUDE_COLUMN]:
        if geo_column not in chunk.columns:
            chunk[geo_column] = np.nan
        chunk[geo_column] = pd.to_numeric(chunk[geo_column], errors='coerce')
    chunk = chunk.sort_values(['datetime', ACCOUNT_COLUMN, STORE_COLUMN])
    chunk = chunk[~chunk[STORE_COLUMN].str.contains('个体户', na=False)]
    return chunk


def build_transaction_pairs(df, time_window=30):
    transaction_pairs = []
    time_delta = timedelta(minutes=time_window)

    for cardno, group in df.groupby(ACCOUNT_COLUMN):
        group = group.sort_values('datetime').reset_index(drop=True)
        n_transactions = len(group)
        """
        for i in range(n_transactions - 1):
            current_trans = group.iloc[i]
            next_trans = group.iloc[i+1]
            if next_trans['datetime'] - current_trans['datetime'] <= time_delta:
                merchant1 = current_trans[STORE_COLUMN]
                merchant2 = next_trans[STORE_COLUMN]

                if merchant1 != merchant2:
                    pair = tuple(sorted([merchant1, merchant2]))
                    transaction_pairs.append(pair)
        """

        for i in range(n_transactions - 1):
            current_trans = group.iloc[i]
            merchant1 = current_trans[STORE_COLUMN]
            current_time = current_trans['datetime']

            for j in range(i + 1, n_transactions):
                next_trans = group.iloc[j]
                next_time = next_trans['datetime']
                if next_time - current_time > time_delta:
                    break

                merchant2 = next_trans[STORE_COLUMN]
                if merchant1 == merchant2:
                    continue

                diff_minutes = max((next_time - current_time).total_seconds() / 60.0, 1e-6)

                if diff_minutes <= 5:
                    pair_weight = 3
                elif diff_minutes <= 15:
                    pair_weight = 2
                else:
                    pair_weight = 1

                pair = tuple(sorted([merchant1, merchant2]))
                transaction_pairs.append((pair[0], pair[1], pair_weight))

    return transaction_pairs


# -------------------- 图构建 --------------------
class GraphBuilder:

    def __init__(self):
        self.G = nx.Graph()
        self.edge_weights = {}
        self.community_partition = {}
        self.merchant_transaction_counts = Counter()
        self.merchant_to_geo_cluster = {}
        self.geo_cluster_centers = {}
        self.geo_cluster_total_weights = {}
        self.interfered_merchants = set()
        self.merchant_abnormal_statuses = {}
        self.merchant_regions = {}
        self.merchant_last_transaction = {}

    def update_with_transaction_pairs(self, transaction_pairs, valid_merchants=None):
        all_merchants = set()
        for pair in transaction_pairs:
            if len(pair) == 3:
                m1, m2, pair_weight = pair
            else:
                m1, m2 = pair
                pair_weight = 1

            if valid_merchants is not None:
                if m1 not in valid_merchants or m2 not in valid_merchants:
                    continue
            all_merchants.update([m1, m2])
        self.G.add_nodes_from(all_merchants)

        for pair in transaction_pairs:
            if len(pair) == 3:
                m1, m2, pair_weight = pair
            else:
                m1, m2 = pair
                pair_weight = 1
            if valid_merchants is not None:
                if m1 not in valid_merchants or m2 not in valid_merchants:
                    continue
            edge_key = tuple(sorted((m1, m2)))
            self.edge_weights[edge_key] = self.edge_weights.get(edge_key, 0) + pair_weight

    def finalize_graph(self, min_weight=1):
        for pair, weight in self.edge_weights.items():
            if weight >= min_weight:
                self.G.add_edge(pair[0], pair[1], weight=weight)
        return self.G

    def save_state(self, path):
        temp_path = path + ".tmp"
        with open(temp_path, 'wb') as f:
            pickle.dump({
                'G': self.G,
                'edge_weights': self.edge_weights,
                'community_partition': self.community_partition,
                'merchant_transaction_counts': self.merchant_transaction_counts,
                'merchant_to_geo_cluster': self.merchant_to_geo_cluster,
                'geo_cluster_centers': self.geo_cluster_centers,
                'geo_cluster_total_weights': self.geo_cluster_total_weights,
                'interfered_merchants': self.interfered_merchants,
                'merchant_abnormal_statuses': self.merchant_abnormal_statuses,
                'merchant_regions': self.merchant_regions,
                'merchant_last_transaction': self.merchant_last_transaction,
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }, f)
        os.replace(temp_path, path)

    def load_state(self, path):
        with open(path, 'rb') as f:
            state = pickle.load(f)
            self.G = state['G']
            self.edge_weights = state['edge_weights']
            self.community_partition = state.get('community_partition', {})
            self.merchant_transaction_counts = Counter(
                state.get('merchant_transaction_counts', {})
            )
            self.merchant_to_geo_cluster = state.get(
                'merchant_to_geo_cluster',
                {}
            )
            self.geo_cluster_centers = state.get('geo_cluster_centers', {})
            self.geo_cluster_total_weights = state.get(
                'geo_cluster_total_weights',
                {}
            )
            self.interfered_merchants = set(
                state.get('interfered_merchants', set())
            )
            self.merchant_abnormal_statuses = state.get(
                'merchant_abnormal_statuses',
                {}
            )
            self.merchant_regions = state.get('merchant_regions', {})
            self.merchant_last_transaction = state.get(
                'merchant_last_transaction',
                {}
            )


# -------------------- 社区发现 --------------------
def detect_communities(G, resolution=0.5):
    partition = community_louvain.best_partition(G, weight='weight', resolution=resolution, random_state=42)
    #partition = {str(k): v for k, v in partition.items()}
    communities = {}
    for node, comm_id in partition.items():
        #if comm_id not in communities:
        #    communities[comm_id] = []
        #communities[comm_id].append(node)
        communities.setdefault(comm_id, []).append(node)
    return communities, partition


def partition_to_communities(partition):
    communities = {}
    for node, comm_id in partition.items():
        communities.setdefault(comm_id, []).append(node)
    return communities


def update_graph_builder_input_metadata(graph_builder, chunk):
    if chunk.empty:
        return

    latest_rows = chunk.sort_values('datetime').drop_duplicates(
        STORE_COLUMN,
        keep='last'
    )
    for row in latest_rows.itertuples(index=False):
        merchant = str(getattr(row, STORE_COLUMN))
        region = getattr(row, REGION_COLUMN, '')
        is_interfere = int(getattr(row, INTERFERE_COLUMN, 0) or 0)
        abnormal_status = getattr(row, ABNORMAL_COLUMN, NORMAL_STATUS)
        transaction_time = getattr(row, 'datetime')

        if is_interfere == 1:
            graph_builder.interfered_merchants.add(merchant)
        else:
            graph_builder.interfered_merchants.discard(merchant)
        if not is_blank_value(region):
            graph_builder.merchant_regions[merchant] = str(region)
        previous_time = graph_builder.merchant_last_transaction.get(merchant)
        if previous_time is None or transaction_time > previous_time:
            graph_builder.merchant_last_transaction[merchant] = transaction_time
        if abnormal_status in VALID_ABNORMAL_STATUSES:
            graph_builder.merchant_abnormal_statuses[merchant] = abnormal_status


def get_inactive_merchants(graph_builder, active_merchants, days=30):
    transaction_times = [
        transaction_time
        for merchant, transaction_time in graph_builder.merchant_last_transaction.items()
        if merchant in active_merchants and pd.notna(transaction_time)
    ]
    if not transaction_times:
        return set()

    latest_time = max(transaction_times)
    inactive_before = latest_time - timedelta(days=days)
    return {
        merchant
        for merchant in active_merchants
        if graph_builder.merchant_last_transaction.get(merchant) is not None
        and graph_builder.merchant_last_transaction[merchant] < inactive_before
    }


def get_suspect_isolated_merchants(G, communities):
    isolated_merchants = set()
    merchant_to_comm = {
        merchant: comm_id
        for comm_id, merchants in communities.items()
        for merchant in merchants
    }

    for comm_id, merchants in communities.items():
        merchant_set = set(merchants)
        subgraph = G.subgraph(merchant_set)
        internal_edges = subgraph.number_of_edges()
        internal_weight = sum(
            data.get('weight', 1)
            for _, _, data in subgraph.edges(data=True)
        )
        cross_edges = 0
        for merchant in merchant_set:
            if merchant not in G:
                continue
            for neighbor in G.neighbors(merchant):
                if merchant_to_comm.get(neighbor) != comm_id:
                    cross_edges += 1
        if (internal_edges <= 1 or internal_weight <= 2) and cross_edges == 0:
            isolated_merchants.update(merchant_set)

    return isolated_merchants


def local_detect_communities(
    G,
    old_partition,
    affected_nodes,
    resolution=0.5,
    hops=1
):
    if G.number_of_nodes() == 0:
        return {}, set()

    graph_nodes = set(G.nodes())
    local_nodes = set(affected_nodes) & graph_nodes
    frontier = set(local_nodes)
    for _ in range(max(hops, 0)):
        next_frontier = set()
        for node in frontier:
            next_frontier.update(G.neighbors(node))
        next_frontier -= local_nodes
        if not next_frontier:
            break
        local_nodes.update(next_frontier)
        frontier = next_frontier

    new_partition = {
        node: comm_id
        for node, comm_id in old_partition.items()
        if node in graph_nodes and node not in local_nodes
    }
    if not local_nodes:
        return new_partition, local_nodes

    local_graph = G.subgraph(local_nodes)
    if local_graph.number_of_edges() > 0:
        local_partition = community_louvain.best_partition(
            local_graph,
            weight='weight',
            resolution=resolution,
            random_state=42
        )
    else:
        local_partition = {
            node: index
            for index, node in enumerate(sorted(local_nodes, key=str))
        }

    next_comm_id = max(old_partition.values(), default=0) + 1
    used_history_ids = set()
    local_communities = partition_to_communities(local_partition)
    ranked_groups = []

    for nodes in local_communities.values():
        overlap_scores = Counter()
        edge_scores = Counter()
        for node in nodes:
            old_comm_id = old_partition.get(node)
            if old_comm_id is not None:
                overlap_scores[old_comm_id] += 1
            for neighbor, edge_data in G[node].items():
                neighbor_comm_id = old_partition.get(neighbor)
                if neighbor_comm_id is not None:
                    edge_scores[neighbor_comm_id] += edge_data.get('weight', 1)

        candidate_ids = set(overlap_scores) | set(edge_scores)
        candidates = sorted(
            candidate_ids,
            key=lambda comm_id: (
                -edge_scores[comm_id],
                -overlap_scores[comm_id],
                str(comm_id)
            )
        )
        best_edge_score = edge_scores[candidates[0]] if candidates else 0
        best_overlap_score = overlap_scores[candidates[0]] if candidates else 0
        ranked_groups.append((
            -best_edge_score,
            -best_overlap_score,
            -len(nodes),
            nodes,
            candidates
        ))

    ranked_groups.sort(key=lambda item: item[:3])
    for _, _, _, nodes, candidates in ranked_groups:
        target_comm_id = next(
            (
                comm_id
                for comm_id in candidates
                if comm_id not in used_history_ids
            ),
            None
        )
        if target_comm_id is None:
            target_comm_id = next_comm_id
            next_comm_id += 1
        else:
            used_history_ids.add(target_comm_id)

        for node in nodes:
            new_partition[node] = target_comm_id

    print(f"增量受影响种子节点数: {len(set(affected_nodes) & graph_nodes)}")
    print(f"扩展 {hops} 跳后局部聚类节点数: {len(local_nodes)}")
    print(f"保持历史社区节点数: {len(graph_nodes - local_nodes)}")
    return new_partition, local_nodes


# -------------------- 计算社区交易量 --------------------
def coverage(graph, partition):
    total_weight = sum(d.get('weight', 1) for _, _, d in graph.edges(data=True))
    if total_weight == 0:
        return 0.0

    internal_weight = 0
    for u, v, d in graph.edges(data=True):
        if partition.get(u) == partition.get(v):
            internal_weight += d.get('weight', 1)
    return internal_weight / total_weight


def avg_conductance(graph, partition):
    comm_nodes = {}
    for node, cid in partition.items():
        comm_nodes.setdefault(cid, set()).add(node)
    conductances = []
    for nodes in comm_nodes.values():
        subgraph = graph.subgraph(nodes)
        internal = subgraph.number_of_edges()
        cut = 0
        for node in nodes:
            for neighbor in graph.neighbors(node):
                if neighbor not in nodes:
                    cut += 1
        cut //= 2
        denom = 2 * internal + cut
        if denom > 0:
            cond = cut / denom
        else:
            cond = 0.0
        conductances.append(cond)
    return np.mean(conductances) if conductances else 0.0


def compute_all_metrics(graph, partition):
    comm_nodes = {}
    for node, cid in partition.items():
        comm_nodes.setdefault(cid, set()).add(node)
    communities_list = list(comm_nodes.values())

    metrics = {}
    metrics['modularity'] = nx.community.modularity(graph, communities_list)
    metrics['coverage'] = coverage(graph, partition)
    metrics['avg_conductance'] = avg_conductance(graph, partition)
    return metrics


def count_inter_community_edges(G, partition):
    intra_edges = 0
    inter_edges = 0
    intra_weight = 0
    inter_weight = 0
    for u, v, d in G.edges(data=True):
        w = d.get('weight', 1)
        if partition.get(u) == partition.get(v):
            intra_edges += 1
            intra_weight += w
        else:
            inter_edges += 1
            inter_weight += w
    total_edges = intra_edges + inter_edges
    total_weight = intra_weight + inter_weight
    print(
        f"社区边统计: 内部边 {intra_edges}, 跨社区边 {inter_edges}, "
        f"跨社区边权占比 "
        f"{inter_weight / total_weight if total_weight else 0:.4f}"
    )


def count_merchant_transactions(file_path, blocksize='256MB'):
    merchant_transactions = Counter()
    ddf = read_transaction_ddf(file_path, blocksize=blocksize)

    ddf = ddf[[STORE_COLUMN]]
    total_partitions = ddf.npartitions
    print(f"开始统计商户交易次数,共{total_partitions}个分区")
    for i in range(total_partitions):
        chunk = ddf.get_partition(i).compute()
        counts = chunk[STORE_COLUMN].value_counts().to_dict()
        for storename, cnt in counts.items():
            merchant_transactions[storename] += cnt
    return merchant_transactions


def calculate_community_transactions(file_path, merchant_to_comm):
    """
    统计每个社区的总交易笔数
    参数:
    - file_path: 原始交易数据路径
    - merchant_to_comm: 商户到社区的映射 {storename: comm_id}
    返回:
    - 社区交易量字典 {comm_id: 交易笔数}
    """
    comm_transactions = Counter()

    # 分块读取交易数据统计
    ddf = read_transaction_ddf(file_path, blocksize='256MB')

    # 只需要商户ID列
    ddf = ddf[[STORE_COLUMN]]

    total_partitions = ddf.npartitions
    print(f"开始统计社区交易量,共 {total_partitions} 个数据分区...")

    for i in range(total_partitions):
        chunk = ddf.get_partition(i).compute()
        # 统计每个商户在当前分区的交易次数
        counts = chunk[STORE_COLUMN].value_counts().to_dict()
        # 累加至对应社区
        for merchant, count in counts.items():
            if merchant in merchant_to_comm:
                comm_id = merchant_to_comm[merchant]
                comm_transactions[comm_id] += count

    return comm_transactions


# -------------------- 智能文件导出 --------------------
def smart_export(df, output_path, max_csv_rows=1000000):
    if len(df) <= max_csv_rows:
        df.to_csv(output_path, index=False)
    else:
        txt_path = os.path.splitext(output_path)[0] + ".txt"
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('|'.join(df.columns) + '\n')
            for _, row in df.iterrows():
                f.write('|'.join(map(str, row.values)) + '\n')


RESULT_FILE_NAMES = [
    'merchant_community_assignments.txt',
    'community_statistics.txt',
    'full_community_results.pkl',
    'merchant_geo_cluster_assignments.txt',
    'geo_cluster_statistics.txt',
    'graph_builder_checkpoint.pkl'
]


def create_staging_output_dir(output_dir):
    staging_dir = os.path.join(
        output_dir,
        f".staging_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    )
    os.makedirs(staging_dir, exist_ok=False)
    return staging_dir


def recover_interrupted_output_update(output_dir):
    if not os.path.isdir(output_dir):
        return

    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        if name.startswith('.staging_') and os.path.isdir(path):
            shutil.rmtree(path)
            continue

        if not name.startswith('.backup_') or not os.path.isdir(path):
            continue

        commit_marker = os.path.join(path, '.commit_complete')
        if os.path.exists(commit_marker):
            shutil.rmtree(path)
            continue

        print(f"检测到未完成的历史文件替换，正在回滚: {path}")
        journal_path = os.path.join(path, '.processed_files')
        processed_files = set()
        if os.path.exists(journal_path):
            with open(journal_path, 'r', encoding='utf-8') as journal:
                processed_files = {
                    line.strip()
                    for line in journal
                    if line.strip()
                }

        for file_name in processed_files:
            target_path = os.path.join(output_dir, file_name)
            if os.path.exists(target_path):
                os.remove(target_path)

        for file_name in RESULT_FILE_NAMES:
            backup_path = os.path.join(path, file_name)
            target_path = os.path.join(output_dir, file_name)
            if not os.path.exists(backup_path):
                continue
            os.replace(backup_path, target_path)
        shutil.rmtree(path)
        print("历史文件回滚完成")


def commit_staged_results(staging_dir, output_dir):
    missing_files = [
        file_name
        for file_name in RESULT_FILE_NAMES
        if not os.path.isfile(os.path.join(staging_dir, file_name))
        or os.path.getsize(os.path.join(staging_dir, file_name)) == 0
    ]
    if missing_files:
        raise RuntimeError(f"新结果文件未完整生成: {missing_files}")

    backup_dir = os.path.join(
        output_dir,
        f".backup_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    )
    os.makedirs(backup_dir, exist_ok=False)
    journal_path = os.path.join(backup_dir, '.processed_files')
    replaced_targets = []
    backed_up_targets = []

    try:
        for file_name in RESULT_FILE_NAMES:
            staged_path = os.path.join(staging_dir, file_name)
            target_path = os.path.join(output_dir, file_name)
            backup_path = os.path.join(backup_dir, file_name)

            if os.path.exists(target_path):
                os.replace(target_path, backup_path)
                backed_up_targets.append((backup_path, target_path))

            with open(journal_path, 'a', encoding='utf-8') as journal:
                journal.write(file_name + '\n')

            os.replace(staged_path, target_path)
            replaced_targets.append(target_path)

        with open(
            os.path.join(backup_dir, '.commit_complete'),
            'w',
            encoding='utf-8'
        ) as marker:
            marker.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    except Exception:
        for target_path in reversed(replaced_targets):
            if os.path.exists(target_path):
                os.remove(target_path)
        for backup_path, target_path in reversed(backed_up_targets):
            if os.path.exists(backup_path):
                os.replace(backup_path, target_path)
        raise
    finally:
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)

    try:
        shutil.rmtree(backup_dir)
    except OSError as exc:
        print(f"历史备份目录清理失败，可稍后手动清理: {backup_dir}, 原因: {exc}")
    print("新结果文件全部生成成功，历史文件已安全替换")
    print(f"正式输出目录: {output_dir}")


def aggregate_community_transactions(merchant_transactions, merchant_to_comm):
    comm_transactions = Counter()
    for merchant, count in merchant_transactions.items():
        comm_id = merchant_to_comm.get(merchant)
        if comm_id is not None:
            comm_transactions[comm_id] += count
    return comm_transactions


def build_position_flags(G, communities, top_n=5):
    position_flags = {}
    for merchants in communities.values():
        merchant_set = set(merchants)
        ranked_merchants = sorted(
            merchants,
            key=lambda merchant: (
                -sum(
                    edge_data.get('weight', 1)
                    for neighbor, edge_data in G[merchant].items()
                    if neighbor in merchant_set
                ) if merchant in G else 0,
                str(merchant)
            )
        )
        position_merchants = set(ranked_merchants[:top_n])
        for merchant in merchants:
            position_flags[merchant] = 1 if merchant in position_merchants else 0
    return position_flags


def save_community_assignments(G, communities, output_dir, risk_types=None):
    risk_types = risk_types or {}
    position_flags = build_position_flags(G, communities, top_n=5)
    data = []
    for comm_id, merchants in communities.items():
        for merchant in merchants:
            data.append([
                merchant,
                comm_id,
                position_flags.get(merchant, 0),
                risk_types.get(merchant, '')
            ])
    df = pd.DataFrame(
        data,
        columns=['storename', 'community_id', 'is_position', 'risk_type']
    )
    output_path = os.path.join(output_dir, "merchant_community_assignments.txt")
    df['storename'] = df['storename'].astype(str)
    smart_export(df, output_path)
    # 返回商户-社区映射, 用于后续交易量统计
    return {merchant: comm_id for comm_id, merchants in communities.items() for merchant in merchants}


def save_community_stats(G, communities, comm_transactions, output_dir):
    stats = []
    for comm_id, merchants in communities.items():
        subgraph = G.subgraph(merchants)
        # 从社区交易量字典中获取当前社区的交易笔数
        total_transactions = comm_transactions.get(comm_id, 0)
        stats.append({
            'community_id': comm_id,
            'num_merchants': len(merchants),
            'num_edges': subgraph.number_of_edges(),
            'total_weight': sum(
                d['weight'] for _, _, d in subgraph.edges(data=True)
            ) if subgraph.number_of_edges() > 0 else 0,
            'density': nx.density(subgraph),
            'total_transactions': total_transactions  # 新增: 社区内总交易笔数
        })

    df = pd.DataFrame(stats)
    output_path = os.path.join(output_dir, "community_statistics.txt")
    smart_export(df, output_path)


def save_full_results(G, communities, output_dir):
    results = {
        'graph': G,
        'communities': communities,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    output_path = os.path.join(output_dir, "full_community_results.pkl")
    with open(output_path, 'wb') as f:
        pickle.dump(results, f)


def detect_delimiter(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        sample = f.read(4096)

    if not sample:
        return ','

    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[',', '|', '\t', ';'])
        return dialect.delimiter
    except csv.Error:
        header_line = sample.splitlines()[0] if sample.splitlines() else ''
        for delimiter in [',', '|', '\t', ';']:
            if delimiter in header_line:
                return delimiter
    return ','


def load_geo_data(geo_file):
    if not os.path.exists(geo_file):
        print(f"未找到经纬度文件: {geo_file}")
        return pd.DataFrame(columns=['storename', 'pos_longitude', 'pos_latitude'])

    delimiter = detect_delimiter(geo_file)
    geo_df = pd.read_csv(
        geo_file,
        sep=delimiter,
        dtype={'storename': str},
        encoding='utf-8',
        engine='python'
    )

    required_columns = ['storename', 'pos_longitude', 'pos_latitude']
    missing_columns = [col for col in required_columns if col not in geo_df.columns]
    if missing_columns:
        print(f"经纬度文件缺少字段: {missing_columns}")
        return pd.DataFrame(columns=required_columns)

    geo_df = geo_df[required_columns].copy()
    geo_df['storename'] = geo_df['storename'].astype(str)
    geo_df['pos_longitude'] = pd.to_numeric(geo_df['pos_longitude'], errors='coerce')
    geo_df['pos_latitude'] = pd.to_numeric(geo_df['pos_latitude'], errors='coerce')
    geo_df = geo_df.dropna(subset=['storename'])
    geo_df = geo_df.drop_duplicates(subset=['storename'], keep='first')
    return geo_df


def build_haversine_distance_matrix(coords):
    earth_radius_m = 6371000.0
    latitudes = np.radians(coords[:, 0])
    longitudes = np.radians(coords[:, 1])

    latitude1 = latitudes[:, None]
    latitude2 = latitudes[None, :]
    longitude1 = longitudes[:, None]
    longitude2 = longitudes[None, :]

    latitude_delta = latitude2 - latitude1
    longitude_delta = longitude2 - longitude1
    haversine_value = (
        np.sin(latitude_delta / 2.0) ** 2
        + np.cos(latitude1)
        * np.cos(latitude2)
        * np.sin(longitude_delta / 2.0) ** 2
    )
    central_angle = 2.0 * np.arcsin(
        np.minimum(1.0, np.sqrt(haversine_value))
    )
    return earth_radius_m * central_angle


def hierarchical_geo_cluster(coords, distance_threshold_m=1500):
    if len(coords) == 0:
        return np.array([], dtype=int)
    if len(coords) == 1:
        return np.array([0], dtype=int)

    distance_matrix = build_haversine_distance_matrix(coords)
    np.fill_diagonal(distance_matrix, 0.0)

    try:
        model = AgglomerativeClustering(
            n_clusters=None,
            metric='precomputed',
            linkage='average',
            distance_threshold=distance_threshold_m
        )
    except TypeError:
        model = AgglomerativeClustering(
            n_clusters=None,
            affinity='precomputed',
            linkage='average',
            distance_threshold=distance_threshold_m
        )

    return model.fit_predict(distance_matrix)


def distance_to_geo_center_m(cluster_rows, center_longitude, center_latitude):
    earth_radius_m = 6371000.0
    latitudes = np.radians([row['pos_latitude'] for row in cluster_rows])
    longitudes = np.radians([row['pos_longitude'] for row in cluster_rows])
    center_latitude_rad = np.radians(center_latitude)
    center_longitude_rad = np.radians(center_longitude)

    dlat = latitudes - center_latitude_rad
    dlon = longitudes - center_longitude_rad
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(center_latitude_rad) * np.cos(latitudes) * np.sin(dlon / 2.0) ** 2
    )
    return earth_radius_m * 2.0 * np.arcsin(np.minimum(1.0, np.sqrt(a)))


def geo_center_distance_m(center1, center2):
    row = {
        'pos_longitude': center1[0],
        'pos_latitude': center1[1]
    }
    return float(distance_to_geo_center_m([row], center2[0], center2[1])[0])


def is_blank_value(value):
    return value is None or pd.isna(value) or str(value).strip() == ''


def normalize_geo_cluster_id(value):
    if is_blank_value(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def to_optional_float(value):
    if is_blank_value(value):
        return ''
    try:
        return float(value)
    except (TypeError, ValueError):
        return ''


def to_float_or_zero(value):
    value = to_optional_float(value)
    return value if value != '' else 0.0


def to_int_or_zero(value):
    return int(to_float_or_zero(value))


def has_valid_center(center):
    if center is None or len(center) != 2:
        return False
    return not is_blank_value(center[0]) and not is_blank_value(center[1])


def identify_suspect_online_merchants(
    G,
    communities,
    geo_file,
    geo_assignment_rows,
    max_distance_m=2000
):
    geo_df = load_geo_data(geo_file)
    merchants_with_valid_geo = (
        set(
            geo_df.loc[
                geo_df['pos_longitude'].notna()
                & geo_df['pos_latitude'].notna(),
                'storename'
            ]
        )
        if not geo_df.empty
        else set()
    )
    merchant_to_geo_cluster = {}
    geo_cluster_centers = {}
    for row in geo_assignment_rows:
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        center = (
            row.get('geo_center_longitude'),
            row.get('geo_center_latitude')
        )
        if geo_cluster_id is None or geo_cluster_id <= 0 or not has_valid_center(center):
            continue
        merchant_to_geo_cluster[row['storename']] = geo_cluster_id
        geo_cluster_centers[geo_cluster_id] = center

    suspect_online = set()
    for merchants in communities.values():
        for merchant in merchants:
            if merchant in merchants_with_valid_geo or merchant not in G:
                continue

            linked_geo_clusters = {
                merchant_to_geo_cluster[neighbor]
                for neighbor in G.neighbors(merchant)
                if neighbor in merchant_to_geo_cluster
            }
            if len(linked_geo_clusters) < 2:
                continue

            linked_geo_clusters = sorted(linked_geo_clusters)
            is_suspect = False
            for index, cluster1 in enumerate(linked_geo_clusters[:-1]):
                center1 = geo_cluster_centers.get(cluster1)
                if center1 is None:
                    continue
                for cluster2 in linked_geo_clusters[index + 1:]:
                    center2 = geo_cluster_centers.get(cluster2)
                    if center2 is None:
                        continue
                    if geo_center_distance_m(center1, center2) > max_distance_m:
                        suspect_online.add(merchant)
                        is_suspect = True
                        break
                if is_suspect:
                    break

    return suspect_online


def trim_geo_cluster_outliers(cluster_rows, max_center_distance_m=2500):
    retained_rows = list(cluster_rows)
    removed_count = 0

    while retained_rows:
        center_longitude = float(np.mean([row['pos_longitude'] for row in retained_rows]))
        center_latitude = float(np.mean([row['pos_latitude'] for row in retained_rows]))
        distances = distance_to_geo_center_m(retained_rows, center_longitude, center_latitude)
        if float(np.max(distances)) <= max_center_distance_m:
            return retained_rows, removed_count

        farthest_index = int(np.argmax(distances))
        retained_rows.pop(farthest_index)
        removed_count += 1

    return [], removed_count


def geo_hierarchical_cluster_merchants(
    communities,
    geo_file,
    distance_threshold_m=1500,
    max_center_distance_m=2500
):
    geo_df = load_geo_data(geo_file)
    if geo_df.empty:
        return [], {}, 0

    geo_map = geo_df.set_index('storename')[['pos_longitude', 'pos_latitude']].to_dict('index')
    removed_outlier_count = 0
    community_geo_groups = []

    for comm_id, merchants in communities.items():
        community_rows = []
        for merchant in merchants:
            geo_info = geo_map.get(str(merchant))
            if geo_info is None:
                continue

            longitude = geo_info['pos_longitude']
            latitude = geo_info['pos_latitude']
            if pd.isna(longitude) or pd.isna(latitude):
                continue

            community_rows.append({
                'storename': str(merchant),
                'community_id': comm_id,
                'pos_longitude': float(longitude),
                'pos_latitude': float(latitude)
            })

        if not community_rows:
            continue

        retained_rows, community_removed_count = trim_geo_cluster_outliers(
            community_rows,
            max_center_distance_m=max_center_distance_m
        )
        removed_outlier_count += community_removed_count
        if not retained_rows:
            continue

        community_geo_groups.append({
            'community_id': comm_id,
            'center_longitude': float(np.mean([
                row['pos_longitude']
                for row in retained_rows
            ])),
            'center_latitude': float(np.mean([
                row['pos_latitude']
                for row in retained_rows
            ])),
            'rows': retained_rows
        })

    if not community_geo_groups:
        return [], {}, removed_outlier_count

    community_centers = np.asarray(
        [
            [group['center_latitude'], group['center_longitude']]
            for group in community_geo_groups
        ],
        dtype=float
    )
    center_labels = hierarchical_geo_cluster(
        community_centers,
        distance_threshold_m=distance_threshold_m
    )

    merged_geo_groups = {}
    for group, label in zip(community_geo_groups, center_labels):
        merged_geo_groups.setdefault(int(label), []).extend(group['rows'])

    geo_clusters = {}
    temporary_cluster_id = 1
    for merged_rows in merged_geo_groups.values():
        retained_rows, merged_removed_count = trim_geo_cluster_outliers(
            merged_rows,
            max_center_distance_m=max_center_distance_m
        )
        removed_outlier_count += merged_removed_count
        if retained_rows:
            geo_clusters[temporary_cluster_id] = retained_rows
            temporary_cluster_id += 1

    print(
        f"有坐标的交易社区数: {len(community_geo_groups)}, "
        f"空间层次聚类距离阈值: {distance_threshold_m}米, "
        f"地理整合后商圈数: {len(geo_clusters)}"
    )

    assignment_rows = []
    for geo_cluster_id, cluster_rows in geo_clusters.items():
        for row in cluster_rows:
            assignment_rows.append({
                'storename': row['storename'],
                'community_id': row['community_id'],
                'geo_cluster_id': geo_cluster_id,
                'pos_longitude': row['pos_longitude'],
                'pos_latitude': row['pos_latitude']
            })

    return assignment_rows, geo_clusters, removed_outlier_count


def _community_sort_key(comm_id):
    try:
        return (0, int(comm_id))
    except (TypeError, ValueError):
        return (1, str(comm_id))


def save_geo_recluster_results(G, assignment_rows, stats_rows, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    assignment_df = pd.DataFrame(
        assignment_rows,
        columns=[
            'storename',
            'region',
            'community_id',
            'geo_cluster_id',
            'pos_longitude',
            'pos_latitude',
            'geo_center_longitude',
            'geo_center_latitude',
            'is_position',
            'status'
        ]
    )
    smart_export(assignment_df, os.path.join(output_dir, 'merchant_geo_cluster_assignments.txt'))

    stats_df = pd.DataFrame(
        stats_rows,
        columns=['geo_cluster_id', 'num_merchants', 'num_edges', 'total_weight', 'density', 'community_count', 'top_community_id']
    )
    smart_export(stats_df, os.path.join(output_dir, 'geo_cluster_statistics.txt'))


def build_output_abnormal_statuses(
    G,
    communities,
    graph_builder,
    geo_assignment_rows,
    suspect_online_merchants
):
    active_merchants = {
        merchant
        for merchants in communities.values()
        for merchant in merchants
    }
    inactive_merchants = get_inactive_merchants(graph_builder, active_merchants)
    isolated_merchants = get_suspect_isolated_merchants(G, communities)
    output_statuses = {}

    for merchant in active_merchants:
        input_status = graph_builder.merchant_abnormal_statuses.get(
            merchant,
            NORMAL_STATUS
        )
        if input_status in VALID_ABNORMAL_STATUSES and input_status != NORMAL_STATUS:
            output_statuses[merchant] = input_status
        elif merchant in inactive_merchants:
            output_statuses[merchant] = INACTIVE_STATUS
        elif merchant in suspect_online_merchants:
            output_statuses[merchant] = ONLINE_STATUS
        elif merchant in isolated_merchants:
            output_statuses[merchant] = ISOLATED_STATUS
        else:
            output_statuses[merchant] = NORMAL_STATUS

    for row in geo_assignment_rows:
        merchant = row['storename']
        output_statuses.setdefault(
            merchant,
            graph_builder.merchant_abnormal_statuses.get(merchant, NORMAL_STATUS)
        )
    return output_statuses


def save_final_assignment_results(
    geo_assignment_rows,
    previous_geo_partition,
    graph_builder,
    output_dir,
    abnormal_statuses,
    update_time
):
    position_flags = build_geo_position_flags(geo_assignment_rows, graph_builder.G)
    rows = []
    seen_merchants = set()
    for row in geo_assignment_rows:
        merchant = row['storename']
        if merchant in seen_merchants:
            continue
        seen_merchants.add(merchant)
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        rows.append({
            'storename': merchant,
            'community_id': geo_cluster_id if geo_cluster_id is not None else '',
            'previous_community_id': previous_geo_partition.get(merchant, ''),
            'region': graph_builder.merchant_regions.get(
                merchant,
                row.get('region', '')
            ),
            'is_interfere': 1 if merchant in graph_builder.interfered_merchants else 0,
            'update_time': update_time,
            'is_abnormal': abnormal_statuses.get(merchant, NORMAL_STATUS),
            'is_position': position_flags.get(merchant, 0)
        })

    output_df = pd.DataFrame(rows, columns=FINAL_ASSIGNMENT_COLUMNS)
    smart_export(
        output_df,
        os.path.join(output_dir, 'merchant_community_assignments.txt')
    )


def build_geo_position_flags(geo_assignment_rows, G, top_n=5):
    flags = {row['storename']: 0 for row in geo_assignment_rows}
    grouped_merchants = {}
    row_by_merchant = {}

    for row in geo_assignment_rows:
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        if geo_cluster_id is None or geo_cluster_id <= 0:
            continue
        merchant = row['storename']
        grouped_merchants.setdefault(geo_cluster_id, set()).add(merchant)
        row_by_merchant[merchant] = row

    for merchants in grouped_merchants.values():
        ranked_candidates = []
        for merchant in merchants:
            row = row_by_merchant[merchant]
            has_geo = (
                not is_blank_value(row.get('pos_longitude'))
                and not is_blank_value(row.get('pos_latitude'))
            )
            if has_geo:
                continue
            internal_weight = 0
            if merchant in G:
                internal_weight = sum(
                    edge_data.get('weight', 1)
                    for neighbor, edge_data in G[merchant].items()
                    if neighbor in merchants
                )
            ranked_candidates.append((merchant, internal_weight))

        ranked_candidates.sort(key=lambda item: (-item[1], str(item[0])))
        for merchant, _ in ranked_candidates[:top_n]:
            flags[merchant] = 1

    return flags


def load_geo_assignment_rows(output_dir):
    path = os.path.join(output_dir, 'merchant_geo_cluster_assignments.txt')
    if not os.path.exists(path):
        return []

    df = pd.read_csv(
        path,
        sep=detect_delimiter(path),
        dtype=str,
        engine='python'
    )
    rows = []
    columns = [
        'storename',
        'region',
        'community_id',
        'geo_cluster_id',
        'pos_longitude',
        'pos_latitude',
        'geo_center_longitude',
        'geo_center_latitude',
        'is_position',
        'status'
    ]
    for column in columns:
        if column not in df.columns:
            df[column] = ''

    for _, row in df.iterrows():
        geo_cluster_id = normalize_geo_cluster_id(row['geo_cluster_id'])
        rows.append({
            'storename': str(row['storename']),
            'region': row['region'] if not is_blank_value(row['region']) else '上海',
            'community_id': row['community_id'] if not is_blank_value(row['community_id']) else '',
            'geo_cluster_id': geo_cluster_id if geo_cluster_id is not None else '',
            'pos_longitude': to_optional_float(row['pos_longitude']),
            'pos_latitude': to_optional_float(row['pos_latitude']),
            'geo_center_longitude': to_optional_float(row['geo_center_longitude']),
            'geo_center_latitude': to_optional_float(row['geo_center_latitude']),
            'is_position': int(float(row['is_position'])) if not is_blank_value(row['is_position']) else 0,
            'status': row['status'] if not is_blank_value(row['status']) else ''
        })
    return rows


def load_geo_stats_rows(output_dir):
    path = os.path.join(output_dir, 'geo_cluster_statistics.txt')
    if not os.path.exists(path):
        return []

    df = pd.read_csv(
        path,
        sep=detect_delimiter(path),
        dtype=str,
        engine='python'
    )
    rows = []
    for _, row in df.iterrows():
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        if geo_cluster_id is None:
            continue
        rows.append({
            'geo_cluster_id': geo_cluster_id,
            'num_merchants': to_int_or_zero(row.get('num_merchants')),
            'num_edges': to_int_or_zero(row.get('num_edges')),
            'total_weight': to_float_or_zero(row.get('total_weight')),
            'density': to_float_or_zero(row.get('density')),
            'community_count': to_int_or_zero(row.get('community_count')),
            'top_community_id': row.get('top_community_id', '')
        })
    return rows


def build_geo_state_from_rows(assignment_rows, stats_rows=None):
    merchant_to_geo_cluster = {}
    geo_cluster_centers = {}
    geo_cluster_total_weights = {}

    for row in assignment_rows:
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        if geo_cluster_id is None or geo_cluster_id <= 0:
            continue
        merchant_to_geo_cluster[row['storename']] = geo_cluster_id
        center = (
            row.get('geo_center_longitude'),
            row.get('geo_center_latitude')
        )
        if has_valid_center(center):
            geo_cluster_centers[geo_cluster_id] = (
                float(center[0]),
                float(center[1])
            )

    for row in stats_rows or []:
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        if geo_cluster_id is None or geo_cluster_id <= 0:
            continue
        geo_cluster_total_weights[geo_cluster_id] = float(
            row.get('total_weight', 0) or 0
        )

    return merchant_to_geo_cluster, geo_cluster_centers, geo_cluster_total_weights


def load_frozen_geo_history(output_dir, graph_builder):
    assignment_rows = load_geo_assignment_rows(output_dir)
    stats_rows = load_geo_stats_rows(output_dir)
    merchant_to_geo_cluster, geo_cluster_centers, geo_cluster_total_weights = (
        build_geo_state_from_rows(assignment_rows, stats_rows)
    )

    if not merchant_to_geo_cluster:
        merchant_to_geo_cluster = dict(graph_builder.merchant_to_geo_cluster)
    if not geo_cluster_centers:
        geo_cluster_centers = dict(graph_builder.geo_cluster_centers)
    if not geo_cluster_total_weights:
        geo_cluster_total_weights = dict(graph_builder.geo_cluster_total_weights)

    return (
        assignment_rows,
        merchant_to_geo_cluster,
        geo_cluster_centers,
        geo_cluster_total_weights
    )


def update_graph_builder_geo_state(graph_builder, assignment_rows, stats_rows):
    merchant_to_geo_cluster, geo_cluster_centers, geo_cluster_total_weights = (
        build_geo_state_from_rows(assignment_rows, stats_rows)
    )
    graph_builder.merchant_to_geo_cluster = merchant_to_geo_cluster
    graph_builder.geo_cluster_centers = geo_cluster_centers
    graph_builder.geo_cluster_total_weights = geo_cluster_total_weights


def build_geo_stats_from_assignment_rows(G, assignment_rows):
    grouped_rows = {}
    for row in assignment_rows:
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        if geo_cluster_id is None or geo_cluster_id <= 0:
            continue
        grouped_rows.setdefault(geo_cluster_id, []).append(row)

    stats_rows = []
    for geo_cluster_id in sorted(grouped_rows):
        cluster_rows = grouped_rows[geo_cluster_id]
        merchants = [row['storename'] for row in cluster_rows]
        subgraph = G.subgraph(merchants)
        community_counter = Counter(
            row.get('community_id')
            for row in cluster_rows
            if not is_blank_value(row.get('community_id'))
        )
        top_community_id = ''
        if community_counter:
            top_community_id = sorted(
                community_counter.items(),
                key=lambda item: (-item[1], _community_sort_key(item[0]))
            )[0][0]

        stats_rows.append({
            'geo_cluster_id': geo_cluster_id,
            'num_merchants': len(merchants),
            'num_edges': subgraph.number_of_edges(),
            'total_weight': sum(
                d.get('weight', 1)
                for _, _, d in subgraph.edges(data=True)
            ) if subgraph.number_of_edges() > 0 else 0,
            'density': nx.density(subgraph) if len(merchants) > 1 else 0.0,
            'community_count': len(community_counter),
            'top_community_id': top_community_id,
            'geo_center_longitude': cluster_rows[0].get('geo_center_longitude', ''),
            'geo_center_latitude': cluster_rows[0].get('geo_center_latitude', '')
        })
    return stats_rows


def all_keyword_terms():
    terms = (
        set(SHANGHAI_LOCAL_KEYWORDS)
        | set(SHANGHAI_ROAD_KEYWORDS)
        | set(CUISINE_BRAND_WHITELIST)
        | set(NON_SHANGHAI_PLACE_KEYWORDS)
    )
    for keyword_group in MANUAL_AREA_KEYWORD_GROUPS:
        terms.update(keyword_group)
    return sorted(terms, key=len, reverse=True)


def extract_keywords_from_names(names):
    text = ''.join(str(name) for name in names)
    keywords = {
        term
        for term in all_keyword_terms()
        if term and term in text and term not in GENERIC_KEYWORDS
    }
    return keywords


def is_protected_shanghai_or_brand_term(text, place_keyword):
    for keyword in SHANGHAI_LOCAL_KEYWORDS | SHANGHAI_ROAD_KEYWORDS:
        if place_keyword in keyword and keyword in text:
            return True

    for keyword in CUISINE_BRAND_WHITELIST:
        if place_keyword in keyword and keyword in text:
            return True

    return False


def detect_out_of_region_by_keywords(names):
    text = ''.join(str(name) for name in names)
    for place_keyword in sorted(NON_SHANGHAI_PLACE_KEYWORDS, key=len, reverse=True):
        if place_keyword not in text:
            continue
        if is_protected_shanghai_or_brand_term(text, place_keyword):
            continue
        return True
    return False


def empty_geo_keyword_profile():
    return {
        'strong_poi_keywords': set(),
        'core_merchant_keywords': set(),
        'road_or_station_keywords': set(),
        'normal_keywords': set(),
        'brand_keywords': set()
    }


def add_keyword_to_profile(profile, keyword, is_core=False):
    if keyword in GENERIC_KEYWORDS:
        return
    if keyword in CUISINE_BRAND_WHITELIST:
        profile['brand_keywords'].add(keyword)
    elif keyword in SHANGHAI_ROAD_KEYWORDS:
        profile['road_or_station_keywords'].add(keyword)
    elif keyword in SHANGHAI_LOCAL_KEYWORDS:
        profile['strong_poi_keywords'].add(keyword)
    elif is_core:
        profile['core_merchant_keywords'].add(keyword)
    else:
        profile['normal_keywords'].add(keyword)


def expand_manual_area_keywords(profile):
    profile_keywords = set()
    for keyword_set in profile.values():
        profile_keywords.update(keyword_set)

    for keyword_group in MANUAL_AREA_KEYWORD_GROUPS:
        if profile_keywords & keyword_group:
            profile['strong_poi_keywords'].update(keyword_group)


def build_geo_keyword_profiles(history_assignment_rows):
    profiles = {}
    for row in history_assignment_rows:
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        if geo_cluster_id is None or geo_cluster_id <= 0:
            continue

        profile = profiles.setdefault(
            geo_cluster_id,
            empty_geo_keyword_profile()
        )
        is_core = to_int_or_zero(row.get('is_position')) == 1
        for keyword in extract_keywords_from_names([row.get('storename', '')]):
            add_keyword_to_profile(profile, keyword, is_core=is_core)

    for profile in profiles.values():
        expand_manual_area_keywords(profile)
    return profiles


def build_manual_keyword_geo_cluster_map(history_assignment_rows):
    keyword_to_geo_cluster = {}

    for keyword_group in MANUAL_AREA_KEYWORD_GROUPS:
        geo_scores = Counter()
        for row in history_assignment_rows:
            geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
            if geo_cluster_id is None or geo_cluster_id <= 0:
                continue

            storename = str(row.get('storename', ''))
            matched_keywords = {
                keyword
                for keyword in keyword_group
                if keyword and keyword in storename
            }
            if not matched_keywords:
                continue

            core_bonus = 5 if to_int_or_zero(row.get('is_position')) == 1 else 0
            geo_scores[geo_cluster_id] += len(matched_keywords) + core_bonus

        if not geo_scores:
            continue

        target_geo_cluster_id = sorted(
            geo_scores,
            key=lambda geo_cluster_id: (
                -geo_scores[geo_cluster_id],
                geo_cluster_id
            )
        )[0]
        for keyword in keyword_group:
            keyword_to_geo_cluster[keyword] = target_geo_cluster_id

    return keyword_to_geo_cluster


def choose_geo_cluster_by_manual_keywords(
    component_merchants,
    keyword_to_geo_cluster
):
    text = ''.join(str(merchant) for merchant in component_merchants)
    matched_geo_clusters = Counter()

    for keyword, geo_cluster_id in keyword_to_geo_cluster.items():
        if keyword and keyword in text:
            matched_geo_clusters[geo_cluster_id] += len(keyword)

    if not matched_geo_clusters:
        return 0, 'no_manual_keyword_match'

    return sorted(
        matched_geo_clusters,
        key=lambda geo_cluster_id: (
            -matched_geo_clusters[geo_cluster_id],
            geo_cluster_id
        )
    )[0], 'manual_keyword_assigned'


def score_keywords_against_profile(component_keywords, profile):
    score = 0
    for keyword in component_keywords:
        if keyword in profile['strong_poi_keywords']:
            score += 10
        elif keyword in profile['core_merchant_keywords']:
            score += 6
        elif keyword in profile['road_or_station_keywords']:
            score += 5
        elif keyword in profile['normal_keywords']:
            score += 2
        elif keyword in profile['brand_keywords']:
            score += 1
    return score


def choose_geo_cluster_by_keywords(component_merchants, geo_keyword_profiles):
    if detect_out_of_region_by_keywords(component_merchants):
        return 0, 'out_of_region_candidate'

    component_keywords = extract_keywords_from_names(component_merchants)
    component_keywords = {
        keyword
        for keyword in component_keywords
        if keyword not in GENERIC_KEYWORDS
    }
    if not component_keywords or not geo_keyword_profiles:
        return 0, 'no_keyword_match'

    scores = {
        geo_cluster_id: score_keywords_against_profile(
            component_keywords,
            profile
        )
        for geo_cluster_id, profile in geo_keyword_profiles.items()
    }
    ranked_scores = sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0])
    )
    best_geo_cluster_id, best_score = ranked_scores[0]
    second_score = ranked_scores[1][1] if len(ranked_scores) > 1 else 0

    if best_score < MIN_KEYWORD_MATCH_SCORE:
        return 0, 'no_keyword_match'
    if (
        second_score > 0
        and (best_score - second_score) / best_score < KEYWORD_AMBIGUITY_RATIO
    ):
        return 0, 'manual_review'
    return best_geo_cluster_id, 'keyword_assigned'


def choose_geo_cluster_for_component(
    G,
    component_merchants,
    merchant_to_geo_cluster,
    geo_cluster_total_weights
):
    weight_by_geo_cluster = Counter()
    edge_count_by_geo_cluster = Counter()
    linked_merchants_by_geo_cluster = {}

    for merchant in component_merchants:
        if merchant not in G:
            continue
        for neighbor, edge_data in G[merchant].items():
            geo_cluster_id = merchant_to_geo_cluster.get(neighbor)
            if geo_cluster_id is None:
                continue
            weight = edge_data.get('weight', 1)
            weight_by_geo_cluster[geo_cluster_id] += weight
            edge_count_by_geo_cluster[geo_cluster_id] += 1
            linked_merchants_by_geo_cluster.setdefault(
                geo_cluster_id,
                set()
            ).add(neighbor)

    if not weight_by_geo_cluster:
        return 0

    return sorted(
        weight_by_geo_cluster,
        key=lambda geo_cluster_id: (
            -weight_by_geo_cluster[geo_cluster_id],
            -len(linked_merchants_by_geo_cluster.get(geo_cluster_id, set())),
            -edge_count_by_geo_cluster[geo_cluster_id],
            -geo_cluster_total_weights.get(geo_cluster_id, 0),
            geo_cluster_id
        )
    )[0]


def build_incremental_geo_assignment_rows(
    G,
    communities,
    geo_file,
    history_assignment_rows,
    merchant_to_geo_cluster,
    geo_cluster_centers,
    geo_cluster_total_weights
):
    merchant_to_comm = {
        merchant: comm_id
        for comm_id, merchants in communities.items()
        for merchant in merchants
    }
    position_flags = build_position_flags(G, communities, top_n=5)
    geo_df = load_geo_data(geo_file)
    geo_map = (
        geo_df.set_index('storename')[['pos_longitude', 'pos_latitude']].to_dict('index')
        if not geo_df.empty
        else {}
    )

    assignment_rows = []
    retained_merchants = set()
    for row in history_assignment_rows:
        merchant = row['storename']
        geo_cluster_id = normalize_geo_cluster_id(row.get('geo_cluster_id'))
        if geo_cluster_id is None or geo_cluster_id <= 0:
            continue
        if merchant not in G and merchant not in merchant_to_comm:
            continue

        center = geo_cluster_centers.get(geo_cluster_id, (
            row.get('geo_center_longitude', ''),
            row.get('geo_center_latitude', '')
        ))
        assignment_rows.append({
            'storename': merchant,
            'region': '上海',
            'community_id': merchant_to_comm.get(merchant, row.get('community_id', '')),
            'geo_cluster_id': geo_cluster_id,
            'pos_longitude': row.get('pos_longitude', ''),
            'pos_latitude': row.get('pos_latitude', ''),
            'geo_center_longitude': center[0] if has_valid_center(center) else '',
            'geo_center_latitude': center[1] if has_valid_center(center) else '',
            'is_position': position_flags.get(merchant, 0),
            'status': ''
        })
        retained_merchants.add(merchant)

    keyword_to_geo_cluster = build_manual_keyword_geo_cluster_map(
        history_assignment_rows
    )
    candidate_merchants = set(G.nodes()) - set(merchant_to_geo_cluster)
    if candidate_merchants:
        candidate_graph = G.subgraph(candidate_merchants)
        components = nx.connected_components(candidate_graph)
    else:
        components = []

    for component in components:
        component_merchants = set(component)
        geo_cluster_id, status = choose_geo_cluster_by_manual_keywords(
            component_merchants,
            keyword_to_geo_cluster
        )

        if status == 'no_manual_keyword_match':
            geo_cluster_id = choose_geo_cluster_for_component(
                G,
                component_merchants,
                merchant_to_geo_cluster,
                geo_cluster_total_weights
            )
            if geo_cluster_id > 0:
                status = 'edge_assigned'
            elif detect_out_of_region_by_keywords(component_merchants):
                status = 'out_of_region_candidate'
            else:
                status = 'pending_geo_cluster'

        center = geo_cluster_centers.get(geo_cluster_id)

        for merchant in sorted(component_merchants, key=str):
            geo_info = geo_map.get(str(merchant), {})
            longitude = to_optional_float(geo_info.get('pos_longitude'))
            latitude = to_optional_float(geo_info.get('pos_latitude'))
            assignment_rows.append({
                'storename': str(merchant),
                'region': '上海',
                'community_id': merchant_to_comm.get(merchant, ''),
                'geo_cluster_id': geo_cluster_id,
                'pos_longitude': longitude,
                'pos_latitude': latitude,
                'geo_center_longitude': center[0] if has_valid_center(center) else '',
                'geo_center_latitude': center[1] if has_valid_center(center) else '',
                'is_position': position_flags.get(merchant, 0),
                'status': status
            })

    assignment_rows.sort(
        key=lambda row: (
            normalize_geo_cluster_id(row.get('geo_cluster_id')) or 0,
            row['storename']
        )
    )
    return assignment_rows, build_geo_stats_from_assignment_rows(G, assignment_rows)



def run_geo_recluster_pipeline(
    G,
    communities,
    geo_file,
    distance_threshold_m=1500,
    max_center_distance_m=2500
):
    print(
        f"\n开始根据交易社区中心进行空间层次聚类并整合社区, "
        f"聚类距离阈值: {distance_threshold_m}米, "
        f"中心距离上限: {max_center_distance_m}米"
    )
    assignment_rows, geo_clusters, removed_outlier_count = geo_hierarchical_cluster_merchants(
        communities,
        geo_file,
        distance_threshold_m=distance_threshold_m,
        max_center_distance_m=max_center_distance_m
    )

    if not assignment_rows:
        print("地理二次聚类未生成结果, 跳过导出")
        return [], []

    position_flags = build_position_flags(G, communities, top_n=5)
    stats_rows = []
    for geo_cluster_id in sorted(geo_clusters):
        cluster_rows = geo_clusters[geo_cluster_id]
        merchants = [row['storename'] for row in cluster_rows]
        subgraph = G.subgraph(merchants)
        community_counter = Counter(row['community_id'] for row in cluster_rows)
        top_community_id = sorted(
            community_counter.items(),
            key=lambda item: (-item[1], _community_sort_key(item[0]))
        )[0][0]
        center_longitude = float(np.mean([row['pos_longitude'] for row in cluster_rows]))
        center_latitude = float(np.mean([row['pos_latitude'] for row in cluster_rows]))

        stats_rows.append({
            'geo_cluster_id': geo_cluster_id,
            'num_merchants': len(merchants),
            'num_edges': subgraph.number_of_edges(),
            'total_weight': sum(d.get('weight', 1) for _, _, d in subgraph.edges(data=True)) if subgraph.number_of_edges() > 0 else 0,
            'density': nx.density(subgraph) if len(merchants) > 1 else 0.0,
            'community_count': len(community_counter),
            'top_community_id': top_community_id,
            'geo_center_longitude': center_longitude,
            'geo_center_latitude': center_latitude
        })

    original_geo_cluster_count = len(stats_rows)
    stats_rows = [
        row
        for row in stats_rows
        if row['num_merchants'] >= MIN_GEO_CLUSTER_MERCHANTS
    ]
    stats_rows.sort(key=lambda row: (-row['total_weight'], -row['num_edges'], -row['num_merchants'], row['geo_cluster_id']))
    geo_cluster_id_mapping = {}
    geo_cluster_center_mapping = {}
    for new_geo_cluster_id, row in enumerate(stats_rows, start=1):
        old_geo_cluster_id = row['geo_cluster_id']
        geo_cluster_id_mapping[old_geo_cluster_id] = new_geo_cluster_id
        geo_cluster_center_mapping[new_geo_cluster_id] = (
            row['geo_center_longitude'],
            row['geo_center_latitude']
        )
        row['geo_cluster_id'] = new_geo_cluster_id

    retained_assignment_rows = []
    for assignment_row in assignment_rows:
        old_geo_cluster_id = assignment_row['geo_cluster_id']
        if old_geo_cluster_id not in geo_cluster_id_mapping:
            continue

        assignment_row['geo_cluster_id'] = geo_cluster_id_mapping[old_geo_cluster_id]
        center_longitude, center_latitude = geo_cluster_center_mapping[assignment_row['geo_cluster_id']]
        assignment_row['region'] = '上海'
        assignment_row['geo_center_longitude'] = center_longitude
        assignment_row['geo_center_latitude'] = center_latitude
        assignment_row['is_position'] = position_flags.get(assignment_row['storename'], 0)
        assignment_row['status'] = ''
        retained_assignment_rows.append(assignment_row)

    assignment_rows = retained_assignment_rows
    assignment_rows.sort(key=lambda row: (row['geo_cluster_id'], row['storename']))
    print(
        f"空间层次聚类原始生成 {original_geo_cluster_count} 个地理社区，"
        f"剔除商户数小于 {MIN_GEO_CLUSTER_MERCHANTS} 的小社区后 "
        f"保留 {len(stats_rows)} 个地理社区，"
        f"距离中心超过 {max_center_distance_m} 米剔除 "
        f"{removed_outlier_count} 个商户"
    )
    return assignment_rows, stats_rows


def generate_and_commit_results(
    graph_builder,
    G,
    communities,
    geo_file,
    output_dir
):
    staging_dir = create_staging_output_dir(output_dir)
    try:
        merchant_to_comm = {
            merchant: comm_id
            for comm_id, merchants in communities.items()
            for merchant in merchants
        }
        comm_transactions = aggregate_community_transactions(
            graph_builder.merchant_transaction_counts,
            merchant_to_comm
        )
        save_community_stats(G, communities, comm_transactions, staging_dir)
        save_full_results(G, communities, staging_dir)

        geo_assignment_rows, geo_stats_rows = run_geo_recluster_pipeline(
            G,
            communities,
            geo_file,
            distance_threshold_m=1500,
            max_center_distance_m=2500
        )
        if not geo_assignment_rows:
            raise RuntimeError("地理聚类结果为空，停止替换历史文件")

        suspect_online_merchants = identify_suspect_online_merchants(
            G,
            communities,
            geo_file,
            geo_assignment_rows,
            max_distance_m=2000
        )
        abnormal_statuses = build_output_abnormal_statuses(
            G,
            communities,
            graph_builder,
            geo_assignment_rows,
            suspect_online_merchants
        )
        for assignment_row in geo_assignment_rows:
            if assignment_row['storename'] in suspect_online_merchants:
                assignment_row['status'] = 'suspect_online'

        save_geo_recluster_results(
            G,
            geo_assignment_rows,
            geo_stats_rows,
            staging_dir
        )
        save_final_assignment_results(
            geo_assignment_rows,
            {},
            graph_builder,
            staging_dir,
            abnormal_statuses,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        graph_builder.community_partition = {
            merchant: comm_id
            for comm_id, merchants in communities.items()
            for merchant in merchants
        }
        update_graph_builder_geo_state(
            graph_builder,
            geo_assignment_rows,
            geo_stats_rows
        )
        graph_builder.save_state(
            os.path.join(staging_dir, 'graph_builder_checkpoint.pkl')
        )
        commit_staged_results(staging_dir, output_dir)
        print(f"疑似线上商户数: {len(suspect_online_merchants)}")
    except Exception:
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)
        raise


def generate_and_commit_incremental_results(
    graph_builder,
    G,
    communities,
    geo_file,
    output_dir,
    history_output_dir
):
    staging_dir = create_staging_output_dir(output_dir)
    try:
        merchant_to_comm = {
            merchant: comm_id
            for comm_id, merchants in communities.items()
            for merchant in merchants
        }
        comm_transactions = aggregate_community_transactions(
            graph_builder.merchant_transaction_counts,
            merchant_to_comm
        )
        save_community_stats(G, communities, comm_transactions, staging_dir)
        save_full_results(G, communities, staging_dir)

        (
            history_assignment_rows,
            merchant_to_geo_cluster,
            geo_cluster_centers,
            geo_cluster_total_weights
        ) = load_frozen_geo_history(history_output_dir, graph_builder)
        if not merchant_to_geo_cluster:
            raise RuntimeError("未找到历史 geo_cluster_id 映射，无法执行冻结式增量")

        geo_assignment_rows, geo_stats_rows = build_incremental_geo_assignment_rows(
            G,
            communities,
            geo_file,
            history_assignment_rows,
            merchant_to_geo_cluster,
            geo_cluster_centers,
            geo_cluster_total_weights
        )
        if not geo_assignment_rows:
            raise RuntimeError("增量地理商圈结果为空，停止替换历史文件")

        suspect_online_merchants = identify_suspect_online_merchants(
            G,
            communities,
            geo_file,
            geo_assignment_rows,
            max_distance_m=2000
        )
        abnormal_statuses = build_output_abnormal_statuses(
            G,
            communities,
            graph_builder,
            geo_assignment_rows,
            suspect_online_merchants
        )
        for assignment_row in geo_assignment_rows:
            if assignment_row['storename'] in suspect_online_merchants:
                assignment_row['status'] = 'suspect_online'

        save_geo_recluster_results(
            G,
            geo_assignment_rows,
            geo_stats_rows,
            staging_dir
        )
        save_final_assignment_results(
            geo_assignment_rows,
            merchant_to_geo_cluster,
            graph_builder,
            staging_dir,
            abnormal_statuses,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )

        graph_builder.community_partition = {
            merchant: comm_id
            for comm_id, merchants in communities.items()
            for merchant in merchants
        }
        update_graph_builder_geo_state(
            graph_builder,
            geo_assignment_rows,
            geo_stats_rows
        )
        graph_builder.save_state(
            os.path.join(staging_dir, 'graph_builder_checkpoint.pkl')
        )
        commit_staged_results(staging_dir, output_dir)
        print(
            f"冻结式增量地理商圈完成: 输出商户 {len(geo_assignment_rows)}, "
            f"疑似线上商户 {len(suspect_online_merchants)}"
        )
    except Exception:
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)
        raise


def load_history_partition(output_dir):
    checkpoint_path = os.path.join(output_dir, 'graph_builder_checkpoint.pkl')
    if os.path.exists(checkpoint_path):
        graph_builder = GraphBuilder()
        graph_builder.load_state(checkpoint_path)
        if graph_builder.community_partition:
            return (
                partition_to_communities(graph_builder.community_partition),
                dict(graph_builder.community_partition)
            )

    result_path = os.path.join(output_dir, "full_community_results.pkl")
    if not os.path.exists(result_path):
        print("未找到历史社区结果，将走全量流程")
        return None, None

    with open(result_path, "rb") as f:
        results = pickle.load(f)

    communities = results['communities']
    partition = {}
    for comm_id, merchants in communities.items():
        for merchant in merchants:
            partition[merchant] = comm_id
    return communities, partition


def incremental_main():
    file_path = '/home/uatvv001504@uos/Downloads/jhj_jy_shanghai_online_20260401_20260414_简单处理/shanghai_202604/incremental_merchants.txt'
    history_file_path = '/home/uatvv001504@uos/Downloads/jhj_jy_shanghai_online_20260401_20260414_简单处理/shanghai_202604/merchants_sorted_offline.txt'
    geo_file = '/home/uatvv001504@uos/Downloads/jhj_jy_shanghai_online_20260401_20260414_简单处理/location_shanghai.txt'
    output_dir = INCREMENTAL_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    recover_interrupted_output_update(output_dir)

    initial_checkpoint_path = os.path.join(
        OUTPUT_DIR,
        "graph_builder_checkpoint.pkl"
    )
    history_output_dir = OUTPUT_DIR
    checkpoint_path = initial_checkpoint_path

    graph_builder = GraphBuilder()
    if os.path.exists(checkpoint_path):
        print(f"检测到历史图状态，加载来源: {checkpoint_path}")
        graph_builder.load_state(checkpoint_path)
    else:
        print("未找到历史图状态, 无法做增量, 请先跑全量")
        return

    if not graph_builder.merchant_transaction_counts:
        print("旧检查点缺少累计交易次数，正在从历史全量文件恢复...")
        graph_builder.merchant_transaction_counts = count_merchant_transactions(
            history_file_path
        )

    old_partition = dict(graph_builder.community_partition)
    if not old_partition:
        _, old_partition = load_history_partition(history_output_dir)
    if not old_partition:
        print("未找到历史社区结果, 无法做增量, 请先跑全量")
        return

    ddf = read_large_data(file_path)
    if ddf is None:
        return

    total_partitions = ddf.npartitions
    print(f"增量数据共{total_partitions}个分区")

    transaction_pairs_all = []
    affected_nodes = set()
    for i in range(total_partitions):
        chunk = ddf.get_partition(i).compute()
        processed_chunk = preprocess_chunk(chunk)
        update_graph_builder_input_metadata(graph_builder, processed_chunk)
        graph_builder.merchant_transaction_counts.update(
            processed_chunk[STORE_COLUMN].value_counts().to_dict()
        )

        transaction_pairs = build_transaction_pairs(
            processed_chunk,
            time_window=30
        )

        for m1, m2, _ in transaction_pairs:
            affected_nodes.add(m1)
            affected_nodes.add(m2)
        transaction_pairs_all.extend(transaction_pairs)

    if not affected_nodes:
        print("本次增量未产生有效交易边，历史文件保持不变")
        return

    graph_builder.update_with_transaction_pairs(transaction_pairs_all, valid_merchants=None)
    G = graph_builder.finalize_graph(min_weight=1).copy()
    print(f"增量更新后图包含{G.number_of_nodes()}个节点和{G.number_of_edges()}条边")

    if G.number_of_nodes() == 0:
        print("图为空, 无法继续社区发现")
        return

    locked_interfered_nodes = {
        node
        for node in affected_nodes
        if node in graph_builder.interfered_merchants and node in old_partition
    }
    affected_nodes.difference_update(locked_interfered_nodes)
    print(f"人工干预锁定历史社区节点数: {len(locked_interfered_nodes)}")

    print("开始局部社区更新...")
    new_partition, local_nodes = local_detect_communities(
        G,
        old_partition,
        affected_nodes,
        resolution=0.5,
        hops=1
    )
    for merchant in locked_interfered_nodes:
        new_partition[merchant] = old_partition[merchant]

    communities = partition_to_communities(new_partition)
    result_graph = G.subgraph(new_partition).copy()

    print(f"更新后共有{len(communities)}个社区，局部更新节点{len(local_nodes)}个")

    metrics = compute_all_metrics(result_graph, new_partition)
    print(
        f"聚类指标: 模块度 {metrics['modularity']:.4f}, "
        f"覆盖率 {metrics['coverage']:.4f}, "
        f"平均导电率 {metrics['avg_conductance']:.4f}"
    )

    count_inter_community_edges(result_graph, new_partition)

    try:
        generate_and_commit_incremental_results(
            graph_builder,
            result_graph,
            communities,
            geo_file,
            output_dir,
            history_output_dir
        )
    except Exception as exc:
        print(f"增量结果生成失败，历史文件保持不变: {exc}")
        return

    print("增量更新完成")


# -------------------- 主流程 --------------------
def main():
    #file_path = "/data/home/uatdd21614@uos/Job/sh712and713/meituanout/ready_merchants.txt"
    #output_dir = "/data/home/uatdd21614@uos/Job/sh712and713/resultmeituan"

    #file_path = "/data/home/uatdd21614@uos/Job/mt0901/mt_go.txt"
    #output_dir = "/data/home/uatdd21614@uos/Job/mt0901/results"

    #file_path = r'/home/uatdd21614@uos/Desktop/AI大模型传入/results_new2/jy_data_0712_0713_go_new2.txt'
    #output_dir = r'/home/uatdd21614@uos/Desktop/AI大模型传入/results_new2'

    #file_path = r'/home/uatdd21614@uos/Desktop/长春美食城/数据预处理/jhj_changchun_jy_202508_new_go.txt'
    #output_dir = r'/home/uatdd21614@uos/Desktop/长春美食城/二次聚类结果'
    #file_path = r'/home/uatdd21614@uos/Desktop/长春美食城/8-9月数据/数据预处理/jhj_changchun_msc_jy_202508_20250925_go.txt'
    #output_dir = r'/home/uatdd21614@uos/Desktop/长春美食城/8-9月数据/聚类结果'

    file_path = '/home/uatvv001504@uos/Downloads/jhj_jy_shanghai_online_20260401_20260414_简单处理/shanghai_202604/merchants_sorted_offline.txt'
    geo_file = '/home/uatvv001504@uos/Downloads/jhj_jy_shanghai_online_20260401_20260414_简单处理/location_shanghai.txt'
    output_dir = OUTPUT_DIR
    checkpoint_path = os.path.join(output_dir, "graph_builder_checkpoint.pkl")
    os.makedirs(output_dir, exist_ok=True)
    recover_interrupted_output_update(output_dir)
    if os.path.exists(checkpoint_path):
        print(f"发现历史checkpoint，将在新结果完整生成后再替换:{checkpoint_path}")
    print("步骤1:统计商户交易次数")
    merchant_transactions = count_merchant_transactions(file_path)
    min_transactions = 3
    valid_merchants = set([m for m, cnt in merchant_transactions.items() if cnt >= min_transactions])
    print(f"总商户数:{len(merchant_transactions)},保留商户数(交易>={min_transactions}):{len(valid_merchants)}")
    # 加载或初始化图构建器
    graph_builder = GraphBuilder()
    graph_builder.merchant_transaction_counts = Counter(merchant_transactions)

    # 读取数据
    ddf = read_large_data(file_path)
    if ddf is None:
        return

    # 分块处理数据并构建图
    total_partitions = ddf.npartitions
    print(f"数据共 {total_partitions} 个分区")

    for i in range(total_partitions):
        chunk = ddf.get_partition(i).compute()
        processed_chunk = preprocess_chunk(chunk)
        update_graph_builder_input_metadata(graph_builder, processed_chunk)
        transaction_pairs = build_transaction_pairs(processed_chunk, time_window=30)

        graph_builder.update_with_transaction_pairs(transaction_pairs, valid_merchants)

    # 完成图构建
    G = graph_builder.finalize_graph(min_weight=1)
    print(f"图构建完成, 包含 {G.number_of_nodes()} 个节点和 {G.number_of_edges()} 条边")
    if G.number_of_nodes() == 0:
        print("新图为空，历史文件保持不变")
        return
    connected_components = list(nx.connected_components(G))
    component_sizes = [len(c) for c in connected_components]
    largest_component_ratio = (
        max(component_sizes) / G.number_of_nodes()
        if component_sizes else 0
    )
    print(
        f"图连通性: 连通分量 {len(connected_components)}, "
        f"最大分量占比 {largest_component_ratio:.4f}"
    )

    # 社区发现
    print("开始社区发现...")
    communities, partition = detect_communities(G)
    print(f"发现了 {len(communities)} 个社区")

    #partition = {node: comm_id for comm_id, nodes in communities.items() for node in nodes}
    metrics = compute_all_metrics(G, partition)
    print(
        f"聚类指标: 模块度 {metrics['modularity']:.4f}, "
        f"覆盖率 {metrics['coverage']:.4f}, "
        f"平均导电率 {metrics['avg_conductance']:.4f}"
    )
    count_inter_community_edges(G, partition)
    # 导出社区分配关系, 并获取商户-社区映射
    try:
        generate_and_commit_results(
            graph_builder,
            G,
            communities,
            geo_file,
            output_dir
        )
    except Exception as exc:
        print(f"新结果生成失败，历史文件保持不变: {exc}")
        return

    print("处理完成")


if __name__ == "__main__":
    run_mode = os.environ.get("CLUSTER_RUN_MODE", "auto").strip().lower()

    if run_mode == "auto":
        initial_checkpoint_path = os.path.join(
            OUTPUT_DIR,
            "graph_builder_checkpoint.pkl"
        )
        checkpoint_path = initial_checkpoint_path
        if os.path.isfile(checkpoint_path):
            print(f"自动模式: 检测到历史检查点，执行增量更新: {checkpoint_path}")
            incremental_main()
        else:
            print("自动模式: 未检测到历史检查点，执行全量初始化")
            main()

    elif run_mode == "full":
        main()
    elif run_mode == "incremental":
        incremental_main()
    else:
        raise ValueError(
            f"不支持的运行模式: {run_mode}，请使用 auto、full 或 incremental"
        )


# 说明:
# 1. 默认 auto：有历史检查点执行增量，否则执行全量。
# 2. CLUSTER_RUN_MODE=full 强制全量，CLUSTER_RUN_MODE=incremental 强制增量。
# 3. 全量结果写入 OUTPUT_DIR，增量结果写入 INCREMENTAL_OUTPUT_DIR。
# 4. 增量优先加载增量目录检查点，首次增量加载全量目录检查点。
# 5. 增量文件只应包含新增交易，避免历史边权重复累加。
# 6. 所有结果先写入暂存目录，完整生成后才替换目标目录文件。
