# IR representative 旧结果分析入口与 Pipeline 集成计划

## 目标

1. 在 `IR_split/pipeline/analysis/` 新建一个可独立运行的分析入口，直接处理旧版 representative 模式产生的 `07.igblastn_out`，无需重跑 match、fastp、split、clean、PANDAseq、representative 或 IgBLAST。
2. 将同一分析能力接入 `IR_split/pipeline/run_ir_pipeline.sh`，作为 representative 模式的第 `08.analysis` 阶段。
3. 保持现有 expression、diversity、CSR、SHM 的计算定义不变；只修改输入发现、IR 的 `umi_counts` 来源、运行控制、断点续跑和输出组织。

## 核心数据契约

### 样本识别

- 从每个样本目录内的 `TCR.tsv`、`BCR.tsv` 发现样本；允许只有 TCR 或只有 BCR。
- 样本唯一键使用 `(input_root, relative_sample_path)`，禁止跨输入根或跨相对目录合并同名 sample ID。
- 公共结果中的 `sample` 使用叶级 sample ID，`batch` 使用 sample 目录的直接上一级目录；完整来源保存在 `processing_manifest.csv`。
- 忽略聚合文件 `all_samples_TCR.tsv`、`all_samples_BCR.tsv`，以及 `.batches`、临时目录和临时文件。

### IR representative 的 `umi_counts`

- IR 是 bulk 数据，barcode 不是细胞。统计范围是单个样本内部。
- 统计键保持为 `sequence + locus`；`umi_counts` 表示该序列/链对应的不同 UMI 数量。
- 优先使用 `06.representative` 的 state：用最终 IgBLAST 的 `sequence_id` 对应 representative state 的原始 `header/query_id`，再对每个 `sequence + locus` 统计 distinct UMI。
- query ID 只做安全归一化：移除开头的 `@`/`>`，取第一个空白前 token；不得删除可能有意义的 ID 片段。
- state 校验必须包括：同一 `sample_id + UMI` 仅有一个代表记录、query ID 仅映射一个 UMI、序列一致、样本覆盖率为 100%。不满足时该样本失败，不静默降级。
- 旧结果若没有 state，但已确认来自“一 UMI 一代表序列”的 representative 模式，可启用 row-contract 回退：先验证 `(sequence_id, locus)` 在样本内唯一，再以 distinct `sequence_id` 计算 `umi_counts`。发现重复时直接报错，防止重复计数。
- 输入中已有的 `umi_count` 或 `umi_counts` 必须丢弃并重新计算。

## 新目录与文件

在 `IR_split/pipeline/analysis/` 建立：

```text
analysis/
├── 00.analysis_config.env
├── main.py
├── run_analysis.sh
├── stop.sh
├── README.md
└── models/
    ├── __init__.py
    ├── preprocessing.py
    ├── expression.py
    ├── diversity.py
    ├── csr_calculate.py
    └── shm_calculate.py
```

实施原则：

- `main.py`、运行状态、内存门控、样本级 checkpoint 参考当前优化后的 `10X_split/pipeline/pipeline/main.py`。
- `expression.py`、`diversity.py`、`csr_calculate.py`、`shm_calculate.py` 复制并保持公式与字段计算不变。
- 只在 `models/preprocessing.py` 实现上述 IR representative 专用的 UMI 关联与计数。
- 所有环境变量使用 `SCIGBLAST_IR_ANALYSIS_*` 前缀，避免与 10X 或旧 preprocessing 入口混用。

## 独立运行配置

`00.analysis_config.env` 至少提供：

