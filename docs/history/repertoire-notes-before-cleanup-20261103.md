> 2026-11-03 HTML v31：样本范围明确全部/单选/多选，搜索与勾选保留焦点和滚动；重置限定本模块，并统一默认值。导航吸顶、手机单行横滑、底部分页及返回筛选；图卡去重复图题，宽图显示溢出提示。放大查看复用现有节点与实例，关闭恢复焦点；主题/模块切换保留图例和图形视图。导出反馈区分生成中、下载已发起、失败与空数据。预览 `outputs/a93f6d1c/report.html` 的20份内嵌表与v30完全一致，未重跑pipeline、未同步服务器。

> 2026-11-03 HTML v30：样本产出支持三柱或单指标查看；样本基因支持可选同链对齐，按区核验参考 SHA/scope 后统一排序和轴域。PNG包含分析身份及指标，文件名反映当前筛选/比较集合；当前图CSV在离线HTML内直接导出，保留原分母，隐藏图例不重新归一化。Isotype丰度单位按来源区分reads/copy，Unique CDR3为distinct_cdr3；历史CSV在生成HTML时也修正单位，缓存统计导出同步归一单位。Total Reads为所选输入范围的Merged计数，共享输入的多链不得相加。预览 `outputs/72c4d9a1/report.html` 基于原8样本公共汇总，未重跑pipeline、未同步服务器。

> 2026-11-02 指标图迁移（统计 v26 / 图 v36 / HTML v20 / Workbook v23）：多样性新增六面板全样本横向比较，Simpson evenness另输出比较图；B cell Metrics新增Mean SHM与CDR3s observed as other isotype双热图，缺失为—，保留真实0，分页使用统一色阶。迁入旧 /colddata/zqy/10X_pig_analysis/src/report/single_chain.py 的 _isotype_bars 横图布局和蓝/橙配色；每个IGH样本按明确C基因展示含IgG亚型的Isotype，左Expression、右Unique CDR3，不改变既有主类合并概览。新isotype_distribution.csv及Excel“样本Isotype分布”与图共用06权重和基因内去重计数。平台指标页默认单样本，可切全部样本比较；HTML选全部样本仅显示比较图，所有图每页最多7张。新图绘制位于render_metrics.py，未重新运行06或修改其结果。模拟验证报告在outputs/metrics_isotype_preview_20261102/report.html，明确为合成数据；尚未推送部署。

> 2026-11-02 最新（统计 v25 / 图 v35）：Pig/Base 的样本及逐引物 V/J/C、Isotype 组成使用已发布且输入/输出回执校验通过的 06 junction_peps，Expression 累加 copy/umi_counts，V/J/C 分别以可明确分类的权重之和为分母。逐引物按 05 read ID 的归属及序列+注释精确核对 copy 份额，不能核对或未唯一归属的 copy 留在样本统计、不分摊。06 缺失/未发布不回退 Mapping 组成。Mapping 效率和匹配率仍逐 read，Unique CDR3 不按 copy 放大。其他流程维持原有代表序列口径。新增多样性与 B cell 指标页、PNG、CSV、Excel Sheet：多样性直接读取对应06 Datapoint；Mean SHM复用同种型SHM列还原copy加权平均，IgD/M合并；共享CDR3按组内去重氨基酸集合交集计算，不按copy加权。旧统计缓存须重新生成；不修改原AIRR或06产物，也未部署服务器。

> 最新报告布局（HTML v18）：导航、模块标题、筛选、按钮、状态和统计口径使用中文；仅图表标题及专业指标保留英文。Mapping效率按样本/链展示全部引物，不再选单个引物。基因匹配在V/J/C三栏内按名称家族分组，例如IGHG4、IGHG5-1/5-2归IGHG，与IGHM/IGHD分隔；每栏独立分页，默认10个基因，可输入1–100调整，组头不占名额。状态只展示已匹配/未匹配，历史不明确记录留底层。家族跨页时重复组头；未知名称不猜分组，不改变统计。支持完整基因清单搜索和状态筛选，Web查询与当前CSV一致。报告头部为固定8位随机编号，底部不展示技术版本行。真实H/N1和两份合成HTML已同步，服务代码未推送部署。

> 双阶段引物组对照（统计v23/图v33）：Raw和Mapping reads独立判断原组是否低于阈值，并按各自分母测试其他组。Raw只对低匹配样本流式重读R1，复用来源/版本检查，跨链共享输入不重复；不保存第二份Raw序列。缺失证据不借用其他阶段结果。CSV/Excel增加stage字段，两个阶段的PNG均将达到阈值且优于原组的其他组放在原柱旁，标Suspected；不修改原始primer归属或重新mapping。

> 最新组成分母（2026-11-02，统计 v22 / 图 v32 / HTML v13 / Workbook v20）：样本和逐引物 V/J/C 的 `% Expression` 分别使用该区可明确归属到基因的 reads；`% Unique CDR3` 使用这些基因内的有效 CDR3aa 去重计数之和。同一 CDR3aa 跨基因分别计入，无注释和未唯一归属不计入组成分母。Isotype 使用可分类 C 注释数据。分母大于0时完整组成合计100%，分页不重算分母；无数据为N/A。引物匹配率、Mapping rate、阶段数据量不改。Web、PNG、CSV、Excel、HTML同步；历史报告须重新统计，不能只替换口径文字。真实H/N1报告及两份合成示例已更新，原AIRR和计数不变。

> 最新读端展示：Raw 与 Clean(Fastp) 各作为一个阶段，在阶段内并排 R1、R2 实际计数，共用配色。两端独立取证，禁止按总量对半分；缺少为N/A，真实0保留。HTML左上角显示来源任务名。真实报告：`outputs/real_two_samples_20261102/report.html`（H、N1），合成示例不可当作真实结果。

> 最新基因匹配页：同时展示所用数据库中已匹配/未匹配的 V/J/C 基因，按颜色和文字区分，保留样本/链/引物范围。Web reference_only 过滤与分页/CSV一致；HTML使用样本和引物完整数据库清单，新增 03_primer_genes/primer_reference_gene_observation.csv。底部不重复统计表，统计值与原图不变。HTML v7 / 图 v28。

