# IR pipeline 并发与正确性审计优化方案

## 1. 审计范围

审阅对象：

- `pipeline/run_ir_pipeline.sh`
- `pipeline/00.pipeline_config.env`
- `pipeline/01.match_sample.py`
- `pipeline/02.run_fastp.sh`
- `pipeline/03.split_barcode.py`
- `pipeline/04.clean_header.sh`
- `pipeline/05.work_pandaseq.sh`
- `pipeline/06.representative.py`
- `pipeline/07.work_igblastn.sh`
- `pipeline/stop.sh`

当前主流程保持不变：

```text
raw:
match -> fastp -> barcode/UMI split -> clean -> PANDAseq
      -> sample 内按 UMI 提取代表序列 -> IgBLAST

presplit:
match/完整性检查 -> 登记 FASTQ 并生成 UMI sidecar -> clean -> PANDAseq
      -> sample 内按 UMI 提取代表序列 -> IgBLAST
```

生物学分组键建议固定为：

```text
library_id + sample_id + UMI
```

barcode 只负责把混样原始文件拆到具体 sample；完成拆分后，代表序列不能继续按 barcode 分组。

## 2. 审计结论

### 2.1 已确认的并发问题

1. `02.run_fastp.sh`、`04.clean_header.sh`、`05.work_pandaseq.sh` 使用 FIFO 回收 PID。
   当最早启动的样本很慢、后启动任务已经结束时，调度器仍等待第一个 PID，不能及时补充新任务，造成 CPU 空闲和长尾放大。

2. `07.work_igblastn.sh` 使用 `wait -n || true` 后无条件执行 `active_jobs--`。
   当 `wait -n` 异常、没有可回收子进程或任务被外部停止时，活动任务计数仍然减少，可能错误释放并发槽并超过 `MAX_PARALLEL_JOBS`。

3. 各 pipeline 独立按 300 GB 内存预算启动任务，没有跨 pipeline 的资源占用协调。
   目前阶段 watchdog 能在 `MemAvailable` 过低时终止阶段，但不能阻止多个 pipeline 同时超卖内存，也不能根据实时内存动态降低派发速度。

4. `03.split_barcode.py` 以“样本/barcode 映射”为并发任务。
   同一个混样 R1/R2 若匹配 N 个样本，会被完整解压和扫描 N 次。三样本混样约产生三倍输入 I/O，同时多个 worker 还会竞争同一磁盘。

5. `06.representative.py` 并行读取每个 FASTA 后，把所有 `prepared` 结果留在内存，再复制聚合到 `sample_groups`。
   内存峰值随已完成样本累计，不只取决于正在运行的 worker 数；32 workers 对大样本风险很高。

6. representative 对同一个 sample 的多个 FASTA 可能重复加载相同 UMI sidecar，并重复构造 `read_id -> UMI` 字典。

### 2.2 已确认的断点续跑与数据契约问题

1. `run_ir_pipeline.sh::stage_done()` 对 fastp、split、clean、PANDAseq 主要检查“目录中存在任意一个输出”。
   它没有验证所有预期 `status=OK` 任务是否都有完整输出，容易把部分完成阶段误判为完成。

2. 阶段 marker 包含 mapping/config hash，但没有包含：

   - 当前阶段脚本版本；
   - 每个任务的输入 fingerprint；
   - 预期任务数、完成数和失败数；
   - 关键输出 fingerprint。

   因此代码更新后可能错误复用旧结果；部分文件被替换时也不一定能准确识别。

3. `03.split_barcode.py` 的 DONE checkpoint 主要验证输出存在，没有绑定当前输入文件 size/mtime、barcode、UMI 长度和算法版本。

4. clean、PANDAseq、representative 仍存在递归扫描上游目录的路径。历史残留、旧布局和不属于本轮 manifest 的文件可能进入下游。

5. `raw/presplit -> sidecar -> clean header -> PANDAseq header -> representative join` 是当前最高风险数据链。
   只要 canonical read ID 规则不完全一致，就会出现 PANDAseq 有序列、sidecar 有 UMI，但代表序列为 0 的情况。

