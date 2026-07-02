import os
import pickle
import time
import datetime
from collections import Counter, defaultdict
from shutil import copyfile

import tracemalloc
import networkx as nx
import numpy as np
import pandas as pd
import spdbccc_data as sd
from spdbccc_data import dtDate
from spdbccc_data import formattedExc
from spdbccc_data import loging as logrecord
from spdbccc_data import mountCheck
from spdbccc_data import task as taskfinish
from community import community_louvain
from sklearn.cluster import AgglomerativeClustering


SOURCE_TABLE = "dev_icamp.icamp_merchant_cluster_algo_input"
SOURCE_DT = [dtDate.dt_date("T-1")]
TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output"
RISK_TARGET_TABLE = "dev_icamp.icamp_merchant_cluster_algo_risk"
TARGET_TEMP_TABLE = "dev_icamp.icamp_merchant_cluster_algo_output_tmp"
RISK_TARGET_TEMP_TABLE = "dev_icamp.icamp_merchant_cluster_algo_risk_tmp"
ENABLE_GRAPH_CHECKPOINT = True
INCREMENTAL_SOURCE_IS_DELTA = True
LOCAL_PROJECT_DIR = "/appdata/project"
CACHE_DIR = "/appdata/project/fid_bg_icmp"
GRAPH_CHECKPOINT_FILENAME = "merchant_graph.pkl"
GRAPH_CHECKPOINT_TMP_FILENAME = "merchant_graph_next.pkl"
GRAPH_CHECKPOINT_PATH = os.path.join(LOCAL_PROJECT_DIR, GRAPH_CHECKPOINT_FILENAME)
GRAPH_CHECKPOINT_TMP_PATH = os.path.join(LOCAL_PROJECT_DIR, GRAPH_CHECKPOINT_TMP_FILENAME)
GRAPH_CHECKPOINT_CACHE_PATH = os.path.join(CACHE_DIR, GRAPH_CHECKPOINT_FILENAME)
GRAPH_CHECKPOINT_BAK_CACHE_PATH = os.path.join(CACHE_DIR, "merchant_graph_backup.pkl")

TIME_WINDOW = 30
ACCOUNT_ACTIVITY_PENALTY = 0.5

DISTANCE_THRESHOLD = 1800
RESOLUTION = 1.0
MIN_GEO_CLUSTER_SIZE = 8

RISK_TOP2_GAP_THRESHOLD = 0.15
RISK_TOP1_MAX_THRESHOLD = 0.65
RISK_MIN_SECOND_PROBABILITY = 0.20

LON_MIN, LON_MAX = 120.8, 122.2
LAT_MIN, LAT_MAX = 30.6, 31.9

TARGET_COLUMNS = [
    "storename",
    "pos_longitude",
    "pos_latitude",
    "geo_cluster_id",
    "community_id",
    "center_longitude_latitude",
    "dt",
]


def log_step(step_name, start_time, extra_info=""):
    return


def latlon_to_xy(lon, lat):
    earth_radius = 6371000.0
    lon_rad = np.radians(lon.astype(np.float64))
    lat_rad = np.radians(lat.astype(np.float64))
    lat0 = np.mean(lat_rad)
    x = earth_radius * lon_rad * np.cos(lat0)
    y = earth_radius * lat_rad
    return np.column_stack([x, y])


def build_geo_location_data(dataframe):
    start_time = time.time()
    loc = dataframe.dropna(subset=["pos_longitude", "pos_latitude"]).copy()
    loc["pos_longitude"] = pd.to_numeric(loc["pos_longitude"], errors="coerce")
    loc["pos_latitude"] = pd.to_numeric(loc["pos_latitude"], errors="coerce")
    loc = loc[
        loc["pos_longitude"].between(LON_MIN, LON_MAX)
        & loc["pos_latitude"].between(LAT_MIN, LAT_MAX)
    ].copy()
    loc = loc[["storename", "pos_longitude", "pos_latitude"]].drop_duplicates(
        subset=["storename"],
        keep="first",
    )
    if loc.empty:
        raise ValueError("No valid geo merchants available for clustering.")

    coords_xy = latlon_to_xy(loc["pos_longitude"].values, loc["pos_latitude"].values)
    loc["_x"] = coords_xy[:, 0]
    loc["_y"] = coords_xy[:, 1]
    log_step("build_geo_location_data", start_time, f"merchant_count={len(loc)}")
    return loc


