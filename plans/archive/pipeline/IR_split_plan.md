# IR split 最终优化计划

## 一、确定的数据类型与核心规则

IR 是 bulk 分析。primer barcode 只用于未拆分混样数据的样本拆分，不代表细胞身份。

IR pipeline 支持两类输入：

1. 未拆分数据：R1 为 `[10 bp UMI][8 bp barcode][insert]`；
2. 已拆分数据：FASTQ header 已含 `#<10 bp UMI>`，例如：

   ```text
   @A01757:148:HY252DRX2:1:2101:10348:1031#TGAATACAAT 1:N:0:ATTACTCG
   ```

两类数据都必须执行 match。区别是：

- 未拆分数据的 match 用于确定样本、barcode 和 chain，并驱动实际拆分；
- 已拆分数据的 match 只用于检查样本身份、R1/R2 完整性、submission 完整性和 chain，不再驱动 barcode 拆分。

## 二、最终流程

### 1. 未拆分数据

```text
01 match sample
→ 02 fastp
→ 03 提取UMI、检查barcode并拆分
→ 04 clean header
→ 05 PANDAseq
→ 06 样本内按UMI提取代表序列
→ 07 IgBLAST
```

### 2. 已拆分数据

```text
01 match sample（仅检查样本完整性和chain）
→ 02 fastp
→ 03 校验预拆分FASTQ并生成UMI sidecar
→ 04 clean header
→ 05 PANDAseq
→ 06 样本内按UMI提取代表序列
→ 07 IgBLAST
```

已拆分数据明确跳过：

- 03 中实际的 barcode read 筛选和拆分；
- 但不跳过 02 fastp。

## 三、match 阶段

修改 `pipeline/01.match_sample.py`，使 manifest 明确记录：

```text
dataset_id
source_mode
pair_id
sample_id
chain_raw
igblast_chains
dual_index
barcode_name
barcode_sequence
r1_path
r2_path
match_method
status
error
```

`source_mode` 为：

```text
raw
presplit
```

### 未拆分数据的样本定位

保持当前安全边界：

```text
Note限定数据批次
→ 文件名中Sample ID优先
→ 未命中时只在相同Note批次内匹配Dual Index
→ 从submission的Barcode候选字段取得8 bp barcode sequence
```

同一个 raw pair 可以匹配多个样本和多个 barcode，summary 为每个样本分别输出记录。

### 已拆分数据的完整性检查

仍读取 submission 并检查：

- FASTQ 是否能映射到唯一 `sample_id`；
- Note 是否覆盖实际数据路径；
- Sample ID 或 Dual Index 是否在该 Note 批次内匹配；
- R1/R2 是否齐全；
- submission 中的 chain 是否存在并可解析；
- submission 有样本但磁盘无 FASTQ；
- 磁盘有 FASTQ 但 submission 无样本；
- 是否存在重复或冲突样本记录。

已拆分数据的 barcode 字段可以保留用于审计，但不得用于下游筛 read，也不得参与代表序列分组。

match 中的 ERROR 行保留在 summary，只有 `status=OK` 的样本进入下游；ERROR 不阻断其他 OK 样本。

## 四、未拆分数据的 split 与 header

修改 `pipeline/03.split_barcode.py`：

1. 从 R1 前 10 bp 提取 UMI；
2. 用第 11–18 bp 与该样本的 barcode sequence 匹配；
3. barcode 不匹配的 read 直接丢弃；
4. 匹配后剪去 R1 前 18 bp；
5. 将相同 UMI 写入 R1/R2 header；
6. 同步生成 `.umi.tsv.gz` sidecar。

新 header 固定为：

```text
@原始read_id#TGAATACAAT 原始空格后内容
```

不再生成：

```text
#UMI:TGAATACAAT
```

迁移期允许读取旧的 `#UMI:<UMI>`，但所有新输出只写 `#<UMI>`。

## 五、已拆分数据的输入准备

第 03 阶段增加 `presplit` 分支。该分支不重新拆分、不改写和不复制外部 FASTQ，只进行流式校验并生成 sidecar。

UMI 从 header 第一个空白前 token 的末尾解析：

```regex
#([ACGTN]{10})$
```

校验内容：

- R1/R2 FASTQ 四行结构正确；
- sequence 和 quality 等长；
- 去掉 `#<UMI>`、`/1`、`/2` 后两端 read ID 相同；
- R1/R2 中的 UMI 相同；
- UMI 长度符合 `SCIGBLAST_IR_UMI_LENGTH`；
- R1/R2 记录数一致。

sidecar 统一格式：

```text
read_id<TAB>umi
```

外部 FASTQ 标记为 `ownership=external`，cleanup 永远不能删除。

## 六、统一 stage 03 active manifest

第 03 阶段发布：

```text
03.IR_split_output/ir_input_manifest.tsv
```

字段包括：

