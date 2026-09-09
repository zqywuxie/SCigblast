import pandas as pd
from itertools import combinations as comb

def calculate_csr(df) -> pd.DataFrame:
    """
    计算 CSR
    """
    df_sub = df[["locus","cdr3_aa","v_call","j_call","c_call","umi_counts"]].copy()
    df_sub = df_sub[df_sub["locus"]=="IGH"].copy()
    df_sub = df_sub[~df_sub["c_call"].isna()]
    df_sub = df_sub[df_sub["c_call"].str.contains("IGH")]
    class_unswitched_percent_by_reads = df_sub[(df_sub["c_call"].str.contains("IGHD"))|(df_sub["c_call"].str.contains("IGHM"))]["umi_counts"].sum()/df_sub["umi_counts"].sum()
    class_switched_percent_by_reads = 1-class_unswitched_percent_by_reads

    df_cdr3 = df_sub[df_sub["cdr3_aa"].isin(df_sub["cdr3_aa"].value_counts().loc[lambda x: x > 1].index)]
    df_cdr3 = df_cdr3[["cdr3_aa","c_call","umi_counts"]]
    df_cdr3["c_call"] = df_cdr3["c_call"].str.split("*").str[0]
    df_cdr3 = df_cdr3.groupby(["cdr3_aa","c_call"]).sum().reset_index()
    results = []

    for cdr3, sub in df_cdr3.groupby("cdr3_aa"):
        for (c1, u1), (c2, u2) in comb(
            sub[["c_call", "umi_counts"]].itertuples(index=False), 2
        ):
            results.append({
                "cdr3_aa": cdr3,
                "c_call_1": c1,
                "c_call_2": c2,
                "umi_sum": u1 + u2
            })
    # A sample may have no same-CDR3 isotype pairs. Keep the empty schema
    # so the existing grouping/ratio formulas still work without a KeyError.
    df_CSR_cdr3 = pd.DataFrame(results, columns=["cdr3_aa", "c_call_1", "c_call_2", "umi_sum"])
    df_CSR_cdr3.drop(columns=['cdr3_aa'], inplace=True)
    CSR_matrix = (df_CSR_cdr3.groupby(["c_call_1","c_call_2"]).sum()/df_sub["umi_counts"].sum()).reset_index()
    CSR_matrix = CSR_matrix.rename(columns={"umi_sum":"ratio"})
    CSR_matrix.insert(2,"CSR_name",CSR_matrix["c_call_1"]+"-"+CSR_matrix["c_call_2"]+"_CSR_ratio")
    CSR_matrix.drop(columns=['c_call_1',"c_call_2"], inplace=True)
    CSR_matrix = CSR_matrix.set_index("CSR_name").T
    CSR_matrix.insert(0,"class_unswitched_percent_by_reads",[class_unswitched_percent_by_reads])
    CSR_matrix.insert(0,"class_switched_percent_by_reads",[class_switched_percent_by_reads])
    return CSR_matrix
