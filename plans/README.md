# 计划文档索引

当前工作与历史设计分开保存。历史方案保留原文状态，不代表功能已实现；实际行为以代码和当前模块文档为准。

## 当前工作

当前没有单独维护的待执行计划。已完成的实现与验证记录归入下方历史设计；新任务需要方案时再添加到 `active/`。

## 历史设计

### Pipeline 与调度

- [Multi-dataset and IgBLAST post-processing plan](archive/pipeline/20260821_multi_dataset_igblast_plan.md)
- [四套 pipeline 阶段化简与编号统一方案](archive/pipeline/20260822_pipeline_stage_simplification_plan.md)
- [IgBLAST 输出独立 preprocessing 与 UMI 统计计划](archive/pipeline/20260825_igblast_preprocessing_only_plan.md)
- [SCigblast：TRA/TRB 与 TRG/TRD 链合并及配对方案](archive/pipeline/20260827_TCR_chain_pairing_plan.md)
- [四套 pipeline 架构统一与 10X 聚类接入计划](archive/pipeline/20260830_four_pipeline_stage_first_and_10x_cluster_plan.md)
- [IR Stage 08 preprocessing implementation](archive/pipeline/20260830_ir_preprocess_implementation.md)
- [Preprocess pipeline 运行层优化计划](archive/pipeline/20260830_preprocess_runtime_optimization_plan.md)
- [四条 pipeline 精简与审计实施计划](archive/pipeline/20260902_four_pipeline_cleanup_and_audit_plan.md)
- [Human CSV → FASTA → IgBLAST → UMI count 整理模块计划](archive/pipeline/20260914_mapping_module_plan.md)
- [无 Submission 的目录输入模式](archive/pipeline/20261014_directory_input_plan.md)
- [样本 C primer reads 占比统计方案](archive/pipeline/20261028_primer_read_fraction_plan.md)
- [Base 05→06 按样本衔接](archive/pipeline/20261102_base_sample_preprocessing_overlap.md)
- [IR representative 旧结果分析入口与 Pipeline 集成计划](archive/pipeline/4f3c8e1a_ir_representative_analysis_plan.md)
- [IR / 10X representative 阶段统一优化计划](archive/pipeline/6f9f8b9d_ir_10x_representative_unification_plan.md)
- [Four-pipeline architecture optimization plan](archive/pipeline/9b6fb4af_four_pipeline_architecture_plan.md)
- [10X：IgBLAST 后链聚类 Stage 8 实施计划](archive/pipeline/9d2e7b91-8b17-4f9d-9c6f-27a81c532ac8_plan.md)
- [IR split 最终优化计划](archive/pipeline/IR_split_plan.md)
- [IR bulk UMI 与预拆分数据接入优化方案](archive/pipeline/IR_split_PLAN_bulk_umi_presplit.md)
- [IR split 双输入与样本内 UMI 代表序列优化计划](archive/pipeline/IR_split_PLAN_IR_dual_input_sample_umi.md)
- [IR pipeline 并发与正确性审计优化方案](archive/pipeline/IR_split_PLAN_pipeline_concurrency_audit.md)
- [IR_split 已有拆分结果复用方案](archive/pipeline/IR_split_PLAN_split_resume.md)
- [Pig IgBLAST pipeline implementation plan](archive/pipeline/PigIgblast_PIG_PIPELINE_PLAN.md)
- [IR stage-first 输出、Datapoint 身份与处理性能优化计划](archive/pipeline/pipeline_01a00e68-6ba2-7290-ab91-710fe98b2d01_plan.md)

### Web 与交互

- [新建任务与任务详情 UI 布局优化方案](archive/web/20261021_task_ui_layout_plan.md)
- [IgBLAST 进度与平台优化审阅方案](archive/web/20261027_progress_and_platform_review.md)
- [Web 前端、后端与用户交互优化方案](archive/web/5c8de3b2-4a61-4cb6-8daf-c0e2b34a6cd9_plan.md)
- [SCigblast Web 与 Pipeline 优化实施方案](archive/web/6d2c8020-a495-4d87-9aaf-758ab44cd73b_plan.md)
- [SCigblast Pipeline 与 Web 优化计划及执行记录](archive/web/947e03ba-9d4b-4f1e-9457-5404d5c3ef01_plan.md)
- [SCigblast Web 界面规范化改造计划](archive/web/web_ui_redesign_plan.md)
- [Web 操作流程优化方案](archive/web/web_workflow_optimization_plan.md)

### 后分析与报告

- [报告计算与生成性能优化](archive/reports/20261103_report_performance.md)
- [多链样本、基因 CSV 与科研报告优化](archive/reports/20261103_multichain_report_and_gene_export.md)

- [Pipeline 数据变化汇总与引物—基因追溯实施方案](archive/reports/20261031_pipeline_trace_primer_7d2e59c4_plan.md)
- [后续分析与汇报图完善方案](archive/reports/20261101_8cb19d64_report_workspace_plan.md)
- [猪 IGHM / IGHD 歧义的分析建议](archive/reports/20261102_ighm_ighd_ambiguity_analysis.md)
- [Pig IGH 四样本部署与流程验证](archive/reports/20261102_pig_igh_integration_validation.md)
- [样本产出比较：三柱分组图](archive/reports/20261102_sample_output_comparison.md)
- [2026-11-03 当前执行：全部样本产出、存疑引物组分析、家族分段](archive/reports/20261103_outputs_suspected_groups_and_family_ui.md)
- [Treemap 后置分析输出接入方案](archive/reports/3e6a87ab-9d53-4ab7-b32f-9d19e367b5cb_plan.md)
- [HTML 报告前端操作体验优化方案](archive/reports/61b12f36-1e55-4ced-afd0-ec8db846af23_plan.md)
- [HTML 报告科研使用体验优化方案](archive/reports/8d976c42-07f7-487d-9c2c-a26fa71c813b_plan.md)
- [统一报告入口与交互图优化方案](archive/reports/a4278dc0-2d84-4b26-921c-9a5e8c03c4d1_plan.md)

### 数据库与物种

- [统一 IgBLAST 流程与物种数据库选择](archive/references/20261029_igblast_species_database_selection_plan.md)
- [统一 IgBLAST base 与物种选择](archive/references/20261029_unified_igblast_species_plan.md)

### 汇报材料

- [猪免疫组库综合比较与 PPT 汇报方案](archive/presentations/20261029_pig_repertoire_ppt_6e02c1_plan.md)
- [PPT 章节分类与编号方案](archive/presentations/20261029_ppt_section_numbering_plan.md)
