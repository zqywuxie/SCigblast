#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import csv
from collections import defaultdict

# todo 后续设置阈值 排除非真实cell数据

# ============================================================
# 1. 全局参数区：只需要改这里
# ============================================================

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BRANCH_ROOT = PROJECT_ROOT
OUTPUT_ROOT = os.environ.get("SCIGBLAST_OUTPUT_ROOT", os.path.join(BRANCH_ROOT, "output"))
SUMMARY_CSV = os.environ.get(
    "SCIGBLAST_10X_PLOT_SUMMARY",
    os.path.join(OUTPUT_ROOT, "06.split_output", "all_samples_summary.csv"),
)

OUTPUT_DIR = os.path.join(OUTPUT_ROOT, "08.barcode_rank")

BARCODE_RANK_PNG = "barcode_rank_plot.png"
BARCODE_RANK_HTML = "barcode_rank_plot.html"

PLOT_TITLE = "VDJ Barcode Rank Plot"

# 参考 10x 风格：固定 log 坐标轴刻度
TICK_VALUES = [1, 10, 100, 1000, 10000, 100000]
TICK_LABELS = ["1", "10", "100", "1000", "10k", "100k"]

# PNG 图片大小
FIG_WIDTH = 8
FIG_HEIGHT = 6
PNG_DPI = 300


# ============================================================
# 2. 读取 summary.csv
# ============================================================


def read_summary_csv(summary_csv):
    """
    读取 split 脚本生成的 summary.csv。

    需要字段：
        sample
        cell_barcode
        rank
        read_count
        umi_count
    """
    rows = []

    with open(summary_csv, "r", newline="") as f:
        reader = csv.DictReader(f)

        required_cols = {
            "sample",
            "cell_barcode",
            "rank",
            "CB_count",
            "umi_count",
        }

        missing_cols = required_cols - set(reader.fieldnames or [])
        if missing_cols:
            raise ValueError(
                f"summary.csv 缺少必要字段: {', '.join(sorted(missing_cols))}"
            )

        for row in reader:
            try:
                rank = int(row["rank"])
                read_count = int(row["CB_count"])
                umi_count = int(row["umi_count"])
            except ValueError:
                continue

            # log 坐标不能包含 0 或负数
            if rank <= 0 or umi_count <= 0:
                continue

            rows.append(
                {
                    "sample": row["sample"],
                    "cell_barcode": row["cell_barcode"],
                    "rank": rank,
                    "read_count": read_count,
                    "umi_count": umi_count,
                }
            )

    rows.sort(key=lambda x: (x["sample"], x["rank"]))

    return rows


def group_rows_by_sample(rows):
    """
    按 sample 分组。

    如果只有一个样本，就是一条曲线；
    如果 summary.csv 中有多个 sample，则每个 sample 画一条曲线。
    """
    sample_rows = defaultdict(list)

    for row in rows:
        sample_rows[row["sample"]].append(row)

    for sample in sample_rows:
        sample_rows[sample].sort(key=lambda x: x["rank"])

    return sample_rows


# ============================================================
# 3. PNG 静态图
# ============================================================


def plot_png(rows, png_path):
    """
    绘制 PNG 静态 Barcode Rank Plot。

    横轴：
        Barcode Rank

    纵轴：
        UMI counts

    坐标轴：
        固定使用 log scale
        tick 显示 1, 10, 100, 1000, 10k, 100k
    """
    if not rows:
        print("[Warning] No valid rows. Skip PNG plot.")
        return

    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("[Warning] matplotlib is not installed. Skip PNG plot.")
        print("          Install: pip install matplotlib")
        return

    sample_rows = group_rows_by_sample(rows)

    plt.figure(figsize=(FIG_WIDTH, FIG_HEIGHT))

    for sample, sub_rows in sample_rows.items():
        ranks = [row["rank"] for row in sub_rows]
        umi_counts = [row["umi_count"] for row in sub_rows]

        if len(sample_rows) == 1:
            plt.plot(ranks, umi_counts, linewidth=1)
        else:
            plt.plot(ranks, umi_counts, linewidth=1, label=sample)

    plt.xscale("log")
    plt.yscale("log")

    plt.xticks(TICK_VALUES, TICK_LABELS)
    plt.yticks(TICK_VALUES, TICK_LABELS)

    plt.xlabel("Barcodes")
    plt.ylabel("UMI counts")
    plt.title(PLOT_TITLE)

    plt.grid(True, which="both", linewidth=0.4, alpha=0.4)

    if len(sample_rows) > 1:
        plt.legend(frameon=False)

    plt.tight_layout()
    plt.savefig(png_path, dpi=PNG_DPI)
    plt.close()