def build_graph_only_partition(graph):
    start_time = time.time()
    if graph.number_of_nodes() == 0:
        return {}, {}

    if graph.number_of_edges() == 0:
        partition = {merchant: idx for idx, merchant in enumerate(sorted(graph.nodes()))}
    else:
        raw_partition = community_louvain.best_partition(
            graph,
            weight="weight",
            resolution=RESOLUTION,
            random_state=42,
        )
        counts = Counter(raw_partition.values())
        sorted_ids = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        id_map = {old_id: new_id for new_id, (old_id, _) in enumerate(sorted_ids)}
        partition = {merchant: id_map[cid] for merchant, cid in raw_partition.items()}

    communities = {}
    for merchant, cid in partition.items():
        communities.setdefault(cid, []).append(merchant)
    log_step(
        "build_graph_only_partition",
        start_time,
        f"community_count={len(communities)}, merchant_count={len(partition)}",
    )
    return communities, partition


def build_transaction_edges(dataframe):
    total_start = time.time()
    tx = dataframe[["account_number", "transaction_time", "storename"]].sort_values(
        ["account_number", "transaction_time"]
    )

    edge_weights = defaultdict(float)
    account_count = 0

    for _, group in tx.groupby("account_number", sort=False):
        account_count += 1
        group = group.reset_index(drop=True)
        merchants = group["storename"].tolist()
        times = group["transaction_time"].tolist()
        group_size = len(group)
        account_penalty = max(group_size, 1) ** ACCOUNT_ACTIVITY_PENALTY

        for i in range(group_size - 1):
            merchant_a = merchants[i]
            time_a = times[i]

            for j in range(i + 1, group_size):
                merchant_b = merchants[j]
                time_b = times[j]
                diff_minutes = (time_b - time_a).total_seconds() / 60.0

                if diff_minutes > TIME_WINDOW:
                    break
                if merchant_a == merchant_b:
                    continue

                if diff_minutes <= 5:
                    base_weight = 3.0
                elif diff_minutes <= 15:
                    base_weight = 2.0
                else:
                    base_weight = 1.0
                weight = base_weight / account_penalty
                merchant_left, merchant_right = sorted((merchant_a, merchant_b))
                edge_weights[(merchant_left, merchant_right)] += weight

    edge_df = pd.DataFrame(
        [(a, b, w, w) for (a, b), w in edge_weights.items()],
        columns=["merchant_a", "merchant_b", "raw_weight", "weight"],
    )
    log_step(
        "build_transaction_edges",
        total_start,
        f"tx_rows={len(tx)}, account_count={account_count}, edge_count={len(edge_df)}",
    )
    return edge_df


def build_graph_from_edge_df(edge_df):
    start_time = time.time()
    graph = nx.Graph()

    for row in edge_df.itertuples(index=False):
        if not row.merchant_a or not row.merchant_b or row.merchant_a == row.merchant_b:
            continue
        graph.add_edge(
            row.merchant_a,
            row.merchant_b,
            weight=float(row.weight),
            raw_weight=float(row.raw_weight),
        )

    log_step(
        "build_graph_from_edge_df",
        start_time,
        f"node_count={graph.number_of_nodes()}, edge_count={graph.number_of_edges()}",
    )
    return graph


