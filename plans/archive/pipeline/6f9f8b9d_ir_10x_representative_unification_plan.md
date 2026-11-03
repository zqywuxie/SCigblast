# IR / 10X representative 阶段统一优化计划

## 目标

让 IR_split 与 10X_split 在代表序列阶段采用同一套处理语义。两者只保留输入获取方式的差异：

- IR：从 split 产生的 UMI sidecar 与 PANDAseq merged FASTA 建立 read ID 关联。
- 10X：从 PANDAseq merged sequence 中定位 TSO，并提取 barcode 与 UMI。

一旦形成标准观察记录，后续分组、代表序列选择、歧义记录、输出字段、断点续跑、并发、错误处理及 IgBLAST 衔接必须一致。实现仍分别保存在两个 pipeline 内，不创建 common 模块。

## 不改变的行为

- 每个 `(sample_key, barcode, UMI)` 只生成一条代表序列。
- 代表序列按精确序列 reads 数选择；最高频并列时按核苷酸序列字典序稳定选择第一条。
- 代表序列 FASTA 保留被选中 read 的原始 PANDAseq header，不再改写成 `REP_*`。
- 最终 IgBLAST TSV 继续仅保留 `productive` 且有有效 `v_call` 的记录。
- 最终 IgBLAST TSV 的 `sequence_id` 继续替换为 barcode。
- 不向最终 IgBLAST TSV 添加字段，不输出跨样本合并文件。
- 不新增公开的 representative candidates 文件。

## 统一数据契约

### 入口观察记录

两个脚本在输入解析后都转换为以下内部记录：

```text
sample_key, barcode, umi, sequence, source_read_id
```

- `sample_key` 使用相对于该阶段输入根目录的 POSIX 路径，并完整保留 `raw 相对目录/pair_stem/sample_id`，防止不同数据批次的同名样本相互覆盖。
- `barcode` 使用实际 barcode sequence；若 IR 只能取得名称，则必须在进入聚合前由 summary 映射成 sequence，不能让两个 pipeline 使用不同主键语义。
- `source_read_id` 只用于输入关联、冲突检查和日志诊断，不增加到公开输出字段。
- barcode、UMI 统一转大写并校验允许字符和长度；非法记录使所属样本失败，不能静默跳过。

### 代表序列算法

两个 pipeline 完全采用同一规则：

1. 按 `(sample_key, barcode, UMI)` 分组。
2. 对组内完整 nucleotide sequence 做精确计数。
3. `total_reads` 为该 UMI 组所有 reads 数。
4. `representative_reads` 为最高频序列 reads 数。
5. `representative_percent = representative_reads / total_reads * 100`。
6. `unique_sequence_count` 为组内不同序列数。
7. 最高频候选仅一条时为 `UNIQUE_TOP`；多条时为 `TIED_TOP`。
8. 并列时按序列字典序选择第一条作为代表，其余按同样顺序写入 `alternative_top_sequences`。
9. `representative_id = REP_ + sha256(sample_key + "\\0" + barcode + "\\0" + umi)[:24]`，仅作为 map、checkpoint 和冲突检查使用。
10. FASTA header 使用获选代表序列的 `source_read_id`。若多条 reads 具有完全相同的获选序列，稳定选择字典序最小的原始 PANDAseq header。
11. 同一 representative ID 若对应不同主键或不同代表序列，阶段必须失败并保留现场。
12. 同一样本内，两个代表序列若得到相同原始 header，必须报冲突，不能静默覆盖。

这里的“原始 header”是 PANDAseq merged FASTA 中 `>` 后的完整文本，输出时原样保留。用于 IgBLAST `sequence_id` 回填的 key，则按 IgBLAST/FASTA 的规则取 header 第一个空白符之前的 ID token；生成 batch map 时必须使用同一解析函数，避免完整 description 与 IgBLAST sequence_id 不一致。

## 统一公开输出

每个 pipeline 保持自己的阶段编号，但 representative 阶段公开产物统一为：

```text
<representative_stage>/
├── representative_fasta/<raw-relative-parent>/<pair_stem>/<sample_id>/<barcode>.fasta
├── representative_map.tsv.gz
└── representative_summary.csv
```

`representative_map.tsv.gz` 字段固定为：

```text
representative_id,sample_id,barcode,umi,total_reads,representative_reads,
representative_percent,unique_sequence_count,top_candidate_count,
ambiguity_status,alternative_top_sequences
```

其中 `sample_id` 字段实际保存 canonical `sample_key`，沿用字段名以避免破坏 IgBLAST 和现有下游脚本。

`representative_summary.csv` 统一按 sample 汇总，至少包含：

```text
sample_id,input_reads,matched_reads,unmatched_reads,invalid_reads,
umi_groups,representative_sequences,tied_umi_groups,status,error
```

FASTA 文件一条 UMI 一条序列，header 为获选代表 read 的原始 PANDAseq header。公开文件不保存全部候选序列明细；并列候选仍编码在 map 的 `alternative_top_sequences` 中。`representative_id` 继续保存在 map 中，用于稳定断点和内部审计，但不再覆盖 FASTA header。

