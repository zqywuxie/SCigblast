# Preprocess pipeline 运行层优化计划

## 目标与不可变边界

本计划只优化 IgBLAST 后 preprocess/analysis 的输入发现、I/O、任务调度、进程数量、内存、临时文件、断点续跑、错误隔离、停止和进度输出。

计算契约固定为：

```text
CALCULATION_CONTRACT=frozen-current
```

以下内容禁止修改：

- `AIRR_filter` 的过滤条件、阈值、执行顺序、分组键和 `umi_counts` 口径；
- expression、diversity、D50、CSR、SHM 的公式、分母、权重、分组和输出字段；
- SHM 的坐标解释、`pairwise2.align.globalms(seq, germline, 2, -1, -2, -0.5)[0]`、首个 alignment 选择和逐行判定；
- TCR/BCR 在样本内部的组合关系；
- Datapoint、junction 的行列顺序、dtype、NaN/空字符串和浮点结果。

不引入 alignment 去重、缓存、零突变快捷路径、`one_alignment_only=True`、PairwiseAligner/parasail、SQLite 模型 reducer 或 `corrected_v2`。

## 审计结论

### 1. 输入读取会放大内存和 IPC

`10X_split/pipeline/pipeline/main.py` 当前对每个 `TCR.tsv/BCR.tsv` 执行整表 `pd.read_csv()`，随后在 `parmap` worker 中调用 `AIRR_filter()`，再把完整过滤 DataFrame pickle 回主进程。

当前默认：

```text
FILTER_WORKERS=8
SAMPLES_PER_WINDOW=2
```

一个窗口可同时读取多个大型文件，内存中还会并存原始表、过滤副本、进程传输副本和主进程结果。窗口限制控制样本数量，但不控制单文件大小。

### 2. SHM 存在近 700 进程风险

`models/shm_calculate.py` 当前使用：

```python
n_workers = max(1, mp.cpu_count() - 1)
```

在 700+ CPU 服务器上，一个 BCR 样本可能创建约 699 个进程。每个进程接收 DataFrame 分块并返回包含原输入列的完整 DataFrame，进程创建、pickle、内存复制和调度竞争会抵消 CPU 收益并可能触发 OOM。

文件内还存在两个同名 `_split_dataframe()` 定义；前一个被后一个覆盖，属于无语义冗余。

### 3. 长任务没有可恢复状态

当前只在整次运行结束时写 `Datapoint.csv`、`Datapoint_all.csv` 和 `processing_manifest.csv`。十小时任务中断后，已完成样本没有独立 checkpoint，下次会重新读取、过滤和计算。

旧 junction 或 Datapoint 也可能残留；当新运行全部失败或输入被删除时，旧公开结果仍可能被误认为本次结果。

### 4. 进度和失败隔离不足

当前只有窗口开始和样本结束日志。单个大 BCR 进入 SHM 后可能数小时没有进度。`map_async().wait().get()` 对 worker 被杀、pickle 失败或进程池损坏没有样本级恢复边界。

### 5. 现有流式实现不能直接启用

`models/preprocessing.py` 已有 `AIRR_filter_file()`，但它使用两遍读取、`dtype=str`、`keep_default_na=False` 和 SQLite 聚合。当前入口使用 pandas 默认类型/空值推断。两条路径尚未证明在 dtype、空值、首行保留、跨 chunk 分组和输出顺序上完全一致，因此不能直接替换默认路径。

### 6. 其他模型不是本轮主要瓶颈

- expression 在循环内重复 `concat`，可在等价性测试后改为按原顺序一次拼接；
- diversity 和 CSR 的 groupby、排序与组合操作暂时保持原样，避免改变浮点累加或结果顺序；
- CSR 的组合规模可能增长，但不能通过改变组合规则加速。

按当前约 470 行/秒，2000 万 BCR 行的 SHM 约需 11.8 小时。本计划不减少比对次数，因此总体仍是小时级；主要收益是避免失控并发、降低数据复制、提供准确进度，并让中断后只补未完成样本。

## 推荐架构

```text
统一 07.igblastn_out 根
        ↓
discover/validate
        ↓
样本任务队列（sample_key 隔离）
        ↓
样本子进程：read → AIRR_filter → models → junction → checkpoint
        ↓
主进程按 discovery_order 从 checkpoint 原子重建 Datapoint/manifest
```

内部身份继续使用：

```text
sample_key = (dataset/input_root, relative_sample)
```

`sample`、`batch` 仅为公开注释字段，不参与计算分组。同名样本保留独立行，完整来源写入 manifest。

