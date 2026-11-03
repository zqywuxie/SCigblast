# IR_split 已有拆分结果复用方案

## 目标

在不改变 IR barcode/UMI 拆分规则的前提下，实现：

- 已完成且输入未变化的 pair 不重新拆分；
- 阶段标记缺失时，可以从已有输出自动恢复；
- 只有部分 pair 缺失、失败或 match 修复后，才处理这些 pair；
- 保留 mapping error 对有效样本下游处理的容错；
- 不把“目录中存在任意 FASTQ”误判为整个 split 阶段完成。

## 当前问题

涉及：

- IR_split/pipeline/03.split_barcode.py
- IR_split/pipeline/run_ir_pipeline.sh

当前已有 pair checkpoint：

- 03.IR_split_output/ir_split_progress.tsv
- 03.IR_split_output/.ir_split_progress_state/*.json
- .part 文件支持按已处理 reads 继续。

但阶段级判断只检查 03.split.DONE、输出目录非空和少量 FASTQ。历史输出没有 state 时不能安全复用，pair state 也没有 R1/R2 输入指纹，因此原始 FASTQ 被替换后可能误跳过。

## 推荐实现

### 1. 在 03.split_barcode.py 增加统一的 pair inventory/复用检查

抽出一个 expected_output_paths(pair)，让检查和实际写出使用同一套路径规则：

03.IR_split_output/<相对目录>/<pair_stem>/<sample_id 或 sample_id__BC_barcode>/
    <pair>_R1.fq.gz
    <pair>_R2.fq.gz
    <pair>_R1.fq.gz.umi.tsv.gz

为每个 pair 计算轻量输入指纹，不对巨大 FASTQ 做完整 SHA256：

    r1 absolute path
    r2 absolute path
    r1 size + mtime_ns
    r2 size + mtime_ns
    sample_id
    barcode_name
    barcode_sequence
    umi_length
    split script version

将指纹写入 pair JSON state。后续只有以下条件全部满足才返回 SKIPPED_EXISTING：

- state.status 为 DONE；
- 当前输入指纹与 state 一致；
- R1、R2 均存在且非空；
- UMI sidecar 存在且非空；
- 输出 FASTQ 成对；
- sidecar 记录数与输出 R1/R2 记录数一致。

已有 state 但指纹不一致时标记为 RETRY，重新处理该 pair。

### 2. 兼容没有 state 的历史拆分结果

增加 SCIGBLAST_IR_VALIDATE_ONLY=1 检查模式，逐条读取当前 match summary，验证已有输出。

历史目录只有在以下条件满足时迁移为可复用状态：

- 能按当前 pair/sample/barcode 推导出目标目录；
- R1、R2 和 UMI sidecar 均存在；
- R1/R2 记录数一致；
- sidecar 的 read key 与输出 FASTQ 能对应；
- 当前输出没有落入旧的平铺目录格式。

迁移时写入当前输入指纹，状态记为 LEGACY_REUSED。如果无法完成一致性检查，则标记 RETRY，不能静默跳过。

### 3. 输出 pair 级恢复摘要

在 03.IR_split_output 写出 ir_split_resume_summary.csv。

至少包含：

    pair_key
    sample_id
    barcode_name
    status
    reason
    input_fingerprint
    output_r1
    output_r2
    umi_sidecar
    processed_reads
    resumed_from

状态统一为：

- SKIPPED_EXISTING：已有完整结果；
- LEGACY_REUSED：历史结果通过验证后复用；
- RESUMED：从 .part 和 checkpoint 继续；
- PROCESSED：新处理；
- RETRY：输入变化、state 错误或输出不完整；
- ERROR：本次处理失败。

### 4. 修改 run_ir_pipeline.sh 的阶段判断

保留当前快速路径：

    03.split.DONE + mapping/config hash 一致 + stage output valid
        → 直接跳过 split

新增阶段标记缺失或为 PARTIAL 时的 pair inventory 检查：

    所有当前 status=OK pair 都是 SKIPPED_EXISTING/LEGACY_REUSED
        → 不调用拆分处理，补写 03.split DONE

    存在 RETRY/RESUMED/PROCESSED pair
        → 调用 03.split_barcode.py
        → 脚本内部只处理这些 pair，已完成 pair 继续跳过

    没有可用 pair 或发生致命配置错误
        → 停止下游

SPLIT_RERAN 应改成实际是否产生新输出，而不是“是否调用过脚本”。只有 PROCESSED、RESUMED 或 RETRY 真正成功时，才清除 clean/PANDAseq/IgBLAST 的下游标记。

如果只是发现已有结果并补写阶段标记，不应触发下游重跑。

### 5. 处理 mapping 修复和旧输出

match summary 的 hash 改变后：

- 原来已经完成且指纹一致的 pair：SKIPPED_EXISTING；
- 新变为 OK 的 pair：PROCESSED；
- 原来 ERROR/PARTIAL 但已补齐 barcode 的 pair：RETRY；
- barcode 或 sample 改变导致旧输出路径失效时：移入 .orphaned/ 并记录原因，不直接删除。

这样不会因为补齐一个样本而重新拆分整个 Lane，也不会让旧 barcode 输出继续被下游扫描。

## 性能设计

- 阶段级 DONE 路径只做目录和 marker 检查；
- pair inventory 使用预先构建的内存索引，不重复递归扫描；
- 正常已有 state 只执行 stat 和文件存在性检查；
- 不对每个 FASTQ 做完整内容 hash；
- 只有无 state 的历史文件才执行一次记录数/sidecar 一致性验证；
- 继续使用现有线程池和 read-level checkpoint。

## 验证计划

使用最小 FASTQ fixture 验证：

1. 首次运行：pair 状态为 PROCESSED。
2. 再次运行：状态为 SKIPPED_EXISTING，不读取 FASTQ 内容。
3. 删除一个 sample 的 R1：只有该 pair 为 RETRY。
4. 保留 .part 并中断：下次状态为 RESUMED。
5. 删除 state 但保留完整旧输出：通过检查后为 LEGACY_REUSED。
6. 修改输入文件 size/mtime：该 pair 必须重新处理。
7. match summary 新增/修复一个样本：旧 OK pair 跳过，新 pair 处理。
8. 当前 summary 有 ERROR 但存在 OK pair：有效 pair 继续，错误 pair 保留并使阶段最终返回非零。
9. 同一物理 pair 对应多个 sample/barcode：各自目录、state 和 sidecar 不互相覆盖。
10. 运行完整 IR pipeline：仅真正变化的 pair 触发后续 clean/PANDAseq/IgBLAST。

## 计划修改文件

- IR_split/pipeline/03.split_barcode.py
  - 输入指纹；
  - 历史输出验证；
  - pair inventory；
  - resume summary；
  - 状态分类。
- IR_split/pipeline/run_ir_pipeline.sh
  - split 阶段快速复用；
  - pair inventory 分支；
  - 仅在实际变化时失效下游阶段。
- 如有必要，更新 IR_split/pipeline/00.pipeline_config.env
  - 增加 legacy reuse、校验级别和脚本版本配置。
