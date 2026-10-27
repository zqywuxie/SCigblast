# Repository Guidelines

## 项目定位与阅读入口

SCigblast 是免疫组库分析平台：Python/Bash 执行分析，`web/` 提供 FastAPI、Jinja2、原生 JavaScript 任务界面，SQLite 保存账户、任务和操作记录。先读 `web/pipeline_registry.json`、`web/app.py`、目标流程的 runner 和配置，再参考 `web/README.md`。`pipeline/README.md` 有过时路径与流程描述，发生冲突时以当前注册表和可执行代码为准。

## 分析架构

- `IR_split/pipeline/run_ir_pipeline.sh`：Match → fastp → Barcode/UMI 拆分 → header 清理 → PANDAseq → 代表序列 → IgBLAST → preprocessing。当前统一自动识别 raw/presplit；结果模型位于该分支 `models/`。
- `10X_split/pipeline/run_10x_pipeline.sh`：Match → fastp → R1/R2 预筛选 → header 清理 → PANDAseq → Cell Barcode/UMI 拆分与代表序列 → IgBLAST → 链聚类。不要混用 IR 与 10X 的拆分规则。
- `Igblast_base/pipeline/run_igblast_base.sh`、`PigIgblast/pipeline/run_pig_pipeline.sh`：Match → fastp → header 清理 → PANDAseq → IgBLAST → 按 locus 拆分；Pig 使用专属数据库逻辑。
- 每个分支通过 `00.pipeline_config.env` 与 `tools/pipeline_config.py` 管理配置；Web 通过环境变量启动固定 Bash runner。容器工具路径由 `SCIGBLAST_RUNTIME_BIN_DIR` 覆盖，数据库需单独挂载。
- `IR_split_merged/`、根目录 `pipeline/`、`preprocess/` 当前没有注册为 Web 流程；不要根据目录名推断它们是生产入口。

## Web 与输出契约

`web/app.py` 管理路径校验、队列、进程组、状态、审核和结果接口；`web/auth.py` 管理账户权限；`web/submission.py` 创建不可变 XLSX 工作副本。现有四条流程首次执行 Match 后进入 `WAITING_REVIEW`，全部记录完成标注且存在 OK 记录才能继续。

新任务输出由服务器按用户、流程和北京时间分配。流程产物通常位于 `<output>/<stage>/<dataset>/`，日志为 `logs/<dataset>/pipeline.log`，状态为 `.pipeline_state/<dataset>/`；完成标记名称须与注册表和 `pipeline_done()` 对齐。仅运行单 Web 实例、单 Uvicorn worker；所有用户共用两个 pipeline 运行名额，第三条起按提交顺序排队；五条正式 runner 使用同一共享 `flock`，主分析使用共享门锁加两个独占槽位（锁路径后缀 .slot-1/.slot-2），Treemap 继续独占门锁并与所有主分析互斥；两种 FD 均由子进程继承并在退出时释放。不得删除正在使用的锁文件。Web 的 MAX_ACTIVE_JOBS 与 runner 槽位数同时固定为 2，不通过旧 SCIGBLAST_MAX_ACTIVE_JOBS 环境变量调整。阶段内部并行由各流程配置控制。保留已有任务归属、路径范围、停止进程组及归档行为。

Web 使用 `execution_json` 关联本次 attempt 的启动日志、运行日志边界和进程身份。续跑不能展示旧执行进度；恢复的进程组需核验后才能停止，不得仅凭旧 PID 发送信号。`web/preflight.py` 按匹配/分析阶段检查真实运行环境；`web/table_preview.py` 为普通结果提供有界游标读取，Match 仍保持完整 revision 审核统计。普通结果的 `total` 在扫描完成前可为 null，调用者使用 `has_more`、`next_cursor`、`scan_complete`，不能把尚未扫描完当成零结果。

Base/Pig 使用根目录 `pipeline_igblast_cache.py` 生成一次 IgBLAST 依赖快照，供 stage 与 task 缓存共同使用。涉及工具、参考库、aux、过滤及参数变化时使相应缓存失效；若人工替换参考文件同时保留大小/修改时间，须更新各自配置的 `SCIGBLAST_IGBLAST_REFERENCE_VERSION`。每次重新进入 stage5 时使当前清单的旧聚合失效，成功聚合和摘要原子发布；`output_path` 必须指向实际 task TSV。

