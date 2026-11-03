# 无 Submission 的目录输入模式

状态：已实施并验证 Base/Pig 目录识别、Chain 分配、清单校验、Match 接入和重新匹配。日期：2026-10-14。

验收记录：Web 完整回归 110 项通过（5 项平台条件跳过），后续目录专项 5 项通过；浏览器验证创建、批量/单样本 Chain、改名/搜索/选择、重新匹配和手机布局。通过 `remote_nanhua` 使用隔离容器（2 CPU / 8 GiB，输入及参考库只读）运行 Base 与 Pig 的新旧入口；Base 完整使用选定 human 配对，Pig 从选定真实配对取前 1000 read pairs，两种入口的最终注释记录集合一致。Base 为 1601 条记录。由于原有并行处理的行顺序及汇总中的输出根不同，不能声称最终文件逐字节相同；本次不修改排序实现。五条流程的 Linux 打包/源代码检查通过。远端验证产物在 `/tmp/scigblast-directory-20261014-i19qjp/output3`，本地 JSON 报告在可视化目录 `directory-input-20261014/linux-validation.json`。尚未推送或部署。

## 目标与首版范围

用户选择服务器目录，扫描 FASTQ 配对，确认样本并指定 Chain，无需准备 Submission 工作簿；保留现有分析阶段、参数、过滤规则和输出结构。

用户已确认首版：IgBLAST base / Pig IgBLAST；统一指定多条 Chain，并允许逐样本覆盖。原 Submission 方式继续保留；reMapping 已有 CSV 扫描，不改用 FASTQ 扫描。IR/10X 待补充拆分信息后再扩展，不用猜测 Barcode 替代原生拆分逻辑。

## 当前代码依据

- `web/app.py` 的 `CreateJob`、`build_job()`、`create_job()`、`validate_job()`依赖 Submission revision；`requires_submission=false` 分支写死了 reMapping CSV 选择，不能直接切换注册表标志。
- `web/submission.py` 管理不可变 XLSX 工作副本。目录模式需要独立清单修订，不伪造 XLSX，也不借用用户传入的绝对文件路径绕过校验。
- 四条 FASTQ runner 的 Match 输出供各自 `02.run_fastp.sh` 按 `pair_id/status/r1_path/r2_path` 读取。新增模式在 Match 入口生成兼容的清单，后续沿用原实现。
- Pig `01.match_sample.py` 的原模式按 Note / Dual Index 匹配，文件名必须符合特殊 Dual Index 规则；目录模式通过明确的配对分配替代该匹配步骤，不给普通文件名伪造 Dual Index。
- Pig 现有配对按目录分组但输出裸 pair_id，Base 的 pair_id 则包含相对目录。内部选择 ID 必须与这些输出字段分离；首版对 Pig 下游标识碰撞明确阻止提交，不通过悄悄改变输出路径解决。
- IR auto/presplit 模式可允许没有样本 Barcode，但 raw 拆分仍需要对应信息；10X 的预筛选和细胞 Barcode / UMI 规则不同。

## 页面与操作

1. Base/Pig 新建任务增加“样本信息来源”：Submission / 扫描目录。已有方式保持默认，切换后只启用当前模式字段。
2. 目录模式显示服务器输入目录和“扫描样本”。扫描期间禁用重复操作，目录或流程更改立即使旧结果失效；丢弃迟到响应。
3. 列表显示选择框、样本名、相对目录、R1/R2、配对状态、Chain。默认勾选有效项，异常项不可提交并显示原因；支持搜索、分页、全选/清空及明确的所选数量。
4. 全局 Chain 支持多选并应用于选中样本，允许逐样本覆盖；不默认猜测某种链。链类型使用流程已有规范化与支持范围校验。
5. 默认按相对目录和配对 stem 标识候选样本，同名不同目录不合并。多 lane / chunk 默认分别列出，不根据相似名字自动合并；输出名称规范化后碰撞必须处理后再提交。
6. 显示“已选 N 个样本 / M 对 FASTQ / 所选 Chain”。Base 使用现有物种配置；Pig 固定猪物种，在界面明示。
7. “检查配置 → 创建任务 → Match 核对 → 开始分析”。去掉外部 Submission 要求不等于自动批准 Match；继续沿用当前审核条件。
8. 详情参数展示来源“目录识别”、所选范围和清单版本。目录模式的“修改样本/重新匹配”回到清单编辑；不要打开 XLSX 编辑器或调用旧的 workbook rematch。

## 数据与接入设计

### 扫描与文件清单

新增共享模块 `pipeline_input_manifest.py`，并用 `web/input_manifest.py` 管理 Web 用户归属、扫描快照和清单修订。

