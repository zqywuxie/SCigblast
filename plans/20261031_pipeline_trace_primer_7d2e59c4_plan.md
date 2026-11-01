# Pipeline 数据变化汇总与引物—基因追溯实施方案

日期：2026-10-31。状态：首版代码已实施并完成本地验证；生产服务和生产数据未修改。

**实施更新（2026-10-31）**：用户最新调整优先于下文最初的“仅旁路统计”：运行前提供表时，在合并后、IgBLAST 前先持久化 primer 归属，再剪除确定长度的引物；未匹配、剪切长度不确定等记录保留原序列继续比对并单列，绝不据此丢弃输入记录。相同长度的多候选允许剪切，名称歧义仍保存。原合并 FASTA/ID 不改，实际比对输入为独立版本，缓存绑定引物配置。原序列、映射前归属与剪切后的 AIRR 注释关联，V/J/C 统计和 PNG 分剪切状态。正式分析启动后补表仅做统计，去引物对比需重跑 mapping。图片仅 PNG，不生成 PDF/SVG。

## 1. 已确认的范围

- 首版完成 **Pig IgBLAST / IgBLAST base**；IR、10X、reMapping 后续接入。
- 支持运行前或结束后导入 submission、引物表，自选工作表、表头行和连接列；一个样本允许多个引物。
- 首版统计实际 **V/J/C 分布、引物匹配、未注释及过滤原因**。用户尚不提供 primer→预期靶基因表，因此不输出“设计命中率”“引物失败结论”。
- 不改变现有 fastp/PANDAseq/IgBLAST 参数、生物学筛选规则、公开结果文件名称或序列内容；新增独立统计和追溯产物。
- 继续遵守全局最多 2 个 pipeline、每 pipeline 最多 4 个 IgBLAST 链任务及当前资源协调规则。

## 2. 代码与实际输入核查

### 2.1 工作簿实际含义

本次检查了用户提供的两个文件：

- `Data_submission_For_20261022_ARP01_P.xlsx`：工作表 `261022-ARP01`，8 个样本；B 列为样本 ID，I 列表头虽为 Who，内容实际是 IgH、IgH_N1、IgH_N2、IgH_O1 等引物分组。
- `C引物序列-pig(1).xls`：工作表 `Order`；B 列为分组，含合并单元格；C/D 与 E/F 为两组引物名称/序列列对。
- **I→B 是引物组连接，不是样本 ID 连接**。需另选 submission 样本列，再与任务中的稳定样本身份对应；目录输入模式同样可补导入此表。
- 当前旧分析口径为：完整引物去除已确认的 adapter + 10N UMI + 8 bp barcode，提取 C primer；对 `_j.fq(.gz)` 的 5′ 起点作全长 IUPAC 精确匹配，read N 不作为确定命中，不自动增加错配、偏移或反向互补。

### 2.2 已有实现与缺口

| 位置 | 已有能力 | 本次需要补足 |
|---|---|---|
| `Igblast_base/pipeline/02.run_fastp.sh` | fastp JSON、reads/bases/Q20/Q30/过滤统计 | 统一 ends/pairs 单位与来源证明 |
| `PigIgblast/pipeline/02.run_fastp.sh` | fastp JSON 和摘要 | 摘要把 total_reads 同时填入 R1/R2；新汇总必须直接核对 JSON/FASTQ，不能再相加 |
| 两分支 `03.clean_header*` | 规范 read header | 存 ID 转换关联；规范化可能导致单 pair 内碰撞 |
| 两分支 `04.work_pandaseq.sh` | input_pairs、merged_sequences、merge_percent | 对可关联 read 保存合并状态；不能虚构具体 merge 失败原因 |
| `pipeline_locus_split.py::filter_mapping_airr` | 流式按 valid V、productive、指定 locus 筛选 | 在丢行前记录全 query 精简注释和原因 |
| 两分支 `05.work_igblastn*` | 每 pair/chain 独立 mapping、缓存、最终 TSV | 过滤后删除 raw；需在删除前提交追溯旁表 |
| `pipeline_igblast_cache.py`、`pipeline_progress.py` | 缓存依赖、运行进度 | 计数凭据/trace 完整性不能用进度观测值替代 |
| `web/submission.py`、`web/app.py` | 上传、导入、不可变 revision、owner 验证 | 通用列映射、XLS 引物表、样本→引物一对多 |
| `web/postanalysis.py`、`web/derived_store.py` | Treemap 后置任务生命周期 | 复用权限/队列模式；引物分析独立业务模型 |

