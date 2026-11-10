# 后续统计与报告

本模块分析已有 Pig/Base Mapping 和 AIRR 后处理结果，不执行 IgBLAST。Web 使用离线 `report.html` 统一展示，保留 PNG、CSV 与 `report.xlsx` 核查工作簿。

Web 手动报告配置引物后的操作顺序为“选择样本与链 → 分配引物 → 运行引物匹配预检查 → 人工核对 → 生成完整报告”。预检查只读取原始 `_j`，以样本全部 Raw R1 reads 为分母，展示样本匹配构成、分链与逐引物占比，低匹配样本按配置测试其他引物组。缺少原始数据时显示不可计算。匹配率供人工核对配置，达到阈值时仍需用户确认。

预检查通过同一资源队列执行，输出位于 `analysis/primer_checks/<版本>/`，提供独立 HTML 和 CSV；私有 `.primer-check.json` 保存可复用统计和来源回执。完整报告重新核验样本、链、配置、代码及 Raw 文件版本后复用这些统计。编辑配置、重新选择样本或原始数据变化后，需要重新预检查。界面的高级设置按表格读取、序列位置及 Adapter/UMI/Barcode 分组；手动分配模式仅显示该模式需要的表格字段。

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

最简请求可只给 `input_root`。Web 默认支持只上传引物表，从识别出的样本与链逐项选择引物组，每条链可多选。选择完整引物名称时，开启精确名称关联。也可切换至 Submission 自动关联。

直接请求可在 `columns.sample_chain_groups` 指定样本与链的分配。所有参与本次分析的样本链均需提供配置。MIX 拆链后按对应链统计引物与基因；共享 Raw/合并输入的总览按来源去重，使用各链引物并集。比对前的归属记录与明确剪切也使用并集。历史 `sample_groups` 配置继续按样本统一分配。

```json
{
  "input_root": "/path/to/results",
  "primer": {
    "primer_path": "/path/to/primers.xls",
    "columns": {
      "primer_join_column": "B",
      "primer_columns": [["C", "D"], ["E", "F"]],
      "allow_primer_name_join": true,
      "sample_chain_groups": {
        "IgH-1": {"IGH": ["IgH_O1"]},
        "TCRα-1": {"TRA": ["Pig_TRAC_1"]},
        "MIX-1": {
          "IGH": ["IgH_O1"],
          "IGK": ["IgK"],
          "IGL": ["IgL"],
          "TRA": ["Pig_TRAC_1"],
          "TRB": ["TCRβ"]
        }
      }
    }
  }
}
```

Submission 自动关联请求如下。

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
| `primer_metrics.py`、`alternatives.py`、`scenario_analysis.py` | 原始 R1 引物匹配、其他组比较与历史关联分析 |
| `output_metrics.py`、`repertoire_metrics.py`、`isotype.py` | 样本产出、多样性、B cell、Isotype |
| `render*.py`、`figure_jobs.py`、`palette.py` | 静态 PNG、统一配色与有界绘图 |
| `workbook*.py` | 实验核查工作簿 |
| `report_html.py`、`chart_data.py`、`assets/`、`export_html.py` | 离线交互报告与仅 HTML 导出 |
| `batch.py`、`compute_jobs.py`、`private_cache.py`、`performance.py` | 批量关联、完整样本统计并行、缓存、阶段计时 |
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
├── 03_primer_genes/            原始 R1 逐引物匹配率、V/J/C
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
- 新报告的引物匹配只使用最初始 R1（_j 文件），分母为该样本全部原始 reads。`primer_metrics.build_raw` 独立于 Mapping 结果和注释索引，共享来源在样本总览中只计一次；分链面板按对应引物筛选，分母仍为样本原始 reads。原始数据不可用时明确记为不可计算。
- 新报告标记 `primer_matching_source=raw_R1`，输出 Raw 匹配率和其他组的 Raw 比较。Mapping rate 及存疑组到 Mapping 的关联分析留待后续实现。旧报告继续按其已保存的统计范围读取。
- Pig/Base 的样本/引物 V/J/C、Isotype 组成使用已发布且回执一致的 06 junction_peps。Expression 累加 copy/umi_counts；V/J/C 分母各用该区可明确分类的权重。Unique CDR3 按基因内有效 AA 去重，同 AA 跨基因分别计数；无数据为 N/A。
- Isotype 主类合并后重新去重，分母为可分类 C 注释；具体 C 基因继续在基因模块查看。
- Total Reads = Merged；Total Receptor RNA = 后处理总 copy/umi_counts；Total Unique CDR3s = 对应范围唯一有效 CDR3aa。Raw/Clean 的 R1/R2 各自取证，不对半拆分。
- 多样性来自对应 06 Datapoint；B cell 为可追溯的 SHM 与共享 CDR3。低匹配其他组对照独立标为 Suspected，不改原组结果。
- IgBLAST 当前每区最高分命中，上限 1；不输出候选基因分析，历史多 call 不强取第一项。完整定义由 `report_rules.py` 生成，并按历史证据保留原口径。

