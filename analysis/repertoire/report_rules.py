"""Short, module-specific definitions shared by published report metadata."""
RULES = {
    'metrics': [
        ('D50', '按 umi_counts 从高到低排序，累计丰度不超过总量 50% 的记录数 ÷ 记录总数 × 100%。'),
        ('Shannon', '根据各记录的相对丰度计算熵，同时反映数量与丰度均匀性。'),
        ('Pielou', 'Shannon 指数经记录数量标准化后的均匀度。'),
        ('Gini', '衡量 umi_counts 分布的不均匀程度，值越大表示丰度越集中。'),
        ('Simpson', '1 − 各记录相对丰度的平方和，值越大表示多样性越高。'),
        ('Simpson evenness', '相对丰度平方和倒数 ÷ 记录数量，用于衡量丰度均匀程度。'),
        ('Largest clone %', '按 CDR3aa 合并丰度后，最大克隆的 umi_counts ÷ 总 umi_counts × 100%。'),
        ('Mean SHM', '各同种型 V 区突变比例的 copy 加权均值。IgM 与 IgD 合为 IgD/M。'),
        ('B cell Isotype Distribution', '按明确 C 基因区分同种型。% Expression 为各同种型 copy 总和占可分类 copy 总和的比例；% Unique CDR3 为各 C 基因内有效 CDR3aa 去重数占这些去重计数之和的比例。'),
        ('CDR3s observed as other isotype', '本组在其他同种型中也出现的唯一 CDR3aa 数 ÷ 本组唯一 CDR3aa 数；IgD/M 内部共享不计入，按去重数量计算，不按 copy 加权。'),
    ],
    'overview': [
        ('引物匹配', 'Raw 在原始 R1 上识别引物；Mapping 阶段在比对输入上识别引物。两阶段独立统计。'),
        ('% Expression', '各 Isotype 的 reads ÷ 当前样本中有可分类 C 注释的 Mapping reads 总数，各类占比合计为 100%。'),
        ('% Unique CDR3', '各 Isotype 内对有效 CDR3aa 去重，以各主类去重数之和为分母；主类合并后重新去重，不直接相加亚类的去重数。'),
    ],
    'matching': [
        ('Raw / Mapping', 'Raw 为原始 R1 的引物匹配；Mapping 为比对输入 reads 的引物匹配。两个阶段分别计算比例。'),
        ('匹配率', '唯一匹配、多重匹配和未匹配分别占本阶段全部可评估 reads 的比例。'),
        ('多重匹配', '同一 read 命中多个引物时单独归为多重匹配，不分摊给各引物。'),
    ],
    'mapping': [
        ('Mapping reads', '从 IgBLAST output 中统计具有有效 V 注释、且 locus 与所选链一致的 reads。'),
        ('Mapping rate', '当前引物的 Mapping reads ÷ 该引物唯一匹配的比对输入 reads。'),
        ('Retained reads', '通过当前分析筛选条件后保留的 reads。'),
    ],
    'genes': [
        ('Mapping reads', 'IgBLAST output 中具有有效 V 注释、且 locus 与所选链一致的 reads。'),
        ('% Expression', '该基因的 reads ÷ 当前样本或引物中、该基因区可明确归属到基因的 reads 总数。V/J/C 分别计算分母。'),
        ('% Unique CDR3', '每个明确归属的基因内对有效 CDR3aa 去重，以这些基因的去重计数之和为分母。同一 CDR3aa 跨基因出现时分别计入；无注释及未唯一归属的数据不计入分母。'),
    ],
    'outputs': [
        ('Total Reads', '所选输入范围内双端合并后的 Merged reads，每个成功合并的配对计一条。共用输入的不同链可重复显示该值，不能跨链相加。'),
        ('Total Receptor RNA', 'AIRR 后处理后 umi_counts 的总和（copy 按 umi_counts 记录）。'),
        ('Total Unique CDR3s', '同一 AIRR 后处理范围、当前链内的唯一 CDR3aa 数，不是各基因去重数的累加。'),
    ],
    'stages': [
        ('Raw', '原始 R1、R2 的 reads 数，分别统计。'),
        ('Clean / Fastp', 'Fastp 质控后 R1、R2 的 reads 数。变化率 =（Clean − Raw）÷ Raw × 100%，在同一读端内计算。'),
        ('Merged', '双端合并后的 reads 数，每个成功合并的配对计一条；不与 R1 + R2 的读端数直接计算损失率。'),
        ('Retained', '各链筛选保留的 reads。保留率 = 保留 reads ÷ 本链的比对输入 reads × 100%。'),
    ],
    'reference': [
        ('基因匹配', '以本次所用数据库的 V/J/C 基因为范围，统计已匹配与未匹配状态。'),
    ],
}


