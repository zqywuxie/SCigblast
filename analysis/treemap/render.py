"""PIL rendering helpers adapted from the standalone SCigblast treemap source."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw

CHAINS = ("TRA", "TRB", "TRD", "TRG", "IGH", "IGK", "IGL")
TCR_CHAINS = ("TRA", "TRB", "TRD", "TRG")
BCR_CHAINS = ("IGH", "IGK", "IGL")
V_COL = "V"
J_COL = "J"
CDR3_COL = "CDR3(pep)"
COPY_COL = "copy"
TREEMAP_SIZE = 3000
TREEMAP_DPI = (300, 300)
COMBINED_SIZE = 3000
COMBINED_DPI = (300, 300)
PANEL_GAP = 24
TCR_HEIGHT_RATIO = 0.60
TCR_WIDTH_RATIOS = (0.32, 0.56, 0.12)
BCR_WIDTH_RATIOS = (0.34, 0.44, 0.22)
PALETTE_OFFSET = 123
PALETTE = (
    "#981840", "#104008", "#a020b0", "#e8c040", "#f8e838", "#8098d0",
    "#e89080", "#e860d0", "#901098", "#70a0e8", "#a8f888", "#286028",
    "#58d000", "#d0b040", "#2818f0", "#c0f018", "#684820", "#685820",
    "#b088e0", "#988060", "#e06040", "#50d018", "#2058a0", "#b84858",
    "#10c8a8", "#a02080", "#c878c8", "#b858b8", "#d8e008", "#b80068",
    "#f86018", "#5848c8", "#582060", "#90d018", "#280018", "#10f0f8",
    "#40d830", "#006b4a", "#3ec4d1", "#ef0db8", "#80d9ab", "#0da12f",
    "#225bb7",
)


def _normalize(items: list[dict[str, Any]], width: float, height: float, key: str) -> None:
    total = sum(max(0.0, float(item[key])) for item in items)
    scale = width * height / total if total else 0.0
    for item in items:
        item["_area"] = max(0.0, float(item[key])) * scale


def _worst(row: list[dict[str, Any]], side: float) -> float:
    if not row or side <= 0:
        return float("inf")
    areas = [item["_area"] for item in row]
    row_sum = sum(areas)
    if row_sum <= 0 or min(areas) <= 0:
        return float("inf")
    return max(side * side * max(areas) / row_sum**2, row_sum**2 / (side * side * min(areas)))


def _layout_row(
    row: list[dict[str, Any]], x: float, y: float, width: float, height: float
) -> tuple[list[dict[str, Any]], float, float, float, float]:
    result: list[dict[str, Any]] = []
    row_sum = sum(item["_area"] for item in row)
    if width >= height:
        strip_width = row_sum / height
        cursor = y
        for index, item in enumerate(row):
            item_height = y + height - cursor if index == len(row) - 1 else item["_area"] / strip_width
            result.append({**item, "x": x, "y": cursor, "dx": strip_width, "dy": item_height})
            cursor += item_height
        return result, x + strip_width, y, width - strip_width, height
    strip_height = row_sum / width
    cursor = x
    for index, item in enumerate(row):
        item_width = x + width - cursor if index == len(row) - 1 else item["_area"] / strip_height
        result.append({**item, "x": cursor, "y": y, "dx": item_width, "dy": strip_height})
        cursor += item_width
    return result, x, y + strip_height, width, height - strip_height


def _squarify_items(
    records: list[dict[str, Any]], x: float, y: float,
    width: float, height: float, value_key: str,
) -> list[dict[str, Any]]:
    items = [dict(record) for record in records if float(record[value_key]) > 0]
    items.sort(key=lambda item: float(item[value_key]), reverse=True)
    _normalize(items, width, height, value_key)
    output: list[dict[str, Any]] = []
    row: list[dict[str, Any]] = []
    next_item = 0
    while next_item < len(items):
        candidate = items[next_item]
        side = min(width, height)
        if not row or _worst([*row, candidate], side) <= _worst(row, side):
            row.append(candidate)
            next_item += 1
        else:
            laid, x, y, width, height = _layout_row(row, x, y, width, height)
            output.extend(laid)
            row = []
    if row:
        laid, _, _, _, _ = _layout_row(row, x, y, width, height)
        output.extend(laid)
    return output


def _clone_color(v_value: str, j_value: str, cdr3_value: str) -> str:
    key = f"{v_value}|{j_value}|{cdr3_value}".encode("utf-8")
    stable_value = int(hashlib.md5(key).hexdigest()[:12], 16)
    return PALETTE[(stable_value + PALETTE_OFFSET) % len(PALETTE)]


def _block_style(dx: float, dy: float) -> tuple[float, float]:
    scale = TREEMAP_SIZE / 1000.0
    side = min(dx, dy) / scale
    if side < 2.0:
        return 0.02 * scale, 0.08 * scale
    if side < 3.5:
        return 0.04 * scale, 0.16 * scale
    if side < 6.0:
        return 0.07 * scale, min(side * 0.06, 0.28) * scale
    if side < 10.0:
        return 0.12 * scale, min(side * 0.08, 0.65) * scale
    if side < 18.0:
        return 0.20 * scale, min(side * 0.12, 1.6) * scale
    if side < 36.0:
        return 0.32 * scale, min(side * 0.18, 4.5) * scale
    return 0.62 * scale, min(side * 0.28, 36.0) * scale


def _draw_leaf(draw: ImageDraw.ImageDraw, rect: dict[str, Any]) -> None:
    x, y = float(rect["x"]), float(rect["y"])
    dx, dy = float(rect["dx"]), float(rect["dy"])
    gap, rounding = _block_style(dx, dy)
    left_gap = 0.0 if x <= 0 else gap / 2
    top_gap = 0.0 if y <= 0 else gap / 2
    right_gap = 0.0 if x + dx >= TREEMAP_SIZE - 1e-6 else gap / 2
    bottom_gap = 0.0 if y + dy >= TREEMAP_SIZE - 1e-6 else gap / 2
    left = max(0, int(round(x + left_gap)))
    top = max(0, int(round(y + top_gap)))
    right = min(TREEMAP_SIZE - 1, int(round(x + dx - right_gap)))
    bottom = min(TREEMAP_SIZE - 1, int(round(y + dy - bottom_gap)))
    if right < left or bottom < top:
        return
    radius = min(rounding, (right - left + 1) / 2, (bottom - top + 1) / 2)
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=max(0, int(round(radius))), fill=rect["color"]
    )


def _save_png_atomic(canvas: Image.Image, output_path: Path, dpi: tuple[int, int]) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=".png", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        canvas.save(temporary_path, format="PNG", dpi=dpi, optimize=True)
        with Image.open(temporary_path) as rendered:
            rendered.verify()
        with Image.open(temporary_path) as rendered:
            if rendered.format != "PNG" or rendered.size != canvas.size:
                raise OSError("Rendered treemap PNG failed format or dimension verification")
        os.replace(temporary_path, output_path)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def generate_qr_treemap(clones: pd.DataFrame, output_path: Path) -> None:
    """Render a copy-weighted V→J→CDR3 squarified treemap as a 3000px PNG."""
    required = [V_COL, J_COL, CDR3_COL, COPY_COL]
    missing = [column for column in required if column not in clones.columns]
    if missing:
        raise ValueError(f"Clone table is missing columns: {', '.join(missing)}")
    data = clones[required].copy()
    data[COPY_COL] = pd.to_numeric(data[COPY_COL], errors="coerce").fillna(0)
    data = data[(data[CDR3_COL].astype(str) != "") & (data[COPY_COL] > 0)]
    if data.empty:
        raise ValueError("No valid clonotypes to draw")

    canvas = Image.new("RGB", (TREEMAP_SIZE, TREEMAP_SIZE), "white")
    try:
        draw = ImageDraw.Draw(canvas)
        v_records = data.groupby(V_COL, sort=False)[COPY_COL].sum().reset_index().to_dict("records")
        v_rects = _squarify_items(v_records, 0, 0, TREEMAP_SIZE, TREEMAP_SIZE, COPY_COL)
        for v_rect in v_rects:
            v_value = v_rect[V_COL]
            v_data = data[data[V_COL] == v_value]
            j_records = v_data.groupby(J_COL, sort=False)[COPY_COL].sum().reset_index().to_dict("records")
            j_rects = _squarify_items(
                j_records, v_rect["x"], v_rect["y"], v_rect["dx"], v_rect["dy"], COPY_COL
            )
            for j_rect in j_rects:
                j_value = j_rect[J_COL]
                leaf_data = v_data[v_data[J_COL] == j_value]
                leaves = [
                    {
                        CDR3_COL: row[CDR3_COL],
                        COPY_COL: row[COPY_COL],
                        "color": _clone_color(str(v_value), str(j_value), str(row[CDR3_COL])),
                    }
                    for _, row in leaf_data.iterrows()
                ]
                for leaf in _squarify_items(
                    leaves, j_rect["x"], j_rect["y"], j_rect["dx"], j_rect["dy"], COPY_COL
                ):
                    leaf["x"] = TREEMAP_SIZE - leaf["x"] - leaf["dx"]
                    leaf["y"] = TREEMAP_SIZE - leaf["y"] - leaf["dy"]
                    _draw_leaf(draw, leaf)
        _save_png_atomic(canvas, Path(output_path), TREEMAP_DPI)
    finally:
        canvas.close()


def save_treemap(clones: pd.DataFrame, out_png: Path) -> None:
    generate_qr_treemap(clones, Path(out_png))


def save_combined_treemap(chain_images: dict[str, Path], output_path: Path) -> None:
    """Compose existing chain images with the source's fixed panel geometry."""
    chain_images = {str(chain).upper(): Path(path) for chain, path in chain_images.items()}
    tcr_available = [chain for chain in TCR_CHAINS if chain in chain_images]
    bcr_available = [chain for chain in BCR_CHAINS if chain in chain_images]
    if not tcr_available and not bcr_available:
        raise ValueError("No chain images are available for composition")

    tcr_bottom = (
        int(round(COMBINED_SIZE * TCR_HEIGHT_RATIO))
        if tcr_available and bcr_available
        else COMBINED_SIZE if tcr_available else 0
    )
    boxes: dict[str, tuple[int, int, int, int]] = {}
    main_chains = [chain for chain in ("TRA", "TRB") if chain in tcr_available]
    side_chains = [chain for chain in ("TRD", "TRG") if chain in tcr_available]
    main_weights = dict(zip(("TRA", "TRB"), TCR_WIDTH_RATIOS[:2]))
    main_total = sum(main_weights[chain] for chain in main_chains)
    side_weight = TCR_WIDTH_RATIOS[2] if side_chains else 0.0
    tcr_total = main_total + side_weight
    if tcr_available:
        main_right = int(round(COMBINED_SIZE * main_total / tcr_total))
        cursor = 0.0
        left = 0
        for index, chain in enumerate(main_chains):
            cursor += main_weights[chain]
            right = main_right if index == len(main_chains) - 1 else int(round(COMBINED_SIZE * cursor / tcr_total))
            boxes[chain] = (left, 0, right, tcr_bottom)
            left = right
        for index, chain in enumerate(side_chains):
            top = int(round(tcr_bottom * index / len(side_chains)))
            bottom = int(round(tcr_bottom * (index + 1) / len(side_chains)))
            boxes[chain] = (main_right, top, COMBINED_SIZE, bottom)

    bcr_weights = dict(zip(BCR_CHAINS, BCR_WIDTH_RATIOS))
    bcr_total = sum(bcr_weights[chain] for chain in bcr_available)
    cursor = 0.0
    left = 0
    for index, chain in enumerate(bcr_available):
        cursor += bcr_weights[chain]
        right = COMBINED_SIZE if index == len(bcr_available) - 1 else int(round(COMBINED_SIZE * cursor / bcr_total))
        boxes[chain] = (left, tcr_bottom, right, COMBINED_SIZE)
        left = right

    canvas = Image.new("RGB", (COMBINED_SIZE, COMBINED_SIZE), "black")
    try:
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        inset = PANEL_GAP // 2
        for chain, (left, top, right, bottom) in boxes.items():
            left, top = left + inset, top + inset
            right, bottom = right - inset, bottom - inset
            if right <= left or bottom <= top:
                continue
            with Image.open(chain_images[chain]) as source:
                panel = source.convert("RGB").resize((right - left, bottom - top), resample=resampling)
            try:
                canvas.paste(panel, (left, top))
            finally:
                panel.close()
        _save_png_atomic(canvas, Path(output_path), COMBINED_DPI)
    finally:
        canvas.close()
