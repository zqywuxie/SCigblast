# 四套 pipeline 阶段化简与编号统一方案

## 目标

四套 pipeline 保持各自独立维护，不建立跨目录 `common` 运行时依赖。每个生物学阶段满足：

1. 一个编号入口脚本；
2. 一个同编号输出目录；
3. 一个阶段 summary；
4. 一套可验证的样本级/阶段级完成标识；
5. 可单独执行，也可由 `run_*.sh` 连贯执行，行为一致。

控制文件不算处理阶段，只保留 `00.pipeline_config.env`、`run_*.sh`、`stop.sh`。

## 当前审计结论

### 应立即修复的问题

1. Pig `pipeline_config.py` 可能丢弃 runner 传入的 `OUTPUT_ROOT`、`RAW_INPUT_DIR`，多数据集子任务可能写回默认目录。
2. Pig `03.clean_header.py` 没有过滤 manifest 中 `status != OK` 的记录，ERROR 样本仍可能进入 clean。
3. Base 的 01–05 都是调用 IR 的薄 wrapper，不是真正独立；Base 的 `PANDASEQ_MAX_PARALLEL_SAMPLES` 与 IR 实际读取的变量名也不一致。
4. Base 的 DONE 只判断 marker 文件存在；IR/10X 的部分检查只要求输出目录中存在任意一个文件。缺样本时可能错误跳过整个阶段。
5. IR/10X `07.work_igblastn.sh` 在 `exit 0` 后各自保留约 100 行以上不可达旧代码，包含旧 raw 发布与旧 summary，容易误导维护。
6. IR/10X IgBLAST 执行 summary 把 AIRR header 也计入原始行数；`mapped` 的含义与 postprocess summary 不一致。
7. IgBLAST postprocess 原地覆盖新文件但不完整清除旧样本结果；chain/config 改变后，旧 `TCR.tsv`/`BCR.tsv` 或 `.NO_RESULTS` 可能残留。
8. `map` 中同一 representative ID 对应不同 barcode 时没有冲突校验，当前逻辑会以后值静默覆盖。
9. `10X_split/pipeline/08.plot_barcode_rank.py` 不由 runner 调用，却使用阶段编号，是孤立脚本。
10. `stop.sh` 仍部分依赖命令行字符串匹配，可能漏掉 wrapper、Python worker 或外部工具子进程。
11. `python` 与 `python3` 混用；直接逐步执行与一键 runner 的默认输入模式/输出目录也有差异，尤其 IR IgBLAST 默认仍偏向 pandaseq，而 runner 使用 representative。
12. 多 pipeline 可在同一时刻通过内存预检；实时 watchdog 必须保留，同时每个 worker 仍需单进程内存上限。

### 可合并或清理的模块

- `IR_split/pipeline/summarize_fastp_json.py` 与 10X 同名文件完全相同：合入各自 fastp 阶段。
- IR/10X `igblast_postprocess.py`：合入各自 IgBLAST 阶段。
- IR/10X/Pig `pipeline_config.py`：删除；每个 Python 阶段在自身文件内用短小、相同的 env loader，使逐步执行仍能读取 `00.pipeline_config.env`。
- `IR_split/pipeline/pipeline_config.env.real.bak`：移出 pipeline 或删除。
- 各 pipeline 的 `__pycache__`：清理并加入忽略规则。
- 10X barcode-rank 绘图：移到 `10X_split/tools/plot_barcode_rank.py`，取消阶段编号；只有未来纳入 runner 时才恢复为正式阶段。

### 不能合并的真实阶段

- IR `03.ir_split` 与 `06.representative`：前者处理 FASTQ 并生成 UMI sidecar，后者必须等待 PANDAseq FASTA，不能合并。
- 10X `03.prefilter` 与 `06.split_and_represent`：前者检查 R1 TSO/R2 primer barcode，后者在 merged FASTA 中提取 cell barcode+UMI 并选代表序列，生物学职责不同。
- 四套 pipeline 的 match/split 特殊规则和数据库不能强行共用实现；只统一接口、summary 和 marker 契约。

## 推荐目标结构

### IR_split

```text
pipeline/
├── 00.pipeline_config.env
├── 01.match_sample_barcode.py
├── 02.fastp.sh
├── 03.ir_split.py
├── 04.clean_header.sh
├── 05.pandaseq.sh
├── 06.representative.py
├── 07.igblastn.py
├── run_ir_pipeline.sh
└── stop.sh
```

输出：

```text
output/<dataset>/
├── 01.match/
├── 02.fastp/{data,report}/
├── 03.ir_split/
├── 04.clean/
├── 05.pandaseq/
├── 06.representative/
└── 07.igblastn/
```