> 2026-11-02 最新约定（覆盖下文双候选和明细展示）：五流程 V/D/J/C 上限改为 1，IgBLAST 选择最高分命中；取消候选基因衍生分析，历史多 call 原文保留，不选首项。Raw 为原始 R1 的显示名；Mapping 效率仅展示 Mapping reads 阶段。所有模块删除底部统计明细，未匹配数据库基因改为单独名称清单，Excel/CSV继续核查。编号统计口径按模块区分并置于图表上方，定义见 report_rules.py。统计 v19 / 图 v27 / Workbook v17 / HTML v6；未部署。

> 2026-11-02 样本产出比较已实现（统计 v18 / 图 v26 / HTML v5 / Workbook v16）：按链比较所有样本，每样本三柱为 Total Reads（Merged）、Total Receptor RNA（AIRR 后处理总 copy）、Total Unique CDR3s（同一后处理范围唯一 CDR3aa）。仅采用已完成、来源/所选链及版本一致的 Datapoint；缺失为 N/A，不以 Mapping 计数替代。新增 01_overview/sample_output_summary.csv、同目录 figures/<CHAIN>__sample_output_comparison.png 及工作簿样本产出比较 Sheet。统计明细隐藏技术列，内部 AA 筛选保留；引物阶段显示名统一 Mapping reads。详见 plans/archive/reports/20261102_sample_output_comparison.md。

> 2026-11-02 最新规则（覆盖下文单候选约定）：五流程 V/D/J/C 的 `-num_alignments_*` 上限统一为 2。AIRR 仍为一条 query 一行，多候选保留在原始 call 中，不保证每条 read 都有两个候选。已有结果须重新 mapping 才能获得未曾输出的候选。新增 Web「候选基因」分页、Excel「候选基因核查」、`02_sample_genes/candidate_gene_summary.csv` 与按样本/区段的 candidates PNG；同基因不同等位基因和不同基因候选分别标注。多基因记录不计入唯一基因频率；IGH 跨类候选不计入单一 Isotype。同类不同亚类仅可汇总到共同主类，不能选取第一个亚类。原流程 C 区衍生指标也不再取多基因候选首项，原 AIRR 不改写。
>
> 绘图 v19：PNG 固定标题、图例和轴标签统一英文，保留实际样本、引物、基因 ID。匹配配置新增「低匹配率时测试其他引物组」，默认开启、唯一匹配率阈值 50%（0–100 可调）。原组严格低于阈值才测试；其他组唯一匹配率达到阈值且优于原组才并排标注 `Suspected`，多个合格组全部保留，不替换原组、不重新 mapping、不修改引物→基因归属。按原始归档 source+ordinal 跨链去重、同单位同分母比较；缺少原始序列或旧 catalog 未保存其他组时明确提示，不从去引物后的序列推断。新建任务和后续分析入口共享此配置；历史报告须重新生成统计，旧版仅重绘不升级统计。全部测试组写入 `01_overview/primer_group_comparisons.csv` 及 Excel「引物组对照」，候选组不增加实际样本数。首张匹配图 ID 保持 `primer_matching_total`，超过 12 柱分页并重复原组供对照。
>
> 配色v18：阶段蓝/青绿/紫/暖橙；匹配蓝/橙/灰；Isotype五主类固定色、IgG亚类蓝系深浅，C构成与Isotype同色。V红/J蓝/C紫保留。共享palette.py供PNG/Excel使用，Web同语义。每列数量深浅尺度与原统计不变，文字按底色适配；Workbook v11按分析类型区分表头、页签、数据条。

> Workbook v10：总览顶部展示实际样本信息（类别/完整样本名/链/引物组），取消总数卡；catalog组名缺失时不猜测。保留下方汇总图。引物计数的数据范围分统计输入、筛选后保留，后者是前者子集，计数不变。

> Workbook v9：总览数字行高38pt、20pt数字且左对齐垂直居中，所有行高在流式append之前设置，避免导出后数字裁切；数值、原生图表不变。

> Workbook v8：Excel不再包含技术_参考观察明细Sheet；后台参考观察CSV及索引不受影响，其他核查表保持。

> 最新规则：五流程V/D/J/C报告上限统一1，取消Excel歧义候选Sheet与Web候选筛选/计数，保留已匹配和未观察基因。旧多候选数据不强取首项、不改写；引物唯一/多重/未匹配仍照常统计。Workbook v7，以下候选展示说明为历史记录。

> Workbook v6：Isotype后基因表默认2–5个主要字段，唯一归属计数简写Reads，歧义计数为Candidate reads。链/基因区/核查结果/备注移到右侧默认隐藏，可取消隐藏；参考批次和必要提示仍可见，全部数值/行保留。样本分块与Isotype相邻排序继续生效。

> Workbook v5：Isotype表在各样本/参考上下文及主类/亚类内按Expression降序，每个Isotype的% Expression与% uCDR3相邻；AA字段、计数不变，不合并参考版本。歧义候选基因表专列无唯一命中但存在多基因候选的基因，候选计数不能相加当作reads数。

> Workbook v4：核查表按样本分块交替底色、加粗样本名，样本起始行加整行分隔线与适量行高；不插空行或合并，筛选/排序及全部数据保持。

> 当前 v17 / Workbook v3：全样本 Read counts 汇总保留，单样本阶段图已移除；标题、图例和色标居中。图表使用 % Expression / % uCDR3、Raw / Clean / Merged / Retained reads。原始 paired-end reads 与合并后的 reads 不直接等同，统计权重不变。Excel 新增总览原生图、数据条、交替底色和核查下拉，技术明细隐藏，字段保持精简。Isotype 主类合并亚类，Unique CDR3 合并后重新去重。以下较早版本中单样本阶段图的说明已失效。

> 当前v16约定：样本/逐引物V/J/C分布只画唯一归属数量大于0的基因。未观察参考基因和只有歧义候选单独记录，不占柱图；V红、J蓝、C紫。下方较早版本的零值柱图说明已被本约定替代。
>
> 主要核查交付为单个report.xlsx，分Sheet列出样本/引物已匹配、未观察、歧义候选及匹配率、Isotype、阶段数量等。中文目录支持跳转、筛选、冻结表头及核查备注；已知源Datapoint另作原流程样本指标Sheet，不混用过滤/权重分母。CSV技术导出继续保留。不存在当次参考清单时不推造未观察基因。

# Pipeline 后续分析与 PNG 汇报图

