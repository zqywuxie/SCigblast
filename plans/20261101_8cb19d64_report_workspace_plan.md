# 后续分析与汇报图完善方案

日期：2026-11-01。状态：已完成本地实现与验证，尚未推送/部署。实现与验收记录见 `analysis/repertoire/README.md` 及 `outputs/report_workspace_v11_preview/`。线上仍有 3 个 ARCHIVING 任务，旧镜像未安装 matplotlib，切换需在归档空闲时重建新镜像。

## 1. 目标与范围

- 复用现有 Pig IgBLAST / IgBLAST base 后续分析，不另建报告系统，不调整主流程的科学分析、筛选规则及原始结果。
- 支持新任务完成后自动分析，也支持已有结果单独补分析。
- 四项核心交付：全部样本引物匹配与 isotype 总览；每样本 V/J/C 与参考零观察基因；每引物 V/J/C；每样本阶段数量变化。
- 用户已确认：每条引物扩展到 V/J/C，取代此前 primer 统计仅 C 区的限制。
- Web 和 PNG 使用同一统计协议；保留 CSV、Excel、分类 ZIP，只输出 PNG 图片，不输出 PDF。
- 不新增 SHM、多样性、目标基因符合率等本次未要求的分析。尚无引物—设计目标基因对照，不能从零观察基因反推负责引物或认定设计失败。

## 2. 已核实的资料与代码

资料：
- `E:/Desktop/H-K93-B_report.pdf`：5 页；第 3/4 页为 V/J 基因水平柱状图，第 5 页包含 Expression % 和 Unique CDR3 % 的 isotype 数据。
- 用户 PPT 快捷方式真实目标为 `E:/Desktop/project/SCigblast/outputs/pig_repertoire_all_eight/猪免疫组库_技术比较汇报.pptx`，当前 21 页；第 4 页为全部样本引物匹配、第 16–19 页为 isotype 总览与分指标展示。
- 上述材料作为展示参考；原报告的表达权重和去重算法尚未从源程序验证，不能承诺与重新统计逐值相同。

代码：
- `analysis/repertoire/aggregate.py` 已保留 V/J/C 原注释，但聚合循环与参考清单查询仅 C；现有 gene_coverage 有零观察行，当前基因图尚未用它补全基因轴。
- `pipeline_trace.py` 保存当次实际 BLAST 索引的基因、等位记录、摘要和来源，排除 1 nt 占位参考；当前精简注释没有 cdr3_aa / junction_aa。
- `analysis/repertoire/sources.py` 支持新 trace、merged + final、final-only；当前 trace 版本识别写死为 v1，升级时必须兼容。
- `render.py` 已有全部样本引物匹配图、单样本阶段图、C 柱状图与引物 C 构成图。保留其合理部分，不重写全部绘图。
- `web/templates/report_panel.html`、`web/static/reports.js`、`reports.css` 已具备筛选、分页、下载和追溯；当前全局筛选与重复图库混在一起，不适合四种不同分析粒度。
- `web/repertoire_analysis.py` 已具备权限、后分析队列、取消、版本目录与导出；summary 接口当前整包读取，扩充 V/J × 引物数据后需要有界读取。

## 3. 先固定统计口径

### 3.1 分析范围与身份

- 样本基因与 isotype 主视图默认最终保留结果。过滤前数据放高级明细，不和最终分布混算。
- 使用数据集、样本、来源分区、记录 ordinal 关联；不能仅凭显示样本名或 sequence_id 合并。
- 同一 query 的多链任务不能累加为样本输入总数。关联不唯一、多行注释、来源冲突保留状态，不猜测归属。
- 前置去引物与运行后补表区分：补表只重新识别/统计，不代表曾去引物比对。保留实际 mapping 引物归属和本次分析归属两个版本。
- 已去引物、原序列保留、历史未知状态仍在元数据和高级筛选中可追溯，不再新增被用户取消的去引物情况图。

### 3.2 全部样本引物匹配

