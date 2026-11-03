# Repository Guidelines

2026-11-03 7fc2a61e资源补充：11:12 UTC左右确认宿主available约492611MiB后，将正在运行的隔离报告容器总内存上限从2GiB调到8GiB（memory-swap=8GiB，禁用该容器swap），为文件缓存留空间；启动器进程树RSS监督仍为2GiB，CPU4/计算1/绘图最多2不变。收据在工作目录resource_change.json。不能将该轮耗时当作与2GiB旧轮固定资源的提速比较。

2026-11-03 最新报告运行状态：5d48c90a统计已完成，但在存疑组绘图时失败（scenario_analysis误把无group_id的全局空Isotype图当单组图），原失败清理逻辑已删除未发布staging，未更新本地统计。已为figure_jobs.render_usage增加skip_isotypes参数，仅存疑组调用设True，普通Isotype不变；单独存疑组11图及完整两样本59图/Excel/HTML验证通过。当前重试run_id=7fc2a61e，工作目录为igblast_base/analysis/.report_work/7fc2a61e，容器scigblast-report-7fc2a61e；独占锁、1012:1014、4CPU/2GiB、计算1/绘图最多2保持不变，采用当前源码。自定义启动器绘图前保存私有statistics_checkpoint（独立CoW SQLite副本+summary/manifest），若展示失败可继续复用，不冒充已完成公共报告。outputs/a93f6d1c仍是旧统计，必须等待7fc2a61e成功校验再替换。上一轮隔离验证目录.codex_report_perf_20261103_7e08已清理约2.6GB；验证收据保存在本地系统Temp。

2026-11-03 本轮报告优化进行中：HTML v32新增总体概览默认可见的“单样本 Isotype”，仅列适用IGH样本，独立按钮切换，不改变全样本概览；标题旁有定位入口。单选时去掉重复主类图，最多7活动图继续有效；切样本保持卡片阅读位置（实测scrollY=1733前后一致）。不受影响的图表节点/实例复用，参考搜索180ms防抖，公共表按样本/引物惰性建索引。outputs/a93f6d1c/report.html已刷新布局，但最新服务器数据重算仍进行中，完成前不能宣称已换成新版统计。

2026-11-03 最新八样本报告正在独立容器重算：run_id=5d48c90a，来源igblast_base/20261102_042057和igblast_base/pig/20261102_203159，八项05/06及trace已验证；映射/trace文件版本相较2b0d74ec旧清单已变化。工作目录…/igblast_base/analysis/.report_work/5d48c90a（status/progress/performance/run.log/request/source快照），目标…/analysis/reports/5d48c90a。容器scigblast-report-5d48c90a，1012:1014、4CPU、2GiB、共享平台门锁独占；计算1worker、绘图最多2worker，不重跑IgBLAST。不得删除该工作目录或冻结source。结束后必须核验status与输出、同步本地a93f6d1c并更新本条。

2026-11-03 性能实现：performance.py记录私有阶段wall/协调进程CPU，Web另记进程树RSS；usage/primer/copy关联用批量/集合SQL，低匹配组保存紧凑命中证据供scenario复用，不重复存原序列；final_lineage仅预留已知存疑样本（缺缓存时回退真实AIRR读取）。compute_jobs支持按完整样本并发归属统计、私有分片确定性合并；SCIGBLAST_REPORT_COMPUTE_WORKERS默认1、最多2，STATISTICS_CACHE_MB默认0，明确开启后有界缓存归属统计。后续全局CDR3/基因汇总仍由一个写者统一计算，不将distinct数相加。HTML旧报告衍生缓存按owner/run、材料/前端指纹隔离、原子发布/校验/限额；report_read_leases在SQLite事务中保护导出与归档/删除，正常/异常释放、90s过期并续租。usage API用256项版本化私有LRU，原payload不变。全局2pipeline/各4链/报告互斥不改。尚未推送部署；实施及验证细节见plans/active/20261103_report_performance.md。

2026-11-03 HTML v31交互优化完成：样本范围明确为全部/单选/多选；逐引物组成仍单选。选择器DOM保留，连续勾选保留焦点/列表滚动，搜索结果批量选择不清除已有选择，单选长列表搜索可用；样本/引物查询随模块保存。集中模块默认值，“重置本模块”仅清当前模块的筛选/搜索/图例/缩放，基因匹配恢复sample和10条；主题与模块往返保留色条/zoom。顶部导航紧凑吸顶、手机单行横滑，主/子模块记忆、上下图表分页/返回筛选、键盘标签完善。手机样本信息首次收起，重复图题与边框减弱；宽图实际溢出才提示滑动。图卡放大移动原节点到dialog，关闭恢复位置和焦点，实例不增加；PNG/CSV显示生成、下载发起、失败重试和空数据反馈。预览outputs/a93f6d1c/report.html，20份公共表与v30完全一致，HTML增约1.01%；browser_validation.json覆盖8真实样本、13/50列表及15引物fixture、模块重置、页面/色条记忆、放大、导出失败/空CSV、900/390/320px、断网零请求、键盘。fixture仅在系统Temp并已清理，未新增项目测试代码。未推送部署或同步服务器。

2026-11-03 HTML v30科研使用优化：导出PNG包含任务/样本/链/引物/指标/存疑组/页码，文件名随筛选及比较集合变化；当前图可离线导出CSV，保留原分子分母并尊重图例/缩放，文本防公式注入。样本产出保留默认三柱，新增单指标切换；样本基因新增同链对齐，逐区核验参考SHA和scope，统一已观察基因并集、排序、分页与坐标，未知不填0。增加安全的公开参考指纹/composition_weight；Isotype按指标修正unit为copy/reads/distinct_cdr3，历史HTML和缓存报告导出也适配。新本地预览outputs/72c4d9a1/report.html源自2b0d74ec原公共包，未重跑pipeline；20份内嵌表的计数/分母/百分比逐项一致，HTML仅增约1.04%。browser_validation.json/edge_validation.json/data_validation.json验证断网导出、存疑组、模块恢复、390px夜间、参考冲突、真实0与缺失；未推送部署或同步服务器。临时验证脚本位于系统Temp，不新增项目测试代码。