复用算法参考：`outputs/N1_all_primers_20261029/analyze.py`、`prepare.py`；旧 `primer_analysis_20261028/` 提供口径与结果凭据，不作为可直接部署模块。

## 3. 推荐架构：阶段账本 + 序列追溯 + 独立引物分析

### 3.1 阶段数据账本

新增共享 `pipeline_stage_metrics.py`，每个 pair/stage 写独立原子 receipt，再汇总到任务级 `analysis/qc/`。避免并行 worker 共同追加一个 CSV。

记录：schema_version、job/attempt、dataset/sample_key/pair_key、stage、输入/输出 artifact 及签名、计数值、单位、分母 ID、统计来源、状态、缓存来源、时间。

数据流按单位显示：

`输入 read pairs → fastp 保留 pairs → header 清理后 pairs → 合并 sequences → 每链 mapping queries → 最终保留 sequences`

- 按用户最新要求，汇总只保留 read ends、read pairs、queries/AIRR rows，不再统计碱基总数；不将 2 个 ends→1 条 merged sequence 画成损失一半。
- fastp 依据实际 JSON schema/tool version，并与输入/输出 FASTQ 流式计数校验；仅在两端记录数相等、成对配对已验证时计算 pairs。旧 Pig 摘要不能作为唯一依据。
- clean 理论上不改数量，仍以处理时观测计数核验；未采集的旧任务标“未核验”，不伪造 100%。
- PANDAseq 只在完整成功且 ID/数量契约验证通过时记 `input_pairs - merged_sequences` 为“未合并 pairs”；更细原因没有证据则不提供。
- mapping 单独展示：输入 queries、有效 V、有效 J、有效 C、productive、最终保留。前三者和 productive 是交叉指标，不能直接串成相减漏斗。
- 原始 query 全集与 AIRR 输出关联，区分未注释、缺失输出行、重复输出、失败/取消/未运行；缺少输出不当作 no-hit。
- 百分比均显示分母；0 分母显示不可计算。原始输入缺失的旧任务显示未知，不记 0。
- 同一 FASTA 被多链比对，输入样本总数只算一次；链结果按 task 分开，禁止相加伪造样本 reads 总量。

最终保留过滤的互斥原因优先级：`no_v_call → nonproductive_or_unknown → unknown_locus → different_locus → retained`。同时保存独立 flags，以便查看“V 缺失且非 productive”等交集；保留当前筛选逻辑不变。

### 3.2 无引物文件时也能建立的追溯层

新 Pig/base 任务默认建立 **mapping 追溯基础层**，不依赖任何引物表：

1. 合并后完整 query 序列 + query 全集；映射前固定顺序清单。
2. 每条链的精简 AIRR 旁表：query_uid、原 sequence_id、selected_chain、observed_locus、v/d/j/c_call 原文、productive/stop/frame flags、必要的 identity/support、过滤原因、缺失字段状态。
3. V/J/C 参考清单和版本快照，保存实际运行的条目 ID/allele/gene、locus、功能性/partial 信息（可得时）、数据库与 aux 的版本/校验凭据；C 未配置要单独记录。

