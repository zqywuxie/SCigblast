# Repository Guidelines

## 工作规则

- Shell 命令始终以 `rtk` 开头；无专用过滤器时使用 `rtk proxy <command>`。检索优先 `rtk rg`。
- 修改前检查 `rtk git status --short`，保留已有未提交改动，不批量提交或改写数据、数据库、结果与工作簿。
- 验证脚本和模拟产物放系统临时目录，完成后清理；按用户要求不在项目新增永久测试目录。保留产品教学数据、`check_runtime.py`、`check_bundle.py` 及真实任务/IgBLAST日志。
- 保留本文件名 `AGENT.md`。历史变更笔记已归档到 [docs/history](docs/history/)，不应作为当前规则覆盖下文。
- 项目结构见 [README.md](README.md)，当前与历史方案见 [plans/README.md](plans/README.md)。移动运行脚本会影响导入、Docker 和 runner；本轮仅整理文档与确定的临时文件。

## 项目入口

SCigblast 使用 Python/Bash 执行免疫组库分析，`web/` 为 FastAPI/Jinja2/原生 JavaScript 平台，SQLite 保存账户、任务和操作记录。

先读 `web/pipeline_registry.json`，再读目标 runner、配置和 `web/README.md`。注册表是流程入口及阶段的依据：

- Base：`Igblast_base/pipeline/run_igblast_base.sh`，01 Match → 02 fastp → 03 header → 04 PANDAseq → 05 IgBLAST/链筛选 → 06 AIRR preprocessing。
- Pig：`PigIgblast/pipeline/run_pig_pipeline.sh`，当前注册至 05；不要凭历史文档补造阶段。
- IR：`IR_split/pipeline/run_ir_pipeline.sh`，自动识别输入，拆分 Barcode/UMI、生成代表序列、IgBLAST、preprocessing。
- 10X：`10X_split/pipeline/run_10x_pipeline.sh`，Cell Barcode/UMI、代表序列、IgBLAST、链聚类；不混用 IR 拆分规则。
- reMapping：`mapping/pipeline/run_mapping_pipeline.sh`，Human CSV → FASTA → IgBLAST → UMI count。
- `pipeline/`、`IR_split_merged/`、`preprocess/` 当前不是注册 Web 流程。

各流程配置位于 `00.pipeline_config.env` 和对应 `tools/pipeline_config.py`。共享处理实现在根目录 `pipeline_*.py`；工具路径可由 `SCIGBLAST_RUNTIME_BIN_DIR` 覆盖，参考库单独挂载。数据库来源以 [reference/DATABASE_SOURCES.md](reference/DATABASE_SOURCES.md) 和实际配置/索引回读为准，不从旧笔记猜测当前库覆盖。

## 流程、资源与结果契约

- 全用户共用最多 **2 个 pipeline**；每个 pipeline 最多 **4 个 IgBLAST 链任务**，可跨样本调度，不是每样本四个线程。现有 CPU/内存预算可进一步降低并发。
- 五条正式 runner 共用资源门锁与两个运行槽位；阶段预算锁参与协调。报告和 Treemap 继续按现有独占门锁运行。所有子进程继承 FD，运行期间不得删除锁文件，也不绕过排队。
- 状态属于本次 attempt；停止/恢复前核验 PID、process token 和进程组身份，不能仅凭旧 PID 发送信号。进度观测不替代 DONE/原子发布；不得将链完成进度说成整条 pipeline ETA。
- Base/Pig 每链比对完成后，在同一 worker 中检查有效 V、productive 和 `locus == 所选链`，原子发布 `<CHAIN>.tsv`，保留 AIRR 列。新任务不再生成 TCR/BCR 汇总副本，不另加 locus_split 阶段。
- 普通主结果筛选与报告的 Mapping 效率口径不同：报告成功 Mapping 为有效 V 且 locus 匹配，不额外要求 productive；保留结果另计。
- IgBLAST V/D/J/C 当前各输出最高分命中，上限 1；不做候选基因衍生分析，历史多 call 不强取首项、不改写。
- 缓存依赖工具、参考库、aux、参数、筛选及输入快照；变更参考材料必须使缓存失效。缺 C 库不补造 c_call，缺真实 V/J 或依赖默认 D 时按预检处理，不能把 1-N 占位当真实覆盖。
- `pipeline_locus_split.atomic_text()` 使用同目录独占临时文件并原子发布，遵循 umask/默认 ACL；不要用 mkstemp 的 0600 发布正式结果，也不要多线程临时改 umask。
- 容器按部署用户非 root UID/GID 运行；`deploy.sh` 自动填充空缺 UID/GID，状态迁移保留 ACL 和其他属主。部署脚本会拉取代码/重建服务，不作为常规检查执行。

## Web 与生命周期

`web/app.py` 管理路径、队列、审核、状态和结果；`auth.py` 管理归属，`submission.py` 保存不可变表格副本。单 Web 实例、单 Uvicorn worker。上传后才扫描并选择列；匹配结果需要人工审核，目录输入 Base/Pig 仍生成 Match 清单并进入审核。

所有输入、下载、归档、删除必须核验 owner、允许根目录、版本和真实路径。不能仅凭样本名推算文件位置，不能将缺文件当生物学零结果。分页结果在扫描完成前 total 可以为空，使用 has_more/next_cursor。重扫失败保留草稿，但不能用旧/失败快照提交。