2026-11-03 HTML v29 修复：总览/数据变化按singleSample选择互斥展示单样本或汇总图，修复v28单样本保留聚合图导致重复的问题。逐引物基因组成限定单样本：隐藏单/多选切换，移除全部样本选项，恢复旧多选状态时规范为第一个有效样本。其他模块多选与独立筛选保留。八样本report.html及ZIP已同步服务器/本地，CSV/PNG/Excel/双SQLite不变；v29_browser_validation.json覆盖四IGH、TRA、全选、多选单元素、模块恢复与390px夜间。ZIP391文件，SHA256=608686359bead0308b16e9fa47e027e44c304ff3f798bae9b7d30a813ff46def；ui_v29_validation.json记录旧产物校验；源码未推送部署。

2026-11-03 已完成 HTML v28：总览默认全部样本，单选/多选实际过滤汇总图；各主模块及二级分类独立保存样本/链/引物/阶段/存疑组/分页。样本产出添加全8样本整合图；家族使用独立边框、间距和表头块，默认10基因分页。N1的IgH与IgH_O1存疑组独立统计Raw/Mapping匹配、Mapping rate、V/J/C Expression与Unique CDR3、C组成和基因匹配；每组精确关联771112 copy，其余70403不猜测分配。原details.sqlite和原CSV/PNG均不变；新增scenario_details.sqlite为私有追溯，下载包排除；新增71PNG及4份CSV/Excel sheet。真实报告outputs/2b0d74ec及服务器同ID目录已更新；v28_browser_validation.json验证多选数据范围、模块恢复、候选图、PNG导出、390px/夜间，scenario_validation.json验证原统计不变。ZIP390文件，SHA256=4c45a4eb0daea7412c594120775ddf62cb2694cddecba2312d6b3ad45c1ddb08。服务器冻结源码已归档至.report_work/2b0d74ec，隔离目录已清理；源码尚未推送部署。计划：plans/archive/reports/20261103_outputs_suspected_groups_and_family_ui.md。

2026-11-03 HTML v27-module-filters：样本/链选项按当前模块的实际公共汇总数据限定，选项顺序仍按类别；Bcell仅4个IGH、单链下拉隐藏，切换模块清理失效选择并恢复适用范围，部分范围用“全部适用样本”。有效0计数/参考零观察不按非零率剔除，缺旧表时用图元数据限定能力。moduleContexts缓存轻量group上下文；交互比较图在选链时继承该链到数据scope，修复仅筛选控件变化而全链比较仍展示的问题，静态无chain聚合PNG不假装可筛。参考基因空区改居中留白空状态，区分无C参考与筛选无结果，空卡无表头/0计数分页，有搜索/状态限制时可清除基因筛选。真实8样本与合成缺C界面验证通过：Bcell自动从TRA切到H、四IGH选项；多样性恢复8样本5链，TRA仅A；清除后C基因恢复、390px/夜间无页面溢出。证据outputs/2b0d74ec/module_filters_browser_validation.json（缺C为验证fixture，不代表真实参考库缺C）及module_filters_validation.json。服务器/本地HTML及ZIP同步，数值公共JSON原样、PNG/Excel/CSV/私有索引均未改。ZIP314文件、SHA256=936a5f39aa6dc338ad270d130f44aabdfb4dd1a23915cd2347fd5ab791d7a95f覆盖下段旧指纹。work/filters_before_v27.tar.gz备份、filters_source_v27.tar.gz含更新源码，临时服务端目录已清理；源码未推送部署。

2026-11-03 Isotype模块归类：按用户要求将4个IGH样本的isotype_distribution从样本分析/B cell移至总体概览，紧随主类Isotype；选择单样本时展示两种指标左右图，全部样本时仍只展示主类比较、不展开4份个体图。B cell页及其统计口径仅保留Mean SHM和共享CDR3。FIGURE_VIEWS映射与viewer metricFigure、overview/bcell规则中英文同步，HTML v26-overview-isotype。八样本报告服务器及outputs/2b0d74ec已更新，四个IGH单样本总览各6活动图、Bcell无重复、全部样本/TRA不展示个体isotype、390px与PNG下载通过。isotype_location_validation.json/isotype_browser_validation.json留证；16个数值表SHA仍758de004cf52dc17e43ea45014c1e67cb76d1401da710c99a89b9112ca60b42f，PNG/CSV/Excel/私有SQLite不变，文件物理目录未迁移。ZIP313文件，SHA256=35171b6e6dca8ff2e7dd36e775bfbf8c5385c66ea29ba7f59e9f2bc486913d4b覆盖下段旧指纹。work/isotype_before_v26.tar.gz备份旧HTML元数据，isotype_source_v26.tar.gz保存本次规则/viewer源码；临时目录已清理，代码未推送部署。

2026-11-03 统计口径文案约定：只介绍指标含义、公式、权重与分母，不写旧报告迁移、绘图方向/布局、导出来源、重算过程等实现叙述。report_rules中英文同步；历史/分类分母仍按真实metadata分支，不只换成新分母。多样性页只显示7项多样性定义，B cell页只显示Mean SHM/Isotype Distribution/共享CDR3三项；保留metrics合并键供旧viewer兼容。HTML v25-metric-definitions已同步服务器与本地outputs/2b0d74ec。仅更新规则、viewer选取及HTML元数据，16个公共数值表SHA256=758de004cf52dc17e43ea45014c1e67cb76d1401da710c99a89b9112ca60b42f；全部PNG/Excel/CSV/私有SQLite未变。rules_validation.json、rules_browser_validation.json验证通过。ZIP312文件、SHA256=e6b353d6647b1bd0e9f72117c8d86373d4f24ad32c908045f9b31023bc1ef57a覆盖下段旧ZIP指纹。原文案备份work/rules_before_v25.tar.gz，规则与viewer源码work/rules_source_v25.tar.gz；临时源码已清理，未推送部署。

2026-11-03 八样本报告版式已更新：HTML v24-grouped-samples、图v39-grouped-matching、Workbook v24。P1引物匹配图改两层x轴：上层引物组（存疑红框/红星），下层同一样本只显示一次；Raw/Mapping继续左右布局，窄屏仅图内横向滚动，PNG导出含完整图与星号说明。正式18张matching PNG也改同样分组标识，其他260PNG和全部数值/CSV/私有SQLite保持；布局数值指纹7389bd2fc38effe441d3052d3c69a34736f52523a748dc04601b6d465acdd75b。样本信息/选择顺序按Heavy(IGH)→Light(IGK/IGL)→TCR(TRA/TRB/…)和类别内自然序，HTML分组边界、Excel总览同步；8样本表可完整显示，超长列表继续限高滚动。outputs/2b0d74ec及服务器同一输出目录已更新HTML/XLSX/ZIP，ZIP311文件/278PNG，SHA256=429d43d1ec19626f96fda26eb6d4db4826e1964828ea2fdde220f0c3241b41e7，覆盖下段旧ZIP指纹。layout_validation.json证明统计与索引未变，layout_browser_validation.json验证两层标签不重叠/姓名未截断/8样本顺序/390px无页面溢出/PNG下载。原版式备份位于work/layout_before_20261103*.tar.gz；修改源码已归档work/layout_source_20261103.tar.gz，隔离临时目录已清理。代码尚未推送部署，服务器只是生成产物已刷新，无需重新跑数据。