Pig 的 05 IgBLAST 对存在的链专属 C 库传 `-c_region_db .../pig_gl_<CHAIN>_C -num_alignments_C 1`，没有 C 库时保留 V/D/J 分析；不能把 TCR 强制改成 Ig。依赖快照对 Base/Pig 均记录 V/D/J/C 的 sidecar，C 库新增或变化使对应链缓存失效。remote_nanhua 上 IgBLAST 1.22.0 已用 TRA/TRB 各 5 条真实序列对照验证：不传 C 库时 AIRR 无 c_call 列，加参数后均有对应 TRAC/TRBC 注释。2026-10-27 补库前 pig_chain 仅 TRA/TRB 有 C 库；当天已按下述记录新增 BCR C 库。缺少 C 参考的链仍不能推造 c_call。05 筛选与 06 locus 拆分须保留新增的 C 字段，旧结果需要重新执行 mapping 才能补齐，不能只新增空列。

2026-10-27 补库前通过 remote_nanhua 的 blastdbcmd 导出核验实际索引内容（不能只看 nhr/nin/nsq 是否存在）：pig_chain 的 TRA 为 V=103/J=98/C=1（C 270 nt）；TRB 为 V=30/D=3/J=18/C=3（C 均 387 nt）；IGH 为 V=17/D=5/J=6，IGK 为 V=25/J=9，IGL 为 V=23/J=4，当时后三者无 C。TRG 只有 11 条真实 V，缺 J/C。TRD 的 V/D/J 虽有索引，实际均为单条长度 1 的 N（placeholder_TRD_V/D/J），无真实参考及 C，不能作为已支持链。TRA/IGK/IGL/TRG 的 D 也为 1-N 兼容占位，不计作真实 D。真实库序列与同目录 FASTA 内容一致；上游 database_251117/pig 合并源也缺 TRD、TRG J/C 和 pig_IG_C，不是分库后丢失。数量为参考序列/等位记录数，V/J/C 组件具备不等于全长基因或全部等位基因覆盖，需来源注释另行核验。

猪 BCR C 区官方来源（2026-10-27）：IMGT 猪 IGHC 表 https://imgt.org/IMGTrepertoire/index.php?group=IGHC&repertoire=genetable&section=LocusGenes&species=Pig 列有 IGHM/IGHD/IGHA/IGHE/IGHG 等，参考登录号包括 AB699686/AB699687/AB699688；其中 IGHD 恒定区基因与 D 片段不能混淆。IGKC 表 https://imgt.org/IMGTrepertoire/LocusGenes/genetable/autotable.php?group=IGKC&species=Sus+scrofa 的 IGKC*01 对应 FP312898:132117-132442，IGKC*02 对应 CU694848:complement(28072-28397)。IGLC 表 https://www.imgt.org/IMGTrepertoire/index.php?group=IGLC&repertoire=genetable&section=LocusGenes&species=Pig 列 IGLC2*01（CU467669）和 IGLC3*01（CU467599），该表注明 IGLC1 尚未提供，不能据此宣称全覆盖。IMGT V-QUEST 猪 IG 下载目录仅 V/D/J，无 C；本次已通过原站 urllib 成功取得 query=14.1+IGHC/IGKC/IGLC&species=Sus+scrofa 的人工剪接外显子核苷酸，以及 query=7.5+IGHC 的单外显子参考。不能将整段多基因 BAC/蛋白展示页直接作为单条 C 核酸参考。