## v11：总体 / 样本 / 引物分析

绘图 v13 为基因使用图增加统一状态配色：已观察蓝色、零匹配灰色空心圆、仅歧义候选赭色菱形；基因名与数值同步着色。Web 和 PNG 一致，零值保持为零；构成图的基因/isotype 类别颜色保持原有含义。

绘图 v12 按用户要求移除图内分母、计算方法、参考库描述等页脚，保留标题、坐标轴、图例、数值及必要的实验条件/当前参考比较/分页标识，并收紧底部留白。统计口径继续记录在表格及本说明中，计数不变。绘图版本独立参与队列去重，旧 v11 统计仍可直接复用重绘。

用户已将原 C-only 范围扩展为 V/J/C，以下新版规则优先于文末旧版本记录。

- **总体概览**：全部样本引物匹配构成、IGH isotype 的记录丰度和 Unique CDR3 两面板，支持主类/亚类。
- **样本分析**：每样本阶段数量，以及按实际参考版本隔离的 V/J/C 使用图。完整参考基因进入图表，零观察与仅歧义候选分别标注；长列表每张 PNG 最多 25 个基因，完整分页导出。
- **引物分析**：完整引物名搜索、唯一匹配率、该引物 V/J/C 图及原有 C 构成图。多重引物匹配不重复归入每条引物。零匹配引物也保留，零分母显示不可计算。
- **数据下载**：单图 PNG、全 PNG ZIP、完整分析 ZIP、Excel 和 CSV。图片库每页最多 7 张。

计数口径：丰度每条有效最终 AIRR 记录权重为 1，并非 UMI/RNA 表达；V/J/C 图的分母为所选区唯一基因归属记录。等位候选先合并到基因，跨基因候选单列。Unique CDR3 文件级优先 `cdr3_aa`，仅具 `junction_aa` 的结果单列该字段，禁止逐行混合。仅标准氨基酸序列参与精确去重；各类型内去重数之和是组成分母，主类合并亚类后重新去重。缺字段、缺注释与不确定氨基酸的覆盖率单独保存。

当次参考来自 trace 内实际 BLAST 索引快照。历史无参考时只显示已观察注释；用户可在配置中显式选择“与当前 Pig/Human/Cat/Dog 参考比较”，读取服务器 `SCIGBLAST_REFERENCE_ROOT`，输出 `reference_scope=comparison_current`，不声称这是历史比对库。不同参考版本按 `group_id` 分组，不静默合并；缺少引物设计目标表时不反推引物失败。

`usage.py` 流式读取最终结果，SQLite 保存 CDR3 去重键与有索引的物化 gene_usage 表。统计耗时不随引物数量重复扫描输入。浏览器先读取小型 overview.json，逐样本/引物/区查询分页数据。v11 单引物重绘复用完成版本的 summary/details（校验文件版本），不再重新匹配引物或扫描 AIRR。原始统计与全部查询能力保留在新版本，不覆盖旧输出。

新版分类目录：`01_overview/`、`02_sample_genes/`、`03_primer_genes/`、`04_data_changes/`、`05_details/`。根目录保留 report.xlsx、summary.json、overview.json、manifest.json；details.sqlite 为私有索引，不通过普通下载暴露。新 exports 映射兼容旧 API 文件名称；旧报告仍按其原始分类下载。

trace v2 新增 cdr3_aa/junction_aa，读取兼容 v1/v2。旧结果直接补读最终 AIRR 即可生成可计算指标，不需要为此重跑 mapping。阶段数量算法、筛选与原 AIRR 结果不变。

支持 Pig IgBLAST / IgBLAST base 的已有结果与新任务。分析不启动 IgBLAST，不修改原 FASTQ/FASTA/链 TSV。

主 pipeline 的新顺序为：**PANDAseq 合并 → 在原合并序列上保存 primer 归属 → 去除确定的引物片段 → IgBLAST → 原链筛选 → 后续统计**。这项前置处理在运行前提供引物配置时启用；后续统计模块本身仍不重跑比对。

`pipeline_primer.py` 保存每条序列的全部 primer 名称、序列家族、剪切状态、剪切长度及前后长度，再生成独立的 mapping FASTA。可确定长度（包括多个候选但长度相同）时剪切；未匹配、不同长度多重匹配、结构不符、剪切后无剩余片段都**保留原序列继续比对**。所有输入记录和原 ID 保留。primer 的 C 区分布统计与 PNG 将已去引物和保留原序列分开展示；明细同时保存“mapping 前归属”与“本次统计归属”，后补其他引物表不会改写原始归属。

## Web 使用

1. 新建任务勾选“分析完成后生成统计与汇报图”（Base/Pig 默认开启）。可以提前上传 Submission 和引物表，也可在任务详情的“引物分析”中保存配置，或任务完成后补充。
   需要先去引物再比对时，请在创建任务或 Match 审核阶段保存配置，勾选“先保存 primer 归属…再做 IgBLAST”。正式分析启动时冻结表格版本、样本分配和剪切规则；运行中修改的表仅用于后续统计。没有引物表时仍可运行原流程并保留追溯，后补表不能追溯性改变已完成的比对；要比较去引物后的结果需另行重跑 mapping。
2. 新建任务默认保存比对输入、全 query 清单及过滤前的精简注释。选择“全流程”会额外归档原始 R1/R2 的完整序列及各阶段 ID 关系；不复制质量值，后续按原始读端匹配时可看到上游去向。这一档会增加 I/O 和磁盘占用。
3. 运行结束后，从“数据变化”“引物分析”查看图表，按样本、链、引物、统计范围筛选。PNG 每页最多 7 张，支持单图下载和图片/数据 ZIP。只导出 300 dpi PNG，不生成 PDF/SVG。
4. 已有任务可在详情页手动扫描并生成；首页“后续分析”可选择有权限的服务器结果目录。扫描后默认选择全部识别结果，可逐项取消。
5. 上传 XLS/XLSX，选择工作表、表头行、样本列、两张表的连接列、名称/序列列对。示例：Submission B 是样本，I 连接引物表 B，C/D 与 E/F 是名称/序列列对。只有真正的合并单元格会继承内容。公式只读取 Excel 已保存的缓存值，不执行公式；关联列无缓存值时需先在 Excel 中保存为值。
6. 本批表格中 I 列同时包含组名和完整引物名。界面用明确开关控制“组名未找到时按完整引物名精确关联”，提交前可查看实际样本分配。
7. 完整寡核苷酸模式按指定 Adapter + UMI 长度 + Barcode 长度提取匹配片段；已经提取的 primer 选择对应模式。Read 本身是否含这些结构单独指定，不默认假设不同来源具有相同窗口。
8. 选择具体 primer 后，可独立排队生成该引物的 C 区分布 PNG。记录追溯支持按基因、引物、匹配类别和过滤状态查询，下载所选记录 FASTA 和关联 TSV。

