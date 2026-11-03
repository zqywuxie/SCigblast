# SCigblast：TRA/TRB 与 TRG/TRD 链合并及配对方案

## 目标与核心结论

配对分析的细胞键必须是 `(sample_id, cell_barcode)`；UMI 是每条候选链的分子支持证据，不要求 TRA 与 TRB（或 TRG 与 TRD）共享 UMI。

最终结果分两层：

1. **严格功能集**：仅接受唯一、合格的 `TRA + TRB` 或 `TRG + TRD`。
2. **完整候选集**：保留 primary、alternative、多链及混合受体，不能把同 barcode 共现表述为已验证的物理配对。

## 当前 pipeline 的关键问题

- `06.split_and_represent.py` 已按 `(sample_id, barcode, umi)` 选择代表序列，并生成 `representative_id` 与 reads/纯度/歧义信息。
- `07.work_igblastn.sh` 后处理将 AIRR `sequence_id` 覆盖成 barcode，导致 `representative_id`、UMI 及代表序列证据无法在最终结果中追溯。
- `preprocessing.py` 当前的 `umi_counts` 实际是相同 `(barcode, sequence, locus)` 的行数；缺少显式 UMI 时不能保证是真正的 distinct UMI 数。
- 合并表若缺少 `sample_id`，相同 16 bp barcode 可能跨样本误合并。
- `sequence_id_chain_summary.py` 只统计 TRA/TRB 的存在性，不处理 TRG/TRD，也不选择或输出候选配对。

## 数据模型

| 层级 | 唯一键 | 含义 |
|---|---|---|
| 细胞 | `(sample_id, cell_barcode)` | 配对单位 |
| UMI 证据 | `(sample_id, cell_barcode, umi)` | 一条 UMI 代表序列 |
| 注释证据 | `representative_id` | 一条代表序列的 IgBLAST 结果 |
| 链候选 | `(sample_id, cell_barcode, locus, normalized_vdj_sequence)` | 合并相同分子序列后的候选链 |

必须保留字段：`sample_id, cell_barcode, umi, representative_id, representative_reads, representative_percent, ambiguity_status`。AIRR `sequence_id` 建议使用稳定的 `representative_id`，barcode 单独存为 `cell_barcode`。

`normalized_vdj_sequence` 必须排除 barcode、UMI、TSO、primer/adaptor，优先由 AIRR alignment 或明确 V/J 坐标生成；规则需版本化。

## 链合并规则

| 情况 | 决策 |
|---|---|
| 同 sample、barcode、locus，normalized VDJ nt 完全相同 | 合并，`umi_support=count(distinct umi)`，累加 read support |
| V/J/C、CDR3 nt 相同，但完整序列存在侧翼差异 | 归入同一链家族；保留所有构建序列和代表序列 |
| V/J 相同，但 CDR3 nt 不同 | 不合并 |
| CDR3 aa 相同、CDR3 nt 不同 | 链候选不合并；仅可归入同一蛋白 clonotype group |
| 不同 locus、barcode 或 sample | 不合并 |
| 注释冲突、locus 模糊或 UMI `TIED_TOP` | 保留并标记，排除或降级严格集 |

“mapping 到相同 V/J”不足以判定同一条链。严格链身份优先使用 locus + CDR3/junction nt；用于功能克隆时还必须保留完整生物学序列。

## 候选链门槛与排序

进入 primary 竞争的基本门槛：provenance 完整、locus 属于 TRA/TRB/TRG/TRD、productive、CDR3 nt/aa 完整、V/J 有效、无未解决的 locus/annotation 冲突。没有可靠 full-length 字段时写 `UNKNOWN`，不能由 productive 推断 full-length。

候选排序特征依次考虑：

1. 完整性及 productive/annotation 质量；
2. distinct UMI support；
3. representative read support；
4. UMI 内代表序列纯度；
5. V alignment score/identity 与注释一致性。

UMI 最多者只能成为 primary 候选，不能静默删除其余链。top 与 second 接近时标记 `UNRESOLVED_TOP`。最低 UMI 和 top/second 比值需按 sample、batch、locus 用数据分布或阳性对照校准，不直接照搬 Cell Ranger 阈值。

## 配对及 barcode 状态

只允许：

- `TRA × TRB` → `alpha_beta`
- `TRG × TRD` → `gamma_delta`

推荐状态：