remote_nanhua 已安装 BCR C 库版本 `imgt-sus-scrofa-C-20261027`：`/colddata/zqy/igblast/database_251117/pig_chain/{IGH,IGK,IGL}/pig_gl_<CHAIN>_C`，分别 29、2、2 条；FASTA 为各目录 IGHC.fasta、IGKC.fasta、IGLC.fasta。每个功能性等位基因一个规范 ID，有官方完整剪接记录时优先分泌型；IGH 22 条来自该剪接导出，另 7 条仅使用官方完整单外显子作部分 C 区参考，不能称其全长：IGHD*04(CH1)、IGHG4*01(CH1)、IGHG4*02(CH2)、IGHG5-1*03(CH1)、IGHG5-2*01/02(CH1)、IGHG6-2*03(CH1)。每链 `pig_gl_<CHAIN>_C.provenance.json` 保存原始头、来源登录号、外显子范围、partial_constant_region 和序列 SHA256；保留 IMGT 明示的补齐 N，不推造未测序区。原始导出、准备/测试/安装脚本及报告位于 `pig_chain/.reference_sources/bcr_C_20261027_a8b8497e/`。发布持有平台共享资源门锁的独占模式，原有 244 个文件 SHA256 保持不变。BLAST 序列回读与三条合成 V(D)J-C 阳性对照通过，Web 容器也已读出全部 33 条参考；这不是全量真实样本验证。数据库安装不等于服务代码已部署；Pig 参数修复需随镜像部署后才能自动使用，旧结果须重新 mapping。

2026-10-27 猪库 V/J 标识规范化版本 `pig-vj-canonical-20261027_115846`：remote_nanhua 当前仍使用 `/colddata/zqy/igblast/database_251117/pig_chain`，344 条真实 V/J 参考仅移除 ID 开头的 `2026pig`（如 `2026pigTRAV01*01` → `TRAV01*01`），13 套现存 V/J 索引重建（包括不改名的 TRD 占位索引）；FASTA 核苷酸/大小写、D/C 及原结果未改。TRG 缺 J、TRD 无真实参考的问题仍存在。完整改名前备份为 `/colddata/zqy/igblast/backups/pig_chain_before_vj_rename_20261027_115658/`，含 pig_chain、internal_data/pig、optional_file/pig.aux 及 316 文件 SHA256.json。`pig.aux` 新增 135 条规范 J ID 别名并保留旧条目，内部 V 库和 FWR/CDR 注释不变；10 条真实 TRA/TRB 序列及 5 条合成 TCR/BCR 对照验证改名前后全部 AIRR 字段仅 V/J 名称不同，旧库配新 aux 的结果也一致。切换须持有同一平台门锁的独占锁；不能只改 FASTA 头而不重建索引/同步 J aux。映射表、构建脚本和验证摘要位于新库 `.reference_sources/pig-vj-canonical-20261027_115846/`，详细独立测试位于同级 `.pig-vj-normalize-7gvnh2j3/`。库内 mkdb_251117.sh 已在 V/J 建库前规范化前缀，D/C 不应用该替换；新 mapping 自动使用新 ID，历史结果需重新 mapping 才会更新命名。

上述 V/J 改名已在 Web 容器 `scigblast-web` 的 IgBLAST 1.22.0 再次验收：13 套 V/J 索引序列回读与备份对应，15 条对照的完整 AIRR 与宿主机一致；摘要见该版本来源目录 `container_validation.json`。容器确认能读取新库不等于 C 区修复代码已经部署。

根目录 `pipeline_parallel.py` 为 Base/Pig/reMapping 限制在途任务为现有 worker 数的两倍；Base/Pig 的多链 FASTA 计数仅在同次运行内共享。性能修改必须保留阶段顺序、分析参数、过滤规则、文件命名、列及最终记录顺序；不要依据合成调度测试提高生产并发。

reMapping 结果页按样本和链展示最终 UMI 文件；条数/保留比例来自现有摘要，不等于 copy 总和。`web/artifact_download.py` 流式打包所选 03 文件，保持原文件内容和相对目录，不写入分析输出目录；下载准备 POST 返回 120 秒有效、当前用户/任务绑定的 URL。结果浏览状态按文件保留，文件版本变化重置游标；共享确认与 busy 反馈位于 `web/static/ui.js`。

详情页用 `sessionStorage` 按当前登录用户 ID 和任务 ID 保存浏览偏好。刷新后恢复筛选、样本列表页码、当前文件及表格搜索/字段选择，表格重新读取第一页；不持久化结果行、游标、下载选择或令牌。执行 attempt 变化时清除旧浏览状态。当前文件不在列表页时，通过有界结果查询重新核验，不根据浏览器记录判断下载权限。

