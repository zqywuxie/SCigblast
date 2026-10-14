SCigblast 合成教学数据 v1
========================

用途
----
练习目录选择、CSV 格式、样本与链选择、Submission 编辑、配置检查、任务提交和 Match 审核。
全部样本名、核酸序列和计数为人工生成，不包含真实研究数据。
CSV 中的 CDR3(pep) 是教学占位值，与随机 joinedSeq 不保证生物学一致。
本包不用于验证注释准确率或性能；也不保证完整分析成功或输出非空。
FASTQ 没有模拟 IR/10X 引物、TSO、Barcode 和 UMI 建库结构，建议练习到 Match 审核后停止。

文件清单
--------
remapping/input/Demo_A__IGH.csv   2 行，copy 为 11、3
remapping/input/Demo_A__IGK.csv   2 行，copy 为 7、2
remapping/input/Demo_B__IGH.csv   2 行，copy 为 5、1
fastq/input/Demo_A_A01_L01_R1.fastq.gz / R2.fastq.gz
fastq/input/Demo_B_A02_L01_R1.fastq.gz / R2.fastq.gz
  每对 20 个 read pairs，每条 150 nt；质量均为 I，双端有 80 nt 重叠。
submission/ir_split.xlsx
submission/10x_split.xlsx
submission/igblast_base.xlsx
submission/pig_igblast.xlsx
barcode.csv                      800 / TTGTTACA，801 / AAGAGTTC

1. 放到服务器
-------------
下载 ZIP 并解压。网页中的目录是服务器路径，不是浏览器所在电脑的路径。
请使用现有文件传输方式，或请管理员将整个 scigblast-teaching-v1 目录放到允许的输入目录。
Submission 和 Barcode 也必须在各自允许的路径范围内，容器部署还需要对应挂载。
本教学包不会自动上传文件，也不会自动提交或运行任务。
每位用户使用独立副本；不要修改其他人的输入或共用示例。

2. reMapping：推荐的第一项练习
-----------------------------
打开“流程介绍与示例”，选择 reMapping，再点“使用此流程新建任务”。
原始目录选择服务器上 scigblast-teaching-v1/remapping/input 的绝对路径。
点“扫描输入目录”：应发现 2 个样本、IGH 和 IGK 两种链、3 个文件。
样本默认全部选中。仅选 IGK，应只有 Demo_A 的 1 个文件参与分析。
选回全部链，应是 3 个文件，共 6 条输入序列；不是样本数乘链数。
无需 Submission 或 Barcode。点“检查配置”，通过后创建任务。
需要服务器已安装 IgBLAST、human 参考库及其余依赖；输入合法不代表环境已配置。
查看运行阶段、日志和结果列表。随机序列可能无有效注释，最终 UMI 结果允许为空。
copy 不展开成多条序列；若某条序列保留，其 umi_count 从该条原始 copy 还原。
不保证 6 条输入全部保留，结果条数也不等于 copy 总和。

3. FASTQ：练习 Match 审核
------------------------
Base / Pig 现在可免 Submission：选择对应流程，样本信息来源选“扫描目录，无需 Submission”。
扫描 fastq/input 应得到 Demo_A_A01_L01、Demo_B_A02_L01 两个有效配对，可改名为 Demo_A、Demo_B。
全局选择 IGH，点击“应用 Chain 到已选样本”；也可展开单个样本修改 Chain（支持多链）。
检查配置后创建任务，Match 仍需核对。无需选择工作簿、编辑 Note 或提供 Barcode。
以下步骤用于原 Submission 方式；IR / 10X 仍需使用该方式。

从 IR split、10X split、IgBLAST base、Pig IgBLAST 选一个。
原始数据目录使用 fastq/input 的服务器绝对路径。
Submission 选择相应流程的 .xlsx，不要把 4 份工作簿所在目录一起提交。
点击“查看 / 编辑”，把两行 Note 的
  /REPLACE_WITH_SERVER_PATH/scigblast-teaching-v1/fastq/input
替换为真实服务器绝对路径。可以使用前缀替换，并保存工作副本。
IR / 10X：展开“可选设置”，Barcode CSV 选择本包 barcode.csv。
Base / Pig：不需要 Barcode；Pig 工作簿必须保持 Species=Pig。
点“检查配置”；解决路径或环境错误后，创建任务并执行 Match。
应匹配到 Demo_A 和 Demo_B，分别为 A01、A02，各 1 对 FASTQ，匹配状态 OK。
在任务的 Match 审核页检查样本、链、配对路径，练习审核标注。
本数据建议在继续分析之前停止任务；随机 FASTQ 后续可能被过滤、无注释或失败。
正式数据审核通过后才继续完整分析。不要为通过本示例而修改生产过滤参数。

4. 常见问题
-----------
扫描不到 CSV：目录应选 remapping/input；文件名必须是 样本__链.csv。
缺列：CSV 必须含 CDR3(pep)、copy、joinedSeq；不要修改标题。
无匹配或 Note 不匹配：检查两行 Note 都替换，且使用容器可见的绝对路径。
路径不允许：请管理员核对 allowed roots 和挂载；不要填写本地下载路径。
缺工具或数据库：需要管理员配置运行环境，教学包不包含分析工具或参考库。
结果为空：对随机教学序列属预期可能情况，不代表真实样本为空。

来源与维护
----------
由 web/tools/build_teaching_demo.py 生成；可重新生成以同步输入模板。
网页“处理步骤”来自平台注册表；本教程不改变分析流程、过滤规则或输出文件。
