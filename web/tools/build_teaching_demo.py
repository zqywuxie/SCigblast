"""Build the downloadable, entirely synthetic onboarding kit (no study data)."""
from __future__ import annotations

import csv
import gzip
import io
import random
import zipfile
from pathlib import Path

from openpyxl import Workbook

DEST = Path(__file__).resolve().parents[1] / "static" / "demos"
ROOT = "scigblast-teaching-v1"
NOTE = "/REPLACE_WITH_SERVER_PATH/scigblast-teaching-v1/fastq/input"


def build() -> Path:
    rng = random.Random(20261011)
    files: dict[str, bytes] = {}
    for sample, chain, counts in [("Demo_A", "IGH", (11, 3)), ("Demo_A", "IGK", (7, 2)), ("Demo_B", "IGH", (5, 1))]:
        buf = io.StringIO(newline="")
        writer = csv.writer(buf)
        writer.writerow(["CDR3(pep)", "copy", "joinedSeq"])
        for i, count in enumerate(counts):
            writer.writerow(["CARGG" if i == 0 else "CASSL", count, "".join(rng.choices("ACGT", k=300))])
        files[f"remapping/input/{sample}__{chain}.csv"] = buf.getvalue().encode()
    for sample, dual in [("Demo_A", "A01"), ("Demo_B", "A02")]:
        reads = {"R1": [], "R2": []}
        for i in range(20):
            fragment = "".join(rng.choices("ACGT", k=220))
            sequences = {"R1": fragment[:150], "R2": fragment[-150:].translate(str.maketrans("ACGT", "TGCA"))[::-1]}
            for side, sequence in sequences.items():
                reads[side].append(f"@DEMO:{sample}:{i}/{1 if side == 'R1' else 2}\n{sequence}\n+\n{'I' * 150}\n")
        for side, entries in reads.items():
            files[f"fastq/input/{sample}_{dual}_L01_{side}.fastq.gz"] = gzip.compress("".join(entries).encode(), mtime=0)
    files["barcode.csv"] = b"name,seq\n800,TTGTTACA\n801,AAGAGTTC\n"
    for pipeline in ["ir_split", "10x_split", "igblast_base", "pig_igblast"]:
        wb = Workbook()
        ws = wb.active
        ws.title = "Samples"
        ws.append(["Sample ID File new name", "Dual Index", "Chain", "Species", "Barcode（引物条码）候选", "Note"])
        for sample, dual, barcode in [("Demo_A", "A01", "800"), ("Demo_B", "A02", "801")]:
            ws.append([sample, dual, "IGH", "Pig" if pipeline == "pig_igblast" else "Human", barcode if pipeline in {"ir_split", "10x_split"} else "", NOTE])
        ws.freeze_panes = "A2"
        for col, width in [("A", 28), ("B", 16), ("C", 14), ("D", 14), ("E", 30), ("F", 85)]:
            ws.column_dimensions[col].width = width
        buf = io.BytesIO()
        wb.save(buf)
        files[f"submission/{pipeline}.xlsx"] = buf.getvalue()
    files["README.txt"] = (DEST / "README.txt").read_bytes()
    path = DEST / f"{ROOT}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(f"{ROOT}/{name}", date_time=(2026, 10, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return path


if __name__ == "__main__":
    print(build())