## 历史结果的范围

| 保存的材料 | 可以统计的内容 |
| --- | --- |
| 新追溯包 | 全部 query、过滤前 V/J/C、具体过滤原因、当次可读取的参考清单 |
| 新全流程包 | 再加原始读端 primer 及其 fastp/clean/merge 去向；碰撞与不唯一关联单列 |
| merged FASTA + 最终 TSV | 全部 query 的 primer 匹配；C 区统计仅来自能唯一关联的最终记录。最终缺失记录的原因未知 |
| 仅最终 TSV | 最终结果行的 C 区统计；序列列存在时可以统计该子集的 primer 匹配 |

没有当次参考清单时，不能用当前数据库补出当时的“零命中基因”。没有 raw 注释时，不能根据最终 TSV 缺行推断 no-hit。缺字段、未运行、未评估与真正的零值分开呈现。

## 统计口径

- 保留原匹配逻辑：5′ 起点、全长、IUPAC 精确匹配；read 中 N 不视为确定命中。不自动错配、偏移、反向互补。所有命中名称都保存，名称唯一与序列家族唯一分开统计。
- read ends、read pairs、merged queries、AIRR rows 分开。重复链比对不增加样本输入序列数；不同链任务的统计不相加当总 reads。
- 数据变化汇总、图表和导出不再统计碱基总数。逐记录的剪切长度与前后长度用于引物处理追溯，不参与总量统计。
- 有效 V 与 productive 是交叉指标；最终保留还要求指定 locus，不能用三个旧摘要列构造连续损失漏斗。
- 多基因候选单列，不取第一个基因；每条记录在频率构成中只属于一个类别。零观测参考基因表示本次未观察到，不能直接推导引物失败或预期目标。
- 原始 read 关联使用来源分区、规范化 ID 与基数校验，不按照 merge 输出行号猜测。无法唯一连接的记录不填造基因归属。
- 旧 Pig fastp 的 R1/R2 摘要重复使用 total_reads，新汇总直接读 JSON，不能把两列再相加。

## CLI

安装 Web requirements（以及运行环境中现有的 BioPython），在仓库根目录执行：

```bash
python -m analysis.repertoire.run --input /path/to/existing/result --output /path/to/new/report
python -m analysis.repertoire.run --request /path/to/request.json --output /path/to/new/report
```

配置示例（CLI 路径为当前执行账号可读文件；Web 用上传版本和权限校验，不接受任意工作簿路径）：

```json
{
  "input_root": "/path/to/result",
  "primer": {
    "submission_path": "/path/to/submission.xlsx",
    "primer_path": "/path/to/primers.xls",
    "columns": {
      "sample_column": "B",
      "submission_join_column": "I",
      "primer_join_column": "B",
      "primer_columns": [["C", "D"], ["E", "F"]],
      "allow_primer_name_join": true,
      "sequence_mode": "oligo",
      "adapter": "ACACTCTTTCCCTACACGACGCTCTTCCGATCT",
      "umi_length": 10,
      "barcode_length": 8,
      "observation_stage": "mapping",
      "read_structure": "window"
    }
  }
}
```

`observation_stage` 为 `mapping`、`retained`、`raw_R1`、`raw_R2`；后两者要求全流程归档。目录没有新索引时自动按真实材料降级，原始读端模式不会静默降级成最终结果。`sample_aliases` 可显式给出结果样本名→Submission 样本名对应，连接键默认仅去首尾空白。

输出按分析类别组织，同类 CSV 与 PNG 放在同一分类目录；`report.xlsx` 保留所有统计 Sheet，根目录另有 `sample_summary.csv`、`summary.json` 和来源/规则 `manifest.json`。`details.sqlite` 和 `.inputs/` 为私有追溯索引，不进入普通打包下载。Web 输出位于关联任务的 `analysis/reports/<版本>/`，独立目录分析使用服务器分配的输出根。

前置归属与剪切材料放在任务私有 `.analysis/primer_inputs/`；原始合并 FASTA 不覆盖。运行配置通过 `SCIGBLAST_PRIMER_MAPPING_CONFIG` + SHA256 的 `SCIGBLAST_PRIMER_MAPPING_HASH` 冻结，IgBLAST 缓存包含该配置与新输入。相同 pair 的链任务共享已验证的准备结果。原序列归档用于匹配回溯，AIRR 注释来自真实提交的剪切后（或按规则保留的原）序列；两者靠稳定记录和原 ID 关联，不能在剪切后的序列上重新猜 primer。

`primer_preparation_summary.csv` 汇总剪切/原样保留类别，`primer_gene_counts.csv` 含 `trim_state` 及同处理状态内的分母/频率，记录 TSV 含 `mapping_primers`、`trim_state`、`trim_length`。FASTA 追溯导出为本次观测的原始序列，便于回查 primer；实际比对序列与坐标以对应 AIRR 和准备清单为准。

## 部署与资源

需要重建镜像。新增 pip 依赖为 xlrd 和 matplotlib，系统字体为 fonts-noto-cjk；Conda 环境定义未改变。模块已加入 Docker COPY、dockerignore 白名单与 check_bundle。

Web 主 SQLite 只加 `report_runs`、`report_plans`、`report_scans`、`report_uploads` 小表，不存逐 read 明细。迁移由启动时 `init_schema` 自动建表；回退前停服并备份 Web DB，保留新表与报告目录可避免证据丢失。

后分析独占现有平台资源门锁，等待主分析，单次一个监督进程；保留全局 2 个 pipeline、每 pipeline 最多 4 个链任务的既有规则。配置 `SCIGBLAST_REPORT_MAX_RSS_MB`（默认 2048）和 `SCIGBLAST_REPORT_TIMEOUT_SECONDS`（默认 14400）控制后分析预算；Linux 监控整个进程组。停止与超限会核验进程身份，重试使用新输出目录。附属分析失败不改变主任务结果状态。

