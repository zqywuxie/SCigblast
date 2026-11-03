# IgBLAST 输出独立 preprocessing 与 UMI 统计计划

## 目标

为 10X_split 和 IR_split 的代表序列阶段之后增加一个独立后处理脚本，只负责：

1. 读取最终 IgBLAST AIRR TSV；
2. 按现有 `AIRR_filter` 规则筛选；
3. 保留 `sequence_id`（当前已经是 barcode）；
4. 对相同 `sample + barcode + sequence + locus` 的代表序列记录计数，写入 `umi_count`；
5. 输出样本级处理结果和统计 summary；同名样本不改名，在 Datapoint 中增加 `batch` 标注来源批次。

不调用 `main.py`，不执行 expression、diversity、CSR、SHM 或 junction peptide 分析。

## 现状与约束

- 10X `06.split_and_represent.py` 和 IR `06.representative.py` 正常情况下每个 `(sample, barcode, UMI)` 只产生一条代表 FASTA 记录。
- 两个 pipeline 的最终 IgBLAST 输出已经把 `sequence_id` 替换成 barcode，并保留 `sequence`。
- 因此最终表中同一 barcode 下相同 sequence 的代表记录数可以作为该 sequence 的 UMI 支持数；该字段应解释为“过滤后代表序列支持的 UMI 数”，不是 barcode 的全部 UMI 总数。
- representative public map 当前没有 `sequence` 字段，不能依赖它直接按 sequence 连接；本计划先使用最终代表记录计数，不修改代表序列阶段的公开 schema。
- 如果以后需要严格追踪每个 UMI，另行扩展 representative map 增加 `sequence` 和 `umi` 连接键。

## 建议文件

新增两个按分支阶段编号的入口（逻辑一致、入口各自维护）：

```text
IR_split/pipeline/08.preprocessing.py
10X_split/pipeline/09.preprocessing.py
```

保留并调整（供旧版 `main.py` 兼容）：

```text
pipeline/pipeline/models/preprocessing.py
```

`main.py` 保持原有完整分析入口，不再作为本功能入口。

两个新阶段脚本现在内置同一份筛选、分块和 SQLite 聚合实现，不再运行时导入
`pipeline/pipeline/models/preprocessing.py`；共享模块仅保留给旧版完整模型入口。

## 脚本配置

脚本顶部使用全局配置，不要求命令行参数：

```python
INPUT_ROOT = Path("...")       # 10X 或 IR 的 07.igblastn_out
OUTPUT_ROOT = Path("...")      # 与输入同级的 preprocessing_output
CHUNK_ROWS = 200_000
```

脚本递归扫描 `TCR.tsv`、`BCR.tsv` 和链级结果，跳过 `chain_summary.csv`、`igblast_summary.tsv`、`postprocess_summary.tsv`、`igblastn_run_summary.tsv` 等统计文件，并保持输入相对路径输出。

## 筛选规则

在 `AIRR_filter` 中统一实现并保留 `sequence_id`：

1. `locus` 非空；
2. `productive` 为 `T` 或 `TRUE`；
3. `v_score > 150`；
4. `v_identity > 85`；
5. `v_call` 或 `j_call` 至少一个有效；
6. 如果有效记录中 TCR locus 占比超过 90%，额外要求 `v_identity >= 99.7`。

所有数值字段先用 `pd.to_numeric(errors="coerce")` 转换，缺失字段或无法转换的记录进入错误 summary，不静默通过。

## UMI 计算

筛选后按以下键分组：

```text
sample_relative_path + sequence_id + sequence + locus
```

每组记录数写入：

```text
umi_count
```

然后每组保留一条代表 AIRR 记录，避免相同 sequence 重复出现在最终结果中。这里的 `umi_count` 依赖代表序列阶段“一 UMI 一条代表序列”的不变量；如果同一 UMI 被重复输出，单靠最终表无法识别，应在 representative 阶段 map 中保留 UMI 进行严格校验。

## 输出字段

最终输出保留原 AIRR 字段，并追加：

```text
umi_count
```

不删除或重命名：

```text
sequence_id  # barcode
sequence
locus
productive
v_call
j_call
```

每个输入文件生成一个对应结果文件；不跨样本、跨 pair 或跨 TCR/BCR 合并。

## Summary

输出 `preprocessing_summary.csv`，字段包括：

```text
source_file
batch
input_rows
filtered_rows
output_rows
unique_barcodes
unique_sequences
umi_count_sum
status
error
```

其中：

- `input_rows`：原始 IgBLAST 输出行数；
- `filtered_rows`：通过 AIRR 筛选的代表记录行数；
- `output_rows`：按 barcode + sequence + locus 聚合后的行数；
- `umi_count_sum`：输出中所有 `umi_count` 之和；
- `batch`：输入样本目录的直接上一级目录名；`sample` 保留叶目录名，不使用 `__dupNN` 伪后缀。
- `status=ERROR` 时保留对应输入，不影响其他文件继续处理，脚本结束返回非零。

## 内存与断点

- 使用两遍 pandas 分块读取，避免一次性加载全部样本；第一遍确定 TCR 阈值，第二遍写入临时 SQLite 聚合键；
- 以输入文件为并发单位，使用有界 `ProcessPoolExecutor`，不预先排队全部任务；默认 4 workers、每 worker 16 GB 预算、总预算 300 GB；
- 每次派发前和任务完成后检查服务器可用内存；低于 150 GB 时停止派发新任务，已运行任务完成，未启动文件写入 `SKIPPED_MEMORY` 并返回非零；
- 每个文件先写临时结果，再原子替换为正式结果；
- 记录输入文件大小和 mtime；输入未变化且输出、`.DONE.json` 标记完整时跳过；
- 失败文件保留错误信息，修复后可单独重跑；临时 SQLite 在每个文件完成后自动清理。

## 验证

1. 单个 barcode、三个不同 UMI、相同 sequence：输出 `umi_count=3`；
2. 一个 UMI 内多个候选序列：只使用代表序列；
3. 不同 sequence：分别计数；
4. `sequence_id` 保持 barcode，不为空；
5. 非 productive 或无有效 `v_call/j_call` 的记录不进入输出；
6. 10X/IR 各用一个小型 IgBLAST TSV 验证输出目录和 summary；
7. 重复执行时不重复追加结果。

代表序列阶段的额外进度输出：

- IR：UMI sidecar 建索引、FASTA 读取和每个样本完成时输出累计 reads/UMI/未匹配数；
- 10X：Phase 1 输出累计 reads，Phase 2 输出每个 batch 和 barcode TSV 的解析进度。

## 实施顺序

1. 改造 `preprocessing.py`：删除 `sequence_id` 删除逻辑，拆出可测试的筛选和计数函数；
2. 新增 `igblast_preprocess_only.py`，只依赖 preprocessing 模块；
3. 增加小型 fixture 测试筛选、聚合、UMI 计数和断点跳过；
4. 用 10X/IR 实际一个样本结果验证后，再接入各自 runner（如需要）。