新建任务窗口采用顶部流程选择、单正文滚动区与固定操作栏。reMapping 的 `mappingRows/mappingSelected` 保存完整清单与选择，搜索和每页 20 项仅影响显示，不能从当前页 DOM 生成完整样本请求；目录模式继续以不可变 row.id 保存 selected/drafts。纯搜索、分页不使配置检查失效；输入、选择和 Chain 修改则失效。提交先读取请求快照，再锁定控件，`taskSubmitting` 防止重复请求；失败恢复输入和选择。保持重新 Match 的流程、来源和输入目录锁定。

页面主题由 `appearance_head.html` 在首屏加载 `appearance.js/appearance.css`；右上角切换日间/夜间，只在当前浏览器 localStorage 的 `scigblast-theme` 保存偏好，未设置时跟随系统。五个页面共用，不能改变树图图片和导出文件配色。单选下拉优先使用可定制 picker 样式，旧浏览器保留有样式的原生控件与键盘/表单行为。任务列表显示可逐项移除的筛选标签；“重置筛选”和状态栏“全部”均清除流程、操作者、搜索与状态并回到第一页，其他状态 Tab 保留右侧条件。轮询不能重建未变化的筛选标签，避免夺走焦点。

链选择组件在 `web/static/ui.js` 的 `chainPicker/bindChainPicker`，统一按 IG 与 TCR 分组，保留原 checkbox 值与多链选择。reMapping 显示扫描文件数，未发现的链禁用，“全部可用链”和本组快捷选择不能勾选禁用项；默认仍仅选扫描到的 IGH。目录批量勾选只是模板，点击应用才覆盖全部已选样本；`syncBatch()` 展示已使用该组合的样本数。逐样本设置继续独立保存，不根据批量区当前勾选生成请求。

详情页仅保留一处状态与下一步说明，阶段明细按需展开，Tab 吸顶且 reMapping 隐藏 Match。克隆树图为输出文件旁的独立 Tab，支持 `#treemap` 直达、浏览偏好恢复和键盘切换；只在该 Tab 可见时加载/轮询树图，不随输出文件刷新。`region()` 按内容变化更新结构，时间、阶段内进度等局部更新；轮询不能重建未变化的操作按钮、展开区或夺取焦点。吸顶导航高度通过 ResizeObserver 写入详情页的 `--detail-tabs-height`，结果 sticky 预览与跳转偏移沿用该变量。保留原有 Tab 初次落点与键盘操作，不用任务状态轮询强行切换 Tab。任务列表操作列的 `<td>` 保留 table-cell 布局，按钮 flex 放在内部 `.row-actions` 容器，表头和按钮左对齐，不能直接把表格单元格设为 flex。

任务详情的“本次运行时长”由 `execution_runtime()` 根据本次 started_at/ended_at 计算，API 返回 runtime_seconds/runtime_running；浏览器用 performance.now() 按秒更新，沿用现有接口轮询。续跑、重试或 Match 后分析会开始新的 attempt，不累计跨次时长；排队及等待审核不计入，排队期间停止也不把旧启动时间算入耗时。缺失或异常历史时间显示未知，不推算累计时长。

邮件提醒模块为 `web/notifications.py`。用户仅通过 `/api/notifications/preferences` 设置自己的收件邮箱（留空关闭）；管理员 `/api/admin/mail-settings` 配置 SMTP、发件地址和平台链接，默认 QQ 的 smtp.qq.com /465 /SSL，服务初始关闭。自动通知为四类：开始执行 STARTED、完成 SUCCEEDED、失败 FAILED、中断异常 INTERRUPTED/COMPLETED_WITHOUT_MARKER；开始提醒只在本次 launcher 日志确认获得共享资源锁后触发，不以 QUEUED/RUNNING 状态变化或启动进程作为依据。监控按 attempt/PID/process_token 核验并原子保存 execution_json.resource_acquired_at 与去重事件；Match、分析、续跑各 attempt 最多一次，进度不重发。发送前若已结束/停止/重新执行则跳过过时的开始事件。等待审核、主动停止和排队不通知，旧队列中已取消支持的事件也跳过，管理员主动测试保留。密码存于服务端 SQLite，不回显、不写入任务参数或 runner 环境；空密码保留原值，清除需显式 clear_password。`update_job()` 与启动中断处理在状态事务内向 mail_outbox 入队，按 job/attempt/event 去重；后台单线程在事务外连接 SMTP，失败最多 3 次，延迟 60/300 秒重试，不改变分析结果。收件人由 owner_id 决定，不由当前操作者决定。发送前核验用户启用、当前邮箱和任务 attempt/status；已过时的通知、已删任务或已关闭收件设置跳过。SMTP 服务关闭时暂停队列；重启恢复 sending 项。测试必须 mock SMTP，不能使用真实邮箱或授权码。镜像同步打包 notifications.py。