索引组合：**BGZF 完整序列分片 + 每 sample/pair 的 SQLite 关联索引 + JSON manifest**。复用环境已有 BioPython BGZF。Web 主 SQLite 仅存任务元信息、状态和聚合，不放几百万 reads。每链 worker 先写自己的窄表分片，完成后由单写者归并 SQLite，避免四个 worker 抢同库写锁。

序列不只保存固定长度的头部：后补引物长度未知，固定 prefix 可能导致错误“未匹配”。prefix 可作为加速缓存，最终匹配始终可读取完整序列。BGZF 虚拟偏移必须与不可变分片 hash 绑定，重压缩后旧偏移失效。

身份设计：

- source_file_uid：输入版本快照，不仅是路径或 basename；sample_key 来自任务清单，不用展示名作为主键。
- pair_key：manifest 的稳定 pair 身份，区分同名 pair 和跨目录输入。
- read_uid：source_file_uid + mate + record ordinal；pair_uid 由已验证的配对关系产生。
- query_uid：merged artifact 版本 + record ordinal；原 header 作为属性保留。
- annotation 主键：query_uid + chain_task_id + reference_version + raw_row_ordinal，保留同一 query 的多行原始注释，不用覆盖写入掩盖重复；query 级汇总另行按明确规则去重。

raw→clean→merged 的桥接需要保留完整原 header、规范化 header、mate、来源域，逐步验证唯一性。不得只按 sequence_id 连接，不得通过 PANDAseq 输出行号猜测输入行号。规范化碰撞或非唯一关联标记 `ambiguous_lineage`；不改变公开 FASTQ/FASTA/TSV 的 ID，不把猜测关联交付给用户。

### 3.3 上游引物归因所需的额外保留

仅有 merged query 可支持“mapping 输入内的引物分布”，无法回答 fastp 丢弃和未合并 reads 来自什么引物。首版明确提供两档能力，并在新建任务中显示：

| 能力 | 保存内容 | 可解释范围 |
|---|---|---|
| mapping 基础追溯（默认） | 完整 merged queries、全 query 注释、关联索引 | mapping/最终筛选前后，可后补任意长度 primer |
| 全流程引物追溯（用户选择，当前需求推荐） | 再保存输入 R1/R2 完整序列（不复制 quality）、fastp 存活关系、clean 桥接和 merge 成败关系 | 包括上游损失，允许当时没有引物表、之后补导入 |

保留质量值只在另有诊断需求时增加；首版导出 FASTA 和关联表，不能在缺 quality 时伪造 FASTQ。全流程档若因空间等原因未完成，需显示真实能力降级，不得把未知上游结果填成 unmatched。

stage/source 必须明确：本次 `_j.fq` 为直接可匹配的 5′ C-primer 窗口；通用原始 R1/R2 不能盲目套用。用户指定观测端/阶段和结构模板，预览解析结果。默认不搜索任意位置、不自动 reverse-complement；不同处理阶段的匹配率分别列出，不能混用分母。

## 4. 引物表导入与匹配规则

新增 `analysis/primer/{catalog,matching,trace_join,aggregate,runner}.py` 及 `web/primer_analysis.py`；从旧脚本提取算法，移除硬编码私有文件路径。

导入向导：

1. 选择父任务/attempt、样本及可用追溯阶段。
2. 上传或复用 submission 与 primer revision；支持 XLSX/XLS，增加受限 XLS 解析依赖，保持大小、展开大小、单元格数量及 owner 检查；不执行公式、宏或外部链接。
3. submission 选择 sample 列和连接列，primer 选择连接列及一组或多组“名称/序列”列对；显示列字母、原表头和示例。
4. 合并单元格只在其实际合并范围内继承。普通空白不全表 forward-fill；如用户选择分组延续规则，明确范围并展示继承来源。
5. 默认仅去除键首尾空白，不擅自合并大小写/内部字符；多组拆分符需显式配置。重复样本/缺组/名字同名异序列等冲突列出并禁止静默选择。
6. 序列模式：已提取 C primer，或完整寡核苷酸 + 结构模板。本次模板 adapter+10N+8bp barcode；逐条验证后提取，不对所有表固定裁剪长度。Barcode 序列/编号保留为独立元数据，不混成 primer 特异性。
7. 显示“样本→组→全部引物名→用于匹配的序列”的预览，缺失/冲突可逐项修正或明确排除后提交不可变分析计划。