## 安全断点续跑设计

### 样本级事务

两个 pipeline 都改为样本级提交，而不是运行结束时一次覆盖全局文件：

1. 为每个 canonical sample 建立隐藏状态目录 `.state/<sample_hash>/`。
2. 状态中保存输入 fingerprint、配置 fingerprint、预期输出清单和该样本的 map/summary shard。
3. 写入临时 FASTA、map shard、summary shard。
4. 完成格式、数量、REP ID 唯一性检查。
5. 使用 `os.replace` 原子提交各文件。
6. 最后写 `.DONE`；只有 `.DONE` 与 fingerprint、输出清单均一致时才能 skip。
7. 样本提交成功后才允许删除其临时 barcode TSV/spool；失败样本保留全部中间输入以便重跑。

### 全局索引重建

- `representative_map.tsv.gz` 与 `representative_summary.csv` 每次都从所有已提交的样本 shard 确定性重建。
- 重建写入临时文件，完成全局 REP ID 冲突检查后原子替换。
- 新增或修正 error 样本时，只处理该样本；旧样本不会重新聚合，也不会因当前运行只产生新 rows 而从全局 map 消失。
- 这会修复 10X 当前 `existing_rows_data=[]` 导致增量运行覆盖历史代表序列的严重问题。

## IR 输入适配优化

修改 `IR_split/pipeline/06.representative.py`：

- 只扫描 `*_merged.fasta`，明确排除 `_unaligned.fasta` 和临时 FASTA。
- sidecar 必须在同一 canonical sample / pair 范围内解析，禁止把同一 sample 目录下所有 pair 的 sidecar 无条件合并。
- clean 后 read ID 与 sidecar read ID 使用同一个 canonicalization 函数和测试向量。
- 同一 canonical read ID 对应多个 UMI 时标记样本 ERROR，不能把冲突 read 静默丢弃后仍判成功。
- sidecar join 的未匹配数、重复数和冲突数写入 sample summary。
- 使用外部排序/分桶或 SQLite 临时库流式聚合，避免同时在内存中保存完整 sidecar、全部 UMI groups、prepared rows 和 all_rows。
- 加入与 10X 相同的样本级 checkpoint、原子提交及全局 shard 重建。

同步检查 `IR_split/pipeline/03.split_barcode.py` 与 `04.clean_header.sh`：

- 定义并验证 split sidecar ID 到 clean/PANDAseq ID 的可逆对应规则。
- 不再依赖偶然一致的 header 文本。
- 保持 split 的业务逻辑和 raw 路径层级不变。

## 10X 输入适配优化

修改 `10X_split/pipeline/06.split_and_represent.py`：

- phase 1 保留现有 TSO、barcode、UMI 的序列提取逻辑，只把结果规范化为统一观察记录。
- malformed TSV、字段缺失、barcode/UMI 非法均变成样本 ERROR，不能 `continue` 静默少计。
- phase 2 改成与 IR 完全相同的代表序列聚合、字段顺序、summary 和样本级事务实现。
- 修复增量覆盖：禁止以空 `existing_rows_data` 重写全局 map；统一从 committed shards 重建。
- 独立运行的默认 PANDAseq 输入目录修正为实际阶段目录 `05.pandaseq`，并由 runner/env 显式传入作为首选。
- 成功提交 sample shard 后再删除该 sample 的 `_barcodes` 临时文件；任何失败时保留。

## 并发与内存

- 并发单位统一为 sample；同一 sample 内默认单进程流式聚合，避免多进程复制大型字典。
- 两边提供同名语义的 representative 并发参数，例如 `REP_WORKERS`，分别写在各自 `00.pipeline_config.env`。
- worker 启动前和运行期间使用现有共享服务器内存监控策略；可用内存低于 150 GB 时停止派发新任务并终止本阶段，已提交样本保持有效。
- 进程预算同时受 `REP_WORKERS`、CPU 配额、单 worker 估算内存和 300 GB pipeline 最大内存限制约束。
- 一个样本失败时，其余已启动/可安全执行的样本继续；阶段结束返回非零，后续 IgBLAST 不启动。
- 两个 pipeline 都输出统一进度：`completed/failed/total/percent/sample/elapsed/peak_memory`。

## IgBLAST 衔接统一

修改两个 `07.work_igblastn.sh`，保持文件各自维护但行为相同：