## 界面与资源

中文操作、英文图题与专业指标；Expression 左、Unique CDR3 右。总览默认全样本，单样本 Isotype 直接可见且独立切换。模块筛选独立，逐引物组成单样本，最多 7 张活动图。无底部重复明细及技术尾注，PNG/CSV 可离线导出；不生成 PDF/SVG。

完整统计持有现有报告资源门锁。计算与绘图分别自动选择最多 2 个进程，受 CPU 与内存预算约束，两个进程池按阶段先后运行。计算按完整样本划分，将共享输入的各链放在同一任务中，覆盖归属、V/J/C、copy 追溯和 Isotype。共享来源跨样本或混合流程适配器时采用串行路径。

私有统计缓存位于报告输出父目录的 `.statistics_cache/`，默认上限 1024 MiB，保存逐样本统计和 B cell/多样性指标。原始结果、06 发布来源、参考材料、样本链配置或相关计算代码变化时，受影响缓存失效。各样本配置分别参与指纹，修改一个样本可继续复用其他样本。

PNG 缓存位于同一父目录的 `.figure_cache/`，默认上限 256 MiB，覆盖总览、数据变化、引物匹配、基因与 Isotype 等图片。缓存核对输入、绘图代码、绘图库版本和 PNG 内容哈希。基因绘图进一步按样本与引物拆分，最多保持 2 个活动任务。缓存不进入公开下载包，空间超过预算时清理旧文件。HTML 衍生缓存默认 128 MiB。私有阶段计时与 RSS 回执用于性能检查。

可通过 `SCIGBLAST_REPORT_COMPUTE_WORKERS`、`SCIGBLAST_REPORT_FIGURE_WORKERS` 指定 `auto`、`1` 或 `2`。缓存预算分别由 `SCIGBLAST_REPORT_STATISTICS_CACHE_MB` 和 `SCIGBLAST_REPORT_FIGURE_CACHE_MB` 设置，`0` 关闭对应缓存。Web 将这些配置传入报告子进程。仅调整页面时沿用上文的 HTML 导出入口，调整 PNG 使用统计复用入口，新增数据执行完整统计并自动使用有效的样本缓存。

## 多链、下载与恢复

同一样本目录多个链结果共用样本选择，组成和 RNA/CDR3 指标仍按链分别统计。全部链产出图在多链样本出现时分为三个面板：共享 Merged/Total Reads 每样本一次，其余两个指标按链分组；单链选择沿用三柱比较。XLSX 的共享输入与链产出分 Sheet，不能将链内的 Unique CDR3 相加当作跨链去重数。

基因匹配只提供“下载基因匹配总表 CSV”，包含全部样本、链、引物、基因和状态，不受页面筛选或分页影响；存疑组单独标记，多个参考结果必要时保留结果标识。优先输出逐引物记录，未提供可用逐引物结果的样本/链使用“全部引物”样本汇总，不补造未知的匹配状态。主页样本信息默认全部列出，在固定高度区域通过滚轮、滚动条、鼠标拖动或触摸滑动查看。HTML 本身可离线重新下载和导出当前图的 PNG/CSV。Web 另有“实验核查包”，仅包含最新界面的 HTML 与 Excel；完整材料仍包含全部 PNG/CSV。

Isotype 及具有可靠证据的基因图显示分类覆盖比例，不改变图内可分类分母。新增 `02_sample_genes/annotation_coverage.csv` 记录 copy 覆盖；旧报告仅使用已有证据，不猜算逐引物覆盖。颜色在总览和单样本 Isotype 之间一致。

完整统计校验后先保存私有检查点，再生成 Excel/PNG/HTML。Web 检查点位于报告输出父目录的 `.report_checkpoints/<report-id>/statistics/`，CLI 默认使用同一隐藏父目录。与结果放在同一文件系统，以支持原子移动，不依赖状态目录与结果目录位于同一 Docker 挂载。重试同一报告可复用一致的统计；来源、配置或科学代码变化会失效。检查点不进入公开包，成功发布时移动为完成报告，不另复制一套大库。每个报告只保留一个完整检查点；归档/删除会清理该私有检查点，保留分析日志。不要手动删除正在运行的锁文件。

存储盘点命令（只读，不执行 VACUUM）：

```bash
rtk proxy python -m analysis.repertoire.storage_audit /path/to/details.sqlite /path/to/scenario_details.sqlite
```

默认只读页数、空闲页和 schema。`--objects` 会读取 dbstat 页面统计各表/索引空间，适合隔离副本或维护时段。阶段回执新增主进程实际磁盘字节计数，copy 分配有独立计时；不把主进程 I/O 或 CPU 当成整个子进程树。

新模块需同步 Docker 白名单和 `web/check_bundle.py`。额外验证使用系统 Temp，不新增项目测试代码。当前优化计划见 [报告性能计划](../../plans/archive/reports/20261103_report_performance.md)；历史说明已移至 [docs/history](../../docs/history/)。
