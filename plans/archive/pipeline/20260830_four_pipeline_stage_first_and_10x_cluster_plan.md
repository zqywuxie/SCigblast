# 四套 pipeline 架构统一与 10X 聚类接入计划

## 1. 目标与不可变约束

### 生物学边界

- **10X**：单细胞；barcode 表示细胞，UMI 表示分子。Stage 06 已保证每条 representative 记录对应一个 barcode+UMI 分子，因此下游聚类不强制恢复或暴露 UMI；IgBLAST 后不接入 bulk preprocessing，而进入单细胞链聚类。
- **IR**：bulk；barcode 表示样本。IgBLAST 后接入 preprocessing，并保留当前 `umi_counts` 计算定义。
- **Pig / Igblast_base**：维持现有业务流程，不新增 split、representative、preprocessing 或 cluster。

### 冻结项

- `preprocess/models/*.py` 与复制到 IR pipeline 的 `models/*.py` 中，筛选、SHM、表达量、`umi_counts` 等计算定义不得修改。
- IR 的性能优化只能发生在 runner、adapter、任务调度和 I/O 层。
- 四套 pipeline 各自维护脚本，不抽取 common pipeline。
- 保留现有 IgBLAST productive、`v_call` 等最终筛选语义；公共 AIRR 字段不额外改写。

## 2. 当前审计结论

| Pipeline | 当前主流程 | 主要问题 | 目标终点 |
|---|---|---|---|
| 10X | match → fastp → prefilter → clean → pandaseq → representative → IgBLAST | Stage 08 未接入；cluster 硬编码路径；representative 平票目前仅按字典序裁决；多输入仍为 dataset-first | `07.igblastn_out/<dataset>` → `08.cluster/<dataset>` |
| IR | match → fastp → split/presplit → clean → pandaseq → representative → IgBLAST → preprocessing | 已是 stage-first；preprocessing 命名与内存参数仍有旧契约 | `07.igblastn_out/<dataset>` → `08.preprocessing/<dataset>` |
| Pig | match → fastp → clean → pandaseq → IgBLAST | 多输入为 dataset-first；状态、日志和 PID 未完全按 dataset 隔离 | `05.igblastn_out/<dataset>` |
| Base | match → fastp → clean → pandaseq → IgBLAST | 多输入为 dataset-first；submission/状态契约仍不统一 | `05.igblastn_out/<dataset>` |

## 3. 统一的多数据集输出契约

四套 pipeline 统一采用“阶段优先、数据集在阶段内分开”的结构：

```text
output/
├── dataset_manifest.csv
├── 01.match/<dataset_id>/
├── 02.fastp/<dataset_id>/{data,report}/
├── ...
├── logs/<dataset_id>/pipeline.log
└── .pipeline_state/<dataset_id>/
```

- `dataset_id` 默认取输入目录 basename；同名输入目录追加稳定的短路径 hash。
- batch parent 给 child 传同一个 `SCIGBLAST_OUTPUT_ROOT`，另传 `SCIGBLAST_DATASET_LABEL`；不再传 `OUTPUT_ROOT/<dataset>`。
- 每个 runner 实现本地 `stage_root(stage)=OUTPUT_ROOT/stage/dataset_id`。
- `dataset_manifest.csv` 至少记录：`dataset_id,input_root,submission_sources,stage_dirs,status,error`。
- submission 是候选文件集合，不按数组下标强制与 input 一一配对；match 根据 Note/路径在候选集合内选择。
- 旧目录只读兼容或由显式迁移脚本处理；新旧布局不得同时作为写入目标。

## 4. 10X：代表序列质量裁决与单细胞聚类

### 4.1 明确“一行代表一个分子”的契约

Stage 06 对每个 `(sample, barcode, UMI)` 只输出一条 representative，因此 Stage 07 的每一条有效 IgBLAST 记录可视为一个分子事件。Stage 08 不要求恢复 UMI；同一细胞内相同 V/J/CDR3 聚类后的 representative 行数就是分子支持数。

必须增加结构校验，防止同一 representative 因批次重试或 join 重复而被多计：Stage 06 输出的 `representative_id` 必须唯一，Stage 07 后处理必须保证每个 `representative_id × chain task` 最多产生一条进入聚类的有效记录。

