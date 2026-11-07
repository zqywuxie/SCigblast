# 猪 J 辅助注释修复与 7 条 C 区完善审查

日期：2026-11-03。正式 IgBLAST 根目录：`/colddata/SCigblast/platform/SCigblastWeb/igblast`。

最新状态：J 注释修复和 7 条 IGH C 扩展参考均已在服务器正式安装。C 安装时间为 2026-11-03 04:09:56 UTC（北京时间 12:09:56）。正式库路径不变，无需修改运行参数。

## 已实施：J 辅助注释

已修复正式 `optional_file/pig.aux` 中 IGH 6、IGK 9、IGL 4 条参考的辅助注释，包含仍保留的同名 `2026pig` 历史别名，共修改 38 行。TRB、TRG 检查通过，保持原值；TRA/TRD 不属于这次修复范围。V/D/J/C 序列及索引没有改变。

依据是本次下载的 IMGT V-QUEST J 序列及标题中的 codon_start：转换为零基阅读框，按该阅读框定位 FWR4 的 WGXG/FGXG 锚点，CDR3 结束于锚点之前。保留官方 ORF 属性；修正阅读框不代表把 ORF 重新认定为功能性基因。TRG 两个非典型 ORF 锚点按官方注释核查。

| 链 | 阅读框不一致的参考 | CDR3 终点修正数量 |
|---|---|---:|
| IGH | IGHJ4*01、IGHJ5*02（ORF） | 6 |
| IGK | IGKJ2*01/*02、IGKJ3*01/*02 | 9 |
| IGL | IGLJ2*01/*02、IGLJ3*01 | 4 |

服务器备份与来源证据：`igblast/reference_builds/pig_J_C_review_20261103_71d3/`，其中 `pig.aux.backup` 是替换前原件。旧 SHA256：`ca4dd5df5d05b087404855632f3ef6fc8e3c32d268775a2fe8b346230bab937a`；新 SHA256：`e1ebc87b1cf05fd0f6bc08d4240a0e5c235bb463bf7058e041861d13449b894f`。

本地 `igblast/optional_file/pig.aux` 是历史副本，与服务器完整文件不同；只修复其中对应的 19 条 BCR 历史别名，保留其他条目。其原件、差异清单和校验值见 `outputs/pig_J_C_review_20261103_71d3/pig.aux.local.before` 与 `local_aux_sync.json`。

## 验证

修复前各抽取 2,000 条真实输入，在内存中替换辅助注释进行受控比较：

| 样本 | 原 productive | 修正后 productive |
|---|---:|---:|
| H-K93-B | 1,861（93.05%） | 1,869（93.45%） |
| K-K93-B | 471（23.55%） | 1,898（94.90%） |
| L-K93-B | 5（0.25%） | 1,887（94.35%） |

三组的 V/J/C call 均未改变。H 中 1,990 条 cdr3_aa 变化，说明旧的 CDR3/克隆统计需要复算。固定随机种子为 20261103；抽样对象是实际去引物后的 mapping 输入，不是最终已过滤 TSV。

安装后又独立以每样本 200 条随机输入比较备份与正式 aux：H productive 185→187，K 39→194，L 1→186，V/J/C call 均不变。此复核用于检查正式路径生效，不能代替全量重跑。详见 `installed_smoke_validation.json`。

现有报告和 05/06 结果没有自动改写。旧任务需从 05 重新计算，再运行 06 和报告；仅刷新 HTML 无法纠正历史结果。本地 pipeline 缓存依赖包含 aux 文件元数据，重新执行 05 时会识别这一依赖变化。

## C 区：7 条扩展参考已替换正式库

采用同一 accession 内、同一等位基因的官方外显子顺序进行本地拼接。先在 22 条已安装且有 IMGT 已拼接参考的序列上复现，22/22 逐碱基一致；再构建并安装以下扩展参考。它们应称为“依据官方外显子本地拼接的参考”，不能称为 IMGT 直接发布的完整拼接条目。

| 参考 | Accession | 原长度 | 更新后长度 | 官方支持范围和限制 |
|---|---|---:|---:|---|
| IGHD*04 | AY228505 | 318 | 878 | CH1+H1+H2+CH2+部分 CH3；仍缺 3′，不跨 accession 拼接其他 CH3 片段 |
| IGHG4*01 | EU372654 | 294 | 955 | CH1+H+CH2+部分 CH3-CHS；仍缺 3′ |
| IGHG4*02 | U03782 | 327 | 833 | 部分 CH1+H+CH2+CH3-CHS；仍缺 5′ |
| IGHG5-1*03 | EU372656 | 294 | 952 | CH1+H+CH2+部分 CH3-CHS；仍缺 3′ |
| IGHG5-2*01 | AB699686 | 294 | 969 | CH1+H+CH2+CH3-CHS 外显子组合完整；初始边界为 N |
| IGHG5-2*02 | EU372657 | 294 | 972 | CH1+H+CH2+CH3-CHS 外显子组合完整；初始边界为 N |
| IGHG6-2*03 | EU372653 | 294 | 960 | CH1+H+CH2+部分 CH3-CHS；仍缺 3′ |

除了 U03782 cDNA 片段，候选均保留初始未知边界 N。IMGT 单外显子导出为补齐密码子可能包含前置边界碱基；独立 C 参考缺少重排后 J-C 的完整上下文，按照已验证的官方拼接形式保留初始 N，不把推定边界碱基当成实测。其余各位置来自同 accession 的官方导出，缺失的末端不填补，不用其他等位基因补齐。

七条候选按对应官方阅读框翻译均无内部终止密码子；这不改变官方 partial 属性。IGHD*04 不连接 AY228506/507/508 的分散片段，以避免把缺失区误接成连续完整参考。

已采用这些经过验证的扩展参考替换原来的单外显子条目，并重建 IGH C 索引，保留 5 条 partial 标记和边界 N。其他 22 条 C 参考、全部 V/D/J 索引与修复后的 J aux 保持不变。扩大覆盖范围不能解决共有短片段的亚型歧义，也不保证所有旧 c_call 不变。

最初的候选 FASTA：`outputs/pig_J_C_review_20261103_71d3/IGHC.seven_expanded_candidates.fasta`，该目录保留审查时的历史状态。逐条来源、坐标、partial 状态、SHA256：同目录 `C_candidate_review.json`。以下安装记录代表最终生效状态。

### C 区安装与验证

- 正式目录：`/colddata/SCigblast/platform/SCigblastWeb/igblast/database_251117/pig_chain/IGH/`。
- 文件：`IGHC.fasta`、`pig_gl_IGH_C.*`、`pig_gl_IGH_C.provenance.json`。
- 备份：`/colddata/SCigblast/platform/SCigblastWeb/igblast/reference_builds/pig_C_expansion_20261103_b4c8/backup_IGH/`，包含完整旧 IGH 目录；同层保存当时的 J aux。
- 采用完整目录原子交换安装；替换前确认没有运行中的 igblastn，交换后重新回读并核对 V/D/J 不变。
- 容器内索引回读与 FASTA 全部 29 条逐碱基一致；总长度 29,841 nt；逐条 provenance 校验通过，partial 条目为 5 条。
- 新 FASTA SHA256：`da066ffd5eb6e0b43f27b4a143bd4b38598708286ba4a8aa5ad49638f794f5d8`。
- H、N2 各抽取 100 条真实 mapping 输入，使用相同修正后 J aux，对比扩展前后 C 库；V/D/J、productive、CDR3/junction 均不变，此次小样本中 c_call 也未变化。此检查不能证明全量数据的 C 命中率一定提升。
- 本地正式库快照：`outputs/pig_C_expansion_20261103_b4c8/installed_IGH/`。
- 安装及容器复核：同目录 `installation_audit.json`、`real_input_validation.json`、`container_validation.json`。容器检查使用 Conda 动态库目录 `/opt/conda/lib`。

## 官方来源及复现

- [NCBI IgBLAST 自定义数据库与辅助注释说明](https://ncbi.github.io/igblast/cook/How-to-set-up.html)
- [IMGT CDR3/FWR4 定义](https://www.imgt.org/IMGTScientificChart/Nomenclature/IMGT-FRCDRdefinition.html)
- [IMGT 猪 V-QUEST 参考目录](https://www.imgt.org/download/V-QUEST/IMGT_V-QUEST_reference_directory/Sus_scrofa/)
- [IMGT 猪 IGHC 官方外显子](https://www.imgt.org/genedb/GENElect?query=7.5+IGHC&species=Sus+scrofa)
- [IMGT 猪 IGHC 已拼接参考](https://www.imgt.org/genedb/GENElect?query=14.1+IGHC&species=Sus+scrofa)

源响应和下载 URL/SHA256 存于证据目录的 `official_sources.json`；J 新旧参数见 `J_repair_audit.json`。复现脚本为 `tools/reference/repair_pig_j_and_review_c.py`，`--apply-j` 仅安装 J 注释，C 只生成候选；本次 C 安装使用 `tools/reference/install_pig_c_expansion.py`。脚本要求新证据目录，保留备份，先验证再替换正式文件。
