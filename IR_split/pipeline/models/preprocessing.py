import pandas as pd
def AIRR_filter(df) -> pd.DataFrame:
    df = df.drop(columns=["sequence_id"])
    df = df[~df["locus"].isna()]
    df = df[df["productive"]=="T"]
    df = df[(df["v_score"]>150 )& (df["v_identity"]>85)]
    df = df[(~df["v_call"].isna())|(~df["j_call"].isna())]
    df = pd.merge(df.drop_duplicates().set_index("sequence"),pd.DataFrame(df["sequence"].value_counts()),left_index=True,right_index=True).reset_index()
    df = df.rename(columns={"count":"umi_counts"})
    if df["locus"].str.contains("TR").sum()/df.shape[0]>0.9:
        df = df[df["v_identity"]>=99.7]
    return df