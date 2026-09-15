# Human reMapping

已取得的免疫组库 CSV → FASTA → IgBLAST → UMI count 整理。

网页选择 **reMapping**，填写 CSV 目录，点击“扫描输入目录”。按实际发现的链和文件数多选处理范围；未发现的链不可选。默认勾选已发现的 IGH，没有 IGH 时需自行选择。无需 Submission、Barcode CSV 或 Match 审核。

扫描结果同时展示样本名、相对来源路径、包含的链和文件数。**样本默认全部勾选**，可逐个取消或使用“全部样本 / 清空样本”。同一目录同一样本的不同链归为一个样本，不同子目录的同名样本分开；实际处理范围为所选样本与所选链的交集。更换输入目录会清空选择，重新扫描后样本恢复全选。任务详情显示已选样本，续跑沿用已保存的选择。旧任务未保存样本选择时继续按全部样本处理。

输入文件命名为 `<sample>__<chain>.csv`，链支持 IGH、IGK、IGL、TRA、TRB、TRD、TRG，大小写不敏感。必需列：`CDR3(pep)`、`copy`、`joinedSeq`。递归保留相对目录，不跨文件合并。每条有效记录生成 `>零基原始行号_CDR3_copy`，copy 保留文本，不展开序列、不加权比对统计。空序列记录跳过；缺列、非法 header/序列或零有效序列报告错误。

IgBLAST 使用 human 各链 V/J 数据库、存在时的 D/C 数据库及 `optional_file/human_gl.aux`，输出 AIRR。raw 结果完整保留；filtered 只保留有效 v_call 且 productive 为 T/TRUE 的行。mapping_percent 分母是 FASTA 记录数，retained_percent 是该交集的比例。

最后一步使用 `umi_count.ipynb` 提取计数的逻辑，已经整理为 `pipeline/umi_count.py`：从 AIRR `sequence_id` 末尾提取原 copy，新增单数 `umi_count` 列。没有 sequence_id 时支持历史 barcode 列。仅处理筛选后的 AIRR，其他列及行顺序不变；合法零行结果仍保留表头。

## Linux 命令行

编辑 `pipeline/00.pipeline_config.env` 或通过环境变量覆盖数据库/并发配置。默认数据库根为 `/data/scAnalyis/Scigblast/igblast`，沿用项目现有 Compose 挂载；`database_251117/human/<chain>/human_gl_<chain>_<V|D|J|C>` 位于该根下。Web 运行环境使用镜像里的 Python 和 IgBLAST。

```bash
rtk proxy bash mapping/pipeline/run_mapping_pipeline.sh \
  --input-dir /colddata/SCigblast/data/official_csv \
  --output-dir /colddata/SCigblast/results/mapping_example \
  --dataset example --chains IGH TRB
```

CLI 输入和输出不得相互嵌套。默认每个 IgBLAST 进程 8 线程、最多并行 2 个文件，并受配置中的总线程/内存预算约束。改数据库路径后需保证该路径在容器内可访问；修改镜像内配置或代码后需重建镜像。

CLI 可附加 `--samples sampleA subdir/sampleB` 只处理指定样本键；省略时默认全部样本。网页将具体样本键以 JSON 数组保存到 `SCIGBLAST_MAPPING_SAMPLES`，显式空选择不允许提交。

## 输出与续跑

- `01.fasta/<dataset>/`：FASTA、`conversion_summary.csv`。
- `02.igblastn_out/<dataset>/`：每个源文件筛选后的 `<样本>__<链>.tsv`、`.log` 和 `chain_summary.csv`。
- `03.umi_count/<dataset>/`：含 umi_count 的筛选后 `<样本>__<链>.tsv`、`umi_count_summary.csv`。
- `logs/<dataset>/pipeline.log` 与 `.pipeline_state/<dataset>/`：日志、阶段及逐文件完成记录。

网页“输出文件”可查看三个汇总报告，并选择 FASTA/AIRR/UMI 文件下载或预览表格。任一所选文件失败，整条任务标为失败，成功产物保留。用相同参数重新运行或网页“断点续跑”，只重做输入、配置或产物变化的文件；仅 UMI 整理失败不会重跑已完成 IgBLAST。网页续跑保留原链选择，改链需新建任务。

```bash
rtk proxy python -m unittest discover -s mapping/tests -v
rtk proxy python -m unittest discover -s web/tests -v
```
