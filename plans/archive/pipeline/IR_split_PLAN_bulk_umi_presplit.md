# IR bulk UMI 与预拆分数据接入优化方案

## 目标与生物学定义

IR 是 bulk 数据，8 bp primer barcode 只负责把混样 FASTQ 拆到具体样本，不能作为细胞身份，也不能参与代表序列的分组。

代表序列的正确分组键为：

```text
(library_id, sample_id, umi)
```

不能在整个项目内只按 UMI 合并，因为不同样本或不同文库可能偶然使用相同的 10 bp UMI。若同一样本分布在多个 primer barcode、lane 或原始文件中，则这些分区必须先汇总，再为相同 UMI 选择一个代表序列。

字段含义应固定为：

- `total_reads`：该 sample/library/UMI 下全部 merged reads 数；
- `representative_reads`：最终被选中序列的 reads 数；
- `representative_percent`：`representative_reads / total_reads`；
- `umi_count`：某个下游重排结果获得的不同 UMI 数，一个代表组只贡献 1；
- `barcode_sequence`：仅作为拆样和审计信息，不是分组键。

## 当前实现的问题

涉及文件：

- `pipeline/03.split_barcode.py`
- `pipeline/04.clean_header.sh`
- `pipeline/05.work_pandaseq.sh`
- `pipeline/06.representative.py`
- `pipeline/07.work_igblastn.sh`
- `pipeline/run_ir_pipeline.sh`

`06.representative.py` 在单个 merged FASTA 内使用 `groups[umi][sequence] += 1`，局部计数本身是按 UMI；但每个 FASTA 独立处理，持久化 key 仍是 `(sample_id, barcode, umi)`，代表 ID 的 hash 也包含 barcode。因此，同一样本跨 barcode/文件分区的相同 UMI 不会合并。

`04.clean_header.sh` 会为 PANDAseq 兼容性删除 header 中 `#...` 标签。预拆分数据若直接进入 clean，示例中的 `#TGAATACAAT` 会消失，representative 阶段无法恢复 UMI。

## 推荐后的两条流程

```text
原始混样数据：
01 match -> 02 fastp -> 03 barcode拆样并提取UMI -> 04 clean
         -> 05 PANDAseq -> 06 sample内按UMI选代表 -> 07 IgBLAST

外部预拆分数据：
01 match -> 02 fastp -> 03 validate/import并从header提取UMI sidecar -> 04 clean
         -> 05 PANDAseq -> 06 sample内按UMI选代表 -> 07 IgBLAST

复用本pipeline既有拆分结果：
03 validate existing FASTQ + sidecar -> 04 clean -> 05 -> 06 -> 07
```

外部预拆分数据仍执行 match 和 fastp；stage 03 只验证/导入已有 UMI 标记，不再次执行 barcode split。

## 1. 用统一输入清单描述 raw 与预拆分数据

在 `00.pipeline_config.env` 只保留一套数据集入口，推荐指向一个 TSV 清单，而不是依赖目录自动猜测：

```text
dataset_id  library_id  input_dir  input_mode  sample_id  submission_source  fastp_policy
```

`input_mode` 允许：

- `raw`：原始混样 FASTQ；
- `presplit_external`：外部已按样本拆好的 FASTQ；
- `reuse_split_output`：复用当前 pipeline 已有的 stage 03 结果。

模式必须显式指定。header 自动识别只能用于校验，不能决定数据走哪条分支，避免普通 read ID 偶然包含 `#10bp` 时走错流程。

若不希望引入用户维护的 TSV，可以在 env 中维护一组输入目录，并为整个批次设置统一模式；但这将无法在一次运行中安全混合 raw 与 presplit，优先推荐清单方式。

## 2. 将 stage 03 统一为 IR 输入准备阶段

保留阶段编号 03，可将 `03.split_barcode.py` 重构或改名为 `03.prepare_ir_input.py`。它包含三种分支，但对下游发布完全相同的 manifest。

统一输出：

```text
03.IR_split_output/
├── ir_input_manifest.tsv
├── sidecars/
├── .state/
└── raw模式产生的拆分FASTQ
```

`ir_input_manifest.tsv` 每行表示一个有效的样本分区，至少包含：

