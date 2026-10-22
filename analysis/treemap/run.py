"""One-work-unit command line entry point for downstream treemap tasks."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


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
    if kind == "batch":
        return run_batch(request)
    target = Path(request["target"])
    if kind == "chain":
        from analysis.treemap.aggregate import load_clones
        from analysis.treemap.render import save_treemap
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
        from analysis.treemap.render import save_combined_treemap
        images = request.get("images")
        if not isinstance(images, dict):
            raise ValueError("combined requests require an 'images' object")
        save_combined_treemap({chain: Path(path) for chain, path in images.items()}, target)
        return {"productive_count": 0, "clone_count": 0, "path": str(target)}
    raise ValueError("kind must be 'chain' or 'combined'")


def process_rss(pid: int) -> int:
    """Linux RSS budget monitor; other platforms retain the configured worker cap."""
    try:
        match = re.search(r'^VmRSS:\s+(\d+) kB', Path(f'/proc/{pid}/status').read_text(), re.M)
        return int(match[1]) * 1024 if match else 0
    except OSError:
        return 0


def run_batch(request: dict[str, Any]) -> dict[str, Any]:
    """A bounded wave of isolated workers, all in the supervisor's process group.

    Each worker also inherits the shared flock FD on Linux, so even a supervisor
    crash cannot release pipeline exclusion while its workers are still alive.
    No large clone frames or images cross process boundaries.
    """
    units = request.get('units', [])
    if not 1 <= len(units) <= 32 or any(unit.get('kind') not in {'chain', 'combined'} for unit in units):
        raise ValueError('batch requires 1–32 independent chain/combined requests')
    limit = max(128, int(request['worker_rss_mb'])) * 1024**2
    timeout = max(10, int(request['timeout_seconds']))
    lock_fd = os.environ.get('SCIGBLAST_TREEMAP_LOCK_FD')
    inherited = {'pass_fds': (int(lock_fd),)} if os.name == 'posix' and lock_fd else {}
    processes = []
    results = [None] * len(units)
    # Store only small request/result JSON beside the supervisor request files.
    with tempfile.TemporaryDirectory(prefix='batch-', dir=request['work_dir']) as directory:
        try:
            for index, unit in enumerate(units):
                source = Path(directory) / f'{index}.request.json'
                target = Path(directory) / f'{index}.result.json'
                _atomic_json(source, unit)
                process = subprocess.Popen([sys.executable, '-m', 'analysis.treemap.run',
                                            '--request', str(source), '--output', str(target)], **inherited)
                processes.append((index, process, target, time.monotonic()))
            print(f'Treemap batch: {len(processes)} workers, PIDs {[p.pid for _, p, _, _ in processes]}', flush=True)
            while any(value is None for value in results):
                for index, process, target, started in processes:
                    if results[index] is not None:
                        continue
                    error = None
                    if process.poll() is None:
                        if time.monotonic() - started > timeout:
                            error = '绘图超过时间预算，请缩小数据范围后重试'
                        elif process_rss(process.pid) > limit:
                            error = '绘图超过内存预算，请缩小数据范围后重试'
                        if not error:
                            continue
                        process.kill()
                        process.wait()
                    if not error:
                        try:
                            if process.returncode != 0:
                                raise ValueError('绘图未完成，请查看 Treemap 日志并检查输入文件')
                            result = json.loads(target.read_text(encoding='utf-8'))
                            results[index] = {'result': result, 'pid': process.pid}
                        except (ValueError, OSError) as exc:
                            error = str(exc)
                    if error:
                        results[index] = {'error': error, 'pid': process.pid}
                if any(value is None for value in results):
                    time.sleep(0.1)
            return {'results': results}
        finally:
            for _, process, _, _ in processes:
                if process.poll() is None:
                    process.kill()
                process.wait()


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