2026-11-03 八样本合并报告已完成：任务名ZNDX_YL，run_id=2b0d74ec，显示编号f0ebc9d4。来源20261102_042057的H/N1/N2/O1=IGH，以及pig/20261102_203159的A=TRA/B=TRB/K=IGK/L=IGL；逐leafroot discover后合并items，保留trace，以igblast_base为共同input_root。八项trace/已发布06均验证通过，使用原上传表SHA核验与原引物分组（A=Pig_TRAC_1，N1不换组，低于50%另组测试标存疑）。容器1012:1014运行完整最新统计v27/图v38/HTMLv23，不重跑IgBLAST，独占公共资源锁、整组RSS限制2GiB、PNG最多2worker，实际耗时约42分38秒，峰值1014.1MiB，退出0。输出 /colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/analysis/reports/2b0d74ec（独立CLI报告，未注册Web任务），work=analysis/.report_work/2b0d74ec保留request/status/progress/run.log。包含278PNG、56多样性、29Bcell行（4IGH）、双阶段primer及其他组复核、V/J/C双指标、参考库匹配、数据变化、Excel/HTML。8样本copy与06一致；204个有数据gene百分比范围守恒；浏览器8样本切换/IGH适用范围/PNG下载/10基因分页/390px/顶部Tabs/断网零请求通过；本地 outputs/2b0d74ec 已同步HTML、Excel、完整包和验证截图。ZIP310文件/278PNG，27.04MiB，SHA256=7189c76846a8e6a27a2c62d2f5bdc1e69e7b29c85d6f9a63a5ac6992af4038c9，不含私有SQLite。SQLite保留服务器追溯；冻结源码已归档于work/source_snapshot.tar.gz，隔离临时目录已清理。未推送/部署。

2026-11-03 B cell缺失修复：旧H/N1报告two_samples_f1f0923e未生成diversity_summary/bcell_metrics，不是IgBLAST缺数据。已只读校验并使用原任务已发布06补算，输出独立版本analysis/reports/bcell_70c8a09f，原gene/primer统计和pipeline结果不改；本地real_report.html已刷新，新增14条B cell、14条多样性、32条isotype数据和9张PNG。样本分析新增独立二级Tab“多样性指标”“B cell 指标”，历史缺数据明确提示、非IGH显示适用范围；主Tab继续顶部横排。统计v27/绘图v38/HTMLv23：共享CDR3统一排除aa_valid=0，按CDR3分组presence替代慢相关子查询，IgD/M先合并再判定与其他isotype共享。真实06计数验证H=605030 copy、N1=841515 copy；两样本5537条含非标准AA的记录仍保留copy/SHM统计，仅不参与有效CDR3去重/共享。50%已知fixture与真实浏览器切样本/两样本对比/PNG/390px/零网络通过，证据outputs/interactive_report_20261102/real_bcell_validation.json及bcell_browser_validation.json。派生HTML是独立补充预览，未注册Web报告任务；代码尚未推送/部署。

2026-11-02 用户最新布局约定：报告主Tab必须在顶部横向排列，禁止左侧导航。HTML v22-top-tabs已修改生成模板及共享CSS，四份现有交互预览同路径刷新；二级Tab、统计值和交互逻辑不变。未推送/部署。

2026-11-02 报告统一实现：平台report_panel与/reports改为报告管理，删除reports-workspace.js及重复图表/统计筛选；保留任务运行统计、treemap、上传配置、状态/历史、重试/取消及独立追溯导出。HTML v21使用analysis/repertoire/assets共享CSS/JS＋固定ECharts6.0.0（按需构建604421字节），chart_data公共白名单、分表延迟解析，支持图例键盘/鼠标显隐、色条范围、双指标同序/配色、当前视图白底PNG导出和日夜主题；原始PNG独立保留，历史缺数据的图回退静态。新inline入口/api/reports/{id}/report.html?download=false，默认仍attachment下载，两条HTML路径均sandbox/CSP/owner/版本核验，不使用iframe。未生成PNG的引物也可从已有汇总生成交互基因图，不将缺图视为无数据。

FIGURE_VERSION v37新增figure_jobs：spawn按样本/参考组生成V/J/C及引物C图，auto待绘图组<4串行，其余按2GiB总预算/每worker640MiB估计最多2个；概览/Isotype/指标仍协调进程绘制。父进程唯一写SQLite/Excel/manifest、稳定汇集、全部成功原子发布，失败终止本executor子进程；同源重绘验证私有图组缓存PNG签名/SHA后复用。全局2pipeline/每pipeline4链和报告独占资源锁不变。Docker白名单与check_bundle已包含viewer资源。验证：真实373个gene点计数/分母一致、N1 Raw/Mapping替代组数值一致；离线零网络、PNG下载、<=7活动实例、390px、管理壳/追溯/历史/列延迟选择、inline/CSP/越权拒绝、worker失败清理通过；8组合成121PNG串行16.26s/双进程12.88s且字节一致，2组小任务无并行收益，缓存2组命中。真实旧HTML11.017MB→新版约0.846MB；32样本合成HTML2.14MB，Windows Chromium切换P95约28.5ms。产物outputs/interactive_report_20261102。代码尚未推送/部署，服务器原结果未改。

2026-11-02 完成状态（覆盖下段“运行中”）：ZNDX_YL剩余A/TRA、B/TRB、K/IGK、L/IGL已全部成功到06.preprocessing，退出0、06 DONE和--check通过，Datapoint四行；未生成分析汇总报告或treemap。输出 /colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/pig/20261102_203159，验证见.manual_run/validation.json。最终保留率A=89.36%、B=89.39%、K=23.74%、L=0.34%；本批off-locus/unknown排除均0，低保留发生于productive筛选，不据此推断数据库或引物缺陷。实际运行源码及优化验证源码已分别归档.manual_run/runtime_source_snapshot.tar.gz、optimized_source_snapshot.tar.gz，隔离目录及临时fixture已清除，生产服务未部署变更。

