# SCigblast Web Runner

这是一个轻量执行面板，不替代各条 pipeline。后端只调用当前服务器上的：

- `IR_split/pipeline/run_ir_pipeline.sh`
- `10X_split/pipeline/run_10x_pipeline.sh`
- `Igblast_base/pipeline/run_igblast_base.sh`
- `PigIgblast/pipeline/run_pig_pipeline.sh`
- `mapping/pipeline/run_mapping_pipeline.sh`：human CSV → FASTA → IgBLAST → UMI count 整理，详见 [reMapping 使用说明](../mapping/README.md)。

reMapping 为第五条独立流程：选择 CSV 目录后先扫描，按实际发现的链与文件数多选，仅处理所选链。无需 Submission、Barcode 或 Match 审核；三阶段全部完成才显示成功，结果页提供转换/比对/UMI 汇总以及逐文件预览和下载。以下四条 FASTQ 流程的 Match 使用说明仍适用于 IR、10X、Base 和 Pig。

reMapping 扫描还展示识别到的样本及其来源路径、链和文件数，样本默认全选，可取消不需要的样本；实际只处理所选样本中的所选链。详情页显示保存的样本范围，断点续跑保留该选择。

## Linux Docker 部署

本版启用登录与管理员注册码注册。在 `web/.env` 中配置初始管理员，部署启动时自动创建；没有内置默认密码。
完整设置与升级说明见 [ACCOUNTS.md](ACCOUNTS.md)。

```dotenv
SCIGBLAST_ADMIN_USERNAME=zqy
SCIGBLAST_ADMIN_PASSWORD='请替换为你自己的至少6位密码'
SCIGBLAST_ADMIN_DISPLAY_NAME=ZQY
```

`SCIGBLAST_ADMIN_DISPLAY_NAME` 为可选英文显示名称，留空使用用户名。仅在数据库没有任何管理员时创建；重复部署不覆盖密码、姓名或启用状态。全部留空时仍可使用命令行创建管理员。

默认目录布局：`web/`、`reference/`、`IR_split/`、`10X_split/`、`Igblast_base/`、`PigIgblast/` 位于同一个项目根目录。
Docker 构建上下文是项目根目录，使用 `web/Dockerfile`。四条 pipeline（包括 tools/models）复制到镜像 `/opt/scigblast`，默认 Barcode CSV 复制到 `/opt/scigblast/reference/8bp_barcodes.csv`；运行时不再挂载宿主机源码。非 Docker 启动仍默认使用 `web` 的上一级。

IR / 10X 的 Barcode 默认使用项目下 `reference/8bp_barcodes.csv`，网页自动填入，也可以选择其他 CSV；空值提交会使用该默认文件。Base / Pig 不自动传入 Barcode。每次打开新建任务都会清空上次填写的内容和 Submission 工作副本，仅恢复系统默认 Barcode，不再从浏览器恢复旧任务路径。
已有 `web/.env` 不会被自动覆盖；旧 `SCIGBLAST_HOST_PIPELINE_ROOT` 已不再使用，可以删除。

1. 构建机器需要完整项目源码；使用已构建镜像的 Linux x86-64 服务器只需 Compose 配置、原始数据、Submission 和 IgBLAST 数据库。镜像内安装完整分析环境，不需要激活或挂载宿主机 Conda。数据库继续使用各 pipeline 配置中的路径；数据库在 `/colddata` 外时，需要另加同路径只读挂载。
2. 复制并编辑 `.env.example`：

```bash
cp .env.example .env
```

确保容器内路径与 submission 的 `Note` 路径一致。尤其不要把宿主机 `/colddata` 映射成另一个容器路径，否则 Note 匹配会失败。
3. 启动：

```bash
cd web
docker compose build
docker compose up -d
docker compose logs -f scigblast-web
```

也可以在仓库根目录使用一键部署脚本（首次运行会提示配置 `web/.env`）：

```bash
bash deploy.sh
```

首次缺少 `web/.env` 时会生成配置模板并退出，请填写路径和初始管理员账号、密码后重新执行。已有镜像时使用 `bash deploy.sh --no-build`；查看帮助使用 `bash deploy.sh --help`。无需先激活 Conda。