运行前导入：绑定版本，pipeline 后自动排队分析；运行后导入：只读取已发布追溯快照，不重跑主 pipeline。另一次更换引物或匹配规则产生新 run，不覆盖旧版本；无 submission 的目录任务也支持通过样本列关联。

匹配语义：

- 默认仍为 5′ 全长 IUPAC 精确匹配、read N 不确定即不匹配，无错配/偏移/反向互补。
- 不将简并引物展开成全部字符串；用编译模式/位掩码匹配，一次扫描序列获取完整命中集合。
- 同名同序列去重；同名异序列需明确版本/变体；不同名称同序列保留同一序列家族及多个名称，不能认定唯一 primer 名。
- 每 observation 在所选候选组内分为“唯一名称匹配/多名称匹配/未匹配”三个互斥类别，额外提供“唯一序列家族”口径。多名称命中总数可重叠，禁止相加冒充总 reads。
- R1/R2 各自是 observation；按 pair 统计时合并命中集合，不把一对两个端当两个分子。按 merged query 统计时为序列数，不称输入 read 数。
- 将原 N1 组与其他候选组重匹配作为不同 comparison/run，保存候选集与分母，不能悄悄改写原组。

## 5. primer→V/J/C 与反向追溯

分析以 observation→pair/query 的**已验证关系**为基础：

- 默认主视图：唯一 primer 名称匹配的记录；提供多重匹配独立分组、未匹配分组和全部记录筛选。
- 按样本、primer、链任务分别统计 V/J/C；基因层级合并 allele 时明确标注，并保留原始 allele 字段供下钻。
- 多个 AIRR gene calls 保留完整集合。默认“唯一基因注释”做频率主图，多基因候选/无注释分别计数；不得无提示取第一个或对每个候选都加一而使百分比超过 100%。
- 每幅图展示计数单位和分母（例如该 primer 的唯一归属且 V 唯一注释的 query）；另表给出占该 primer 全部可关联 query 的比例，避免隐藏未注释部分。
- raw/all-query 与最终保留结果可切换，过滤损失不会消失在最终图之外。
- 同一 query 的多个链任务分别展示，不跨链相加；任务总览按 query_uid 去重。跨任务候选一致性/多 locus 冲突单列，不用第一个成功链任意分配。

必须区分以下未检出情况：

| 情况 | 能报告什么 | 不能报告什么 |
|---|---|---|
| primer 已匹配，V/J/C 未注释 | 对应 primer 的未注释数量、序列及参考版本，可下载回查 | 未知序列“应该属于某基因” |
| V/J/C 已注释，primer 未匹配 | 基因分布和原始序列；提示匹配规则/候选集范围 | 猜测负责的 primer |
| 参考库有某基因但零命中 | 按实际使用的参考清单左连接显示 0，并区分无唯一命中/有歧义候选 | 直接判定某 primer 失效 |
| 基因不在当次参考库、链未运行、C 未配置 | 未评估/参考不可用 | 写成 0 命中 |
| 数据未通过 fastp/merge、ID 关联不唯一 | 丢失阶段或无法归因及原因 | 写成 mapping 未命中 |

本次是 C 引物，C-call 分布是重要的直接观察；V/J 分布可用于比较该引物捕获的 repertoire，不能仅凭 V/J 未检出就认定扩增性能不佳。真实生物组成、深度、参考完整性和技术处理都可能影响结果。

输出建议：

