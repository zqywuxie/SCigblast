# models/shm_calculate.py
from Bio import pairwise2
import pandas as pd
from .call_resolution import unique_constant_gene
import numpy as np
import multiprocessing as mp
from collections import OrderedDict, deque
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout

SHM_FIELDS = ('locus', 'v_alignment_start', 'v_alignment_end',
              'sequence_alignment', 'germline_alignment')
_ALIGNMENT_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LIMIT = 16 * 1024 * 1024


def _cached_alignment(seq, germline):
    global _CACHE_BYTES
    key = (seq, germline)
    if key in _ALIGNMENT_CACHE:
        _ALIGNMENT_CACHE.move_to_end(key)
        return _ALIGNMENT_CACHE[key][0]
    result = _calc_v_region_shm(seq, germline)
    size = 256 + 4 * (len(seq) + len(germline))
    if size <= _CACHE_LIMIT:
        while _ALIGNMENT_CACHE and (_CACHE_BYTES + size > _CACHE_LIMIT or len(_ALIGNMENT_CACHE) >= 8192):
            _, (_, removed) = _ALIGNMENT_CACHE.popitem(last=False)
            _CACHE_BYTES -= removed
        _ALIGNMENT_CACHE[key] = (result, size)
        _CACHE_BYTES += size
    return result


def _compute_shm_records(records):
    return [compute_shm(dict(zip(SHM_FIELDS, values))) for values in records]


def _reset_alignment_cache():
    global _CACHE_BYTES
    _ALIGNMENT_CACHE.clear()
    _CACHE_BYTES = 0


def _wait_shm_batch(task, progress, completed, total):
    while True:
        try:
            return task.result(timeout=5)
        except FutureTimeout:
            if progress is not None:
                progress(completed, total)

BCR_LOCI = ["IGH", "IGK", "IGL"]


def _dedup_columns(df: pd.DataFrame) -> pd.DataFrame:
    """确保 columns 唯一；若重复则保留第一列（更安全），也可改成重命名。"""
    if df is None or df.empty:
        return df
    if df.columns.is_unique:
        return df
    return df.loc[:, ~df.columns.duplicated(keep="first")].copy()

def _safe_concat_rows(dfs):
    """按行拼接，自动过滤 None/空df，并确保 columns 唯一。"""
    dfs2 = []
    for d in dfs or []:
        if d is None:
            continue
        if isinstance(d, pd.DataFrame) and len(d) == 0:
            continue
        d = _dedup_columns(d)
        dfs2.append(d)
    if not dfs2:
        return pd.DataFrame()
    # ignore_index=True 会更稳，避免 index 对齐带来的奇怪问题
    return pd.concat(dfs2, axis=0, ignore_index=True, sort=False)


# -----------------------------------------------------
# 基础 SHM 计算（只计算 V 区）
# -----------------------------------------------------
def _calc_v_region_shm(seq, germline):
    aln = pairwise2.align.globalms(seq, germline, 2, -1, -2, -0.5)[0]
    s1, s2 = aln.seqA, aln.seqB

    mismatch = 0
    insertion = 0
    deletion = 0

    for a, b in zip(s1, s2):
        if a == "-" and b != "-":
            deletion += 1
        elif a != "-" and b == "-":
            insertion += 1
        elif a != b:
            mismatch += 1

    aligned_len = sum((a != "-" and b != "-") for a, b in zip(s1, s2))
    total = mismatch + insertion + deletion

    shm_ratio = 0 if aligned_len + insertion + deletion == 0 else \
                total / (aligned_len + insertion + deletion)

    return mismatch, insertion, deletion, total, shm_ratio


