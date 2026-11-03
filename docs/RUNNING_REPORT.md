# 最新八样本报告

日期：2026-11-03。状态：**生成、校验及本地更新已完成**；未推送或部署。

- 新分析编号：`7fc2a61e`。
- 来源：服务器 `igblast_base/20261102_042057` 的 H/N1/N2/O1，以及 `igblast_base/pig/20261102_203159` 的 A/B/K/L。
- 工作目录：`/colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/analysis/.report_work/7fc2a61e`。
- 目标目录：同结果根的 `analysis/reports/7fc2a61e`。
- 隔离容器：`scigblast-report-7fc2a61e`，1012:1014，4 CPU；容器总内存 2→8 GiB，为文件缓存留空间，禁用容器 swap。进程树 RSS 监督仍为 2 GiB，计算 1、绘图最多 2。资源变化另有 `resource_change.json`，不可当作固定资源前后性能对比。
- 全局基因/CDR3/copy、引物统计、存疑组、指标与最终输出均已完成，8 样本、349 张图。
- 自定义启动器在绘图前保存私有 `statistics_checkpoint`，不把它当成功公共报告。
- 本地 `outputs/a93f6d1c/report.html` 已使用 `7fc2a61e` 新统计；目录名保留 a93f6d1c，不能再称为旧数据预览。另有 report.xlsx、公开 ZIP、数据/浏览器/性能验证回执。

上次 `5d48c90a` 在统计后绘制存疑组图时失败，未发布中间结果被原清理逻辑删除。已修复跳过存疑组未计算的空 Isotype 图；完整两样本 59 图、Excel、HTML 验证通过。

后续仅更新 HTML 使用 `python -m analysis.repertoire.export_html`，不重新计算 reads。不得删除活跃工作目录、运行锁、输入或参考。

完成验收：来源和公开产物版本、后处理 copy、236 个基因组成范围及 8 个 Isotype 范围的比例均通过；8 个样本的三项产出指标都有完成证据。完整运行约 5139 秒，进程树 RSS 峰值约 1.06 GiB。不能用此轮做固定资源前后性能对比。

从新公共材料单独生成本地 HTML 约 7.7 秒，20 份内嵌表与新 CSV 一致，未打开 SQLite 或扫描 reads。真实浏览器断网验证切换 Isotype 前后 scrollY=1773，PNG/CSV 导出、模块独立状态、分页、手机/夜间和键盘通过，零页面错误及外部请求。

随后按用户要求将“引物分析”移到“样本分析”之前（HTML v33），使用轻量入口约 7.4 秒刷新本地报告；20 份内嵌统计表保持相同，公开 ZIP 的 HTML 也已同步。未重新统计或修改服务器完成版本。

## N2 Isotype 诊断（2026-11-03）

N2 原始 IgA/IgG 引物组识别比例分别6.01%/20.27%，在Mapping前已有差异；去引物还造成明显C注释偏差。后处理750970 copy中491262（65.42%）无C注释，目前Isotype百分比只描述其余259708的可分类子集。

各取200条保留记录：IgA/IgG/IgM去引物后J尾部长度中位数15/20/3nt，C注释数0/182/0；同库、同IgBLAST 1.22.0参数的原序列对照为200/200/200。剪切组600条c_call与正式结果逐条一致，V/D/J/C指纹均匹配原任务。IGHA*01参考存在（1026nt），不是因库里没有IGHA。保留人工引物会影响C注释及IgG亚型，不能把诊断对照直接替换成正式生物学分类。

检查记录在`outputs/N2_isotype_check_20261103/`（Markdown、CSV、JSON），私有逐read对照留在服务器`.report_work/n2_isotype_check_20261103`；临时实验代码、FASTA、raw TSV和日志已清理。此次未修改pipeline或正式报告。

后续长度复核：本次N2输入R1/R2都是282nt，fastp前后长度未变，每端抽查1000条序列与质量均一致，Header清理亦一致；PANDAseq未设置primer/固定长度截断。05前primer准备明确剪切IgA21nt、IgD20nt、IgE24nt、IgG/IgM22nt，共748254条，62101条原样保留。详见同检查目录stage_lengths.json和Markdown追加章节。