```text
analysis/qc/
  stage_summary.csv
  stage_summary.json
analysis/primer/<analysis_id>/
  sample_summary.csv
  primer_counts.csv
  primer_gene_counts.csv
  gene_coverage.csv
  unmatched_summary.csv
  report.xlsx
  manifest.json
```

内部索引放任务私有 `.analysis/trace/<attempt>/`，不作为普通文件列表直接暴露。下载未注释/多重匹配序列由筛选条件创建受控导出任务，提供 FASTA+关联 TSV+参数 manifest 的 ZIP；大明细用压缩 CSV/TSV，不强塞超过 Excel 行数上限的工作簿。

## 6. Web 展示与任务生命周期

### 6.1 运行后的可视化汇总与汇报图导出

用户补充确认：本功能不仅提供 Web 可视化，还需在 pipeline 结束后生成可直接用于汇报的独立图片。运行中采集证据，完成后汇总；引物分析在引物表可用且派生分析完成后生成。没有引物表不阻塞阶段汇总出图。

- 同一份版本化聚合数据驱动 Web 与静态绘图，不分别计算统计值；图内分母、单位、筛选范围与下载数据一致。
- 阶段数据变化：各样本阶段数量及保留率图、跨样本对比图。不同单位分区展示；mapping 交叉指标不用误导性漏斗连接。
- 引物匹配：唯一/多重/未匹配构成堆叠柱状图、各样本引物唯一匹配占比横向柱状图，保留完整 primer 名称。
- 引物与基因：按样本/引物/链生成 V/J/C 频率横向柱状图，生成 primer×gene 热图；无注释与过滤构成另图展示。大量基因支持 Top N+其他的汇报视图，完整数据仍可下载，并标明截断规则。
- 每张图仅导出 PNG（300 dpi），提供包含图片、绘图数据和参数 manifest 的 ZIP。按用户最新要求不输出 PDF/SVG；图片可直接插入 PPT。
- 默认采用白底、克制配色、统一字体及同类图一致的颜色映射；支持单样本图与跨样本比较图。跨样本同类指标尽可能统一坐标范围，标签过长或样本较多时自动换行/分页，不通过缩小字体硬塞。
- 图题使用用户熟悉的指标名称，正文不添加推测结论或来源脚注；完整来源、参考版本、计数口径和配置放在 manifest/随附说明。不可计算和未评估不得绘制成 0。
- 默认生成有限数量的概览图；按 primer 的细分图按需生成，并支持批量导出，避免所有样本×引物×链的笛卡尔积拖慢结束汇总。导出受现有资源门锁约束，可取消、可重试，失败不改变主 pipeline 成功状态。
- QC 图片位于 `analysis/qc/figures/`，引物图片位于 `analysis/primer/<analysis_id>/figures/`；统一使用安全且可追溯的样本/链/指标文件名，名称冲突通过稳定 ID 区分。导出绑定不可变分析版本，防止混用不同筛选结果。

### 6.2 Web 与生命周期

- 任务详情新增“数据变化”tab：样本筛选、单位明确的阶段数量/保留率图、各阶段损失/未知/未运行、下载明细。技术参数和来源签名折叠。
- 新增“引物分析”tab：导入配置/分配预览、分析运行列表、匹配构成、primer×V/J/C 热图/条形图、未检出基因表、未注释记录下钻。
- 支持从 primer 看基因，也支持从 gene/未注释状态反查 primer 分组和序列；每页有界加载，不一次返回全量序列。
- 运行前的引物配置作为可选区域；结束后入口复用同一向导。新建任务显示追溯档位与“是否支持未来上游归因”。
- 新增独立 `primer_runs` 等小元信息表，借用 Treemap 的 owner、scan token、immutable revision、取消/重试/去重、artifact 白名单模式；不把 primer run 塞入硬编码 Treemap payload。
- 父任务重新执行/删除/归档时，与 primer runs/索引的状态同事务协调；读取老 attempt 要显式选择并显示过期，不混合新旧结果。进行中的后分析阻止删除源文件。
- 后分析先采用现有资源门锁的保守互斥模式运行，单独有界 worker；等待锁时可取消。不会额外绕开两个 pipeline 的资源限制。不要把附属分析失败改写成主 pipeline 生物学分析失败。

