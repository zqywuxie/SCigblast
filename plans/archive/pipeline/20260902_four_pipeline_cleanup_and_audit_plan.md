# 四条 pipeline 精简与审计实施计划

## 1. 范围与不可变边界

本计划只覆盖：

- `IR_split/pipeline`
- `10X_split/pipeline`
- `Igblast_base/pipeline`
- `PigIgblast/pipeline`

约束：

- 四条 pipeline 继续各自维护，不建立跨分支 `common` pipeline。
- 保留 `IR_split/pipeline/models/*.py`，不修改其中的过滤阈值、`umi_counts`、表达量、多样性、CSR 或 SHM 计算定义。
- 10X 保持单细胞链聚类终点；IR representative 保持 preprocessing 终点；Base/Pig 不新增 split、representative 或 preprocessing。
- 已有阶段优先输出结构 `output/<stage>/<dataset>/...` 保持不变。
- 本次只整理调用层、I/O、状态、并发、错误处理和目录结构，不借机改变生物学含义。

## 2. 当前调用链

```text
10X:
01.match → 02.fastp → 03.prefilter → 04.clean → 05.pandaseq
→ 06.split_and_represent → 07.igblast → 08.chain_cluster

IR representative:
01.match → 02.fastp → 03.split/presplit → 04.clean → 05.pandaseq
→ 06.representative → 07.igblast → 08.preprocessing

IR merged:
01.match → 02.fastp → 03.split/presplit → 04.clean → 05.pandaseq
→ 06.igblast（跳过 representative 和 preprocessing）

Base/Pig:
01.match → 02.fastp → 03.clean → 04.pandaseq → 05.igblast
```

## 3. 已确认问题与优先级

### P0：可能影响结果正确性

1. `10X_split/pipeline/08.chain_cluster.py` 递归扫描所有 CSV/TSV，只排除少量文件名，没有排除 `.batches`。保留 IgBLAST 中间批次时，Stage 08 可能把 batch TSV 当正式样本结果重复处理。
2. 10X、Base、Pig 的 batch runner 仍把 `SCIGBLAST_DATA_INPUTS` 和 `SCIGBLAST_SUBMISSIONS` 按数组下标配对；这与“submission 是独立候选集合，由 Note 硬匹配数据路径”的既定规则不一致。
3. IR 同时存在 `RUN_ANALYSIS`、`RUN_PREPROCESSING` 及两套 env 别名，当前优先级会把 preprocessing 值写入 `RUN_ANALYSIS`，配置含义不清且容易误开/误关 Stage 08。
4. Pig 主要依赖样本级 marker，runner 没有与其他分支等价的阶段级完成契约；重跑时会重新进入全部阶段，再由各脚本逐样本检查。

### P1：恢复、缓存和大数据风险

1. `ir_preprocessing_adapter.py` 建 SQLite representative index 时直接删除旧索引后原地重建；中断可能留下缺失或半成品索引。应临时文件构建并原子替换。
2. IR representative state 任意 alias 冲突会让整个输入根失败。应只隔离冲突 ID/样本并写入 manifest；其他有效样本继续完成本阶段，阶段最终返回非零。
3. 10X Stage 08 当前逐文件串行，并在使用 `chunksize` 读取后又把全部 chunk 拼回一个 DataFrame；大文件仍可能占用大量内存。
4. helper 文件移动后，如果 runner 的 stage fingerprint 不包含 helper，修改配置解析器可能错误复用旧 DONE marker。
5. Base 的阶段级 marker 与产物存在性同时参与判断，但 marker 与 canonical manifest 不一致时仍可能误用残留文件；需要以任务 manifest/fingerprint 为主。

### P2：结构冗余和维护漂移

1. `IR_split/pipeline/analysis/` 只是旧兼容入口；主 runner 已直接调用 `08.preprocessing.py`。
2. `ir_preprocessing_adapter.py` 只有 `08.preprocessing.py` 一个调用者，适合原样并入 Stage 08。
3. 四个 `pipeline_config.py` 位于阶段脚本同级，且三套近似、Pig 一套不同 API；不能直接删除，但可以移入各自 `tools/`。
4. `10X_split/pipeline/pipeline/` 是未被主 runner 调用的旧 bulk preprocessing 入口，与当前“10X 终点为 Stage 08 cluster”冲突。
5. `08.plot_barcode_rank.py`、`sequence_id_chain_summary.py` 未被 10X runner 调用，不应占据阶段脚本根目录。
6. `__pycache__/`、`*.pyc` 是生成物，不应保留在 pipeline 目录。
7. Pig runner/config 仍携带未被匹配逻辑读取的 `BARCODE_CSV` 参数；输出表可继续保留空 barcode 字段，但不需要文件参数链。