```text
dataset_id
source_mode
pair_id
sample_id
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

- raw 行指向 pipeline 自己生成的拆分 FASTQ；
- presplit 行直接指向外部已拆分 FASTQ；
- 04–07 只读取 manifest 中 `status=OK` 的记录；
- 不再递归扫描目录并猜测有效输入。

统一下游相对结构：

```text
<rawdata相对目录>/<原始pair_stem>/<sample_id>
```

## 七、clean、PANDAseq 与 UMI sidecar

修改：

- `pipeline/04.clean_header.sh`；
- `pipeline/05.work_pandaseq.sh`；
- `pipeline/06.representative.py`。

clean 会删除 header 的 `#<UMI>` 以兼容 PANDAseq，因此 UMI 必须在 clean 前写入 sidecar。

03、04、06 必须共享同一套 canonical read ID 规则。若 clean 会改变 read ID，必须同步转换 sidecar key，使：

```text
PANDAseq merged FASTA read_id
↔ UMI sidecar read_id
```

完全一致。representative 阶段不得再从 clean 后 header 猜 UMI。

## 八、样本内部按 UMI 提取代表序列

修改 `pipeline/06.representative.py`。

代表序列分组键为：

```text
(dataset_id, sample_id, umi)
```

本质为一个样本内部按相同 UMI 分组。`dataset_id` 只防止不同独立批次中的同名样本被错误合并。

同一 `sample_id` 来自多个 barcode、lane 或文件时，必须先汇总全部分区：

```python
groups[umi][sequence] += 1
```

选择规则：

1. 统计该样本、该 UMI 下所有 sequence 的 reads 数；
2. 选择 reads 数最高的 sequence；
3. 并列最高时按固定字典序选择；
4. 标记 `TIED_TOP` 并保留其他最高频候选。

每个 UMI 记录：

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

barcode 仅保留在 `source_barcodes` 中用于审计，不参与 key、hash 或代表序列选择。

## 九、IgBLAST 的 sequence_id

修改 `pipeline/07.work_igblastn.sh` 及其后处理逻辑。

IR 最终不再用 primer barcode 替换 `sequence_id`。

最终规则：

```text
sequence_id = representative_id
```

并通过 representative map 关联：

```text
representative_id
→ sample_id
→ umi
→ total_reads
→ representative_reads
```

IgBLAST 原始字段意义、productive 筛选和 `v_call` 非空筛选保持不变。

该修改仅适用于 IR bulk；10X 中 barcode 代表细胞身份，不随本计划修改。

## 十、断点续跑

状态最小单位为：

```text
dataset_id + pair_id + sample_id + source_mode
```

fingerprint 包含：

- R1/R2 路径、size、mtime；
- sample_id；
- source mode；
- UMI 长度；
- header parser 版本；
- barcode sequence（仅 raw）。

行为要求：

- 已完成且 fingerprint 未变：跳过；
- 新补 submission/barcode 后，只处理新变为 OK 的样本；
- presplit sidecar 已验证且输入未变：不重新扫描 FASTQ；
- 同一样本新增 source 文件或 barcode 分区：只重建该样本的 representative 和 IgBLAST；
- 其他已完成样本不受影响；
- ERROR 样本保留并允许以后补齐重跑。

旧 `(sample_id, barcode, umi)` representative state 不得直接复用。升级 schema 后，从已有 PANDAseq FASTA 和 sidecar 重建 stage 06–07。

## 十一、需要修改的文件

- `IR_split/pipeline/00.pipeline_config.env`
- `IR_split/pipeline/01.match_sample.py`
- `IR_split/pipeline/03.split_barcode.py`
- `IR_split/pipeline/04.clean_header.sh`
- `IR_split/pipeline/05.work_pandaseq.sh`
- `IR_split/pipeline/06.representative.py`
- `IR_split/pipeline/07.work_igblastn.sh`
- `IR_split/pipeline/run_ir_pipeline.sh`
- IR 小型测试 fixture 和测试脚本

## 十二、验收测试

1. raw 数据通过 Note 内的 Sample ID 或 Dual Index 定位样本，并读取正确 barcode；
2. raw R1 正确提取 10 bp UMI、匹配 8 bp barcode并剪去前18 bp；
3. 新 header 精确为 `@原ID#TGAATACAAT 原空格后内容`；
4. presplit 数据仍执行 match 完整性检查，并先经过 fastp；stage 03 仅验证/导入已有 UMI 标记，不再次执行 barcode 剪切；
5. presplit `#TGAATACAAT` 正确生成 sidecar；
6. R1/R2 UMI、read ID 或记录数不一致时，该样本为 ERROR；
7. 同一样本跨 barcode/file 的相同 UMI 合并为一个代表组；
8. 不同样本的相同 UMI 不合并；
9. `total_reads`、`representative_reads`、比例和并列候选正确；
10. clean 后仍可准确关联 sidecar；
11. IR IgBLAST 最终 `sequence_id` 是 `representative_id`，未被 barcode 覆盖；
12. ERROR 样本不阻断 OK 样本；补齐后只处理新增或修复样本；
13. presplit 外部 FASTQ 不被修改、复制或 cleanup 删除；
14. 原样重跑时所有有效完成项被跳过。