- `POST /api/input/scan`：校验流程、允许根和输入目录，按现有配置的 FASTQ 扩展名、读端后缀及 vendor counter 规则递归识别；只读文件元数据，不全量解压 FASTQ。
- 不跨目录拼接 R1/R2；重复读端、缺失配对、空文件、无法识别名称分别报告，不以最后发现的文件覆盖冲突。
- 目录符号链接不递归跟随；文件必须 resolve 后仍在所选根和允许根内，别名去重；拒绝不安全输出标识及规范化碰撞。
- 扫描建立服务器拥有、绑定用户/流程/输入根的快照。返回不透明扫描 ID、候选样本 ID、分页列表与诊断，设置扫描规模/响应上限；超限明确失败或要求更窄目录，不静默截断后声称扫描完成。
- 提交只接受扫描 ID、候选 ID、选中状态、样本名称和 Chain 分配。服务器从快照重新解析路径，客户端不能任意指定文件或清单存储路径。
- 检查和创建时重新核对存在性、大小和 mtime_ns；运行与续跑前再次核对。发现变化要求重新扫描确认，不自动替换旧文件。元数据指纹用于变更检测，不声称是内容完整性哈希。
- 创建保存原子发布、不可变的显式文件清单，包含 schema_version、来源根、pair_id、sample_id、chain_raw/规范化链、物种、R1/R2 及文件指纹；清单内容哈希参与任务参数和重跑校验。新增文件不自动进入已创建任务。

### Web 和 runner

- `CreateJob` 增加 `sample_source`（缺省 submission）、扫描引用和样本分配。目录模式不接受混入 Submission revision；reMapping 保留独立现有字段。
- `build_job()` 的 requires_submission 和 requires_match_review 两个分支均含 Mapping 专属行为，应显式分开目录输入与 CSV 流程；不因免工作簿而关闭审核，处理器也不再对目录模式调用工作簿 revision 校验。
- `/api/validate` 复用同一纯校验逻辑，不创建任务、revision 或运行输出。只有创建时发布最终清单，失败清理未绑定的临时产物。
- options 保存清单版本和选择范围，env 只传服务器生成的清单位置，例如 `SCIGBLAST_RUN_INPUT_MANIFEST`。子进程显式传递，不能从旧环境继承其他任务的清单。
- Base/Pig runner 传递参数，并把清单哈希纳入当前阶段/任务缓存判定。
- Base/Pig 的 `01.match_sample.py` 加目录清单分支：验证后映射成原字段及原文件名，保留原排序与写出方式，不增加分析产物列；原 XLSX 路径无变化。
- `sample_id`、`pair_id`、note 与 R1/R2 的路径语义必须和下游 fastp / 汇总兼容。Pig 物种固定 pig；目录模式不再按文件名 Dual Index 查工作簿。
- 保持 `WAITING_REVIEW` 和 Match revision 签名机制。修改清单需新 revision、重新 Match 与重新审核；已经批准的分配仍受现有变更限制。
- 历史任务缺新字段时按原 Submission 模式解释。任务删除、归档、分享和清单访问复用任务所有权检查，不删除源 FASTQ。

## 实施顺序与文件

1. 扫描/清单模块及配对测试：`pipeline_input_manifest.py`、`web/input_manifest.py`。
2. Base/Pig Match 适配：两条 `pipeline/01.match_sample.py` 及 `run_igblast_base.sh` / `run_pig_pipeline.sh`；核对 stage fingerprint 与输出语义。
3. Web API：`web/app.py`、`web/preflight.py`、`web/pipeline_registry.json`。新增能力字段而非把 requires_submission 全局关掉；错误定位到 Chain/目录字段。
4. UI：`web/templates/index.html`、`web/static/index.js`、`web/static/style.css`，以及详情 `job.js` / `job.html` 的参数与重新匹配入口。继续使用白灰简洁配色。
5. Docker 打包：`web/Dockerfile`、`web/Dockerfile.dockerignore`、`web/check_bundle.py`；补教程及 `AGENT.md`。

## 验证与验收

- 配对测试：各合法后缀、vendor counter、嵌套目录、同名不同目录、多 lane、缺失/重复端、空文件、越界链接、输出标识碰撞、空选择、不支持的 Chain、混合来源字段。
- API 测试：无工作簿可检查和创建；不同用户不可复用扫描/清单；客户端路径伪造无效；过期/变化扫描拒绝；未选文件及创建后新增文件不参与任务；检查无写入副作用。
- 回归：使用已有教学 FASTQ，分别构造等价的 XLSX 与目录清单，对比 Base/Pig Match 的有效样本、配对路径、Chain、物种和下游输入；验证原 Submission 和 reMapping 不变。
- 生命周期：创建、Match 审核、重新匹配、确认、停止、续跑；改变清单不能复用旧审核和缓存，任务详情完整恢复所选范围。
- 浏览器：模式切换、扫描过期、异步迟到、批量/逐样本多链、分页选择、错误定位、手机布局、键盘焦点。
- Linux/Docker：使用 `ssh remote_nanhua` 的既定测试数据，独立输出、受限资源、只读输入；同样本同链进行新旧入口完整结果比较。不得将本地模拟测试描述为真实 IgBLAST 验收。

## 后续 IR / 10X

用户已确认 IR/10X 暂不纳入首版。后续在同一清单协议上增加流程专属拆分元数据录入时，IR raw 需表达一对物理 FASTQ 对应多个样本/Barcode 的分配，presplit 沿用真实 header 验证；10X 保留其专属 Barcode 与预筛选约束。没有足够信息时明确要求补齐，不制造默认 Barcode，也不把多个生物样本自动当作一个目录样本。