## 4. 目标目录

每条分支的 `pipeline/` 顶层仅保留：

```text
00.pipeline_config.env
01...阶段脚本
02...阶段脚本
...
run_<pipeline>.sh
stop.sh
models/                 # 仅 IR 必需
tools/                  # 仅放被阶段调用或保留的辅助工具
docs/                   # 可选说明文档
```

`tools/` 是分支本地目录，不在四条 pipeline 之间共享。

## 5. 结构整理实施

### 5.1 IR

1. 删除 `IR_split/pipeline/analysis/` 整个旧兼容入口：
   - `00.analysis_config.env`
   - `main.py`
   - `run_analysis.sh`
   - `stop.sh`
   - `README.md`
2. 保留 `IR_split/pipeline/models/`，并保持模型源码内容不变。
3. 将 `ir_preprocessing_adapter.py` 中以下职责原样并入 `08.preprocessing.py`：
   - UMI/header/read-ID 规范化；
   - representative state 的 SQLite alias index；
   - `filter_and_count()` 对 frozen `AIRR_filter()` 的调用；
   - SHM worker 数限制适配。
4. 删除 `ir_preprocessing_adapter.py` 后同步：
   - 移除 import；
   - 移除 runtime fingerprint 和 runner stage hash 中的 adapter 条目；
   - 让 Stage 08 fingerprint 继续包含自身和 `models/*.py`；
   - 更新 `models/README.md`；
   - 从 `stop.sh` 删除 `analysis/*` 名称。
5. 将 `pipeline_config.py` 移至 `IR_split/pipeline/tools/pipeline_config.py`，更新 `01.match_sample.py`、`03.split_barcode.py` 的 import；Bash 阶段仍直接 source `00.pipeline_config.env`。
6. 将 `RUN_ANALYSIS`/`ANALYSIS_DIR` 全部收敛为 `RUN_PREPROCESSING`/`PREPROCESSING_DIR`，配置只保留 `SCIGBLAST_IR_RUN_PREPROCESSING=auto|0|1`。旧 `ANALYSIS_*` 环境变量不再作为正式配置源。

### 5.2 10X

1. 将 `pipeline_config.py` 移至 `10X_split/pipeline/tools/pipeline_config.py`，更新 match、prefilter、split/represent 的 import。
2. 删除未被主 runner 调用的 `10X_split/pipeline/pipeline/` 旧 preprocessing 目录；10X 不保留 bulk preprocessing models。
3. 将手工工具移出阶段根目录：
   - `08.plot_barcode_rank.py` → `tools/plot_barcode_rank.py`；
   - `sequence_id_chain_summary.py` → `tools/sequence_id_chain_summary.py`；
   - `08.chain_cluster.md` → `docs/08.chain_cluster.md`。
4. 从 `stop.sh` 移除旧路径，保留主 runner 和正式 01–08 阶段进程名。
5. `08.chain_cluster.py` 不再提供历史 Windows 输入/输出默认值；必须由 runner 显式传入 dataset-scoped input/output。

### 5.3 Igblast Base

1. 将 `pipeline_config.py` 移至 `Igblast_base/pipeline/tools/pipeline_config.py`，更新 `01.match_sample.py` import。
2. 保留五个正式阶段和一个 runner；不拆分 `05.work_igblastn.sh` 的内嵌 Python，因为它仍是一个完整阶段且没有第二调用者。
3. submission 改为独立候选集合；每个数据输入接收同一组 submission 源，matcher 合并所有 xlsx 后按 Note 选择。

### 5.4 Pig