`web/mapping_results.py` 按三个摘要的文件版本和执行 attempt 构建有限 LRU 索引。新任务在内部 `options_json.mapping_inputs` 保存创建时实际选中的源文件，只用于结果完整性展示，不传给 runner 或详情 API；历史任务仅合并已有摘要并标记清单不完整。`/mapping-results` 分页返回处理状态、原因和计数，计数依据摘要，文件可用性只对当前页实时核验；单文件预览与下载直接查索引后核验目标。不要根据样本与链的笛卡尔积猜测缺失输入，也不要把文件缺失计作生物学零结果。

IR `08.preprocessing.py` 的 `read_sample()` 将 Future 和读取临时变量限制在函数内，使上一样本大表在下一次内存检查前可释放；主流程仍按原顺序计算、写出并保存 checkpoint。不要把该读取循环移回长生命周期的 `main()` 局部作用域。

IR/10X stage7 后处理仅在本次进程内复用同一 FASTA 的计数，按真实路径及文件版本识别多链软链接。重新执行会重新计数；缺失/空文件、旧批次目录和 AIRR 行数回退保持原行为，不使用可能过期的阶段汇总替代输入计数。

## Treemap 派生结果

合图仅在同一样本至少有两条不同链的可用单链图片时生成；只选一条链，或其他链为空/失败后仅剩一张图时跳过合图，但仍保存该样本合图阶段完成标记，继续发布 Clone Top 和统计文件。规则同时适用于 v1/v2 运行，计数、单链图、合图比例和文件命名保持原约定。手动生成去重键包含 `COMPOSITION_VERSION`，便于重新生成时使用新规则；自动意图键不变，不自动重跑或删除已完成历史图片。

用户提供的 `treemap_g.py` QR 核心移植到 `analysis/treemap/`，仅按 `productive == T` 且 CDR3 非空的最终记录数聚合完整 V/J/CDR3，不读取 copy/UMI 权重。CDR3 优先 junction_aa，否则 cdr3_aa；保留原始字符串、配色、排序、3000×3000/300 DPI 和固定比例合图。合图链区面积不代表链间丰度。`sources.py` 用各流程最终摘要与真实 output_path 识别来源，不重复统计聚合文件；10X 合并文件以 locus 拆链，IR 使用 preprocessing 的 junction_peps。

新 Treemap 运行聚合时核对行内链字段。普通来源优先 `chain`，没有此列才用 `locus`，不对空/未知 chain 猜链；两个列都没有时才沿用已知单链身份。Base/Pig 的 06 拆分来源显式指定 `chain_column=locus`，不能被附带的 chain 列覆盖。`CHAIN_PARTITION_VERSION` 写入新手动/自动运行元信息及手动去重键；请求显式传 `partition_by_chain`，无版本的历史 Web 运行在续跑/重试时保留旧策略，不能混用新旧统计或自动重算已完成图。工作台新建生成记录可使用新策略。

Base/Pig 新增第六阶段 `06.locus_split`，共用根目录 `pipeline_locus_split.py`，接在 05 IgBLAST 后且成功后才允许主任务完成。以 01 Match 的每样本/配对 igblast_chains 为允许集合，读取 05 摘要真实 task TSV，按 locus 精确匹配目标链，排除其他/空/未知 locus，保留全部 AIRR 列与原始字段值。输出 `06.locus_split/<dataset>/<sample>/<chain>.tsv`，重名样本/规范化冲突时加稳定标识；汇总为 `locus_split_summary.csv`（输入、保留、排除、未知数量）。标准库逐行流式处理，不改写 05，不读 TCR/BCR 合并副本；无保留行输出表头。原子发布与 `.checkpoints` 按实现/输入文件版本/样本链/输出版本复用，强制重跑支持 --force；缺字段、输入变化、文件缺失须失败，不能伪造 DONE。Shell 与 Web 进度/完成标记、Docker COPY/白名单/check_bundle 同步。新建及续跑任务保存 locus_split_version=1；已完成旧五阶段任务不自动改状态或补算。Treemap 优先且对新任务必须读取 06 摘要，06 缺失/失败不能回退到 05；旧任务无 06 时保留 05 兼容入口。目录扫描同一数据集有 06 时忽略 05，不能重复计数。