## 实施阶段

### Phase 0：冻结当前合法结果

先建立只读黄金基线，不改运行逻辑。测试集至少包括：

- TCR-only；
- BCR-only；
- 同一内部样本同时有 TCR/BCR；
- 不同目录下同名 sample；
- 过滤后为空；
- 一个真实中型和一个真实大型 BCR。

冻结：

```text
Datapoint.csv
Datapoint_all.csv
processing_manifest.csv
junction_peps/**
```

逐文件验证行顺序、列顺序、dtype、空值和数值；使用 `assert_frame_equal(check_exact=True, check_like=False)`。任何处理优化不能通过该基线时不得启用。

### Phase 1：先建立样本隔离、断点和原子发布

主进程只负责发现、资源准入、排队和汇总。每个样本由独立子进程完成现有调用顺序，子进程只向主进程返回状态和小型结果路径，不返回大型 DataFrame。

采用独立子进程而不是把样本放入 `multiprocessing.Pool` daemon worker，确保样本内部可以安全创建受控 SHM worker，并且样本退出后 pandas 内存和碎片由操作系统完整回收。

状态目录：

```text
08.analysis/
├── .analysis_state/
│   ├── run.json
│   ├── pids/<run_id>.tsv
│   ├── samples/<sample_key_hash>/
│   │   ├── input.json
│   │   ├── base_metrics.pkl
│   │   ├── full_metrics.pkl
│   │   ├── manifest_row.json
│   │   ├── junction.DONE.json
│   │   ├── sample.DONE.json
│   │   └── sample.ERROR.json
│   └── staging/<run_id>/...
├── logs/run.<run_id>.log
├── logs/resource.<run_id>.tsv
├── Datapoint.csv
├── Datapoint_all.csv
├── processing_manifest.csv
└── junction_peps/...
```

checkpoint 绑定：

```text
input_root, dataset, relative_sample, discovery_order
TCR/BCR canonical path, size, mtime_ns, header fingerprint
main/model script hashes, config hash
CALCULATION_CONTRACT=frozen-current
expected outputs and their size/fingerprint
```

只有 fingerprint 和输出清单全部一致的 `sample.DONE.json` 才能跳过。失败或半成品只失效当前样本。

每个样本先在 staging 中写结果，校验后逐文件原子替换，最后写 DONE。最终三个公开汇总文件只从有效 checkpoint 按原 discovery order 重建并原子替换。新运行失败时保留上一版完整公开结果，同时在 run manifest 标明本次失败，避免旧结果冒充新结果。

同一输出目录只允许一个 writer；使用输出目录内的 run ownership marker/PID 校验，不使用服务器级 heavy lock。不同输出目录可以并行。

### Phase 2：建立统一资源预算，先修复 SHM 失控并发

新增独立 preprocess 配置：

```bash
SCIGBLAST_10X_ANALYSIS_SAMPLE_WORKERS=4
SCIGBLAST_10X_ANALYSIS_MAX_BCR_SAMPLES=1
SCIGBLAST_10X_ANALYSIS_SHM_WORKERS=32
SCIGBLAST_10X_ANALYSIS_TOTAL_PROCESS_BUDGET=64
SCIGBLAST_10X_ANALYSIS_SHM_CHUNK_ROWS=10000
SCIGBLAST_10X_ANALYSIS_MEMORY_BUDGET_GB=300
SCIGBLAST_10X_ANALYSIS_MIN_AVAILABLE_GB=150
SCIGBLAST_10X_ANALYSIS_MEMORY_POLL_SECONDS=5
SCIGBLAST_10X_ANALYSIS_PROGRESS_SECONDS=30
```

正式默认 worker 数由 8/16/32/64 benchmark 决定；在验证前以 32 为建议上限，不再使用 `cpu_count()-1`。

准入条件：

```text
active_processes + requested_processes <= TOTAL_PROCESS_BUDGET
reserved_memory + requested_memory <= MEMORY_BUDGET_GB
MemAvailable >= MIN_AVAILABLE_GB + requested_memory
```

调度规则：

- 同时最多一个大型 BCR 样本进入 SHM；
- TCR-only 或小样本可使用剩余样本槽；
- 低于 150 GB 时不再领取新样本/SHM chunk；当前 chunk 在安全边界完成后写 checkpoint，阶段停止并返回非零；
- 不强杀正在原子提交的输出；
- 单样本失败不终止其他样本，全部可运行样本结束后整体返回非零，后续阶段不启动。

### Phase 3：减少 SHM 进程间数据搬运并增加进度

