# Human CSV → FASTA → IgBLAST → UMI count 整理模块计划

日期：2026-09-14。状态：已实现，验证记录见文末。

2026-09-15 补充实现：扫描展示样本名、相对路径、链和文件数；按相对目录/样本名分组，样本默认全部勾选，支持用户多选。`mapping_samples` 保存具体样本键，三个阶段按链与样本的交集执行。CLI 增加 `--samples`，未传时保持全部样本；已创建任务续跑沿用保存的样本范围。

## 目标与已确认范围

用户已确认：首版使用 `mapping/fasta.py` 的现有 CSV 输入格式，仅支持 human，并使用 `mapping/umi_count.ipynb` 的 UMI count 整理逻辑。新增 Web 流程 `mapping`，完成 CSV → FASTA → IgBLAST → UMI count 整理。输入为已经取得的免疫组库 CSV 目录；不增加数据下载、其他官方格式转换、FASTQ 处理、UMI 聚类/纠错或原始注释对比分析。

第一项项目检索成果独立写入根目录 `AGENT.md`。以下保留接入设计；实际 IgBLAST 阶段集成在 `mapping/pipeline/run_mapping.py`，notebook 整理逻辑位于 `mapping/pipeline/umi_count.py`，由 Bash runner 顺序调度三个阶段。

## 现状依据与接入选择

- 当前四条生产流程由 `web/pipeline_registry.json` 注册；`web/app.py` 的 `build_job()` 与 `create_job()` 强制校验或复制 Submission，并设置 Match-only 环境变量。
- `mapping/fasta.py` 只有转换功能，默认输入为相对 cwd 的 `artificial_peps`，默认只选 IGH；目录中的 `AL_s003_v8__IGH.csv` 符合三列输入契约。
- `mapping/umi_count.ipynb` 读取 `./igblastn_out` 下的 TSV，执行 `df['barcode'].str.split('_').str[2]` 生成 `umi_count`，另存至 `./igblast_out/umi/IGH` 并保留相对目录。实际取第三段，注释“第二段”和缺列提示 `sequence_id` 有误；不能直接用当前 notebook 处理只有 `sequence_id` 的 AIRR。
- Base 的 `05.work_igblastn.sh` 即使传入 `--data-dir`，仍从 FASTQ manifest 推导 `<pair>_merged.fasta`。因此新增独立 Mapping runner，参考其 human 参数和统计定义；不修改四条分析流程的内部逻辑。
- 复用现有任务队列、账户归属、服务器输出目录、进程组停止、日志和归档。Mapping 不使用 Submission、Barcode 或人工 Match 审核。

## 输入与转换契约

1. 用户选择输入目录后先扫描，再选择只处理哪些链。递归发现 `<sample>__<chain>.csv`，链取最后一个 `__` 后缀，大小写不敏感，sample 不得为空。可识别 IGH、IGK、IGL、TRA、TRB、TRD、TRG；多选框只提供本次目录实际发现的链，并展示各链 CSV 文件数，支持全选/清空。默认仅勾选已发现的 IGH；没有 IGH 时由用户选择，不自动改成处理全部链。至少选择一条链才能创建任务。
2. 必需列为 `CDR3(pep)`、`copy`、`joinedSeq`；额外列允许存在。先提供固定三列配置，不增加网页列映射编辑器。
3. 保持合法输入的输出兼容：`>零基原始数据行号_CDR3(pep)_copy`，下一行为 `joinedSeq`。行号不因跳过空序列重新编号；一条有效 CSV 记录对应一条 FASTA 序列，copy 不展开、不加权、不去重。
4. 保留输入相对目录和 stem，作为源文件身份及输出路径。每个文件独立保留 AIRR，避免不同文件相同行号造成混淆；汇总携带 `source_file`、`sample_id`、`chain`。
5. 缺列、无法解析 CSV、缺失 header 值或会破坏 FASTA ID 的空白/换行、非法序列必须在转换报告中给出文件和原因。序列首尾空白可清除，缺失/空序列计入跳过数；其余无效记录使对应文件失败，避免悄悄改变注释。允许核酸 IUPAC 字符，不自动修改内部字符。
6. 未选中的链记录为跳过；未识别命名的 CSV 记录原因。选中文件零有效记录或整个目录没有可处理文件时任务失败。有效文件可以产出结果，但任一选中文件处理失败时整条任务为 FAILED，便于修复后续跑。
7. 对 `mapping/fasta.py` 做局部调整：保留现有可调用转换函数和合法输入 header 行为，增加路径/链 CLI 参数、可供 runner 使用的逐文件转换结果及必要的输入校验。无需新建解析框架或更换 pandas。

