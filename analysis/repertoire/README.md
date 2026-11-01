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


引物配置的日常设置：上传 Submission 与引物表，确认样本列（默认 B）、Submission 引物组列（I）、引物表分组列（B）、引物名称/序列列对（C:D，可加 E:F），选择完整引物或仅 C primer。单工作表自动选择，多工作表需明确选择。正式运行前可启用“记录归属并去引物”，结束后补表只做统计。

高级设置保留表头行、多组分隔符、统计范围、统计/比对输入的序列结构、完整引物名关联以及 Adapter/UMI/Barcode。该批默认完整引物结构为指定 Adapter + 10N UMI + 8bp Barcode + C primer；若表内已经是 C primer，选择对应序列类型。表内的完整结构与实际比对输入的序列起点是两件事：合并序列已去 UMI/Barcode 时仍应从 C primer 起始，不再按结构长度剪切一次。

列映射预览：保留默认列，上传后自动显示各列的表头和示例值；修改列后展开“查看所选列内容”，显示前 5 条数据及原 Excel 行号，primer 名称与序列成对显示。支持 AA 等 Excel 列及非第一行表头；组名合并单元格按真实合并范围预览并标注，普通空白仍为空。工作表预览只返回表头附近最多 11 行，统计仍读取完整表格。


`stage_volume_summary.csv` 是面向数据量的主汇总，Excel 同名 Sheet 排在前面。包含阶段数量、单位、上一步数量、减少量、保留/合并率及配对覆盖范围。缺少任一所选配对的阶段计数时，该样本该阶段总量标未知；历史仅有最终 TSV 时只恢复实际结果行数。合并率分母是输入配对数，不将 reads 数转为序列数的单位变化当作损失。各链的比对输入与输出独立展示；AIRR 行数和输入序列数不直接计算保留率。Web 默认显示数据量明细，全部原始阶段指标和引物分类仍可在折叠明细或导出表查看。

2026-10-31 数据量图 v6：每个样本仍输出一张 PNG，reads、双端合并和分链 IgBLAST 输入/结果三个区域全部改为竖向柱状图。柱顶标注数量，质控保留率/减少量和合并率保留，统计口径与 CSV/XLSX 不变。已有报告重新生成后取得竖向样式。

2026-11-01 绘图 v15：新增 `04_data_changes/figures/all_samples__stages.png`，全部样本的数量与变化集中到一张矩阵图（超过12样本使用 `_01` 等分页后缀）。格内主值为实际数量，Fastp/Header 小字为相对前阶段的百分比变化，合并率分母为合并输入配对；IgBLAST 为结果行/输入序列数量比，不宣称 reads 保留率。各列按全样本最大数量着色，跨页共用比例；缺失显示“—”，未选链为空，零计数显示0。只有全样本 Fastp/Header 已知数量均相同才合并一列；当链输入不同于合并后数量时补充链输入列，不累加共享输入。原单样本竖向图与全部明细不变。Web 默认总览，可切换单样本。V/J/C 的样本和引物基因图分别使用蓝、绿、紫；零观察与歧义候选保持统一灰/赭色。
