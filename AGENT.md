# Repository Guidelines

## 项目定位与阅读入口

SCigblast 是免疫组库分析平台：Python/Bash 执行分析，`web/` 提供 FastAPI、Jinja2、原生 JavaScript 任务界面，SQLite 保存账户、任务和操作记录。先读 `web/pipeline_registry.json`、`web/app.py`、目标流程的 runner 和配置，再参考 `web/README.md`。`pipeline/README.md` 有过时路径与流程描述，发生冲突时以当前注册表和可执行代码为准。

## 分析架构

- `IR_split/pipeline/run_ir_pipeline.sh`：Match → fastp → Barcode/UMI 拆分 → header 清理 → PANDAseq → 代表序列 → IgBLAST → preprocessing。当前统一自动识别 raw/presplit；结果模型位于该分支 `models/`。
- `10X_split/pipeline/run_10x_pipeline.sh`：Match → fastp → R1/R2 预筛选 → header 清理 → PANDAseq → Cell Barcode/UMI 拆分与代表序列 → IgBLAST → 链聚类。不要混用 IR 与 10X 的拆分规则。
- `Igblast_base/pipeline/run_igblast_base.sh`、`PigIgblast/pipeline/run_pig_pipeline.sh`：Match → fastp → header 清理 → PANDAseq → IgBLAST；Pig 使用专属数据库逻辑。
- 每个分支通过 `00.pipeline_config.env` 与 `tools/pipeline_config.py` 管理配置；Web 通过环境变量启动固定 Bash runner。容器工具路径由 `SCIGBLAST_RUNTIME_BIN_DIR` 覆盖，数据库需单独挂载。
- `IR_split_merged/`、根目录 `pipeline/`、`preprocess/` 当前没有注册为 Web 流程；不要根据目录名推断它们是生产入口。

## Web 与输出契约

`web/app.py` 管理路径校验、队列、进程组、状态、审核和结果接口；`web/auth.py` 管理账户权限；`web/submission.py` 创建不可变 XLSX 工作副本。现有四条流程首次执行 Match 后进入 `WAITING_REVIEW`，全部记录完成标注且存在 OK 记录才能继续。

新任务输出由服务器按用户、流程和北京时间分配。流程产物通常位于 `<output>/<stage>/<dataset>/`，日志为 `logs/<dataset>/pipeline.log`，状态为 `.pipeline_state/<dataset>/`；完成标记名称须与注册表和 `pipeline_done()` 对齐。仅运行单 Web 实例、单 Uvicorn worker；网页同时运行一条 pipeline，五条正式 runner 使用同一共享 `flock`，阶段内部并行由各流程配置控制。保留已有任务归属、路径范围、停止进程组及归档行为。

Web 使用 `execution_json` 关联本次 attempt 的启动日志、运行日志边界和进程身份。续跑不能展示旧执行进度；恢复的进程组需核验后才能停止，不得仅凭旧 PID 发送信号。`web/preflight.py` 按匹配/分析阶段检查真实运行环境；`web/table_preview.py` 为普通结果提供有界游标读取，Match 仍保持完整 revision 审核统计。普通结果的 `total` 在扫描完成前可为 null，调用者使用 `has_more`、`next_cursor`、`scan_complete`，不能把尚未扫描完当成零结果。

Base/Pig 使用根目录 `pipeline_igblast_cache.py` 生成一次 IgBLAST 依赖快照，供 stage 与 task 缓存共同使用。涉及工具、参考库、aux、过滤及参数变化时使相应缓存失效；若人工替换参考文件同时保留大小/修改时间，须更新各自配置的 `SCIGBLAST_IGBLAST_REFERENCE_VERSION`。每次重新进入 stage5 时使当前清单的旧聚合失效，成功聚合和摘要原子发布；`output_path` 必须指向实际 task TSV。