扫描仅识别文件名与链并统计文件数，不运行转换或 IgBLAST，也不把文件数称作序列数。CSV 列和记录有效性仍由转换阶段检查。输入目录变更立即清除扫描结果和链选择，旧请求返回时不得覆盖新目录结果；无可识别链时展示命名要求并禁止提交。提交时后端重新检查目录及选中链，拒绝空集合、非法链和目录中不存在的链。三阶段共享已选链对应的输入清单，未选中链不进入 FASTA、IgBLAST 或 UMI 整理，也不因其数据库缺失阻断任务。任务参数保存链选择，续跑沿用；改变链选择创建新任务。

## IgBLAST 调用与产物

新增 `mapping/pipeline/00.pipeline_config.env`、`run_mapping_pipeline.sh`、`02.work_igblastn.py`、`03.umi_count.py`。runner 读取配置与 Web 环境覆盖，通过现有镜像 Python/IgBLAST 运行；第一阶段调用上一级 `fasta.py`，第二阶段消费转换生成的成功文件清单，第三阶段整理本次成功比对文件的 umi_count，不重新扫描历史输出。

human 数据库沿用 Base 的路径约定：

```text
<db_root>/database_251117/human/<chain>/human_gl_<chain>_V
<db_root>/database_251117/human/<chain>/human_gl_<chain>_J
<db_root>/optional_file/human_gl.aux
```

执行参数为 `-query <fasta> -germline_db_V <V> -germline_db_J <J> -auxiliary_data <aux> -organism human -ig_seqtype <Ig或TCR> -num_threads <n> -outfmt 19 -out <raw.tsv>`。IGH/IGK/IGL 使用 Ig，四条 TR 链使用 TCR；按现有 Base 约定，有对应 D/C 数据库时添加 `-germline_db_D` / `-c_region_db`（C 同时设置 `-num_alignments_C 1`）。具体可运行性须用服务器现有数据库实测，不借用 Pig 的专属配置。

仅预检 Mapping 所需 Python/pandas、IgBLAST、选中链数据库及 aux；不要求 fastp/PANDAseq。IgBLAST 使用参数列表调用。并发和每进程线程由模块配置限制，继续遵守容器总资源及 Web 两任务上限。

输出布局：

```text
<job_output>/
  01.fasta/<dataset>/
    <relative_parent>/<sample>__<chain>.fasta
    conversion_summary.csv
  02.igblastn_out/<dataset>/
    <relative_parent>/<sample>__<chain>.raw.tsv
    <relative_parent>/<sample>__<chain>.filtered.tsv
    <relative_parent>/<sample>__<chain>.log
    chain_summary.csv
  03.umi_count/<dataset>/
    <relative_parent>/<sample>__<chain>.raw.tsv
    <relative_parent>/<sample>__<chain>.filtered.tsv
    umi_count_summary.csv
  logs/<dataset>/pipeline.log
  .pipeline_state/<dataset>/
    .pipeline_stage_01.fasta.DONE
    .pipeline_stage_02.igblast.DONE
    .pipeline_stage_03.umi_count.DONE
    .pipeline.DONE
    files/  # 逐源文件完成记录
```

原始 AIRR 保留全部输出；另产出 `v_call` 有效且 productive 为 T/TRUE 的 filtered TSV，沿用 Base 的无效 v_call 值集合。网页区分“原始比对”和“过滤结果”。首版不合并样本或链。

汇总至少包含来源、样本、链、human、`input_sequences`、`raw_rows`、`mapped_rows`、`productive_rows`、`output_rows`、`mapping_percent`、`retained_percent`、状态和错误。分母统一为实际写出的 FASTA 记录数；mapped 指有效 v_call，productive 独立计数，output 指两条件交集。缺失 productive/AIRR 必需表头应报错，不能伪装为合法零命中。合法零命中可成功并写出带表头的空过滤表。

## UMI count 整理契约

将 notebook 的核心整理逻辑提取为 `mapping/pipeline/03.umi_count.py` 的可调用函数和 CLI，保留原 notebook 作为来源，不引入 Jupyter 运行依赖。