2026-11-02 新增Base按样本05→06衔接：同一样本全部配对/所选链任务OK后，单个有界后处理worker先生成该样本检查点和junction；其他样本IgBLAST继续。最终06复用有效检查点，一次性发布Datapoint/processing_manifest/run.json/完成回执，不公布部分全局汇总。SCIGBLAST_BASE_PIPELINED_PREPROCESSING默认1；在原最多4个链任务基础上预留后处理内存和进程预算，内存/线程不足自动串行。chains布局检查点新增junction文件版本（含预期不存在状态）验证，丢失/变化重算。Linux容器真实数据小样验证多链就绪边界、单worker、失败样本不提前处理、全局表不提前发布、最终两样本checkpoint复用、缺junction失效，以及CSV/junction逐字节一致；证据.manual_run/overlap_validation.json。打包五流程及内存/线程回退验证通过。此优化尚未推送/部署，本批已完成的实际任务仍采用冻结的原串行调度。实现边界见plans/archive/pipeline/20261102_base_sample_preprocessing_overlap.md。

2026-11-02 当前运行（不要在结束前删除隔离源码）：用户要求对ZNDX_YL剩余K-K93-B/IGK、L-K93-B/IGL、A-K266-B/TRA、B-K266-B/TRB按最新脚本跑到06.preprocessing，不生成报告/树图。已在在线容器中以1012:1014启动独立官方runner，共用全局2槽/阶段资源锁、最多4个IgBLAST任务；不是Web队列任务。输出 /colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/pig/20261102_203159，dataset=ZNDX_YL_remaining4_20261102；.manual_run/status.json记录进程/退出，launcher.log与logs/<dataset>/pipeline.log记录过程。01–04完成，05运行中（后续必须实际核对06 DONE/--check后才能称完成）。源码 /colddata/SCigblast/platform/SCigblastWeb/.codex_remaining4_20261102 保留至任务结束；源包LF规范化，仅并发设置降到4样本/128线程，分析阈值未变。隔离包最初漏mapping/pipeline/resources.py导致资源探测回退独占锁，已补该依赖；后续阶段正常探测，未改变分析内容。两份上传表与用户原文件SHA一致：Submission 575eb29cab7ab86b9b91636765da86183ac9e4123b6c7e1c2fee3f1407f68c57，primer bc41ecdb95921e8bc9cb64406ec98801543f015f2b699a7c77b8a9b96720bfaa。继续用B样本/I组，primer B组/C:D及E:F；原组归属后剪切，未匹配/不明确保留原序列。

2026-11-02 最新功能：HTML v18的基因匹配每栏默认10个，可输入1–100整数，改数量回第一页；图页仍最多7张、成对不拆。匹配状态只展示已匹配/未匹配，definite_only作用于Web total/分页/当前CSV；历史不明确注释仍留底层，不重标。报告头部只显示稳定8位随机report_display_id，不展示real_two_samples前缀；技术run_id/目录不改。新IgBLAST base默认目录加物种层 …/igblast_base/<species>/<时间戳>，以已校验species构造，显式output_root和历史任务路径不改；其他pipeline布局不变。

2026-11-02 双阶段其他引物组对照：统计v23/图v33/Workbook v21。alternatives.compare统计Mapping，compare_raw按Raw阶段独立低于阈值的样本流式重读R1，不在SQLite增加原始序列副本；共享来源跨链去重，前后版本核验，缺证据标不可用，不借用Mapping比例。stage写入reviews/comparisons/CSV/Excel及Web对照表，PNG原组旁分别展示本阶段Suspected结果，不改原归属。真实H/N1旧结果已补Raw，N1 IgH/IgH_O1 Raw=894306/1011739=88.3929551%，Mapping=828821/906485=91.4324010%；原组仍IgH_N1。真实 report.html/overview/report.xlsx已同步本地 outputs/real_two_samples_20261102，report_display_id=06485245。此前基因/Isotype/阶段CSV/PNG和私有索引不变，两个模拟报告也同步。服务代码未推送部署；真实派生报告已在服务器更新。独立真实报告验证源码在 .codex_raw_group_review_20261102，结束后应归档并清理。已有合成验证和真实浏览器检查通过分母/IUPAC/缺证据/共享链去重、10/3/25分页、两状态/家族、短编号、双阶段图、断网/390px及5流程bundle。

2026-11-02 状态与页脚：HTML v16的基因匹配状态统一为文字+符号+浅色徽标，已匹配绿色✓、未匹配浅红×、未唯一归属赭色!，未知灰色?；字体加深，Web同步日/夜配色，不只用小圆点区分。用户要求删除最底部SCIGBLAST/HTML版本/统计版本/离线报告整行，正式stream和三份已有本地HTML均移除footer；内部版本记录保留。PNG、统计JSON内嵌数据、CSV、Excel和原始注释不变。中文控件/英文图标题、家族分组、25基因分页保持；未推送部署。

2026-11-02 最新语言约定（覆盖下文HTML全英文）：用户明确“可操作title统一中文，只是图title英文”。HTML v15恢复中文导航、模块标题、操作按钮、筛选、匹配状态、统计口径和提示；图内标题、图表caption以及% Expression等专业指标保留英文，样本/引物/基因原名不翻译。`report_html`调用`for_report(language='zh')`，不改PNG/计数/分母。真实H/N1和两份模拟HTML已同路径刷新，所有73/71/32张内嵌PNG及统计行逐项不变。家族分组/25基因分页与Mapping效率无引物筛选约定继续适用；本地更新未推送部署。