来源重跑、归档和删除须在 SQLite 事务内检查活跃 report/derived/read lease。HTML 缓存按 owner/run/材料/前端指纹隔离，有容量上限；导出期间续租，正常/异常/断开均释放，归档/删除后清理相关缓存。旧结果兼容读取，不自动迁移或补算。

SMTP 凭据仅保存在服务器私有设置，不回显、不写日志/任务参数。通知按 attempt 去重；开始通知需确认已获运行槽位及首个阶段锁。邮件验证使用 mock SMTP，不使用真实授权码。

## 引物与后续报告

当前实现与命令见 [analysis/repertoire/README.md](analysis/repertoire/README.md)。

- 先在原合并序列上记录 primer 归属，再对明确长度片段剪切后 mapping；未匹配、不同长度多重匹配、结构不符、剪切后为空均保留原序列继续比对。原 FASTA 与 ID 不覆盖。
- 运行前配置冻结后参与 IgBLAST 缓存。运行中/结束后补表只重算归属统计，不能声称历史结果已经按新引物剪切或重新比对。
- 匹配使用严格 5′ 全长 IUPAC；read N 不算确定命中，不自动反向互补或偏移。原 query、实际 mapping 序列和最终 AIRR 靠来源/ordinal/唯一 ID 及序列注释校验追溯，重复 ID 不猜配。
- 逐 read 索引保存在私有 details.sqlite，不写入 Web 主库，不进入普通下载包；同来源多链输入不能重复累加。
- Pig/Base 的组成以已发布且回执一致的 06 junction_peps 为依据。Expression 累加 copy/umi_counts，不能声称是真实 UMI 分子计数；缺 06 时不假装有后处理组成。
- V/J/C 分别用可明确分类到该区基因的数据作组成分母；Unique CDR3 按基因内有效 AA 去重，跨基因分别计数，不混用全局 distinct。Isotype 主类合并后重新去重，以可分类 C 数据为分母。零分母为 N/A，不伪造 0。
- 引物匹配率和 Mapping rate 逐 read 计算。低于阈值时的其他组测试及存疑分析独立保存，不自动替换原组，不重新 mapping。
- Total Reads 使用 Merged；Total Receptor RNA 使用 AIRR 后处理总 copy/umi_counts；Total Unique CDR3s 使用对应范围的唯一有效 CDR3。Raw R1/R2、Clean R1/R2 分别取真实计数，不对半拆总数。
- 多样性读取对应 06 Datapoint，B cell 指标使用可追溯的后处理数据，不为 UI 修改统计值。
- 仅更新 HTML 时使用 `python -m analysis.repertoire.export_html`，读取已完成公共表和 PNG；不要调用全量 `run` 重扫 reads。新数据/引物/规则变化仍需重新统计；不能只改分母文案冒充新规则。
- 完整统计后保留私有校验检查点，展示失败重试可复用；Web 位于报告输出父目录 `.report_checkpoints/<id>/`，与结果在同一文件系统；成功原子发布时消费，归档/删除清理，分析日志保留。检查点不进入公开包；不绕过来源/配置/代码指纹核验。

## 报告与 Treemap 展示

平台操作及报告筛选为中文，图题和专业指标为英文；仅输出 PNG，不生成 PDF/SVG。HTML 离线可导出当前 PNG/CSV，最多 7 张活动图；顶部 Tab 顺序为总体概览→样本比较→引物分析→样本分析→基因匹配，各模块独立筛选，总览默认全部样本。单样本 Isotype 默认可见，按钮切换保留阅读位置；逐引物基因组成为单样本。Expression 左、Unique CDR3 右；不加底部重复明细和技术版本尾注。基因匹配按家族分组，默认 10 个基因，可调整。

多链样本共享输入只计一次，链级组成/RNA/CDR3 不混合；基因匹配只提供一份全部样本/引物的总表 CSV，不随界面筛选或分页变化，保留链及必要的存疑组/参考身份。分类覆盖仅从可靠 copy 证据取得，不改变组成分母。主页样本信息默认展开并列出全部行，在固定高度区域滚动/拖动查看；HTML 轻量分享与 HTML＋Excel 核查包不包含私有追溯库。

Treemap 使用用户 QR 绘图逻辑，按有效记录数聚合 V/J/CDR3，不自行改成 copy 权重。单链 PNG 平铺 `individual_treemaps/样本__CHAIN.png`；至少两条可用链才生成多链图，位于 `multi_chain_treemaps/`。合图链区面积不代表链间丰度。Clone Top 频率使用各样本链内有效记录数。新旧输出按存储版本兼容，不推算旧路径或自动重画。

## 验证与部署

代码变更运行适当的定向检查；Windows 模拟验证不代表 Linux 分析工具链全量验收。新增文件检查 Docker COPY、白名单及 `web/check_bundle.py`。不新增永久测试目录；保留教学包和运行环境检查。

远端使用 `ssh remote_nanhua`，隔离验证输入/参考只读，输出与正式任务分离。没有明确部署指令时，不改在线服务或生产并发配置。项目整理不删除原始数据、参考、已完成报告或真实任务日志。

最新八样本报告已完成并更新本地，来源及验证见 [docs/RUNNING_REPORT.md](docs/RUNNING_REPORT.md)；完整历史笔记在 [docs/history](docs/history/)。