根目录 `pipeline_parallel.py` 为 Base/Pig/reMapping 限制在途任务为现有 worker 数的两倍；Base/Pig 的多链 FASTA 计数仅在同次运行内共享。性能修改必须保留阶段顺序、分析参数、过滤规则、文件命名、列及最终记录顺序；不要依据合成调度测试提高生产并发。

reMapping 结果页按样本和链展示最终 UMI 文件；条数/保留比例来自现有摘要，不等于 copy 总和。`web/artifact_download.py` 流式打包所选 03 文件，保持原文件内容和相对目录，不写入分析输出目录；下载准备 POST 返回 120 秒有效、当前用户/任务绑定的 URL。结果浏览状态按文件保留，文件版本变化重置游标；共享确认与 busy 反馈位于 `web/static/ui.js`。

详情页用 `sessionStorage` 按当前登录用户 ID 和任务 ID 保存浏览偏好。刷新后恢复筛选、样本列表页码、当前文件及表格搜索/字段选择，表格重新读取第一页；不持久化结果行、游标、下载选择或令牌。执行 attempt 变化时清除旧浏览状态。当前文件不在列表页时，通过有界结果查询重新核验，不根据浏览器记录判断下载权限。

`web/mapping_results.py` 按三个摘要的文件版本和执行 attempt 构建有限 LRU 索引。新任务在内部 `options_json.mapping_inputs` 保存创建时实际选中的源文件，只用于结果完整性展示，不传给 runner 或详情 API；历史任务仅合并已有摘要并标记清单不完整。`/mapping-results` 分页返回处理状态、原因和计数，计数依据摘要，文件可用性只对当前页实时核验；单文件预览与下载直接查索引后核验目标。不要根据样本与链的笛卡尔积猜测缺失输入，也不要把文件缺失计作生物学零结果。

IR `08.preprocessing.py` 的 `read_sample()` 将 Future 和读取临时变量限制在函数内，使上一样本大表在下一次内存检查前可释放；主流程仍按原顺序计算、写出并保存 checkpoint。不要把该读取循环移回长生命周期的 `main()` 局部作用域。

IR/10X stage7 后处理仅在本次进程内复用同一 FASTA 的计数，按真实路径及文件版本识别多链软链接。重新执行会重新计数；缺失/空文件、旧批次目录和 AIRR 行数回退保持原行为，不使用可能过期的阶段汇总替代输入计数。

## Mapping 当前状态

`mapping/fasta.py` 递归读取 `<sample>__<chain>.csv`，要求 `CDR3(pep)`、`copy`、`joinedSeq`，生成 `>零基行号_CDR3_copy` 的 FASTA，保留相对子目录及 copy 文本。支持 CLI，独立转换默认仅 IGH；`copy` 不展开或加权序列。目录中有 `AL_s003_v8__IGH.csv` 示例。

`mapping/umi_count.ipynb` 的整理逻辑已提取到 `mapping/pipeline/umi_count.py`，从 AIRR 的 `sequence_id` 末尾还原 copy 为 `umi_count`，没有该列时兼容历史 `barcode` 列。不做 UMI 聚类或重新计数；原 notebook 保留。

第五条 Web 流程 `mapping` 已注册，入口为 `mapping/pipeline/run_mapping_pipeline.sh`，三阶段由 `run_mapping.py` 执行。仅 human：扫描目录后按链多选，只处理所选链；不要求 Submission/Barcode/Match 审核。阶段为 `01.fasta`、`02.igblast`、`03.umi_count`，各阶段成功后写 DONE，全部成功才写 `.pipeline.DONE`。02 和 03 各输出筛选后的 `<样本>__<链>.tsv`，03 添加 umi_count；不输出 raw/filtered 两份文件，逐文件续跑。数据库默认沿用现有 `/data/scAnalyis/Scigblast/igblast` 挂载，通过模块配置或环境变量覆盖。命令见 `mapping/README.md`；实施设计见 `plans/20260914_mapping_module_plan.md`。不要直接调用依赖 FASTQ manifest 的 Base stage5。