保持 `compute_shm()` 和所有逐行公式原样。只重构 transport：

1. 按原始连续行号切固定 chunk；
2. worker 只接收 SHM 必需列：

   ```text
   locus
   v_alignment_start
   v_alignment_end
   sequence_alignment
   germline_alignment
   ```

3. worker 只返回当前十个 SHM 派生列、原始行号和 chunk 编号；
4. 控制器按 chunk 编号和原始行号恢复顺序；
5. 再调用现有汇总代码，保持现有权重和浮点运算顺序；
6. 删除被覆盖的重复 `_split_dataframe()` 定义。

任务提交必须有界，内存中只保留有限数量的未回收 future。不得把 2000 万行一次性预提交到进程池。

进度每 30 秒输出：

```text
sample, phase, completed_rows, total_rows, percent
rows_per_second, eta_seconds
active_workers, failed_chunks
pipeline_rss_gb, server_available_gb
```

chunk 失败时记录原始行范围，终止当前样本，保留其他样本；不得用空值替代失败 chunk 继续生成指标。

### Phase 4：降低输入读取峰值，但设等价性开关

第一步先使用风险最低的读取优化：

- 大文件默认 `SAMPLES_PER_WINDOW=1`；
- 文件读取并发和 SHM 并发分开配置，默认读取并发 1–2；
- 仅读取过滤、所有现有模型和 junction 输出实际需要的列；列集合由代码显式定义并测试；
- 样本子进程内部处理，避免 DataFrame 经 IPC 返回主进程。

随后单独验证 `AIRR_filter_file()`：

- 同一文件的 in-memory 与 file API 输出必须在稳定排序前就保持相同行顺序；
- dtype、NaN/空字符串、首行保留、跨 chunk 同 key、`sample_id` 可选键和 `umi_counts` 完全一致；
- 测试 chunk size 1、边界 key 横跨 chunk、真实大文件；
- 同时记录临时盘占用与两遍扫描耗时。

只有逐列完全一致时，才为超过阈值的大文件启用流式路径并建立：

```text
.analysis_state/preprocessed/<source_id>.tsv
.analysis_state/preprocessed/<source_id>.DONE.json
```

当前 SQLite 路径在等价性验证前保持关闭。若无法完全一致，继续使用样本子进程整表路径，通过单大型样本准入控制峰值，不以近似结果换取性能。

### Phase 5：低风险的非公式整理

完成前四阶段并通过黄金回归后，再做：

- expression 将循环内多次 `concat` 改为按原列表顺序收集后一次 `concat`；
- 最终 Datapoint 从样本级单行 checkpoint 流式重建，避免保留全部结果对象；
- 清理重复 `_split_dataframe()`；
- 增加必需列、文件可读性和输出路径碰撞的入口校验；
- 错误只记录，不为坏数据生成新的零值/NaN 指标。

diversity、CSR 的 groupby、排序和组合实现本轮保持原样。

## 输入发现和旧输出治理

优先读取 pipeline 的 `dataset_manifest.csv`，只发现登记且 IgBLAST 状态可用的 dataset/sample。没有 manifest 时允许递归扫描精确命名的 `TCR.tsv/BCR.tsv`，但必须先生成 discovery manifest 并记录 fallback 模式。

manifest 增加：

```text
dataset,input_root,relative_sample,batch,sample
internal_sample_key,public_id_collision,collision_count,collision_group_id
available_receptors,tcr_path,bcr_path,status,error
```

输入移除或 fingerprint 改变时，对应 checkpoint 失效。新 generation 成功发布后，manifest 中不再存在的 junction 结果才归档；不在运行开始时删除旧结果。

## 运行和停止

在 `10X_split/pipeline/pipeline/` 内各自维护：

```text
00.preprocess_config.env
run_preprocess.sh
stop.sh
```

`stop.sh` 只读取当前输出目录 `.analysis_state/pids/` 中登记的 PID/PGID，先 TERM 对应进程组，等待配置时间，再处理仍存活的本 preprocess 子进程；不扫描服务器全部 `/proc`，不影响 IR/10X/Pig 或其他用户任务。

停止后：

- 已发布 sample DONE 保留；
- 当前样本未完成 staging 不作为结果；
- 重跑只补未完成或 fingerprint 改变的样本；
- 最终 Datapoint 重新按 discovery order 从所有有效 sample checkpoint 构建。

## 需要修改/新增的文件

