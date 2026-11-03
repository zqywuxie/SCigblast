# 后续统计与报告

本模块分析已有 Pig/Base Mapping 和 AIRR 后处理结果，不执行 IgBLAST。Web 使用离线 `report.html` 统一展示，保留 PNG、CSV 与 `report.xlsx` 核查工作簿。

## 选择正确的生成方式

| 情况 | 入口 | 是否重算 |
| --- | --- | --- |
| 修改页面布局、交互或图表前端样式 | `analysis.repertoire.export_html` | 仅生成 HTML，读取公共表和已有 PNG |
| 修改静态 PNG，统计规则不变 | 完成版本的 `statistics_source` 重绘路径 | 复用已完成统计，重新生成所选展示材料 |
| 新数据、引物配置或统计规则变化 | `analysis.repertoire.run` | 扫描、追溯和统计后生成完整产物 |

### 仅生成 HTML

```bash
rtk proxy python -m analysis.repertoire.export_html --source /path/to/completed-report --output /path/to/preview/report.html
```

来源是含 `manifest.json`、`summary.json`、CSV 和 PNG 的完成目录，也可使用公开 ZIP 解压目录。不会打开私有 SQLite、扫描 FASTQ/AIRR、重新匹配引物或重画静态 PNG。输出必须在来源目录外，保持原完成清单不变；更新已有预览加 `--replace`，仅更换展示任务名可加 `--task-name`。

导出会检查清单成员、公共文件大小及生成期间的文件版本；跨机器解压后 inode/时间不同，不将原机器 stat 当作内容哈希。它复用原统计含义，不升级旧报告的科学计数。2026-11-03 使用现有八样本公共材料验证：349 张图元信息、20 份表约 4.4 秒生成 HTML；这是局部测试，不是完整统计耗时承诺。

### 完整统计

```bash
rtk proxy python -m analysis.repertoire.run --request /path/to/request.json --output /path/to/new-report-version
```

最简请求可只给 `input_root`；需要引物分析时提供 `primer`：

```json
{
  "input_root": "/path/to/results",
  "task_name": "experiment",
  "primer": {
    "submission_path": "/path/to/submission.xlsx",
    "primer_path": "/path/to/primers.xls",
    "columns": {
      "sample_column": "B",
      "submission_join_column": "I",
      "primer_join_column": "B",
      "primer_columns": [["C", "D"], ["E", "F"]]
    }
  }
}
```

多工作表用 `submission_sheet`、`primer_sheet`；表头行、完整寡核苷酸/功能引物类型、Adapter、UMI 和 Barcode 长度按实际文件配置。默认完整寡核苷酸结构为 Adapter + 10 N UMI + 8 bp Barcode + 功能引物。仅真实合并单元格继承，不做普通空格向下填充。配置字段的实际默认值见 `catalog.build_catalog`。

## 模块职责

| 实现 | 职责 |
| --- | --- |
| `sources.py`、`io.py`、`catalog.py` | 来源发现、版本核验、表格与序列读取、引物配置 |
| `aggregate.py`、`matching.py` | 原 query 归属、跨链去重、匹配汇总 |
| `usage.py`、`gene_cdr3.py`、`postprocessed_usage.py` | 基因组成、CDR3 去重、06 copy 追溯 |
| `primer_metrics.py`、`alternatives.py`、`scenario_analysis.py` | Raw/Mapping 匹配率、Mapping 率、其他组与存疑分析 |
| `output_metrics.py`、`repertoire_metrics.py`、`isotype.py` | 样本产出、多样性、B cell、Isotype |
| `render*.py`、`figure_jobs.py`、`palette.py` | 静态 PNG、统一配色与有界绘图 |
| `workbook*.py` | 实验核查工作簿 |
| `report_html.py`、`chart_data.py`、`assets/`、`export_html.py` | 离线交互报告与仅 HTML 导出 |
| `batch.py`、`compute_jobs.py`、`private_cache.py`、`performance.py` | 批量关联、可选样本归属并行、缓存、阶段计时 |
| `html_cache.py`、`api_summary.py` | Web 衍生 HTML 与查询摘要缓存 |

