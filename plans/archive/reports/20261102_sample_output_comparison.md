# 样本产出比较：三柱分组图

状态：已实现并通过隔离验证，尚未推送/部署。不修改既有 pipeline 或原报告数值。

## 图形

只输出一张分组柱状图，X 轴为完整样本名，每个样本并排三根柱，Y 轴为数量。三项指标使用固定颜色，柱顶标具体值，样本顺序一致；不拆成三幅图、不使用堆叠、不归一化成百分比。

| 指标 | 口径 | 数据来源 |
|---|---|---|
| Total Reads | 双端合并后的 reads 数，每个成功合并配对计一条 | 当前样本 `stage_volumes` 的 `merged` 计数；对应 PANDAseq 汇总 |
| Total Receptor RNA | AIRR 后处理后 `umi_counts` 的总和（copy 按 `umi_counts` 记录） | 后处理表中 `umi_counts` 的和；优先读取已有 Datapoint 的 `<CHAIN>_umi_counts` |
| Total Unique CDR3s | 同一后处理范围内唯一 CDR3aa 的数量 | 后处理表 `cdr3_aa` 去重数量；优先读取已有 Datapoint 的 `<CHAIN>_uCDR3` |

`Total Reads` 已按用户最新说明改为 Merged，不使用 Raw R1 + R2，也不使用去引物后的 Mapping 输入量。后两项使用 AIRR 后处理范围，不能拿第五阶段 mapped/retained 行数替代。

## 入口和交互

在分析汇总中增加“样本产出比较”，与逐样本基因分析分开。比较页按链展示全部样本，多链时提供链选择；单链不显示冗余下拉框。其他页面保持默认一个样本。窄屏可在图内横向滚动。

页面只展示有关的样本/链选择和比较图；不附重复统计明细。工作簿增加简单核查 Sheet：样本、链、Total Reads、Total Receptor RNA、Total Unique CDR3s；同一数据生成 PNG 和独立 HTML。

## 现有代码核查

`IR_split/pipeline/models/preprocessing.py::AIRR_filter` 先筛选 locus、productive、V score/identity，再按序列支持次数产生兼容字段 `umi_counts`；`models/expression.py::calculate_expresion` 按 locus 汇总该字段，并对 CDR3aa 去重生成 `<CHAIN>_uCDR3`。Base 06.preprocessing 通过共享适配器复用这些逻辑。

因此 Base/Pig 当前 copy 是后处理序列的重复支持数口径，不能解释为经 UMI 校正的绝对 RNA 分子数；图仍可沿用用户指定的 Total Receptor RNA 标题，字段说明标注 copy。原有计算逻辑不重写。

仅统计本次选择的样本/链和对应后处理版本；跨批次或多份后处理结果不自动混加。未运行 AIRR 后处理、字段缺失或版本不能唯一确定时显示 N/A，不补 0、不从 Mapping 数量猜算。真实 0 保留为 0。

若需跨链样本总计，copy 可在确认分区互斥后合计；唯一 CDR3 必须回到后处理数据按约定的链/CDR3 键去重，不能直接将各基因或各 Isotype 的去重数相加。

本图与现有基因 `% Expression / % Unique CDR3` 的统计范围不同：基因组成基于 Mapping 范围，这里的后两项明确基于 AIRR 后处理范围。

实现：output_metrics.py 核对 Datapoint、processing_manifest、Base 完成回执的输出版本和所选输入链指纹，确认 DONE、单一批次、同一来源后再读现有指标；不重跑 Mapping。新 CSV 为 01_overview/sample_output_summary.csv，PNG 为同目录 figures/<CHAIN>__sample_output_comparison.png；工作簿 Sheet“样本产出比较”仅样本、链和三项指标。Web 与独立 HTML 提供比较页，Read counts 不重复附明细表。统计 v18、图 v26、HTML v5、Workbook v16。

验证覆盖完整执行、缺失/N/A、真实 0、非法数值、多批次、来源变化、导出列与下载名、按链选择、单样本默认保持、手机和断网 HTML。统计明细展示隐藏参考版本/参考口径、AA 字段、分类层级、单位；AA 字段仍在内部用于 Unique CDR3 筛选。引物分析显示名统一 Mapping reads，技术 stage key 不变；Total Reads 仍为 Merged。