1. 将 `pipeline_config.py` 移至 `PigIgblast/pipeline/tools/pipeline_config.py`。
2. 将 loader 对外契约整理为与本分支阶段一致的 env 加载方式；更新 `01.match_sample.py`、`03.clean_header.py`、`05.work_igblastn.py` import。无需与其他分支共享实现。
3. 删除 config、runner 和 child env 中无调用的 `SCIGBLAST_BARCODE_CSVS`、`BARCODE_CSV`、`SCIGBLAST_RUN_BARCODE_CSV`；manifest 中为跨分支格式预留的空 barcode 列不变。
4. submission 改为独立候选集合并由 Note 硬匹配；保留 Species=Pig、Chain 展开和 Pig 数据库选择逻辑。
5. 增加与 Base/IR/10X 一致的阶段级 marker、脚本/config fingerprint 和最终 DONE 验证。

## 6. 各阶段共同运行契约

不复制成 common 代码，只统一行为：

1. match 是唯一 raw suffix 解析入口；下游只读取 canonical `_R1/_R2`。
2. submission 是独立集合，不与 input 按下标绑定；Note 是 Dual Index 搜索前的硬批次边界。
3. fastp 只处理 match manifest 中 `status=OK` 的物理 FASTQ pair；同一物理 pair 对应多个样本时只运行一次。
4. 每个 worker 只写自己的临时输出、row shard、日志和 marker；汇总 CSV 由主进程稳定排序后单写。
5. 一个样本失败时，本阶段其他任务继续；阶段结束返回非零，runner 不启动下一阶段。
6. 重跑只执行缺失、失败或 fingerprint 变化的任务；即使全部 skip，也从任务 marker 重新生成 summary。
7. stage DONE 必须同时满足：输入 fingerprint、config/script/tool fingerprint、任务状态和必要输出验证。
8. 四条 runner 默认串行处理 dataset，阶段内部进行样本/链任务并发，避免多个 dataset 同时占满 300 GB 预算。

## 7. 具体逻辑与性能修复

### 7.1 10X Stage 08

1. 输入发现严格限定为正式 `TCR.tsv`/`BCR.tsv`，并显式忽略 `.batches`、`.postprocess`、状态目录和输出目录。
2. 输入为空但存在合法 `.NO_RESULTS` 时写空 summary 并成功；否则报输入错误。
3. 先以文件/样本为任务边界增加 `SCIGBLAST_10X_CLUSTER_WORKERS`，并受总线程、300 GB 预算和实时可用内存限制。
4. 为超大单文件增加按 barcode 稳定分桶的流式路径：读取 chunk → 写临时分桶 → 每桶沿用现有聚类函数 → 稳定合并。分组键、阈值、排序和输出字段不变。
5. Stage 08 marker 记录真实 input/output、正式输入文件清单、cluster 参数和脚本 hash。

### 7.2 IR Stage 08

1. SQLite index 在同目录唯一临时文件中构建、校验后 `os.replace()`；失败保留旧有效索引。
2. 冲突 alias 记录到 `processing_manifest.csv`，只排除受影响记录/样本，不因一个冲突终止整个 input root。
3. state 缺失时仅按现有 `UMI_SOURCE=auto/state/row_contract` 契约处理，不新增 sidecar/header 竞争逻辑。
4. 保留单样本窗口和受控 SHM pool；只优化调度、索引和 I/O，不改模型公式。
5. 对模型的空输入/不适用场景在 Stage 08 调用前做资格判断并记录 `NOT_APPLICABLE`，不改 `models/*.py` 的计算路径。

### 7.3 Base/Pig 和公共前处理阶段

1. Fastp、clean、PANDAseq、IgBLAST 的样本并发继续取以下最小值：配置任务数、线程预算/每任务线程、内存预算/每任务内存、待处理任务数。
2. 保留 PANDAseq 多 worker STAT 聚合逻辑，测试必须用真实多线程日志验证总 reads 与 FASTA 记录数一致。
3. IgBLAST summary 继续区分 `input`、`mapped`、`productive`、`filtered` 及各自百分比，不把 productive 数写成 mapped 数。
4. Pig DB preflight 保持在首次 match 审核之后、重阶段之前。

## 8. Stop 与进度

