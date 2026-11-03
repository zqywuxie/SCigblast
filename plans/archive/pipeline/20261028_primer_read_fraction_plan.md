# 样本 C primer reads 占比统计方案

## 范围与已确认输入

- 首次分析 `ssh local` 上 `/colddata/SCigblast/data/SCigblastWeb/data/H-K93-B/H-K93-B_j.fq`（约 470 MiB）；后续相同程序支持其他样本，不修改现有 pipeline、FASTQ、数据库或其结果。
- Submission 工作表 `261022-ARP01`：B 列样本名 `H-K93-B`，D 列 barcode 编号 `BC_818`，I 列 `Who` 为 `IgH`。以样本名精确定位行，读取 D/I，禁止混用 `IgH_O1`、`IgH_N1`、`IgH_N2` 分组。
- 引物 xls 的 `Order` 工作表 A/B 为 barcode 编号/分组，C/D 和 E/F 均可能是 primer 名称/全序列。按真实合并区域读取，禁止跨组盲目填充；精确匹配编号和分组，校验同名冲突、缺失及重复。`reference/8bp_barcodes.csv` 中 `818,AGAGGCTC` 将 Submission 的编号与实际序列对应起来。
- `IgH` 组共有 6 条：`510HA`、`P_1-HD`、`P_2-HE`、`510HG`、`P_1-HMα`、`P_1-HMβ`。分别从全长 primer 验证并提取 `Partial Adapter A(33 nt) + NNNNNNNNNN(10 nt) + barcode(8 nt) + C primer` 中的 C primer，保留原名与完整来源。大小写统一，支持 W/Y/R/M 等 IUPAC 简并码；未知或不合规则记录中止并报告。
- 示例 FASTQ 序列已去掉 10 nt UMI 和 8 nt barcode，read header 的 `#` 后保留了 UMI；前三条 reads 的起始序列分别对应 `P_1-HMβ`、`P_1-HMα`、`P_2-HE`。只凭处理后的序列无法重新验证已去除的 barcode，映射只能根据表格及已有流程记录确认。

## 实施

1. 新增独立命令行工具（如 `tools/primer_read_fraction.py`）：输入 Submission、primer 表、barcode 表、样本名及对应 `_j.fq` / `_j.fq.gz`；先输出供复核的样本—barcode—分组—primer 映射。旧版 `.xls` 使用 `xlrd` 的 BIFF 合并区域读取支持，`.xlsx` 使用已有 `openpyxl`；在实际运行环境明确安装所需依赖，不转换或覆盖用户原表。Windows 和 Linux 服务器之间的输入材料按运行位置准备隔离副本，不迁移整个测序数据集。
2. 基于上述实测的 reads 方向，首版对每条 read 的 5′ 起点执行完整 C primer 精确 IUPAC 匹配；reads 中 `N` 不作为确定匹配证据。每条 read 仅归类为某 primer 唯一匹配、多 primer 匹配、未匹配之一。同序列不同名或序列前缀重叠也按多重匹配处理，不随意归到最长 primer；记录多重匹配组合以便复核。
3. 对已指定样本流式读取 FASTQ，支持普通及 gzip 格式，校验 header、`+` 行、四行完整和序列/质量长度。遇损坏记录明确报错，不静默排除。内存不随 reads 总数增长；不要自动同时扫描目录中的 `.fq` 与相同的 `.fq.gz` 造成重复。
4. 输出 primer 汇总 CSV/TSV，字段包括样本、primer 名、C primer、barcode 编号与序列、唯一匹配 reads、占全部有效 reads 的百分比。另输出总 reads、唯一匹配总数、多重匹配和未匹配数及百分比，核验 `总 reads = 各 primer 唯一匹配数之和 + 多重匹配 + 未匹配`。映射和统计配置一并记录；不输出逐条原始 reads 或默认为 UMI 去重。
5. 如果未匹配偏高，增加单独标记的诊断：读取方向、前缀偏移、截断、单错配；未经确认不将容错命中混入主统计。成功匹配率是本次规则下的 reads 分布，不等于 PCR 效率或参考库准确率。

## 验证与交付

- 对照原始表核验 H-K93-B 的六个候选、BC_818→AGAGGCTC、合并单元格和 C/D、E/F 列对；对未匹配分组或错 barcode 明确报错。
- 使用少量合成记录验证 IUPAC 正反例、read 内 N、不同名相同序列、前缀重叠、多重匹配、未匹配、截断和损坏 FASTQ；验证计数守恒及不改变输入文件。
- 执行真实样本全量扫描，检查前三条 read 的预期归类及总数守恒，报告处理耗时、各 primer 数量/百分比、未匹配和多重匹配比例；具体结果以运行时计算为准。
- 批量扩展时使用明确的样本与 FASTQ 路径映射逐个执行；文件名和数据来源不明时报告缺失，不猜测样本关联。首版不接入 Web、不变更现有 pipeline 流程和输出。
