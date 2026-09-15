# Repository Guidelines

## 项目定位与阅读入口

SCigblast 是免疫组库分析平台：Python/Bash 执行分析，`web/` 提供 FastAPI、Jinja2、原生 JavaScript 任务界面，SQLite 保存账户、任务和操作记录。先读 `web/pipeline_registry.json`、`web/app.py`、目标流程的 runner 和配置，再参考 `web/README.md`。`pipeline/README.md` 有过时路径与流程描述，发生冲突时以当前注册表和可执行代码为准。

## 分析架构

- `IR_split/pipeline/run_ir_pipeline.sh`：Match → fastp → Barcode/UMI 拆分 → header 清理 → PANDAseq → 代表序列 → IgBLAST → preprocessing。当前统一自动识别 raw/presplit；结果模型位于该分支 `models/`。
- `10X_split/pipeline/run_10x_pipeline.sh`：Match → fastp → R1/R2 预筛选 → header 清理 → PANDAseq → Cell Barcode/UMI 拆分与代表序列 → IgBLAST → 链聚类。不要混用 IR 与 10X 的拆分规则。
- `Igblast_base/pipeline/run_igblast_base.sh`、`PigIgblast/pipeline/run_pig_pipeline.sh`：Match → fastp → header 清理 → PANDAseq → IgBLAST；Pig 使用专属数据库逻辑。
- 每个分支通过 `00.pipeline_config.env` 与 `tools/pipeline_config.py` 管理配置；Web 通过环境变量启动固定 Bash runner。容器工具路径由 `SCIGBLAST_RUNTIME_BIN_DIR` 覆盖，数据库需单独挂载。
- `IR_split_merged/`、根目录 `pipeline/`、`preprocess/` 当前没有注册为 Web 流程；不要根据目录名推断它们是生产入口。

## Web 与输出契约

`web/app.py` 管理路径校验、队列、进程组、状态、审核和结果接口；`web/auth.py` 管理账户权限；`web/submission.py` 创建不可变 XLSX 工作副本。现有四条流程首次执行 Match 后进入 `WAITING_REVIEW`，全部记录完成标注且存在 OK 记录才能继续。

新任务输出由服务器按用户、流程和北京时间分配。流程产物通常位于 `<output>/<stage>/<dataset>/`，日志为 `logs/<dataset>/pipeline.log`，状态为 `.pipeline_state/<dataset>/`；完成标记名称须与注册表和 `pipeline_done()` 对齐。仅运行单 Web 实例、单 Uvicorn worker；网页最多并行两条任务。保留已有任务归属、路径范围、停止进程组及归档行为。

## Mapping 当前状态

`mapping/fasta.py` 递归读取 `<sample>__<chain>.csv`，要求 `CDR3(pep)`、`copy`、`joinedSeq`，生成 `>零基行号_CDR3_copy` 的 FASTA，保留相对子目录及 copy 文本。支持 CLI，独立转换默认仅 IGH；`copy` 不展开或加权序列。目录中有 `AL_s003_v8__IGH.csv` 示例。

`mapping/umi_count.ipynb` 的整理逻辑已提取到 `mapping/pipeline/umi_count.py`，从 AIRR 的 `sequence_id` 末尾还原 copy 为 `umi_count`，没有该列时兼容历史 `barcode` 列。不做 UMI 聚类或重新计数；原 notebook 保留。

第五条 Web 流程 `mapping` 已注册，入口为 `mapping/pipeline/run_mapping_pipeline.sh`，三阶段由 `run_mapping.py` 执行。仅 human：扫描目录后按链多选，只处理所选链；不要求 Submission/Barcode/Match 审核。阶段为 `01.fasta`、`02.igblast`、`03.umi_count`，各阶段成功后写 DONE，全部成功才写 `.pipeline.DONE`。02 和 03 各输出筛选后的 `<样本>__<链>.tsv`，03 添加 umi_count；不输出 raw/filtered 两份文件，逐文件续跑。数据库默认沿用现有 `/data/scAnalyis/Scigblast/igblast` 挂载，通过模块配置或环境变量覆盖。命令见 `mapping/README.md`；实施设计见 `plans/20260914_mapping_module_plan.md`。不要直接调用依赖 FASTQ manifest 的 Base stage5。

## 开发与验证

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

修改前检查 `rtk git status --short`，保留已有未提交改动。不要批量改写或提交 `data/`、`reference/`、数据库、结果目录及工作簿。按用户指定保留本文件名 `AGENT.md`；后续 AI 请显式读取本文件。