1. 仅处理第二阶段清单中的 raw/filtered AIRR，不将 `chain_summary` 等统计表当作序列表。分别另存到 `03.umi_count/<dataset>/`，保留相对目录和所有原始列、行顺序、行数；只新增 `umi_count`，不覆盖第二阶段产物。
2. 本流程明确从 `sequence_id` 读取 FASTA 标识符，例如 `0_CARALSSAWKGVFDSW_11` → `umi_count=11`。为承接 notebook 的现有表格，独立整理入口在没有 `sequence_id` 时允许使用 `barcode`；两列均存在时以 `sequence_id` 为准，不能自动按行切换来源。这里的 barcode 是历史列名，不引入 Barcode CSV 输入要求。
3. 解析 header 的零基行号、CDR3 和末尾 copy；从最右侧分割提取 copy，避免 CDR3 中的下划线影响位置。对当前三段合法 header，与 notebook 的 `str[2]` 结果一致。保留 copy 的原始表示，不擅自四舍五入、补零或赋默认值；转换阶段也应避免 pandas 类型推断改变 copy 文本。
4. `umi_count` 的来源是原 CSV 的 copy 字段，此步骤还原已有计数，不推断新 UMI、不聚类、不展开记录，也不改变前述 mapping/retained 百分比。字段名固定为 notebook 使用的单数 `umi_count`。
5. 缺少标识列、标识为空或不符合本流程 header 约定时，记录来源文件与行号并使该文件整理失败；不得像原 notebook 一样只打印提示后整体成功。合法零行 filtered AIRR 仍输出带 `umi_count` 表头的零行结果。保留其他列的字符串与缺失值表示，避免 pandas 默认 NA/数值推断改写注释。
6. 输出 `umi_count_summary.csv`，记录源文件、样本、链、raw/filtered 类型、标识来源列、输入/输出行数、状态和错误。网页最终结果提供“UMI count 整理结果”的预览与下载。

## 状态与续跑

- 注册三阶段 `01.fasta`、`02.igblast`、`03.umi_count`，完成策略为 `pipeline_done`。日志采用 `[MAPPING 1/3]`、`[MAPPING 2/3]`、`[MAPPING 3/3]` 和已有 `percent=` 格式。第三阶段完成后才允许整条任务 SUCCEEDED。
- 状态为 QUEUED → RUNNING → SUCCEEDED/FAILED/STOPPED；不设置 Match-only，不生成 `.match_review.done`。保留失败、停止及中断任务的现有 resume 操作。
- 逐文件记录源相对路径、大小、mtime_ns、转换字段/链及实际比对配置；源文件或相关配置变化时重做受影响的阶段。无需新增内容哈希框架。配置相同且产物齐全才跳过；临时文件完成后改名，再写完成记录。
- 任一阶段失败不写该阶段/整条流程 DONE；重试时先校验完成记录，不能仅凭全局 DONE 跳过。重新生成汇总仅包含本次输入清单，删除或改名的源文件不能混入旧结果。
- UMI 整理使用独立逐文件完成记录，关联 AIRR 输入元数据及解析配置；只在整理失败时续跑不重做已完成的 IgBLAST。上游重算使对应第三阶段记录失效。
- Web 沿用任务进程组停止；runner 不脱离该进程组。保留 FASTA、AIRR 和日志供检查及续跑。

## Web 和镜像的必要修改

1. `web/pipeline_registry.json`：增加 Mapping 入口、三阶段、无 Barcode、空 `match_summary`，增加 `requires_submission=false`、`requires_match_review=false`；现有四流程这两个能力默认 true。
2. `web/app.py`：
   - 增加只读 `POST /api/mapping/scan`，复用登录及允许输入路径校验，返回实际发现的链、各链文件数和未识别文件数；扫描与 runner 共用 `mapping/fasta.py` 的文件发现规则，目录内文件解析路径也须遵守允许输入范围。
   - `CreateJob` 增加显式链选择字段，Mapping 必须提交非空选择，不能由后端默默补 IGH 或全部链；species 固定 human。创建/验证任务时重新扫描，拒绝非法链、空集合和当前目录不存在的链；把选中链写入任务参数与 runner 环境。
   - `create_job()`、`build_job()`、验证接口仅在流程需要时导入/校验 Submission、检查其归属及传递 Match-only 变量。无 Submission 保存空字符串，保持现有数据库字段兼容。
   - `snapshot()` 暴露上述能力；Mapping 不补入虚构的 `01.match`。`review_ready()` 与 Match 专属接口对 Mapping 不适用，明确拒绝错误调用。
   - `preflight()` 按 Mapping 分支检查实际所需工具/数据库；`parse_progress()` 识别 MAPPING；结果解析、阶段报告、artifacts 和下载增加转换报告、原始/过滤 AIRR、FASTA、第三阶段带 umi_count 的结果及整理汇总。新增下载仍验证任务归属和输出目录范围。
3. `web/templates/index.html`、`web/static/index.js`：Mapping 表单按“输入目录 → 扫描链 → 勾选链 → 创建任务”排列；扫描后展示实际链及文件数，提供全选/清空、扫描中/失败/空结果反馈，扫描未完成或未选链时禁用提交。目录更换清空结果并忽略过期扫描响应。切换到 Mapping 隐藏、禁用且解除 Submission/Barcode 的 required，不发送残留副本，并使尚未完成的 Submission 异步导入回调失效；切回原流程恢复原必填和默认值。帮助、提交及创建操作记录文案显示三阶段流程。
4. `web/templates/job.html`、`web/static/job.js`：根据能力隐藏 Match 页签和编辑资料入口，不触发后台 Match/metadata 请求；概览显示 human、链和转换统计，提供产物下载。按需要局部调整 `style.css`，不重新设计页面。
5. `web/Dockerfile`、`web/Dockerfile.dockerignore`：仅复制 Mapping Python/Bash/config，包含上一级 `mapping/fasta.py`，不打包 CSV/notebook。
6. `web/check_bundle.py`：检查新增 runner、配置、转换器、`03.umi_count.py` 和 Python/Bash 语法；按 Mapping 实际文件需求分支校验，不为了原有 `tools/pipeline_config.py` / `stop.sh` 假设增加空文件。输出流程数量从注册表读取。
7. 更新 `web/README.md` 与 `AGENT.md` 中 Mapping 状态和运行说明。保留当前已有 `web/static/job.js`、`style.css` 及测试中的未提交修改。