## 输出分类

```text
<report-version>/
├── report.html                 离线交互报告，可导出当前 PNG/CSV
├── report.xlsx                 样本与实验核查，多 Sheet
├── sample_summary.csv
├── 01_overview/                匹配、Isotype、样本产出汇总
├── 02_sample_genes/            样本 V/J/C 与参考匹配清单
├── 02_sample_metrics/          多样性、B cell、单样本 Isotype 数据
├── 03_primer_genes/            逐引物匹配率、Mapping 率、V/J/C
│   └── suspected/              独立的存疑引物组统计
├── 04_data_changes/            Raw/Clean/Merged/Mapping/Retained 变化
├── 05_details/                 诊断及可用性明细 CSV
├── summary.json / overview.json
├── manifest.json               完成与来源回执
├── details.sqlite              私有追溯，不进公开 ZIP
└── scenario_details.sqlite     私有存疑组索引，不进公开 ZIP
```

具体 CSV 映射由 `TABLE_PATHS` 定义；PNG 在对应类别的 `figures/` 下。历史目录按其 manifest 读取，不迁移旧产物。

## 科学口径

- 原 query、实际 mapping 输入及最终 AIRR 分开保存；先记录引物归属再剪切。后补表只重新分析既有数据，不重新 mapping。
- 严格 5′ 全长 IUPAC；read N 不算确定命中。唯一、多重、未匹配分开；不可评估不冒充未匹配。
- 匹配率逐 read；Mapping rate 为对应匹配 reads 中有效 V 且 locus 为所选链的比例，不额外要求 productive。最终筛选保留量另计。
- Pig/Base 的样本/引物 V/J/C、Isotype 组成使用已发布且回执一致的 06 junction_peps。Expression 累加 copy/umi_counts；V/J/C 分母各用该区可明确分类的权重。Unique CDR3 按基因内有效 AA 去重，同 AA 跨基因分别计数；无数据为 N/A。
- Isotype 主类合并后重新去重，分母为可分类 C 注释；具体 C 基因继续在基因模块查看。
- Total Reads = Merged；Total Receptor RNA = 后处理总 copy/umi_counts；Total Unique CDR3s = 对应范围唯一有效 CDR3aa。Raw/Clean 的 R1/R2 各自取证，不对半拆分。
- 多样性来自对应 06 Datapoint；B cell 为可追溯的 SHM 与共享 CDR3。低匹配其他组对照独立标为 Suspected，不改原组结果。
- IgBLAST 当前每区最高分命中，上限 1；不输出候选基因分析，历史多 call 不强取第一项。完整定义由 `report_rules.py` 生成，并按历史证据保留原口径。

## 界面与资源

中文操作、英文图题与专业指标；Expression 左、Unique CDR3 右。总览默认全样本，单样本 Isotype 直接可见且独立切换。模块筛选独立，逐引物组成单样本，最多 7 张活动图。无底部重复明细及技术尾注，PNG/CSV 可离线导出；不生成 PDF/SVG。

完整统计持有现有报告资源门锁，不改变全局 2 个 pipeline / 每 pipeline 4 个 IgBLAST 任务。计算 worker 默认 1、最多 2（仅独立完整样本的 aggregate）；绘图最多 2。可选统计缓存默认 0 MiB，HTML 衍生缓存默认 128 MiB。私有阶段计时与 RSS 回执不作为实验报表内容。

新模块需同步 Docker 白名单和 `web/check_bundle.py`。额外验证使用系统 Temp，不新增项目测试代码。当前优化计划见 [报告性能计划](../../plans/active/20261103_report_performance.md)；历史说明已移至 [docs/history](../../docs/history/)。
