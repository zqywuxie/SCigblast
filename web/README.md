# SCigblast Web Runner

这是一个轻量执行面板，不替代四条 pipeline。后端只调用当前服务器上的：

- `IR_split/pipeline/run_ir_pipeline.sh`
- `10X_split/pipeline/run_10x_pipeline.sh`
- `Igblast_base/pipeline/run_igblast_base.sh`
- `PigIgblast/pipeline/run_pig_pipeline.sh`

## Linux Docker 部署

默认目录布局：`web/`、`reference/`、`IR_split/`、`10X_split/`、`Igblast_base/`、`PigIgblast/` 位于同一个项目根目录。
Docker 构建上下文是项目根目录，使用 `web/Dockerfile`。四条 pipeline（包括 tools/models）复制到镜像 `/opt/scigblast`，默认 Barcode CSV 复制到 `/opt/scigblast/reference/8bp_barcodes.csv`；运行时不再挂载宿主机源码。非 Docker 启动仍默认使用 `web` 的上一级。

IR / 10X 的 Barcode 默认使用项目下 `reference/8bp_barcodes.csv`，网页自动填入，也可以选择其他 CSV；空值提交会使用该默认文件。Base / Pig 不自动传入 Barcode。文件树会提供项目 `reference` 目录；缺少默认文件时校验明确报错，不会猜选其他 CSV。浏览器之前记住的自定义 Barcode 路径仍优先保留。
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

脚本会检查 Docker Compose、构建并重建容器，最后轮询 `/health` 健康检查。也可以通过 `SCIGBLAST_ENV_FILE=/path/to/.env bash deploy.sh` 指定配置文件。

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

页面首页是全宽任务控制台：可以按状态、Pipeline 和关键词筛选任务；“新建任务”从右侧抽屉打开，并提供路径验证。输入目录、Submission、Barcode CSV 和输出目录都可以手动填写，也可以点击“浏览”通过服务器文件树选择（仅显示 `SCIGBLAST_ALLOWED_*_ROOTS` 下的内容）。任务详情页分为运行概览、Match 审核、实时日志、输出文件和参数记录五个标签。日志按字节增量读取，支持暂停刷新、自动滚动和复制。

默认结果目录为 `/colddata/zqy/SCigblast/results/web_output`，也可以在页面填写允许根目录下的其他输出目录。`runtime/scigblast.sqlite3` 保存任务和操作记录；pipeline 的 FASTQ、FASTA、TSV、日志和 DONE marker 仍写入用户指定的 output。

## 使用流程

1. 选择 Pipeline，页面展示对应流程图；填写操作者、输入/输出和需要的 Barcode CSV。点击“验证路径”检查入口工具与 Python 库缺项。
2. 上传 XLSX，或选择服务器 Submission 文件/目录后点击“查看 / 编辑”。按工作表分页查看；支持修改样本、Dual Index、Chain、Barcode、Species 和 Note。Note 合并/继承区域一起修改；前缀替换先显示影响行数，目标目录可浏览选择。
3. 保存工作副本，原始 XLSX 不变。每次编辑生成新版本，当前版本和原始副本均可下载；副本与任务记录保存在 `web/runtime`。
4. 创建任务首次只做 Match，进入 `WAITING_REVIEW`。按样本/原因搜索、只看 ERROR、分页查看全部记录。支持逐行勾选、全选当前页、跨页保留选择，再批量“已核对 / 待补资料 / 清除标注”；更改筛选或清单版本会清空选择。标注不改变系统 OK/ERROR 状态。
5. 在 Match 页检查后“审核并继续”。确认绑定当前清单版本，至少一条 OK 才能继续；ERROR 保留，不进入下游。
6. 补齐资料时点击“编辑资料 / 重新 Match”，再次确认后复用既有样本级断点。若先前确认的样本归属/Barcode/Chain 被改变，禁止复用旧输出，需建立新任务和新输出目录。
7. 中途失败或停止后点击“断点续跑”。未完成审核的任务不能通过该按钮跳过审核。
8. “IgBLAST 统计”分页展示原始 chain_summary 列，可搜索样本/链并下载；不跨链相加、不重算分析指标。input=0 但 mapped>0 的行标红。

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
- 当前版本面向受信任的 Linux 内网；如果需要公网或跨网段访问，应在前面增加 Nginx/Basic Auth，不要直接暴露 8000 端口。

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

仅有镜像的服务器不要使用 `deploy.sh`（它会要求从源码重建）。数据/数据库挂载仍需配置正确；网页仍可选择自定义 Barcode CSV。

镜像打包不是代码加密：普通网页用户看不到源码，但有 Docker/服务器管理权限的人仍可从镜像提取脚本。