每链过滤前保存独立精简注释，pair 间可并行，各链共享同 pair 的只读 query 归档。trace 发布失败保留 raw AIRR 并写 `trace_complete=0`，不会把不完整追溯当成功。已有主分析缓存不会仅因补传引物表而失效；缺少追溯的旧结果按上表处理。

关联任务归档时，报告随结果归档；活动后分析会阻止来源重新执行、删除与归档。独立目录分析不自动改变原目录生命周期。

2026-10-31：引物与基因统计仅汇总 primer 对应的 C 区（c_call）分布，CSV/XLSX、Web 与 PNG 同步收敛到 C；缺少或歧义 C 注释仍单列。原始 AIRR 与私有追溯保留 V/D/J/C 字段，不改变 IgBLAST 比对和链筛选。报告版本已更新，旧报告需重新生成后获得 C-only 导出。


分类输出结构（v10；旧报告不自动搬移）：

```text
analysis/reports/<分析版本>/
├── report.xlsx
├── sample_summary.csv
├── 01_data_changes/
│   ├── stage_volume_summary.csv
│   ├── stage_summary.csv
│   └── figures/<样本标识>__stages.png
├── 02_primer_matching/
│   ├── matching_summary.csv
│   ├── matching_total_summary.csv
│   ├── primer_counts.csv
│   ├── sequence_family_matching.csv
│   └── figures/*.png
├── 03_primer_preparation/
│   └── primer_preparation_summary.csv
├── 04_primer_c_distribution/
│   ├── primer_gene_counts.csv
│   ├── gene_coverage.csv
│   └── figures/*.png
├── 05_filter_summary/
│   └── filter_summary.csv
├── summary.json
├── manifest.json
├── details.sqlite
└── .inputs/
```

引物匹配总统计以全部样本的横向对照为准：一张 100% 堆叠柱状图，每个样本一根竖柱，唯一、多重、未匹配三个组分，不把全部样本合并为一根柱或三个条形。不显示 X 轴“占本次观测记录”说明，样本名保留；每柱标注唯一匹配率，低于 0.1% 的非零结果保留四位小数。三类比例的分母为该样本已完成匹配判定的记录；序列缺失、结构不符、未配置及判定冲突归入未评估数量，不冒充未匹配。同一样本配对内，共享输入按归档来源及记录序号跨链去重；重复序列 ID 的不同记录仍分别计数。同名样本的不同计数单位分列，不混加。只有历史最终 TSV 时，无法核验跨链输入身份，保留逐链结果行口径并在图及页面说明。`matching_total_summary.csv` 与 Excel 同名 Sheet 按样本/单位各提供三类数量、分母和比例，`matching_summary.csv` 保留样本链明细。Web 总览显示本报告当前链范围的全部样本，样本筛选只影响其余图和明细。

不再生成去引物与原序列保留情况、参考 C 基因观察情况、注释与过滤去向三类 PNG；页面图库同步隐藏这三类旧图。相关明细 CSV 与追溯信息继续保留。

v10 将引物 × C 基因热图替换为 100% 堆叠竖柱图（kind=composition，文件名包含 _composition_）。每条引物一柱、按 C 基因固定配色，柱内标注至少 6% 的组分；完整名称换行显示。比例继续以该引物全部唯一 C 基因注释为分母，不混入未注释或多基因候选，不新增 Expression/Unique CDR3 指标。超出 Top N 的真实 C 基因合并为其他类，保持每柱总和 100%，不重新缩小分母。零匹配引物仍保留在排名表，缺少唯一 C 注释的引物不伪造构成。Web 图库支持新 composition 类型，并继续读取历史 heatmap 元数据。

v8 绘图统一低饱和蓝灰配色：主蓝用于结果/唯一匹配/已注释基因，灰色用于输入基线及未匹配/未注释，赭色仅用于多重匹配。排名和 C 分布采用统一 0–100% 横坐标、独立对齐的数量/比例列；引物全名按实测宽度保留，长名称仅换行。热图固定 0–100% 色阶并标注格内比例。数据变化仍为竖向柱状图，最多三链时三列，大于三链时底部链结果面板横跨两列。所有绘图设置在 rc_context 内，不污染其他图；继续仅导出 300 dpi PNG。统计字段、分母、Top N 和零值处理均不变。可通过 render(..., figure_audit=...) 在最终绘制后、PNG 发布前注入排版审阅；运行环境无需安装本地技能工具。

图片按实际可用数据生成。数据变化每个样本只有一张 `<样本标识>__stages.png`，图中依次展示原始/Fastp/Header 清理后的 reads 数及相邻变化、配对到合并序列的数量与合并率、分链比对输入与最终结果。上游按本次选择的样本配对去重汇总，链输入不相加。灰格“—”表示无可核验数量，0 表示实际计数为零；有效 V 与 productive 为交叉统计。Web 按所选样本展示对应汇总 PNG，默认选中一个样本；每张图包含该样本全部链，链筛选作用于统计明细。`summary.json.exports` 记录分类后的 CSV 路径，ZIP 保留目录结构，内部索引仍不打包。


引物配置的日常设置：先上传 Submission 与引物表，成功读取后从实际列的下拉选项中选择样本列、Submission 引物组列、引物表分组列，并成对选择引物名称列和序列列（可添加，最多 12 对）。选项显示 Excel 列字母和表头；不预设字段归属。再选择完整引物或仅 C primer。单工作表自动选择，多工作表需明确选择。正式运行前可启用“记录归属并去引物”，结束后补表只做统计。

高级设置保留表头行、多组分隔符、统计范围、统计/比对输入的序列结构、完整引物名关联以及 Adapter/UMI/Barcode。该批默认完整引物结构为指定 Adapter + 10N UMI + 8bp Barcode + C primer；若表内已经是 C primer，选择对应序列类型。表内的完整结构与实际比对输入的序列起点是两件事：合并序列已去 UMI/Barcode 时仍应从 C primer 起始，不再按结构长度剪切一次。

