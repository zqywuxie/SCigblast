# 2026-11-03 当前执行：全部样本产出、存疑引物组分析、家族分段

用户连续提出三项，均需完成，后来的家族UI要求没有取消前两项。

1. 样本产出默认全部样本整合：一个X轴按样本/链保留上下文，每个上下文三个柱，不跨链相加共享Merged或Unique CDR3。保留单链筛选和正式PNG。
2. N1等检测出存疑引物组时，为每个存疑组独立统计Raw/Mapping引物匹配、Mapping率、V/J/C Expression/Unique CDR3、参考匹配；沿用现有mapping，只重统计归属，不自动更改原组，不声称去引物后重比对。用户未要求重跑mapping。
3. 参考基因家族独立边框/间距/加粗标题，取代淡灰行分组。默认10基因分页不变，空状态v27不回退。

## 当前状态

- source `output_metrics.render`已新增全部样本图（每图最多12上下文，保留per-chain）。`chart_data.virtual_figures`为历史只读汇总补交互all-sample图；`report.js` outputs默认全部链，仅显示所选范围图；`charts.js.outputs`按样本信息顺序排列并带chain标签，窄屏图内滚动。
- `references`已改`.gene-family-block`独立卡片、总/本页计数和表格。JS语法通过，尚未浏览器检查或发布。
- 当前FIGURE v40-all-sample-outputs，HTML v28-all-sample-outputs，统计仍v27。尚未改统计计算，也尚未发布本次结果。
- 八样本稳定报告：服务器 `/colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/analysis/reports/2b0d74ec`，本地`outputs/2b0d74ec`。任务名ZNDX_YL。原报告HTML v27，278PNG，SQLite约14GB，所有原数据应只读使用。
- SSH remote_nanhua，容器scigblast-web，UID:GID1012:1014，宿主Python3，容器Python有依赖。直接`ssh ... docker exec`有时假输出off/exit；使用宿主Python subprocess调用docker。

## 存疑组设计

新增独立scenario表，不复制或改写原14GB索引。原分组及样本层面基因/Isotype/多样性/产出不变。仅引物相关模块提供“原组 / IgH（存疑）/ IgH_O1（存疑）”选择，N1当前两个存疑组均须输出。

数据可用性：

- 原details.sqlite有`observations`：原始mapping输入DNA、source/ordinal、mapping_sequence_id、sample_key/chain、原注释/筛选原因；`primer_mapping_links`：Raw/Mapping逐条mapped/retained/关联状态，映射判定与引物组无关可复用。
- Raw DNA不在raw_primer_reads，只存ID/原组类别。必须从已验证原R1或归档读取一次，同时评估所有存疑组。按sample_key/chain/stage/source/ordinal逐条对齐已有links，不用计数猜测。
- `composition_records`保存sample_record=1的AIRR后copy、有效CDR3、identity_key，`composition_calls`保存V/J/C。`identity(row)`为sequence+locus+V/J/C+CDR3aa的SHA。临时composition_lineage已删，不能直接凭原primer重分配。
- 对N1 final TSV只扫描一次，按实际mapping序列计算identity，并用新的scenario mapping匹配归属累计(identity,primer,n)。仅当sum(n)==postprocessed weight且同identity仅一条sample_record时，分配对应primer的精确copy；否则保留未归属诊断，不摊比例。
- 原SQLite全程只读。scenario私有SQLite保存每个场景Raw/Mapping匹配状态、映射状态及精确copy分配；不保存额外DNA副本。公开只导出汇总。
- 原`primer_group_comparisons`四个N1场景/阶段的denominator/unique/multiple/unmatched作为匹配守恒验证，不能只对未匹配子集重试。

预计独立模块`scenario_analysis.py`及公开table字段scenario_id/primer_group；public_metadata提供primer_scenarios和scenario引物catalog。新增匹配Mapping、gene_usage、reference清单，覆盖4个引物分类和primer参考范围。主样本/其他分析不混scenario。

backend可复用`render_usage`对scenario-only gene_usage视图出PNG；Excel新增简洁的存疑组匹配/基因组成/参考匹配Sheet。UI selector仅有候选时显示，换样本自动恢复原组，所有候选明确Suspected且注明沿用已有mapping。

## 运行/发布

先在独立隔离源码和工作目录生成，使用公共报告资源锁、内存预算2GiB。先完成all-output及familyUI独立验证，再完成scenario并一起发布新HTML/Excel/CSV/PNG/ZIP和数值验证。不得重跑原pipeline或破坏既有结果/原primer归属。单独新scenario索引可保留供追溯。

临时验证脚本仅放系统Temp，结束清理，不永久新增tests。更新AGENT和README。未获“推送/部署”请求，本轮只改源码并更新指定派生报告。

## 新增要求：样本选择与模块隔离

支持单选/多选，默认总览全部样本，样本产出全部样本；其他模块初次默认一个样本。多选真实过滤比较图数据，最多每页7个图；按模块分别保存样本、链、引物、stage、存疑组及分页，不跨模块同步。2026-11-03 实际存疑组统计已启动，隔离辅助SQLite，只读原始mapping与索引。

## 完成验证

- 全8样本产出图与单链图并存，未合并跨链计数。
- N1 IgH、IgH_O1独立scenario完成；每组copies=841515，assigned=771112，unresolved=70403。与原Raw/Mapping比较表逐类计数完全相符。
- 原公共CSV、原PNG及14GB索引未改，新增71PNG/4CSV/4Excel sheet，辅助SQLite私有。
- 全样本默认、单/多选真实过滤、各模块独立恢复、家族块、每页7图、窄屏夜间、PNG导出均通过浏览器验证。
- 真实报告与本地副本已同步。ZIP390文件，SHA256=4c45a4eb0daea7412c594120775ddf62cb2694cddecba2312d6b3ad45c1ddb08。源码未推送部署；冻结源归档，隔离目录清理。