- 保留每样本一根 100% 堆叠柱，唯一 / 多重 / 未匹配，分母是该样本可评估记录数。
- 未配置、缺序列、无法评估不冒充未匹配；图旁显示评估覆盖率和对应数量。分母为零显示无可评估数据。
- reads / queries / AIRR rows 不混为同一单位，单位不同分面展示；跨链按已有 source + ordinal 去重。
- N1 等替代引物配置作为同一样本不同条件，明确标注，不作为新样本或合并到原条件。

### 3.3 Isotype 总览

- 仅 IGH 展示 isotype；IGK/IGL/TCR 仍为各链 C 基因分布，不命名为 IGH isotype。
- 两个并排面板：记录丰度组成与 Unique CDR3 组成。前者参考 PPT Expression 面板，但当前 Pig/base 每条有效独立记录权重为 1，标题/口径注明 queries 或结果记录，不称 UMI/RNA 表达量。
- 丰度组成：有效单一 isotype 归属记录数 / 全部有效单一 isotype 归属记录数。缺 C 注释、无法归类、多候选跨类型单列数量与覆盖率，不隐去。
- Unique CDR3：每个 isotype 内氨基酸序列去重，分母为各 isotype 内去重数之和，即 isotype–CDR3 组合数，确保组成柱为 100%。跨 isotype 共享序列分别计入；全样本去重 CDR3 总数另列，不能拿它作为组成分母。
- 保存 cdr3_aa 和 junction_aa 两列。新指标优先采用 cdr3_aa；仅具 junction_aa 的来源单列为该序列口径，明确标签，不逐行回退混合、不与不同序列口径汇总为同一柱。现有 Treemap 的字段策略不在本次擅自修改。
- 缺失、含终止或非确定氨基酸的记录标为不可用于精确去重，不生成“未知克隆”；记录有效覆盖率。具体字段与有效性规则写入 manifest。
- 可审计的物种 C 基因→isotype 映射，不粗暴删除数字或猜测名字。默认主类 IgM/IgD/IgA/IgG/IgE，支持 IgG 亚类；未知名称保留原名供核对。
- 主类 Unique CDR3 必须重新按主类去重，不能相加亚类去重数；两种组成的类别排序、颜色跨样本固定。

### 3.4 样本及引物 V/J/C

- 基因级合并等位：同一基因多个等位候选归并为该基因；多个不同基因不强行分配到第一项。
- 主柱图为唯一基因归属记录比例，分母是当前样本/链/区或当前引物/链/区的唯一基因归属记录总数；同时给出记录数。未注释及多基因候选数量显示在图旁和表格中。
- 引物主统计只用唯一归属该引物的记录；多重引物不重复计入每个引物。候选关系留在追溯表。
- 参考基因全集来自当次该链该区实际参考快照，与观察记录外连接，保留零值。状态区分：唯一观察、仅歧义候选、零观察、参考清单不可用、观察到但不在清单。
- “零观察”要求唯一和歧义候选均为零；仅歧义候选不能写成完全未命中。
- 图中全部基因均可查看/导出，零值用 0 与浅灰标记，不画假小柱。基因多时完整分页，不以 Top N 隐藏零值。
- 参考列表与分母按物种、链、V/J/C 区隔离；多个来源使用不同参考版本时分组展示，不静默并成同一参考。
- 尚无原始设计靶基因表：提供“参考基因未观察列表”与可追溯引物的真实观察分布，不判断哪个引物应负责一个未观察基因。

### 3.5 数据变化

- 继续每样本一张竖向柱状图：原始、fastp、header 清理 reads；输入配对与合并序列；各链 mapping 输入与保留结果。
- reads、pairs、queries、AIRR rows 分区，不跨单位计算相邻损耗，不新增碱基数。
- 不因新增基因/isotype 统计改变原 stage_volume_summary 的数字与去重规则。

## 4. Web 布局与交互

复用同一报告组件；任务详情将“引物分析”改为“分析汇总”，保留“数据变化”快捷入口。独立 /reports 使用相同布局。旧链接仍跳到相应内容。

