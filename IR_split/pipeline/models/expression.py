import pandas as pd

def calculate_expresion(df_list) -> pd.DataFrame:
    df_expression = pd.DataFrame()
    for df in df_list:
        df_expression_t = df[["cdr3_aa","locus","umi_counts"]].copy()
        df_expression = pd.concat([df_expression, df_expression_t])
    chain2uCDR3 = {}
    for chain,df_chain in df_expression.groupby("locus"):
        chain2uCDR3[chain+"_uCDR3"] = {"umi_counts":df_chain.groupby("cdr3_aa")["umi_counts"].sum().shape[0]}
    df_expression_uCDR3 = pd.DataFrame(chain2uCDR3)
    df_expression_counts = pd.DataFrame(df_expression.groupby("locus")["umi_counts"].sum()).T
    df_expression_counts.columns = [col+"_umi_counts" for col in df_expression_counts.columns]
    df_expression_matrix = pd.DataFrame(df_expression.groupby("locus")["umi_counts"].sum()/df_expression["umi_counts"].sum()).T
    df_expression_matrix.columns = [col+"_Percent" for col in df_expression_matrix.columns]
    df_expression_matrix = pd.concat([df_expression_counts,df_expression_uCDR3,df_expression_matrix],axis=1)
    return df_expression_matrix
    
def calculate_isotype_ratio(df_BCR) -> pd.DataFrame:
    df_isotype_expression = df_BCR[["cdr3_aa","locus","c_call","umi_counts"]].copy()
    df_isotype_expression = df_isotype_expression[df_isotype_expression["locus"]=="IGH"].dropna()
    df_isotype_expression = df_isotype_expression[df_isotype_expression["c_call"].str.contains("IGH")]
    df_isotype_expression["c_call"] = df_isotype_expression["c_call"].str.split("*").str[0]
    df_isotype_matrix = df_isotype_expression.groupby("c_call")["umi_counts"].sum()/df_isotype_expression["umi_counts"].sum()
    return pd.DataFrame(df_isotype_matrix).T