# -----------------------------------------------------
# 单条记录计算 SHM
# -----------------------------------------------------
def compute_shm(
    row,
):

    locus = row["locus"].upper()
    if locus not in BCR_LOCI:
        raise ValueError(f"{locus} is not IGH/IGK/IGL")

    V_start = int(row["v_alignment_start"])
    V_end =  int(row["v_alignment_end"])
    seq_V = row["sequence_alignment"][V_start-1:V_end]
    germmline_V = row["germline_alignment"][V_start-1:V_end]

    # ---------- 计算当前链v区 SHM ----------
    v_m, v_i, v_d, v_total, v_ratio = _cached_alignment(
        seq_V,
        germmline_V
    )

    # ---------- 计算全长 SHM ----------
    tt_m, tt_i, tt_d, tt_total, tt_ratio = _cached_alignment(
        row["sequence_alignment"],
        row["germline_alignment"]
    )

    result = {}


    result[f"V_SHM"] = v_total
    result[f"V_SHM_ratio"] = v_ratio
    result[f"V_mismatch"] = v_m
    result[f"V_insertion"] = v_i
    result[f"V_deletion"] = v_d

    result[f"TT_SHM"] = tt_total
    result[f"TT_SHM_ratio"] = tt_ratio
    result[f"TT_mismatch"] = tt_m
    result[f"TT_insertion"] = tt_i
    result[f"TT_deletion"] = tt_d

    return result

def _wavg(x: pd.Series, w: pd.Series) -> float:
    wsum = float(w.sum())
    if wsum == 0:
        return np.nan
    return float((x * w).sum() / wsum)

# -----------------------------------------------------
# 批量处理
# -----------------------------------------------------
def _batch_compute_shm(df):
    shm_df = df.apply(
        lambda row: compute_shm(row),
        axis=1,
        result_type="expand"
    )
    return pd.concat([df, shm_df], axis=1)

# === df 拆分函数 ===
def _split_dataframe(df, n_chunks):
    chunk_size = int(np.ceil(len(df) / n_chunks))
    return [df.iloc[i*chunk_size:(i+1)*chunk_size] for i in range(n_chunks)]


def _split_dataframe(df, n_chunks):
    if df is None or len(df) == 0:
        return []
    n_chunks = max(1, int(n_chunks))
    chunk_size = int(np.ceil(len(df) / n_chunks))
    chunks = []
    for i in range(n_chunks):
        c = df.iloc[i*chunk_size:(i+1)*chunk_size]
        if len(c) > 0:
            chunks.append(c)
    return chunks

def calculate_shm(df: pd.DataFrame, workers=None, progress=None) -> pd.DataFrame:
    # 0) df 为空直接返回空表
    if df is None or len(df) == 0:
        return pd.DataFrame()

    # 1) 计算 worker 数：别用 n_cores-50 这种写法
    n_cores = mp.cpu_count()
    # 给你一个保守策略：最多用 n_cores-1，最少 1
    n_workers = min(len(df), max(1, int(workers) if workers is not None else n_cores - 1))

    # 2) 拆分（拆成不超过 worker 数的块，避免大量空 chunk）
    batch_size = min(256, max(1, int(np.ceil(len(df) / (n_workers * 8)))))
    pending = deque()
    results = []
    completed = 0
    with ProcessPoolExecutor(max_workers=n_workers, initializer=_reset_alignment_cache) as pool:
        for start in range(0, len(df), batch_size):
            records = list(df.iloc[start:start + batch_size].loc[:, SHM_FIELDS].itertuples(index=False, name=None))
            pending.append(pool.submit(_compute_shm_records, records))
            if len(pending) >= 2 * n_workers:
                rows = _wait_shm_batch(pending.popleft(), progress, completed, len(df))
                results.extend(rows)
                completed += len(rows)
                if progress is not None:
                    progress(completed, len(df))
        for task in pending:
            rows = _wait_shm_batch(task, progress, completed, len(df))
            results.extend(rows)
            completed += len(rows)
            if progress is not None:
                progress(completed, len(df))
    # Legacy DataFrame.apply expands each mixed numeric result as float64.
    return _dedup_columns(pd.concat([df.reset_index(drop=True), pd.DataFrame(results, dtype=float)], axis=1))

