# SCigblast Web Runner

这是一个轻量执行面板，不替代四条 pipeline。后端只调用当前服务器上的：

- `IR_split/pipeline/run_ir_pipeline.sh`
- `10X_split/pipeline/run_10x_pipeline.sh`
- `Igblast_base/pipeline/run_igblast_base.sh`
- `PigIgblast/pipeline/run_pig_pipeline.sh`

## Linux Docker 部署

1. 在 Linux 宿主机准备 pipeline 根目录、`/colddata` 和工具目录。工具目录至少需要 `fastp`、`pandaseq`、`igblastn` 及其可执行依赖；Python 依赖由镜像提供。IgBLAST 数据库继续使用宿主机路径。
2. 复制并编辑 `.env.example`：

```bash
cp .env.example .env
```

确保容器内路径与 submission 的 `Note` 路径一致。尤其不要把宿主机 `/colddata` 映射成另一个容器路径，否则 Note 匹配会失败。
3. 启动：

```bash
cd web
docker compose build
docker compose up -d
docker compose logs -f scigblast-web
```

浏览器访问 `http://服务器地址:8000`。

> 如果服务器的 8000 端口已被占用，可在 `.env` 中设置 `SCIGBLAST_WEB_PORT=8080`，此时访问 `http://服务器地址:8080`。

页面首页是全宽任务控制台：可以按状态、Pipeline 和关键词筛选任务；“新建任务”从右侧抽屉打开，并提供路径验证。任务详情页分为运行概览、Match 审核、实时日志、输出文件和参数记录五个标签。日志按字节增量读取，支持暂停刷新、自动滚动和复制。

默认结果目录为 `/colddata/zqy/SCigblast/results/web_output`，也可以在页面填写允许根目录下的其他输出目录。`runtime/scigblast.sqlite3` 保存任务和操作记录；pipeline 的 FASTQ、FASTA、TSV、日志和 DONE marker 仍写入用户指定的 output。

## 使用流程

1. 填写操作者、pipeline、原始数据目录、submission 文件/目录以及必要的 barcode CSV。
2. 第一次运行只做 Match，任务进入 `WAITING_REVIEW`。
3. 查看 Match summary，确认后点击“确认 Match”。
4. 后端再次调用同一个 runner，现有 pipeline 的 DONE marker 和样本级 checkpoint 负责断点续跑。
5. 中途失败或停止后点击“续跑”。

任务默认最多同时运行 2 条 pipeline，具体 fastp、PANDAseq、IgBLAST 并发和内存策略仍由各 pipeline 自己的 `00.pipeline_config.env` 控制。

## 安全边界

- 页面只允许选择四个固定 runner，不能传入脚本路径或任意命令。
- 输入、submission、barcode 和输出必须位于 `SCIGBLAST_ALLOWED_*_ROOTS`。
- 输入和数据库只读挂载，结果目录可写。
- Web 停止任务时按该任务自己的进程组发送 TERM，不扫描或终止其他用户任务。
- 当前版本面向受信任的 Linux 内网；如果需要公网或跨网段访问，应在前面增加 Nginx/Basic Auth，不要直接暴露 8000 端口。

## 更新 pipeline

更新宿主机 `/opt/scigblast` 下的脚本后，新任务会直接调用新脚本；每个任务记录 runner 的 SHA-256。不要在一个正在运行的任务中途覆盖它依赖的脚本，等该任务结束后再更新。