### 10X_split

```text
pipeline/
├── 00.pipeline_config.env
├── 01.match_sample_barcode.py
├── 02.fastp.sh
├── 03.prefilter_r1_r2.py
├── 04.clean_header.sh
├── 05.pandaseq.sh
├── 06.split_and_represent.py
├── 07.igblastn.py
├── run_10x_pipeline.sh
└── stop.sh
tools/plot_barcode_rank.py
```

输出：`01.match → 02.fastp → 03.prefilter → 04.clean → 05.pandaseq → 06.split_and_represent → 07.igblastn`。

### PigIgblast

```text
pipeline/
├── 00.pipeline_config.env
├── 01.match_sample.py
├── 02.fastp.sh
├── 03.clean_header.py
├── 04.pandaseq.sh
├── 05.igblastn.py
├── run_pig_pipeline.sh
└── stop.sh
```

输出：`01.match → 02.fastp → 03.clean → 04.pandaseq → 05.igblastn`。

### Igblast_base

```text
pipeline/
├── 00.pipeline_config.env
├── 01.match_sample.py
├── 02.fastp.sh
├── 03.clean_header.sh
├── 04.pandaseq.sh
├── 05.igblastn.py
├── run_igblast_base.sh
└── stop.sh
```

Base 的 01–05 改成分支本地真实实现，不再 `runpy/exec` IR；仍然无 split、无 barcode，保留原始 `sequence_id`。

## 单阶段脚本职责

### 00 配置

- 单文件名固定为 `00.pipeline_config.env`。
- 每套只放本 pipeline 需要的配置；多输入继续使用 Bash 数组，数组 N 对应 submission N。
- 增加 `PYTHON_BIN="python3"`；全部 runner 和 shell 阶段只调用 `${PYTHON_BIN}`。
- 公共处理参数名称一致，IR/10X/Pig/Base 特有参数只出现在所属配置中。
- runner source 后导出标量配置；Python 阶段直接运行时自行读取同一 00 文件。

### fastp

- 样本发现只来自 match summary 中 `status=OK` 的 pair，不再扫描全部 rawdata。
- fastp FASTQ 与 HTML/JSON 继续分到 `data/`、`report/`。
- JSON 解析和 `fastp_summary.csv` 写入 `02.fastp.sh`，删除单独 summary 模块。
- DONE 校验 R1/R2、JSON、summary 行及输入 fingerprint；已有有效样本跳过，新增 OK 样本继续。

### clean / PANDAseq

- 只消费前一阶段 summary 中成功样本，不能重新递归发现不受控文件。
- 保持原 rawdata 的相对路径与 `raw pair/sample_id` 层级。
- 每个样本 marker 记录输入大小/mtime、脚本版本、配置 hash、输出 reads/sequence 数。
- 损坏 gzip、空 FASTA、PANDAseq 失败只标记当前样本错误；阶段内其余样本继续，阶段结束非零，后续阶段不启动。

### split / representative

- 保持 IR 与 10X 当前不同算法。
- summary 明确记录 input、passed、discarded、passed_percent；代表序列阶段记录 barcode 数、UMI 数、代表序列数、ambiguous UMI 数。
- 样本级原子写入，完整输出验证后才写 DONE。

### IgBLAST

- 推荐将 IR/10X 的 shell 调度与 Python postprocess 重写为单个 `07.igblastn.py`；Pig/Base 对应 `05.igblastn.py`。这样真正做到一个阶段一个文件，并让四套公共阶段的并发、summary、过滤定义一致。
- 保留：按 submission chain 选择数据库、productive 筛选、有效 `v_call` 筛选、样本内 `TCR.tsv/BCR.tsv` 合并、禁止跨样本合并。
- IR/10X：最终用 barcode 覆盖 `sequence_id`；Pig/Base：保留原 `sequence_id`。
- 不新增 `cell_type`、`chain`、注释行或其他非 AIRR 字段。
- summary 统一为：`input_sequences, raw_rows, valid_v_rows, productive_rows, output_rows, mapping_percent, retained_percent, status, error`。
- `mapping_percent = valid_v_rows / input_sequences`；`retained_percent = output_rows / input_sequences`，header 不计数。
- 发布前在临时样本目录构建完整结果；成功后替换该样本的旧 `TCR.tsv/BCR.tsv/.NO_RESULTS`，避免残留。
- representative map 必须检测重复 ID 冲突；冲突则该样本 ERROR，不静默覆盖。

## 完成标识与断点续跑

统一阶段 marker：