1. 四条 pipeline 各自维护 `stop.sh`，只读自己的 `output/.pipeline_state/<dataset>/pids` 和 PGID。
2. 删除/移动脚本后同步正式进程名清单；不扫描全服务器每个 `/proc/<pid>`。
3. 先 TERM 整个登记进程组，短暂等待后只对仍存活的登记 PID/PGID 使用 KILL。
4. 每阶段输出 `submitted/running/completed/failed/skipped/total/percent/current`；长时间索引、分桶和汇总额外输出行数/字节进度。

## 9. 断点迁移

1. helper/adapter 路径变化会改变 stage fingerprint，因此相关阶段第一次运行应重新验证，但不删除现有大文件。
2. 若输入、参数和正式输出都有效，可根据新 fingerprint 重写 marker；否则只重跑受影响阶段及其下游。
3. IR Stage 08 旧 checkpoint 的 fingerprint 含 adapter 路径，内联后视为失效并重算 Stage 08；Stage 01–07 不受影响。
4. 删除 10X 旧 nested preprocessing 不影响主 runner marker，因为它从未进入正式调用链。
5. 不自动删除任何中间产物；清理由显式配置控制。

## 10. 验证矩阵

### 静态验证

- 四条 runner 和所有 shell 阶段执行 `bash -n`。
- 所有 Python 阶段做无落盘 AST/compile 检查。
- `rg` 确认不存在旧 `analysis/`、顶层 `pipeline_config`、旧 10X nested preprocessing 和 adapter 引用。
- 比较改造前后 `IR_split/pipeline/models/*.py` hash，必须完全一致。

### 配置和目录验证

- 每个 Python 阶段可独立读取本分支 `00.pipeline_config.env`。
- 单输入、多输入、重复 basename 输入均输出到 `output/<stage>/<dataset>`。
- 多 input + 多 submission 源不按下标配对，Note 能为每个 raw path 选择正确记录。

### 四条小数据全流程

- 10X：混合链、一个 `.NO_RESULTS` 样本、保留 `.batches`；确认 Stage 08 只读取正式 TCR/BCR。
- IR raw：split → representative → Stage 08；验证 Datapoint/junction/`umi_counts` 与改造前 golden result 一致。
- IR presplit：header UMI 路径，不产生第二次 barcode split；结果与原逻辑一致。
- IR merged：跳过 representative/preprocessing。
- Base：TCR-only、BCR-only 和 mixed chain。
- Pig：Pig/non-Pig submission 行、ab/TRA/TRB Chain、Pig DB preflight。

### 故障与恢复

- 每阶段完成至少一个样本后中断，重跑验证已完成样本 skip、未完成样本继续。
- 注入一个样本失败：同阶段其他样本完成、阶段最终非零、下游不启动。
- 修改 helper/config 后仅失效对应阶段及下游 marker。
- 两个样本同 lane 同时结束时，marker/summary 不发生临时文件竞态。
- 四条本地 `stop.sh` 仅停止各自登记进程。

## 11. 推荐实施顺序

1. 先为四条 pipeline 建立小型 golden fixtures 和当前结果快照。
2. 修复 P0：10X Stage 08 输入白名单、submission 独立集合、IR preprocessing 开关、Pig 阶段 marker。
3. 移动四个 config loader 到各自 `tools/`，更新 import 和 fingerprint。
4. 内联 IR adapter、删除 IR `analysis/`，验证 Stage 08 golden result 不变。
5. 清理 10X nested preprocessing；移动手工工具和文档；清理 stop 清单。
6. 实施 10X Stage 08 有界并发和大文件分桶，以及 IR SQLite 原子索引/冲突隔离。
7. 删除 `__pycache__`/`*.pyc` 并加入忽略规则。
8. 完成四条小数据全流程、恢复、并发、内存和 stop 验证后再迁移正式结果目录。

## 12. 完成标准

- pipeline 顶层只保留配置、runner、stop、正式编号阶段及必要的本地 `models/tools/docs` 目录。
- IR 无 `analysis/` 和 adapter 单文件，Stage 08 仍产生相同生物学计算结果。
- 10X 无旧 bulk preprocessing 入口，Stage 08 不读取任何 IgBLAST 中间 batch。
- Base/Pig/10X/IR 都以 Note 从独立 submission 集合匹配数据。
- 所有正式阶段具备一致的输入契约、原子 marker、样本级恢复、阶段失败语义、进度和资源限制。
- IR models hash 与改造前一致。
