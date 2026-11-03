# 统一 IgBLAST base 与物种选择

## 目标

新任务统一使用 IgBLAST base，选择 human、pig、cat 或 dog；五阶段分析、逐链过滤、输出字段和全局并发限制保持不变。旧 `pig_igblast` 任务继续使用原任务类型、配置、输出路径和 runner，以保证查看、审核、续跑和下载兼容。

## 已核对的现状

- 两个流程均为 Match、fastp、header 清理、PANDAseq、IgBLAST 加链筛选；最终过滤共用 `pipeline_locus_split.filter_mapping_airr`。
- Base 使用 `database_251117/<species>/<chain>/<species>_gl_<chain>_<part>` 和 `optional_file/<species>_gl.aux`；Pig 使用 `database_251117/pig_chain/<chain>/pig_gl_<chain>_<part>` 和 `optional_file/pig.aux`。
- Base 的物种来自环境配置，任务创建模型、页面尚无逐任务物种字段。配置文件中直接赋值 species，需要确认加载顺序并通过显式运行参数保证不被配置覆盖。
- Pig 目录模式具有 `sample-v1` 输出布局，旧目录模式和 Submission 模式又有其他约定；不能把旧 Pig 任务改成 Base 继续运行。
- 用户已准备猫狗七链参考，存放在 `database_251117/cat`、`database_251117/dog`；对应 `internal_data` 与 `optional_file` 已分开安装。参考序列覆盖与注释局限见 `outputs/cat_dog_igblast_20261029/README.md`，不能将已建库等同于真实样本全面验证。

## 实施方案

1. 保留注册表中的 `pig_igblast` 以解析历史任务，增加仅用于新建界面及新建 API 的可见性/禁用标记；任务列表筛选、详情和旧任务重试继续识别旧类型。严禁迁移已有任务的 pipeline 字段或结果目录。
2. Base 新建界面增加必选物种选项，人、猪、猫、狗；默认 human。任务选定的物种由服务端白名单验证并保存到不可变任务选项和运行环境；目录扫描、Match 审核、重新 Match、预检、缓存与续跑统一使用该值，旧 Base 任务缺字段时按历史 human 规则处理。更改物种后使旧预检及目录选择失效或重新核验。
3. 建立统一物种参考解析规则，human/cat/dog 用现有 `<species>` 目录与 `<species>_gl.aux`；pig 映射到 `pig_chain` 与 `pig.aux`，实际传 `-organism pig`。执行器、预检、缓存指纹、部署挂载检查使用同一映射，不能只改 Web 标签。校验每个所选链实际 V/J、D、可用 C、internal_data 与 aux，尤其提示猪 TRD 占位库和缺失链资源，按当前服务器实况重新检查，不使用本地旧数据推断。
4. 检查 Submission 的 Species 列语义：选择物种应作为任务唯一权威来源；非空值与所选物种冲突时在审核前明确提示并拒绝继续，不能仅在汇总中显示不同物种却使用统一数据库。目录模式将选择值写入 manifest 并绑定哈希。不同拼写需显式规范化，禁止悄悄回退 human。
5. 新建 Base 任务使用现有 Base 目录与输出规则；列出 Pig 新旧实现的 FASTQ 清理、合并、输入配对和输出命名差异，确认选择 pig 时不改变实际过滤条件、序列内容和用户期望的目录组织；如无法逐项等价，应明确区分行为并提供迁移方案，不能声称两者完全一致。
6. 更新流程介绍、任务详情、复制任务、目录重新 Match、通知与文档，展示物种；保留旧 Pig 标签。检查 UI 切换、预检一致性和 API 越权/无效物种校验。

## 验收

- 对四个物种分别验证目录模式和 Submission 模式的配置传递、匹配审核、数据库路径、链筛选与输出；Submission 物种冲突拒绝、目录扫描重新选择物种后的状态正确。
- 在受限资源的隔离容器中，对实际可用参考分别执行 IgBLAST；以同一批猪数据对比旧 Pig 与新 Base 的 AIRR 数据和过滤计数，并记录路径与参数差异。猪缺失/占位参考的链应在预检阻止。
- 历史 `pig_igblast` 与无物种字段的历史 Base 任务继续查看、重审、续跑、复制；验证不重用错误物种的缓存。验证任务级物种在配置文件加载之后仍有效。
- 验证全局两个 pipeline 和每个 pipeline 最多四个 IgBLAST 任务限制不变；确认 Docker 打包清单与文档一致。猫狗的合成验证不能代替真实样本验收。