### 4.2 barcode 字段语义

- 10X 最终 IgBLAST 输出不再把 barcode 值塞在名为 `sequence_id` 的字段中。
- 将该列正式命名为 `barcode`；下游 cluster、summary 和 manifest 均读取 `barcode`。
- 如内部 batch/map 仍依赖 query ID，可仅在隐藏中间文件保留 `representative_id`，最终公开表不必暴露 UMI。
- 这是 10X 特有输出契约；IR、Pig、Base 的 AIRR 字段不随之改名。

建议在 10X 输出新增以下代表序列审计字段：

```text
barcode, representative_read_count, representative_read_percent,
top_candidate_count, ambiguity_status,
alternative_top_sequences, selection_method
```

这些字段来自 Stage 06 map/state，通过内部 `representative_id` join 到 IgBLAST 行；它们用于审计代表序列选择，但不改变 IgBLAST 比对结果。

### 4.3 代表序列选取优化

当前逻辑是：同一 `(barcode, UMI)` 内按完整序列 reads 数排序；最高频并列时按核苷酸字典序取第一条。该过程是确定性的，并非运行时随机，但字典序不代表质量。

目标排序规则：

1. `sequence_read_count` 降序，仍以实验支持数作为首要证据。
2. 对最高频并列候选，使用可追溯的序列质量分数降序。
3. 质量仍并列时，选择 `N` 数更少、expected error 更低的候选。
4. 仍无法区分时，才用稳定字典序保证可复现，同时标记 `TIED_AFTER_QUALITY`，不能标为明确结果。

当前 Stage 06 的中间 TSV 只有 `umi,header,sequence`，PANDAseq FASTA 本身不携带 base quality，因此不能直接凭现有文件做真实质量排序。实施时应从上游增加轻量质量 sidecar，而不是伪造质量指标：

- 优先保存每个 read/merged record 的 `mean_base_quality` 或 `expected_errors`；如果 PANDAseq 能提供逐条 assembly quality，则优先使用该值。
- sidecar 只保存 `record_id + quality metrics`，不重复保存完整序列，避免再次产生超大状态文件。
- 若某批历史数据没有质量 sidecar，回退为“频数 → N 数 → 稳定字典序”，并将 `selection_method=COUNT_FALLBACK_NO_QUALITY`。
- `alternative_top_sequences` 至少保留所有最高频候选的序列、count 和可用质量分数；数量过大时写独立 gzip 审计表，主表仅保留候选数和审计表引用。

### 4.4 单细胞链聚类

聚类键为：

```text
(dataset_id, relative_sample, barcode, locus,
 normalized_v_call, normalized_j_call, normalized_cdr3)
```

- 每条 representative IgBLAST 行贡献 1 个分子支持；聚类的 `umi_counts` 等于组内去重后的 representative 记录数。
- 不把 UMI 放进最终聚类键，也不要求最终表含 UMI。
- 若一个 representative 在同一 chain task 内产生重复行，先按内部 representative ID 去重，不能将重复输出当作多个 UMI。
- 再按分子支持数、比对质量和稳定顺序选择每条链的代表。

保留现有最多两个链候选、dominant ratio 等阈值，除非后续另行确认修改；本次不偷偷改变阈值定义。

### 4.5 Stage 08 接入 runner

- 将 `08.chain_cluster.py` 的硬编码 Windows 路径改为 runner/env 输入。
- 输入：`07` 产生的 cluster sidecar；输出：`output/08.cluster/<dataset_id>/`。
- 新增 Stage 08 DONE marker，指纹包含 IgBLAST 输入、representative map/state、质量 sidecar、cluster 参数和脚本 hash。
- 10X 总 DONE 必须依赖 Stage 08；明确弃用孤立的旧 `pipeline/pipeline` bulk preprocessing 入口。

## 5. IR：接入 preprocessing，但冻结计算定义