```text
dataset_id
library_id
sample_id
chain
pair_id
source_mode
source_partition_id
source_barcode_name
source_barcode_sequence
r1_path
r2_path
umi_sidecar_path
logical_relpath
input_fingerprint
parser_version
ownership
status
error
```

04、05、06 不再递归扫描整个旧目录，而只消费 manifest 中 `status=OK` 的行。这可避免旧输出、已变为 ERROR 的样本或其他批次文件被误带入下游。

### raw 分支

保留当前逻辑：从 R1 读取 `[10 bp UMI][8 bp barcode][insert]`，用 barcode 拆到样本，去掉 UMI+barcode，将 UMI 写入 R1/R2 临时 header，并生成标准 sidecar。

### presplit_external 分支

对以下格式流式解析：

```text
@A01757:148:HY252DRX2:1:2101:10348:1031#TGAATACAAT 1:N:0:ATTACTCG
```

从第一个空白前的 token 末尾提取：

```regex
#([ACGTN]{10})$
```

同时兼容当前 pipeline 的 `#UMI:TGAATACAAT`，但统一输出 sidecar：

```text
read_id<TAB>umi
```

导入时逐条同步读取 R1/R2，并校验：

- R1/R2 记录数一致；
- 删除 UMI 标签和 `/1`、`/2` 后 read key 一致；
- 两端 UMI 一致；
- UMI 长度和字符合法；
- FASTQ 四行结构、sequence/quality 长度合法。

该分支只写 gzip sidecar、manifest 和状态文件，不复制、不改写外部大 FASTQ。`ownership=external`，任何 cleanup 都不得删除这些源文件。

### reuse_split_output 分支

复用既有 R1、R2 和 sidecar，但必须验证三者存在、记录数可对应、路径结构可映射到 sample/library。缺 sidecar 时不能跳过拆分，因为 clean 后无法恢复 UMI。

## 3. 统一 header/canonical read ID 规则

把以下逻辑集中在一个 IR 专用 helper，供 stage 03 和 06 同时使用：

- 解析 `#UMI:<umi>`；
- 解析末尾 `#<umi>`；
- 删除 UMI 标签；
- 删除 `/1`、`/2`；
- 执行当前仪器坐标规范化；
- 返回 canonical read key。

`04.clean_header.sh` 仍可去掉 `#...` 以满足 PANDAseq，但必须保证 clean 使用的 canonical 规则与 sidecar key 完全一致。不要依赖 UMI 标签穿过 PANDAseq。

## 4. clean 与 PANDAseq 按 manifest 工作

`04.clean_header.sh` 按 `ir_input_manifest.tsv` 枚举 R1/R2，并将输出写到：

```text
04.clean_data/<logical_relpath>/
```

`05.work_pandaseq.sh` 读取 clean manifest 或同一个 logical path，输出：

```text
05.pandaseq/<logical_relpath>/*_merged.fasta
```

这样 raw 与外部预拆分数据会得到相同的下游目录结构，也不会把一个 lane 下多个样本误认为一个样本。

## 5. 重构 representative 为跨分区 sample 内 UMI 聚合

`06.representative.py` 应先按 `(library_id, sample_id)` 收集所有 merged FASTA 和 sidecar，再统一累加：

```text
groups[umi][merged_sequence] += read_count
```

只有所有 source partition 都读完后，才为每个 UMI 选择代表序列。

选择规则保持：

1. 统计该 UMI 下每条 merged sequence 的 reads 数；
2. 选择 reads 数最高的序列；
3. 若并列，按固定字典序选择，标记 `TIED_TOP`；
4. 其余并列候选写入独立的 ambiguity 文件，避免主 state 因保存完整长序列膨胀。

新的 state key 和代表 ID：

```text
state_key = (library_id, sample_id, umi)
representative_id = hash(schema_version, library_id, sample_id, umi)
```

barcode 从 identity 中删除，只保留：

```text
source_barcodes
source_partitions
source_partition_count
```

推荐输出：

```text
06.representative/<library_id>/<sample_id>/representatives.fasta
06.representative/representative_map.tsv.gz
06.representative/representative_summary.csv
06.representative/representative_ambiguity.tsv.gz
```

published FASTA 可继续保留被选中 read 的原始 PANDAseq header；但 IgBLAST 临时批次应使用唯一的 `representative_id` 作为 query ID，避免不同文件中相同仪器 header 冲突。