- `E:/Desktop/project/SCigblast/10X_split/pipeline/pipeline/main.py`
- `E:/Desktop/project/SCigblast/10X_split/pipeline/pipeline/models/shm_calculate.py`
- `E:/Desktop/project/SCigblast/10X_split/pipeline/pipeline/models/preprocessing.py`
- `E:/Desktop/project/SCigblast/10X_split/pipeline/pipeline/models/expression.py`
- `E:/Desktop/project/SCigblast/10X_split/pipeline/pipeline/00.preprocess_config.env`（新增）
- `E:/Desktop/project/SCigblast/10X_split/pipeline/pipeline/run_preprocess.sh`（新增）
- `E:/Desktop/project/SCigblast/10X_split/pipeline/pipeline/stop.sh`（新增）
- `E:/Desktop/project/SCigblast/10X_split/pipeline/tests/`（新增冻结回归、恢复和性能测试）
- `E:/Desktop/project/SCigblast/pipeline/README.md`

## 验证与发布门槛

### 结果等价

- 单 worker 旧版与新版逐文件、逐行、逐列完全一致；
- 1/8/16/32/64 SHM workers 输出一致；
- TCR-only、BCR-only、TCR+BCR 和同名样本均保持现有行为；
- 单个 receptor 缺失仍可处理另一个 receptor，错误状态语义保持现状；
- 最终行顺序与 discovery order 一致。

### 恢复和并发

- 在 read/filter、SHM 50%、junction 写入、sample DONE 和最终汇总前强制停止；
- 重跑只补未完成样本，不接受 `.tmp` 或不完整 checkpoint；
- 两个进程写同一输出目录时第二个立即拒绝；不同输出目录可以并发；
- 某 worker 被杀后其他样本继续，阶段最终返回非零。

### 资源和性能

- 使用 100 万行及真实大型 BCR 测试 8/16/32/64 workers；
- 记录 rows/s、wall time、process tree peak RSS、server MemAvailable、临时盘、进程创建次数和 pickle 字节量；
- 峰值内存不得超过 300 GB，低于 150 GB 时必须停止领取新任务；
- 只有输出完全一致、吞吐提升且资源稳定时才提高默认 worker 数；
- 如果 64 workers 比 32 workers 更慢，正式默认保持 32 或 benchmark 最优值，不按 CPU 总核数扩张。

## 推荐执行顺序

```text
Phase 0 冻结结果
→ Phase 1 样本隔离、checkpoint、原子发布
→ Phase 2 统一资源预算和 SHM worker 上限
→ Phase 3 SHM 最小 payload、固定 chunk、进度
→ Phase 4 等价流式输入准备
→ Phase 5 低风险整理
→ 全量恢复测试和真实 BCR benchmark
```

该顺序先消除 OOM、并发失控和重跑成本，再尝试流式读取。任一阶段若不能通过 `frozen-current` 回归，就停止在上一阶段，不修改计算定义来换取速度。

## 本次执行记录（2026-08-30）

已执行的低风险运行层优化：

- `10X_split/pipeline/pipeline/main.py` 增加保守的 filter/SHM/总进程预算配置、主机
  `MemAvailable` 准入检查、窗口级资源日志、读取阶段心跳和模型阶段起止日志；
- 增加以输入文件 `size + mtime_ns + 64KiB 头部 fingerprint` 及模型代码 fingerprint
  绑定的样本级 checkpoint。完成样本在下一次运行显示 `SKIPPED(checkpoint)`，输入或
  代码变化会自动失效；checkpoint 使用原始 DataFrame pickle，避免 CSV 重新推断
  dtype/NA；
- junction 写入先于 `sample.DONE.json`，中断不会把半成品标成完成；内存低于配置下限
  时只暂停未领取样本并返回非零，已完成样本保留；
- 两份 `shm_calculate.py` 不再按 700+ CPU 数量创建进程，改为可配置且默认 32 个
  worker，并清理重复 `_split_dataframe()` 定义；未改变 SHM 的比对调用、坐标、权重、
  聚合和输出定义；
- 两份 `expression.py` 将循环内重复 `concat` 改为保持原顺序的一次 `concat`，不改变
  分组、求和、比例或输出字段；
- 新增 `00.preprocess_config.env`、`run_preprocess.sh`、`stop_preprocess.sh` 和
  `stop.sh` 兼容入口。停止脚本只读取当前 output 下的 PID/PGID registry，不扫描全机，
  不使用服务器级锁。

仍按发布门槛暂缓：SHM 最小 payload、跨样本子进程调度、默认流式 `AIRR_filter_file()`、
最终汇总 staging 和真实大样本 benchmark；这些需要先完成 frozen 回归，不能在未验证时
改变计算定义或 dtype/顺序。