顶部仅放：分析记录、状态、生成/重试、下载。下面四个局部视图：

| 视图 | 默认内容 | 局部交互 |
|---|---|---|
| 总体概览 | 全样本引物匹配大图；isotype 两面板 | 样本组/条件、isotype 主类/亚类；不受单样本选择器暗中影响 |
| 样本分析 | 当前样本 reads 变化；V/J/C 基因图 | 样本、链、V/J/C、仅零观察切换；默认完整基因集合 |
| 引物分析 | 引物唯一匹配率列表；选中引物 V/J/C 三图 | 样本、链、可搜索完整引物名；零匹配引物仍可选并明确为空 |
| 数据下载 | 当前图、完整 PNG 包、CSV、Excel、口径及版本 | 下载当前筛选或全量，明确两者区别；高级明细与追溯折叠 |

- 不再同时绘制一套交互图并在下方重复排列同一套 PNG。每张图旁提供 PNG 下载，完整图库放下载视图。
- 样本切换时保留有效链/引物；失效选择自动回到明确有效项。显示当前范围并提供清除筛选。
- 选择后自动更新，取消反复“确认查看”；请求带取消/序号，防止慢响应覆盖新选择。
- 大屏 isotype 双列、V/J 双列 C 单列；长基因列表优先全宽，窄屏纵排。不用固定大高度撑出空白。
- 完整引物名称允许换行和搜索；原有自定义选择器、键盘操作和焦点状态继续复用。
- 图片每页最多 7 张，有明确总数与分页；长基因图本身按每页约 20–25 个基因拆分，完整 ZIP 包含所有页。
- 页面复用现有蓝灰主题与日夜变量；导出 PNG 固定白底，适合 PPT。唯一/多重/未匹配颜色固定；isotype 使用克制、跨图一致的配色。
- 空数据、缺 CDR3、缺参考清单、未配置引物分别显示可理解的状态，不统一写“暂无结果”，不显示伪 0。

## 5. 输出结构

新分析版本采用下列逻辑分类；旧版本不搬移、不覆盖，下载 API 按 exports/manifest 解析并兼容旧名称。

```text
analysis/reports/<分析版本>/
  01_overview/
    primer_matching_summary.csv
    isotype_summary.csv
    figures/primer_matching_overview.png
    figures/isotype_overview.png       # 丰度与 Unique CDR3 双面板；缺能力时注明
  02_sample_genes/
    sample_gene_usage.csv
    reference_gene_observation.csv
    figures/<sample>__<chain>__V__01.png
    figures/<sample>__<chain>__J__01.png
    figures/<sample>__<chain>__C__01.png
  03_primer_genes/
    primer_counts.csv
    primer_gene_usage.csv
    figures/<sample>__<chain>__<primer>__V__01.png
    figures/<sample>__<chain>__<primer>__J__01.png
    figures/<sample>__<chain>__<primer>__C__01.png
    figures/<sample>__<chain>__primer_C_composition.png
  04_data_changes/
    stage_volume_summary.csv
    stage_summary.csv
    figures/<sample>__stages.png
  05_details/
    matching_details.csv
    primer_preparation_summary.csv
    filter_summary.csv
  report.xlsx
  summary.json
  manifest.json
```

- 文件名经过安全处理，冲突时附短哈希；完整原名写在图标题、表格与 manifest，不简写。
- 默认生成总览、各样本图、全部有效引物 V/J/C 图及原有 C 构成图；无记录不生成无意义饼图/组成柱，状态仍入表。
- 数据量大时批量绘图在现有后分析队列执行，显示“已统计/已绘制 x/y”并可取消；允许只生成选中引物图片，不要求重新匹配或扫描 FASTQ。
- 顶层统计清单记录 count、denominator、unit、scope、计数规则、CDR3 字段、参考版本、图表筛选和排序；Web/PNG/CSV 共用同一份值。
- SQLite 追溯库留在私有分析目录，不因新增 ZIP 打包暴露未授权明细。

## 6. 实现路径

