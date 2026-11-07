# 共享 Pipeline 运行模块

这里集中保存五条正式流程、Web 和报告共用的运行代码。根目录不再保留 `pipeline_*.py` 包装副本。

| 模块 | 职责 |
| --- | --- |
| `input_manifest.py` | 目录扫描、样本配对及不可变输入清单 |
| `igblast_cache.py` | 物种参考配置、依赖快照和比对缓存指纹 |
| `parallel.py` | 有界任务提交和 FASTA 数量缓存 |
| `progress.py` | IgBLAST 进度记录、读取和子进程资源描述符 |
| `locus_split.py` | 链过滤、原子发布与历史结果拆分工具 |
| `primer.py` | 原序列引物归属及明确长度的剪切 |
| `trace.py` | 原 query、比对输入与 AIRR 结果追溯 |
| `stage_metrics.py` | 各阶段数据量采集及汇总 |
| `airr_preprocessing.py` | Base AIRR 后处理和按样本衔接 |
| `resource_lock.sh` | 全局两个 pipeline 槽位和阶段资源门锁 |

Python 使用显式包导入，例如 `from scigblast.parallel import bounded_completed`。

在项目根目录运行命令行工具：

```bash
rtk proxy python -m scigblast.igblast_cache --help
rtk proxy python -m scigblast.locus_split --help
rtk proxy python -m scigblast.airr_preprocessing --help
```

runner 仍从各自 `pipeline/run_*.sh` 启动；共享包不替代流程入口。Docker 将本目录复制到 `/opt/scigblast/scigblast/`，`web/check_bundle.py` 负责核验包内文件及五条流程的依赖。