列映射预览：选择列后显示表头和示例值，并展开“核对列内容”，显示前 5 条数据及原 Excel 行号，primer 名称与序列成对显示。支持 AA 等 Excel 列及非第一行表头；更换文件、工作表或表头行后清空旧选择，重新读取后再选。组名合并单元格按真实合并范围预览并标注，普通空白仍为空。工作表预览只返回表头附近最多 11 行，统计仍读取完整表格。读取未完成、读取失败或必需列未选齐时不能提交。


`stage_volume_summary.csv` 是面向数据量的主汇总，Excel 同名 Sheet 排在前面。包含阶段数量、单位、上一步数量、减少量、保留/合并率及配对覆盖范围。缺少任一所选配对的阶段计数时，该样本该阶段总量标未知；历史仅有最终 TSV 时只恢复实际结果行数。合并率分母是输入配对数，不将 reads 数转为序列数的单位变化当作损失。各链的比对输入与输出独立展示；AIRR 行数和输入序列数不直接计算保留率。Web 默认显示数据量明细，全部原始阶段指标和引物分类仍可在折叠明细或导出表查看。

2026-10-31 数据量图 v6：每个样本仍输出一张 PNG，reads、双端合并和分链 IgBLAST 输入/结果三个区域全部改为竖向柱状图。柱顶标注数量，质控保留率/减少量和合并率保留，统计口径与 CSV/XLSX 不变。已有报告重新生成后取得竖向样式。

2026-11-01 绘图 v15：新增 `04_data_changes/figures/all_samples__stages.png`，全部样本的数量与变化集中到一张矩阵图（超过12样本使用 `_01` 等分页后缀）。格内主值为实际数量，Fastp/Header 小字为相对前阶段的百分比变化，合并率分母为合并输入配对；IgBLAST 为结果行/输入序列数量比，不宣称 reads 保留率。各列按全样本最大数量着色，跨页共用比例；缺失显示“—”，未选链为空，零计数显示0。只有全样本 Fastp/Header 已知数量均相同才合并一列；当链输入不同于合并后数量时补充链输入列，不累加共享输入。原单样本竖向图与全部明细不变。Web 默认总览，可切换单样本。V/J/C 的样本和引物基因图分别使用蓝、绿、紫；零观察与歧义候选保持统一灰/赭色。
## 2026-11-02：双阶段引物统计与 Mapped reads 分母

默认同时输出 **Raw R1**、**Mapping reads** 的匹配构成和逐引物匹配率，不再要求二选一。Raw R1 为一个读端，不使用 R1+R2 总量作分母。引物匹配规则仍为 5′ 锚定、完整 C-primer、IUPAC 精确匹配；多重匹配不分摊给各引物。

- Primer match (%) = 该引物唯一匹配 reads / 对应阶段全部 reads。
- Mapping rate (%) = 该引物对应成功 Mapping reads / 该引物唯一匹配 reads。原始 R1 通过当前配对分区内唯一 canonical ID 关联 query；其 Mapping rate 包含质控与合并过程的损失。
- 成功 Mapping = 有有效 V call 且 locus 与所选链一致。productive 是后续筛选条件；Retained reads 单独列出，主 pipeline TSV 和筛选规则未改。
- 样本与逐引物 V/J/C 的 % Expression = 明确归属该基因的 reads / 对应范围、对应基因区可明确分类的 reads。V/J/C 各自计算分母；无注释和未唯一归属不计入。Isotype 以可分类 C 注释 reads 为分母；% Unique CDR3 使用独立的去重 AA 组成分母，不能称为 reads 比例。

`05_details/primer_gene_counts.csv` 是包含失败 query 的观测诊断，仅保留计数，不再导出旧的按输入量归一化百分比；正式基因组成见样本与引物 `gene_usage` 表。

新 trace 以轻量 `read_sources` 文件指纹记录来源，不强制全流程序列归档。报告优先读取已有全流程归档；否则只读服务器允许输入根中的原始 R1。旧结果可从对应 Match 清单定位当前 R1，证据标记 `current_raw_source`，不能冒充当时冻结的原始快照。缺失文件、无 mapping 证据、重复 ID 或关联不唯一时，不用最终行数伪造 Mapping rate；无法确认的数量及原因另列。

输出新增：`01_overview/primer_matching_by_stage.csv`、两套匹配构成 PNG；`03_primer_genes/primer_mapping_summary.csv`、逐引物双阶段匹配率与 Mapping rate PNG；`report.xlsx` 中的“双阶段匹配总览”“引物Mapping率”。私有 `details.sqlite` 的 `raw_primer_reads`、`primer_mapping_links` 保存读端归属及 query 关联，原始/合并后归属独立，不用 raw 归属覆盖 mapping 前冻结的归属。跨链的样本总统计按来源与 ordinal 去重，缺少输入的样本在 availability 表中记录。

统计版本 v15 / 绘图 v21 / Workbook v14；旧报告不改写，需重新生成后续分析。仅有最终结果的历史文件明确标记 `available_result_reads`，无法恢复过滤前全部 mapped reads。

## 最新：分析汇总、独立 HTML 和基因 Unique CDR3

任务详情仅保留一个“分析汇总”入口：总体概览首先展示与 Excel 一致的样本信息表，其后为匹配和 Isotype 主类；“数据变化”在分析汇总内部切换。旧 `#stages-data` 入口仍可转到分析汇总。用户最新决定不再单列 Isotype 亚类，具体亚类直接在 C 基因组成中查看；主类数据在合并后重新去重。

样本及每引物 V/J/C 同时提供 **% Expression** 和 **% Unique CDR3**。前者以当前基因区可明确分类的 reads 为分母；后者在各明确基因内对有效、标准氨基酸的 CDR3aa 精确去重，同一 AA 跨不同基因分别计入。Unique CDR3 分母为这些明确基因的去重 AA 归属数之和，无注释和未唯一归属类别不计入；它不是跨所有基因的全局 distinct AA 数。仅有 junction_aa 时保留追溯字段，不输出 Unique Junction 指标。无可分类数据时显示 N/A，不伪造 0% 或 100%。参考零观察仍依据原 Mapping 注释，不能将缺少 CDR3 的基因误判为未匹配。

私有 SQLite 增加 `usage_aa_counts`、`usage_aa_other_counts` 和 `usage_aa_denominators`；`gene_usage` 用 `metric`、`aa_field` 区分两指标，样本和引物 CSV/Excel 同步，基因表显示 Metric / Count / Percentage。Web 可切指标，CSV/PNG 下载文件名包含指标，旧索引请求 Unique CDR3 返回409并要求重新生成后续分析；无需重新 Mapping。