# ============================================================
# 4. HTML 交互图
# ============================================================


def plot_html(rows, html_path):
    """
    绘制可交互 HTML Barcode Rank Plot。

    参考 10x 风格：
        - Scattergl
        - xaxis type = log
        - yaxis type = log
        - xaxis title = Barcodes
        - yaxis title = UMI counts
        - hovermode = closest

    鼠标悬停点时显示：
        Rank: xx
        umi_count: xx
    """
    if not rows:
        print("[Warning] No valid rows. Skip HTML plot.")
        return

    try:
        import plotly.graph_objects as go
    except ImportError:
        print("[Warning] plotly is not installed. Skip HTML plot.")
        print("          Install: pip install plotly")
        return

    sample_rows = group_rows_by_sample(rows)

    fig = go.Figure()

    for sample, sub_rows in sample_rows.items():
        ranks = [row["rank"] for row in sub_rows]
        umi_counts = [row["umi_count"] for row in sub_rows]

        fig.add_trace(
            go.Scattergl(
                x=ranks,
                y=umi_counts,
                mode="lines+markers",
                line=dict(width=1),
                marker=dict(size=3),
                name=sample,
                hovertemplate=("Rank: %{x}<br>umi_count: %{y}<extra></extra>"),
            )
        )

    fig.update_layout(
        title=PLOT_TITLE,
        xaxis_title="Barcodes",
        yaxis_title="UMI counts",
        hovermode="closest",
        template="plotly_white",
    )

    fig.update_xaxes(
        type="log",
        tickvals=TICK_VALUES,
        ticktext=TICK_LABELS,
        showline=True,
        zeroline=False,
    )

    fig.update_yaxes(
        type="log",
        tickvals=TICK_VALUES,
        ticktext=TICK_LABELS,
        showline=True,
        zeroline=False,
    )

    fig.write_html(html_path, include_plotlyjs=True, full_html=True)


# ============================================================
# 5. 主程序
# ============================================================


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    total_steps = 4
    print(f"[plot 1/{total_steps} 25.0%] 准备读取 barcode summary", flush=True)

    png_path = os.path.join(OUTPUT_DIR, BARCODE_RANK_PNG)
    html_path = os.path.join(OUTPUT_DIR, BARCODE_RANK_HTML)

    summary_path = SUMMARY_CSV
    # Compatibility with older split outputs that used a root-level
    # ``summary.csv`` name.
    if not os.path.isfile(summary_path):
        legacy_path = os.path.join(BRANCH_ROOT, "06.split_output", "summary.csv")
        if os.path.isfile(legacy_path):
            summary_path = legacy_path
    rows = read_summary_csv(summary_path)

    print(f"[plot 2/{total_steps} 50.0%] 已读取 summary={summary_path}; valid_rows={len(rows)}", flush=True)

    plot_png(rows, png_path)
    print(f"[plot 3/{total_steps} 75.0%] PNG 输出完成: {png_path}", flush=True)
    plot_html(rows, html_path)
    print(f"[plot 4/{total_steps} 100.0%] HTML 输出完成: {html_path}", flush=True)

    print("[plot] Done.", flush=True)
    print(f"PNG: {png_path}")
    print(f"HTML: {html_path}")


if __name__ == "__main__":
    main()