6. representative 当前以 `sample_id + UMI` 聚合。对单批次成立，但同名 sample 出现在不同独立文库时会错误合并，需要显式增加 `library_id`。

7. representative state 保存完整 sequence、header 和并列候选序列，且增量执行时会全量重写 state；大数据会产生几十至上百 GB 的临时文件和长时间单线程收尾。

8. `07.work_igblastn.sh` 在部分路径下仍会删除 `.job_results` 或无效 `.batches`，与“默认保留中间产物”契约不完全一致。

### 2.3 性能与可观测性问题

1. PANDAseq 为统计 input reads 可能重新完整解压 FASTQ，随后 PANDAseq 再读取一次；应复用 fastp/split summary。
2. FASTA 序列数在多个阶段被重复全文件扫描。
3. representative 在 worker 显示 100% 后仍有全量 state 写盘和 FASTA 输出，但没有分步骤进度，表现为“卡在 100%”。
4. `stop.sh` 仍依赖多次进程发现；应以本次 run 的 PID/PGID 注册表为主，避免扫描整台共享服务器。
5. clean 配置中的每任务 threads 与实际单进程行为不一致，资源预算含义容易误导。

## 3. 目标架构

每个阶段只消费上游 manifest，不再通过递归扫描猜测输入：

```text
stage_manifest.tsv
  stage
  task_id
  library_id
  raw_pair_id
  sample_id
  chain
  input_path_1
  input_path_2
  input_fingerprint
  output_path_1
  output_path_2
  output_fingerprint
  status
  error
  attempt
  elapsed_s
  peak_rss_mb
  updated_at
```

任务 ID 固定为：

```text
sha1(library_id + raw_pair_id + sample_id + barcode_sequence + stage)
```

每个任务使用独立原子 marker：

```text
.done/<task_id>.json
```

marker 至少包含：

```text
status, script_version, config_hash, input_fingerprint,
output_paths, output_fingerprint, attempt, updated_at
```

只有 `marker 指纹一致 + summary 状态 DONE + 关键输出完整` 才允许跳过。

## 4. 实施计划

### P0：先修正确性与断点续跑

#### P0-1 建立 active manifest

涉及文件：

- `run_ir_pipeline.sh`
- `02.run_fastp.sh`
- `03.split_barcode.py`
- `04.clean_header.sh`
- `05.work_pandaseq.sh`
- `06.representative.py`
- `07.work_igblastn.sh`

操作：

1. match 输出 canonical manifest，记录 `library_id/raw_pair_id/sample_id/status`。
2. 每个阶段根据上游 manifest 的 `DONE/OK` 行生成自己的任务清单。
3. 后续阶段禁止递归扫描整个上游目录作为主要输入来源。
4. ERROR 样本不进入下游；本次补齐 submission 后，只新增或修复对应任务。
5. 阶段状态定义为：

   - `DONE`：全部可处理任务完成；
   - `PARTIAL`：部分任务完成、部分失败或暂不可匹配；
   - `FAILED`：没有任何可用任务成功，或发生阶段级错误；
   - `RESOURCE_PAUSED`：资源不足，已停止派发新任务。

#### P0-2 修复 stage_done

涉及函数：`run_ir_pipeline.sh::write_stage_marker()`、`stage_done()`。

操作：

1. marker 记录 manifest hash、script hash、成功/失败/跳过数量。
2. `stage_done()` 比对预期任务集合，而不是仅检查目录中存在一个文件。
3. 任一输出被删除、为空、输入变化或脚本版本变化时，只使对应任务及下游任务失效。
4. 保留当前“已完成样本跳过、新补齐样本继续”的样本级恢复能力。

#### P0-3 统一 raw/presplit UMI 数据契约

涉及文件：`03.split_barcode.py`、`04.clean_header.sh`、`06.representative.py`。

统一 stage 03 输出：

```text
canonical R1 FASTQ
canonical R2 FASTQ
umi_sidecar.tsv.gz
```

sidecar 字段：

```text
library_id, sample_id, raw_pair_id,
read_id_original, read_id_canonical, umi
```

