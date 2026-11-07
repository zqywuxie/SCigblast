# 开发和参考库维护工具

本目录工具不属于正常任务的 pipeline 阶段，不会在部署时自动执行。

| 分类 | 工具 | 用途 |
| --- | --- | --- |
| `reference/` | `repair_pig_j_and_review_c.py` | 猪 J 区修复与 C 区候选核查 |
| `reference/` | `install_pig_c_expansion.py` | 校验、备份并安装猪 C 区扩展 |
| `frontend/` | `build_report_charts.cjs` | 构建报告使用的前端图表资源 |

参考库维护必须遵循工具的校验与备份流程，来源说明见 [数据库来源](../reference/DATABASE_SOURCES.md)。前端构建依赖放外部或系统临时目录，生产镜像直接使用已提交资源。