ENGLISH_RULES = {
    'metrics': [
        ('D50', 'Records with cumulative abundance at most 50% of the total, sorted by descending umi_counts, divided by the total number of records and multiplied by 100%.'),
        ('Shannon', 'Entropy of relative record abundances, reflecting both abundance diversity and evenness.'),
        ('Pielou', 'Shannon entropy normalized by the number of records to describe evenness.'),
        ('Gini', 'Inequality of the umi_counts distribution; larger values indicate greater concentration.'),
        ('Simpson', 'One minus the sum of squared relative record abundances; larger values indicate greater diversity.'),
        ('Simpson evenness', 'The reciprocal sum of squared relative abundances divided by the number of records.'),
        ('Largest clone %', 'Largest pooled CDR3aa umi_counts divided by total umi_counts, multiplied by 100%.'),
        ('Mean SHM', 'Copy-weighted mean V-region mutation fraction. IgM and IgD are pooled as IgD/M.'),
        ('B cell Isotype Distribution', 'Isotypes defined by uniquely classified C genes. Expression divides each isotype copy total by all classifiable copies. Unique CDR3 divides valid CDR3aa distinct counts within each C gene by the sum of these counts.'),
        ('CDR3s observed as other isotype', 'Distinct CDR3aa shared with other isotypes divided by distinct CDR3aa in the current group. IgD/M internal sharing is excluded; counts are not copy-weighted.'),
    ],
    'overview': [
        ('Primer matching', 'Primers are identified independently in raw R1 reads and in mapping input reads.'),
        ('% Expression', 'Isotype reads divided by mapped reads with a classifiable C annotation in the current sample. The complete composition sums to 100%.'),
        ('% Unique CDR3', 'Distinct valid CDR3aa are counted within each isotype; the denominator is the sum of these counts. Main isotype classes are deduplicated again after subclass merging.'),
    ],
    'matching': [
        ('Raw / Mapping', 'Primer matching in raw R1 reads and in mapping input reads, with stage-specific percentages.'),
        ('Match rate', 'Unique, multiple and unmatched reads are divided by all assessable reads in the corresponding stage.'),
        ('Multiple matches', 'A read matching multiple primers is counted separately and is not allocated across primers.'),
    ],
    'mapping': [
        ('Mapping reads', 'IgBLAST output reads with a valid V annotation and a locus consistent with the selected chain.'),
        ('Mapping rate', 'Mapped reads assigned to each primer divided by its uniquely primer-matched mapping input reads.'),
        ('Retained reads', 'Reads passing the filtering criteria of the current analysis.'),
    ],
    'genes': [
        ('Mapping reads', 'IgBLAST output reads with a valid V annotation and a locus consistent with the selected chain.'),
        ('% Expression', 'Reads assigned to a gene divided by all uniquely classified reads for that segment in the current sample or primer. V, J and C use separate denominators.'),
        ('% Unique CDR3', 'Distinct valid CDR3aa are counted within each uniquely assigned gene; the denominator is the sum of these counts. A CDR3aa found in different genes counts once per gene. Missing and unresolved annotations are excluded.'),
    ],
    'outputs': [
        ('Total Reads', 'Merged reads within the selected input scope; each successfully merged read pair counts once. Chains sharing the same input may repeat this count; do not sum it across chains.'),
        ('Total Receptor RNA', 'Sum of umi_counts after AIRR postprocessing; copy counts are recorded as umi_counts.'),
        ('Total Unique CDR3s', 'Distinct CDR3aa in the same postprocessing scope and selected chain; not the sum of gene-specific distinct counts.'),
    ],
    'stages': [
        ('Raw', 'Original R1 and R2 read counts, counted independently.'),
        ('Clean / Fastp', 'R1 and R2 read counts after Fastp quality control. Change = (Clean - Raw) / Raw × 100%, calculated within the same mate.'),
        ('Merged', 'Each successfully merged read pair counts once. A loss rate is not calculated directly against R1 + R2.'),
        ('Retained', 'Reads retained for each chain. Retention = retained reads divided by mapping input reads for the same chain, multiplied by 100%.'),
    ],
    'reference': [
        ('Gene matching', 'Matched and unmatched states of V/J/C genes in the database used for the analysis.'),
    ],
}


