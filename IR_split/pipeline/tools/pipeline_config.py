"""Minimal branch-local loader for IR_split/00.pipeline_config.env."""
from __future__ import annotations
import ast
import os
import shlex
from pathlib import Path

def load_config(path: str | Path | None = None) -> Path:
    config_path = Path(path) if path else Path(os.environ.get("SCIGBLAST_CONFIG", Path(__file__).resolve().parents[1] / "00.pipeline_config.env"))
    if not config_path.is_file():
        return config_path
    lines = config_path.read_text(encoding="utf-8-sig").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip(); index += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key or not key.replace("_", "").isalnum():
            continue
        if value == "(":
            parts = []
            while index < len(lines):
                item = lines[index].strip(); index += 1
                if item == ")": break
                if item and not item.startswith("#"): parts.append(item)
            value = "(" + " ".join(parts) + ")"
        if len(value) >= 2 and value[0] == "(" and value[-1] == ")":
            try: value = "\n".join(shlex.split(value[1:-1], comments=False, posix=True))
            except ValueError: continue
        elif len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
            try: value = str(ast.literal_eval(value))
            except (SyntaxError, ValueError): value = value[1:-1]
        os.environ.setdefault(key, value)
    runtime_bin = os.environ.get("SCIGBLAST_RUNTIME_BIN_DIR", "")
    if runtime_bin:
        for key, executable in {
            "PYTHON_BIN": "python3", "SCIGBLAST_FASTP_BIN": "fastp",
            "SCIGBLAST_PANDASEQ_BIN": "pandaseq", "SCIGBLAST_IGBLAST_BIN": "igblastn",
            "FASTP_BIN": "fastp", "PANDASEQ_BIN": "pandaseq", "IGBLAST_BIN": "igblastn",
        }.items():
            os.environ[key] = str(Path(runtime_bin) / executable)
    return config_path