def calculate_shm_result(df, workers=None, progress=None):
    df=df.copy()
    df['c_call']=df['c_call'].map(unique_constant_gene)
    df_shm = calculate_shm(df, workers=workers, progress=progress)
    shm_dict = {}

    #---计算重链突变比例---#
    df_shm_IGH = df_shm[df_shm["locus"]=="IGH"].copy()
    shm_dict["IGH_V_mutated_percent"] = df_shm_IGH[df_shm_IGH["V_SHM"]!=0]["umi_counts"].sum()/df_shm_IGH["umi_counts"].sum()
    shm_dict["IGH_V_unmutated_percent"] = df_shm_IGH[df_shm_IGH["V_SHM"]==0]["umi_counts"].sum()/df_shm_IGH["umi_counts"].sum()
    shm_dict["IGH_TT_mutated_percent"] = df_shm_IGH[df_shm_IGH["TT_SHM"]!=0]["umi_counts"].sum()/df_shm_IGH["umi_counts"].sum()
    shm_dict["IGH_TT_unmutated_percent"] = df_shm_IGH[df_shm_IGH["TT_SHM"]==0]["umi_counts"].sum()/df_shm_IGH["umi_counts"].sum()

    df_shm_IGH_naive = df_shm_IGH[(df_shm_IGH["c_call"].str.contains("IGHD"))|(df_shm_IGH["c_call"].str.contains("IGHM"))]
    shm_dict["IGH_V_naive_mutated_percent"] = df_shm_IGH_naive[df_shm_IGH_naive["V_SHM"]!=0]["umi_counts"].sum()/df_shm_IGH_naive["umi_counts"].sum()
    shm_dict["IGH_V_naive_unmutated_percent"] = df_shm_IGH_naive[df_shm_IGH_naive["V_SHM"]==0]["umi_counts"].sum()/df_shm_IGH_naive["umi_counts"].sum()
    shm_dict["IGH_TT_naive_mutated_percent"] = df_shm_IGH_naive[df_shm_IGH_naive["TT_SHM"]!=0]["umi_counts"].sum()/df_shm_IGH_naive["umi_counts"].sum()
    shm_dict["IGH_TT_naive_unmutated_percent"] = df_shm_IGH_naive[df_shm_IGH_naive["TT_SHM"]==0]["umi_counts"].sum()/df_shm_IGH_naive["umi_counts"].sum()

    #---计算3条链超突变---#
    
    for chain, chain_SHM in df_shm[["locus","V_SHM","V_SHM_ratio","TT_SHM","TT_SHM_ratio","umi_counts"]].groupby("locus"):
        w = chain_SHM["umi_counts"].fillna(0)

        TT_SHM_ratio = _wavg(chain_SHM["TT_SHM_ratio"].fillna(0), w)
        V_SHM_ratio  = _wavg(chain_SHM["V_SHM_ratio"].fillna(0), w)
        V_SHM        = _wavg(chain_SHM["V_SHM"].fillna(0), w)
        TT_SHM       = _wavg(chain_SHM["TT_SHM"].fillna(0), w)

        shm_dict[f"{chain}_TT_SHM_ratio"] = {"SHM": TT_SHM_ratio}
        shm_dict[f"{chain}_V_SHM_ratio"]  = {"SHM": V_SHM_ratio}
        shm_dict[f"{chain}_V_SHM"]        = {"SHM": V_SHM}
        shm_dict[f"{chain}_TT_SHM"]       = {"SHM": TT_SHM}

    #---计算亚型超突变---#
    df_shm_c = df_shm[["c_call","locus","V_SHM","V_SHM_ratio","TT_SHM","TT_SHM_ratio","umi_counts"]].copy()
    df_shm_c = df_shm_c[df_shm_c["locus"]=="IGH"]
    df_shm_c = df_shm_c[~df_shm_c["c_call"].isna()]
    df_shm_c = df_shm_c[df_shm_c["c_call"].str.contains("IGH")]
    df_shm_c["c_call"] = df_shm_c["c_call"].str.split("*").str[0]
    for isotpye,isotpye_SHM in df_shm_c.groupby("c_call"):
        TT_SHM_ratio = (isotpye_SHM["TT_SHM_ratio"]*isotpye_SHM["umi_counts"]).mean()
        V_SHM_ratio = (isotpye_SHM["V_SHM_ratio"]*isotpye_SHM["umi_counts"]).mean()
        V_SHM = (isotpye_SHM["V_SHM"]*isotpye_SHM["umi_counts"]).mean()
        TT_SHM = (isotpye_SHM["TT_SHM"]*isotpye_SHM["umi_counts"]).mean()
        shm_dict[isotpye+"_ig_TT_SHM_ratio"] = {"SHM":TT_SHM_ratio}
        shm_dict[isotpye+"_ig_V_SHM_ratio"] = {"SHM":V_SHM_ratio}
        shm_dict[isotpye+"_ig_V_SHM"] = {"SHM":V_SHM}
        shm_dict[isotpye+"_ig_TT_SHM"] = {"SHM":TT_SHM}

    return pd.DataFrame(shm_dict)