要求：

1. presplit 模式必须显式指定输入根，禁止静默回退到旧 fastp 目录。
2. presplit header 只在第一个空格前解析 `#<UMI>`。
3. R1/R2 必须验证 canonical ID、UMI 和记录数一致。
4. representative summary 新增：

   - `fasta_reads`
   - `sidecar_reads`
   - `umi_joined_reads`
   - `umi_join_percent`

5. join 率低于可配置阈值时标记该任务 ERROR，不允许以“0 条代表序列但成功”结束。

#### P0-4 修复 IgBLAST 并发计数

涉及文件：`07.work_igblastn.sh`。

操作：

1. 维护 `pid -> task_id` 映射。
2. 仅当已知 PID 被成功回收时释放并发槽。
3. 保留每个任务真实退出码，不使用 `wait -n || true` 吞掉状态。
4. job result、summary row 和 DONE marker 均采用任务唯一临时文件再原子 rename。
5. `KEEP_INTERMEDIATE=1` 时不得删除有效 `.batches`、raw output 或 job result；失效批次移动到 `.stale/<timestamp>/`。

### P1：再优化吞吐与内存

#### P1-1 将 FIFO 调度改为 wait-any

涉及文件：

- `02.run_fastp.sh`
- `04.clean_header.sh`
- `05.work_pandaseq.sh`
- `07.work_igblastn.sh`

操作：

1. 使用 `wait -n -p completed_pid`（若目标 Bash 不支持，则轮询已知 PID 状态）。
2. 任一任务完成立即回收并提交下一个任务。
3. 统一记录 `submitted/running/done/failed/skipped/total`。
4. 每个任务保留 `attempt/elapsed/peak_rss/exit_code`。

#### P1-2 物理 FASTQ pair 一次读取、多 barcode 分发

涉及文件：`03.split_barcode.py`。

当前：

```text
一个 physical pair 匹配 N 个样本
=> 建立 N 个 worker
=> 同一 R1/R2 解压 N 次
```

目标：

```text
一个 physical_pair_id 一个 worker
=> 建立 barcode_sequence -> sample output 路由表
=> 每条 read 只读取一次
=> 命中后写入对应 sample 目录
```

补充要求：

1. 对重复 barcode 显式报歧义，不能一条 read 静默写入两个 sample。
2. checkpoint 以 physical pair 为单位，同时记录各 sample 已写 reads。
3. 恢复时确保所有输出 part 文件与同一 checkpoint 一致。
4. 输出目录继续保持 `<raw relative path>/<raw pair>/<sample_id>/`。

#### P1-3 representative 改为按样本流式处理

涉及文件：`06.representative.py`。

操作：

1. 新增独立 `SCIGBLAST_IR_REPRESENTATIVE_WORKERS`，不再复用 split workers。
2. 按 `library_id + sample_id` 收集该样本的 FASTA 与 sidecar。
3. 一个样本只建立一次 sidecar index；能按 raw pair 精确加载时，不加载整个样本 sidecar。
4. 一个样本完成聚合、代表序列选择和分样本 state 写盘后立即释放 Counter 和 header 字典。
5. 不在主进程保留所有样本的 `prepared` 和 `sample_groups`。
6. 代表 state 改为每样本独立原子文件，总 state 仅保存索引。

推荐主 state 字段：

```text
library_id, sample_id, umi, representative_id,
total_reads, representative_reads, unique_sequence_count,
top_candidate_count, ambiguity_status, source_barcodes,
input_fingerprint
```

并列最高频候选：

- 主 state 不保存所有完整候选序列；
- `TIED_TOP` 才写独立 `ambiguity.tsv.gz`；
- 主表保存候选数量与 sequence hash；
- representative FASTA 保留选中序列的原始 header。

#### P1-4 动态资源准入

涉及文件：`00.pipeline_config.env`、各并发阶段脚本、IR 内部 runtime helper。

不恢复独占 pipeline lock。每次启动任务前判定：

