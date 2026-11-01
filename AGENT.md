# Repository Guidelines

2026-11-02 非 root PID 注册修复：Base run_igblast_base.sh 不再在 SCRIPT_DIR 写 run_igblast_base.<pid>.pid，而是在解析运行时输出根后写 <OUTPUT_ROOT>/.pipeline_state/pids/run.<pid>.pid，EXIT 清理；包含批量父进程及子任务，原 stop.sh 的 */pids/*.pid 搜索兼容，不改共享两任务/四链调度与阶段处理。IR 03.split_barcode.py 的 PID_FILE 从源码目录迁移到 <OUTPUT_ROOT>/.pipeline_state/<DATASET_LABEL>/pids/split_barcode.pid，保留同一输出/数据集的运行检查和退出清理。Pig/10X/IR主启动器已使用输出内状态目录，reMapping未发现源码目录PID写入。隔离 Linux 容器以1012:1014、只读源码挂载验证：两个真实 Base Match-only 任务完成，失败启动无残留PID，现有stop.sh可停止对应输出而不影响另一个输出；IR分输出并行、同输出拒绝重复、遗留PID恢复与清理通过。没有对真实任务/结果重跑或修改权限；需新镜像部署后生效。

2026-11-02 目录选择弹窗：index.html 的 file-picker-message/search 改为统一 toolbar，搜索框显式边框/内边距/图标/焦点和清空按钮；路径、列表、分页、底部操作分别固定布局，仅目录列表滚动，短屏紧凑显示并保留列表最小高度。路径可换行且保留完整 title，长目录名保留 title；目录/文件使用 SVG 图标。清空复用原搜索 input 事件，保留分页游标、目录权限和过期请求防护。样式限定 .file-picker，不改后端浏览 API。Playwright 隔离预览复用真实弹窗模板/样式和目录事件代码，已检查1360桌面、320/390窄屏、844x390横屏、长路径、目录列表滚动、查询/清空、分页、读取失败恢复、选择及Escape；临时验证脚本不保留，预览在 outputs/directory_picker_20261102。代码修改不等于部署。

2026-11-02 引物配置改为上传后选列（覆盖此前 B/I、B、C:D/E:F 预设 UI）：共享 report_config.html/configPanel 用于新建任务、任务详情和独立后续分析。未上传前不展示列选择；成功解析 XLS/XLSX 后，按实际 column_count、当前工作表/表头的列字母与表头生成下拉项，空表头可带示例，不猜测字段归属、不默认选列。引物名称/序列使用可增删的成对下拉（1–12 对，支持 AA/AB 等列），请求仍为原 primer_columns 二维数组。选择后即时显示前五条及真实行号/合并单元格；更换文件/工作表/表头立即清空旧选择并忽略过期响应。未读完、读取失败、必需列或任一列对未完成时阻止提交，未上传任何表仍可按原规则只做数据量统计。统计/匹配和后端 API 未改。Playwright 已检查真实临时 XLSX 解析、无预选、多个列对、合并预览、表头越界、替换上传竞态、重置、日夜及390px布局；临时验证代码不纳入项目。

2026-11-02 归档修复：web/archive_results.py 的归档范围与新建任务输出权限分开，兼容 DEFAULT_OUTPUT_ROOT、ALLOWED_OUTPUT_ROOTS 及 ARCHIVE_LEGACY_ROOTS（默认两种历史 results 根，显式空值关闭）；已登记目录仍须通过独占、输入/应用目录、symlink 校验，bind mount 同源根按同一路径比较以防别名绕过共享检查。压缩包发布前失败恢复 SUCCEEDED 并记录 archive-failed；已发布 results.tar.gz/manifest/pending 的失败保留 ARCHIVING 供校验恢复。--recover-stale 持有归档独占锁，先在线备份 SQLite，只恢复未发布且无 pid/pgid 的遗留状态，不压缩/清理结果。备份位于 runtime/maintenance/archive-recovery-backups；部署保护不绕过归档锁或数据库状态。Compose/.env.example 已提供单独历史范围参数。

本次在线恢复：7167252126074e48、b7d0e8c7d6f74fb0、79bc5c38f35f4c92 从遗留 ARCHIVING 恢复 SUCCEEDED，结果文件未变更；维护脚本已原子更新，旧脚本和恢复前 SQLite 均有备份。三任务历史路径 dry-run 通过，部署探针确认主分析/后分析/树图/归档均空闲。没有重启容器或构建部署。临时合成验证覆盖真实压缩/逐字节校验、发布前失败恢复、已发布日志保留、bind mount 共享冲突、根目录/symlink 保护、SQLite 备份与遗留进程保护。

2026-11-01 配色v18 / Workbook v11：analysis/repertoire/palette.py统一语义色。数据阶段Raw蓝/Clean青绿/Merged紫/Retained暖橙，阶段矩阵每列仍按全批最大值缩放，用阶段独立渐变色键；匹配Unique蓝/Multiple橙/Unmatched灰；Isotype五主类各固定色，IgG亚类固定蓝色深浅；C构成沿用相同Isotype色，V红/J蓝/C紫保持原报告约定。标签按实际底色选择文字，填充内文本对比>=4.5:1。Excel分类表头/Tab/数据条分主题色，Web阶段卡和V/J/C卡加语义色边线并适配夜间。新增palette模块随Docker通配打包/check_bundle检查。统计VERSION仍v11，FIGURE_VERSION=v18、Workbook VERSION=v11变化触发重绘/重导出。模拟输出outputs/new_analysis_simulation_20261101_color_v18含181分析PNG与新Excel，旧v3保留供对比；各数值和范围与旧版逐项一致。

2026-11-01 Workbook v10：总览移除Samples/Sample-chain pairs/Primers总数卡，改为类别、完整样本名、链、引物组/对应方式四列样本表；去重显示全部样本/链，不截断表格，Heavy/Light/TCR由链确定，引物组只来自catalog实际group，不从名称猜测，无组名则明示未提供。write_workbook新增可选catalog参数，run传入本次catalog；下方两张原生图保留且动态下移，图表最多12组预览，组名/样本换行行高在append前设置。引物计数的统计范围改数据范围：all=统计输入，retained=筛选后保留；这是包含关系，retained由mapping/筛选状态判定，不等于引物唯一匹配，数量及原始CSV字段不变。VERSION=experiment-check-workbook-v10-sample-overview。

2026-11-01 Workbook v9：修复总览KPI数字裁切。openpyxl write_only行高必须在append该行之前设置，原helper在append后设置导致导出XML不保存行高。现总览1–6行预设24/16/10/22/38/30pt，KPI数字20pt、标签/数字左对齐垂直居中；保留原值和两张原生图表。VERSION=experiment-check-workbook-v9-overview-row-heights。

2026-11-01 Workbook v8：按用户要求移除Excel中的技术_参考观察明细Sheet，不只是隐藏；writer忽略reference_gene_observation及历史ambiguous_gene_observation表，不影响其余Sheet与统计。参考观察CSV/私有索引继续供后台查询，图表数据隐藏Sheet为原生图表依赖，不删除。VERSION=experiment-check-workbook-v8-no-reference-details。

2026-11-01 最新用户决定（覆盖此前候选上限2/10及歧义候选展示约定）：五条正式流程Base/Pig/IR/10X/reMapping的V/D/J/C全部显式-num_alignments_*=1，每区只报告1个比对。Excel不再输出歧义候选基因Sheet，Web移除该状态筛选、候选计数和入口；保留已匹配、未观察参考基因、Isotype及引物匹配统计（primer多重匹配不是基因候选，不得一并移除）。历史多候选注释仍由兼容解析器如实处理，不强取第一个当唯一，不改旧TSV；若要更新历史需显式重跑mapping。缓存随脚本/参数指纹变化，代码修改未部署。Workbook v7=experiment-check-workbook-v7-single-hit。

2026-11-01 Workbook v6：Isotype之后五张基因核查表默认可见列精简；样本已匹配=样本/基因/Reads/% Expression，引物已匹配加引物全名，未观察表仅身份列及必要提示，歧义表=样本/引物全名/基因/Candidate reads。链、基因区、核查结果/备注保留到表右侧并默认隐藏，可取消隐藏；技术明细继续隐藏。不同参考group仍以结果批次可见区分，comparison_current和零分母提示不能隐藏。基因格以V红/J蓝/C紫淡底保留区分，样本分块与数字数据条不变。只改变工作簿布局/简写表头，CSV、统计和主pipeline不变。VERSION=experiment-check-workbook-v6-compact-genes。

2026-11-01 IgBLAST候选报告上限：用户明确要求五条正式流程（Base/Pig/IR/10X/reMapping）V/D/J/C统一显式-num_alignments_*=2；不得恢复为1、默认3或此前建议10。AIRR仍一条query/read一行，同字段以逗号保存并列最佳候选，2是上限而非必须有两个，超过上限仍会截断。服务器IgBLAST1.22.0实测：num_alignments=1确实截断并列V/C候选，不能把单个call误认作唯一；改参数不会恢复已有TSV中丢失的候选。Base/Pig依实现哈希、reMapping依脚本元数据与db_args、IR/10X依阶段脚本哈希使续跑缓存失效；旧完成结果不自动改写，需显式重跑比对及后续统计。修改代码不代表已部署。

2026-11-01 Workbook v5：Isotype组成Sheet按样本/链/结果group及参考版本/主类-亚类隔离排序；同一Isotype的Expression与uCDR3（仅junction时uJunction）紧邻，Expression计数降序，同值按Isotype名稳定排序。排序仅作用于Excel，不改CSV、图例或统计值；缺Expression时不补0、不合并不同参考。歧义候选基因表只含unique_count=0且ambiguous_count>0的基因，等位基因先折叠到gene，多基因候选不计唯一归属；候选计数跨基因不能相加当reads数。VERSION=experiment-check-workbook-v5-isotype-rank。

2026-11-01 Workbook v4：所有可见含样本列的核查表按连续样本区块交替白/浅蓝底色，样本列加深并加粗；各样本首行整行中等粗细分隔线及26pt行高，不插空行、不合并，不改数值、列、筛选、冻结、数据条或核查下拉。技术表保持原样。VERSION=experiment-check-workbook-v4-sample-blocks。

2026-11-01 绘图 v17 / 工作簿 v3：移除单样本阶段数量PNG及Web入口，仅保留全样本Read counts across samples汇总矩阵。公开图表/UI使用Raw reads、Clean reads、Merged reads、IgBLAST input reads、Retained reads和% Expression / % uCDR3；原始R1+R2读端与合并后每配对一条read的换算仍明确区分，不改变计数和筛选。最终筛选后计数不称总Mapped reads。标题、图例与阶段矩阵色标统一居中。Isotype主类合并IgG亚类，uCDR3在合并后重新去重，不能简单相加。report.xlsx增加总览及原生Excel图表、交替行底色、百分比数据条、V/J/C色标、核查下拉框；图表数据和技术明细隐藏，主核查表仍精简。原生总览图预览最多12样本/组，完整数据不截断。新版模拟交付outputs/new_analysis_simulation_20261101_v3含181张分析PNG及新版工作簿，旧版本保留。

2026-11-01 最新约定 v16（覆盖下文 v13/v15）：样本/逐引物 V/J/C 分布只画 unique_count>0 的已有明确注释基因，分页查询、PNG、对应主 CSV 同步过滤；分母保留原统计，不按页内重新归一化。零观察参考基因只进独立列表及 unobserved_reference_genes.csv（样本、引物各一份），只有歧义候选单独标识，不当作零命中。Web 新增参考观察清单，可按样本/引物/基因区/状态分页核查，不画零柱。未知历史参考仍不能推造零值。V/J 配色依原 report 为红 #C24D51、蓝 #4C72B0，C 为紫 #7C6C96。FIGURE_VERSION=v16，统计schema仍v11。

实验核查：analysis/repertoire/workbook.py 复用平台 openpyxl 流式输出 report.xlsx；一个工作簿含中文目录与各分析Sheet、筛选、冻结表头，基因表优先样本/链/引物/基因并留核查/备注空列，技术ID置后隐藏。已匹配/未观察/只有候选分开。输入根内有06或08.preprocessing/Datapoint.csv时另纳入原流程样本指标Sheet，记录来源及原选择范围，不冒充本次AIRR记录口径。CSV/私有索引保留，已有报告不覆盖，重生成取得新版。

IgBLAST base新增06.preprocessing，适用于其所有物种（包含Pig）。Igblast_base/pipeline/06.preprocessing.py经pipeline_airr_preprocessing.py读取chain_summary.csv中的本次单链结果，按样本合并配对、按BCR/TCR复用IR_split/pipeline/08.preprocessing.py和现有models；不重写过滤/Expression/Unique CDR3/多样性/Isotype/CSR/SHM公式。IR入口默认不变，仅chains布局适配。Base无UMI代表序列，清单标alignment_rows；兼容列umi_counts是比对记录支持数，不称真实分子UMI/RNA。05输出不修改；06输出Datapoint.csv、processing_manifest.csv和junction_peps。默认SHM4进程、32GB阶段预算，SCIGBLAST_BASE_PREPROCESSING_*可调。来源/模型/设置变化使06失效；仅来源变化的样本重算，产物缺失重建，空链记录EMPTY。旧已完成任务保留五阶段，新建或显式续跑Base启用airr_preprocessing_version=1，以06标记判定完成。Docker白名单/check_bundle已加入适配模块。

本次模拟交付：outputs/new_analysis_simulation_20261101含8模拟样本、9样本链的189张分析PNG、report.xlsx（12个Sheet）及图形目录；同层“新增分析_完整模拟结果.zip”统一下载。数据和参考清单均模拟，不混入真实患者内容，不伪造原流程Datapoint。原报告图表清单见outputs/original_report_inventory_20261101/原报告图表清单.md。

2026-11-01 绘图 v15：样本和每引物 V/J/C 柱状图分别使用蓝 #3C718F、绿 #477C78、紫 #7C6C96，Web 同步并适配夜间主题；零观察灰色空心圆、只有歧义候选赭色菱形优先于基因区配色。新增 kind=stage_overview 的全样本数据量 PNG（04_data_changes/figures/all_samples__stages.png，超过12样本分页）；采用样本×阶段数量矩阵、格内数量与变化，比重复三套柱图紧凑。颜色按各列最大值统一缩放（跨页一致），不是数据保留率。Fastp/Header 显示相对前阶段变化，合并率用输入配对作分母；mapping 只标结果行/输入序列数量比，不能当 read 保留率。Header 与 Fastp 全样本已知数量均相同才合并展示；未选链留空、缺失用横线、真实零保留0，跨链输入不累加。Web 数据变化默认全部样本，可切单样本，原竖向柱图与统计口径保留。FIGURE_VERSION=v15，统计 VERSION 仍v11，重绘缓存同样更新绘图格式标记。

2026-11-01 部署检查：web/deployment.py 用只读 SQLite 与 /health 联合检查主分析/队列/ARCHIVING/DELETING、INTERRUPTED 遗留 pgid、derived_runs/report_runs 活动状态或遗留 PID。deploy.sh 将本地新探针通过 stdin 传给旧容器，构建前后及切换前检查；/health 新增 deployment_ready/deployment_blockers；不自动取消或修改任务。新探针加入 Docker 白名单和 check_bundle。服务器曾提前修改的9份受管配置已备份到 .igblast-migration/deploy-preparation-a6156673 并恢复，git status 已为空；.env/状态/结果/IgBLAST 副本保留，未来推送后再用 deploy.sh 更新。检查时3个 ARCHIVING 任务仍阻止部署，未重启服务。

2026-11-01 绘图 v13：V/J/C 已观察基因使用蓝色，零观察使用灰色名称/数值与空心圆，只有歧义候选使用赭色名称/数值与菱形。Web 同步使用该配色，夜间主题提高明度；不为零值画非零柱。该样式只影响基因状态，不改变 isotype/C 构成的类别配色及任何计数。

2026-11-01 绘图 v12：用户要求精简图中文字说明。PNG 不再包含分母/方法/参考库描述页脚，“各链独立统计”等解释也移到表和说明；保留坐标、图例、数值与必要实验条件标识。FIGURE_VERSION 独立于统计 VERSION，参与自动/手动分析及引物重绘去重，避免复用旧样式；统计 schema 仍 v11，可复用现有统计索引。更新示例位于 outputs/report_workspace_v12_clean_labels。

2026-11-01 后续分析 v11（优先于下文 C-only/v10 约定）：用户已明确扩展样本及每引物统计为 V/J/C。`analysis/repertoire/usage.py` 将最终 AIRR、引物追溯、当次参考快照汇总到私有 details.sqlite，gene_usage 为有索引的物化表；大表通过 CSV/Excel 流式导出及 `/api/reports/{id}/usage` 有界分页读取，overview.json 不携带逐基因大表。历史缺参考时不能推造零值；仅用户显式选择 comparison_species 后读取服务器 SCIGBLAST_REFERENCE_ROOT，标记 comparison_current（当前参考比较），不可冒充历史比对库。各参考版本分 group_id，不按显示样本名静默合并。仅歧义候选与零观察分开。

`isotype.py` 只将可明确识别的 IGH C 基因归类；丰度每条最终有效 AIRR 记录权重 1，不声称 UMI/RNA 表达。Unique CDR3 按文件优先 cdr3_aa；仅有 junction_aa 时单独口径，不逐行混用；标准 AA 精确去重，主类合并后重新去重，组成分母为各类型内去重数之和。共享 AA 跨 isotype 可重复计入不同类型。trace 新版为 mapping-trace-v2，保留两个 AA 字段，sources 同时兼容 v1/v2；新增 trace 实现哈希进入 Base/Pig 缓存。已有结果直接补分析读取最终 AIRR，不要求重跑 mapping。

Web 在 reports-workspace.js 中复用四视图（总体概览/样本分析/引物分析/数据下载），局部筛选、零观察开关、参考版本说明；PNG 库每页最多 7 张。旧报告仍走 reports.js 原组件。新分类为 01_overview、02_sample_genes、03_primer_genes、04_data_changes、05_details；下载兼容旧 exports 路径。`/{id}/figures` 对 v11 复用已验证 summary/details 索引排队重绘，不再重复匹配/扫描。仅 PNG。样本阶段数量计算保持原口径，缺失计数不绘制 NaN 柱。

2026-11-01 Web 参考目录迁移：用户要求所有流程统一采用 `/colddata/zqy/igblast` 最新版，已复制到服务器项目 `/colddata/SCigblast/platform/SCigblastWeb/igblast`，2988 文件 SHA256 一致。Compose 的 `SCIGBLAST_HOST_IGBLAST_ROOT` 默认 `../igblast`，规范容器路径 `/opt/scigblast/igblast`，`SCIGBLAST_REFERENCE_ROOT` 供五条流程读取；两个旧容器路径也指向相同只读副本以兼容现有镜像。原目录不删除。当前因 3 个 ARCHIVING 任务未重建在线容器，配置尚待应用；不要误报已切换。详情及生效命令见 `reference/IGBLAST_DEPLOYMENT.md`。运行程序仍是镜像 Conda 中的 igblastn。

## 项目定位与阅读入口

SCigblast 是免疫组库分析平台：Python/Bash 执行分析，`web/` 提供 FastAPI、Jinja2、原生 JavaScript 任务界面，SQLite 保存账户、任务和操作记录。先读 `web/pipeline_registry.json`、`web/app.py`、目标流程的 runner 和配置，再参考 `web/README.md`。`pipeline/README.md` 有过时路径与流程描述，发生冲突时以当前注册表和可执行代码为准。

## 分析架构

- `IR_split/pipeline/run_ir_pipeline.sh`：Match → fastp → Barcode/UMI 拆分 → header 清理 → PANDAseq → 代表序列 → IgBLAST → preprocessing。当前统一自动识别 raw/presplit；结果模型位于该分支 `models/`。
- `10X_split/pipeline/run_10x_pipeline.sh`：Match → fastp → R1/R2 预筛选 → header 清理 → PANDAseq → Cell Barcode/UMI 拆分与代表序列 → IgBLAST → 链聚类。不要混用 IR 与 10X 的拆分规则。
- `Igblast_base/pipeline/run_igblast_base.sh`、`PigIgblast/pipeline/run_pig_pipeline.sh`：Match → fastp → header 清理 → PANDAseq → IgBLAST 与链筛选，共五阶段；Pig 使用专属数据库逻辑。每个样本/链的比对一结束，就在同一 worker 内筛选并发布该链，不等待所有链结束。
- 每个分支通过 `00.pipeline_config.env` 与 `tools/pipeline_config.py` 管理配置；Web 通过环境变量启动固定 Bash runner。容器工具路径由 `SCIGBLAST_RUNTIME_BIN_DIR` 覆盖，数据库需单独挂载。
- `IR_split_merged/`、根目录 `pipeline/`、`preprocess/` 当前没有注册为 Web 流程；不要根据目录名推断它们是生产入口。

## Web 与输出契约

`web/app.py` 管理路径校验、队列、进程组、状态、审核和结果接口；`web/auth.py` 管理账户权限；`web/submission.py` 创建不可变 XLSX 工作副本。现有四条流程首次执行 Match 后进入 `WAITING_REVIEW`，全部记录完成标注且存在 OK 记录才能继续。

新任务输出由服务器按用户、流程和北京时间分配。流程产物通常位于 `<output>/<stage>/<dataset>/`，日志为 `logs/<dataset>/pipeline.log`，状态为 `.pipeline_state/<dataset>/`；完成标记名称须与注册表和 `pipeline_done()` 对齐。仅运行单 Web 实例、单 Uvicorn worker；所有用户共用两个 pipeline 运行名额，第三条起按提交顺序排队；五条正式 runner 使用同一共享 `flock`，主分析使用共享门锁加两个独占槽位（锁路径后缀 .slot-1/.slot-2），Treemap 继续独占门锁并与所有主分析互斥；两种 FD 均由子进程继承并在退出时释放。不得删除正在使用的锁文件。Web 的 MAX_ACTIVE_JOBS 与 runner 槽位数同时固定为 2，不通过旧 SCIGBLAST_MAX_ACTIVE_JOBS 环境变量调整。IgBLAST 在全部五条流程中最多并行 4 个链任务（不是每个进程 4 个线程）；配置默认 SCIGBLAST_IGBLAST_MAX_PARALLEL_JOBS=4，入口/worker 对更高值封顶，原 CPU/内存预算仍可降低实际并发。阶段内部并行由各流程配置控制。保留已有任务归属、路径范围、停止进程组及归档行为。

Web 使用 `execution_json` 关联本次 attempt 的启动日志、运行日志边界和进程身份。续跑不能展示旧执行进度；恢复的进程组需核验后才能停止，不得仅凭旧 PID 发送信号。`web/preflight.py` 按匹配/分析阶段检查真实运行环境；`web/table_preview.py` 为普通结果提供有界游标读取，Match 仍保持完整 revision 审核统计。普通结果的 `total` 在扫描完成前可为 null，调用者使用 `has_more`、`next_cursor`、`scan_complete`，不能把尚未扫描完当成零结果。

Base/Pig 使用根目录 `pipeline_igblast_cache.py` 生成一次 IgBLAST 依赖快照，供 stage 与 task 缓存共同使用。涉及工具、参考库、aux、过滤及参数变化时使相应缓存失效；若人工替换参考文件同时保留大小/修改时间，须更新各自配置的 `SCIGBLAST_IGBLAST_REFERENCE_VERSION`。每次重新进入 stage5 时清理当前清单对应的旧 TCR.tsv/BCR.tsv，逐链结果与总摘要原子发布，不再生成这两类聚合副本；`output_path` 必须指向实际单链 TSV。

结果权限：`pipeline_locus_split.atomic_text()` 使用 UUID + `os.open(O_CREAT|O_EXCL, 0666)` 创建同目录临时文件，让内核应用 umask 和默认 ACL，再原子发布；Pig DONE 文本也复用该函数。不要沿用 mkstemp 的 0600 去发布用户结果，它会把继承 ACL 的 mask 变为无权限；也不应在多线程 worker 中临时修改进程 umask。已在 Linux 隔离容器验证 0022/0002/0077 下分别跟随普通文件的 0644/0664/0600，并验证带命名用户 ACL 时 UID 1012 可读写。文件属主取决于容器运行 UID；Dockerfile 仅在安装/打包阶段用 root，最后 USER $MAMBA_USER。Compose 需要 SCIGBLAST_UID/GID，deploy.sh 自动把调用者非零 id -u / id -g 填入空缺的 web/.env 字段；SCIGBLAST_DATA_GID 可追加共享数据组。HOME/cache 放在状态目录 home 中。更换已有 root 运行账号前需在任务空闲时备份状态，迁移本平台 runtime/results 中 root 拥有的对象，保留其他属主、权限与 ACL；部署权限探测失败时不得替换旧服务。

2026-10-27 将独立 06 链筛选并入 Base/Pig 的 05：`pipeline_locus_split.filter_mapping_airr()` 单次流式读取各链 raw AIRR，同时检查 V 命中、productive 与 `locus == 当前任务配置链`，保留全部 AIRR 列；未知及其他 locus 排除。逐链结果直接写到 05 的既有样本目录 `<CHAIN>.tsv`，raw/日志/逐链 DONE 仍位于 `.tasks`；筛选成功并原子发布后才写 DONE。所有链结束后仅发布 igblast_summary.tsv 与 chain_summary.csv 统计，不再生成 TCR.tsv/BCR.tsv 聚合副本，也不再批量筛选或创建 06。摘要增加 locus_input_rows、excluded_locus_rows、unknown_locus_rows、locus_filter_version；排除统计仅针对已通过 V/productive 的记录，unknown 为排除的子集。缓存记录新的筛选版本和共享实现哈希，旧筛选缓存不得直接复用。Web 新任务记录 inline_locus_filter_version=1，阶段完成标记也须含 locus_filter_version=1；旧六阶段任务保留历史显示，明确续跑时转为新五阶段。Treemap 新任务直接读取 05 的已验证单链结果，明确用 locus 字段；新 05 摘要存在时目录扫描也忽略残留旧 06。历史六阶段结果与手工离线拆分 CLI 继续兼容，不自动改写旧结果。

Pig 的 05 IgBLAST 对存在的链专属 C 库传 `-c_region_db .../pig_gl_<CHAIN>_C -num_alignments_C 1`，没有 C 库时保留 V/D/J 分析；不能把 TCR 强制改成 Ig。依赖快照对 Base/Pig 均记录 V/D/J/C 的 sidecar，C 库新增或变化使对应链缓存失效。remote_nanhua 上 IgBLAST 1.22.0 已用 TRA/TRB 各 5 条真实序列对照验证：不传 C 库时 AIRR 无 c_call 列，加参数后均有对应 TRAC/TRBC 注释。2026-10-27 补库前 pig_chain 仅 TRA/TRB 有 C 库；当天已按下述记录新增 BCR C 库。缺少 C 参考的链仍不能推造 c_call。05 内链筛选及兼容旧 06 拆分须保留新增的 C 字段，旧结果需要重新执行 mapping 才能补齐，不能只新增空列。

2026-10-27 补库前通过 remote_nanhua 的 blastdbcmd 导出核验实际索引内容（不能只看 nhr/nin/nsq 是否存在）：pig_chain 的 TRA 为 V=103/J=98/C=1（C 270 nt）；TRB 为 V=30/D=3/J=18/C=3（C 均 387 nt）；IGH 为 V=17/D=5/J=6，IGK 为 V=25/J=9，IGL 为 V=23/J=4，当时后三者无 C。TRG 只有 11 条真实 V，缺 J/C。TRD 的 V/D/J 虽有索引，实际均为单条长度 1 的 N（placeholder_TRD_V/D/J），无真实参考及 C，不能作为已支持链。TRA/IGK/IGL/TRG 的 D 也为 1-N 兼容占位，不计作真实 D。真实库序列与同目录 FASTA 内容一致；上游 database_251117/pig 合并源也缺 TRD、TRG J/C 和 pig_IG_C，不是分库后丢失。数量为参考序列/等位记录数，V/J/C 组件具备不等于全长基因或全部等位基因覆盖，需来源注释另行核验。

猪 BCR C 区官方来源（2026-10-27）：IMGT 猪 IGHC 表 https://imgt.org/IMGTrepertoire/index.php?group=IGHC&repertoire=genetable&section=LocusGenes&species=Pig 列有 IGHM/IGHD/IGHA/IGHE/IGHG 等，参考登录号包括 AB699686/AB699687/AB699688；其中 IGHD 恒定区基因与 D 片段不能混淆。IGKC 表 https://imgt.org/IMGTrepertoire/LocusGenes/genetable/autotable.php?group=IGKC&species=Sus+scrofa 的 IGKC*01 对应 FP312898:132117-132442，IGKC*02 对应 CU694848:complement(28072-28397)。IGLC 表 https://www.imgt.org/IMGTrepertoire/index.php?group=IGLC&repertoire=genetable&section=LocusGenes&species=Pig 列 IGLC2*01（CU467669）和 IGLC3*01（CU467599），该表注明 IGLC1 尚未提供，不能据此宣称全覆盖。IMGT V-QUEST 猪 IG 下载目录仅 V/D/J，无 C；本次已通过原站 urllib 成功取得 query=14.1+IGHC/IGKC/IGLC&species=Sus+scrofa 的人工剪接外显子核苷酸，以及 query=7.5+IGHC 的单外显子参考。不能将整段多基因 BAC/蛋白展示页直接作为单条 C 核酸参考。

remote_nanhua 已安装 BCR C 库版本 `imgt-sus-scrofa-C-20261027`：`/colddata/zqy/igblast/database_251117/pig_chain/{IGH,IGK,IGL}/pig_gl_<CHAIN>_C`，分别 29、2、2 条；FASTA 为各目录 IGHC.fasta、IGKC.fasta、IGLC.fasta。每个功能性等位基因一个规范 ID，有官方完整剪接记录时优先分泌型；IGH 22 条来自该剪接导出，另 7 条仅使用官方完整单外显子作部分 C 区参考，不能称其全长：IGHD*04(CH1)、IGHG4*01(CH1)、IGHG4*02(CH2)、IGHG5-1*03(CH1)、IGHG5-2*01/02(CH1)、IGHG6-2*03(CH1)。每链 `pig_gl_<CHAIN>_C.provenance.json` 保存原始头、来源登录号、外显子范围、partial_constant_region 和序列 SHA256；保留 IMGT 明示的补齐 N，不推造未测序区。原始导出、准备/测试/安装脚本及报告位于 `pig_chain/.reference_sources/bcr_C_20261027_a8b8497e/`。发布持有平台共享资源门锁的独占模式，原有 244 个文件 SHA256 保持不变。BLAST 序列回读与三条合成 V(D)J-C 阳性对照通过，Web 容器也已读出全部 33 条参考；这不是全量真实样本验证。数据库安装不等于服务代码已部署；Pig 参数修复需随镜像部署后才能自动使用，旧结果须重新 mapping。

2026-10-27 猪库 V/J 标识规范化版本 `pig-vj-canonical-20261027_115846`：remote_nanhua 当前仍使用 `/colddata/zqy/igblast/database_251117/pig_chain`，344 条真实 V/J 参考仅移除 ID 开头的 `2026pig`（如 `2026pigTRAV01*01` → `TRAV01*01`），13 套现存 V/J 索引重建（包括不改名的 TRD 占位索引）；FASTA 核苷酸/大小写、D/C 及原结果未改。TRG 缺 J、TRD 无真实参考的问题仍存在。完整改名前备份为 `/colddata/zqy/igblast/backups/pig_chain_before_vj_rename_20261027_115658/`，含 pig_chain、internal_data/pig、optional_file/pig.aux 及 316 文件 SHA256.json。`pig.aux` 新增 135 条规范 J ID 别名并保留旧条目，内部 V 库和 FWR/CDR 注释不变；10 条真实 TRA/TRB 序列及 5 条合成 TCR/BCR 对照验证改名前后全部 AIRR 字段仅 V/J 名称不同，旧库配新 aux 的结果也一致。切换须持有同一平台门锁的独占锁；不能只改 FASTA 头而不重建索引/同步 J aux。映射表、构建脚本和验证摘要位于新库 `.reference_sources/pig-vj-canonical-20261027_115846/`，详细独立测试位于同级 `.pig-vj-normalize-7gvnh2j3/`。该次 mkdb_251117.sh 只在 V/J 建库前规范化前缀，D/C 当时未应用替换；后续 D 更新见下文。新 mapping 自动使用新 ID，历史结果需重新 mapping 才会更新命名。

上述 V/J 改名已在 Web 容器 `scigblast-web` 的 IgBLAST 1.22.0 再次验收：13 套 V/J 索引序列回读与备份对应，15 条对照的完整 AIRR 与宿主机一致；摘要见该版本来源目录 `container_validation.json`。容器确认能读取新库不等于 C 区修复代码已经部署。

2026-10-27 后续 D 标识规范化版本 `pig-d-canonical-20261027_192734`：上述 V/J 迁移当时未处理 D，因此历史 d_call 仍可能含 2026pig。remote_nanhua 现已将 IGH 的 5 条、TRB 的 3 条真实 D 参考 ID 去掉开头 2026pig，重建两套 D 索引；保留 FASTA 核苷酸及原始大小写，不修改 V/J/C、占位库、internal_data/pig、pig.aux 或历史结果。完整备份为 `/colddata/zqy/igblast/backups/pig_chain_before_d_rename_20261027_192734/`，含猪链库、内部注释、aux 及 SHA256.json。持有平台门锁独占锁后切换，主机 10 条真实 IGH/TRB 序列对照验证全部 AIRR 字段仅 d_call 名称变化，容器完整 AIRR 与主机一致；映射、迁移与容器验证记录在现用库 `.reference_sources/pig-d-canonical-20261027_192734/`。该次 mkdb_251117.sh 规范化 V/D/J 的前缀，C 当时未改；后续 TCR C 更新见下文。现用库路径未变，新 mapping 直接使用新 D ID，索引文件版本变化使相关缓存失效；已有 TSV 不自动改名，需重新 mapping 更新。

2026-10-27 TCR C 标识规范化版本 `pig-tcr-c-canonical-20261027_233313`：TRA 的 1 条及 TRB 的 3 条 C 参考 ID 去掉开头 2026pig，变为 TRAC*01、TRBC1*01、TRBC2*01、TRBC3*01，并重建两套 C 索引。保留核苷酸及 FASTA 大小写、V/D/J、BCR C、占位库、internal_data/pig 和 pig.aux；pig.aux 的 135 条旧 J 别名保留兼容性。完整备份为 `/colddata/zqy/igblast/backups/pig_chain_before_tcr_c_rename_20261027_233313/`，含猪链库、内部注释、aux 和 SHA256.json。独占平台门锁切换；TRA/TRB 各 5 条真实序列和 1 条 C 阳性合成对照的主机 AIRR 逐字段对比仅 c_call 名称变化，Web 容器结果与主机一致。映射、迁移和验证记录位于现用库 `.reference_sources/pig-tcr-c-canonical-20261027_233313/`。mkdb_251117.sh 现规范化 V/D/J/C 前缀；库挂载直接生效，C 索引更新使相关缓存失效；历史 TSV 保留旧名称，重新 mapping 才会更新。

根目录 `pipeline_parallel.py` 为 Base/Pig/reMapping 限制在途任务为现有 worker 数的两倍；Base/Pig 的多链 FASTA 计数仅在同次运行内共享。性能修改必须保留阶段顺序、分析参数、过滤规则、文件命名、列及最终记录顺序；不要依据合成调度测试提高生产并发。

reMapping 结果页按样本和链展示最终 UMI 文件；条数/保留比例来自现有摘要，不等于 copy 总和。`web/artifact_download.py` 流式打包所选 03 文件，保持原文件内容和相对目录，不写入分析输出目录；下载准备 POST 返回 120 秒有效、当前用户/任务绑定的 URL。结果浏览状态按文件保留，文件版本变化重置游标；共享确认与 busy 反馈位于 `web/static/ui.js`。

详情页用 `sessionStorage` 按当前登录用户 ID 和任务 ID 保存浏览偏好。刷新后恢复筛选、样本列表页码、当前文件及表格搜索/字段选择，表格重新读取第一页；不持久化结果行、游标、下载选择或令牌。执行 attempt 变化时清除旧浏览状态。当前文件不在列表页时，通过有界结果查询重新核验，不根据浏览器记录判断下载权限。

新建任务窗口采用顶部流程选择、单正文滚动区与固定操作栏。reMapping 的 `mappingRows/mappingSelected` 保存完整清单与选择，搜索和每页 20 项仅影响显示，不能从当前页 DOM 生成完整样本请求；目录模式继续以不可变 row.id 保存 selected/drafts。纯搜索、分页不使配置检查失效；输入、选择和 Chain 修改则失效。提交先读取请求快照，再锁定控件，`taskSubmitting` 防止重复请求；失败恢复输入和选择。保持重新 Match 的流程、来源和输入目录锁定。

页面主题由 `appearance_head.html` 在首屏加载 `appearance.js/appearance.css`；右上角切换日间/夜间，只在当前浏览器 localStorage 的 `scigblast-theme` 保存偏好，未设置时跟随系统。五个页面共用，不能改变树图图片和导出文件配色。单选下拉优先使用可定制 picker 样式，旧浏览器保留有样式的原生控件与键盘/表单行为。任务列表显示可逐项移除的筛选标签；“重置筛选”和状态栏“全部”均清除流程、操作者、搜索与状态并回到第一页，其他状态 Tab 保留右侧条件。轮询不能重建未变化的筛选标签，避免夺走焦点。

链选择组件在 `web/static/ui.js` 的 `chainPicker/bindChainPicker`，统一按 IG 与 TCR 分组，保留原 checkbox 值与多链选择。reMapping 显示扫描文件数，未发现的链禁用，“全部可用链”和本组快捷选择不能勾选禁用项；默认仍仅选扫描到的 IGH。目录批量勾选只是模板，点击应用才覆盖全部已选样本；`syncBatch()` 展示已使用该组合的样本数。逐样本设置继续独立保存，不根据批量区当前勾选生成请求。

详情页仅保留一处状态与下一步说明，阶段明细按需展开，Tab 吸顶且 reMapping 隐藏 Match。克隆树图为输出文件旁的独立 Tab，支持 `#treemap` 直达、浏览偏好恢复和键盘切换；只在该 Tab 可见时加载/轮询树图，不随输出文件刷新。`region()` 按内容变化更新结构，时间、阶段内进度等局部更新；轮询不能重建未变化的操作按钮、展开区或夺取焦点。吸顶导航高度通过 ResizeObserver 写入详情页的 `--detail-tabs-height`，结果 sticky 预览与跳转偏移沿用该变量。保留原有 Tab 初次落点与键盘操作，不用任务状态轮询强行切换 Tab。任务列表操作列的 `<td>` 保留 table-cell 布局，按钮 flex 放在内部 `.row-actions` 容器，表头和按钮左对齐，不能直接把表格单元格设为 flex。

任务详情的“本次运行时长”由 `execution_runtime()` 根据本次 started_at/ended_at 计算，API 返回 runtime_seconds/runtime_running；浏览器用 performance.now() 按秒更新，沿用现有接口轮询。续跑、重试或 Match 后分析会开始新的 attempt，不累计跨次时长；排队及等待审核不计入，排队期间停止也不把旧启动时间算入耗时。缺失或异常历史时间显示未知，不推算累计时长。

邮件提醒模块为 `web/notifications.py`。用户仅通过 `/api/notifications/preferences` 设置自己的收件邮箱（留空关闭）；管理员 `/api/admin/mail-settings` 配置 SMTP、发件地址和平台链接，默认 QQ 的 smtp.qq.com /465 /SSL，服务初始关闭。自动通知为四类：开始执行 STARTED、完成 SUCCEEDED、失败 FAILED、中断异常 INTERRUPTED/COMPLETED_WITHOUT_MARKER；新版本开始提醒只在本次 launcher 日志确认获得共享运行槽位和首个阶段资源锁后触发，不以 QUEUED/RUNNING 状态变化或启动进程作为依据。监控按 attempt/PID/process_token 核验并原子保存 execution_json.resource_acquired_at 与去重事件；Match、分析、续跑各 attempt 最多一次，进度不重发。发送前若已结束/停止/重新执行则跳过过时的开始事件。等待审核、主动停止和排队不通知，旧队列中已取消支持的事件也跳过，管理员主动测试保留。密码存于服务端 SQLite，不回显、不写入任务参数或 runner 环境；空密码保留原值，清除需显式 clear_password。`update_job()` 与启动中断处理在状态事务内向 mail_outbox 入队，按 job/attempt/event 去重；后台单线程在事务外连接 SMTP，失败最多 3 次，延迟 60/300 秒重试，不改变分析结果。收件人由 owner_id 决定，不由当前操作者决定。发送前核验用户启用、当前邮箱和任务 attempt/status；已过时的通知、已删任务或已关闭收件设置跳过。SMTP 服务关闭时暂停队列；重启恢复 sending 项。测试必须 mock SMTP，不能使用真实邮箱或授权码。镜像同步打包 notifications.py。

`web/mapping_results.py` 按三个摘要的文件版本和执行 attempt 构建有限 LRU 索引。新任务在内部 `options_json.mapping_inputs` 保存创建时实际选中的源文件，只用于结果完整性展示，不传给 runner 或详情 API；历史任务仅合并已有摘要并标记清单不完整。`/mapping-results` 分页返回处理状态、原因和计数，计数依据摘要，文件可用性只对当前页实时核验；单文件预览与下载直接查索引后核验目标。不要根据样本与链的笛卡尔积猜测缺失输入，也不要把文件缺失计作生物学零结果。

IR `08.preprocessing.py` 的 `read_sample()` 将 Future 和读取临时变量限制在函数内，使上一样本大表在下一次内存检查前可释放；主流程仍按原顺序计算、写出并保存 checkpoint。不要把该读取循环移回长生命周期的 `main()` 局部作用域。

IR/10X stage7 后处理仅在本次进程内复用同一 FASTA 的计数，按真实路径及文件版本识别多链软链接。重新执行会重新计数；缺失/空文件、旧批次目录和 AIRR 行数回退保持原行为，不使用可能过期的阶段汇总替代输入计数。

## Treemap 派生结果

合图仅在同一样本至少有两条不同链的可用单链图片时生成；只选一条链，或其他链为空/失败后仅剩一张图时跳过合图，但仍保存该样本合图阶段完成标记，继续发布 Clone Top 和统计文件。规则同时适用于 v1/v2 运行，计数、单链图、合图比例和文件命名保持原约定。手动生成去重键包含 `COMPOSITION_VERSION`，便于重新生成时使用新规则；自动意图键不变，不自动重跑或删除已完成历史图片。

用户提供的 `treemap_g.py` QR 核心移植到 `analysis/treemap/`，仅按 `productive == T` 且 CDR3 非空的最终记录数聚合完整 V/J/CDR3，不读取 copy/UMI 权重。CDR3 优先 junction_aa，否则 cdr3_aa；保留原始字符串、配色、排序、3000×3000/300 DPI 和固定比例合图。合图链区面积不代表链间丰度。`sources.py` 用各流程最终摘要与真实 output_path 识别来源，不重复统计聚合文件；10X 合并文件以 locus 拆链，IR 使用 preprocessing 的 junction_peps。

新 Treemap 运行聚合时核对行内链字段。普通来源优先 `chain`，没有此列才用 `locus`，不对空/未知 chain 猜链；两个列都没有时才沿用已知单链身份。Base/Pig 的 06 拆分来源显式指定 `chain_column=locus`，不能被附带的 chain 列覆盖。`CHAIN_PARTITION_VERSION` 写入新手动/自动运行元信息及手动去重键；请求显式传 `partition_by_chain`，无版本的历史 Web 运行在续跑/重试时保留旧策略，不能混用新旧统计或自动重算已完成图。工作台新建生成记录可使用新策略。

历史 Base/Pig 曾将 `06.locus_split` 作为独立阶段；现行主流程已将逐链 locus 筛选合入 05，见上方“2026-10-27 将独立 06 链筛选并入”记录。历史六阶段任务及独立 `pipeline_locus_split.py` 离线拆分仍须兼容，不能重新把 06 加入新任务或自动改写历史结果。

新增 `clone_top.csv`（kind=clone_top）：按样本链内 record_count 降序排名，V/J/CDR3 文本打破并列，保留全部克隆。frequency_percent/cumulative_frequency_percent 以本样本本链有效记录数为分母，不能改成跨链或 UMI 口径。`clone_table.py` 写 UTF-8 BOM CSV，复用绘图聚合结果，先原子发布每链 `.clone_tables/<entry_id>.csv`，由 item.clone_table 保存内部版本；该字段不能回传详情 API。合图完成后独立 clone_merge 子进程流式汇总，不在 Web 的 PROCESS_LOCK 内读取大表。CSV 总表进入 manifest、单文件下载与全量 ZIP，样本 ZIP 仍仅含该样本图片；空数据仅表头，失败链排除且原因保留在摘要。重试重新合并总表，成功中间表正常复用，缺失/变更时仅重处理对应链。新手动运行的去重键包含 clone_table_version；自动意图键及 PNG 版本保持不变，重启不能为已完成旧任务自动补算；旧记录无该格式标识时保持原行为，可手动重新生成以获得表格。Docker 打包检查须包含 clone_table.py。

新建任务 `generate_treemap` 默认 false，只写 options_json，不传给主 runner；成功状态事务只保存绘图意图。`web/postanalysis.py` 用单调度线程启动有界绘图批次；默认最多 2 个单链 worker，按 cgroup/CPU/可用内存限制，合图仍逐样本执行。`SCIGBLAST_TREEMAP_MAX_WORKERS=1` 恢复串行；worker 总预算为 `SCIGBLAST_TREEMAP_MEMORY_BUDGET_MB`，单进程预算为 `SCIGBLAST_TREEMAP_MAX_RSS_MB`。每批一个轻量监督进程，数据库 PID/token 指向监督进程；所有 worker 同组且继承共享 flock FD，禁止单独释放锁。取消、超总预算、关闭或监督进程异常时核验并停止整个组，确认全部退出后才清除 PID；身份不明则保留 interrupted 记录。进程间只传文件路径和少量 JSON，不复制 DataFrame/图片。主分析排队后不启动下一批；单链失败不影响其他 worker，重试保留成功图。主分析状态、DONE、耗时、输出和邮件不受绘图失败影响。状态和进度独立，重复提交去重；修改来源/attempt、取消、续跑、删除与归档均核验生命周期，旧 PID 必须通过 process_identity 验证。归档认领须在 SQLite 事务中检查无活跃 derived_runs，不能只靠 PROCESS_LOCK。

独立入口 `/treemap` 可选有权限的成功任务或受控服务器目录，`/api/treemap/scan` 返回绑定用户和有效期的快照，提交 selected_ids；跨页/搜索只影响显示。纯目录表格必须有明确样本链身份，原始 FASTQ/FASTA/官方未注释 CSV 不适用。手填平台任务路径仍检查任务归属。关联运行归属于来源任务所有者，独立运行归属于提交者。产物按 run/attempt 独立保存，PNG/manifest 原子发布；下载用产物 ID 并核验所有权、版本、真实路径和符号链接。历史列表只读进度摘要，详情才载入产物，不在每次列表轮询扫描所有图。

Web 入口包含新建任务开关、任务输出页共用预览组件及独立工作台（treemap-view.js/treemap.js/treemap.css）；适用于五流程最终 mapping 结果及兼容 AIRR 目录，不限制为 Pig。默认不加载图片；选择样本或链/合图类型后直接展示，两项可组合筛选，无需确认。共用组件按筛选结果分页，每页最多 7 张，仅为当前页创建 img，采用懒加载；“查看全部”清除筛选并仅展示第一页。轮询保留筛选、页码与未变化的图片节点，所选样本/链暂时缺图时保留条件，不能悄悄切回全部；切换生成记录重置视图。新输出与 ZIP 的单链 PNG 直接位于 `individual_treemaps/`，不再按样本建子目录；合图仍位于 `multi_chain_treemaps/<安全样本标识>/`。历史文件不迁移，ZIP 按 manifest 的 kind/sample_key 整理路径。支持全部结果/当前样本流式 ZIP，权限、版本及路径检查必须全部通过，不静默遗漏文件；全量含根目录统计 CSV，样本 ZIP 仅含该样本图片。旧任务未设置不会自动补生成。关联任务图随任务归档，纯目录绘图不自动进入旧归档脚本。Docker 白名单打包 analysis/treemap 与 postanalysis.py，环境显式依赖 Pillow。额外验证使用临时合成数据与独立输出，完成后清理脚本与测试日志。

任务详情的树图组件采用 `mount(..., {embedded:true, metaTarget, actionsTarget})`：共用筛选与分页图库，名称不在正文重复显示，状态/处理数量/创建时间与取消或重试按钮合并到任务树图标题栏。只有生成中显示进度条，完成后数量显示在状态旁；计数口径折叠为“计数说明”。当前已有生成记录时不再显示无效的“生成树图”按钮，失败/空结果等通过该记录的重试操作处理；多条记录才显示折叠的“其他生成记录”，入口链接到工作台。轮询保留折叠状态、筛选与图片节点。

Treemap 新输出统一为关联任务的 `<任务输出>/treemap/YYYYMMDD_HHMMSS/` 或纯目录模式的 `<默认输出根>/<用户>/treemap/YYYYMMDD_HHMMSS/`。自动与手动生成共用 `allocate_output()`，在 SQLite 写事务内检查 indexed output_root 与磁盘占用，同秒冲突增加 `_02`、`_03`；时间采用北京时间。记录 ID/attempt 仍在数据库，不能以新约定推算历史记录路径，也不能自动迁移旧目录。下载与重试继续使用记录中的 output_root。

PNG 命名统一由 `image_relative()` 负责：新单链图直接输出为 `individual_treemaps/样本名__CHAIN.png`；`chain_image_names()` 在完整选择上一次性识别同名、清理/截断/大小写后的重名，仅冲突样本添加稳定短标识并避开已有名称。重名映射保存在运行元数据 image_names，V2 分批处理不能仅按当前波次猜测名称。多链合图仍为 `multi_chain_treemaps/<安全样本标识>/样本名.png`。新手动去重键含 IMAGE_LAYOUT_VERSION；自动意图键不变，不自动重跑已完成图。无 image_names 的历史运行续跑保持原磁盘布局；历史图片仍按 manifest 原路径读取，下载 ZIP 时单链图按完整样本集合平铺并防重名。

## Mapping 当前状态

`mapping/fasta.py` 递归读取 `<sample>__<chain>.csv`，要求 `CDR3(pep)`、`copy`、`joinedSeq`，生成 `>零基行号_CDR3_copy` 的 FASTA，保留相对子目录及 copy 文本。支持 CLI，独立转换默认仅 IGH；`copy` 不展开或加权序列。目录中有 `AL_s003_v8__IGH.csv` 示例。

`mapping/umi_count.ipynb` 的整理逻辑已提取到 `mapping/pipeline/umi_count.py`，从 AIRR 的 `sequence_id` 末尾还原 copy 为 `umi_count`，没有该列时兼容历史 `barcode` 列。不做 UMI 聚类或重新计数；原 notebook 保留。

第五条 Web 流程 `mapping` 已注册，入口为 `mapping/pipeline/run_mapping_pipeline.sh`，三阶段由 `run_mapping.py` 执行。仅 human：扫描目录后按链多选，只处理所选链；不要求 Submission/Barcode/Match 审核。阶段为 `01.fasta`、`02.igblast`、`03.umi_count`，各阶段成功后写 DONE，全部成功才写 `.pipeline.DONE`。02 和 03 各输出筛选后的 `<样本>__<链>.tsv`，03 添加 umi_count；不输出 raw/filtered 两份文件，逐文件续跑。数据库默认沿用现有 `/data/scAnalyis/Scigblast/igblast` 挂载，通过模块配置或环境变量覆盖。命令见 `mapping/README.md`；实施设计见 `plans/20260914_mapping_module_plan.md`。不要直接调用依赖 FASTQ manifest 的 Base stage5。

`mapping/pipeline/resources.py` 使用主机可用内存、cgroup v1/v2 上限及使用量、CPU 配额/亲和性计算实际线程和 worker 数，保留 5% 内存余量（至少 256 MiB）。单 worker 内存配置是排程估计，不是硬性 RSS 限制；余量后不足一个 worker 时明确报错。

## 开发与验证

按用户 2026-10-27 明确要求移除项目全部测试代码（测试目录与合成测试基准脚本），并清理临时测试日志、缓存；后续额外验证应放在独立临时目录，结束后清理。保留产品合成教学包/生成器、镜像构建必需的 check_runtime.py / check_bundle.py，以及正式任务与 IgBLAST 日志。

2026-10-28 部分实施进度方案：`pipeline_progress.py` 在 Web 当前 attempt 私有 job 状态目录创建 SQLite 进度库，Base/Pig/reMapping 按样本链写准备、比对、筛选、发布、缓存/成功/失败状态；`execution_json.process_token` 绑定读取，旧 attempt 不回显。后台线程有界读取当前原始 AIRR 完整行，显示的是未筛选文件已输出记录，不等于保留行或最终完成；读取失败只影响观察，原命令与分析结果不受影响。已验证 IgBLAST 1.22.0 的 Pig TRA/TRB/IGH/IGK/IGL 和 human IGH/TRB 代表性数据，仅这些工具/物种/链且观测稳定至少 20 秒、无积压、速率差不超过 4 倍时才显示粗略比对剩余区间，不含筛选/发布。只有全部活动任务可估计且没有内部队列时才显示当前比对总体估计；不得声称全 pipeline ETA。IR/10X Web 监控器增量读取本 attempt 的任务日志，显示已启动的样本链批次和比对完成数；批次比对结束不表示后续合并筛选完成，不再把启动序号当完成百分比。该日志跟踪不提供序列级进度或 ETA。进度库是观测信息，DONE/原子发布仍为成功依据；部署需同步 Docker 白名单和 check_bundle。

Web 预检用 blastdbcmd -info 核验参考索引；可选链 D 缺失时还须检查 IgBLAST 默认 `<species>_gl_D` 可用，可选 C 缺失则提示。V/J 为单条 1 bp 占位时拒绝分析，D 占位保留兼容。2026-10-28 在隔离容器中以 human TRA 实际确认：无链 D 且无默认 human_gl_D 时即使是 VJ 链也会报 BLAST Database error；当前该挂载库的人源 TRA/IGK/IGL 预检应拦截，IGH 可通过。不得凭此自动修改数据库或分析参数。参考核验缓存与所有同前缀文件的 inode、大小和 mtime 绑定。目录浏览按 100 项分页，以目录身份/mtime 和路径、类型、查询绑定游标；每页仍会枚举目录但内存有界，目录变化需重新读取。删除前事务认领 DELETING，锁外执行 rmtree，重试对照输出目录和状态目录的设备/inode，禁止重新 Match、续跑和新任务重叠；进程仍在删除时禁止重复请求。归档先验证文件再原子发布 pending journal、压缩包、正式 marker；中断时验证 pending 与原文件后恢复。IR/10X 后处理最多同时保留 32 个输出句柄，10X 代表 map 流式读取；原始结果与排序契约不变。

Base/Pig 在 IgBLAST 前通过 `scigblast_igblast_workers` 按现有线程和内存预算计算实际任务数，guard 需求与 worker 使用相同数值，预算不足一个 worker 时失败；保留总共 2 条 pipeline、每条最多 4 个链任务。官方 runner 另用同一路径的 `.budget` 阶段锁做保守资源协调：探测到的 CPU 和内存允许两份已知阶段估算同时运行时使用共享锁，其余使用独占锁；Match、清理和 reMapping 当前为独占。默认共享内存总额 300 GiB、预留 150 GiB，实际总额不超过探测到的容器/宿主机上限；自定义总额或预留值时暂时统一独占。它不是精确额度预留或 CPU/RSS 硬限制，估算误差及其他进程仍可能触发内存 watchdog。所有 runner 需更新到同版本、共享可写锁路径，旧 runner 或单独调用阶段脚本不会参加此协调；锁文件运行期间不得删除。开始邮件需同时确认运行槽位及首个阶段资源锁。SMTP 的稳定 Message-ID 可以辅助识别重复，但服务重启可能重试已被 SMTP 接受而尚未记账的邮件。

2026-10-22 Web 优化：目录、reMapping 和独立 Treemap 重扫使用候选结果，失败保留已确认草稿，但禁止使用失败/旧来源提交。目录按稳定 ID、读端路径与 versions 恢复编辑；reMapping/Treemap 扫描返回不透明 source_version，仅为恢复选择使用，不作为提交授权。变化条目需重新选择；首次扫描默认全选、重新 Match 新增项默认不选保持不变。分页 GET 仅对网络错误与 502/503/504 最多重试两次，不重试扫描 POST 或权限/过期错误。任务列表按 job.id/单元格更新，保留链接、按钮及 summary 焦点；Treemap 详情最多一个请求在途、一个最新待处理目标，不使用递归定时器。

`web/input_manifest.py` 的私有 LRU 上限为 8 个快照、20,000 行、估算 32 MiB，对外仅返回页/清单副本；每次命中仍核验文件身份及 owner/pipeline/path/expires。`web/mapping_results.py` 按完整 key 共用在途构建，CSV 解析在全局 LRU 锁外，失败释放等待者；文件可用性仍实时检查。任务分页以 created_at/id 双降序稳定排序，owner/created_at/id 组合索引不改变统计权限范围。

Treemap 新运行使用 `web/derived_store.py` 的 storage_version=2：derived_runs 只存小元信息与进度，derived_items/artifacts/samples 分项存储。每波最多读取 32 个 queued 候选（实际 worker 上限不变），仅写变化项和新产物；合图按一个样本读取，详情/最终表才恢复全量清单。无版本/版本 1 沿用整份 JSON，不自动迁移或补算。创建/发现、批次使用/发布、合图图片和最终发布继续核验来源/版本；保存事务重新核验取消与 parent attempt，缺失/变更单链图在重试时重做。删除任务须同事务删除三个子表；不得把 `_partial`、source 或 clone_table 暴露给 API。新模块必须同步 Docker COPY、白名单和 check_bundle。已经写入 v2 的数据库不能只回退旧代码；停服备份后须做受控 v1 兼容导出或恢复对应旧数据库，优先向前修复。


Base/Pig 已支持 `sample_source=directory`，`pipeline_input_manifest.py` 扫描并验证明确配对，`web/input_manifest.py` 保存用户绑定快照和不可变清单；API 为 `/api/input/scan`、分页扫描视图、任务 `/input-assignments` 与 `/input-rematch`。目录模式绕过外部 XLSX，仍生成原 Match CSV、进入人工审核；严禁将 requires_submission=false 分支当通用模式，该分支仍为 reMapping 专用。runner 接收 `SCIGBLAST_RUN_INPUT_MANIFEST/HASH`，检查失败必须停止，不能复用旧成功 Match。Base/Pig 阶段指纹包含清单与共享模块。IR/10X 尚未开放目录模式；文件/样本身份不得通过目录名或 Chain 猜测 Barcode。UI 状态组件在 `web/static/directory-input.js`。

新建 Pig 目录任务清单记录 `output_layout=sample-v1`，各阶段用 `pipeline_input_manifest.pig_output_relative()` 生成单层样本目录，最终链结果为 `05.igblastn_out/<dataset>/<sample>/<CHAIN>.tsv`，仅保留单链 TSV，不再生成 TCR.tsv/BCR.tsv 聚合副本。旧清单无该字段、Submission 模式沿用原 `raw_relative_parent/pair/sample` 路径；目录重新 Match 保留任务原布局版本。不能只修改最终阶段，否则前序输入和缓存会失配。Shell 阶段在构建任务列表时解析路径一次，避免每个 worker 重读整份清单；Pig 阶段指纹始终包含共享模块。

首页流程介绍同时提供 `web/static/demos/scigblast-teaching-v1.zip` 合成教学包与 README；由 `web/tools/build_teaching_demo.py` 生成。教学数据仅练习格式、选择、提交与审核，随机序列不保证有效免疫注释，FASTQ 不模拟 IR/10X 建库。不要把真实用户数据打包进静态下载；更新包时同步教程、检查点和 Docker 白名单。

Mapping 扫描还按 `sample_key=相对目录/样本名` 展示样本及链，默认全选，用户可多选样本。API 字段为 `mapping_samples`，runner 环境变量为 JSON 数组 `SCIGBLAST_MAPPING_SAMPLES`；任务保存具体选择，三个阶段仅处理“所选链 ∩ 所选样本”的源文件。未传样本选择的旧任务/CLI 默认全部样本，显式空列表拒绝执行。

所有 shell 命令按项目要求加 `rtk`；无对应过滤器时使用 `rtk proxy <command>`。Git 历史采用 `Add/Fix/Improve/Unify ...` 英文祈使句提交说明，未发现统一 formatter/linter 配置。

```bash
rtk proxy python -m pip install -r web/requirements.txt
rtk proxy docker compose -f web/docker-compose.yml build
rtk proxy docker compose -f web/docker-compose.yml up -d
```

临时模拟检查不代表真实 IgBLAST 全量验收。分析工具链在 Linux/Docker 验收。镜像由 `web/Dockerfile` 和 `web/Dockerfile.dockerignore` 白名单打包，新增流程需同步 `web/check_bundle.py`；宿主机脚本修改需重建镜像才生效。`deploy.sh` 会先拉取代码并重建服务，不能作为普通本地检查执行。

用户指定测试服务器通过 `ssh remote_nanhua` 访问；测试数据在 `/colddata/SCigblast/data/SCigblastWeb/web_test/20260909`。远端验证使用独立临时输出和受限资源容器，输入/参考库只读挂载；不要直接修改在线服务、生产结果或并发配置。

修改前检查 `rtk git status --short`，保留已有未提交改动。不要批量改写或提交 `data/`、`reference/`、数据库、结果目录及工作簿。按用户指定保留本文件名 `AGENT.md`；后续 AI 请显式读取本文件。
# 2026-10-31 后续 QC / primer 分析

`analysis/repertoire/` 为 Pig/base 后续分析模块，`pipeline_stage_metrics.py` 读取已有阶段 JSON/摘要，`pipeline_trace.py` 在每链 05 过滤时保存未筛选精简注释、query 全集和 BGZF+SQLite 索引。`filter_mapping_airr(..., trace_context=...)` 旁路失败时保留 raw，返回 trace_complete=0，原筛选列/顺序/谓词不变。默认 mapping 档；Web 新建任务 trace_mode=full 时再归档原始 R1/R2 序列与 fastp/clean 成员关系，不存 quality、不改输出 header。跨样本用来源分区隔离，同分区 ID 碰撞/多注释行不可猜测归属；一条 query 的多个链任务不相加作输入总数。

`web/repertoire_analysis.py` 提供 `/api/reports` 的上传、扫描快照、列映射预览、前置计划、队列、取消/重试、PNG/统计表/ZIP、分页记录及受筛选的 FASTA/TSV 导出；`/reports` 是独立已有结果入口，详情页有“数据变化”“引物分析”。使用独立 report_* 小表和任务私有明细 SQLite，不能将逐 read 放进 Web 主库。匹配仍是严格 5′ 全长 IUPAC，read N 不视为确定命中；同序列异名的名称/家族口径分开。XLS/XLSX 实际合并范围继承，公式只读保存值。Submission 的组名/完整引物名混用由显式开关控制，不能凭模糊名称猜测预期基因。

新任务默认 generate_report=true，但只为 Pig/base 完成后自动入队；旧任务没有该选项不会自动补算，手动入口可用。已有 merged+final 可补全部 query 的 primer 匹配，但最终缺行的原因未知；仅 final 时只统计结果子集。没有当次参考清单不补造零命中。图与 Web 共用 summary.json；**用户最新要求只导出 PNG，不生成 PDF/SVG**。图页最多 7 张，可单独排队绘制某个 primer 的 C 区分布。参考基因零观察与未注释序列分开，不据此断言引物失败。

后分析单进程独占同一平台门锁，主分析有队列时不启动；Linux 用进程组身份 token 做取消和 RSS/时限监督。活动 report runs 在重新 Match/续跑/删除/归档前阻止来源变更；归档声明需在 SQLite 写事务内检查 report_runs，不能只看 Python 锁。报告随来源归档，独立目录分析不改来源。新增模块已加入 Docker 白名单/check_bundle；pip 依赖 xlrd、matplotlib 和系统 fonts-noto-cjk 需镜像重建。CLI 与边界详见 `analysis/repertoire/README.md`。验证脚本与产物仅放独立临时目录，结束清理，不新增永久测试目录。
# 2026-10-31 最新引物处理规则（优先于上文无剪切的初始方案）

用户最新要求为“先记录 primer 归属，再去除引物进行 mapping”，且未匹配/不同长度多重匹配保留原序列继续比对，不能排除。`pipeline_primer.py` 在 Base/Pig 05 的 IgBLAST 前记录逐 query 的全部名称/家族、状态和前后长度；可确定相同长度时剪切，否则原样保留（primer-only 也保留）。不改原合并 FASTA 和 ID，所有记录数保留，独立生成比对输入。正式分析启动前 `REPORTS.freeze_mapping()` 冻结运行前配置到私有 JSON，SHA 和新输入参与缓存；Match-only 不进行此处理。没有表/明确关闭前置处理时走原输入。运行中/结束后补表只重新统计，不能伪装成已经重新去引物比对。

trace 归档未剪切的原 query，并记录实际 mapping 来源及 `primer_preparation`，注释始终来自真实比对输入。逐 query 归属索引通过原 ordinal 关联，AIRR 仍按唯一原 ID 核验；ID 不唯一继续标未知。统计和 PNG 将已去引物/保留原序列分开，明细的 mapping_primers 与本次分析 primers 分开保存；不能在去引物后的序列上重新识别原 primer 而丢失归属。Web 可按 C 区处理分组、精确剪切状态筛选/导出。新准备模块已加入 Docker/check_bundle。规则和配置入口详见 `analysis/repertoire/README.md`。

2026-10-31：引物与基因统计仅汇总 primer 对应的 C 区（c_call）分布，CSV/XLSX、Web 与 PNG 同步收敛到 C；缺少或歧义 C 注释仍单列。原始 AIRR 与私有追溯保留 V/D/J/C 字段，不改变 IgBLAST 比对和链筛选。报告版本已更新，旧报告需重新生成后获得 C-only 导出。

2026-10-31 分类输出：后续分析报告 v4 将 CSV 与 PNG 分到 01_data_changes、02_primer_matching、03_primer_preparation、04_primer_c_distribution、05_filter_summary；report.xlsx 与 sample_summary.csv 保留根目录。数据变化以样本为单位，每样本输出一张 <样本标识>__stages.png，汇总该样本全部阶段/配对/链，按单位分区，不跨链累加；Web 默认选择一个样本并显示其图，链筛选作用于明细。引物配置默认仅展示上传、列对应、序列类型和前置去引物开关，单工作表自动选择，多工作表才显示选择器；结构/表头/范围等参数保留在高级设置中。summary.exports 记录 CSV 路径，文件 API 兼容旧的 CSV 名称请求，ZIP 保留分类结构。旧报告需重新生成，新目录不搬移旧产物。

2026-11-01 后续分析 v7：引物匹配构成改为三类总统计（唯一/多重/未匹配），取消 X 轴；matching_total_summary.csv/Excel 同名 Sheet 提供总计。共享输入按样本配对、归档来源与记录序号跨链去重，重复 ID 不合并；原始端与 query 分开，历史只有最终 TSV 时保留链结果行口径并说明，未评估不计入未匹配。Web 的 matching_totals 按 chains 成员筛选，不能直接累加各链 match_counts 冒充输入总量。不再输出 primer/coverage/filters 三类 PNG，旧图从图库隐藏；03/05 的 CSV 和全部私有追溯仍保留。

2026-11-01 后续分析 v8：render.py 统一蓝灰/赭色科学绘图样式，支持中文字体回退、实测全名标签宽度、0–100% 统一比较轴、右侧数值列、热图格内比例；stage 图仍用竖向柱状图，多于三链时采用上二下一跨列布局。render 的 figure_audit 回调在最终画布绘制后、PNG 保存前执行，用于本地技能的物理对齐/文字/碰撞审阅，生产不依赖技能安装。该版本只改绘图与缓存版本，不改统计或 pipeline。模拟计数不代表真实分析，输出仍仅 PNG。

2026-11-01 v9 纠正“总统计”含义：引物匹配总览是全部样本的一张 100% 堆叠竖柱图，每样本一柱（唯一/多重/未匹配），不再把所有样本合并为三条水平条形。matching.py 从已跨链去重的 matching_totals 按 sample+unit 汇总，每样本使用自己的分母；CSV/Excel matching_total_summary 也按样本分列三类。Web 总览按当前链筛选展示所有样本，样本筛选用于其他图及明细。N1* 等替代引物组结果必须作为同一样本的独立对照标明，不能与原条件混算或声称新生物样本；不自动猜测引物组。

2026-11-01 v10：引物 × C 基因图由 heatmap 改为 composition 类型的 100% 堆叠柱，每引物一柱，C 基因固定配色，标注 ≥6% 组分；_composition_ 替代新文件名中的 _heatmap_。统计继续使用唯一 C 注释的引物内分母，Top N 以外合并其他类以保持 100%，不虚构 Expression/Unique CDR3 指标。CSV/源计数不变。Web 图库需同时识别 composition 与历史 heatmap。单引物 C 分布标题不显示“去引物后”，剪切分组仍在元数据中保留。

2026-10-31 引物列预览：默认列配置保留；上传后列输入下显示表头与示例，修改列立即同步，展开所选列内容可见前 5 条数据及真实 Excel 行号，名称/序列成对展示。工作表/表头行切换使用有界预览并忽略过期响应；支持 AA 等列，组名仅按真实合并区域继承且标注合并，不向下填充普通空白。预览仅供核对，不改变匹配规则。

2026-10-31 引物配置 UI：使用上传/列对应/匹配方式三个分区，文件上传卡保留原生可访问文件输入并显示读取状态；列映射按两张表分组，所选列预览通过可键盘切换的 Tab 展示，修改列自动定位对应表。预览表明确区分 Excel 行号、列字母、表头和等宽序列，长表在内部滚动；共用主题变量适配日夜与窄屏。字段、默认值和匹配算法不变。

2026-10-31 后续分析界面统一：数据变化、引物 C 区与独立入口使用记录状态条、筛选区、数据范围、下载区和折叠明细；图片按分析类别筛选并保持每页最多 7 张，共享图标明全部样本/全部引物。数据变化页只展示所选样本的一张汇总图。独立入口有已有结果时优先展示结果，无记录时展开配置；扫描可全选/清空，分析按钮显示加载状态。导出的 PNG 与统计字段不改动，默认配置与分析规则保留；浏览器已验证扫描、分类、分页、追溯、下载、状态、日夜主题和 390px 窄屏。

2026-10-31 数据量汇总 v5：数据变化聚焦原始/Fastp/Header 清理后的 reads 数、相邻减少量和保留率、输入配对与合并序列数及合并率、各链 IgBLAST 输入序列和最终 AIRR 结果行数。每样本一张 PNG，新增 01_data_changes/stage_volume_summary.csv 与 Excel 同名 Sheet；原始阶段指标仍在 stage_summary.csv/折叠明细。按样本汇总时上游每个配对只计一次，某配对缺计数时不冒充完整总量；mapping 的合并分母按该链实际覆盖配对计算，不跨链累加。不同单位不直接相减或计算保留率，明确配对到合并序列的关系用于合并率。Web 首屏显示输入 reads、Fastp 后 reads 和合并序列三项。主 pipeline 与结果 TSV 不改动；已有报告重新生成后取得新汇总。

2026-10-31 数据量图 v6：每个样本仍输出一张 PNG，reads、双端合并和分链 IgBLAST 输入/结果三个区域全部改为竖向柱状图。柱顶标注数量，质控保留率/减少量和合并率保留，统计口径与 CSV/XLSX 不变。已有报告重新生成后取得竖向样式。