脚本先执行当前分支的 `git pull --ff-only`，然后重新加载更新后的 deploy.sh，再检查 Docker Compose、构建并重建容器，最后轮询 `/health`。有未提交的受版本管理文件改动、无上游、分支分叉或网络错误时停止，不自动 stash/reset。未跟踪文件若阻挡拉取，Git 也会报错停止。`web/.env` 不被覆盖。服务器此前手动修改过 Dockerfile 或 pipeline 配置时，先备份并整理这些改动再拉取。`--no-build` 也会拉取仓库，但现有镜像不会包含新拉取的代码；发布代码更新应使用默认模式。也可以通过 `SCIGBLAST_ENV_FILE=/path/to/.env bash deploy.sh` 指定配置文件。

浏览器访问 `http://服务器地址:8000`。

### 镜像内分析环境

Web 和四条 pipeline 使用同一个 `/opt/conda` 环境。`environment.yml` 固定核心分析依赖版本（fastp、PANDAseq、IgBLAST、BLAST、NumPy、pandas、Biopython、scikit-bio、parmap），`requirements.txt` 固定 Web 直接依赖版本。构建时执行 `check_runtime.py` 检查导入和工具启动，实际解析出的 Conda/Python 包清单保存在镜像 `/app/conda-explicit.txt`、`/app/python-packages.txt`。这些是版本记录，不是已预生成的全依赖锁文件。

镜像设置 `SCIGBLAST_RUNTIME_BIN_DIR=/opt/conda/bin`，四条 pipeline 只覆盖 Python/fastp/PANDAseq/IgBLAST 的可执行路径，包括 Pig 原先写死的 IgBLAST 路径；数据库、模型计算定义、过滤及并发参数不变。脱离容器且未设置这个变量时，仍使用各自 `00.pipeline_config.env` 的工具配置。网页续跑旧任务也使用当前镜像环境，不沿用旧 PATH。

旧 `SCIGBLAST_HOST_TOOL_ROOT` 已不再使用，可从 `web/.env` 删除；不要挂载宿主机 Conda 覆盖 `/opt/conda`。修改环境文件后重新执行 `bash deploy.sh` 构建，首次需要联网下载依赖。部署后可验证：

```bash
cd web
docker compose exec scigblast-web python /app/check_runtime.py
```

