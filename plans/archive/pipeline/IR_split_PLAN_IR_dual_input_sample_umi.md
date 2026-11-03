# IR split 双输入与样本内 UMI 代表序列优化计划

## 最终规则

IR pipeline 支持两类 FASTQ，但只维护一套下游处理。

### 未拆分数据

R1 结构固定为：

```text
[10 bp UMI][8 bp primer barcode][insert]
```

样本身份仍由 match 阶段确定：

```text
Note 数据批次边界
→ 文件名中的 Sample ID 优先匹配
→ 未命中时在同一个 Note 批次内匹配 Dual Index
→ 从 submission 的 Barcode 候选字段取得 8 bp barcode sequence
```

split 时用 barcode sequence 检查 R1 第 11–18 bp，匹配后去掉 R1 前 18 bp，并把前 10 bp UMI 同时写入 R1/R2 header。

新 header 统一为：

```text
@A01757:148:HY252DRX2:1:2101:10348:1031#TGAATACAAT 1:N:0:ATTACTCG
```

即在原始 header 第一个 token 后直接添加 `#<UMI>`，不再使用 `#UMI:<UMI>`；空格后的 CASAVA 信息保持不变。

### 已拆分数据

header 已含：

```text
#TGAATACAAT
```

这类数据不再执行 match、fastp 和 barcode split。输入准备阶段只完成：

- R1/R2 配对验证；
- 从两端 header 解析 UMI；
- R1/R2 read ID 和 UMI 一致性验证；
- 生成标准 UMI sidecar；
- 写入统一输入 manifest；
- 不复制、不改写原始 FASTQ。

### 代表序列

代表序列在每个样本内部按相同 UMI 提取：

```text
(dataset_id, sample_id, umi)
```

`dataset_id` 只隔离不同数据批次；实际生物学逻辑是 `sample_id + umi`。primer barcode、lane 和源文件都不参与分组。同一样本来自多个 barcode、lane 或文件时，需要先跨分区汇总相同 UMI。

## 优化后的阶段流程

```text
未拆分数据：
01 match sample（Note + Sample ID / Dual Index + Barcode）
→ 02 fastp
→ 03 IR input preparation：拆样、剪去UMI+barcode、写#UMI与sidecar
→ 04 clean header
→ 05 PANDAseq
→ 06 sample内按UMI提取代表序列
→ 07 IgBLAST

已拆分数据：
03 IR input preparation：校验#UMI、生成sidecar、登记输入
→ 04 clean header
→ 05 PANDAseq
→ 06 sample内按UMI提取代表序列
→ 07 IgBLAST
```

这里流程图中的“写 `#UMI`”表示写 `#<UMI sequence>`，不是字符串 `#UMI:`。

## 1. 配置双输入模式

修改 `IR_split/pipeline/00.pipeline_config.env`，增加 IR 输入模式与路径配置。推荐显式配置，禁止仅凭 header 自动猜测：

```text
raw
presplit
```

若一次运行需要同时处理两类数据，使用一份输入清单，每行记录：

```text
dataset_id
input_dir
input_mode
sample_id
submission_source
```

`sample_id` 对 raw 可以为空，由 match 产生；presplit 必须能从清单或可靠目录层级确定，不能把文件名随意当作样本。

## 2. 统一第 03 阶段

将 `IR_split/pipeline/03.split_barcode.py` 重构为统一输入准备脚本；可保留文件名以降低迁移成本，也可在确认调用点全部更新后改名为 `03.prepare_ir_input.py`。

### raw 分支

- 读取 `01.match` 中 `status=OK` 的 sample/barcode mapping；
- 同一个原始 pair 可以对应多个 sample/barcode；
- 从 R1 提取 10 bp UMI；
- 用后续 8 bp 与该样本的 barcode sequence 匹配；
- 匹配后剪去 R1 前 18 bp；
- R1/R2 首 token 均追加 `#<UMI>`；
- 未匹配 reads 直接丢弃；
- 同步写 `.umi.tsv.gz`。

### presplit 分支

流式同步读取 R1/R2。header 解析规则为：

```regex
#([ACGTN]{10})$
```

匹配对象是第一个空白前的 header token。迁移期可只读兼容旧的 `#UMI:<UMI>`，但所有新 raw 输出只写 `#<UMI>`。

逐条验证：

- R1/R2 FASTQ 四行结构正常；
- sequence 与 quality 等长；
- 删除 `#<UMI>`、`/1`、`/2` 后 read key 相同；
- 两端 UMI 相同；
- UMI 长度等于 `SCIGBLAST_IR_UMI_LENGTH`；
- 两端记录数一致。

验证通过后只生成 sidecar 和 manifest，不重写 FASTQ。输入标记 `ownership=external`，cleanup 不得删除。

## 3. 建立统一 active manifest

第 03 阶段输出：

```text
03.IR_split_output/ir_input_manifest.tsv
```

字段至少包括：

```text
dataset_id
sample_id
source_mode
pair_id
source_partition_id
source_barcode_name
source_barcode_sequence
r1_path
r2_path
umi_sidecar_path
logical_relpath
input_fingerprint
ownership
status
error
```

raw 拆分后的路径指向 pipeline 自有 FASTQ；presplit 路径直接指向外部 FASTQ。04–07 只处理 manifest 中 `status=OK` 的行，不再递归猜测所有目录。