## 7. 原子发布、缓存与历史任务

### 7.1 已有分析结果独立执行后续分析（首版必需）

用户确认：已有分析结果同样可以执行后续统计与汇报图生成，不只面向新建 pipeline。

- 提供两个入口：历史任务详情中的“后续分析”，以及独立入口选择服务器允许目录内的现有分析结果。目录扫描与下载沿用 owner/授权根目录限制，不因外部结果导入绕过权限。
- 先只读扫描 manifest、阶段报告/JSON、输入与 merged 序列、raw/final AIRR、参考版本证据，展示样本、链和能力清单；不根据相似目录名静默关联样本，不对缺少出处的结果套用当前数据库版本。
- 能力预览逐项列出：阶段数量汇总、阶段保留率、全部 query 的引物匹配、最终保留子集的引物与 V/J/C、过滤前注释、上游逐 read 归因。每项标记“可执行/仅部分数据/缺少文件”，用户可以直接执行可用部分。
- 最终 TSV 若含可靠的序列列，可直接做最终保留子集的引物匹配及 V/J/C 可视化；若没有序列但有唯一可关联的 merged FASTA，可补充连接；两者均没有时只能进行基因分布和已存聚合统计。匹配窗口与序列方向仍必须符合所选规则，不能保证任意最终序列都保留引物。
- 只有最终 TSV 时，未出现的记录标为“未见于最终结果，原因未知”；不能补算 raw mapping 未命中率、全部 reads 引物匹配率或 fastp 损失归因。若无当次参考清单，仅展示观察到的 gene calls，不能按当前库补出当时的零命中基因。
- 支持补传 submission/引物表、自选连接列后独立启动，生成同样的 PNG、汇总表与 ZIP。输出到受控派生分析目录，保存原结果路径/签名、统计范围、来源版本可用性；不改原 TSV，不覆盖旧报告。
- 老结果不具备新追溯索引时，按现有证据创建派生索引；不强制新 pipeline DONE/schema，也不自动重新比对。补齐数据后创建新分析版本，允许重新生成更完整的统计。
- 将历史结果验收作为首版交付项：分别验证“完整证据”“merged+final”“仅 final”“仅阶段摘要”四档，确认可用图正常生成、缺失能力明确提示，且没有偷偷启动主 pipeline。

### 7.2 发布与缓存规则

- 每 stage/pair/chain 先写 staging，核验条数、主键与签名，关闭 BGZF/SQLite 后发布 immutable manifest；跨文件提交以 manifest 为唯一完成凭据，不能只靠文件存在。
- 每条链 mapping 完成即保存其全 query 注释再删除 raw，不等待所有样本；filtered TSV 发布与 trace 发布均绑定当前 attempt。trace 写失败时保留可恢复 raw，显式标记 analysis_trace_incomplete；不得让后置功能误认已具备完整能力。
- 追溯增加单独 schema/cache fingerprint。已有核心 DONE 可表示原分析成功，但没有对应 trace 时只允许“核心结果可用，追溯待补建”；不因添加 primer 列配置而重跑 fastp/IgBLAST。
- 缓存必须验证 trace 清单完整性、原输入版本、reference 快照及参数。晚导入 primer 的 hash/匹配规则只影响 primer 分析缓存，不污染主 mapping 缓存。
- 历史能力分级：有可读取输入+raw AIRR可补建；只有 merged FASTA+最终 TSV可做有限 retained-only 统计；raw 已删时不能还原丢弃注释。需要独立诊断重比对时由用户明确启动，新目录保存，不覆盖原任务。
- 参考记录按每次运行冻结。原有 mtime/size 指纹不足以证明数据库内容，新增审计快照优先复用安装 provenance SHA256，缺少时一次计算并缓存，不为每个 chain worker 重扫整库。

