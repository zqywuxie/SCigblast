"""Branch-local configuration loader for PigIgblast."""
from __future__ import annotations
import os
from pathlib import Path

def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'": value = value[1:-1]
    return os.path.expandvars(value)

def load_config(path: str | Path | None = None) -> dict[str, str]:
    base = Path(__file__).resolve().parents[1]
    cfg_path = Path(path or os.environ.get("PIG_PIPELINE_CONFIG", base / "00.pipeline_config.env"))
    cfg: dict[str, str] = {"PIPELINE_DIR": str(base)}
    if cfg_path.exists():
        for raw in cfg_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            key, value = line.split("=", 1); cfg[key.strip()] = _unquote(value)
    runtime_keys = {"RAW_INPUT_DIR", "SUBMISSION_XLSX", "BARCODE_CSV", "OUTPUT_ROOT"}
    for key, value in os.environ.items():
        if key.startswith("PIG_") or key in cfg or key in runtime_keys: cfg[key] = value
    cfg["PIPELINE_DIR"] = str(base)
    for key, value in list(cfg.items()):
        cfg[key] = os.path.expandvars(value).replace("${PIPELINE_DIR}", str(base)).replace("$PIPELINE_DIR", str(base))
    return cfg

def path(cfg: dict[str, str], key: str, default: str) -> Path:
    value = cfg.get(key, default) or default
    p = Path(value)
    return p if p.is_absolute() else Path(cfg["PIPELINE_DIR"]) / p