```bash
SCIGBLAST_IR_ANALYSIS_INPUTS=("/path/to/output/07.igblastn_out")
SCIGBLAST_IR_ANALYSIS_OUTPUT="/path/to/output/08.analysis"
SCIGBLAST_IR_ANALYSIS_REPRESENTATIVE_ROOT=""
SCIGBLAST_IR_ANALYSIS_UMI_SOURCE="auto"
SCIGBLAST_IR_ANALYSIS_ALLOW_ROW_CONTRACT="1"
SCIGBLAST_IR_ANALYSIS_FILTER_WORKERS="2"
SCIGBLAST_IR_ANALYSIS_SHM_WORKERS="32"
SCIGBLAST_IR_ANALYSIS_TOTAL_PROCESS_BUDGET="64"
SCIGBLAST_IR_ANALYSIS_SAMPLES_PER_WINDOW="1"
SCIGBLAST_IR_ANALYSIS_MEMORY_BUDGET_GB="300"
SCIGBLAST_IR_ANALYSIS_MIN_AVAILABLE_GB="150"
```

- `INPUTS` 支持一个或多个旧 `07.igblastn_out` 根目录。
- `REPRESENTATIVE_ROOT` 为空时，按标准同级结构自动寻找 `06.representative/<dataset>/.representative_state.tsv.gz`，同时兼容旧 `.csv` state；显式路径优先。
- `UMI_SOURCE=auto`：有 state 时使用 state；没有 state 且允许 row-contract 时采用经过严格验证的回退逻辑。
- 独立入口为：

```bash
cd IR_split/pipeline/analysis
bash run_analysis.sh
```

## 大 state 文件处理

- 禁止将完整 representative state 一次性读入 pandas/内存。
- 流式读取 state，并在 `08.analysis/.analysis_state/representative_index/` 建立带输入指纹的 SQLite 索引。
- 索引键至少包含 `(sample_id, query_id)`，保存 `umi` 与 `sequence`；建立必要索引以支持按样本查询。
- state 指纹应包含路径、大小、mtime 和内容摘要策略；每个样本单独记录 digest，使 state 变化只使相关样本 checkpoint 失效。
- manifest 增加：`umi_source`、`representative_state`、`state_total_rows`、`state_matched_rows`、`state_unmatched_rows`、`row_contract_valid`。

## 输出

默认输出到指定的 `08.analysis`：

```text
08.analysis/
├── Datapoint.csv
├── processing_manifest.csv
├── junction_peps/
└── .analysis_state/
```

- 公共汇总只保留完整的 `Datapoint.csv`，不再公开第二份精简/重复 Datapoint。
- `Datapoint.csv` 一行对应一个独立样本键，不把所有样本汇总为一行。
- `junction_peps/` 保持输入的相对样本目录结构，跨根目录同名时使用稳定 input label 消歧。
- `processing_manifest.csv` 保存 input root、relative sample、sample、batch、TCR/BCR 来源、UMI 模式、状态及错误。
- 输入 TCR/BCR 文件只读，不原地修改。

## 接入 IR pipeline

修改 `IR_split/pipeline/run_ir_pipeline.sh`：

1. 增加配置 `SCIGBLAST_IR_RUN_ANALYSIS=auto|0|1`：
   - `auto`：representative 模式执行第 08 阶段；merged 模式明确跳过。
   - `1`：强制要求 representative 模式，否则报错。
   - `0`：禁用分析。
2. 增加 `ANALYSIS_OUTPUT_DIR="${SCIGBLAST_IR_ANALYSIS_OUTPUT:-$(stage_root 08.analysis)}"`。
3. 在 stage script path/hash、pipeline hash、stage marker、最终输出验证中纳入 `08.analysis`。
4. 第 07 阶段成功后调用：

   ```bash
   SCIGBLAST_IR_ANALYSIS_INPUTS=("<当前数据集的 07.igblastn_out>")
   SCIGBLAST_IR_ANALYSIS_OUTPUT="<当前数据集的 08.analysis>"
   SCIGBLAST_IR_ANALYSIS_REPRESENTATIVE_ROOT="<当前数据集的 06.representative>"
   bash analysis/run_analysis.sh
   ```

   实现时不能直接通过普通环境变量传 Bash 数组；wrapper 应使用单数据集标量覆盖变量，或由 runner 生成安全的子进程配置。