新增 `clone_top.csv`（kind=clone_top）：按样本链内 record_count 降序排名，V/J/CDR3 文本打破并列，保留全部克隆。frequency_percent/cumulative_frequency_percent 以本样本本链有效记录数为分母，不能改成跨链或 UMI 口径。`clone_table.py` 写 UTF-8 BOM CSV，复用绘图聚合结果，先原子发布每链 `.clone_tables/<entry_id>.csv`，由 item.clone_table 保存内部版本；该字段不能回传详情 API。合图完成后独立 clone_merge 子进程流式汇总，不在 Web 的 PROCESS_LOCK 内读取大表。CSV 总表进入 manifest、单文件下载与全量 ZIP，样本 ZIP 仍仅含该样本图片；空数据仅表头，失败链排除且原因保留在摘要。重试重新合并总表，成功中间表正常复用，缺失/变更时仅重处理对应链。新手动运行的去重键包含 clone_table_version；自动意图键及 PNG 版本保持不变，重启不能为已完成旧任务自动补算；旧记录无该格式标识时保持原行为，可手动重新生成以获得表格。Docker 打包检查须包含 clone_table.py。

新建任务 `generate_treemap` 默认 false，只写 options_json，不传给主 runner；成功状态事务只保存绘图意图。`web/postanalysis.py` 用单调度线程启动有界绘图批次；默认最多 2 个单链 worker，按 cgroup/CPU/可用内存限制，合图仍逐样本执行。`SCIGBLAST_TREEMAP_MAX_WORKERS=1` 恢复串行；worker 总预算为 `SCIGBLAST_TREEMAP_MEMORY_BUDGET_MB`，单进程预算为 `SCIGBLAST_TREEMAP_MAX_RSS_MB`。每批一个轻量监督进程，数据库 PID/token 指向监督进程；所有 worker 同组且继承共享 flock FD，禁止单独释放锁。取消、超总预算、关闭或监督进程异常时核验并停止整个组，确认全部退出后才清除 PID；身份不明则保留 interrupted 记录。进程间只传文件路径和少量 JSON，不复制 DataFrame/图片。主分析排队后不启动下一批；单链失败不影响其他 worker，重试保留成功图。主分析状态、DONE、耗时、输出和邮件不受绘图失败影响。状态和进度独立，重复提交去重；修改来源/attempt、取消、续跑、删除与归档均核验生命周期，旧 PID 必须通过 process_identity 验证。归档认领须在 SQLite 事务中检查无活跃 derived_runs，不能只靠 PROCESS_LOCK。

独立入口 `/treemap` 可选有权限的成功任务或受控服务器目录，`/api/treemap/scan` 返回绑定用户和有效期的快照，提交 selected_ids；跨页/搜索只影响显示。纯目录表格必须有明确样本链身份，原始 FASTQ/FASTA/官方未注释 CSV 不适用。手填平台任务路径仍检查任务归属。关联运行归属于来源任务所有者，独立运行归属于提交者。产物按 run/attempt 独立保存，PNG/manifest 原子发布；下载用产物 ID 并核验所有权、版本、真实路径和符号链接。历史列表只读进度摘要，详情才载入产物，不在每次列表轮询扫描所有图。