- IR representative 的生物学分组键固定为 `(dataset_id, relative_sample, UMI)`；barcode 只是样本拆分来源，不加入细胞级分组，也不能套用 10X 的 barcode 逻辑。
- 当前 `06.representative.py` 与 10X 一样采用“最高 reads 数 → 字典序”规则。改为与 10X 共识一致的“最高 reads 数 → 合并质量 → N 数 → 稳定字典序”，但两套脚本各自实现和维护。
- `05.work_pandaseq.sh` 对 representative 模式启用 PANDAseq `-F`，输出带 assembled quality 的 merged FASTQ；`06.representative.py` 流式解析 FASTQ，并按候选序列累计 count 与轻量质量统计。
- IR 的每个候选序列记录 `median_expected_errors`、`median_mean_quality`；仅最高频并列候选进入质量裁决。历史 merged FASTA 仍可读取，但必须标记 `COUNT_FALLBACK_NO_QUALITY`。
- IR 同样输出 `selection_method`、`top_candidate_count`、`ambiguity_status` 和候选审计信息；仍无法区分时标记 `TIED_AFTER_QUALITY`。
- 必须防止同一 `sample_id` 在不同原始 library/pair 下意外合并 UMI。默认以 `dataset_id + relative_sample` 作为 UMI namespace；只有显式声明为同一生物学 library 时才允许跨源合并。
- 保持 stage-first 结构和 representative 分支的 `08.preprocessing/<dataset>`。
- `models/*.py` 只复制/校验，不修改任何公式、字段筛选或 `umi_counts` 定义。
- 所有并发、流式读写、SQLite 索引、ID alias 和容错改动仅放在 `08.preprocessing.py`、`ir_preprocessing_adapter.py` 与 runner。
- 将 `RUN_ANALYSIS` 命名统一为 `RUN_PREPROCESSING`；旧 env 仅作为兼容别名并输出弃用警告。
- 增加独立 `PREPROCESSING_REQUIRED_GB`，不得复用 representative 内存参数。
- Stage 08 marker 包含 input、adapter、models 文件 hash，确保模型变化会失效缓存，但本次模型 hash 应保持不变。
- representative 与 merged 两种模式保持当前边界：只有具备所需代表序列/UMI 状态的路径进入 preprocessing。

## 6. Pig 与 Igblast_base：只迁移编排布局

### Pig

- 改为 `output/<stage>/<dataset>`，同步修改五个阶段的显式输入输出目录。
- 保留 Pig species 检查、chain 展开、猪专用数据库和现有过滤定义。
- DB preflight 放在 match 审核通过之后、重阶段开始之前。
- 不接入 representative、preprocessing 或 cluster。

### Igblast_base

- 改为 `output/<stage>/<dataset>`，同步修改五个阶段的显式输入输出目录。
- 保留 chain 驱动的 IgBLAST 配置和现有最终筛选。
- baseline 不引入 barcode 字段或 barcode 文件依赖。
- 不接入 representative、preprocessing 或 cluster。

## 7. 并发、恢复与失败语义

- 默认数据集串行、阶段内样本并行，避免四条 pipeline 同时放大 I/O 和内存。
- 统一 300 GB 可用预算与 150 GB 实时停止阈值；每个重阶段按任务估算内存后动态限制 worker 数。
- 单样本失败时本阶段其他样本继续；阶段结束返回非零，后续阶段不启动。
- 下游仅消费 match manifest 中 `status=OK` 的样本；ERROR 样本后续补齐后可独立续跑，已完成样本不重跑。
- DONE marker 使用原子、样本唯一临时文件，避免并发覆盖；内容包含输入、配置、脚本与关键 sidecar 指纹。
- 每套 pipeline 的 `stop.sh` 只读取自身 `output/.pipeline_state/<dataset>/pids`，不全机扫描，不共享 common stop。
- 所有阶段打印 `completed/failed/skipped/total/percent/current_sample`，大文件归并额外打印字节或行数进度。

## 7.1 四套 pipeline 补充审计改进项

### PANDAseq 与存储

