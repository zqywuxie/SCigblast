# IgBLAST 各物种数据库来源与审阅记录

2026-11-03 更新：服务器平台使用的猪 IGH/IGK/IGL J 辅助注释已按 IMGT 修正并备份；7 条单外显子 C 参考已扩展并替换正式 C 库，IGH C 仍为 29 条，其中 5 条保留 partial 标记。当前运行库位于 `/colddata/SCigblast/platform/SCigblastWeb/igblast`。详见 [猪 J 修复与 C 区完善记录](PIG_J_REPAIR_C_REVIEW_20261103.md)。下文 2026-10-29 审阅结论保留为历史记录。

核查日期：2026-10-29。服务器：`ssh remote_nanhua`。数据库根目录：`/colddata/zqy/igblast`。

本文记录服务器实际文件及可追溯证据，不将“目录存在”“索引可读”等同于生物学覆盖完整，也不将文件夹 `database_251117` 当作官方发布日期。本次审阅未改动数据库序列。

## 1. 物种和目录总览

| 物种 | 数据目录（相对根目录） | 来源依据 | 日期/版本可信度 |
|---|---|---|---|
| 人 human | `database_251117/human` | 原始 FASTA 的 IMGT 标准标题、accession、区域标签；现有 edit_imgt_file.pl/makeblastdb 脚本 | 具体下载日期、原始 release 未找到，不标注为最新版 |
| 小鼠 mouse | `database_251117/mouse` | 同上；包含多个品系及部分 Mus spretus 条目 | 具体下载日期、原始 release 未找到 |
| 猪 pig | `database_251117/pig_chain` | IMGT VDJ/C、NCBI TRA cDNA/基因组、部分本地派生注释 | 不同区域分批维护，见第 4 节 |
| 猫 cat | `database_251117/cat` | 2026-10-29 IMGT 下载快照、逐条 provenance 和 SHA256 | V-QUEST 页面 release 202643-4；GENE-DB 是查询当天快照 |
| 狗 dog | `database_251117/dog` | 同猫，独立物种来源清单 | 同上 |
| 恒河猴 rhesus_monkey | `database/rhesus_monkey/BCR`、`TCR` | 现存原始 FASTA 为 IMGT 标题、Macaca mulatta | 历史资料目录；本次未发现该目录下 `.nin` 核苷酸索引，不能视为已配置的链级运行库 |

根目录还存在 `database/human`、`database/mouse` 和 `database_251117/pig` 等历史布局。它们不应与 `database_251117/<species>/<chain>` 的当前链级库混用。尤其旧 `pig` 汇总库没有随 `pig_chain` 的近期修复自动更新。

平台入口以实际配置及部署代码为准：当前仓库 reMapping 固定使用 human；base 的物种由 `SCIGBLAST_IGBLAST_SPECIES` 决定；Pig 使用 `PIG_DB_VERSION`。猫狗安装到标准目录不等于 Web 已开放选择，也不等于已有任务会自动切换。

## 2. 官方来源和建库原则