## 8. 实施顺序与具体修改点

1. **计数契约与 QC 汇总**：新增 `pipeline_stage_metrics.py`；接入两个分支的 `02.run_fastp.sh`、`03.clean_header*`、`04.work_pandaseq.sh`、`05.work_igblastn*`、runner。修正新汇总对 Pig fastp total_reads 的解释。先交付数据变化 tab。
2. **mapping 基础追溯**：新增 `pipeline_trace.py`；扩展 `pipeline_locus_split.py::filter_mapping_airr` 可选旁路 sink；改两处 worker 的 raw 删除、DONE/cache 和发布；接入 query 全集、DB 快照、BGZF+SQLite。
3. **全流程关联**：输入 R1/R2 序列归档、fastp 存活、header 转换和 merge 关联；重名/collision 显式标记。先验证 `_j.fq` 和实际 Pig/base 输入结构，再开放相应模板。
4. **引物分析 CLI**：实现 XLS/XLSX catalog、配置预览、匹配集合、trace join、V/J/C/未注释聚合与结果导出；支持 before/after 使用同一配置模型。
5. **Web 集成**：`web/primer_analysis.py`、上传 revision 适配层、`web/app.py` 生命周期、`web/templates/index.html`/`job.html`、`web/static/primer-analysis.js`/`stage-metrics.js`；复用组件和安全路径机制。
6. **部署和说明**：更新 `web/requirements.txt`（XLS 解析）、Docker COPY/白名单、`web/check_bundle.py`、数据库迁移/回滚说明、`web/README.md` 与 `AGENT.md`。不得直接改线上数据库或运行中的任务。
7. 首版验收后再为 IR/10X 建立 read→UMI→representative 的多对一关系；reMapping 按输入记录与 copy 权重展示，不能冒充原始 read 数。

## 9. 验证与验收

遵守项目不保留测试代码的要求：验证脚本和模拟数据在独立临时目录运行，结束清理；保留验收结果与版本凭据。

- **工作簿**：用本次 XLS/XLSX 验证 I→B、B 样本列、C/D+E/F、合并单元格、多引物、空键、重复名称异序列、重复样本和无对应组；预览与真实分配一致。
- **旧统计回归**：原 N1 组：总 1,011,739、唯一 47、多重 68、未匹配 1,011,624；N1 独立 IgH 组：唯一 894,306、多重 0、未匹配 117,433。必须在同一 `_j.fq` 和候选集上复现，不能用经过 fastp 的 reads 冒充相同分母。
- **IUPAC**：W/Y/R 等、read N、短 read、前缀偏移、反向互补、同序列异名、多候选组、长于 prefix 的引物，确保互斥计数和名称/序列口径正确。
- **计数守恒**：独立小数据校验 PE ends/pairs、clean、merge、query 全集、每链原始注释与最终过滤；检查多链 input 不重复累加。
- **身份**：跨样本同 ID、同 pair headerclean 碰撞、乱序 merge 输出、重复 sequence_id、多 chain、重复文件，不误连接；无法唯一关联明确计入 unknown。
- **结果不变**：基线与新增旁路分别运行，比较旧公开 FASTQ/FASTA/最终链 TSV 的序列、字段、排序和计数；所有变化只能来自明确新增旁表。
- **统计边界**：多 gene call 不取首个；C DB 缺失、chain 未运行、无 AIRR 行、数据库里 0 命中分开；按 primer/基因下钻导出的序列与父摘要守恒。
- **恢复**：中断、磁盘满、索引损坏、旁表发布前失败、缓存命中缺 trace、源文件变更、改 primer revision 后重算；不混用 attempt，不删除未保全的 raw。
- **性能**：在 remote_nanhua 的独立只读输入/参考挂载容器中，分别测关闭追溯、mapping 档、全流程档的耗时、峰值 RSS、索引磁盘增量、随机取样/分页延迟；验证双任务、每任务最多四链时的 CPU/RSS/IO，不事先承诺零开销。批量事务、流式归并、有界队列，禁止全表读入 pandas 或单条 read 提交事务。
- **Web**：跨用户不可读取引物表、索引和导出；列预览可修正、取消/重试可恢复；大样本分页、空结果、部分能力、历史任务、父任务归档/删除均有明确状态。
- **汇报图**：复用聚合数据实现共享静态绘图模块并接入派生分析完成后的导出；逐项核对图值与 CSV/Web 一致。验证中文字体、完整 primer 名、长标签、多样本分页、PNG 分辨率；检查空值不画成零、同类图颜色一致，以及 ZIP 不混用分析版本。