- 只有需要 representative 质量裁决的 10X、IR 改为 merged FASTQ；Pig、Base 没有 representative，继续输出 FASTA，避免无收益地增加存储。
- 10X/IR 的 merged FASTQ 建议即时 gzip；Stage 06 流式读取，不解压成第二份永久文件。
- representative FASTA 成功落盘并完成 marker 校验后，是否保留 merged FASTQ 由配置控制，默认保留以支持审计和断点重建。
- PANDAseq `-F` 的质量表示合并置信度，不作为普通 Illumina 绝对质量阈值；本计划只用于同 UMI 最高频候选的相对平票裁决。

### ID 与数据关联

- `clean → pandaseq → representative` 的 canonical read ID 必须逐阶段一致；FASTQ header 清理不能改变 UMI sidecar 的关联键。
- 10X/IR marker 增加输出格式版本（FASTA/FASTQ）、PANDAseq 参数、质量算法版本；旧 FASTA marker 不能误判为新质量模式已完成。
- Stage 07/08 join 必须验证一对一或预期的一对多关系，并报告 unmatched、duplicate、conflict 数，不能静默丢记录。

### 并发与临时文件

- 所有样本 worker 的临时 marker、summary row、日志和中间输出必须包含 dataset、sample hash、PID；禁止 lane 级共享 `.tmp`。
- worker 数同时受 CPU、线程预算、内存预算与活跃样本数约束；避免 `sample_workers × tool_threads` 超过全局预算。
- 汇总文件由主进程按稳定顺序单写；worker 只写独立 row/shard，杜绝并发追加 CSV。
- gzip 压缩若成为单核瓶颈，可使用可配置 pigz，但不改变文件格式和计算结果。

### 断点与可重复性

- 每阶段 marker 除输入文件指纹外，还必须包含阶段脚本 hash、配置 hash、schema version 和关键外部工具版本。
- 即使所有样本均 skip，也重新由 per-sample marker 生成阶段 summary，避免旧 summary 缺行或统计陈旧。
- 失败样本保留中间产物和错误状态；补齐输入后只重跑失败/失效样本。
- 质量排序所有末级 tie-break 必须稳定，确保改变 worker 数或任务完成顺序不会改变 representative。

### IgBLAST 与数据库

- 四条 pipeline 在运行前验证 V/D/J、auxiliary data 与目标链配置完整，并把数据库文件指纹写入 marker。
- IgBLAST summary 明确区分 `input_sequences`、`mapped_sequences`、`productive_sequences`、`filtered_sequences` 及各自百分比分母。
- 每样本输出独立，不进行跨样本 TCR/BCR 合并；同名样本依靠 dataset manifest 和相对路径区分。
- 批次 FASTA/map 仅在必要时物化，优先复用或流式构建，避免复制大体积 representative FASTA；不得改变输入序列集合。

### Pipeline 特有清理

- 10X：移除主流程外孤立的 bulk preprocessing 入口；Stage 08 cluster 纳入 DONE、stop 和进度体系。
- IR：统一 `RUN_PREPROCESSING` 命名并保留旧变量兼容警告；为 preprocessing 设置独立内存参数。
- Pig：保留猪 species/chain/数据库逻辑；DB preflight 在 match 审核之后执行。
- Base：保持无 barcode 依赖；submission 作为候选集合而非与 input 按数组位置绑定。

## 8. 实施顺序

1. 为四个 runner 固化 dataset ID、`stage_root`、manifest、marker、log/PID 契约。
2. 先迁移 10X 的路径布局及全部阶段输入输出，不改变计算。
3. 将 10X PANDAseq 改为 merged FASTQ，扩展 representative 质量字段，将最终 `sequence_id` 列正式改为 `barcode`，参数化并接入 Stage 08 cluster。
4. 将 IR representative 模式改为 merged FASTQ 和相同质量裁决，随后整理 preprocessing 命名和独立资源参数；校验 models 文件字节/结果不变。
5. 迁移 Pig 的路径布局，复核 Pig DB preflight 与数据库选择。
6. 迁移 Igblast_base 的路径布局与 submission 候选集合处理。
7. 更新四套 env 示例、README、stop.sh、schema version 和旧 FASTA/目录迁移说明。

## 9. 验证方案

## 9.0 已实施修正（2026-08-30）

