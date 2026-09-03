from skbio.diversity.alpha import shannon,pielou_e,gini_index,simpson,simpson_e
import pandas as pd

def calculate_diversity_chain(df) -> pd.DataFrame:
    df_diversity = df[["cdr3_aa","locus","umi_counts"]].copy()
    df_diversity_dict = {}
    for chain, df_cdr3_umi in df_diversity.groupby("locus"):
        df_cdr3_umi = df_cdr3_umi.sort_values("umi_counts",ascending=False)
        half = df_cdr3_umi["umi_counts"][df_cdr3_umi["umi_counts"].cumsum() <= df_cdr3_umi["umi_counts"].sum() * 0.50]
        d50 = (len(half) / df_cdr3_umi.shape[0]) * 100.0

        df_pep = df_cdr3_umi.groupby("cdr3_aa")["umi_counts"].sum().reset_index()
        df_pep = df_pep.sort_values("umi_counts",ascending=False)
        largest_clone_percent = df_pep["umi_counts"].tolist()[0]/df_pep["umi_counts"].sum()

        shannon_index = shannon(df_cdr3_umi["umi_counts"])
        pielou_evenness = pielou_e(df_cdr3_umi["umi_counts"])
        Gini_index = gini_index(df_cdr3_umi["umi_counts"])
        simpson_index = simpson(df_cdr3_umi["umi_counts"])
        simpson_evenness = simpson_e(df_cdr3_umi["umi_counts"])

        df_diversity_dict["{chain}_D50".format(chain=chain)] = {"diversity":d50}
        df_diversity_dict["{chain}_Largest_clone_percent".format(chain=chain)] = {"diversity":largest_clone_percent}
        df_diversity_dict["{chain}_Shannon_index".format(chain=chain)] = {"diversity":shannon_index}
        df_diversity_dict["{chain}_Pielou_evenness".format(chain=chain)] = {"diversity":pielou_evenness}
        df_diversity_dict["{chain}_Gini_index".format(chain=chain)] = {"diversity":Gini_index}
        df_diversity_dict["{chain}_Simpson_index".format(chain=chain)] = {"diversity":simpson_index}
        df_diversity_dict["{chain}_Simpson_evenness".format(chain=chain)] = {"diversity":simpson_evenness}
    return pd.DataFrame(df_diversity_dict)

def calculate_diversity_Bcell(df) -> pd.DataFrame:
    """
    Only input BCR.tsv
    """
    from skbio.diversity.alpha import shannon,pielou_e,gini_index,simpson,simpson_e
    df_diversity = df[["cdr3_aa","locus","c_call","umi_counts"]].copy()
    df_diversity = df_diversity[df_diversity["locus"] == "IGH"]
    df_diversity = df_diversity[~df_diversity["c_call"].isna()]
    df_diversity = df_diversity[~df_diversity["cdr3_aa"].isna()]
    df_diversity["c_call"]  = df_diversity["c_call"].str.split("*").str[0]
    df_diversity = df_diversity[df_diversity["c_call"].str.contains("IGH")]
    df_diversity_dict = {}
    for chain, df_cdr3_umi in df_diversity.groupby("c_call"):
        df_cdr3_umi = df_cdr3_umi.sort_values("umi_counts",ascending=False)
        half = df_cdr3_umi["umi_counts"][df_cdr3_umi["umi_counts"].cumsum() <= df_cdr3_umi["umi_counts"].sum() * 0.50]
        d50 = (len(half) / df_cdr3_umi.shape[0]) * 100.0

        df_pep = df_cdr3_umi.groupby("cdr3_aa")["umi_counts"].sum().reset_index()
        df_pep = df_pep.sort_values("umi_counts",ascending=False)
        largest_clone_percent = df_pep["umi_counts"].tolist()[0]/df_pep["umi_counts"].sum()

        shannon_index = shannon(df_cdr3_umi["umi_counts"])
        pielou_evenness = pielou_e(df_cdr3_umi["umi_counts"])
        Gini_index = gini_index(df_cdr3_umi["umi_counts"])
        simpson_index = simpson(df_cdr3_umi["umi_counts"])
        simpson_evenness = simpson_e(df_cdr3_umi["umi_counts"])

        df_diversity_dict["{chain}_D50".format(chain=chain)] = {"diversity":d50}
        df_diversity_dict["{chain}_Largest_clone_percent".format(chain=chain)] = {"diversity":largest_clone_percent}
        df_diversity_dict["{chain}_Shannon_index".format(chain=chain)] = {"diversity":shannon_index}
        df_diversity_dict["{chain}_Pielou_evenness".format(chain=chain)] = {"diversity":pielou_evenness}
        df_diversity_dict["{chain}_Gini_index".format(chain=chain)] = {"diversity":Gini_index}
        df_diversity_dict["{chain}_Simpson_index".format(chain=chain)] = {"diversity":simpson_index}
        df_diversity_dict["{chain}_Simpson_evenness".format(chain=chain)] = {"diversity":simpson_evenness}
    return pd.DataFrame(df_diversity_dict)