环境安装方式参考 [micromamba 官方 Docker 指南](https://micromamba-docker.readthedocs.io/en/latest/quick_start.html)。工具/库版本固定不等于与旧宿主机环境结果逐字一致；首次迁移应使用已有小数据对照验收。

> 如果服务器的 8000 端口已被占用，可在 `.env` 中设置 `SCIGBLAST_WEB_PORT=8080`，此时访问 `http://服务器地址:8080`。

页面首页是全宽任务控制台：可以按状态、Pipeline 和关键词筛选任务；“新建任务”以居中弹窗打开，先选择 Pipeline，再填写路径。输入目录、Submission、Barcode CSV 和输出目录都可以手动填写，也可以点击“浏览”通过服务器文件树选择（仅显示 `SCIGBLAST_ALLOWED_*_ROOTS` 下的内容）。详情页提供运行概览、Match 审核、实时日志、输出文件、参数记录和 IgBLAST 统计。日志按字节增量读取，支持暂停刷新、自动滚动和复制。

默认结果目录按 `results/姓名_账号短编号/Pipeline类型/时间/` 分层，例如 `/colddata/zqy/SCigblast/results/ZQY_a1b2c3d4/ir_split/20260909_143000/`。目录名取登录账户姓名并追加账号 ID 前 8 位；空格等路径特殊字符转为下划线。改名仅影响新任务，旧任务仍沿用原路径；Pipeline 类型为 `ir_split`、`10x_split`、`igblast_base` 或 `pig_igblast`；时间格式为 `YYYYMMDD_HHMMSS`，使用北京时间。同一用户、同一流程的同秒任务追加 `_02` 等编号，分析阶段目录写入各自的时间目录。`SCIGBLAST_DEFAULT_OUTPUT_ROOT` 仍表示最外层结果根目录，无需调整现有挂载；原始默认值 `/colddata/zqy/SCigblast/results/web_output` 自动兼容为其父目录。已有任务不迁移，续跑、下载及删除均沿用记录中的结果路径；删除新任务只移除其时间目录，保留用户和流程父目录。`runtime/scigblast.sqlite3` 保存任务和操作记录。

## 使用流程

Match 审核要求当前版本全部记录（包括其他分页和 ERROR 记录）都有“已核对”或“待补资料”标注；存在未标注记录时无法确认进入下游。重新 Match 后须重新标注。标注不改变匹配状态，仅 OK 记录参与下游分析。

IR_split 自动识别 raw 和已带 UMI 标签的 presplit 输入，网页无需设置 IR 专用参数。主流程统一执行 representative → IgBLAST → preprocessing，命令行同样不再选择 merged 或跳过 preprocessing。

1. 登录后选择 Pipeline，页面展示对应流程图；填写输入/输出和需要的 Barcode CSV。操作者由后端绑定登录用户，无需填写。点击“验证路径”检查入口工具与 Python 库缺项。
2. 统一在“Submission 文件或目录”中选择路径，再点击同一处“查看 / 编辑”。按工作表分页查看；支持修改样本、Dual Index、Chain、Barcode、Species 和 Note。Note 合并/继承区域一起修改；前缀替换先显示影响行数，目标目录可浏览选择。编辑器仍支持用补齐后的 XLSX 替换工作副本。
3. 保存工作副本，原始 XLSX 不变。每次编辑生成新版本，当前版本和原始副本均可下载；副本与任务记录保存在 `web/runtime`。
4. 创建任务首次只做 Match，进入 `WAITING_REVIEW`。按样本/原因搜索、只看 ERROR、分页查看全部记录。支持逐行勾选、全选当前页、跨页保留选择，再批量“已核对 / 待补资料 / 清除标注”；更改筛选或清单版本会清空选择。标注不改变系统 OK/ERROR 状态。
5. 在 Match 页检查后“审核并继续”。确认绑定当前清单版本，至少一条 OK 才能继续；ERROR 保留，不进入下游。
6. 补齐资料时点击“编辑资料 / 重新 Match”，再次确认后复用既有样本级断点。若先前确认的样本归属/Barcode/Chain 被改变，禁止复用旧输出，需建立新任务和新输出目录。
7. 中途失败或停止后点击“断点续跑”。未完成审核的任务不能通过该按钮跳过审核。
8. “输出文件”中的 IgBLAST 报告展示原始 chain_summary 列，可搜索样本/链并下载；不跨链相加、不重算分析指标。IR 还可预览 preprocessing 的 Datapoint。
9. “输出文件”优先提供 IR 拆分、10X 预筛选/拆分、PANDAseq、fastp 等阶段统计卡片，可分页、搜索及下载原始 CSV/TSV。默认折叠路径字段，不修改计数定义。参数与记录展示中文操作名和第几次 Match，不显示内部哈希；后台仍保留版本校验。
10. 非运行任务可在列表或详情点击“删除”，输入 `DELETE` 确认。删除任务记录及其独占的服务器结果目录，不删除原始数据、Submission 或共享工作副本。公共根目录、与其他任务共享/嵌套的输出、符号链接或无法验证归属的目录会被拒绝；不会自动删除任何历史任务。删除不可恢复，需保留的结果请事先备份。

Web 启动的任务最多同时运行 1 条 pipeline。具体 fastp、PANDAseq、IgBLAST 并发和内存策略仍由各 pipeline 自己的 `00.pipeline_config.env` 控制。

`SCIGBLAST_MAX_ACTIVE_JOBS` 不是可调参数，旧 `.env` 中该值不会改变串行行为。界面“进行中”还包括正在归档的任务，但归档不会占用分析运行位。

创建和出队启动均执行当前阶段的环境检查：四条 FASTQ 流程首次只匹配样本，确认分析/续跑时检查比对工具、Python 依赖及 Match 中实际链的数据库；reMapping 直接检查所选 human 链和比对环境。页面路径检查不能替代服务端启动检查。

详情默认读取本次 attempt 的日志和进度；启动失败、等待共享资源锁分别显示原因。Web 进程单独重启后若旧 runner 仍存活，先核验任务进程标识，禁止重复续跑；用户可停止核验通过的上次执行。旧任务缺少身份标识时不会凭历史 PID 杀进程，应由管理员核实状态。

普通 CSV/TSV 预览使用有界读取；总数未知时显示已读取记录和“继续加载/继续查找”，不会为了首屏重复扫描大表。文件更新后游标失效，重新从首页加载。Match 审核仍基于完整清单与版本校验。reMapping 优先提供 03 UMI count 样本结果，FASTA 不提供预览或下载。

Base/Pig 的 stage 与 task 缓存共同验证 IgBLAST 工具、参考库 sidecar、aux 和参数版本；参考文件保留大小/修改时间替换时，应更新各分支配置的 `SCIGBLAST_IGBLAST_REFERENCE_VERSION`。部署修改需重建镜像；新版本首次遇到旧缓存会重新验证/执行相应阶段。

必须使用单 Web 实例、单 Uvicorn worker。第二条及后续 Web 任务排队，等待审核不占运行位，停止中的进程仍占位。Compose 的 `300g` 是整个容器共享上限，不是每条任务 300GB。所有官方 runner（包括 reMapping）在处理前都会获取共享 `flock`，因此 Web 与终端直接启动的任务也会串行。默认锁文件为 `/colddata/SCigblast/results/.scigblast-resource.lock`，必须放在 Web 容器与终端 runner 都能写入的共享结果盘。更改结果根目录时，设置 `.env` 的 `SCIGBLAST_RESOURCE_LOCK_FILE`，并让终端 runner 指向映射到同一宿主机文件的路径。任务内部并行度仍由各 pipeline 的 `00.pipeline_config.env` 控制。

`.env.example` 中 `SCIGBLAST_ALLOWED_*_ROOTS` 是容器内路径，并且必须已被 Compose 挂载；Linux 多根用冒号分隔。设置允许根不会自动增加挂载。路径验证不等于数据库/工具真实运行成功；部署后仍需各类型小数据验收，特别是 cgroup 内存限制。`deploy.sh` 检测到运行/排队任务会拒绝重建；更新期间不要提交新任务。

### 验证

```bash
python -m pip install -r web/requirements.txt httpx
python -m unittest discover -s web/tests -v
```

测试使用临时 XLSX/清单和模拟进程，不执行分析工具。`web/tests/serve_fixture.py` 是本地浏览器测试入口（127.0.0.1:8001），禁用于正式部署。真实 Linux 工具链及两任务峰值内存需在服务器另行验收。

## 安全边界

- 页面只允许选择四个固定 runner，不能传入脚本路径或任意命令。
- 输入、submission、barcode 和输出必须位于 `SCIGBLAST_ALLOWED_*_ROOTS`。
- 输入和数据库只读挂载，结果目录可写。
- Web 停止任务时按该任务自己的进程组发送 TERM，不扫描或终止其他用户任务。
- 普通用户只能查看和操作自己创建的任务，管理员可管理全部；旧任务仅管理员可见。
- 当前版本面向受信任的 Linux 工作区。正式使用应在前面配置 HTTPS，并启用 `SCIGBLAST_COOKIE_SECURE=1`；不要明文公网传输登录密码。

## 更新 pipeline

脚本已随镜像打包。修改四条 pipeline 或 `00.pipeline_config.env` 后，等待任务结束，再从项目根执行 `bash deploy.sh` 重建镜像；单独修改宿主机脚本不会影响正在运行的容器。构建只发送白名单代码和默认 Barcode CSV，不打包原始数据、Submission、历史结果、数据库、Web runtime 或 `.env`。构建时还检查四条入口、配置、models、Python 和 Shell 语法。

只需改变数据库路径/并发配置而不想重建时，可通过 Compose override 将特定的 `00.pipeline_config.env` 文件只读挂载到对应 `/opt/scigblast/<分支>/pipeline/00.pipeline_config.env`；不要将整个项目挂到 `/opt/scigblast`，否则会遮住镜像内代码。

### 将镜像交给另一台服务器（无需发送项目源码目录）

在构建机器上：

```bash
docker compose -f web/docker-compose.yml build
docker save scigblast-web:local -o scigblast-web.tar
```

将镜像文件、`web/docker-compose.yml` 和配置好的 `.env` 发送到服务器，在这两个配置文件所在目录执行：

```bash
docker load -i /path/to/scigblast-web.tar
docker compose up -d --no-build
```

仅有镜像、没有 Git 仓库的服务器请直接使用上面的 Docker Compose 命令。新版 `deploy.sh` 要求 Git 克隆目录，默认先拉取再从源码重建；`--no-build` 只适用于已有 Git 仓库且明确使用现有镜像的情况。数据/数据库挂载仍需配置正确；网页仍可选择自定义 Barcode CSV。

镜像打包不是代码加密：普通网页用户看不到源码，但有 Docker/服务器管理权限的人仍可从镜像提取脚本。

## Results 自动归档

部署时安装宿主机 cron，每天北京时间 03:00 检查 Web 任务。仅归档成功完成满 14 天的独立 results 子目录；运行、待审核、失败、停止任务及共享目录不处理。生成目录内的 `results.tar.gz`，逐文件校验后清理已打包文件，保留小于等于 10 MB 的统计表、日志和状态文件供网页查看。无法节省空间时保留原文件。已有 gzip 数据通常压缩收益有限。

输出文件页提供完整压缩包下载；归档后的任务不能直接续跑或重新 Match，可下载解压查看。手动检查：`docker exec scigblast-web python /app/archive_results.py --dry-run`。计划：`crontab -l`；最近一次日志：`~/.local/state/scigblast/archive-results.log`。安装脚本支持 UTC 和 UTC+8 主机，重复部署更新同一条 cron。旧版 Web 未支持归档状态时计划暂不执行清理，部署新版后自动启用。

当前服务器目录：代码 `/colddata/SCigblast/code`，输入 `/colddata/SCigblast/data`，结果 `/colddata/SCigblast/results`。服务器 `.env` 的 `SCIGBLAST_ALLOWED_INPUT_ROOTS` 固定为上述 data 目录，`SCIGBLAST_HOST_RESULTS_ROOT` 指向上述 results 目录。为兼容已有任务，容器内仍映射为历史 `/colddata/zqy/SCigblast/results`；这不会在宿主机复制一份结果。

### 固定数据与结果目录

新部署的输入和 Submission 浏览范围均为 `/colddata/SCigblast/data`；测试数据放在 `data/web_test/20260909`，配套 Excel 放在其 `submission` 子目录。新任务输出由服务器分配到 `/colddata/SCigblast/results/姓名_短编号/Pipeline类型/北京时间/`，网页不提供输出路径选择，API 也拒绝自定义输出路径。已有部署请同步设置 `.env` 的 `SCIGBLAST_ALLOWED_INPUT_ROOTS` 和 `SCIGBLAST_ALLOWED_SUBMISSION_ROOTS`。

迁移部署保留 `/colddata/zqy/SCigblast/results` 作为同一 results 目录的容器兼容挂载；已有 `.env` 的输出根路径可继续使用该别名，以保持历史任务及归档的路径一致。
## 结果浏览与阶段内并发优化（2026-10-05）

- reMapping 按实际输入文件显示“样本 × 链”结果，每页 50 条，支持搜索、链/处理状态筛选、跨页多选和 ZIP 下载。全选只作用于当前页可下载的文件；ZIP 内保留原文件内容与相对目录。02 结果在对应样本行的次级入口，阶段统计折叠，FASTA 不提供预览或下载。
- 状态统计分为处理完成、结果为空、处理失败、尚未完成和已归档，依据已有摘要；缺失文件在当前页单独提示并禁用下载。新任务保存创建时实际选中的文件清单；历史任务没有该快照时明确提示仅能依据现有摘要展示，不把不存在的样本/链组合算作缺失。
- CSV 输入行数、进入比对序列条数和最终序列条数来自已有摘要。保留比例为最终条数 / 进入比对条数；没有分母或历史摘要缺字段时显示 `—`，不把 copy 总和当作序列条数。
- 当前页面中，每个结果保留查询、分页、字段和滚动位置；文件更新后自动重置游标并提示。隐藏页面停止轮询，恢复可见时立即更新；活跃任务状态每 5 秒、非活跃任务每 30 秒刷新。
- 同一浏览器标签页刷新或返回任务后，恢复当前用户的任务页签、样本筛选、列表页码、当前文件、表格查询、每页数量和字段选择。表格从第一页读取最新文件；下载勾选需要重新选择。浏览偏好按用户和任务隔离，任务续跑后重置，浏览器存储不可用时仍可正常浏览。
- Base/Pig/reMapping 使用原 worker 和 IgBLAST 线程配置，有界提交任务（最多 worker × 2）；Base/Pig 同次执行内共用 FASTA 计数。分析阶段、筛选、输出命名、表结构和最终排序不变。
- `web/mapping_results.py` 以摘要文件版本、执行 attempt 和选择范围构建限额索引，预览/下载只实时核验目标文件。页面调用 `artifacts?include_mapping_files=false` 和分页 `/mapping-results`，避免传输完整文件清单；旧的全量接口仍兼容已有调用。
- 新增 `pipeline_parallel.py`、`web/artifact_download.py`、`web/mapping_results.py` 和静态组件均由 Docker 白名单打包；发布方式仍是重建镜像。
## 流程介绍与合成教学包

Base / Pig 新建任务支持“扫描目录，无需 Submission”：选择服务器输入根并扫描，有效配对默认全选；统一设置多条 Chain，点击“应用 Chain 到已选样本”，也可展开每个样本的 Chain 修改。样本可改名；同目录配对缺端、重复读端或空文件不参与选择。仍先执行 Match 并审核，再开始分析。IR / 10X 继续使用 Submission。

目录扫描只读取名称和元信息，不解压读取全部 FASTQ；最多检查 20000 个文件、5000 项配对。沿用流程配置的扩展名和读端后缀，支持 vendor counter。不同目录或 lane 不自动合并；Pig 不同目录同名配对因现有输出语义会阻止提交。目录模式不再要求 Pig 文件名含 Dual Index；物种固定为 pig，Base 沿用其物种配置。

服务器将实际选择保存为独立不可变输入清单（`state/input_manifests`），扫描快照绑定用户/流程/输入根，24 小时有效，每位用户保留最多 16 个。检查、创建、启动和续跑校验所选文件的大小与 mtime_ns；发生改变时需重新扫描，不自动纳入后来新增的文件。此指纹用于变更检测，不是内容哈希。任务详情“编辑资料 / 重新 Match”可恢复目录选择，重新扫描时新增配对默认不选；已批准的样本分配仍受现有确认限制。

首页“流程介绍与示例”按流程显示输入要求、阶段、主要结果、操作练习和检查点；可直接转入对应流程的新建任务窗口。处理细节默认折叠。下载入口为 `/static/demos/scigblast-teaching-v1.zip`，教程为 `/static/demos/README.txt`。

教学包完全合成：2 个样本、3 份 reMapping CSV（6 条序列）、2 对 FASTQ（各 20 对 reads）、4 份 Submission XLSX 及 Barcode CSV。用户需将文件解压到服务器允许的路径，并将 Submission 的 Note 占位路径改为实际输入路径。网页不自动上传或提交任务。FASTQ 用于练习到 Match 审核，不模拟 IR/10X 建库结构；reMapping 随机序列也可能无有效注释，不能用作完整分析或生物学准确性验证。

维护时运行 `python web/tools/build_teaching_demo.py` 重新生成 ZIP，再运行 `python -m unittest discover -s web/tests -p test_teaching_demo.py -v`：直接验证发布包的 FASTQ 格式、真实 CSV 转换和四条流程的 Match。`web/Dockerfile.dockerignore` 明确允许打包上述两个静态文件；镜像更新仍需重建。