- 10X 与 IR representative 已采用“reads 数优先，质量/expected errors/N 数次级，稳定字典序兜底”的确定性选择，并保留 `alternative_top_sequences`、`ambiguity_status`、`selection_method` 审计字段。
- IR representative 支持预拆分 header 的 `#UMI`/`#UMI:` 回退；sidecar 与 header UMI 冲突时丢弃该 read并计入 unmatched，不猜测归属。
- IR/10X PANDAseq 批次 map 改为批次构建结束后一次性校验，空 map、重复 header 和旧失效 manifest 会触发安全重建。
- 四套 runner 使用阶段优先的多数据集输出、原子且数据集隔离的 marker/PID/log；Pig 数据库预检移到首次 match review 之后。
- 10X Stage 08 chain cluster 已接入主 runner；IR Stage 08 preprocessing 保持 `models` 计算定义不变，仅在适配、并发、检查点和内存监控层优化。
- 所有阶段默认保留中间产物；只有显式设置 `SCIGBLAST_CLEANUP_INTERMEDIATE=1`（或对应 keep-intermediate 选项）才删除大体积序列文件。

### 四套共同测试

- 两个微型输入目录全流程运行，确认结构为 `stage/dataset`。
- 两个输入目录 basename 相同，确认 stable hash 消歧且断点互不污染。
- 首次 match-only、第二次完整运行、故障后续跑、补齐 ERROR 样本后增量运行。
- 中断后重跑不覆盖已完成样本；脚本或配置变化只失效相关 dataset 的当前及下游阶段。
- stop.sh 在数秒内停止本 pipeline 的父子进程且不影响其他 pipeline。

### 10X 专项断言

- 每个 `(barcode, UMI)` 仅产生一条 representative；即使 Stage 07 重试或 join，也只计一个分子。
- 同 barcode、同 V/J/CDR3 的 N 条不同 representative 记录，cluster `umi_counts=N`。
- 不同 barcode 绝不合并；不同 sample/dataset 绝不合并。
- 同一 UMI 出现最高频冲突序列时，质量高者胜；仍无法区分时有稳定代表、`TIED_AFTER_QUALITY` 和完整歧义记录。
- 无质量 sidecar 的历史数据明确标记 fallback，结果仍可复现。
- 最终 10X IgBLAST 表存在 `barcode` 字段，不再将 barcode 放在 `sequence_id` 字段下。

### IR 专项断言

- 修改前后同一 fixture 的 Datapoint、junction、筛选行数及 `umi_counts` 逐字段一致。
- 对 `preprocess/models` 与 IR 本地 `models` 做 hash/差异检查，确认计算定义未变化。
- 同一样本 UMI 内最高频唯一时结果不变；最高频并列时由质量稳定裁决；质量仍并列时保留歧义。
- 相同 sample_id 位于不同 dataset/relative library 时不发生 UMI 碰撞合并。

### Representative 质量专项断言（10X 与 IR）

- count 不同：高 count 候选胜，即使另一候选单条质量更高。
- count 相同：expected errors 更低的候选胜，标记 `RESOLVED_BY_QUALITY`。
- count、质量均相同：结果可重复且标记 `TIED_AFTER_QUALITY`，候选审计信息完整。
- 历史 FASTA：可兼容运行，标记 `COUNT_FALLBACK_NO_QUALITY`，不会伪造质量值。
- FASTQ header、UMI sidecar 与 representative ID 关联零丢失；任何 unmatched 都导致样本告警/失败而非静默跳过。

### Pig / Base 专项断言

- 与迁移前相比，每样本 IgBLAST 行数、映射数、productive/filtered 数及百分比完全一致。
- Pig 的 species/chain/数据库选择与 Base 的 chain 配置不受路径迁移影响。

## 10. 完成标准

- 10X 终点为以“一条 representative 行代表一个 barcode+UMI 分子”为契约的单细胞链聚类，不执行 bulk preprocessing。
- IR 终点为 preprocessing，且模型计算定义和结果不变。
- Pig/Base 仅完成 stage-first 多数据集编排迁移，生物学处理不变。
- 四套 pipeline 均具备一致的 dataset manifest、阶段进度、资源控制、断点续跑和本地 stop 能力。