新报告根目录增加 `report.html`，自动纳入完成 manifest 与 ZIP；分析顶部和数据下载页均提供 **HTML 分析报告**。所有 PNG base64 内嵌、公共统计表内嵌，离线支持样本/链/引物/基因指标筛选，图每页7张，表每页25条，单图仍可下载。图片按48KiB分块写入，不一次加载全套图片；只导出公开汇总字段，不包含逐read序列、私有路径或配置。字体、CSS、JS均不依赖外网。旧完成报告通过 `/api/reports/{id}/report.html` 以原统计只读导出，不改已有文件；缺失/已归档材料须恢复或重新生成。HTML文件名为`SCIGBLAST_report.html`，UTF-8 / `text/html`。

当前版本：统计 v16、绘图 v23、Workbook v15；HTML布局有独立版本。主 pipeline、原 AIRR 和筛选规则均保持原样。

最新显示约定：Read counts across samples 的 Clean reads 只标 **Fastp**，图中不单列 Header 规范化阶段；仍取 Fastp 实际计数，缺失不从 Header 推造，底层 clean 技术记录保留。Isotype PNG 下载使用独立操作行，单份图按钮不显示字段名；报告/引物配置的折叠项显式标记“展开查看／收起”和方向箭头，保留原生键盘操作。

引物分析现在分为四个用途明确的分类：**引物匹配**（样本匹配构成和各引物匹配率）、**Mapping 效率**（输入、Mapped、Mapping rate、Retained）、**逐引物基因组成**（所选引物 V/J/C）、**引物间 C 基因对比**（同一样本全部引物）。Raw R1 / Merged 选择仅在前两类出现，后两类沿用 Mapping 输入时的归属。样本基因与逐引物基因均默认左 `% Expression`、右 `% Unique CDR3`，Junction-only 右侧准确标为 `% Unique Junction`；窄屏上下排列。

HTML布局 v3 同步四分类，按完整数据上下文和页码配对图，缺少单侧明确提示，不能用另一指标填充。分页最多7张，不拆开一对图；“明细指标”筛选仅改变表格。分类图表与明细一一对应，每张图仍可独立下载PNG。统计和现有PNG/CSV/Excel不因重新排版而变化；绘图/报告缓存版本更新为 v24。
# 统一报告浏览与绘图调度（2026-11-02）

2026-11-03 HTML v28：总览默认全部样本；样本选择支持单选和复选。多选会实际过滤公共汇总数据，各主模块和二级分析分类分别保存样本、链、引物、阶段、存疑组及分页，切换模块不互相覆盖。恢复默认仅影响当前模块。比较图使用所选样本，逐样本图按每页最多7张展示。样本产出增加跨链的全部样本比较图，以样本/链为独立上下文，不相加跨链共用的Merged或Unique CDR3。

存疑引物组现在可选择独立分析。`scenario_details.sqlite`只保存替代引物匹配、原Mapping状态和准确copy分配，不改写原`details.sqlite`和AIRR结果，也不声称重新去引物比对。Raw按原始ID/ordinal核验，Mapping按已存追溯关系关联；仅当完整identity的最终记录数等于06 copy且对应唯一后处理记录时分配copy，其余不猜测分摊。独立输出`03_primer_genes/suspected/`的四份CSV、存疑组PNG及Excel的存疑组sheet；辅助SQLite不进入公开下载包。可同时查看其他样本原组和N1存疑组，但样本总统计始终沿用原分析结果。基因匹配列表使用带边框和间距的家族分块，默认每页10个基因，可调整。

HTML v27：筛选项按当前模块已有的样本/链数据收窄；失效选择自动调整，返回其他模块恢复可用范围。只有一个适用链时隐藏链下拉；比较图继承当前链范围，保留真实0计数和参考零观察。基因列表为空时仅显示居中状态，不显示空表头及分页；区分参考清单缺失和搜索/状态无结果，后者提供清除基因筛选操作。均为展示调整，不修改统计分母或源数据。

HTML v26：单样本B cell Isotype Distribution移到总体概览的Isotype区域，跟随样本选择展示Expression/Unique CDR3双图。全部样本视图仅保留主类比较，B cell指标页只保留Mean SHM与共享CDR3，定义随模块同步。只更改展示归类，CSV/PNG/Excel位置和数值不变。

统计口径只保留指标定义、计算公式、统计权重和分母；不出现旧报告迁移、图表布局或导出实现等叙述。多样性与B cell页分别显示本模块的定义。历史范围仍按原始metadata说明其实际分母，文案更新不改变数值。HTML v25。

2026-11-03 版式：样本信息和选择顺序按Heavy、Light、TCR排序，类别内按链及样本自然序；HTML类别之间有分隔线，Excel总览共用`sample_rows`。引物匹配图以引物组和样本名两层标签展示，同一样本的原组/存疑组归在一起，存疑组红框和星号只解释一次，完整信息保留在悬浮提示中。Raw/Mapping仍左右展示；窄屏在图内滚动，PNG导出保留完整画布。18张正式matching PNG采用一致结构，数值、分母及私有索引不变。版本HTML v24、绘图v39、Workbook v24。

2026-11-03：样本分析的二级Tab分为“基因组成”“多样性指标”“B cell 指标”。B cell包含Mean SHM、CDR3s observed as other isotype，以及已有B cell Isotype Distribution。只对IGH显示适用数据；旧报告没有相应汇总时明确提示补充生成，不能用空图表示0。主Tab仍在顶部。

共享CDR3统计仅使用有效标准氨基酸CDR3，与Unique CDR3口径一致；IgD/M内部共享不算“other isotype”。按CDR3分组聚合本类/其他类presence，避免逐CDR3相关查询。无效CDR3不参与去重或共享分子/分母，其copy和SHM信息保留。统计v27、绘图v38、HTMLv23已更新缓存版本。

真实H/N1预览已从原任务通过来源校验的已发布06补齐上述指标，无需重新Mapping；原报告的gene/primer统计不变。补充输出是独立版本，未覆盖历史文件或注册新的Web任务，验证见`outputs/interactive_report_20261102/real_bcell_validation.json`。