def build_or_update_graph(edge_df):
    start_time = time.time()
    use_checkpoint = ENABLE_GRAPH_CHECKPOINT and os.path.exists(GRAPH_CHECKPOINT_CACHE_PATH)

    if use_checkpoint:
        if not INCREMENTAL_SOURCE_IS_DELTA:
            raise ValueError("Graph checkpoint exists, but current input is not marked as incremental.")
        os.makedirs(LOCAL_PROJECT_DIR, exist_ok=True)
        copyfile(GRAPH_CHECKPOINT_CACHE_PATH, GRAPH_CHECKPOINT_PATH)
        with open(GRAPH_CHECKPOINT_PATH, "rb") as file_obj:
            graph = pickle.load(file_obj)
        for row in edge_df.itertuples(index=False):
            if not row.merchant_a or not row.merchant_b or row.merchant_a == row.merchant_b:
                continue
            if graph.has_edge(row.merchant_a, row.merchant_b):
                graph[row.merchant_a][row.merchant_b]["weight"] += float(row.weight)
                graph[row.merchant_a][row.merchant_b]["raw_weight"] += float(row.raw_weight)
            else:
                graph.add_edge(
                    row.merchant_a,
                    row.merchant_b,
                    weight=float(row.weight),
                    raw_weight=float(row.raw_weight),
                )
        run_mode = "incremental"
    else:
        graph = build_graph_from_edge_df(edge_df)
        run_mode = "full"

    if ENABLE_GRAPH_CHECKPOINT:
        os.makedirs(LOCAL_PROJECT_DIR, exist_ok=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(GRAPH_CHECKPOINT_TMP_PATH, "wb") as file_obj:
            pickle.dump(graph, file_obj, protocol=pickle.HIGHEST_PROTOCOL)
        copyfile(GRAPH_CHECKPOINT_TMP_PATH, GRAPH_CHECKPOINT_PATH)
        if os.path.exists(GRAPH_CHECKPOINT_CACHE_PATH):
            copyfile(GRAPH_CHECKPOINT_CACHE_PATH, GRAPH_CHECKPOINT_BAK_CACHE_PATH)
        try:
            copyfile(GRAPH_CHECKPOINT_TMP_PATH, GRAPH_CHECKPOINT_CACHE_PATH)
        except Exception:
            if os.path.exists(GRAPH_CHECKPOINT_BAK_CACHE_PATH):
                copyfile(GRAPH_CHECKPOINT_BAK_CACHE_PATH, GRAPH_CHECKPOINT_CACHE_PATH)
                raise
    log_step(
        "build_or_update_graph",
        start_time,
        f"mode={run_mode}, node_count={graph.number_of_nodes()}, edge_count={graph.number_of_edges()}",
    )
    return graph, run_mode


def build_geo_clusters(loc):
    start_time = time.time()
    cluster = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=DISTANCE_THRESHOLD,
        linkage="complete",
    )
    loc = loc.copy()
    loc["geo_cluster_id"] = cluster.fit_predict(loc[["_x", "_y"]].values)
    log_step(
        "build_geo_clusters",
        start_time,
        f"geo_cluster_count={loc['geo_cluster_id'].nunique()}",
    )
    return loc


def louvain_inside_geo_cluster(graph, merchants, start_id):
    partition = {}
    next_id = start_id
    merchants = set(merchants)

    if len(merchants) < MIN_GEO_CLUSTER_SIZE:
        for merchant in merchants:
            partition[merchant] = next_id
        return partition, next_id + 1

    subgraph = graph.subgraph(merchants).copy()
    if subgraph.number_of_edges() == 0:
        for merchant in merchants:
            partition[merchant] = next_id
            next_id += 1
        return partition, next_id

    sub_partition = community_louvain.best_partition(
        subgraph,
        weight="weight",
        resolution=RESOLUTION,
        random_state=42,
    )
    local_map = {}
    for local_id in sorted(set(sub_partition.values())):
        local_map[local_id] = next_id
        next_id += 1
    for merchant, local_id in sub_partition.items():
        partition[merchant] = local_map[local_id]
    return partition, next_id