## 6. 同步 IgBLAST map 与最终身份

`07.work_igblastn.sh` 必须读取新版 representative map。IR primer barcode 不再覆盖 `sequence_id`。

最终建议：

- `sequence_id` 使用稳定的 `representative_id`；
- 相邻输出 `representative_metadata.tsv.gz`，按 `representative_id` 保存 `library_id`、`sample_id`、`umi`、`total_reads`、`representative_reads`、ambiguity 等；
- 若最终 TSV 需要直接计算 UMI count，可在后处理 join metadata，但不要把 `representative_reads` 错当成 `umi_count`。

原始 IgBLAST 字段和 productive/v_call 筛选逻辑保持不变。

## 7. 断点续传与局部失效

每个 logical pair 保存输入 fingerprint：

```text
absolute path + size + mtime_ns
dataset/library/sample
source_mode
UMI length
parser version
相关 barcode 配置（仅 raw）
```

仅在 fingerprint、DONE state、R1/R2、sidecar 和 manifest 行全部一致时跳过。

行为应为：

- 已完成 pair：跳过；
- 新增或修复后的 pair：只处理该 pair；
- 某个 sample 新增一个 barcode/file 分区：重新构建该 sample/library 的全部 representative 结果，因为原有 UMI 的总 reads 和代表选择可能改变；
- 其他 sample 的 representative 和 IgBLAST 结果保持跳过；
- ERROR 行保留在 summary，但不阻断 status=OK 样本继续下游。

旧 representative state 的 key 含 barcode，不能简单改列名复用。将 schema 升级到 v2，从已有 stage 03/05 结果重建 06 和 07；旧结果先保留，验证完成后再清理。

## 8. 实现顺序

1. 在 `00.pipeline_config.env` 增加显式输入模式/数据集清单配置。
2. 抽出统一 UMI header 与 canonical ID helper。
3. 将 stage 03 改为 raw split、external presplit import、existing split reuse 的统一入口。
4. 发布并验证 `ir_input_manifest.tsv`，使 ERROR 行不阻断 OK 行。
5. 修改 04/05，只读取 active manifest 并保持 `logical_relpath`。
6. 重写 06 的聚合边界、state key、代表 ID、map 和 ambiguity 输出。
7. 修改 07 的 representative map 读取及 query ID 映射。
8. 升级 stage/state schema，使旧 barcode-keyed representative 结果失效。
9. 更新 stop、cleanup 和阶段 marker，使 external 文件永不进入删除范围。

## 9. 验收测试

至少使用小型合成 paired FASTQ 覆盖：

1. 同一 library/sample/UMI 分布在两个 barcode 分区，序列 A 计数 3+1、序列 B 计数 2+4：最终只有一个 UMI 代表组，`total_reads=10`，选择 B，`representative_reads=6`。
2. 不同 sample 使用相同 UMI：不得合并。
3. 不同 library 使用相同 sample_id 和 UMI：不得合并。
4. `#UMI:TGAATACAAT` 与 `#TGAATACAAT` 均能生成相同 sidecar 语义。
5. R1/R2 UMI 不一致、read key 不一致或记录数不一致：该 pair 为 ERROR，其他 pair 继续。
6. clean 删除 header UMI 后，PANDAseq 结果仍能通过 sidecar准确关联。
7. 并列最高序列：稳定选择、`TIED_TOP` 和 ambiguity 候选正确。
8. 原样重跑：所有有效 pair/sample 跳过，输出 mtime 不变。
9. 新增一个分区：只重建受影响 sample 的 06/07。
10. external presplit 数据没有被复制、改写或 cleanup 删除。
11. IgBLAST 输出可由 `representative_id` 唯一关联回 sample、UMI 和 reads 支持数。

## 关键决策

- 代表序列不是“全局相同 UMI”聚合，而是 `library + sample + UMI` 聚合。
- barcode 只参与 raw 拆样，不参与代表序列身份。
- 已拆分 header 中的 UMI 必须在 clean 前落入 sidecar。
- 预拆分 external 文件只读引用，不复制大 FASTQ。
- stage 04 之后只依赖统一 active manifest，raw 与 presplit 共用同一套处理逻辑。