- [IMGT V-QUEST / GENE-DB 参考目录](https://www.imgt.org/vquest/refseqh.html)：VDJ 参考入口，含功能性、ORF 和部分假基因条目。
- [IMGT GENE-DB 导出说明](https://www.imgt.org/genedb/directlinks)：C 区使用 query=14.1（已拼接形式）或 query=7.5（区域/外显子）时，必须保留实际标签及边界。
- [NCBI IgBLAST 建库说明](https://ncbi.github.io/igblast/cook/How-to-set-up.html)：IMGT 标题转换、makeblastdb、J aux 与自定义物种注释要求。
- [NCBI IgBLAST 官方发布入口](https://ftp.ncbi.nih.gov/blast/executables/igblast/release/)：软件及官方辅助文件入口；不能据此推断项目的所有参考序列均来自 NCBI。

常规处理是保留原始下载文件，提取官方名称，去除 IMGT 对齐点号，将序列转换为核苷酸 FASTA，再用 `makeblastdb -parse_seqids -dbtype nucl` 建索引。J 的 `aux` 必须与实际名称和序列边界匹配。`internal_data` 用于内部区域注释，与用户选择的 V/D/J/C 库用途不同。

IGH/TRB/TRD 有真实 D 区；IGK/IGL/TRA/TRG 为 VJ 链。为兼容软件设置而存在的 1-N D 占位记录不属于真实 D 基因。

## 3. 人和小鼠：来源可追溯，但版本与 C 区完整性需补充

### 3.1 人 human / Homo sapiens

原始文件位于各链 `<CHAIN><REGION>.fasta`，保留完整 IMGT 标题。例如 IGHV1-18*01 的原始 accession 为 M99641。`human_gl_<CHAIN>_<REGION>` 是转换后建库输入，带后缀的同名前缀文件是 BLAST 索引。

历史建库脚本：`database_251117/human/mkdb_251117.sh`（本地仓库也有副本）。依据标题和脚本可确认 IMGT 格式来源，但不能恢复确切下载时刻与 release。

当前 C 文件的实际区域标签：

| 链 | 参考条数及标签 | 完整性说明 |
|---|---|---|
| IGH | 123：CH1 109、CH3 8、CH2 5、H 1 | 单个外显子/片段集合，不是 123 条完整 C 区 |
| IGK | 5：C-REGION | 轻链 C-REGION；本次未见原始 partial 标记 |
| IGL | 25：C-REGION | 其中 3 条原始标题有 partial 标记 |
| TRA | 1：EX1 | 仅首外显子 |
| TRB | 6：EX1 | 仅首外显子 |
| TRG | 13：EX1 | 仅首外显子，其中 1 条标注 partial |
| TRD | 1：EX1 | 仅首外显子 |

人 IGH 原始 partial 条目包括 IGHEP1*01–*04、IGHG3*02、IGHM*02。即使未写 partial，单独 CH1/CH2/CH3 也不等于整个 C 区完整。

官方补查入口：[人 IGHC 已拼接导出](https://www.imgt.org/genedb/GENElect?query=14.1+IGHC&species=Homo+sapiens)。该链接是后续核查入口，不是对历史库具体下载 URL 的证明。

### 3.2 小鼠 mouse / Mus musculus 等

原始 IMGT 标题包含品系，例如 IGHV1-11*01 对应 AC090843、Mus musculus_C57BL/6。历史建库脚本为 `database_251117/mouse/mkdb_251117.sh`。

小鼠库并非单一品系库，也不全是 Mus musculus：转换后的 IGK V、IGL V/J/C 中保留 `_Mus_spretus` 名称。4 个库的名称差异已额外验证：索引与转换后输入完全一致，原始序列与索引序列的多重集合一致，不属于序列损坏。后续分析需要说明使用的是混合参考范围，不能默认为 C57BL/6 专属参考。

| 链 | C 参考条数及标签 | 完整性说明 |
|---|---|---|
| IGH | 40：CH1 38、H-CH2 1、M1 1 | 外显子/片段集合；不是完整 C 区集合 |
| IGK | 3：C-REGION | 轻链参考 |
| IGL | 6：C-REGION | 含 Mus spretus 条目 |
| TRA/TRB/TRG/TRD | 分别 2/4/8/1，均 EX1 | 均只覆盖首外显子 |

IGH 的 IGHA*02、IGHE*02、IGHE*03 原始标题带 partial 标记。官方补查入口：[小鼠 IGHC 已拼接导出](https://www.imgt.org/genedb/GENElect?query=14.1+IGHC&species=Mus+musculus)。

## 4. 猪 pig / Sus scrofa

正式库路径为 `database_251117/pig_chain`，近期更新记录是分区域的，不存在一个统一 IMGT release 能代表全部内容。

| 链/区域 | 来源和处理 | 当前限制 |
|---|---|---|
| IGH/IGK/IGL VDJ、TRB VDJ、TRG V | 2026-10-29 已与 IMGT 导出逐序列核对；移除旧 2026pig 名称前缀 | 匹配到官方序列不证明覆盖所有品种和等位基因 |
| IGH C | 2026-10-27 IMGT GENE-DB 导出，29 条；有逐条来源文件 | 7 条为单外显子部分 C 参考，仍需补全，不能称全长 |
| IGK C | IGKC*01、IGKC*02，各 327 nt；与官方 C-REGION 导出一致 | 只有当前官方可取得条目，不代表生物学只有两个等位基因 |
| IGL C | IGLC2*01、IGLC3*01，各 318 nt；与官方导出一致 | 本轮官方表中 IGLC1 参考尚不可用 |
| TRA V | 103 条现有序列，均与 NCBI 成熟 TRA cDNA 的 V_segment 一致 | 33 个现有名称组；并非 103 个胚系基因，等位命名仍待核查 |
| TRA J | IMGT TRAJ 排列页 58 条（54 F、4 ORF），2026-10-29 替换 | 3 条 P 未从排列页提取，不宣称覆盖全部 61 个名称 |
| TRA C | NCBI AB053451.1，3 个已知编码外显子拼接，422 nt | 原记录 5′ 部分边界及 putative 限制仍保留 |
| TRB C | TRBC1/2/3，各 531 nt；2/3 为官方已拼接导出，1 为同 accession 的 EX1–4 本地拼接 | 前 387 nt 相同，短 reads 仍可能无法区分；不能把首命中当成唯一证据 |
| TRG J/C | 2026-10-29 补入 IMGT 6 J、5 C；保存 F/ORF 信息及来源 | ORF 命中不能直接解读为功能性表达 |
| TRD V/D/J/C | V/D/J 为 1-nt 占位，C 缺失 | **不是可用猪 TRD 参考库**；NCBI 候选资料未正式补入 |

猪 TRA 来源入口：

- [IMGT TRAJ 排列](https://www.imgt.org/IMGTrepertoire/Proteins/alleles/index.php?gene=TRAJ-overview&group=TRAJ&species=Sus+scrofa)
- [NCBI AB053451.1 基因组记录](https://www.ncbi.nlm.nih.gov/nuccore/AB053451.1)：C 编码片段 97686–97955、99920–99964、100861–100967；按记录 codon_start=3 核对翻译。
- [NCBI AB087915.1 TRA cDNA 示例](https://www.ncbi.nlm.nih.gov/nuccore/AB087915.1)
- [Yamamoto 2005 TRA cDNA 研究](https://pubmed.ncbi.nlm.nih.gov/15900493/)
- [猪 IGHC 已拼接导出](https://www.imgt.org/genedb/GENElect?query=14.1+IGHC&species=Sus+scrofa)、[猪 TRBC 已拼接导出](https://www.imgt.org/genedb/GENElect?query=14.1+TRBC&species=Sus+scrofa)、[猪 TRGC 已拼接导出](https://www.imgt.org/genedb/GENElect?query=14.1+TRGC&species=Sus+scrofa)。

来源证据位置：各 BCR 链 `pig_gl_<CHAIN>_C.provenance.json`；TRA 的 `TRA_JC.provenance.json`；TRB 的 `pig_gl_TRB_C.provenance.json`；TRG 的 `TRG_JC.provenance.json`。原始 BCR 导出及名称迁移记录在 `pig_chain/.reference_sources/`。

最新 TRA 完整备份：`database_251117/backup_TRA_20261029_114107_UTC`。详细验证报告在项目 `outputs/pig_reference_audit_20261029/`。此前的候选报告状态应以 `TRA正式库替换记录.md` 为准。

## 5. 猫 cat / Felis catus 和狗 dog / Canis lupus familiaris

构建快照：`reference_builds/cat_dog_imgt_20261029/`。已安装到 `database_251117/cat`、`dog`；安装证据在 `database_251117/.reference_sources/cat_dog_imgt_20261029/`。

- `sources_manifest.json`：每个实际下载 URL、时间和原始响应 SHA256。
- `sources/`：原始 IMGT FASTA/HTML。
- 各物种 `provenance.json`：逐条 accession、原始标题、功能性、区域标签、序列 SHA256。
- `annotation_warnings.json`：非典型 V/J 区域边界、未解析锚点等限制。
- `database_sha256.json`、`index_validation.json`、`smoke_validation.json`：构建和验证凭据。

VDJ 来自 IMGT/V-QUEST 官方物种目录；C 优先 query=14.1 已拼接导出，同一等位基因多个剪接形式优先 CHS 分泌型，按需用 query=7.5 补查。轻链 C-REGION 可作为轻链 C 参考，不能与重链单 CH 外显子混为一谈。

猫 TRBC1 的 EX1–4 来自同 accession，拼接方法先在官方 TRBC2 上逐碱基验证。猫 IGHD*01 缺少已验证拼接，未纳入 C 库。猫 IGHC 目前仅有 IGHA*01、IGHG1A*01、IGHG2B*01、IGHM*01，不能把未检出 IGHE 解读为没有 IgE。

J aux 和自定义内部 V 区注释由本地构建程序派生，保留方法与警告；不是 IMGT 发布的现成 IgBLAST 注释包。部分 ORF/P 的非典型 J 只提供可靠读框，不猜测 CDR3 末端。原序列中的 N、ORF、P 均按来源保留。

官方入口：[猫 C 导出](https://www.imgt.org/genedb/GENElect?query=14.1+IGHC&species=Felis+catus)、[狗 C 导出](https://www.imgt.org/genedb/GENElect?query=14.1+IGHC&species=Canis+lupus+familiaris)。完整逐文件访问链接见 sources_manifest.json。

## 6. 恒河猴历史资料

`database/rhesus_monkey` 有 17 份 V/D/J 原始 FASTA，示例标题为 BK063715、IGHV1-111*01、Macaca mulatta_AG07107。来源格式指向 IMGT；原下载日期/release 未知。本次没有在此目录发现 `.nin` 索引，也没有在当前链级目录找到 rhesus_monkey，因此只登记为历史原始参考，不能称为已完成配置和验证的物种库。

官方核查从 [IMGT 参考目录](https://www.imgt.org/vquest/refseqh.html) 的 Rhesus monkey / Macaca mulatta 入口进入。后续需要独立完成链级整理、C 参考检查、索引和辅助注释验证。

## 7. 后续维护规则

1. 每次更新保留下载日期、原始 URL、accession/version、完整标题、选择规则、原始与最终 FASTA SHA256。
2. 区分“下载的官方序列”和“本地拼接/裁剪/名称转换/aux 派生”；派生结果记录来源坐标、读框、验证方法。
3. 替换前备份库目录和对应 aux；替换后全量回读索引，与输入序列核对，再做合成查询及独立真实数据验证。
4. 仅在证据支持时标记完整 C 区。EX1、CH1/2/3、M1 或 partial 标签必须明确展示。
5. 条数指纳入的序列记录数，不等于基因数、物种全部等位基因数或功能性基因数。
6. 人/小鼠的 C 区完整性、猪 IGH 的 7 条部分参考、猪 TRD、猪 TRA V 胚系命名是后续优先项。此次未自动修改这些区域。

## 8. 本次索引审阅汇总

下方由服务器只读审阅结果生成。缺失的 VJ 链 D 不应误报成生物学缺失；猪 TRD 的占位另行标注。IMGT 标题和点号规范化后比较，Mus spretus 名称转换以实际建库输入复核。


- 检查 5 个链级物种 × 7 链 × 4 区域，共 140 个预期位置；131 个现存索引均可回读。
- 9 个位置没有索引：人/小鼠各 4 个 VJ 链的 D（生物学不适用），以及猪 TRD C（确实未建立）。是否需要技术 D 占位由运行参数决定，此处不把 VJ 链缺 D 当作生物学缺陷。
- 124 个有标准命名原始 FASTA 的库完成索引比对：人/小鼠先规范化 IMGT 标题和对齐点号；小鼠 4 个名称例外再与转换后输入复核。均未发现序列内容不一致。剩余 7 个猪技术占位库回读为 1 nt，不计为真实参考。
- 所有回读链级索引未发现重复 ID、非 IUPAC DNA 字符或 `2026pig` 前缀。这个结论仅针对上述链级库，不包括旧汇总库、历史备份或内部注释文件。
- 本次未对全部物种做新的真实样本性能/生物学准确性验证。索引一致不代表 C 区完整、aux 边界正确或基因命名全部标准化。

### 8.1 J aux 缺项（14 条真实记录，另有猪 TRD 占位）

| 物种/链 | 缺少的名称 | 原始功能性 |
|---|---|---|
| human IGH | IGHJ5*03、IGHJ5*04 | P、**F** |
| human TRA | TRAJ51*01、TRAJ55*01、TRAJ60*01 | P |
| mouse IGL | IGLJ2P*01、IGLJ4*01_Mus_spretus、IGLJ5*01_Mus_spretus | ORF |
| mouse TRA | TRAJ2*02、TRAJ36*01、TRAJ54*01、TRAJ60*01、TRAJ61*01 | 首条 **F**，其余 P |
| mouse TRB | TRBJ2-6*01 | P |
| pig TRD | placeholder_TRD_J | 技术占位，不计入 14 条 |

缺少 aux 不表示这些参考不能命中，而是对应的读框/CDR3 注释可能不完整。优先核对两个 F 条目；P/ORF 不能为了补齐行数而猜测不存在的保守锚点。此次只记录，未改动 human/mouse aux。

### 8.2 各链当前记录数

“—”表示生物学无 D；“占位”表示只有技术序列，不能作为有效参考。数量为当前索引记录数。

| 物种 | 链 | V | D | J | C |
|---|---|---:|---:|---:|---:|
| human | IGH | 458 | 47 | 15 | 123 |
| human | IGK | 129 | — | 9 | 5 |
| human | IGL | 118 | — | 10 | 25 |
| human | TRA | 113 | — | 71 | 1 |
| human | TRB | 153 | 3 | 16 | 6 |
| human | TRG | 19 | — | 6 | 13 |
| human | TRD | 21 | 3 | 4 | 1 |
| mouse | IGH | 678 | 61 | 9 | 40 |
| mouse | IGK | 168 | — | 10 | 3 |
| mouse | IGL | 19 | — | 8 | 6 |
| mouse | TRA | 296 | — | 70 | 2 |
| mouse | TRB | 52 | 2 | 19 | 4 |
| mouse | TRG | 28 | — | 4 | 8 |
| mouse | TRD | 53 | 3 | 3 | 1 |
| pig | IGH | 17 | 5 | 6 | 29 |
| pig | IGK | 25 | — | 9 | 2 |
| pig | IGL | 23 | — | 4 | 2 |
| pig | TRA | 103 | — | 58 | 1 |
| pig | TRB | 30 | 3 | 18 | 3 |
| pig | TRG | 11 | — | 6 | 5 |
| pig | TRD | 占位 | 占位 | 占位 | 缺失 |
| cat | IGH | 66 | 6 | 6 | 4 |
| cat | IGK | 14 | — | 5 | 1 |
| cat | IGL | 49 | — | 12 | 6 |
| cat | TRA | 48 | — | 63 | 1 |
| cat | TRB | 25 | 2 | 10 | 2 |
| cat | TRG | 7 | — | 10 | 5 |
| cat | TRD | 7 | 2 | 4 | 1 |
| dog | IGH | 54 | 6 | 6 | 11 |
| dog | IGK | 61 | — | 5 | 2 |
| dog | IGL | 122 | — | 9 | 9 |
| dog | TRA | 41 | — | 53 | 1 |
| dog | TRB | 28 | 2 | 12 | 3 |
| dog | TRG | 17 | — | 22 | 12 |
| dog | TRD | 3 | 2 | 4 | 1 |

### 8.3 其他来源限制

狗 provenance 明确标记 `IGLC4*01` 为部分 C 参考，不能因为标签是 C-REGION 就忽略这条 partial 标记。猫/狗官方序列可能包含补位 N；这属于来源序列信息，不是本次发现的非法字符。

审阅原始凭据（项目相对路径）：

- `outputs/pig_reference_audit_20261029/all_species_audit.json`：索引回读、计数、名称、aux 缺项。其原始 fasta_matches_index 对 human/mouse 未规范化，不能单独作为损坏判断。
- `outputs/pig_reference_audit_20261029/imgt_normalized_audit.json`：人/小鼠 IMGT 规范化复核及恒河猴原始文件清单。
- `outputs/pig_reference_audit_20261029/audit_exceptions.json`：Mus spretus 命名例外和人/小鼠 C 区原始标签复核。
- `outputs/pig_reference_audit_20261029/database_inventory.csv`：全物种逐链逐区域机器可读清单。

猪 IGH 7 条部分 C 参考（来自逐条 provenance，均标记 partial_constant_region=true）：

| 参考名称 | 原始 accession | 当前长度 |
|---|---|---:|
| IGHD*04 | [ AY228505 ](https://www.ncbi.nlm.nih.gov/nuccore/AY228505) | 318 nt |
| IGHG4*01 | [ EU372654 ](https://www.ncbi.nlm.nih.gov/nuccore/EU372654) | 294 nt |
| IGHG4*02 | [ U03782 ](https://www.ncbi.nlm.nih.gov/nuccore/U03782) | 327 nt |
| IGHG5-1*03 | [ EU372656 ](https://www.ncbi.nlm.nih.gov/nuccore/EU372656) | 294 nt |
| IGHG5-2*01 | [ AB699686 ](https://www.ncbi.nlm.nih.gov/nuccore/AB699686) | 294 nt |
| IGHG5-2*02 | [ EU372657 ](https://www.ncbi.nlm.nih.gov/nuccore/EU372657) | 294 nt |
| IGHG6-2*03 | [ EU372653 ](https://www.ncbi.nlm.nih.gov/nuccore/EU372653) | 294 nt |