def geo_hierarchical_graph_clustering(graph, loc):
    total_start = time.time()
    loc = loc[loc["storename"].isin(graph.nodes())].copy()
    if loc.empty:
        raise ValueError("Graph nodes and geo merchants do not overlap.")

    loc = build_geo_clusters(loc)
    partition = {}
    next_id = 0

    for _, group in loc.groupby("geo_cluster_id"):
        merchants = set(group["storename"])
        part, next_id = louvain_inside_geo_cluster(graph, merchants, next_id)
        partition.update(part)

    counts = Counter(partition.values())
    sorted_ids = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    id_map = {old_id: new_id for new_id, (old_id, _) in enumerate(sorted_ids)}
    partition = {merchant: id_map[cid] for merchant, cid in partition.items()}

    communities = {}
    for merchant, cid in partition.items():
        communities.setdefault(cid, []).append(merchant)
    log_step(
        "geo_hierarchical_graph_clustering",
        total_start,
        f"community_count={len(communities)}, merchant_count={len(partition)}",
    )
    return communities, partition, loc


def assign_non_geo_merchants(graph, geo_partition, all_merchants, clean_data):
    total_start = time.time()
    geo_merchants = set(geo_partition.keys())
    non_geo_merchants = sorted(set(all_merchants["storename"]) - geo_merchants)

    dt_lookup = (
        clean_data.assign(dt=clean_data["transaction_time"].dt.strftime("%Y%m%d"))
        .sort_values(["storename", "transaction_time"])
        .drop_duplicates(subset=["storename"], keep="last")
        .set_index("storename")["dt"]
        .to_dict()
    )

    assigned_rows = []
    risk_rows = []

    for merchant in non_geo_merchants:
        if merchant not in graph:
            continue

        community_scores = defaultdict(float)
        for neighbor, edge_attrs in graph[merchant].items():
            neighbor_community = geo_partition.get(neighbor)
            if neighbor_community is None:
                continue
            community_scores[int(neighbor_community)] += float(edge_attrs.get("weight", 0.0))

        if not community_scores:
            continue

        total_score = sum(community_scores.values())
        if total_score <= 0:
            continue

        sorted_probs = sorted(
            ((community_id, score / total_score) for community_id, score in community_scores.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        top1_id, top1_prob = sorted_probs[0]
        top2_prob = sorted_probs[1][1] if len(sorted_probs) > 1 else 0.0
        probability_gap = top1_prob - top2_prob

        assigned_rows.append(
            {
                "storename": merchant,
                "community_id": int(top1_id),
                "top1_probability": float(top1_prob),
                "top2_probability": float(top2_prob),
                "probability_gap": float(probability_gap),
                "dt": dt_lookup.get(merchant, ""),
            }
        )

        if (
            len(sorted_probs) > 1
            and top2_prob >= RISK_MIN_SECOND_PROBABILITY
            and probability_gap <= RISK_TOP2_GAP_THRESHOLD
            and top1_prob <= RISK_TOP1_MAX_THRESHOLD
        ):
            risk_rows.append(
                {
                    "storename": merchant,
                    "community_id": int(top1_id),
                    "top1_probability": float(top1_prob),
                    "top2_probability": float(top2_prob),
                    "probability_gap": float(probability_gap),
                    "dt": dt_lookup.get(merchant, ""),
                }
            )

    assigned_df = pd.DataFrame(assigned_rows)
    risk_df = pd.DataFrame(risk_rows)
    log_step(
        "assign_non_geo_merchants",
        total_start,
        f"assigned_count={len(assigned_df)}, risk_count={len(risk_df)}",
    )
    return assigned_df, risk_df


def build_geo_merchant_result(loc, partition, source_data):
    total_start = time.time()
    merchant_df = loc.copy()
    merchant_df["community_id"] = merchant_df["storename"].map(partition)
    merchant_df = merchant_df.dropna(subset=["community_id"]).copy()
    merchant_df["community_id"] = merchant_df["community_id"].astype(int)
    merchant_df["geo_cluster_id"] = merchant_df["geo_cluster_id"].astype(int)
    source_dt = source_data[["storename", "transaction_time"]].copy()
    source_dt["dt"] = dtDate.dt_date("T-1")
    source_dt = (
        source_dt.sort_values(["storename", "transaction_time"])
        .drop_duplicates(subset=["storename"], keep="last")[["storename", "dt"]]
    )
    merchant_df = merchant_df.merge(source_dt, on="storename", how="left")
    merchant_df["dt"] = merchant_df["dt"].fillna("")

    centers = (
        merchant_df.groupby("community_id")
        .agg(center_lon=("pos_longitude", "mean"), center_lat=("pos_latitude", "mean"))
        .reset_index()
    )
    centers["center_longitude_latitude"] = centers.apply(
        lambda row: f"{row['center_lon']:.6f},{row['center_lat']:.6f}",
        axis=1,
    )
    merchant_df = merchant_df.merge(
        centers[["community_id", "center_longitude_latitude", "center_lon", "center_lat"]],
        on="community_id",
        how="left",
    )

    merchant_df = merchant_df[
        [
            "storename",
            "pos_longitude",
            "pos_latitude",
            "geo_cluster_id",
            "community_id",
            "center_longitude_latitude",
            "center_lon",
            "center_lat",
            "_x",
            "_y",
            "dt",
        ]
    ].copy()
    log_step("build_geo_merchant_result", total_start, f"merchant_count={len(merchant_df)}")
    return merchant_df, centers


def build_graph_only_result(partition, source_data):
    total_start = time.time()
    if not partition:
        empty_output = pd.DataFrame(
            columns=[
                "storename",
                "pos_longitude",
                "pos_latitude",
                "geo_cluster_id",
                "community_id",
                "center_longitude_latitude",
                "center_lon",
                "center_lat",
                "_x",
                "_y",
                "dt",
            ]
        )
        empty_centers = pd.DataFrame(columns=["community_id", "center_lon", "center_lat", "center_longitude_latitude"])
        return empty_output, empty_centers

    source_dt = source_data[["storename", "transaction_time"]].copy()
    source_dt["dt"] = dtDate.dt_date("T-1")
    source_dt = (
        source_dt.sort_values(["storename", "transaction_time"])
        .drop_duplicates(subset=["storename"], keep="last")[["storename", "dt"]]
    )

    merchant_df = pd.DataFrame({"storename": sorted(partition.keys())})
    merchant_df["community_id"] = merchant_df["storename"].map(partition).astype(int)
    merchant_df = merchant_df.merge(source_dt, on="storename", how="left")
    merchant_df["dt"] = merchant_df["dt"].fillna("")
    merchant_df["pos_longitude"] = ""
    merchant_df["pos_latitude"] = ""
    merchant_df["geo_cluster_id"] = ""
    merchant_df["center_longitude_latitude"] = ""
    merchant_df["center_lon"] = np.nan
    merchant_df["center_lat"] = np.nan
    merchant_df["_x"] = np.nan
    merchant_df["_y"] = np.nan
    merchant_df = merchant_df[
        [
            "storename",
            "pos_longitude",
            "pos_latitude",
            "geo_cluster_id",
            "community_id",
            "center_longitude_latitude",
            "center_lon",
            "center_lat",
            "_x",
            "_y",
            "dt",
        ]
    ].copy()

    centers = (
        merchant_df[["community_id", "center_lon", "center_lat", "center_longitude_latitude"]]
        .drop_duplicates(subset=["community_id"])
        .reset_index(drop=True)
    )
    log_step("build_graph_only_result", total_start, f"merchant_count={len(merchant_df)}")
    return merchant_df, centers


def evaluate_clustering(merchant_df, graph):
    return {}


def calculate_merchant_coverage(all_merchants, geo_merchant_df, non_geo_assigned_df):
    start_time = time.time()
    total_merchants = int(all_merchants["storename"].nunique())
    covered_merchants = set(geo_merchant_df["storename"])
    if not non_geo_assigned_df.empty:
        covered_merchants.update(non_geo_assigned_df["storename"].tolist())
    covered_count = len(covered_merchants)
    coverage = covered_count / total_merchants if total_merchants else 0.0
    log_step(
        "calculate_merchant_coverage",
        start_time,
        f"covered_count={covered_count}, total_merchants={total_merchants}",
    )
    return {
        "covered_merchant_count": covered_count,
        "total_merchant_count": total_merchants,
        "merchant_coverage": coverage,
    }


def build_risk_output(centers_df, risk_non_geo_df):
    start_time = time.time()
    if risk_non_geo_df.empty:
        return pd.DataFrame(columns=TARGET_COLUMNS)

    center_lookup = centers_df[["community_id", "center_longitude_latitude"]].copy()
    risk_df = risk_non_geo_df.merge(center_lookup, on="community_id", how="left")
    risk_df["pos_longitude"] = ""
    risk_df["pos_latitude"] = ""
    risk_df["geo_cluster_id"] = ""
    risk_df["community_id"] = risk_df["community_id"].astype(str)
    risk_df["storename"] = risk_df["storename"].astype(str)
    risk_df["dt"] = risk_df["dt"].astype(str)
    risk_df["center_longitude_latitude"] = risk_df["center_longitude_latitude"].fillna("")
    risk_df = risk_df[risk_df["dt"] != ""].copy()
    risk_df = risk_df[TARGET_COLUMNS]
    log_step("build_risk_output", start_time, f"risk_count={len(risk_df)}")
    return risk_df


def build_full_output(geo_merchant_df, centers_df, non_geo_assigned_df):
    start_time = time.time()
    geo_output = geo_merchant_df[
        [
            "storename",
            "pos_longitude",
            "pos_latitude",
            "geo_cluster_id",
            "community_id",
            "center_longitude_latitude",
            "dt",
        ]
    ].copy()
    geo_output["pos_longitude"] = geo_output["pos_longitude"].map(
        lambda value: f"{float(value):.6f}" if value != "" else DEFAULT_TEST_LONGITUDE
    )
    geo_output["pos_latitude"] = geo_output["pos_latitude"].map(
        lambda value: f"{float(value):.6f}" if value != "" else DEFAULT_TEST_LATITUDE
    )
    geo_output["geo_cluster_id"] = geo_output["geo_cluster_id"].astype(str)
    geo_output["community_id"] = geo_output["community_id"].astype(str)
    geo_output["storename"] = geo_output["storename"].astype(str)
    geo_output["dt"] = geo_output["dt"].astype(str)

    if non_geo_assigned_df.empty:
        full_output = geo_output.copy()
    else:
        center_lookup = centers_df[["community_id", "center_longitude_latitude"]].copy()
        non_geo_output = non_geo_assigned_df.merge(center_lookup, on="community_id", how="left")
        non_geo_output["storename"] = non_geo_output["storename"].astype(str)
        non_geo_output["pos_longitude"] = ""
        non_geo_output["pos_latitude"] = ""
        non_geo_output["geo_cluster_id"] = ""
        non_geo_output["community_id"] = non_geo_output["community_id"].astype(str)
        non_geo_output["center_longitude_latitude"] = non_geo_output["center_longitude_latitude"].fillna("")
        non_geo_output["dt"] = non_geo_output["dt"].astype(str)
        non_geo_output = non_geo_output[TARGET_COLUMNS]
        full_output = pd.concat([geo_output, non_geo_output], ignore_index=True)

    full_output = full_output[full_output["dt"] != ""].copy()
    full_output = full_output.drop_duplicates(subset=["storename"], keep="last").reset_index(drop=True)
    log_step("build_full_output", start_time, f"output_count={len(full_output)}")
    return full_output


def print_quality_metrics(metrics):
    return


def overwrite_target_table(result, table_name, temp_table_name):
    start_time = time.time()
    if result.empty:
        log_step("overwrite_target_table", start_time, f"result_empty_skip table={table_name}")
        return

    select_columns = ", ".join(
        [
            "storename",
            "pos_longitude",
            "pos_latitude",
            "geo_cluster_id",
            "community_id",
            "center_longitude_latitude",
        ]
    )

    for dt_value, partition_df in result.groupby("dt", sort=True):
        write_df = partition_df.drop(columns=["dt"]).reset_index(drop=True)
        sd.execute_sql(f"drop table if exists {temp_table_name}")
        try:
            sd.write_table(write_df, temp_table_name, debug=False, dt=None)
            sd.execute_sql(
                f"""
                insert overwrite table {table_name}
                partition (dt={dt_value})
                select {select_columns}
                from {temp_table_name}
                """
            )
        finally:
            sd.execute_sql(f"drop table if exists {temp_table_name}")

    log_step(
        "overwrite_target_table",
        start_time,
        f"table={table_name}, row_count={len(result)}, partition_count={result['dt'].nunique()}",
    )


class TaskMain:
    def __init__(self):
        self.dt_var = None

    def check(self):
        mountCheck.mount_check()

    def taskrun(self):
        try:
            total_start = time.time()
            self.dt_var = dtDate.dt_date("T-1")
            logrecord.log_data(f"task dt={self.dt_var}")
            read_start = time.time()
            table_name = "dev_icamp.icamp_merchant_cluster_algo_input"
            dt_list = [dtDate.dt_date("T-1")]
            print(dt_list)
            clean_data = sd.read_table(table_name, dt=dt_list)
            clean_data["transaction_time"] = pd.to_datetime(clean_data["transaction_time"], errors="coerce")
            clean_data = clean_data.dropna(subset=["transaction_time"]).sort_values(
                ["transaction_time", "account_number", "storename"]
            ).reset_index(drop=True)
            log_step("load_source_data", read_start, f"shape={getattr(clean_data, 'shape', None)}")

            graph, run_mode = build_or_update_graph(build_transaction_edges(clean_data))
            geo_source = clean_data.dropna(subset=["pos_longitude", "pos_latitude"])
            if geo_source.empty:
                _, geo_partition = build_graph_only_partition(graph)
                geo_merchant_df, centers_df = build_graph_only_result(geo_partition, clean_data)
            else:
                _, geo_partition, geo_loc = geo_hierarchical_graph_clustering(
                    graph,
                    build_geo_location_data(clean_data),
                )
                geo_merchant_df, centers_df = build_geo_merchant_result(geo_loc, geo_partition, clean_data)
            non_geo_assigned_df, risk_non_geo_df = assign_non_geo_merchants(
                graph,
                geo_partition,
                pd.DataFrame({"storename": sorted(clean_data["storename"].dropna().unique())}),
                clean_data,
            )

            metrics = evaluate_clustering(geo_merchant_df, graph)
            coverage_metrics = calculate_merchant_coverage(
                pd.DataFrame({"storename": sorted(clean_data["storename"].dropna().unique())}),
                geo_merchant_df,
                non_geo_assigned_df,
            )
            full_output = build_full_output(geo_merchant_df, centers_df, non_geo_assigned_df)
            risk_output = build_risk_output(centers_df, risk_non_geo_df)
            overwrite_target_table(full_output, TARGET_TABLE, TARGET_TEMP_TABLE)
            overwrite_target_table(risk_output, RISK_TARGET_TABLE, RISK_TARGET_TEMP_TABLE)

            print_quality_metrics(metrics)
            log_step("taskrun", total_start)
        except Exception:
            formattedExc.formatted_exc()
            raise

        logrecord.log_data("python task log record.")

    def destroy(self):
        for table_name in [TARGET_TEMP_TABLE, RISK_TARGET_TEMP_TABLE]:
            try:
                sd.execute_sql(f"drop table if exists {table_name}")
            except Exception:
                pass


if __name__ == "__main__":
    tracemalloc.start()
    start_time = datetime.datetime.now()
    task = TaskMain()

    try:
        task.check()
        task.taskrun()
    finally:
        task.destroy()

    end_time = datetime.datetime.now()
    time_difference = end_time - start_time
    logrecord.log_data(f"task use time {time_difference}")
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    print(
        f"memory_current_mb = {current_memory / 1024 / 1024:.2f},"
        f"memory_peak_mb = {peak_memory / 1024 / 1024:.2f}"
    )
    taskfinish.finish_task()
