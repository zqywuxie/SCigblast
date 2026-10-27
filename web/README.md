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

结果文件的属主由容器运行账号决定。镜像只在安装/打包阶段使用 root，最后切回普通用户；Compose 必须指定宿主机账号的 `SCIGBLAST_UID` / `SCIGBLAST_GID`。`deploy.sh` 在配置缺失或空值时用 `id -u` / `id -g` 自动填入 `web/.env`，拒绝零 UID/GID。直接使用 Compose 前需先填写这两个数字；例如 remote_nanhua 的 zhengqinyun 为 `1012:1014`。`SCIGBLAST_DATA_GID` 可追加一个共享数据组，留空沿用主 GID；该服务器的 nhstu 组为 `1024`。账号映射采用数字 ID，宿主机新产物会显示为该宿主机用户，无需在容器内创建同名账号。任意 UID/GID 的运行方式见 [micromamba 官方说明](https://micromamba-docker.readthedocs.io/en/stable/advanced_usage.html#changing-the-user-id-or-name)。

容器 HOME 与缓存位于可写的运行状态目录 `/var/lib/scigblast-web/home`。部署会先用目标用户检查状态、SQLite、结果和共享锁的访问权限，失败时保留旧服务。旧 root 部署首次迁移时，需等待主分析、树图及归档进程结束，备份 `web/runtime` 后将该目录和本平台结果目录中 root 拥有的对象移交给目标 UID/GID；保留其他用户的文件、权限、ACL、锁文件及参考库。已有 Docker 权限的账号可通过一次性维护容器完成，无需 sudo；不可只切换运行用户后忽略旧 SQLite/结果文件的权限。

Base/Pig 逐链结果及 Pig 完成标记的原子写入遵循正常文件创建模式和目录 ACL：使用独占创建的唯一临时文件，按 umask/default ACL 生成最终权限，而不保留 mkstemp 的固定 0600。没有默认 ACL 时，umask 0022/0002 对应 0644/0664；有 ACL 时由内核计算有效权限。该修复影响后续写入，不自动更改历史文件的属主或权限；单纯修复旧文件权限无需重新 mapping。

Web 和四条 pipeline 使用同一个 `/opt/conda` 环境。`environment.yml` 固定核心分析依赖版本（fastp、PANDAseq、IgBLAST、BLAST、NumPy、pandas、Biopython、scikit-bio、parmap），`requirements.txt` 固定 Web 直接依赖版本。构建时执行 `check_runtime.py` 检查导入和工具启动，实际解析出的 Conda/Python 包清单保存在镜像 `/app/conda-explicit.txt`、`/app/python-packages.txt`。这些是版本记录，不是已预生成的全依赖锁文件。

镜像设置 `SCIGBLAST_RUNTIME_BIN_DIR=/opt/conda/bin`，四条 pipeline 只覆盖 Python/fastp/PANDAseq/IgBLAST 的可执行路径，包括 Pig 原先写死的 IgBLAST 路径；数据库、模型计算定义、过滤及并发参数不变。脱离容器且未设置这个变量时，仍使用各自 `00.pipeline_config.env` 的工具配置。网页续跑旧任务也使用当前镜像环境，不沿用旧 PATH。

旧 `SCIGBLAST_HOST_TOOL_ROOT` 已不再使用，可从 `web/.env` 删除；不要挂载宿主机 Conda 覆盖 `/opt/conda`。修改环境文件后重新执行 `bash deploy.sh` 构建，首次需要联网下载依赖。部署后可验证：

```bash
cd web
docker compose exec scigblast-web python /app/check_runtime.py
```

环境安装方式参考 [micromamba 官方 Docker 指南](https://micromamba-docker.readthedocs.io/en/latest/quick_start.html)。工具/库版本固定不等于与旧宿主机环境结果逐字一致；首次迁移应使用已有小数据对照验收。

若构建在 `libltdl7` 下载时出现 `Could not connect to deb.debian.org:80`，这是 Debian 源连接超时。该后置安装步骤默认改用清华 HTTPS 镜像，并配置 3 次重试和 30 秒连接/读超时；原 Conda、pip 安装步骤及其之前的指令保留，以便继续复用成功缓存。不要使用 `--no-cache` 重试。镜像地址是 `SCIGBLAST_APT_MIRROR` 构建参数（不是运行时环境变量），从仓库根目录可使用其他镜像，例如：

```bash
rtk proxy docker compose --progress plain --env-file web/.env --project-directory web -f web/docker-compose.yml build --build-arg SCIGBLAST_APT_MIRROR=https://mirrors.ustc.edu.cn scigblast-web
```

该命令仅构建镜像，不替换运行中的容器；构建通过后再使用正常部署流程。此配置只改变 Conda/pip 之后的 APT 步骤，以保留其缓存；全新构建前置系统依赖步骤仍使用基础镜像的源。

> 如果服务器的 8000 端口已被占用，可在 `.env` 中设置 `SCIGBLAST_WEB_PORT=8080`，此时访问 `http://服务器地址:8080`。

页面首页是全宽任务控制台：可以按状态、Pipeline 和关键词筛选任务；“新建任务”以居中弹窗打开，先选择 Pipeline，再填写路径。输入目录、Submission、Barcode CSV 和输出目录都可以手动填写，也可以点击“浏览”通过服务器文件树选择（仅显示 `SCIGBLAST_ALLOWED_*_ROOTS` 下的内容）。详情页提供运行概览、Match 审核、实时日志、输出文件、参数记录和 IgBLAST 统计。日志按字节增量读取，支持暂停刷新、自动滚动和复制。

任务详情顶部显示“本次运行时长”，运行中按秒更新，结束后固定显示；超过一天时显示天数。排队显示“等待启动”，未执行显示“尚未运行”。时长从本次执行启动算到结束，不包含排队和审核等待；审核后分析、断点续跑或重试均重新计时，不代表跨次累计耗时。

新建任务窗口顶部选择流程，正文集中填写输入和选择样本，底部始终显示实际处理范围、检查状态与创建操作。目录与 reMapping 样本每页显示 20 项，搜索和翻页保留全局选择；“全部样本”和批量 Chain 操作包括其他页及搜索结果外的样本。检查通过后更改输入或处理范围需重新检查，单纯搜索或翻页不会使检查失效。提交期间锁定输入，失败后保留已填内容。

处理链按免疫球蛋白（IGH／IGK／IGL）和 TCR（TRA／TRB／TRD／TRG）分组，支持“仅选 IG”“仅选 TCR”和跨组多选。reMapping 显示每条链的文件数，未发现文件的链不可选。目录模式需点击“应用到已选样本”使批量链设置生效；再次应用会覆盖逐样本配置，应用前会提示当前使用该组合的样本数。

任务详情以紧凑摘要展示状态、运行时长与当前阶段，完整阶段列表点击“阶段明细”展开。五个功能 Tab 随页面滚动固定在顶部（reMapping 无 Match 审核）。Match 将核对进度、筛选与批量标注分区；输出文件保留最终结果、下载与预览，参数与记录按时间线和配置快照展示。定时刷新保留当前 Tab、已展开信息及阅读位置。

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

Web 启动的任务最多同时运行 2 条 pipeline，名额由所有用户和流程共享；第三条及之后的任务按提交顺序排队。具体 fastp、PANDAseq、IgBLAST 并发和内存策略仍由各 pipeline 自己的 `00.pipeline_config.env` 控制。

`SCIGBLAST_MAX_ACTIVE_JOBS` 不是可调参数，旧 `.env` 中该值不会改变固定的 2 条上限。界面“进行中”还包括正在归档的任务，但归档不会占用分析运行位。

创建和出队启动均执行当前阶段的环境检查：四条 FASTQ 流程首次只匹配样本，确认分析/续跑时检查比对工具、Python 依赖及 Match 中实际链的数据库；reMapping 直接检查所选 human 链和比对环境。页面路径检查不能替代服务端启动检查。

详情默认读取本次 attempt 的日志和进度；启动失败、等待共享资源锁分别显示原因。Web 进程单独重启后若旧 runner 仍存活，先核验任务进程标识，禁止重复续跑；用户可停止核验通过的上次执行。旧任务缺少身份标识时不会凭历史 PID 杀进程，应由管理员核实状态。

普通 CSV/TSV 预览使用有界读取；总数未知时显示已读取记录和“继续加载/继续查找”，不会为了首屏重复扫描大表。文件更新后游标失效，重新从首页加载。Match 审核仍基于完整清单与版本校验。reMapping 优先提供 03 UMI count 样本结果，FASTA 不提供预览或下载。

Base/Pig 的 stage 与 task 缓存共同验证 IgBLAST 工具、参考库 sidecar、aux 和参数版本；参考文件保留大小/修改时间替换时，应更新各分支配置的 `SCIGBLAST_IGBLAST_REFERENCE_VERSION`。部署修改需重建镜像；新版本首次遇到旧缓存会重新验证/执行相应阶段。

Pig 会在链专属 C 区数据库存在时传入 `-c_region_db`，让 IgBLAST 的 AIRR 结果输出 `c_call` 等 C 区字段；TCR 仍使用 TCR 模式。没有对应 C 库时继续 V/D/J 分析，不补造 C 注释。C 库新增或更新会使对应链缓存失效，05 筛选和 06 locus 拆分保留完整 AIRR 列。旧的无 C 注释结果需在部署后重新执行 mapping，单独重新拆分或绘制 Treemap 不能补算 C 区。

2026-10-27，remote_nanhua 的 `database_251117/pig_chain` 已补入 IMGT 猪 BCR C 参考：IGH 29 条、IGK 2 条、IGL 2 条，版本为 `imgt-sus-scrofa-C-20261027`。每链保留原始字段来源及 SHA256 的 `pig_gl_<CHAIN>_C.provenance.json`；IGH 中 7 条为有明确标记的部分 C 区外显子参考，不宣称全长。原始导出与构建/验证记录保存在该库的 `.reference_sources/bcr_C_20261027_a8b8497e/`，原有 V/D/J 和 TCR 文件保持原样。来源为 [IMGT/GENE-DB 官方核苷酸导出](https://imgt.org/genedb/directlinks)，不使用整段基因组 BAC 作为单条 C 参考。使用这些新库需要部署含上述 C 区参数修复的 Pig 脚本。

同日已将该猪库 V/J 的 344 条参考 ID 去掉开头 `2026pig`，例如 `2026pigIGHV1-10*01` → `IGHV1-10*01`，版本为 `pig-vj-canonical-20261027_115846`。核苷酸序列与 D/C 标识保留；现存 V/J 索引重建，`pig.aux` 添加规范 J ID 别名且兼容旧库，内部 V 注释保留并经对照验证。完整备份（包括刚补入的 C 库、内部注释及 aux）位于 remote_nanhua 的 `/colddata/zqy/igblast/backups/pig_chain_before_vj_rename_20261027_115658/`，`SHA256.json` 校验全部 316 个原文件。现用库路径不变，详情与验证记录在库内 `.reference_sources/pig-vj-canonical-20261027_115846/`；新 mapping 使用规范名称，已有结果不会自动改名。恢复数据库时须同时恢复配套 aux，并持有平台门锁的独占锁，避免与分析进程并行替换参考。

必须使用单 Web 实例、单 Uvicorn worker。第三条及后续 Web 任务排队，等待审核不占运行位，停止中的进程仍占位。Compose 的 `300g` 是整个容器共享上限，两条任务共用，不是每条任务 300GB。所有官方 runner（包括 reMapping）在处理前获取共享门锁及两个槽位之一的独占 `flock`，Web 与终端直接启动的任务合计最多执行两条。Treemap 持有门锁的独占模式，保持与主分析互斥。默认门锁为 `/colddata/SCigblast/results/.scigblast-resource.lock`，槽位文件为同路径加 `.slot-1` / `.slot-2`；文件不能在任务运行时删除。锁和槽位必须放在 Web 容器与终端 runner 都能写入的共享结果盘。更改结果根目录时，设置 `.env` 的 `SCIGBLAST_RESOURCE_LOCK_FILE`，并让终端 runner 指向映射到同一宿主机文件的路径。任务内部并行度仍由各 pipeline 的 `00.pipeline_config.env` 控制。IgBLAST 链任务的默认及硬上限为每条 pipeline 4 个，五条正式流程均限制；更高配置或 --parallel 值不能突破 4，CPU/内存不足时仍按原资源预算限制执行。这里的 4 是进程任务数，各进程线程数仍由 SCIGBLAST_IGBLAST_THREADS 设置。

`.env.example` 中 `SCIGBLAST_ALLOWED_*_ROOTS` 是容器内路径，并且必须已被 Compose 挂载；Linux 多根用冒号分隔。设置允许根不会自动增加挂载。路径验证不等于数据库/工具真实运行成功；部署后仍需各类型小数据验收，特别是 cgroup 内存限制。`deploy.sh` 检测到运行/排队任务会拒绝重建；更新期间不要提交新任务。

### 验证

```bash
rtk proxy docker compose --env-file web/.env --project-directory web -f web/docker-compose.yml build scigblast-web
```

按维护要求已移除项目测试代码。构建时仍执行正式运行所需的 `check_runtime.py`（依赖导入、分析工具与合成序列组装）和 `check_bundle.py`（打包完整性）检查。后续额外验证放在独立临时目录，结束后清理临时脚本、日志和缓存；正式任务、IgBLAST 和部署日志保留。真实 Linux 工具链及两任务峰值内存仍需按实际输入验收。

## 邮件提醒与 QQ 发件邮箱

用户进入“我的账户 → 邮件提醒”，保存一个接收邮箱；留空保存即可关闭。自动提醒覆盖后续执行的四类情况：开始执行（STARTED）、完成（SUCCEEDED）、失败（FAILED）、中断异常（INTERRUPTED 或 COMPLETED_WITHOUT_MARKER），仅发送给任务所属用户。开始执行提醒在本次 runner 实际获得共享资源锁后入队，不在提交、QUEUED、刚启动进程或等待资源锁时发送。首次 Match、确认后的分析和断点续跑每次执行各提醒一次，并标明执行内容；进度更新不重复发信。邮件含状态、执行次数和需要登录的任务详情链接，不附原始序列或结果文件；启用设置后不补发已经开始或结束的历史执行。等待审核和主动停止不单独发邮件，已过时的开始提醒也会跳过。

管理员进入“用户管理 → 发件邮箱配置”，首次配置已预填 QQ 参数：

| 字段 | 填写内容 |
| --- | --- |
| SMTP 服务器 | `smtp.qq.com` |
| 端口 | `465` |
| 加密方式 | `SSL / TLS` |
| 发件邮箱 | 管理员完整 QQ 邮箱，如 `123456789@qq.com` |
| SMTP 账号 | 与发件邮箱相同的完整地址 |
| SMTP 密码 / 邮箱授权码 | QQ 邮箱生成的授权码 |
| 平台访问地址 | 用户实际访问的平台地址，例如 `http://服务器地址:10321` 或正式 HTTPS 域名 |

QQ 参数可参考[腾讯官方 SMTP 配置表](https://intl.cloud.tencent.com/zh/document/product/1266/71700)。登录 [QQ 邮箱](https://mail.qq.com/)，在设置中找到 POP3/IMAP/SMTP 服务，开启服务并按页面提示生成授权码；密码字段使用授权码，不使用网页登录密码。[授权码获取说明](https://consumer.huawei.com/cn/support/content/zh-cn16108643/)

勾选启用并保存配置，随后在本页上方保存管理员自己的提醒邮箱，点击“发送测试邮件到我的提醒邮箱”。状态显示“已提交给 SMTP 服务器”后检查收件箱与垃圾箱。配置保存后立即生效；账号和授权码只需管理员配置，普通用户不需要 SMTP 设置。

配置与待发队列保存在 `SCIGBLAST_STATE_DIR` 下的 SQLite（Compose 默认 `web/runtime/scigblast.sqlite3`），重建镜像、重启容器后保留，不需要在 `.env` 写邮箱密码。授权码不会在 API 或页面回显，留空保留原值；输入新授权码替换，显式勾选清除才能删除。该数据库包含发件凭证，部署和备份只交由服务器管理员访问。

后台邮件队列独立于分析进程，每项失败最多尝试 3 次（后续重试间隔 60 秒、300 秒）。修改或清空收件邮箱、停用用户、删除任务、续跑导致通知已过时，会跳过尚未发送的旧通知。关闭 SMTP 服务暂停待发队列。同一任务／执行次数／状态只入队一次，重启恢复未确认完成的发送项。测试全程使用模拟 SMTP。

开始提醒由现有进程监控读取本次 `launcher-N.log` 的锁获取标记，按 attempt、PID 和进程 token 核验后，在同一 SQLite 事务中记录 `execution_json.resource_acquired_at` 与邮件事件。此时间为监控观测时间，不改变原有运行时长口径。后台实际发送前再次核验当前用户邮箱、执行次数及运行状态；快速任务已结束时跳过过时的开始提醒，仍按原规则发送完成/失败邮件。发件服务、收件邮箱与重试参数不需要另行配置。

若认证失败，先检查 SMTP 服务已开启、完整邮箱账号与授权码；若连接失败，检查部署服务器是否能出站访问 `smtp.qq.com:465`。SMTP 已接收不代表收件箱立即可见，可检查垃圾箱与邮箱侧限制。

## Treemap 后置分析

每个样本只有一条链出图时，仅输出单链图；至少两条不同链成功出图才生成多链合图。其他链为空或失败时，不为剩余的一张图额外生成合图。Clone Top 和统计 CSV 照常发布，ZIP 与网页按实际生成的图片展示。已完成的历史图片保留；手动重新生成会采用新规则。

新建任务时可勾选“分析完成后生成 Treemap”，默认关闭。开启后，原有分析和结果整理成功才会后台排队绘图；失败、主动停止或等待 Match 审核不会触发。任务详情在“输出文件”旁提供独立“克隆树图”Tab，可用 `/jobs/<任务ID>#treemap` 直达；打开时才加载树图状态，支持刷新和键盘切换。该 Tab 和独立工作台默认不加载图片；选择一个样本或一种链/合图类型即可直接展示，也可组合筛选，每页最多 7 张，无需额外确认。“查看全部”清除条件并显示第一页；轮询保留当前筛选和页码。可下载 PNG、统计 CSV、全部结果 ZIP 或当前样本 ZIP。绘图失败可独立重试，不改变分析状态、结果内容或完成邮件。

首页“树图工作台”是独立入口 `/treemap`：选择已完成任务或填写服务器结果目录 → 扫描样本和链 → 勾选需要的条目 → 生成。支持 IgBLAST base、Pig IgBLAST、IR、10X、reMapping 的最终结果，不限物种；搜索/分页不丢失选择，默认选择全部有效项。无需 Submission，也不重新运行 IgBLAST。目录须包含已注释 AIRR CSV/TSV（支持 gzip），列为 `productive`、`v_call`、`j_call`、`junction_aa`（无此列时使用 `cdr3_aa`）；普通独立文件需明确命名为 `样本_CHAIN` 或 `样本__CHAIN`。未注释的官方 CSV、FASTA、FASTQ 请先运行分析。平台已有任务目录按任务归属校验，不能通过手填路径访问他人的结果。

绘图复用 `treemap_g.py` 的 QR 样式：只统计 `productive == T` 且 CDR3 非空的最终记录，按完整 V/J/CDR3 聚合，每条记录计 1，**不按 copy/umi_count/umi_counts 加权**。块面积表示链内记录占比；多链图的链区采用固定比例排版，面积不表示链间丰度。无有效记录、缺字段、来源文件缺失和绘图失败分别展示。首版不提供俄罗斯方块或 V/J 环形图。

Base/Pig 主流程为五阶段，链筛选已合并进 `05.igblastn_out`。每个“样本 × Chain”任务选用该链参考库比对，一结束就在同一 worker 内单次流式处理原始 AIRR，同时保留 V 命中、productive 且 `locus` 与该任务配置链一致的记录；不等待其他链完成后统一筛选。最终 `<CHAIN>.tsv` 原子发布到 05 的既有样本目录，保留全部 AIRR 列（包括 c_call），筛选后才写逐链完成标记，零结果仅表头。raw 中间文件成功后清理，IgBLAST 日志保留；各链全部完成后只发布总摘要与兼容的 TCR.tsv/BCR.tsv，不再执行独立第六阶段。

`chain_summary.csv` / `igblast_summary.tsv` 增加 locus_input_rows（V/productive 通过数）、excluded_locus_rows（其中其他链或未知 locus 的数量）、unknown_locus_rows（排除的子集）及 locus_filter_version。output_rows 是最终目标链保留数。缺少 locus 或格式错误使该链失败且不写 DONE；已有成功链保留供续跑。筛选版本纳入任务/阶段指纹，旧筛选缓存会失效，升级后明确续跑可能重新执行 mapping。

Treemap 和 Clone Top 新任务直接使用 05 摘要指向的已筛选单链结果，并明确按 `locus` 核对；未完成新筛选的旧 05 结果不能冒充最终结果。目录扫描优先新 05 摘要，忽略同数据集残留的旧 06，避免重复计数。旧六阶段任务仍可查看其 06 结果及阶段信息，旧图不自动重算；明确续跑才转为新五阶段。reMapping 仍使用 `03.umi_count`，IR 使用 `08.preprocessing/junction_peps`，10X 使用 `08.cluster`。普通独立来源在聚合前核对 `chain`，无此列时使用 `locus`；字段为空、未知或属于其他链的记录不计入目标链，两个字段都没有的明确单链文件才沿用文件身份。已有绘图运行保留原统计策略，避免续跑时混用新旧结果。

历史 05 结果仍可手工离线拆分，不重新比对：在项目根目录运行 `rtk proxy python pipeline_locus_split.py --summary <任务输出>/05.igblastn_out/<数据集>/chain_summary.csv --manifest <任务输出>/01.match/<数据集>/sample_manifest.csv --output <任务输出>/06.locus_split/<数据集>`。这是兼容旧结果的工具，不是新主流程阶段；`--check` 只核验完成状态，`--force` 强制重新拆分，不修改 Web 任务状态。

新生成的记录另输出一份 `clone_top.csv`，可点击“下载 Clone Top CSV”，全量 ZIP 也包含该表。默认保留所有克隆，按每个样本、每条链的有效记录数降序排列，可用 `rank` 筛选 Top 10/50/100；计数相同按 V、J、CDR3 原始文本排序。CSV 使用 UTF-8 BOM，字段为：

| 字段 | 含义 |
| --- | --- |
| `sample_key` / `sample_label` | 唯一样本标识 / 样本显示名称 |
| `chain` / `rank` | 链 / 样本链内排名（从 1 开始） |
| `v_call` / `j_call` / `cdr3_aa` | 完整克隆身份，与树图分组一致 |
| `record_count` | 该克隆的有效记录数，不是原始 copy 或 UMI 权重 |
| `frequency_percent` | 该克隆记录数 ÷ 本样本本条链有效记录数 × 100 |
| `cumulative_frequency_percent` | 按排名累计的链内占比，最后一行是 100% |

表格与图片复用同一次聚合结果，不重新读取来源数据；失败链不写入克隆表，原因保留在 `treemap_summary.csv`，全部无有效克隆时仅输出表头。失败重试后重新合并总表，避免重复记录。历史生成记录不会自动补算，可从树图 Tab 或工作台重新生成以获得表格。

关联任务的派生输出在 `<任务输出>/treemap/YYYYMMDD_HHMMSS/`；纯目录绘图由服务器分配到 `<默认输出根>/<用户>/treemap/YYYYMMDD_HHMMSS/`，不写回输入目录。目录时间采用北京时间，同秒多次生成依次加 `_02`、`_03`，并在数据库写事务内检查已登记路径和现存目录，避免覆盖。生成记录 ID 与执行次数保留在数据库；历史记录继续使用已登记的原路径，不迁移目录。单链图位于 `individual_treemaps/<安全样本标识>/`，合图位于 `multi_chain_treemaps/<安全样本标识>/`。各运行保留来源版本、选择、计数规则、PNG、`clone_top.csv` 和 `treemap_summary.csv`。ZIP 流式打包，不在结果目录创建压缩包；全量 ZIP 包含两份 CSV，当前样本 ZIP 仅含该样本图片。历史图片保持原路径，下载 ZIP 时也整理为上述两个目录。关联任务续跑后旧图标为过期；归档时 CSV/PNG 随完整压缩包保存，在线副本可以移除，归档后不能补生成。纯目录绘图首版不纳入 pipeline 的 14 天自动归档规则。

PNG 文件名：单链为 `样本名__CHAIN.png`（如 `Demo_A__IGH.png`），多链合图直接为 `样本名.png`（如 `Demo_A.png`）。ZIP 使用相同命名；不同来源的同名样本由各自的安全样本子目录隔离。历史磁盘文件保留原名称和访问路径。

绘图采用一个后台调度器和有界的多进程批次：默认最多 2 个独立单链 worker，实际数量取 CPU 配额/亲和性、当前可用内存和配置上限的较小值；单链完成后逐样本生成合图。不在进程间传输 DataFrame 或图片。每批次由一个轻量监督进程管理，同组子进程继承 pipeline 的共享资源锁，主分析排队后不再启动下一批。取消/关闭会停止并核验整组进程退出；单链失败独立记录，重试复用成功单链。

可在 `web/.env` 设置以下参数，随后从 `web/` 执行 `docker compose up -d --build`：

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `SCIGBLAST_TREEMAP_MAX_WORKERS` | `2` | 单链最大并行进程数，设为 `1` 恢复串行，最多 32 |
| `SCIGBLAST_TREEMAP_MEMORY_BUDGET_MB` | `4096` | 绘图 worker 合计内存预算（MiB），监督进程另预留 64 MiB |
| `SCIGBLAST_TREEMAP_MAX_RSS_MB` | `2048` | 每个 worker 的 RSS 上限（MiB）；可用内存不足时下调并发或单进程额度 |
| `SCIGBLAST_TREEMAP_TIMEOUT_SECONDS` | `1800` | 每个 worker 的时间预算（秒） |

例如 4 个 worker、每个 2048 MiB，需同时将总预算设为 `8192`，且容器实际 CPU/内存允许才会生效。每个新批次重新检测 cgroup v1/v2 和主机资源，保留至少 256 MiB 或当前可用内存的 5% 余量。单 worker 和进程组 RSS 周期监测属于预算控制，不是容器硬性内存隔离。超预算明确报错，不截断克隆或丢弃低频记录。更改 Python/绘图代码或增加 Pillow 依赖后须重建镜像。

Treemap 验证应使用合成数据和独立临时输出，检查单链/合图、Clone Top 及 ZIP，不发送真实邮件；临时脚本与日志结束后清理。

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
## 操作连续性与状态处理优化（2026-10-22）

- Base/Pig 目录、reMapping 与 Treemap 重扫失败保留原样本选择、名称和链；扫描完整成功后才替换结果。失败后需重新扫描成功才能提交，不能误用旧来源。未变化条目保留编辑，来源版本变化的条目重新选择；首次扫描与重新 Match 的默认选择规则不变。
- 扫描文件系统时显示活动状态与耗时；获得清单总数后才显示分页读取进度。只对瞬时网络故障或 502/503/504 的清单 GET 最多重试两次，不自动重复扫描 POST。切换输入后迟到的响应不覆盖新来源。
- 任务列表轮询局部更新，保留键盘焦点；树图历史只在显示字段变化时重建。快速切换生成记录时，共用一个在途请求并只读取最后选择。手机端保留执行次数；配置状态区分匹配检查与分析检查，正常提示继续折叠。CSV 与 ZIP 使用统一的下载按钮。
- 扫描快照使用进程内有界缓存，最多 8 份、20,000 行、估算 32 MiB；每页仍检查文件身份、用户、流程、目录和有效期。完整提交继续检查真实输入文件。reMapping 索引在全局锁外解析，不同任务分别构建，同 key 共用结果；现有 32 任务/100,000 权重限制不变。
- 历史任务新增 owner/created_at/id 组合索引，创建时间相同的任务也保持稳定分页。操作者、精确统计和权限范围不变，仍使用原页码交互。

Treemap 新运行在 SQLite 使用 `storage_version=2`，状态、产物和合图完成标记分项保存；单链阶段只处理有界批次，合图只读取对应样本。已有 v1 JSON 记录继续读取、重试和完成，不自动迁移。文件、统计 CSV、Clone Top、manifest 与 ZIP 的公开结构和排序保持原约定；绘图 worker 数、计数口径、pipeline 阶段和输出不变。缺失或变更单链图片会阻止合图，重试仅重做受影响链。

部署仍需重建镜像，启动时自动增加兼容表/索引。发布前等待主分析与派生任务退出并备份 SQLite。**数据库已产生 v2 记录后，不能只回退到不支持 v2 的旧程序**：优先向前修复；如需降级，停服并备份当前库，由维护人员在事务中用 `derived_store.load()` 恢复完整 v1 JSON、切换 storage_version 后核对记录/产物数量，或恢复与旧程序对应的数据库备份。不要在在线服务中转换、清理新表，也不要自动迁移历史任务。

额外性能验证使用临时合成数据，分别检查快照缓存、JSON 序列化、SQLite 查询和归档内存；不能将这些指标当成实际 IgBLAST 加速承诺。验证结束后清理临时脚本与日志，保留正式归档的完整内容核验与清理机制。

## 流程介绍与合成教学包

Base / Pig 新建任务支持“扫描目录，无需 Submission”：选择服务器输入根并扫描，有效配对默认全选；统一设置多条 Chain，点击“应用 Chain 到已选样本”，也可展开每个样本的 Chain 修改。样本可改名；同目录配对缺端、重复读端或空文件不参与选择。仍先执行 Match 并审核，再开始分析。IR / 10X 继续使用 Submission。

目录扫描只读取名称和元信息，不解压读取全部 FASTQ；最多检查 20000 个文件、5000 项配对。沿用流程配置的扩展名和读端后缀，支持 vendor counter。不同目录或 lane 不自动合并；Pig 不同目录同名配对因现有输出语义会阻止提交。目录模式不再要求 Pig 文件名含 Dual Index；物种固定为 pig，Base 沿用其物种配置。

新建 Pig 目录任务采用单层样本输出布局：`02.fastp/<dataset>/data/<sample>/`、`03.clean_data/<dataset>/<sample>/`、`04.pandaseq/<dataset>/<sample>/` 和 `05.igblastn_out/<dataset>/<sample>/<CHAIN>.tsv`（兼容汇总 TCR.tsv/BCR.tsv 保留）。清单记录 `output_layout=sample-v1`，四个阶段共用路径解析；改名后使用确认的样本名。没有该字段的历史任务及 Submission 任务沿用原目录结构，重新 Match 保留该任务的布局版本，避免历史缓存与新路径混用。

服务器将实际选择保存为独立不可变输入清单（`state/input_manifests`），扫描快照绑定用户/流程/输入根，24 小时有效，每位用户保留最多 16 个。检查、创建、启动和续跑校验所选文件的大小与 mtime_ns；发生改变时需重新扫描，不自动纳入后来新增的文件。此指纹用于变更检测，不是内容哈希。任务详情“编辑资料 / 重新 Match”可恢复目录选择，重新扫描时新增配对默认不选；已批准的样本分配仍受现有确认限制。

首页“流程介绍与示例”按流程显示输入要求、阶段、主要结果、操作练习和检查点；可直接转入对应流程的新建任务窗口。处理细节默认折叠。下载入口为 `/static/demos/scigblast-teaching-v1.zip`，教程为 `/static/demos/README.txt`。

教学包完全合成：2 个样本、3 份 reMapping CSV（6 条序列）、2 对 FASTQ（各 20 对 reads）、4 份 Submission XLSX 及 Barcode CSV。用户需将文件解压到服务器允许的路径，并将 Submission 的 Note 占位路径改为实际输入路径。网页不自动上传或提交任务。FASTQ 用于练习到 Match 审核，不模拟 IR/10X 建库结构；reMapping 随机序列也可能无有效注释，不能用作完整分析或生物学准确性验证。

维护时运行 `rtk proxy python web/tools/build_teaching_demo.py` 重新生成 ZIP，在临时目录检查发布包的 FASTQ 格式、CSV 转换和四条流程的 Match。该脚本和合成教学包属于产品功能，保留用于用户学习。`web/Dockerfile.dockerignore` 明确允许打包上述两个静态文件；镜像更新仍需重建。