def for_report(data, *, language='zh'):
    """Do not describe old, un-recalculated percentages with the new denominator."""
    rules=data.get('usage',{}).get('rules',{})
    english=language=='en'
    source=ENGLISH_RULES if english else RULES
    result=dict(source)
    bcell={'Mean SHM','B cell Isotype Distribution','CDR3s observed as other isotype'}
    result['diversity']=[row for row in source['metrics'] if row[0] not in bcell]
    result['bcell']=[row for row in source['metrics'] if row[0] in bcell and row[0]!='B cell Isotype Distribution']
    if rules.get('isotype_expression_denominator')!='classified_c_reads':
        result['overview']=[(title,('Isotype reads divided by all mapped reads in the current sample.' if english else '各 Isotype 的 reads ÷ 当前样本全部 Mapping reads。') if title=='% Expression' else text) for title,text in source['overview']]
    if rules.get('gene_expression_denominator')!='classified_gene_reads':
        result['genes']=[('Mapping reads',source['genes'][0][1]),('% Expression','Gene reads divided by all mapped reads.' if english else '该基因的 reads ÷ 全部 Mapping reads。'),('% Unique CDR3','Distinct CDR3aa within each gene or unresolved/unannotated category, divided by the sum of these counts.' if english else '在各基因及未唯一归属、无注释类别内分别去重 CDR3aa，以这些类别的去重计数之和为分母。')]
    if rules.get('composition_source')=='published_airr_preprocessing':
        result['genes'] = [
            ('Abundance weight' if english else '丰度权重',
             'Copy counts recorded as umi_counts. Primer-specific counts include only copies with a definite primer assignment.' if english else
             '以 umi_counts 记录的 copy 数作为丰度权重。逐引物统计仅计入能够明确归属到该引物的 copy。'),
            ('% Expression',
             'Gene copy counts divided by all uniquely classified copy counts for that segment in the selected sample or primer. V, J and C have separate denominators.' if english else
             '该基因的 copy 总和 ÷ 当前样本或引物中该区可明确归属基因的 copy 总和。V/J/C 分别计算分母；无注释和无法唯一归属的数据不计入。'),
            source['genes'][2],
        ]
        result['overview'] = [
            source['overview'][0],
            ('% Expression',
             'Isotype copy counts divided by copy counts with a classifiable C annotation. Copy counts are recorded as umi_counts.' if english else
             '各 Isotype 的 copy 总和 ÷ 有可分类 C 注释的 copy 总和，copy 以 umi_counts 记录。'),
            source['overview'][2],
        ]
    result['overview']=[*result['overview'],next(row for row in source['metrics'] if row[0]=='B cell Isotype Distribution')]
    return result