2026-11-02 最新报告/UI：HTML v14统一英文，包括导航、标题、状态、筛选、编号统计口径；平台操作界面保留中文，真实样本/引物/基因名称不翻译。`report_rules.for_report(language='en')`与中文Web共用定义，历史分母继续正确标明。Mapping效率只按样本/链展示全部引物，不提供引物搜索/下拉，也不显示当前单个引物KPI；隐藏的旧引物选择不影响scope，逐引物基因组成仍可选引物。基因匹配改为V/J/C三栏紧凑表、每栏独立25个基因分页，并在栏内按名称家族分组（最新用户指的是IGHG4/IGHG5归IGHG，不只是按V/J/C）。IGHG与IGHM/IGHD分开；V例IGHV1-4与IGHV1S2归IGHV1，TRBJ1-1/1-2归TRBJ1。未知accession不猜家族。组头不占25个基因名额，跨页重复组头；按家族/基因自然序在SQL分页前排序，避免同一家族被夹散。`usage.gene_family`与Web/HTML同规则；此分组只影响展示，不合并统计计数。API新增gene_query字面子串搜索（不是SQL通配），total、分页、CSV保持相同筛选，支持已匹配/未匹配/未唯一归属。原始计数及PNG/CSV/XLSX未变，真实H/N1与两份合成HTML已更新本地；未推送部署，服务器旧HTML未在本次改写。已验证150基因分页/搜索/状态/CSV/特殊字符、家族分组、Mapping跨页切换、全英文各模块、独立PNG文件名、390px及断网零请求。

2026-11-02 最新组成分母（覆盖下文全部 mapped reads 及含缺失类别的旧约定）：统计 v22 / 图 v32 / HTML v13 / Workbook v20。样本和每条引物的 V/J/C `% Expression` 分别以该区可唯一归属到基因的 reads 为分母；`% Unique CDR3` 仅累加这些明确基因内的有效 CDR3aa 去重计数，同一 AA 跨基因分别计入。不含无注释和未唯一归属类别；无可分类数据为 N/A，不伪造0或100。Isotype Expression 使用可分类 C 注释的 reads，主类去重仍重新计算；基因匹配清单和原始计数不变。引物匹配率、Mapping rate、阶段 counts 不随组成分母变更。`usage.rules.gene_expression_denominator=classified_gene_reads`、`isotype_expression_denominator=classified_c_reads` 标识新口径；未重算旧报告继续说明旧口径，不只替换文案。API/CSV/PNG/Excel/HTML一致，分母不随分页变小。`gene_cdr3.rebase_classified` 可对已有私有汇总重算，不改逐read归属或AIRR；正式新分析由build/enrich生成。H/N1真实报告及两份模拟报告已重算；真实78个范围中52个有可分类数据，均合计100%，原计数/状态和无关导出不变。已验证新建及已有索引、缺注释/多call/跨基因同AA、72范围API JSON/CSV及分页、断网HTML/390px。真实报告路径 outputs/real_two_samples_20261102/report.html；代码未推送部署。

2026-11-02 真实报告验证：取服务器 igblast_base/20261102_042057 的 H-K93-B、N1-K270-med-B（IGH），任务名 ZNDX_YL_IGH_test_20261102；在独立代码目录以1012:1014生成派生汇总，未重跑IgBLAST或改原TSV。用户给定Submission B为样本/I为组，primer B为组/C:D和E:F为名称序列，oligo结构10N+8bp；N1不自动换组。原始R1/R2及Fastp后两端分别从fastp JSON独立读取，图中Raw、Clean(Fastp)各为同一阶段下R1/R2两列，共用阶段配色；不能total/2、total减另端或用Header造Fastp计数。trace全归档优先，其次JSON；冲突/缺少为N/A，实际0保留，跨链共享配对不重复。报告左上角显示真实任务名，Web入队和runner传task_name，HTML转义。读端、Merged、AIRR后处理指标保持不同统计单位。真实输出及验证证据保存在服务器 analysis/reports/two_samples_f1f0923e 和对应_work目录；本地report.html可离线查看，包含73张PNG。

2026-11-02 文案更新：Total Receptor RNA 说明统一为“AIRR 后处理后 umi_counts 的总和（copy 按 umi_counts 记录）”。仅修改说明，不改标题、计数或计算。report_rules.py为共同来源；Web outputs优先用当前说明，HTML v8确保历史下载重新导出时应用新文案。两份模拟HTML同路径已刷新，PNG/CSV/SQLite/XLSX未改；尚未推送部署。

2026-11-02 最新基因匹配页（覆盖“仅未匹配”约定）：入口及标题统一“基因匹配”，同时展示本次数据库内已匹配和未匹配的V/J/C基因。绿色+已匹配、灰色+未匹配；历史命中未唯一归属以独立状态显示，不强行当未匹配/确定匹配。Web /usage新增reference_only=true，total、分页、rows和CSV都加reference_catalog=1，排除库外注释；不再仅请求zero_observed。HTML v7使用sample的reference_gene_observation全清单和新增primer_reference_gene_observation（03_primer_genes同名CSV），按sample/chain/group_id分组，不混不同数据批次；旧primer清单缺失时用可用matched/unmatched材料并提示需补全。图版本v28触发报告更新，统计v19不变。没有恢复底部统计表或候选分析。两样本模拟report同路径已更新，原CSV/SQLite/PNG/XLSX逐字节未改，仅新增引物完整数据库状态CSV和更新HTML/元数据。已验证数据库外排除、API total/分页/CSV、owner、sample/primer/segment筛选、Web及断网HTML两种状态/颜色/文字、390px。尚未推送部署。

2026-11-02 最新简化（覆盖下文双候选和统计明细约定）：用户暂时取消候选基因分析。五流程 V/D/J/C 的 num_alignments_* 已统一1，由IgBLAST选择最高分命中；Base/Pig共享dependency reported_alignment_limit=1，脚本指纹使旧2命中缓存失效。IR/10X脚本及reMapping源码元数据缓存同样失效。新report统计v19/图v27/Workbook v17/HTML v6不再生成候选基因CSV/PNG/Sheet或入口；usage不再采集候选组合，历史原生多call不强取首项，仍按无法唯一归属处理，不改旧AIRR。Raw R1显示名统一Raw，raw_R1技术key与原始R1计数不变；Mapping效率仅Mapping reads阶段，Raw的匹配率仍保留。所有模块的底部统计明细已移除，Excel/CSV仍核查。HTML参考页改名“未匹配基因”，标题“数据库中未匹配到的基因”，按样本/链/引物展示V/J/C名称清单，不是统计表。report_rules.py按总览、引物匹配、Mapping效率、基因组成、数据产出、数据变化分别提供编号口径说明，Web及HTML均在图表上方；Mapping reads定义为IgBLAST output有效V且locus与所选链一致，基因Expression和Unique CDR3分母仍按现有实现，不改计数。两样本模拟报告同路径已更新为71张PNG；旧CSV/index数值逐字节未改，旧候选派生产物在模拟目录清除。验证5流程打包/语法、实际reMapping参数构建、2→1缓存失效、新索引不生成候选而历史call仍保留、Web与file://断网/Raw切换后Mapping-only/分模块编号规则位置/无底部统计/未匹配清单/390px均通过。代码尚未推送、部署。