平台“分析报告”和独立“报告管理”只负责配置、生成、状态、历史、下载及记录追溯。统计图统一在`report.html`中查看；在线“打开报告”与下载后的离线文件使用相同CSS/JS和公共汇总数据，不依赖CDN。

新报告内嵌一次固定版本ECharts运行库及按模块分块的汇总数据；可点击图例、查看悬浮精确值、选择连续色条范围并导出当前视图PNG。图例显隐和范围选择不改变原计数与分母。两种组成指标成对、颜色及基因顺序一致。旧材料无法还原交互数据的图仍显示原PNG，不能从图片猜数。正式高分辨率PNG、Excel、CSV及私有SQLite继续独立输出；下载原图包与导出当前视图含义不同。

在线HTML响应通过owner/完成状态/manifest校验，并使用只允许脚本和下载的sandbox、禁止联网的CSP及独立标签页。离线报告不包含序列、query ID、服务器路径或引物序列。`chart_data.py`是公共字段白名单；`report_rules.py`仍是当前及历史统计口径来源。

PNG高开销部分按样本/参考组进行spawn并行：V/J/C及逐引物C组成最多2个绘图worker；概览、Isotype及其他指标图仍由协调进程生成。SQLite、Excel和manifest单进程写入，图组结果按稳定次序汇集，全部成功后原子发布。取消或worker失败会退出全部子进程；不改变全局2个pipeline及每pipeline4个IgBLAST链任务。

管理员可在`web/.env`设置以下环境变量，Compose已接入；部署重建/重启后生效：

| 变量 | 默认 | 含义 |
|---|---|---|
| `SCIGBLAST_REPORT_FIGURE_WORKERS` | `auto` | 待绘图组少于4个时串行；其余按预算最多2个。可设1或2强制上限。 |
| `SCIGBLAST_REPORT_FIGURE_WORKER_MB` | `640` | 每个绘图worker的准入内存估计，至少256 MiB。 |
| `SCIGBLAST_REPORT_MAX_RSS_MB` | `2048` | 报告整个进程组的RSS上限，继续由Web worker监控。 |

Web把实际可用总预算传为`SCIGBLAST_REPORT_MEMORY_BUDGET_MB`，协调进程为自身及系统预留内存后再确定worker数；不足时串行。当前报告重绘可复用来源报告的`.figure_cache.json`：按图组数据、版本、实现和绘图库版本生成key，校验PNG签名及SHA后复制到新staging。该缓存不进入下载包，也不跨用户共享，不就地改写旧报告。

运行库版本：ECharts6.0.0，构建工具esbuild0.25.12。构建方法见`tools/build_report_charts.cjs`；生产镜像只使用已提交的构建资源，不运行npm。图表资源及许可证由`check_bundle.py`核验，Docker白名单已包含`analysis/repertoire/assets/`。

验证产物见`outputs/interactive_report_20261102/`。真实H/N1预览沿用原统计；metrics/stress报告是合成数据。Windows/Chromium实测32样本模拟报告切换P95约28.5ms，30次切换GC后堆6.79→7.64MiB；这是本机基线，不代表服务器/所有浏览器。8组模拟数据121张PNG串行16.26s、双进程12.88s且逐字节一致；2组小任务双进程较慢，因此默认auto小任务串行。

HTML v29：总览与数据变化的单样本/汇总图互斥选择，避免切单样本后重复展示。逐引物基因组成只允许一个明确样本，不提供多选或全部样本；其他模块保留独立多选能力。仅变更显示筛选，不改变统计或原输出图。
# 2026-11-03：有界计算、缓存与 Isotype 快速查看

总体概览同时保留全样本比较和默认单样本 Isotype 卡片。卡片按钮只切换该卡，适用样本来自已有 Isotype 数据；上方快捷入口用于定位。切换样本保持阅读位置。单选总览不重复展示主类图，活动图仍不超过7个；相同图实例可复用，离线PNG/CSV照常导出。

报告生成在私有 `.performance.json`（Web为`report_work/<run_id>/performance.json`）记录各阶段耗时；`coordinator_cpu_seconds`只代表协调进程CPU，不代表子进程合计。Web `resource_usage.json`另记采样的进程树RSS峰值。新计时不进入实验人员的Excel/统计口径。

| 环境参数 | 默认值 | 作用 |
|---|---|---|
| `SCIGBLAST_REPORT_COMPUTE_WORKERS` | `1` | 归属统计按完整样本运行；支持`2`/`auto`，最多2进程，有内存/CPU配额约束。后续基因/CDR3汇总仍串行统一计算。 |
| `SCIGBLAST_REPORT_COMPUTE_WORKER_MB` | `640` | 计算子进程内存预估，MiB。整组仍受既有RSS监督。 |
| `SCIGBLAST_REPORT_STATISTICS_CACHE_MB` | `0` | 样本归属统计缓存限额，MiB；0禁用，缓存位于reports父目录的`.statistics_cache`，不是所有统计模块的通用缓存。 |
| `SCIGBLAST_REPORT_HTML_CACHE_MB` | `128` | 每报告旧版HTML衍生缓存限额，MiB，位于状态目录`report_html_cache/<owner>/<run>`。 |

计算worker与绘图worker分阶段运行，不嵌套扩大进程数。不同样本共享来源、身份不明确或无法安全分片时回退串行。两个进程不保证更快；小任务保留默认1，生产提升并发需基于固定资源实测。缓存核对来源、引物/结构规则、实现指纹和产物摘要；不影响原始AIRR、已发布pipeline结果或分类分母。私有匹配、lineage和缓存不加入公共ZIP。

旧报告已能用原有公共表生成新版HTML；现在可缓存该HTML，重复下载无需重算/重绘，也不改旧报告manifest。并发请求只生成一次；损坏缓存重建，超限文件服务后不保留。缓存读取、生成和流式响应持有报告读取租约，归档/删除事务会等待；归档、删除后清理相关HTML缓存。API查询摘要按用户归属、文件版本和统计scope缓存，最多256项。

来源数据改变时仍须重新统计，不能用HTML缓存冒充新结果。仅UI修改可刷新HTML；PNG绘图缓存另行校验。原有共享资源锁、最多两个pipeline及每pipeline最多四个IgBLAST链任务保持原配置。