## 10. 参考规范

- [AIRR Rearrangement Schema](https://docs.airr-community.org/en/stable/datarep/rearrangements.html)：sequence_id、V/D/J/C calls、duplicate_count/consensus_count 的语义与字段分工；不把 read/UMI/consensus 权重混为一类。
- [NCBI IgBLAST 建库与辅助注释说明](https://ncbi.github.io/igblast/cook/How-to-set-up.html)：J aux 与序列/名称不一致会影响 frame/CDR3；未注释可能涉及数据库/注释覆盖，不应仅归因引物。
- 匹配规则具体以本项目既有 `analyze.py` 的 IUPAC 集合和严格 5′ 逻辑作为回归基线。

## 11. 首版验收记录

- 已实现代码：`analysis/repertoire/`、`pipeline_stage_metrics.py`、`pipeline_trace.py`、`pipeline_primer.py`、`web/repertoire_analysis.py`，接入 Base/Pig 05、Web 生命周期、两个详情页签、独立工作台、前置表格配置、Docker 白名单与来源说明。
- 临时合成数据验证：原筛选链 TSV 逐字节一致；Base header 清理输出的解压 FASTQ 与原转换一致；旧 Pig fastp 重复 total_reads 不会相加；重复 ID/多 AIRR 行/缺失输出单列；原始读端可区分 fastp 后未出现、未合并、最终保留。
- 新前置处理：8 条合成序列覆盖唯一匹配、同长多重、异长多重、同序列异名、未匹配、read N、全为 primer、大小写；8 条全部保留，明确剪切后序列正确，四个准备 worker 共用同一结果。验证了“剪切前归属 → 独立比对输入 → 原始序列/新 AIRR 关联 → PNG”，并核验 SHA 配置与缓存。
- 实际工作簿验证：8 个样本成功关联，候选 primer 数 H=6、N1=5、N2=5、O1=5、K=2、L=1、A=1、B=1；明确启用完整名称回退，A 按 Pig_TRAC_1 连接。这只验证工作簿关联，不代表重跑了八个真实 FASTQ。
- 已验证 API：认证、上传、列分配预览、独立队列、PNG/XLSX/ZIP、选定 primer 出图、基因/primer 记录查询、FASTA/TSV、越界路径拒绝、跨用户拒绝、前置冻结及 Match-only 不改、活动派生任务阻止来源变更。
- 已验证浏览器：新建任务入口、任务详情页签、筛选清除、每页最多 7 张图片、390px 窄屏无横向溢出。打包检查覆盖五条原有流程，新增 Python/JS/Bash 语法检查通过。
- 图片仅 PNG（300 dpi）；未生成 PDF/SVG。验证脚本和模拟数据在独立临时目录执行，清理后不保留测试代码。
- 未进行生产部署或真实数据全量 IgBLAST/双任务性能验收；remote_nanhua SSH 后续连接被关闭。需重建镜像后在隔离数据上做 Linux 全量验收，不能把本地模拟验证当作真实比对质量结论。