Web 入口包含新建任务开关、任务输出页共用预览组件及独立工作台（treemap-view.js/treemap.js/treemap.css）；适用于五流程最终 mapping 结果及兼容 AIRR 目录，不限制为 Pig。默认不加载图片；选择样本或链/合图类型后直接展示，两项可组合筛选，无需确认。共用组件按筛选结果分页，每页最多 7 张，仅为当前页创建 img，采用懒加载；“查看全部”清除筛选并仅展示第一页。轮询保留筛选、页码与未变化的图片节点，所选样本/链暂时缺图时保留条件，不能悄悄切回全部；切换生成记录重置视图。新输出与 ZIP 使用 `individual_treemaps/<安全样本标识>/`、`multi_chain_treemaps/<安全样本标识>/`；历史文件不迁移，ZIP 按 manifest 的 kind/sample_key 整理路径。支持全部结果/当前样本流式 ZIP，权限、版本及路径检查必须全部通过，不静默遗漏文件；全量含根目录统计 CSV，样本 ZIP 仅含该样本图片。旧任务未设置不会自动补生成。关联任务图随任务归档，纯目录绘图不自动进入旧归档脚本。Docker 白名单打包 analysis/treemap 与 postanalysis.py，环境显式依赖 Pillow。聚合/像素保真测试、五流程来源测试和 API/生命周期测试位于 analysis/treemap/tests/test_core.py、tests/test_sources.py、web/tests/test_treemap.py。

任务详情的树图组件采用 `mount(..., {embedded:true, metaTarget, actionsTarget})`：共用筛选与分页图库，名称不在正文重复显示，状态/处理数量/创建时间与取消或重试按钮合并到任务树图标题栏。只有生成中显示进度条，完成后数量显示在状态旁；计数口径折叠为“计数说明”。当前已有生成记录时不再显示无效的“生成树图”按钮，失败/空结果等通过该记录的重试操作处理；多条记录才显示折叠的“其他生成记录”，入口链接到工作台。轮询保留折叠状态、筛选与图片节点。

Treemap 新输出统一为关联任务的 `<任务输出>/treemap/YYYYMMDD_HHMMSS/` 或纯目录模式的 `<默认输出根>/<用户>/treemap/YYYYMMDD_HHMMSS/`。自动与手动生成共用 `allocate_output()`，在 SQLite 写事务内检查 indexed output_root 与磁盘占用，同秒冲突增加 `_02`、`_03`；时间采用北京时间。记录 ID/attempt 仍在数据库，不能以新约定推算历史记录路径，也不能自动迁移旧目录。下载与重试继续使用记录中的 output_root。

PNG 命名统一由 `image_relative()` 负责：单链为 `样本名__CHAIN.png`，多链合图为 `样本名.png`；安全样本子目录继续隔离不同来源的同名样本，文件名不附加链合并后缀。新生成文件和 ZIP 内文件名使用同一规则，历史磁盘文件不迁移，仍按 manifest 原路径读取；历史 ZIP 也按新规则命名。

## Mapping 当前状态

`mapping/fasta.py` 递归读取 `<sample>__<chain>.csv`，要求 `CDR3(pep)`、`copy`、`joinedSeq`，生成 `>零基行号_CDR3_copy` 的 FASTA，保留相对子目录及 copy 文本。支持 CLI，独立转换默认仅 IGH；`copy` 不展开或加权序列。目录中有 `AL_s003_v8__IGH.csv` 示例。

`mapping/umi_count.ipynb` 的整理逻辑已提取到 `mapping/pipeline/umi_count.py`，从 AIRR 的 `sequence_id` 末尾还原 copy 为 `umi_count`，没有该列时兼容历史 `barcode` 列。不做 UMI 聚类或重新计数；原 notebook 保留。

第五条 Web 流程 `mapping` 已注册，入口为 `mapping/pipeline/run_mapping_pipeline.sh`，三阶段由 `run_mapping.py` 执行。仅 human：扫描目录后按链多选，只处理所选链；不要求 Submission/Barcode/Match 审核。阶段为 `01.fasta`、`02.igblast`、`03.umi_count`，各阶段成功后写 DONE，全部成功才写 `.pipeline.DONE`。02 和 03 各输出筛选后的 `<样本>__<链>.tsv`，03 添加 umi_count；不输出 raw/filtered 两份文件，逐文件续跑。数据库默认沿用现有 `/data/scAnalyis/Scigblast/igblast` 挂载，通过模块配置或环境变量覆盖。命令见 `mapping/README.md`；实施设计见 `plans/20260914_mapping_module_plan.md`。不要直接调用依赖 FASTQ manifest 的 Base stage5。