```text
MemAvailableGB >= STOP_THRESHOLD_GB + task_reserved_gb
active_threads + task_threads <= pipeline_thread_budget
reserved_memory + task_reserved_gb <= pipeline_memory_budget
```

内存低于 150 GB 时：

1. 停止派发新任务；
2. 允许正在运行任务正常结束；
3. 写入 `RESOURCE_PAUSED` 日志和 marker；
4. 若继续跌至紧急阈值（建议 100 GB），再终止本 pipeline 的进程组；
5. 不删除任何已完成或中间结果。

推荐拆分并发参数：

```text
FASTP_MAX_JOBS
FASTP_THREADS_PER_JOB
SPLIT_WORKERS
CLEAN_MAX_JOBS
PANDASEQ_MAX_JOBS
PANDASEQ_THREADS_PER_JOB
REPRESENTATIVE_WORKERS
IGBLAST_MAX_JOBS
IGBLAST_THREADS_PER_JOB
```

初始基准参数（最终应通过小规模 benchmark 校准）：

| 阶段 | 并发任务 | 每任务线程 | 每任务预留内存 |
|---|---:|---:|---:|
| fastp | 24 | 8 | 4 GB |
| split | 12–16 | 1 | 8 GB |
| clean | 24 | 1 | 2 GB |
| PANDAseq | 20 | 4 | 4 GB |
| representative | 4–8 | 1 | 24 GB |
| IgBLAST | 16 | 16 | 8 GB |

不建议直接用满 700+ CPU；gzip、共享磁盘和数据库 page cache 更可能先成为瓶颈。

#### P1-5 去掉重复全文件统计

涉及文件：`05.work_pandaseq.sh`、`06.representative.py`、`07.work_igblastn.sh`。

操作：

1. input reads 优先读取 fastp/split/clean summary。
2. PANDAseq summary 直接记录 merged/unmerged 数。
3. 后续阶段使用 manifest 中计数，不再重新完整解压 FASTQ。
4. FASTA 计数随生成过程记录，避免为统计再次扫描整个 FASTA。

### P2：可观测性、停止与维护

#### P2-1 快速可靠 stop.sh

涉及文件：`run_ir_pipeline.sh`、所有后台任务启动点、`stop.sh`。

运行时创建：

```text
.run/<run_id>/processes.tsv
```

字段：

```text
pid, pgid, stage, task_id, started_at, cmd_hash
```

停止流程：

1. 只读取本 IR run 的 PID/PGID 注册表；
2. 校验 PID start time/cmd hash，防止 PID 复用误杀；
3. 对 process group 发送 TERM；
4. 等待可配置宽限时间；
5. 仅对仍存活的本 run process group 发送 KILL；
6. 最后仅用一次命令匹配兜底检查 orphan fastp/PANDAseq/igblastn；
7. 不扫描所有 `/proc/<pid>/cwd/environ`。

#### P2-2 分阶段进度与资源日志

每个阶段统一输出：

```text
submitted, running, done, failed, skipped, total,
available_gb, reserved_gb, active_threads
```

representative 单独显示：

```text
scan inputs -> build UMI groups -> select representatives
-> write per-sample state -> write FASTA/map -> finalize index
```

避免 worker 已 100% 但主线程还在写几十 GB 文件时无日志。

#### P2-3 IgBLAST summary 字段固定语义

`chain_summary.csv` 至少包含：

```text
input_sequences
raw_mapped_sequences
raw_mapping_percent
productive_sequences
productive_percent_of_input
final_output_sequences
final_retained_percent
status
error
```

不得再用 productive 筛选后的数量填入 mapping 字段。

## 5. 测试与验收方案

### 5.1 最小测试数据矩阵

至少构造以下小数据：

1. raw 单样本、单 barcode；
2. raw 一个物理 pair 对应三个 sample/barcode；
3. presplit header 为 `@read#UMI 1:N:0:index`；
4. submission 中部分 ERROR、部分 OK；
5. 同一 sample/UMI 下：
   - 全部 reads 相同；
   - 一个主序列加低频错误序列；
   - 两个并列最高频序列；