5. `08.analysis` 的脚本 hash 必须覆盖 wrapper、main、config 及全部 models，不能只 hash `main.py`。
6. 只有 `Datapoint.csv`、manifest 和 `.analysis_state/run.json` 均为 DONE 时，才写 `.pipeline_stage_08.analysis.DONE`。
7. 第 07 阶段重新执行或输入摘要改变时，删除/失效第 08 阶段 marker；第 01–06 阶段不受影响。
8. representative 模式的最终 `.pipeline.DONE` 必须依赖第 07 与第 08 阶段；merged 模式仍以第 07 阶段为终点。
9. dataset manifest 增加 `analysis_dir`。

旧的已完成 pipeline 因 pipeline hash 更新会重新检查，但有效的 01–07 marker 应继续复用，只补跑第 08 阶段。

## 并发、内存与续跑

- 过滤阶段默认 2 workers；SHM 默认 32 workers；总进程预算 64；样本窗口默认 1。
- 总内存预算 300 GB，服务器 available memory 小于 150 GB 时停止派发新任务并写日志；已运行子任务按现有安全策略收尾或停止。
- 每个样本独立 checkpoint，指纹包含输入 TCR/BCR、state/row-contract 模式、模型代码和相关参数。
- 单样本失败不取消同阶段其他样本；阶段结束返回非零，pipeline 不启动后续/不写最终 DONE。
- 重跑只处理失败、缺失或指纹变化的样本。

## stop.sh

- `analysis/stop.sh` 只读取当前 analysis 输出目录 `.analysis_state/pids/` 中登记的 PID/PGID，先 TERM、超时后 KILL，不全服务器扫描。
- `IR_split/pipeline/stop.sh` 增加 `analysis/run_analysis.sh`、`analysis/main.py` 等标识；集成运行时优先终止 runner 的进程组及其子进程。
- PID 文件必须使用每个任务唯一临时名并原子写入，避免并发样本共享 `.tmp` 的竞态。

## 验证方案

1. 合成样本：同一 `sequence+locus` 来自 2 个 UMI，预期 `umi_counts=2`。
2. state 中同一 UMI 重复行，仍只计数一次。
3. 相同 sequence、不同 locus 分别计数。
4. 无 state 且 `(sequence_id,locus)` 唯一时，row-contract 回退成功。
5. 无 state 且 `(sequence_id,locus)` 重复时，样本明确失败。
6. 分别测试 TCR-only、BCR-only、TCR+BCR、空样本。
7. 多输入根及同名 sample ID 不合并，manifest 可追溯来源。
8. 二次运行全部 checkpoint skip；修改单个样本 TSV 或对应 state 后只重跑该样本。
9. 人为制造第 08 阶段失败，确认不写 stage marker 和 pipeline DONE，且保留中间结果。
10. 对旧的完整 IR 输出运行，确认 01–07 不重跑，只生成 08。
11. merged 模式在 `auto` 下跳过，在强制 `1` 下报清晰错误。
12. 验证独立与集成 `stop.sh` 均能快速终止子进程且不误杀其他 pipeline。
13. 执行 `python -m py_compile`、`bash -n`、小型端到端测试，再选取真实旧结果子集验证。
14. 用固定样本与原 models 做 golden comparison，确认 expression、diversity、CSR、SHM 数值完全一致，唯一预期差异是 IR `umi_counts` 的正确来源与新增运行元数据。

## 实施顺序

1. 创建独立 `analysis/` 目录并复制稳定模型。
2. 实现 IR preprocessing、state 流式索引及 row-contract 校验。
3. 实现独立配置、runner、checkpoint、manifest、stop。
4. 完成独立旧结果测试。
5. 将其接入 IR runner 第 08 阶段并更新 hash、marker、最终完成判定。
6. 完成 merged/representative、失败续跑、停止和真实数据子集回归测试。