## 实施顺序与验收

先完成转换、比对和 UMI count 整理的独立 runner，再接 Web 能力分支和报告下载，最后补镜像打包及回归。测试使用 `unittest`，放在新增 `mapping/tests/` 与 `web/tests/test_mapping.py`，不执行项目真实数据全量计算。

- 转换：现有小样本检查首条 header/序列和记录数；嵌套同名文件不会覆盖；多链筛选；零基行号；copy 不展开；缺列、空序列、缺失 header、非法字符及零有效文件的退出状态。
- 扫描与选择：混合七链目录返回实际链和准确文件数；大小写后缀及嵌套文件识别一致；只选 IGH/TRB 时三个阶段均不处理其他链；未选链缺库不阻断；空目录、错误命名、越界路径、空选择和伪造不存在链均正确处理。扫描后删去选中链文件，创建时应报错。
- 比对：模拟进程核对七链 human V/J/D/C、Ig/TCR、outfmt 19；验证失败退出码、raw 保留、过滤交集及明确百分比分母；续跑只补失败或输入/参数变化的文件。
- UMI count：标准三段 header 与 notebook 提取值一致；sequence_id 主入口与 barcode 后备入口；CDR3 含下划线；缺列/空标识/畸形标识失败；零行过滤表；整理前后其他列和行数不变；逐行 umi_count 等于原 CSV 对应 copy；整理失败续跑不调用 IgBLAST。
- Web：Mapping 无 XLSX 可创建、无 WAITING_REVIEW、参数校验、三阶段进度、停止/续跑、报告与下载；UMI 整理未完成不得成功；四条原流程仍要求 Submission/Match 审核；保持权限、输出分配、队列和删除行为。
- 前端：先扫描再选择、全选/清空、无 IGH 时不自动全选、输入目录改变清空选择、过期响应不污染新结果；切换 Mapping ↔ 原流程时必填字段恢复正确，详情展示已选链且不请求 Match 接口，原始/过滤结果入口可用。
- 镜像：构建后通过 check_bundle，确认包含 Mapping 源码且没有示例 CSV/notebook。
- Linux/Docker 真运行：用示例 IGH CSV 走完整三阶段，核对 FASTA 与 raw AIRR 的 sequence_id 对应、过滤结果、统计以及最终 umi_count 与原 CSV copy 一致；使用可获得的小型其他链输入验证数据库路由与零命中。Web 正常完成状态必须以产物和三个阶段 DONE 同时验收。

实施时的检查命令：

```bash
rtk proxy python -m unittest discover -s mapping/tests -v
rtk proxy python -m unittest discover -s web/tests -v
rtk proxy node --check web/static/index.js
rtk proxy node --check web/static/job.js
rtk proxy bash -n mapping/pipeline/run_mapping_pipeline.sh
rtk proxy docker compose -f web/docker-compose.yml build
```

实施已完成：共享链扫描、三阶段 CLI、逐文件续跑、Web 表单/任务/结果接口及 Docker 源码白名单均已接入。独立运行命令和当前文件布局以 `mapping/README.md` 为准。

验证：Mapping 专项 9 项通过；Web 执行 65 项，62 项通过、3 项部署环境测试跳过。浏览器验证扫描、选择/清空、目录变更、恢复原流程必填项、无 Submission 创建、三阶段详情、键盘页签切换、UMI 表格预览及下载。最终 Docker 镜像 `scigblast-web:local` 构建通过，运行环境检查和源码打包检查均成功，识别 5 条流程。

真实验收使用 `mapping/AL_s003_v8__IGH.csv` 和仓库 human 数据库：43 条输入、43 条 AIRR/有效 V 匹配、40 条 productive/过滤保留，mapping_percent=100.00、retained_percent=93.02；43 条最终 umi_count 逐行等于原 CSV copy，三阶段及总 DONE 完整。再次运行返回成功且 raw AIRR、UMI 输出修改时间不变，验证断点复用。验收产物在工作区外的本次 visualization 目录 `mapping-validation/smoke/`，未覆盖用户数据或重启现有服务。