6. 两个 library 使用相同 sample_id 和相同 UMI；
7. 人工中断 fastp、split、representative 和 IgBLAST；
8. 在输出目录放入无关旧 FASTQ/FASTA。

### 5.2 必须通过的正确性断言

1. 混样一个 physical pair 只完整读取一次，分别输出到正确 sample 目录。
2. representative 在同一 `library_id + sample_id + UMI` 内聚合，不跨 sample、不跨 library。
3. 每个 UMI 只输出一条代表序列；并列最高频记录为 `TIED_TOP`。
4. 原始 representative FASTA header 被保留。
5. ERROR match 不进入下游；已匹配样本能继续完成。
6. 停止后重跑只处理未完成/指纹变化任务。
7. 旧残留文件不会被下游扫描。
8. IgBLAST raw mapped、productive 和 final retained 三组统计可从输出复算一致。

### 5.3 并发与资源验收

1. 制造一个慢任务时，其他任务完成后立即补充新任务，不出现 FIFO 空闲。
2. IgBLAST 活动 PID 数始终不超过配置并发数。
3. 混样三 barcode 的输入读取量接近一遍 FASTQ，而非三遍。
4. representative 峰值内存受 `REPRESENTATIVE_WORKERS × 单样本预算` 约束，不随已完成样本数持续增加。
5. `MemAvailable < 150 GB` 时停止派发；当前任务完成后阶段以 `RESOURCE_PAUSED` 退出。
6. `stop.sh` 在数秒内停止本 run，不影响同服务器其他 pipeline。

## 6. 推荐实施顺序

1. 先实现 P0：manifest、任务级 marker、raw/presplit ID/UMI 契约和 IgBLAST slot 修复。
2. 用最小测试矩阵验证断点续跑和混样正确性。
3. 实现 physical-pair 单遍 split 与 representative 按样本流式处理。
4. 改造 wait-any 调度和动态资源准入，再通过 benchmark 校准并发参数。
5. 最后完成 stop.sh、统一进度和 summary 语义。

在完成 P0 前不建议继续上调并发；当前主要风险不是 CPU 不足，而是错误复用 checkpoint、重复 I/O 和 representative 内存累积。

## 7. 本轮已落地

本轮已在 IR pipeline 中完成以下低风险修复：

- fastp、clean、PANDAseq 使用 `wait -n -p` 完成任务即回收并发槽；旧 Bash 自动回退到 FIFO。
- IgBLAST 维护已登记 PID，只有已知子进程被回收后才释放并发槽；不再无条件 `active_jobs--`。
- IgBLAST 监控改为统计完整进程树 RSS，并在保留中间产物时保留 job result 目录。
- 无效 IgBLAST batch 在默认保留模式下归档到 `.batches.stale.*`，不再直接删除。
- 阶段 marker 和最终 pipeline marker 增加脚本 SHA256；阶段 skip 会因代码变更而失效。
- fastp marker 增加双端文件元数据和 fastp 参数指纹；split checkpoint 增加输入文件、模式、UMI 长度和 barcode 指纹。
- representative 改为按 biological sample 分组、逐样本释放 Counter/header/sidecar cache，避免将所有 FASTA 的中间聚合结果同时保留。
- representative 使用独立的 `SCIGBLAST_IR_REPRESENTATIVE_WORKERS`，默认 4，不再直接继承高并发 split worker 数。
- raw IR split 对同一 physical R1/R2 pair 的多个 sample/barcode 映射采用一次读取、多目的输出；presplit 逻辑保持原有校验/登记模式。
- `stop.sh` 在存在有效 run PID 时优先停止该 PID 的完整进程树，仅在没有有效 PID 或收尾检查时进行一次兜底扫描。
- `stage_done()` 额外要求 fastp、split、PANDAseq 的核心 summary 存在，降低“目录有残留文件即误判完成”的风险。

仍待后续迭代的高收益项目：active manifest 全面替代递归扫描、representative 分样本 state、跨 pipeline 资源令牌和动态内存暂停策略。这些改动会影响更多阶段的数据契约，建议先用第 5 节测试矩阵验证本轮修复后再实施。