- `SINGLE_AB`：1 TRA + 1 TRB，可进入严格 αβ 集。
- `SINGLE_GD`：1 TRG + 1 TRD，可进入严格 γδ 集。
- `DUAL_ALPHA`：2 TRA + 1 TRB，输出两个候选组合，不进入严格唯一配对集。
- `DUAL_BETA`：1 TRA + 2 TRB，同上。
- `MULTI_CANDIDATE_AB/GD`：两侧多链，保留组合与歧义。
- `MIXED_RECEPTOR`：同 barcode 同时存在 αβ 与 γδ，分别保留，标记疑似 doublet/污染，不给唯一受体。
- `ORPHAN_TRA/TRB/TRG/TRD`：只有单侧链。
- `MULTICHAIN_SUSPECT`：任一 locus >2 或 productive 总链数 >4；作为待校准 QC，不直接宣称是 doublet。
- `NO_VALID_CHAIN`、`REVIEW_REQUIRED`、`INVALID_PROVENANCE`。

所有组合均记录 `pairing_evidence=SAME_BARCODE_ONLY`；除非另有实验验证，`physical_pair_verified=FALSE`。

## 输出

1. `chain_candidates.tsv.gz`：一行一条候选链，包含序列、V/D/J/C、CDR3、UMI/read 支持、rank/role、证据 IDs、冲突和拒绝原因。
2. `receptor_pairs.tsv.gz`：一行一个候选 `TRA×TRB` 或 `TRG×TRD` 组合，包含 primary/alternative、两侧支持、严格集资格和配对证据类型。
3. `barcode_receptor_qc.tsv`：一行一个细胞键，包含各 locus 候选数、barcode 状态、主配对 ID、mixed/multichain/doublet/legacy flags。

每张表包含 `schema_version, run_id, threshold_profile/config_hash`。

## 实施位置

1. `06.split_and_represent.py`：FASTA 第一 token 使用 `representative_id`；map 保留 original header 和完整 provenance。
2. `07.work_igblastn.sh`：不再用 barcode 覆盖 `sequence_id`；通过 sidecar 回填 sample/barcode/UMI/代表序列质量；映射非唯一时失败。
3. `pipeline/models/preprocessing.py`：新模式以 explicit distinct UMI 聚合；旧模式明确标记 `LEGACY_ROW_COUNT`。
4. 新增 `08.pair_receptors.py`：完成候选聚合、排序、状态机和三张输出表。
5. `sequence_id_chain_summary.py`：保留为 legacy presence/count 工具，不作为配对结果来源。
6. `run_10x_pipeline.sh`：07 后增加可恢复的 stage 08，成功前不清理 provenance。

## 旧数据处理

- 有 representative map/FASTA/sample-local 07 输出：可完整或基本完整回连，优先重跑 07 后处理。
- 只有 sample-local TCR.tsv：仅在 `(sample, barcode, normalized sequence)` 唯一匹配时恢复 UMI provenance。
- 只有缺少 `sample_id` 的聚合 CSV：按来源文件隔离，标记 legacy；不得跨样本合并，也不进入严格功能集。

## 验证矩阵

至少覆盖：1A1B、2A1B、1A2B、1G1D、αβ/γδ 混合、orphan、同 V/J 不同 CDR3、同 CDR3 aa 不同 nt、相同序列跨多个 UMI、TIED_TOP、nonproductive、同 barcode 不同 sample、缺失 provenance。

关键不变量：不跨样本合并；不跨受体体系配对；UMI 支持守恒；所有候选可追溯；任何候选不被静默丢弃；同一输入重复运行结果及排序稳定。

## 依据

- 10x Cell Ranger V(D)J algorithm overview: https://www.10xgenomics.com/support/software/cell-ranger/latest/algorithms-overview/cr-5p-vdj-algorithm
- 10x V(D)J annotations: https://www.10xgenomics.com/support/cn/software/cell-ranger/9.0/analysis/cr-5p-outputs-annotations-vdj
- 10x V(D)J clonotyping: https://www.10xgenomics.com/support/software/cell-ranger/latest/resources/cr-5p-vdj-algorithm-clonotyping
- 10x TRG/D workflow: https://www.10xgenomics.com/support/software/cell-ranger/latest/analysis/running-pipelines/cr-5p-gamma-delta
- Pai & Satpathy, Nature Methods 2021: https://www.nature.com/articles/s41592-021-01201-8
- scRepertoire `combineTCR`: https://www.borch.dev/uploads/screpertoire/reference/combinetcr