`mapping/pipeline/resources.py` 使用主机可用内存、cgroup v1/v2 上限及使用量、CPU 配额/亲和性计算实际线程和 worker 数，保留 5% 内存余量（至少 256 MiB）。单 worker 内存配置是排程估计，不是硬性 RSS 限制；余量后不足一个 worker 时明确报错。

## 开发与验证

Base/Pig 已支持 `sample_source=directory`，`pipeline_input_manifest.py` 扫描并验证明确配对，`web/input_manifest.py` 保存用户绑定快照和不可变清单；API 为 `/api/input/scan`、分页扫描视图、任务 `/input-assignments` 与 `/input-rematch`。目录模式绕过外部 XLSX，仍生成原 Match CSV、进入人工审核；严禁将 requires_submission=false 分支当通用模式，该分支仍为 reMapping 专用。runner 接收 `SCIGBLAST_RUN_INPUT_MANIFEST/HASH`，检查失败必须停止，不能复用旧成功 Match。Base/Pig 阶段指纹包含清单与共享模块。IR/10X 尚未开放目录模式；文件/样本身份不得通过目录名或 Chain 猜测 Barcode。UI 状态组件在 `web/static/directory-input.js`。验证用 `web/tests/test_directory_input.py`。

首页流程介绍同时提供 `web/static/demos/scigblast-teaching-v1.zip` 合成教学包与 README；由 `web/tools/build_teaching_demo.py` 生成，`web/tests/test_teaching_demo.py` 验证真实转换和四条 Match。教学数据仅练习格式、选择、提交与审核，随机序列不保证有效免疫注释，FASTQ 不模拟 IR/10X 建库。不要把真实用户数据打包进静态下载；更新包时同步教程、检查点和 Docker 白名单。

Mapping 扫描还按 `sample_key=相对目录/样本名` 展示样本及链，默认全选，用户可多选样本。API 字段为 `mapping_samples`，runner 环境变量为 JSON 数组 `SCIGBLAST_MAPPING_SAMPLES`；任务保存具体选择，三个阶段仅处理“所选链 ∩ 所选样本”的源文件。未传样本选择的旧任务/CLI 默认全部样本，显式空列表拒绝执行。

所有 shell 命令按项目要求加 `rtk`；无对应过滤器时使用 `rtk proxy <command>`。Git 历史采用 `Add/Fix/Improve/Unify ...` 英文祈使句提交说明，未发现统一 formatter/linter 配置。

```bash
rtk proxy python -m pip install -r web/requirements.txt httpx
rtk proxy python -m unittest discover -s web/tests -v
rtk proxy python -m unittest discover -s web/tests -p test_workflow.py -v
rtk proxy docker compose -f web/docker-compose.yml build
rtk proxy docker compose -f web/docker-compose.yml up -d
```

Web 测试使用临时数据及模拟进程，不代表真实 IgBLAST 已通过验证。分析工具链在 Linux/Docker 验收。镜像由 `web/Dockerfile` 和 `web/Dockerfile.dockerignore` 白名单打包，新增流程需同步 `web/check_bundle.py`；宿主机脚本修改需重建镜像才生效。`deploy.sh` 会先拉取代码并重建服务，不能作为普通本地检查执行。

用户指定测试服务器通过 `ssh remote_nanhua` 访问；测试数据在 `/colddata/SCigblast/data/SCigblastWeb/web_test/20260909`。远端验证使用独立临时输出和受限资源容器，输入/参考库只读挂载；不要直接修改在线服务、生产结果或并发配置。

修改前检查 `rtk git status --short`，保留已有未提交改动。不要批量改写或提交 `data/`、`reference/`、数据库、结果目录及工作簿。按用户指定保留本文件名 `AGENT.md`；后续 AI 请显式读取本文件。