`mapping/pipeline/resources.py` 使用主机可用内存、cgroup v1/v2 上限及使用量、CPU 配额/亲和性计算实际线程和 worker 数，保留 5% 内存余量（至少 256 MiB）。单 worker 内存配置是排程估计，不是硬性 RSS 限制；余量后不足一个 worker 时明确报错。

## 开发与验证

2026-10-22 Web 优化：目录、reMapping 和独立 Treemap 重扫使用候选结果，失败保留已确认草稿，但禁止使用失败/旧来源提交。目录按稳定 ID、读端路径与 versions 恢复编辑；reMapping/Treemap 扫描返回不透明 source_version，仅为恢复选择使用，不作为提交授权。变化条目需重新选择；首次扫描默认全选、重新 Match 新增项默认不选保持不变。分页 GET 仅对网络错误与 502/503/504 最多重试两次，不重试扫描 POST 或权限/过期错误。任务列表按 job.id/单元格更新，保留链接、按钮及 summary 焦点；Treemap 详情最多一个请求在途、一个最新待处理目标，不使用递归定时器。

`web/input_manifest.py` 的私有 LRU 上限为 8 个快照、20,000 行、估算 32 MiB，对外仅返回页/清单副本；每次命中仍核验文件身份及 owner/pipeline/path/expires。`web/mapping_results.py` 按完整 key 共用在途构建，CSV 解析在全局 LRU 锁外，失败释放等待者；文件可用性仍实时检查。任务分页以 created_at/id 双降序稳定排序，owner/created_at/id 组合索引不改变统计权限范围。

Treemap 新运行使用 `web/derived_store.py` 的 storage_version=2：derived_runs 只存小元信息与进度，derived_items/artifacts/samples 分项存储。每波最多读取 32 个 queued 候选（实际 worker 上限不变），仅写变化项和新产物；合图按一个样本读取，详情/最终表才恢复全量清单。无版本/版本 1 沿用整份 JSON，不自动迁移或补算。创建/发现、批次使用/发布、合图图片和最终发布继续核验来源/版本；保存事务重新核验取消与 parent attempt，缺失/变更单链图在重试时重做。删除任务须同事务删除三个子表；不得把 `_partial`、source 或 clone_table 暴露给 API。新模块必须同步 Docker COPY、白名单和 check_bundle。已经写入 v2 的数据库不能只回退旧代码；停服备份后须做受控 v1 兼容导出或恢复对应旧数据库，优先向前修复。

合成状态基准：`rtk proxy python web/tools/benchmark_web_state.py --archive-files 10000`，只使用临时目录和内存 SQLite，不运行分析。分别报告快照读取、整份 JSON 序列化投影、增量序列化实际采样、查询计划及归档 Python 分配峰值；不把这些数值当作真实生物分析耗时或进程 RSS。归档保留原压缩/完整性检查流程。

Base/Pig 已支持 `sample_source=directory`，`pipeline_input_manifest.py` 扫描并验证明确配对，`web/input_manifest.py` 保存用户绑定快照和不可变清单；API 为 `/api/input/scan`、分页扫描视图、任务 `/input-assignments` 与 `/input-rematch`。目录模式绕过外部 XLSX，仍生成原 Match CSV、进入人工审核；严禁将 requires_submission=false 分支当通用模式，该分支仍为 reMapping 专用。runner 接收 `SCIGBLAST_RUN_INPUT_MANIFEST/HASH`，检查失败必须停止，不能复用旧成功 Match。Base/Pig 阶段指纹包含清单与共享模块。IR/10X 尚未开放目录模式；文件/样本身份不得通过目录名或 Chain 猜测 Barcode。UI 状态组件在 `web/static/directory-input.js`。验证用 `web/tests/test_directory_input.py`。

新建 Pig 目录任务清单记录 `output_layout=sample-v1`，各阶段用 `pipeline_input_manifest.pig_output_relative()` 生成单层样本目录，最终为 `05.igblastn_out/<dataset>/<sample>/TCR.tsv` 或 BCR.tsv。旧清单无该字段、Submission 模式沿用原 `raw_relative_parent/pair/sample` 路径；目录重新 Match 保留任务原布局版本。不能只修改最终阶段，否则前序输入和缓存会失配。Shell 阶段在构建任务列表时解析路径一次，避免每个 worker 重读整份清单；Pig 阶段指纹始终包含共享模块。

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
