"""One-work-unit command line entry point for downstream treemap tasks."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from analysis.treemap.aggregate import load_clones
from analysis.treemap.render import save_combined_treemap, save_treemap


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run_request(request: dict[str, Any]) -> dict[str, Any]:
    kind = request.get("kind")
    target = Path(request["target"])
    if kind == "chain":
        chain = str(request["chain"]).strip().upper()
        clones, productive_count = load_clones(request.get("sources", []), chain)
        if clones.empty:
            return {
                "empty": True,
                "productive_count": productive_count,
                "clone_count": 0,
                "path": None,
            }
        save_treemap(clones, target)
        return {
            "empty": False,
            "productive_count": productive_count,
            "clone_count": len(clones),
            "path": str(target),
        }
    if kind == "combined":
        images = request.get("images")
        if not isinstance(images, dict):
            raise ValueError("combined requests require an 'images' object")
        save_combined_treemap({chain: Path(path) for chain, path in images.items()}, target)
        return {"productive_count": 0, "clone_count": 0, "path": str(target)}
    raise ValueError("kind must be 'chain' or 'combined'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="JSON request file")
    parser.add_argument("--output", required=True, help="JSON result file")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request JSON must be an object")
        result = run_request(request)
        _atomic_json(output, result)
        return 0
    except Exception as exc:
        print(f"treemap task failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