最新模拟交付：outputs/two_samples_multi_primers_20261102_v2/report.html，2个IGH合成样本、5个配置引物（含一条短重叠引物以展示多重匹配），75张PNG全部内嵌离线，默认一个样本，比较页同链全样本。所有reads/calls/参考快照均为模拟；后处理数由现有AIRR_filter计算，RNA copy=379/489、Merged=420/540、唯一CDR3aa各7，不能作为患者结论。全页筛选、技术列隐藏、两指标成对、命名PNG下载、断网及390px验证通过。旧合成单样本report也已更新HTML和Mapping reads标签，未推送部署。

2026-11-02 最新样本比较实现（覆盖下文“尚未实现”说明）：output_metrics.py 已接入 run/render/Excel/Web/独立HTML，统计v18/图v26/Workbook v16/HTML v5。一张分组柱状图，每样本三柱=Total Reads（Merged后）、Total Receptor RNA（AIRR后处理总copy）、Total Unique CDR3s（该链后处理唯一CDR3aa）。比较页按链展示全部样本，多链时才显示链下拉，其余页默认单样本。Datapoint只从唯一来源/批次、同一所选链文件、DONE且完成回执输出版本/输入指纹一致时采用；缺失/冲突/历史无回执为N/A，真实0仍显示0。不拿Mapping行数或各gene/isotype去重相加代替。CSV=01_overview/sample_output_summary.csv，PNG=01_overview/figures/<CHAIN>__sample_output_comparison.png，Excel“样本产出比较”五列。统计明细展示去掉参考版本/参考口径、AA字段、分类层级、单位，内部AA仍用于筛选，CSV追溯字段保留。引物分析显示名统一Mapping reads（取代Merged / mapping input），技术stage不改，Total Reads仍Merged。测试覆盖源版本/缺失/0/非法/重复、实际execute导出与隔离浏览器桌面/手机/离线/下载名。代码尚未推送部署。预览均为合成数据，不是患者结果。

2026-11-02 最新显示及后续比较方案：所有一般分析视图默认首个样本，保留显式全部样本选项；样本信息表仍全量。render新增单样本matching/stage_overview/Isotype主类PNG并保留全样本图，Web/HTML总览按sample元数据选择，不从全样本PNG假装筛选；匹配构成Raw R1左、Merged右。Header不单列的Fastp显示约定延续。筛选栏按场景隐藏无用项（总览/数据变化仅样本，匹配样本链阶段，基因组成才显示引物/基因区，C对比仅样本链）；统计明细标题独占一行，表选择与指标分两块标注，没metric列就隐藏指标控件并收紧宽度。HTML Read counts不再嵌统计明细；原CSV/Excel保留。Unique Junction暂时下线：统计v17/图v25；保留原始junction追溯字段，gene/isotype去重仅cdr3_aa，Web/API/PNG/Excel/HTML均不提供Unique Junction；不能用Junction补CDR3。HTML布局v4。注意Array.filter(include)会把index传给第二个images参数，必须用row=>include(row)，否则明细指标筛选被跳过。已浏览器验证默认样本、Raw/Merged配对、场景控件、明细指标、无Junction选项、数据变化无表、离线/手机；例报告已更新。

用户新增“样本产出比较”的设计要求已确定，尚未接入计算：一张图，X样本，每样本3根柱=Total Reads（Merged后，每成功配对一条）、Total Receptor RNA（AIRR后处理总copy）、Total Unique CDR3s（同一后处理范围CDR3aa唯一数）。不能回到Raw R1+R2口径，不能拿Mapping行数填后处理copy，也不能把各基因/Isotype的去重数相加作总CDR3。现有Datapoint字段为<CHAIN>_umi_counts与<CHAIN>_uCDR3；Base/Pig copy来自AIRR_filter序列支持数，非真实UMI分子。具体构思及范围/缺失规则见plans/archive/reports/20261102_sample_output_comparison.md。本次未实现三柱比较页，不应向用户声称已经计算或输出；其余UI/离线报告改动尚未推送部署。

2026-11-02 最新分类/左右对照：引物分析必须按用途分四个二级页“引物匹配 / Mapping效率 / 逐引物基因组成 / 引物间C基因对比”。Raw R1与Merged选择只在前两类；匹配页为跨链去重的样本构成+当前链逐引物匹配率，Mapping页为所选引物输入/Mapped/Rate/Retained+逐引物效率图，基因页仅V/J/C，C对比为当前样本所有引物。网页两指标不再切换，按基因区左Expression、右Unique CDR3/Junction并排；窄屏上下。两个卡独立加载/分页，单侧旧数据不可用不阻止另一侧。C组成图同样成对，参考基因页仍单一Mapping观察口径。

HTML布局v3按kind/group_id/sample/chain/primer/segment/preparation/page严格配对，AA字段分别成组，不能把不同参考/来源/页拼一起；缺一侧显示未生成，不冒充0。每页最多7张图片，整对不跨页（全成对时每页3对），逐图PNG下载保留。引物四类图表与明细对应；“明细指标”筛选只作用表格，不拆开图对。匹配/Mapping阶段选择不影响Mapping前归属的基因组成。prepare增加逐引物匹配率和C对比表视图，公共CSV原值不改。FIGURE_VERSION v24触发新版报告布局，统计仍v16。已真实组件及file://断网验证四类、请求范围、独立左右指标、成对分页/<=7、手机/夜间，例图UI_gene_metrics_paired.png/HTML_primer_categories_paired.png，outputs/analysis_summary_report_20261102/report.html已更新；全部CSV/PNG/XLSX/SQLite逐字节不变。尚未推送部署。

2026-11-02 追加UI约定：Isotype下载改为独立“图表下载”操作行，与下方计算范围details至少12px间隔，单份图不再在按钮显示cdr3_aa；双AA命名用CDR3aa/Junctionaa。报告及引物配置的原生details统一左侧方向箭头、右侧“展开查看/收起”、浅色整行点击背景与焦点态，保留键盘语义；顶层rp-config原有::after旋转箭头必须重置width/height/transform，否则提示会旋转或被裁切。已实测closed/open/Enter、390px/夜间、按钮不贴边，截图outputs/analysis_summary_report_20261102/UI_isotype_actions_mobile.png。Read counts across samples中的Clean只标Fastp；图不再单列Header计数阶段，仍使用Fastp真实计数，不拿Header补缺失值；底层clean阶段技术记录不删。模拟HTML与新矩阵图已同步刷新。未部署。

