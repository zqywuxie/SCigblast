# SCigblast

免疫组库分析平台：运行 IgBLAST 流程，管理样本与处理链，输出 AIRR、引物统计、树图和可离线使用的 HTML 报告。

## 从哪里开始

- 部署与配置：[Web 使用说明](web/README.md)、[数据库来源](reference/DATABASE_SOURCES.md)、[IgBLAST 部署路径](reference/IGBLAST_DEPLOYMENT.md)。
- 报告生成：[后续分析说明](analysis/repertoire/README.md)。仅更新 HTML 时，不需要重新跑统计。
- AI 修改项目：[AGENT.md](AGENT.md)。
- 设计记录：[计划索引](plans/README.md)。当前计划与历史方案已分开。

## 模块分类

| 分类 | 位置 | 用途 |
| --- | --- | --- |
| 平台 | `web/` | FastAPI、账户、任务、审核、队列、归档、页面与下载 |
| 通用分析 | `Igblast_base/pipeline/` | 多物种 IgBLAST；含 06 AIRR preprocessing |
| 猪专用旧入口 | `PigIgblast/pipeline/` | Pig IgBLAST；当前注册为五阶段 |
| Bulk IR | `IR_split/pipeline/` | Barcode/UMI 拆分、代表序列和后处理 |
| 单细胞 | `10X_split/pipeline/` | Cell Barcode/UMI、IgBLAST 和链聚类 |
| reMapping | `mapping/` | 官方 CSV → FASTA → IgBLAST → UMI count |
| 统计报告 | `analysis/repertoire/` | 引物、基因、Isotype、指标、PNG、Excel、HTML |
| 克隆树图 | `analysis/treemap/` | 单链树图、多链合图及 Clone Top |
| 共享流程实现 | 根目录 `pipeline_*.py`、`pipeline_resource_lock.sh` | 输入清单、缓存、链筛选、追溯、进度与资源锁 |
| 参考与运维工具 | `reference/`、`tools/` | 来源记录、数据库维护和前端图表构建 |
| 设计与历史 | `plans/`、`docs/history/` | 当前计划、分类归档及原始变更笔记 |

实际流程入口与阶段以 [pipeline_registry.json](web/pipeline_registry.json) 为准。根目录共享脚本被 runner、镜像和 Python 导入引用，保持原路径。

## 数据与本地产物

`data/`、`reference/data/`、`igblast/` 为数据或参考材料；`outputs/`、`output/`、`primer_analysis_20261028/`、`work_airr_excel/` 以及本地 PPT 工作目录为生成物或工作材料，不能当作生产代码入口。目录整理未迁移原始数据、参考库或已生成结果。

`pipeline/`、`IR_split_merged/`、`preprocess/` 为未注册的历史或辅助实现，保留兼容用途，不代表 Web 当前运行入口。

## 仅更新报告界面

```bash
rtk proxy python -m analysis.repertoire.export_html --source /path/to/completed-report --output /path/to/preview/report.html
```

读取已完成报告的 `summary.json`、CSV 和 PNG，不打开私有 SQLite，不重新扫描 reads；输出位置应在来源报告目录外。已有输出可加 `--replace`。新数据或统计规则变化仍需完整统计。