1. **数据协议与兼容**：在 `pipeline_trace.py` 增加 CDR3 字段与 schema；`sources.py` 同时识别新旧版本及逐项能力，不将“有 trace”当成“所有指标可用”。相关缓存/版本校验沿用 `pipeline_igblast_cache.py`，确认新增字段需要重新归档时不会错误复用旧证据。
2. **统计**：扩展 `aggregate.py` 的 V/J/C 与样本、引物粒度；新增独立 `isotype.py` 保存映射/计数规则。SQLite 持久化 CDR3 去重键，避免全量 Python set；批量插入与事务，单次流式处理 AIRR。
3. **历史补分析**：旧 trace 可从版本匹配的最终 AIRR 补读 CDR3，按唯一来源 ID 关联，仅补最终保留记录；ID 不唯一不猜。final-only 可计算已有字段；无输入时不能声称全部 reads primer 匹配率。无历史参考清单默认不补零；显式“与指定当前参考比较”是另一比较范围，不能冒充当时参考。
4. **导出与图**：更新 `run.py`、`__init__.py` 的 schema、目录、导出表和版本；`render.py` 增加总览、完整参考轴、V/J/C 分面及分页。保持原有 matching/stages 已验证计数。
5. **API 与性能**：`web/repertoire_analysis.py` 提供紧凑概览与按 sample/chain/primer/segment 有界取数；大 V/J×primer 明细从分析私有 SQLite 查询，不把全部明细堆入首次 summary 响应。支持按已统计数据排队重绘，不重复跑 matcher。
6. **页面**：修改 `report_panel.html`、`reports.html`、`job.html`、`reports.js`、`reports.css`；必要时调整 `job.js` 的标签/旧跳转兼容，不改任务列表或无关配置。
7. **集成**：更新 `analysis/repertoire/README.md`、`web/README.md`、`AGENT.md`；检查 Docker 打包白名单与 `web/check_bundle.py` 对新增模块的覆盖。

保留现有两个 pipeline 名额、IgBLAST 链并发上限和后分析门锁；本次不改资源调度语义。

## 7. 验收

- 临时验证放项目外独立目录，完成清理，不新增永久 test 代码和日志。
- 人工可核对小数据：V/J/C 同基因多等位、多基因候选、缺注释、参考零值、仅歧义命中、无参考、不同参考版本、零总量。
- 引物：唯一/多重/未匹配与未评估守恒；同源跨链不重复；多重匹配不能重复归入多个引物；完整引物名称。
- CDR3：同类型重复、跨类型共享、亚类合并后重新去重、缺字段、缺值、两种 AA 字段、非法/不确定 AA；组成比例与独立分母一致。
- 兼容：新 trace、旧 trace、merged+final、final-only；补分析不修改历史 AIRR、FASTQ 或参考库，缺证据不伪造。
- 同一计数集逐项核对 Web、CSV、Excel、PNG。数据变化与改动前一致；包含所有参考零值的图片与表格不能被 Top N 截断。
- 只读使用一份真实 Pig 已有结果生成独立后分析输出，与其 AIRR 直接聚合对照；PDF/PPT 用于版式参考，不作为未经验证的数值黄金标准。
- 检查实际大文件的耗时、峰值 RSS、SQLite 临时盘、首次接口响应；原始文件扫描次数不随引物数增长，单引物重绘不重跑整个分析。
- 浏览器覆盖日/夜、桌面/窄屏、长名字、快速切换、7 图分页、零记录、缺能力提示、PNG/ZIP 下载及用户隔离。
- 最终 PNG 渲染检查字体、全名可读性、标题居中、零值、图例一致、刻度和数值不重叠；仅交付 PNG。

## 8. 实施顺序

先统计口径与追溯兼容 → 样本/引物 V/J/C 和零观察 → isotype 总览 → PNG 分类输出 → Web 四视图 → 历史数据与视觉验收。

完成条件：四项需求均在 Web 和下载中可访问；任一图可定位统计表、分母与参考版本；旧结果的不可恢复部分有准确提示，不要求为展示已有可计算统计而重跑 mapping。