2026-11-02 最新分析汇总/UI/报告（覆盖下文旧布局和单指标）：任务详情移除独立“数据变化”顶层Tab及重复report_panel，统一到“分析汇总”内部“数据变化”；job.js将旧#stages-data/缓存Tab映射到primer，避免旧入口报错。总体概览最前新增与report.xlsx“总览”一致的样本信息表：类别、完整样本名、链、真实引物组；workbook_overview.sample_rows共用，run保存sample_overview，旧Web/HTML可从已有catalog推导。分析记录顶部和下载区均提供独立HTML下载。

新增report_html.py：新后续分析原子输出report.html并纳入manifest/ZIP。图片base64内嵌、公共CSV表内嵌、样本/链/引物/指标筛选，图7张/页、表25行/页，无外部资源/网络依赖；流式48KiB图片块，不将所有图片载入服务器内存。不导出逐read序列、query IDs、数据库路径或输入私有配置，文本/JSON转义并有CSP。/api/reports/{id}/report.html先校验owner/succeeded/manifest/artifact版本，新报告直接下载，旧报告按原统计只读流式生成，不改旧目录；已归档/材料变化返回409，不重新Mapping。显式text/html及SCIGBLAST_report.html文件名。真实file://断网浏览器验证零HTTP请求、7图分页、筛选、PNG下载、手机/夜间Web，以及注入文本不执行。

基因统计现在同时输出% Expression与% Unique CDR3，覆盖样本、每引物V/J/C和逐引物C构成图。gene_cdr3.enrich用SQLite磁盘TEMP及COUNT(DISTINCT aa_value)汇总；原gene_usage读数行标metric=expression，另加unique_cdr3行/独立aa_field。%Unique CDR3分母是当前基因区各明确基因内去重AA归属计数之和，加候选和无注释类别各自去重计数；同一AA跨不同基因分别计入，不能称全局distinct AA分母，也不使用reads作分母。无效/空AA排除，cdr3_aa与junction_aa不混合，后者明确% Unique Junction；无证据不伪造0。参考零观察仅按expression原始mapping观察判断；不能把“有reads但无有效CDR3”当零mapping基因。API按metric/aa_field分页和下载，旧索引仅允许expression，unique请求409提示重新生成；PNG文件名含指标/AA，Excel基因表带Metric/Count/Percentage。统计v16、图v23、Workbook v15。用户随后取消独立Isotype亚类：只保留Isotype主类，具体亚类看C基因，两指标均保留；不实现其先前截图要求的独立亚类横图，历史Web/HTML也不单列亚类。原AIRR与主pipeline不改。端到端验证重复reads、跨基因同AA、缺失/无效AA、候选/无注释、引物分区、Junction-only、API权限/旧版/CSV和全部PNG/XLSX/HTML，示例outputs/analysis_summary_report_20261102（合成，非真实患者）；代码未推送部署。

2026-11-02 最新双阶段引物/分母约定：用户要求同时保留 Raw R1 与 Merged/mapping input 的匹配构成和逐引物匹配率，新增每引物 Mapping rate=对应成功Mapping reads/该阶段该引物唯一匹配reads。成功Mapping按有效V+所选locus定义，不要求productive；Retained另外计数，不修改主流程筛选/TSV。usage.py优先从trace过滤前annotations汇总成功Mapping数据（含nonproductive），所有样本/逐引物V/J/C与Isotype Expression分母均为对应mapped reads，包含该区无注释和多候选，柱图不得重归一化到100%；uCDR3继续用去重AA分母。旧仅最终结果明确available_result_reads，不伪造历史mapping率。

新primer_metrics.py在私有SQLite raw_primer_reads/primer_mapping_links保存Raw R1独立归属和query关联；双端合并前后不覆盖彼此归属。唯一canonical ID只在配对分区内连接，重复/缺证据为N/A；跨链总匹配按来源+ordinal去重，数据表均磁盘索引。pipeline_trace轻量read_sources记录原始文件指纹，full模式已有归档优先，无需为此强制归档所有上游reads；后续分析读取外部R1时只用服务器SCIGBLAST_ALLOWED_INPUT_ROOTS，不信客户端allowlist。旧trace可用对应Match清单定位当前R1，标current_raw_source，不能当历史冻结快照。Web配置改为默认双阶段，不再二选一；总览双图，引物页阶段切换、匹配率/Mapping rate卡、两类PNG；Excel“双阶段匹配总览”“引物Mapping率”，不重复旧单阶段总览Sheet。统计v15/图v21/Workbook v14，旧结果不改写，需重生成。已移除基因条右侧“已观察”及空白第四列。端到端合成实测两链去重、nonproductive仍算mapping、候选C不入唯一但保留分母、输入白名单/文件变化/重复ID/N/A、原始文件删除后读取full归档；真实组件1360/390px/夜间切换及截图通过，示例outputs/dual_primer_mapping_20261102_v15_f76cfe（合成，非患者结果）。代码未推送/部署。

H双候选真实实验已完成：固定原四样本任务H的675505 queries/四区参考/参数，仅num_alignments从1改2，8线程单链、共享slot及48GB阶段预算，835.36秒；原输出未覆盖。共同最终保留623747、前后资格无变化。IGHM-only保持116；IGHM含候选116→341，新增225为IGHD/IGHM歧义（IGHD*03→IGHD*03,IGHM*02 146，→IGHD*03,IGHM*01 79），不能视为225确定IgM。保留reads口径0.0185973%→0.0546696%仅为候选覆盖。旧报告Isotype可分类分母496914→496689，M-only占比微升为分母排除225所致。完整实验与报告outputs/H_IGHM_IGHD_two_hits_20261102，远端原任务analysis/ighm_ighd_check/20261102_110330。旧引物PPT用原始R1 738817，当前merged 675505，六引物名字/序列/IUPAC规则一致；例如P_2-HE旧195812/738817=26.5035%，新184340/675505=27.2892%，不是四舍五入差异。

