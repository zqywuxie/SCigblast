# Web IgBLAST 参考副本

2026-11-01 按用户要求统一使用 `/colddata/zqy/igblast` 的最新版，完整复制到服务器项目：

`/colddata/SCigblast/platform/SCigblastWeb/igblast`

复制校验：2,988 个文件、148,663,804 字节，源目录复制前后与副本逐文件 SHA256 一致。该副本保留 bin、database、database_251117、internal_data、optional_file 及来源资料。原目录保持不变，副本属主为 zhengqinyun（1012:1014）。核对清单位于副本 `project_copy_manifest.json`。

`web/docker-compose.yml` 使用 `SCIGBLAST_HOST_IGBLAST_ROOT`，默认 `../igblast`（相对 web 目录），只读挂载到 `/opt/scigblast/igblast`。五条流程通过 `SCIGBLAST_REFERENCE_ROOT` 获取该位置。两个历史容器路径 `/colddata/zqy/igblast` 和 `/data/scAnalyis/Scigblast/igblast` 也挂载到同一副本，保证旧镜像和历史配置兼容。CLI 未设置此变量时保留原有路径；如在宿主机直接运行，设置为项目 igblast 的绝对路径。

这是参考目录迁移；Web 的 igblastn 继续使用镜像中 `/opt/conda/bin/igblastn`，不替换已验证的运行环境。副本 bin 中也保留原始 Linux 程序。

旧 Base 副本和变更前配置保存于服务器项目 `.igblast-migration/20261101-initial-copy/`，不参与运行。`igblast/` 和该备份目录不提交 Git。

## 生效状态

本地已准备新配置，尚未推送或部署。服务器上提前修改的 9 份受 Git 管理配置已核对、备份并恢复，工作区已确认干净，避免阻止后续 `git pull`。完整文件、差异和 SHA256 清单保存于 `.igblast-migration/deploy-preparation-a6156673/`；服务器 `.env`、运行状态、结果、项目 IgBLAST 副本均保留。`igblast/` 与 `.igblast-migration/` 已加入该服务器的 Git 本地排除文件。

检查时仍有 3 个 ARCHIVING 任务，未重建在线容器，当前服务仍使用旧挂载。代码推送后，归档及其他活动任务结束时，从服务器项目目录执行：

```bash
bash deploy.sh
```

默认拉取代码、构建新镜像并切换容器，不重跑分析。此次包含 Python/绘图库与 Web 更新，需要完整构建，不使用 `--no-build`。部署脚本在构建前后及切换前检查主分析、队列、后分析、树图、归档、删除和未确认退出的进程；忙碌或不能确认状态时退出。探针通过标准输入传入旧容器，首次升级也能检查新后分析状态。检查期间不要提交新任务；当前机制不是阻止新提交的维护锁。完成后检查健康状态和三个 IgBLAST 挂载的 Source 均为项目副本。将该状态更新为实际验证结果。

后续数据库更新应维护项目副本；原 `/colddata/zqy/igblast` 的修改不会自动同步。保留来源、版本与校验记录，并按原流程使分析缓存失效。