`logical_relpath` 统一使用：

```text
<rawdata相对目录>/<原始pair_stem>/<sample_id>
```

因此两类数据在下游都保持“源文件/样本”的层级，不会平铺，也不会把同 lane 的样本合并。

## 4. sidecar 与 clean header 的契约

修改：

- `IR_split/pipeline/03.split_barcode.py`
- `IR_split/pipeline/04.clean_header.sh`
- `IR_split/pipeline/06.representative.py`

UMI 必须在 clean 前落盘到 sidecar，因为 clean 会删除 header 中的 `#...`。

统一 sidecar：

```text
read_id<TAB>umi
```

需要集中实现一个 canonical read ID 规则，同时用于：

- 从 raw/presplit header 写 sidecar；
- clean 后 ID 规范化；
- PANDAseq FASTA 与 sidecar join。

不能让 03 与 06 各自维护不同的正则规则。若继续使用 shell clean，可由 clean 同步转换 sidecar key，使其与实际 clean 输出 ID 完全一致。

## 5. 按样本内部 UMI 重构 representative

修改 `IR_split/pipeline/06.representative.py`：

1. 按 `(dataset_id, sample_id)` 收集该样本全部 merged FASTA 和 sidecar；
2. 跨 barcode、lane 和文件累加：

   ```python
   groups[umi][sequence] += 1
   ```

3. 全部来源处理完成后，每个 UMI 只选择一条代表序列；
4. 最高 reads 序列获选；
5. 最高频并列时按固定字典序选择，并记录 `TIED_TOP` 和其他候选；
6. state key 和 representative ID 改为 `(dataset_id, sample_id, umi)`，删除 barcode；
7. barcode 仅以 `source_barcodes` 审计字段保留。

每个 UMI 输出：

```text
representative_id
dataset_id
sample_id
umi
total_reads
representative_reads
representative_percent
unique_sequence_count
top_candidate_count
ambiguity_status
source_barcodes
source_files
```

`alternative_top_sequences` 建议放入单独 gzip ambiguity 文件，避免 durable state 保存大量完整序列。

## 6. IgBLAST 映射同步

修改 `IR_split/pipeline/07.work_igblastn.sh` 的 representative map 读取逻辑：

- IgBLAST query 使用稳定、唯一的 `representative_id`；
- 不再用 primer barcode 覆盖 `sequence_id`；
- 通过 representative map 关联 sample、UMI、总 reads 和代表 reads；
- productive 和 `v_call` 筛选规则保持不变；
- 不修改 IgBLAST 原始字段含义。

## 7. 断点续跑

每个输入 pair/sample 保存：

```text
dataset_id + pair_id + sample_id + source_mode
输入路径、size、mtime
UMI长度
header parser版本
barcode sequence（仅raw）
```

只有 fingerprint、DONE state、R1/R2、sidecar 和 manifest 行一致时跳过。

- 新补 submission/barcode：只处理新变为 OK 的样本；
- 已完成 raw 样本：不重复 fastp/split；
- 已验证 presplit 样本：不重新扫描，除非输入指纹变化或 sidecar 缺失；
- 同一样本新增一个文件/barcode 分区：重新计算该样本的 representative，因为 UMI 计数和代表序列可能变化；
- 其他样本继续复用；
- ERROR 行保留，但不阻断其他 OK 样本进入下游。

旧 `#UMI:<UMI>` 输出和旧 `(sample,barcode,umi)` representative state 不能直接当作新版结果。header 可以兼容读取；representative state 必须升级 schema 并从现有 merged FASTA + sidecar 重建 06/07。

## 8. 修改文件

- `IR_split/pipeline/00.pipeline_config.env`
- `IR_split/pipeline/run_ir_pipeline.sh`
- `IR_split/pipeline/03.split_barcode.py`（或迁移为 `03.prepare_ir_input.py`）
- `IR_split/pipeline/04.clean_header.sh`
- `IR_split/pipeline/05.work_pandaseq.sh`
- `IR_split/pipeline/06.representative.py`
- `IR_split/pipeline/07.work_igblastn.sh`
- IR pipeline 测试 fixture 与测试脚本

## 9. 验证方案

使用小型 paired FASTQ 完整跑通两条入口：

1. raw R1 `[10bp UMI][8bp barcode][insert]` 能按 Dual Index + Barcode 映射并正确剪去 18 bp；
2. raw 输出 header 精确为 `@原ID#TGAATACAAT 原空格后内容`，不出现 `#UMI:`；
3. presplit `#TGAATACAAT` 不进入实际拆分，直接生成 sidecar 并进入 clean；
4. R1/R2 UMI 不同、read ID 不同或记录数不同会标记 ERROR；
5. 同一样本、相同 UMI、不同 barcode/file 的 reads 会合为一个代表组；
6. 不同样本的相同 UMI 不会合并；
7. 代表序列的 `total_reads`、`representative_reads`、比例和并列候选正确；
8. clean 删除 `#UMI` 后仍能通过 sidecar 与 merged FASTA 准确关联；
9. 原样重跑跳过完成项；新增或修复样本只重跑受影响样本；
10. presplit 外部 FASTQ 不被改写、复制或 cleanup 删除；
11. IgBLAST 结果能由 representative ID 唯一关联回 sample、UMI 和 reads 支持数。