- 只消费已提交且 `status=OK` 的 representative sample。
- 构建 batch 时从 canonical map 校验每个 FASTA 原始 header 均能唯一映射到 barcode。
- batch map 改为 `original_header -> barcode`；IgBLAST 原始输出的 `sequence_id` 应与该 header 一致，后处理后仍替换为 barcode。
- map shard 内同时保存 `representative_id` 与实际 FASTA header 的内部对应关系，以支持 checkpoint、去重和审计；不向最终 AIRR TSV 新增字段。
- 同一个 batch 中重复原始 header、header 缺失、一个 header 映射多个 barcode、重复或冲突 REP ID 时硬失败。为避免不同样本原始 header 重名，IgBLAST batch 必须按 sample 隔离构建，不能跨样本拼接。
- batch manifest 同时记录输入 fingerprint、代表序列配置 fingerprint、数据库/链配置 fingerprint 和输出清单；IR 补齐与 10X 等价的校验。
- 样本链选择只读取 match summary 中 `status=OK` 的行；IR 与 10X 统一。
- 最终仍按样本目录输出 `TCR.tsv`、`BCR.tsv` 和同级 mapping summary，不跨样本合并。

## Runner 与配置调整

分别修改：

- `IR_split/pipeline/run_ir_pipeline.sh`
- `10X_split/pipeline/run_10x_pipeline.sh`
- 两边各自的 `00.pipeline_config.env`

要求：

- runner 显式传递 representative 输入、输出、worker、内存和 resume 配置。
- 阶段完成判断不只检查全局 `.DONE`，而是验证 manifest 中所有当前 `status=OK` 样本均有有效 committed shard。
- match 后新增/修复样本时，只将缺失或 fingerprint 变化的 sample 纳入下游。
- representative 阶段非零时不启动 IgBLAST，但不清理任何已提交样本和失败样本所需的输入。
- 两边的 `stop.sh` 继续分别维护，并验证能识别 representative 主进程、worker 和 IgBLAST 子进程。

## 迁移策略

1. 新代码首次运行时读取现有 `representative_map.tsv.gz` 与 FASTA，验证完整性。
2. 能够唯一还原 sample/barcode/UMI 且 FASTA 存在的旧记录，导入为 committed shard，避免全部重算。
3. 缺少 UMI、REP ID 冲突、FASTA 缺失或 sample path 不规范的旧记录不自动接纳；仅将对应样本标记为待重算。
4. 旧全局 map 在新 map 成功原子提交前保持不变。
5. 不自动删除旧 `_barcodes`、sidecar、FASTA 或 `.batches`；确认新 manifest 后再由用户控制清理。

## 文件级实施顺序

1. 在两个 representative 脚本内分别实现相同的 schema、ID、tie policy、校验和原子 shard 写入函数。
2. 修正 IR sidecar 的 pair 范围与 canonical read ID 关联。
3. 修正 10X malformed input 和历史 rows 覆盖问题。
4. 统一两个阶段的 summary、目录结构、进度和退出码。
5. 更新两个 runner/config 的参数与 stage completion 判定。
6. 对齐两个 IgBLAST 脚本的 committed sample 发现、map 校验、chain status 过滤和 manifest fingerprint。
7. 最后才启用成功样本的可选中间文件清理。

## 验证方案

### 单元/小样本测试

- 单 UMI 单序列：reads 与百分比正确。
- 单 UMI 多序列：选择最高频序列。
- 最高频并列：稳定选择字典序第一条，ambiguity 字段完整。
- 同一序列出现在多个 UMI：生成多条 REP 记录，每个 UMI 计数一次。
- 非法 barcode/UMI、malformed TSV、sidecar 缺失、read ID 冲突：样本 ERROR 且阶段最终非零。
- 两个 pipeline 对同一 canonical observation fixture 产生字节级一致的 map、summary 和 FASTA。

### 断点续跑测试

- 中途 kill：已提交 sample 可 skip，未提交 sample 重跑，无半文件被接受。
- 首次存在 error sample，补齐后重跑：只处理补齐样本，全局 map 保留旧样本。
- phase 2 完成后临时 TSV 已删除：仍可从 shard 重建全局 map。
- 修改代表序列配置或输入：仅 fingerprint 变化的样本失效。
- 删除一个预期 FASTA：checkpoint 校验失败并只重建对应样本。

### IgBLAST 集成测试

- 每个样本 batch 内的 FASTA 原始 header 唯一，且全部能映射到 barcode。
- representative FASTA header 与对应 PANDAseq merged FASTA header 完全一致，没有 `REP_*`、barcode、UMI 或其他附加内容。
- 最终 `TCR.tsv/BCR.tsv` 仅包含 productive 且有 v_call 的记录。
- 最终 `sequence_id` 为 barcode，没有新增额外列或首行注释。
- 每个结果保留在 `raw-relative-parent/pair_stem/sample_id` 下，不发生跨样本合并。
- 一个样本 IgBLAST 失败时其他样本继续，本阶段结束非零且后续阶段不启动。

## 完成标准

- 除 acquisition adapter 外，IR 与 10X 的代表序列行为、输出 schema、错误语义、resume 和进度一致。
- 增量运行不会删除或覆盖既有已完成样本。
- 任何输入损坏、ID 冲突或 map 缺失都不会被静默当作成功。
- 代表序列和最终 IgBLAST 输出目录完整保留 raw 路径、pair stem 与 sample ID 层级。
- 最终下游格式保持兼容：`sequence_id=barcode`，无额外业务字段，无跨样本合并。