2026-11-02 Merged 汇总修复：Base 04.work_pandaseq.sh 成功/缓存两分支原用4个%s接5个值，将7列CSV写成每样本两行、status错位，FASTA/DONE计数正常；已改5占位符、stage v5。pipeline_stage_metrics.merge_records() 仅读取可验证的旧两行格式（相邻百分比行、数量/比例一致）恢复数量，保留原文件，异常不猜测、不吞下一个有效样本、不用mapping输入补Merged。统计v14使旧缓存需重统计，绘图v20删除色条下文字并收紧顶部；同量mapping_input仍按原规则折叠。真实四样本Merged=675505/906485/810355/797616，与各.DONE一致，旧摘要未改；隔离Linux实际新shell正常与缓存两次输出均为一条7列记录/样本。预览与证据 outputs/merged_counts_20261102，代码未部署。

2026-11-02 下载文件名核查：用户163KB UUID截图对应自动化浏览器行为。线上report.xlsx响应attachment filename正确、166862字节/21个Sheet可解析；Playwright suggestedFilename=report.xlsx，而internal path为Temp/playwright-artifacts-*/UUID（core使用allowAndName），不能将该临时路径直接当交付文件名，应download.saveAs(目标/建议文件名)。可用文件 outputs/downloads_20261102/report.xlsx。另修复真实服务器导出MIME问题：web/artifact_download.file_response显式XLSX/PNG/CSV/TSV/JSON/ZIP等类型，不依赖Linux MIME注册表；报告与任务/Submission路由复用，权限/版本校验仍先执行。报告/树图前端下载a显式download名称，预览不加。已验证真实report路由、UTF8文件名、字节一致/ZIP CRC、Chromium按钮与saveAs；不能声称网站补丁可改变Playwright内部GUID命名，补丁未部署。

2026-11-02 用户最新澄清：希望考察Top1/Top2以核查猪IGHM/IGHD，随后询问应怎样处理歧义。不要把AIRR逗号等价最佳列表当完整排名，亦不要把两个排名解释为两套确定Isotype。当前建议主图保留IGHM/IGHD unresolved，逐条比较模板C区的得分/长度/覆盖/区分位点，引物仅辅助，做归属上下界敏感性分析；尚未校准判定阈值，也未实现完整得分排名。详见 plans/archive/reports/20261102_ighm_ighd_ambiguity_analysis.md。旧候选上限1结果不能证明唯一；C上限2可能仅含同基因两等位基因，不能保证涵盖两个不同基因。

2026-11-02 最新候选/绘图约定：用户恢复五流程 V/D/J/C 的 `-num_alignments_* 2`，覆盖下文 1 的历史决定。AIRR 仍一 query 一行，原生多 call 不选首项；analysis/repertoire 统计v13/绘图v19/Workbook v13 新增 usage_candidates 与 candidate_gene_summary，Web「候选基因」按样本/引物/VDJC分页，CSV/Excel 和样本 candidates PNG 同源。不同基因/同基因不同等位基因分开标记，IGHD/IGHM 跨类不能计入单一 Isotype；原流程 models/call_resolution.py 也只为 C 相关衍生指标解析唯一基因，原 AIRR 与链级数据不删。新参数自动使 Base/Pig/reMapping 缓存及 IR/10X 阶段脚本指纹失效；历史第二候选只能重新 mapping 获得。低引物匹配率复核默认开启、阈值50%，严格低于才扫描其他组，候选须唯一率≥阈值且优于原组，全部合格组并排红框标 Suspected。catalog 保存全部已上传组；alternatives.py 用原始 observations.sequence 按 source+ordinal 跨链去重，保留原计数/归属/数据库，证据不足不比较。CSV/Excel记录所有测试组，Web列候选/不可评估原因。PNG固定文字英文，实际ID保留。真实N1的906485 queries：原IgH_N1唯一46（0.0050745%）；IgH与IgH_O1均828821（91.432401%），两者都存疑，未自动认定引物或重跑主流程。隔离容器12条真实序列验证 num_alignments=2 可在 c_call保留IGHG1*03,IGHG2*01；不声称全部reads都有第二候选。当前代码尚未推送/部署。

2026-11-02 在线真实四 IGH 样本测试：已推送/运行 deploy.sh 部署 8a6eecc，容器1012:1014、健康。用户授权通过网站创建 a038bc2a574747a0（/jobs/a038bc2a574747a0），Base/pig，输入 /colddata/SCigblast/data/SCigblastWeb/data/ZNDX_YL，目录模式仅 H-K93-B、N1/N2/O1-K270-med-B，IGH。原表通过网页上传，样本B/引物组I，primer分组B、列对C:D/E:F，完整oligo、window、先归属再明确剪切、未匹配保留，自动report/treemap开启；N1仍按原IgH_N1，不替换组。输出 /colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/20261102_042057。第1次Match成功并审核4OK，第2次从04:22:21正式分析；05已4/4成功。逐行验证最终H/N1/N2/O1各623747/842379/749450/740016行，全IGH/productiveT/有效V，无2026pig前缀，属主1012:1014。04:47仍在06正常4CPU运行，后处理/图表尚未验收。已发现PANDAseq汇总printf缺1个占位符产生8行伪样本（FASTA不受影响）、进度观察器1MiB/2s追不上输出、06无子步骤进度；只记录计划不热修本次结果。详见 plans/archive/reports/20261102_pig_igh_integration_validation.md，证据 outputs/pig_igh_integration_20261102。测试仍由服务器继续运行；不得把尚未完成的06和自动后分析标为成功，后续应先检查此任务再决定是否重跑。

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

第五条 Web 流程 `mapping` 已注册，入口为 `mapping/pipeline/run_mapping_pipeline.sh`，三阶段由 `run_mapping.py` 执行。仅 human：扫描目录后按链多选，只处理所选链；不要求 Submission/Barcode/Match 审核。阶段为 `01.fasta`、`02.igblast`、`03.umi_count`，各阶段成功后写 DONE，全部成功才写 `.pipeline.DONE`。02 和 03 各输出筛选后的 `<样本>__<链>.tsv`，03 添加 umi_count；不输出 raw/filtered 两份文件，逐文件续跑。数据库默认沿用现有 `/data/scAnalyis/Scigblast/igblast` 挂载，通过模块配置或环境变量覆盖。命令见 `mapping/README.md`；实施设计见 `plans/archive/pipeline/20260914_mapping_module_plan.md`。不要直接调用依赖 FASTQ manifest 的 Base stage5。

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
