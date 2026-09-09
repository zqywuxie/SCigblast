# SCigblast Web Runner

这是一个轻量执行面板，不替代四条 pipeline。后端只调用当前服务器上的：

- `IR_split/pipeline/run_ir_pipeline.sh`
- `10X_split/pipeline/run_10x_pipeline.sh`
- `Igblast_base/pipeline/run_igblast_base.sh`
- `PigIgblast/pipeline/run_pig_pipeline.sh`

## Linux Docker 部署

本版启用登录与管理员注册码注册。在 `web/.env` 中配置初始管理员，部署启动时自动创建；没有内置默认密码。
完整设置与升级说明见 [ACCOUNTS.md](ACCOUNTS.md)。

```dotenv
SCIGBLAST_ADMIN_USERNAME=zqy
SCIGBLAST_ADMIN_PASSWORD='请替换为你自己的至少12位密码'
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

默认结果目录为 `/colddata/zqy/SCigblast/results/姓名拼音首字母_pipeline_YYYYMMDD_HHMMSS`，按北京时间命名；同秒同名冲突追加 `_02` 等编号。例如郑钦云对应 `zqy_ir_split_20260909_143000`，页面和操作历史保留完整姓名。拼音按默认读音转换，多音姓名可直接填写期望的英文缩写。`SCIGBLAST_DEFAULT_OUTPUT_ROOT` 表示独立任务目录的父目录；原始默认值 `/colddata/zqy/SCigblast/results/web_output` 自动兼容为其父目录，新任务不再共享 web_output，旧任务路径不迁移。页面填写自定义输出时使用该目录本身，不额外追加命名。`runtime/scigblast.sqlite3` 保存任务和操作记录。

## 使用流程

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

任务默认最多同时运行 2 条 pipeline，具体 fastp、PANDAseq、IgBLAST 并发和内存策略仍由各 pipeline 自己的 `00.pipeline_config.env` 控制。

必须使用单 Web 实例、单 Uvicorn worker。第三条排队，等待审核不占运行位，停止中的进程仍占位。这个限制只管理网页启动的任务，不统计用户直接在终端启动的 pipeline。Compose 的 `300g` 是整个容器共享上限，不是每条任务 300GB；高内存组合未验收前可将 `SCIGBLAST_MAX_ACTIVE_JOBS=1`，代码无论如何最多允许 2。

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
