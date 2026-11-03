# Base 05→06 按样本衔接

## 执行边界

适用于 IgBLAST base（包括 pig 物种）。一个样本的全部配对、全部所选链在05返回OK后，即可提前执行该样本的AIRR后处理；不等其他样本完成。单链样本完成一项即可，多链样本必须等待本样本全部任务完成。失败样本不进入提前处理。

最多4个IgBLAST链任务保持原限制，新增最多1个样本后处理worker。它复用现有06计算函数，结果和输出结构不变。所有mapping完成后，正式06复用已验证的样本检查点，统一生成全局汇总和完成回执。提前处理失败时正式06重试。

## 资源与完成状态

`SCIGBLAST_BASE_PIPELINED_PREPROCESSING=1`默认开启；设为0关闭。runner预留现有后处理内存预算（默认32 GiB）及进程预算（默认4），与IgBLAST预算合并参与阶段资源锁申请。预算不足时自动回退串行，不改变全局最多2个pipeline的限制。

提前处理只输出样本检查点和junction文件，不发布Datapoint.csv、processing_manifest.csv、全局run.json或base_completion.json。正式完成仍由最终06统一校验。源文件、模型、适配器和资源配置变化会使检查点失效；junction丢失、变化或意外出现也会触发重算。

## 验证与上线状态

Linux现有运行容器内，使用实际IGK/IGL/TRB各100条记录组成两个隔离样本验证：多链样本就绪边界正确；最多1个提前处理worker；失败样本不入队；最终复用两个样本检查点；Datapoint、processing_manifest和junction与串行结果逐字节一致；丢失junction可重算恢复。线程或内存预算不足、显式关闭时均正确回退串行。五流程打包与源码检查通过。

真实四样本任务已按旧调度完成到06，不声称本次运行受益于衔接优化。实现尚未推送或部署。验证证据与源码快照位于：

`/colddata/SCigblast/results/SCigblastWeb/Admin_b36c9f1e/igblast_base/pig/20261102_203159/.manual_run/`
