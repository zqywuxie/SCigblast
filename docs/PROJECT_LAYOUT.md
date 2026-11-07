# 项目目录与清理记录

更新：2026-11-07。

## 当前分类

```text
SCigblast/
├── AGENT.md                   AI 修改约定
├── README.md                  使用入口与模块索引
├── deploy.sh                  部署入口
├── scigblast/                 各流程、Web、报告共用的 Python 运行包与资源锁
├── web/                       平台后端、页面、部署配置和打包检查
├── Igblast_base/pipeline/      通用 IgBLAST 流程
├── PigIgblast/pipeline/        猪专用旧入口
├── IR_split/pipeline/          Bulk IR 流程
├── 10X_split/pipeline/         单细胞流程
├── mapping/                   reMapping 流程
├── analysis/
│   ├── repertoire/            统计与离线报告
│   └── treemap/               Treemap 与 Clone Top
├── tools/
│   ├── reference/             数据库核查与维护工具
│   └── frontend/              报告前端资源构建
├── reference/                 数据库来源、参考材料和默认 Barcode
├── docs/                      当前说明与历史变更记录
├── plans/archive/             已完成或历史设计，按主题分类
├── outputs/                   本地报告、汇报材料与验证记录
└── igblast/、data/             本地工具、参考库或输入数据，不纳入代码提交
```

未注册的 `pipeline/`、`IR_split_merged/`、`preprocess/` 保留原位置，避免破坏历史手工分析。`primer_analysis_20261028/`、`work_airr_excel/` 和隐藏 PPT 工作目录是既有本地分析材料，不作为平台运行入口。最新报告链接和个人数据路径不随本次代码整理迁移。

## 共享代码迁移

根目录的九个 `pipeline_*.py` 已迁移到 `scigblast/`，模块去掉 `pipeline_` 前缀。`pipeline_resource_lock.sh` 改为 `scigblast/resource_lock.sh`。不保留根目录转发脚本，新的导入示例为 `from scigblast.parallel import bounded_completed`。

同步更新了所有正式 runner、Python 导入、子进程调用、依赖指纹、Docker COPY、构建上下文白名单和 `check_bundle.py`。直接执行的命令行模块可以在项目外工作目录启动；AIRR 后处理定位旧计算模型时仍以项目根目录为准。

维护工具移入 `tools/reference/`；前端构建脚本移入 `tools/frontend/`，其资源输出位置保持 `analysis/repertoire/assets/`。两份已完成的报告性能、多链报告方案移入 `plans/archive/reports/`，现行文档链接已同步。

## 已清理内容

- 浏览器调试目录 `.playwright-mcp/`：46 个临时截图、页面快照与控制台日志。
- 正式代码目录内的 Python `__pycache__/` 缓存。
- PPT 工作目录中 v1–v30 的旧汇报中间版本及对应校验 JSON：60 个文件；保留 v31 和正式发布的 PPT。
- 空的 `output/.pids/`、`output/` 和完成归档后的空 `plans/active/`。

浏览器临时文件和旧 PPT 中间版本共释放约 122 MiB，另有 Python 缓存。删除路径和字节数记录在本地 `outputs/project_cleanup_20261107/cleanup.json`。没有清理真实任务日志、IgBLAST 日志、输入数据、参考库或正式报告。

## 验证

- 五条正式流程的部署包文件和 Bash 语法检查通过。
- 110 个 Python 文件及 30 段 Shell 内嵌 Python 语法检查通过。
- 九个共享模块导入通过，三个 CLI 从项目外工作目录执行 `--help` 通过。
- Web 使用临时状态目录导入通过，没有启动在线服务。
- 与迁移前代码对照：链筛选 AIRR、五种引物处理状态、逐 read 归属记录和 mapping FASTA 一致。
- 使用真实八样本公共材料重新导出 HTML 成功：349 张图元信息、20 份嵌入表，嵌入统计不变，未重算原始数据。

上述验证不等于重新运行完整 Linux 生信工具链。本次整理没有部署服务器。