```text
stage=02.fastp
version=<stage schema version>
status=DONE|PARTIAL|ERROR
config_sha256=<hash>
input_manifest_sha256=<hash>
expected_count=<n>
success_count=<n>
error_count=<n>
timestamp=<ISO8601>
```

- 阶段 DONE 不能只凭“存在一个文件”；必须逐项核对 expected sample/pair。
- 每个样本另有 `.DONE`，支持 submission 补齐后只处理新增 OK 样本。
- `PARTIAL` 表示有可用输出但仍有错误，不能冒充完整 DONE；runner 可允许同阶段其余样本完成，但阶段结束非零且不启动下游。
- match 每次重跑；若 summary hash 未变，已完成样本继续复用。summary 新增 OK 样本时，只使相关样本及其下游失效。
- config fingerprint 应按阶段只包含该阶段实际使用的变量，避免修改绘图参数导致 fastp 重跑。

## 输出重编号兼容策略

- 不自动移动或删除现有 TB 级结果。
- 第一版 runner 同时识别旧目录和新目录：新目录不存在但旧目录的 marker 与 expected manifest 完整匹配时可只读复用，并打印 `LEGACY_OUTPUT_REUSED`。
- 新产生/重跑的结果只写新编号目录。
- 兼容期结束前提供只读迁移检查报告；不自动 `mv/rm`。
- 同 basename 多输入继续使用稳定 hash 后缀，不能使用顺序型 dataset ID。

## stop 与资源控制

- runner 为每个数据集 child 写 PID manifest 和当前 stage；stop 以 PID 树为主，命令行匹配仅作兜底。
- TERM → 等待宽限期 → KILL，覆盖 shell、Python worker、fastp、pandaseq、igblastn。
- 保留阶段启动前及运行中的 `MemAvailable` 检查；低于 150 GB 写日志并终止当前阶段，保留中间结果。
- 每个 worker 继续有内存上限；禁止恢复全局 pipeline lock。
- 多 pipeline 同时启动存在预检竞态，先通过实时 watchdog 和保守 worker 上限控制；后续可加入动态降并发，但不引入共享锁。

## 实施顺序

1. 建立小型 fixture 和当前输出 golden counts，冻结四套现有有效行为。
2. 先修 Pig runtime 路径、Pig ERROR 样本过滤、Base 参数传递、IgBLAST summary/header 计数和旧结果残留。
3. 将配置改名为 `00.pipeline_config.env`，更新所有 runner/逐步脚本引用，并统一 `PYTHON_BIN`。
4. 引入新输出编号、完整 stage/sample marker 与 legacy 只读兼容；暂不删除旧目录。
5. 合并 fastp summary，删除 `summarize_fastp_json.py`。
6. 重写 IR/10X/Base 单文件 IgBLAST 阶段，删除 `igblast_postprocess.py` 与 `exit 0` 后死代码；与 Pig 的过滤结果做契约对照。
7. 将 Base wrapper 替换为本地实现，逐阶段与 IR/Base golden fixture 对比。
8. 移走 10X plot、备份文件和缓存目录，更新 stop、帮助文本、阶段进度和 README。
9. 四套 pipeline 分支逐个启用，禁止一次性同时切换全部生产流程。

## 验证矩阵

### 静态

- 所有 shell：`bash -n`。
- 所有 Python：`python -m py_compile`。
- runner 引用的每个阶段文件存在且编号唯一；pipeline 主目录无孤立编号脚本。

### 断点与错误

- 首次只运行 match，第二次完整执行。
- 单/多输入、重复 basename 稳定 hash。
- OK+ERROR、全部 ERROR、补齐 ERROR 后只跑新增样本。
- 缺 R1/R2、损坏 gzip、空 FASTA、PANDAseq/IgBLAST 单样本失败。
- 中途 stop 后续跑；手动删除一个样本输出后只修复该样本。
- config/submission/输入文件改变时，只有正确依赖阶段失效。
- 低于 150 GB 时停止并保留结果；多个 pipeline 同时运行；stop 能终止完整 PID 树。

### 生物学与统计

- IR/10X 最终 `sequence_id=barcode`；Pig/Base 保留原 ID。
- `productive=F`、空/无效 `v_call` 被排除。
- 每个样本只有自身的 `TCR.tsv/BCR.tsv`，不存在跨样本总表。
- input/raw/valid-v/productive/output 计数与真实 FASTA/TSV 行数一致，百分比可重算。
- IR 混样输出保持 `raw pair/sample_id` 层级；一个文件匹配多个 barcode 时各样本独立。
- IR UMI sidecar 与代表序列 join、10X TSO/barcode/UMI representative 行为与改造前 golden fixture 一致。
