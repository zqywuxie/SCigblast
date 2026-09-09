from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
import csv
import tempfile
import shutil
import shlex
import submission
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


APP_DIR = Path(__file__).resolve().parent
PIPELINE_ROOT = Path(os.environ.get("SCIGBLAST_PIPELINE_ROOT") or APP_DIR.parent).resolve()
STATE_ROOT = Path(os.environ.get("SCIGBLAST_STATE_DIR", "/var/lib/scigblast-web")).resolve()
DB_PATH = STATE_ROOT / "scigblast.sqlite3"
DEFAULT_OUTPUT_ROOT = Path(
    os.environ.get("SCIGBLAST_DEFAULT_OUTPUT_ROOT", "/colddata/zqy/SCigblast/results")
).resolve()
# Compatibility with the original .env: web_output was a shared task root.
if DEFAULT_OUTPUT_ROOT == Path('/colddata/zqy/SCigblast/results/web_output').resolve():
    DEFAULT_OUTPUT_ROOT = DEFAULT_OUTPUT_ROOT.parent
MAX_ACTIVE_JOBS = min(2, max(1, int(os.environ.get("SCIGBLAST_MAX_ACTIVE_JOBS", "2"))))


def load_registry() -> dict[str, dict[str, Any]]:
    with (APP_DIR / "pipeline_registry.json").open(encoding="utf-8") as handle:
        return json.load(handle)


REGISTRY = load_registry()
PROCESS_LOCK = threading.RLock()
PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
STOP_REQUESTED: set[str] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                operator TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                dataset TEXT NOT NULL,
                input_path TEXT NOT NULL,
                submission_path TEXT NOT NULL,
                barcode_csv TEXT,
                output_root TEXT NOT NULL,
                options_json TEXT NOT NULL,
                env_json TEXT NOT NULL,
                runner_path TEXT NOT NULL,
                runner_hash TEXT,
                status TEXT NOT NULL,
                current_stage TEXT,
                progress INTEGER NOT NULL DEFAULT 0,
                attempt_no INTEGER NOT NULL DEFAULT 0,
                pid INTEGER,
                pgid INTEGER,
                exit_code INTEGER,
                last_error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                operator TEXT NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
            CREATE TABLE IF NOT EXISTS reviews (
                job_id TEXT NOT NULL, revision TEXT NOT NULL, row_key TEXT NOT NULL,
                label TEXT NOT NULL, note TEXT NOT NULL,
                PRIMARY KEY(job_id, revision, row_key)
            );
            """
        )


def update_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    assignments = ", ".join(f"{key} = ?" for key in values)
    with db() as connection:
        connection.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?", (*values.values(), job_id)
        )


def add_action(job_id: str, operator: str, action: str, details: str = "") -> None:
    with db() as connection:
        connection.execute(
            "INSERT INTO actions(job_id, operator, action, details, created_at) VALUES(?,?,?,?,?)",
            (job_id, operator, action, details, now()),
        )


def get_job_row(job_id: str) -> sqlite3.Row:
    with db() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "job not found")
    return row


def children(root: Path) -> bool:
    return root.exists() and root.is_dir()


def is_under(path: Path, roots: list[Path]) -> bool:
    candidate = os.path.realpath(path)
    for root in roots:
        try:
            if os.path.commonpath([candidate, os.path.realpath(root)]) == os.path.realpath(root):
                return True
        except ValueError:
            continue
    return False


def configured_roots(name: str, default: str) -> list[Path]:
    raw = os.environ.get(name, default)
    return [Path(item).resolve() for item in raw.split(os.pathsep) if item.strip()]


def validate_input_path(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, f"{label} must be an absolute server path")
    resolved = path.resolve()
    if not children(resolved):
        raise HTTPException(400, f"{label} is not an existing directory: {resolved}")
    if not is_under(resolved, configured_roots("SCIGBLAST_ALLOWED_INPUT_ROOTS", "/colddata")):
        raise HTTPException(400, f"{label} is outside the allowed input roots")
    return resolved


def validate_submission(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, "submission_path must be an absolute server path")
    resolved = path.resolve()
    if resolved.is_file():
        if resolved.suffix.lower() != ".xlsx":
            raise HTTPException(400, "submission file must be .xlsx")
    elif resolved.is_dir():
        if not any(resolved.glob("*.xlsx")):
            raise HTTPException(400, "submission directory contains no .xlsx files")
    else:
        raise HTTPException(400, f"submission path does not exist: {resolved}")
    if not is_under(resolved, configured_roots("SCIGBLAST_ALLOWED_SUBMISSION_ROOTS", "/colddata")):
        raise HTTPException(400, "submission path is outside the allowed roots")
    return resolved


def validate_barcode(value: str | None, required: bool) -> Path | None:
    if not value and required:
        value = str(PIPELINE_ROOT / 'reference' / '8bp_barcodes.csv')
    if not value:
        if required:
            raise HTTPException(400, "barcode_csv is required for this pipeline")
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise HTTPException(400, "barcode_csv must be an existing .csv file")
    if not is_under(path, barcode_roots()):
        raise HTTPException(400, "barcode_csv is outside the allowed roots")
    return path


def barcode_roots() -> list[Path]:
    return configured_roots('SCIGBLAST_ALLOWED_BARCODE_ROOTS', '/colddata') + [PIPELINE_ROOT / 'reference']


def validate_output(value: str | None) -> Path:
    raw = value.strip() if value else ""
    path = Path(raw).expanduser() if raw else DEFAULT_OUTPUT_ROOT
    if not path.is_absolute():
        raise HTTPException(400, "output_root must be an absolute server path")
    resolved = path.resolve()
    if not is_under(resolved, configured_roots("SCIGBLAST_ALLOWED_OUTPUT_ROOTS", str(DEFAULT_OUTPUT_ROOT))):
        raise HTTPException(400, "output_root is outside the allowed output roots")
    return resolved


def default_job_output(operator: str, pipeline: str) -> Path:
    # Preserve Chinese names, but never interpret a name as a filesystem path.
    name = re.sub(r"[^\w.-]+", "_", operator.strip(), flags=re.UNICODE).strip("._")[:60] or "operator"
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_ROOT / f"{name}_{pipeline}_{stamp}"


BROWSE_SPECS: dict[str, tuple[str, set[str]]] = {
    "input": ("SCIGBLAST_ALLOWED_INPUT_ROOTS", set()),
    "submission": ("SCIGBLAST_ALLOWED_SUBMISSION_ROOTS", {".xlsx"}),
    "barcode": ("SCIGBLAST_ALLOWED_BARCODE_ROOTS", {".csv"}),
    "output": ("SCIGBLAST_ALLOWED_OUTPUT_ROOTS", set()),
}


def browse_directory(kind: str, value: str | None) -> dict[str, Any]:
    """Return one safe directory level for the web file-tree picker.

    Only configured roots and their descendants are exposed. File contents are
    never returned; callers receive names and absolute server paths only.
    """
    if kind not in BROWSE_SPECS:
        raise HTTPException(400, "unsupported browse kind")
    env_name, suffixes = BROWSE_SPECS[kind]
    roots = barcode_roots() if kind == 'barcode' else configured_roots(env_name, "/colddata")
    if not roots:
        return {"kind": kind, "path": None, "parent": None, "roots": [], "entries": []}

    current: Path | None = None
    if value:
        requested = Path(value).expanduser()
        if not requested.is_absolute():
            raise HTTPException(400, "browse path must be an absolute server path")
        resolved = requested.resolve()
        if not is_under(resolved, roots):
            raise HTTPException(400, "browse path is outside the allowed roots")
        if not resolved.exists():
            raise HTTPException(400, f"browse path does not exist: {resolved}")
        if resolved.is_file():
            if resolved.suffix.lower() not in suffixes:
                raise HTTPException(400, "file type is not selectable for this field")
            current = resolved.parent
        elif resolved.is_dir():
            current = resolved
        else:
            raise HTTPException(400, "browse path is not a directory")

    if current is None:
        root_entries = [
            {
                "name": str(root),
                "path": str(root),
                "type": "directory",
                "selectable": kind != "barcode",
            }
            for root in roots
            if root.exists() and root.is_dir()
        ]
        return {"kind": kind, "path": None, "parent": None, "roots": [str(root) for root in roots], "entries": root_entries}

    entries: list[dict[str, Any]] = []
    try:
        children = sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
    except OSError as exc:
        raise HTTPException(403, f"cannot list browse path: {current}") from exc
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            resolved_child = child.resolve()
            if not is_under(resolved_child, roots):
                continue
            if child.is_dir():
                entries.append({"name": child.name, "path": str(resolved_child), "type": "directory", "selectable": kind != "barcode"})
            elif child.is_file() and child.suffix.lower() in suffixes:
                entries.append({"name": child.name, "path": str(resolved_child), "type": "file", "selectable": True, "size": child.stat().st_size})
        except OSError:
            continue

    parent = None
    if not any(current == root for root in roots):
        candidate_parent = current.parent.resolve()
        if is_under(candidate_parent, roots):
            parent = str(candidate_parent)
    return {
        "kind": kind,
        "path": str(current),
        "parent": parent,
        "roots": [str(root) for root in roots],
        "entries": entries,
        "can_select_current": kind != "barcode",
    }


def safe_dataset(value: str, input_path: Path) -> str:
    raw = value.strip() or input_path.name
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    if not result:
        raise HTTPException(400, "dataset_label is empty after sanitization")
    return result[:120]


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CreateJob(BaseModel):
    pipeline: str
    operator: str
    input_path: str
    submission_path: str = ""
    submission_revision: str = ""
    barcode_csv: str | None = None
    output_root: str | None = None
    dataset_label: str = ""
    ir_input_mode: str = "raw"
    ir_variant: str = "representative"
    run_preprocessing: str = "auto"


def build_job(request: CreateJob) -> tuple[dict[str, Any], dict[str, str]]:
    request.output_root = (request.output_root or '').strip() or None
    if request.pipeline not in REGISTRY:
        raise HTTPException(400, "unknown pipeline")
    config = REGISTRY[request.pipeline]
    input_path = validate_input_path(request.input_path, "input_path")
    try:
        submission_path = (submission.resolve(STATE_ROOT, request.submission_revision)
                           if request.submission_revision else validate_submission(request.submission_path))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    barcode = validate_barcode(request.barcode_csv, bool(config["requires_barcode"]))
    output_root = validate_output(request.output_root or str(default_job_output(request.operator, request.pipeline)))
    dataset = safe_dataset(request.dataset_label, input_path)
    if request.operator.strip() == "":
        raise HTTPException(400, "operator is required")
    if request.pipeline == "ir_split":
        if request.ir_input_mode not in {"raw", "presplit"}:
            raise HTTPException(400, "ir_input_mode must be raw or presplit")
        if request.ir_variant not in {"representative", "merged"}:
            raise HTTPException(400, "ir_variant must be representative or merged")
        if request.run_preprocessing not in {"auto", "0", "1"}:
            raise HTTPException(400, "run_preprocessing must be auto, 0, or 1")

    runner = (PIPELINE_ROOT / config["runner"]).resolve()
    if not runner.is_file() or not is_under(runner, [PIPELINE_ROOT]):
        raise HTTPException(500, f"runner is not available: {runner}")
    env: dict[str, str] = {
        "SCIGBLAST_MULTI_CHILD": "1",
        "SCIGBLAST_MATCH_ONLY_FIRST_RUN": "1",
        "SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN": "1",
        "SCIGBLAST_WEB_MATCH_ONLY": "1",
        "SCIGBLAST_RUN_RAW_INPUT_DIR": str(input_path),
        "SCIGBLAST_RUN_SUBMISSION_PATHS": str(submission_path),
        "SCIGBLAST_DATASET_LABEL": dataset,
        "SCIGBLAST_RUN_DATASET_LABEL": dataset,
        "SCIGBLAST_OUTPUT_ROOT": str(output_root),
        "SCIGBLAST_RUN_OUTPUT_ROOT": str(output_root),
    }
    if barcode:
        env["SCIGBLAST_RUN_BARCODE_CSV"] = str(barcode)
    if request.pipeline == "ir_split":
        env.update(
            {
                "SCIGBLAST_RUN_INPUT_MODE": request.ir_input_mode,
                "SCIGBLAST_IR_PIPELINE_VARIANT": request.ir_variant,
                "SCIGBLAST_RUN_PREPROCESSING": request.run_preprocessing,
            }
        )
    # Preserve the container's tool PATH and configured Python environment.
    env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    runner_hash = sha256_file(runner)
    job = {
        "id": uuid.uuid4().hex[:16],
        "operator": request.operator.strip(),
        "pipeline": request.pipeline,
        "dataset": dataset,
        "input_path": str(input_path),
        "submission_path": str(submission_path),
        "barcode_csv": str(barcode) if barcode else None,
        "output_root": str(output_root),
        "options_json": json.dumps(request.model_dump(), ensure_ascii=False),
        "env_json": json.dumps(env, ensure_ascii=False),
        "runner_path": str(runner),
        "runner_hash": runner_hash,
        "status": "QUEUED",
        "current_stage": "",
        "progress": 0,
        "attempt_no": 0,
        "created_at": now(),
    }
    return job, env


def insert_job(job: dict[str, Any]) -> None:
    columns = ",".join(job.keys())
    placeholders = ",".join("?" for _ in job)
    with db() as connection:
        connection.execute(
            f"INSERT INTO jobs({columns}) VALUES({placeholders})", tuple(job.values())
        )


def state_dir(row: sqlite3.Row) -> Path:
    return Path(row["output_root"]) / ".pipeline_state" / row["dataset"]


def log_path(row: sqlite3.Row) -> Path:
    return Path(row["output_root"]) / "logs" / row["dataset"] / "pipeline.log"


def review_ready(row: sqlite3.Row) -> bool:
    if json.loads(row["env_json"]).get("SCIGBLAST_WEB_MATCH_ONLY") != "1":
        return False
    marker = state_dir(row) / ".match_review.done"
    try:
        return (marker.is_file() and marker.stat().st_mtime >= datetime.fromisoformat(row['started_at']).timestamp()
                and "READY_FOR_REVIEW" in marker.read_text(encoding="utf-8", errors="replace")
                and bool(summary_files(row)))
    except OSError:
        return False


def pipeline_done(row: sqlite3.Row) -> bool:
    config = job_config(row)
    state = state_dir(row)
    if config["completion"] == "pipeline_done" and (state / ".pipeline.DONE").is_file():
        return True
    if config["completion"] == "last_stage":
        stage_names = config["stages"]
        if not stage_names:
            return False
        final = stage_names[-1]
        marker_name = final.split('.', 1)[1] if row['pipeline'] == 'pig_igblast' else final
        for marker in (state / f".pipeline_stage_{marker_name}.DONE", state / ".pipeline.DONE"):
            if marker.is_file() and "status=DONE" in marker.read_text(encoding="utf-8", errors="replace"):
                return True
    return False


def snapshot(row: sqlite3.Row) -> dict[str, Any]:
    config = job_config(row)
    state = state_dir(row)
    done = []
    for stage in config["stages"]:
        marker_name = stage.split('.', 1)[1] if row['pipeline'] == 'pig_igblast' else stage
        marker = state / f".pipeline_stage_{marker_name}.DONE"
        if marker.is_file() and "status=DONE" in marker.read_text(encoding="utf-8", errors="replace"):
            done.append(stage)
    if '01.match' not in done and summary_files(row) and (row['status'] in {'WAITING_REVIEW', 'SUCCEEDED'} or row['current_stage'] not in {'', None, '01.match'}):
        done.insert(0, '01.match')
    current = row["current_stage"] or (done[-1] if done else "")
    progress = 100 if row["status"] == "SUCCEEDED" else int(len(done) * 100 / len(config["stages"]))
    return {
        "id": row["id"],
        "operator": row["operator"],
        "pipeline": row["pipeline"],
        "pipeline_label": config["label"],
        "dataset": row["dataset"],
        "input_path": row["input_path"],
        "submission_path": row["submission_path"],
        "barcode_csv": row["barcode_csv"],
        "output_root": row["output_root"],
        "status": row["status"],
        "current_stage": current,
        "progress": progress,
        "stage_progress": int(row["progress"] or 0),
        "submission_revision": json.loads(row["options_json"]).get("submission_revision", ""),
        "stages": config["stages"],
        "stage_labels": config.get("stage_labels", {}),
        "completed_stages": done,
        "attempt_no": row["attempt_no"],
        "runner_path": row["runner_path"],
        "runner_hash": row["runner_hash"],
        "pid": row["pid"],
        "pgid": row["pgid"],
        "exit_code": row["exit_code"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "log_path": str(log_path(row)),
        "review_ready": review_ready(row),
        "pipeline_done": pipeline_done(row),
    }


def parse_progress(row: sqlite3.Row) -> tuple[str, int]:
    config = job_config(row)
    path = log_path(row)
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 250_000))
            text = handle.read(250_000).decode("utf-8", errors="replace")
    except OSError:
        return row["current_stage"] or "", int(row["progress"] or 0)
    current = row["current_stage"] or ""
    stage_end = 0
    for match in re.finditer(r"\[(?:IR|10X|BASE|PIG)\s+(\d+)/(\d+)\]", text):
        number, total = int(match.group(1)), int(match.group(2))
        if total:
            current = config["stages"][min(number - 1, len(config["stages"]) - 1)]
            stage_end = match.end()
    if row['pipeline'] == 'pig_igblast':
        for match in re.finditer(r'\[PIG\]\s+(match|fastp|clean|pandaseq|igblast)\b', text):
            current = next(s for s in config['stages'] if s.endswith('.' + match.group(1)))
            stage_end = match.end()
    progress = int(row["progress"] or 0)
    percentages = re.findall(r"(?:percent=(\d{1,3})|\[\s*\d+/\d+\s+(\d{1,3})(?:\.\d+)?%)", text[stage_end:])
    if percentages:
        progress = max(0, min(100, int(next(v for v in percentages[-1] if v))))
    elif current != row["current_stage"]:
        progress = 0
    return current, progress


def active_count() -> int:
    with PROCESS_LOCK:
        return len(PROCESSES)


def launch_job(job_id: str) -> bool:
    row = get_job_row(job_id)
    if row["status"] != "QUEUED" or job_id in PROCESSES:
        return False
    env = json.loads(row["env_json"])
    env = {str(key): str(value) for key, value in env.items()}
    env.update({key: value for key, value in os.environ.items() if key not in env})
    # Resumed jobs may have saved PATH from an older, host-mounted runtime.
    # Image-owned tools always follow the currently deployed image.
    if os.environ.get("SCIGBLAST_RUNTIME_BIN_DIR"):
        env["SCIGBLAST_RUNTIME_BIN_DIR"] = os.environ["SCIGBLAST_RUNTIME_BIN_DIR"]
        env["PATH"] = os.environ["PATH"]
    attempt = int(row["attempt_no"] or 0) + 1
    try:
        destination = STATE_ROOT / "jobs" / job_id
        destination.mkdir(parents=True, exist_ok=True)
        if env.get('SCIGBLAST_WEB_MATCH_ONLY') == '1':
            (state_dir(row) / '.match_review.done').unlink(missing_ok=True)
        with (destination / f"launcher-{attempt}.log").open("ab") as launcher:
            process = subprocess.Popen(
                ["bash", row["runner_path"]], cwd=str(Path(row["runner_path"]).parent),
                env=env, stdin=subprocess.DEVNULL, stdout=launcher, stderr=launcher,
                start_new_session=True,
            )
        pgid = process.pid
    except OSError as exc:
        update_job(job_id, status="FAILED", ended_at=now(), exit_code=127, last_error=str(exc))
        return False
    with PROCESS_LOCK:
        PROCESSES[job_id] = process
    update_job(job_id, status="RUNNING", attempt_no=attempt, pid=process.pid, pgid=pgid, started_at=now(), ended_at=None, exit_code=None, last_error=None)
    threading.Thread(target=watch_job, args=(job_id, process), daemon=True).start()
    return True


def schedule() -> None:
    with PROCESS_LOCK:
        with db() as connection:
            rows = connection.execute("SELECT id FROM jobs WHERE status='QUEUED' ORDER BY created_at,id").fetchall()
        for row in rows:
            if active_count() >= MAX_ACTIVE_JOBS:
                break
            launch_job(row["id"])


def watch_job(job_id: str, process: subprocess.Popen[bytes]) -> None:
    row = get_job_row(job_id)
    while process.poll() is None:
        current, progress = parse_progress(row)
        update_job(job_id, current_stage=current, progress=progress)
        time.sleep(2)
        row = get_job_row(job_id)
    exit_code = process.returncode
    # Do not release a stopped job's slot while its child processes are exiting.
    if job_id in STOP_REQUESTED:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.2)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    row = get_job_row(job_id)
    current, progress = parse_progress(row)
    stopped = job_id in STOP_REQUESTED
    if stopped:
        status = "STOPPED"
    elif review_ready(row):
        status = "WAITING_REVIEW"
    elif exit_code == 0 and pipeline_done(row):
        status = "SUCCEEDED"
    elif exit_code == 0:
        status = "COMPLETED_WITHOUT_MARKER"
    else:
        status = "FAILED"
    error = None if status in {"WAITING_REVIEW", "SUCCEEDED"} else f"runner exit={exit_code}"
    with PROCESS_LOCK:
        update_job(job_id, status=status, current_stage=current, progress=progress, ended_at=now(), exit_code=exit_code, pid=None, pgid=None, last_error=error)
        PROCESSES.pop(job_id, None)
        STOP_REQUESTED.discard(job_id)
    schedule()


def mark_interrupted_jobs() -> None:
    with db() as connection:
        connection.execute(
            "UPDATE jobs SET status='INTERRUPTED', ended_at=?, last_error='web container restarted' WHERE status IN ('RUNNING','MATCHING','STOPPING')",
            (now(),),
        )


def summary_files(row: sqlite3.Row) -> list[str]:
    result = []
    for candidate in REGISTRY[row["pipeline"]]["match_summary"]:
        path = Path(row["output_root"]) / candidate.format(dataset=row["dataset"])
        if path.is_file() and is_under(path, [Path(row['output_root'])]):
            result.append(str(path))
    return result


def job_config(row):
    config = dict(REGISTRY[row["pipeline"]])
    config["stages"] = list(config["stages"])
    options = json.loads(row["options_json"])
    if row["pipeline"] == "ir_split":
        if options.get("ir_variant") == "merged":
            config["stages"] = ["01.match", "02.fastp", "03.split", "04.clean", "05.pandaseq", "06.igblast"]
            config["stage_labels"] = {**config.get("stage_labels", {}), "06.igblast": "IgBLAST"}
        elif options.get("run_preprocessing") == "0":
            config["stages"].remove("08.preprocessing")
    return config


def result_file(row):
    options = json.loads(row["options_json"])
    stage = "05.igblastn_out"
    if row["pipeline"] in {"ir_split", "10x_split"}:
        stage = "06.igblastn_out" if options.get("ir_variant") == "merged" else "07.igblastn_out"
    root = Path(row["output_root"])
    for name in ("chain_summary.csv", "igblast_summary.tsv"):
        path = root / stage / row["dataset"] / name
        if path.is_file() and is_under(path, [root]):
            return path
    return None


def stage_reports(row):
    root, dataset = Path(row['output_root']), row['dataset']
    entries = [('fastp', 'Fastp 质量过滤', '输入、保留 reads 及保留比例',
                [root / '02.fastp' / dataset / 'report/fastp_summary.csv'])]
    if row['pipeline'] == 'ir_split':
        entries.append(('split', 'IR Barcode / UMI 拆分', '匹配、丢弃与保留序列数及百分比',
                        [root / '03.IR_split_output' / dataset / 'ir_split_summary.csv']))
    if row['pipeline'] == '10x_split':
        entries.extend([
            ('prefilter', '10X R1 / R2 预筛选', 'TSO 与 Barcode 筛选结果',
             [root / '03.prefilter_data' / dataset / 'r1_r2_prefilter_summary.csv']),
            ('split', '10X Barcode / UMI 拆分', '拆分样本及序列统计',
             [root / '06.split_output' / dataset / 'all_samples_summary.csv']),
            ('representative', '10X 代表序列', 'UMI 分组与代表序列统计',
             [root / '06.split_output' / dataset / 'representative_summary.csv'])])
    panda = '05.pandaseq' if row['pipeline'] in {'ir_split', '10x_split'} else '04.pandaseq'
    entries.append(('pandaseq', 'PANDAseq 序列合并', '输入、合并成功数及合并比例',
                    [root / panda / dataset / 'pandaseq_summary.csv']))
    entries.append(('results', 'IgBLAST 比对与筛选', '匹配统计与 productive 筛选后统计', [result_file(row)]))
    reports = []
    for kind, label, description, candidates in entries:
        path = next((p for p in candidates if p and p.is_file() and is_under(p, [root])), None)
        reports.append({'kind': kind, 'label': label, 'description': description,
                        'path': str(path) if path else None, 'exists': bool(path)})
    return reports


def stage_summary_file(row, kind):
    if kind == 'match':
        return next(iter(summary_files(row)), None)
    report = next((r for r in stage_reports(row) if r['kind'] == kind), None)
    return Path(report['path']) if report and report['path'] else None


def read_table(path: Path, offset=0, limit=50, query="", errors_only=False):
    rows, counts, matched = [], {"total": 0, "ok": 0, "error": 0}, 0
    pairs, samples = set(), set()
    note = ''
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t" if path.suffix == ".tsv" else ",")
        for index, item in enumerate(reader):
            if not any(str(v or "").strip() for v in item.values()):
                note = ''
                continue
            if 'note' in item:
                note = item.get('note') or note
                item['note'] = note
            ok = str(item.get("status", "")).upper() == "OK"
            counts["total"] += 1
            counts["ok" if ok else "error"] += 1
            pair = item.get("r1_path") or item.get("pair_id") or re.sub(r"_R[12](?=\.)", "", item.get("file_path", ""))
            if pair:
                pairs.add(pair)
                if ok:
                    samples.add((pair, item.get("sample_id", "")))
            if errors_only and ok:
                continue
            if query and query.casefold() not in " ".join(str(v or "") for v in item.values()).casefold():
                continue
            if max(0, offset) <= matched < max(0, offset) + min(200, max(1, limit)):
                rows.append({**item, "_row_key": str(index)})
            matched += 1
    return {"path": str(path), "columns": reader.fieldnames or [], "rows": rows,
            "counts": {**counts, "file_pairs": len(pairs), "matched_samples": len(samples)},
            "total": matched, "offset": max(0, offset)}


app = FastAPI(title="SCigblast Pipeline Runner", version="0.1.0")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()
    mark_interrupted_jobs()
    schedule()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str) -> HTMLResponse:
    get_job_row(job_id)
    return templates.TemplateResponse("job.html", {"request": request, "job_id": job_id})


@app.get("/health")
def health() -> dict[str, Any]:
    with db() as connection:
        queued = connection.execute("SELECT COUNT(*) FROM jobs WHERE status='QUEUED'").fetchone()[0]
    return {"status": "ok", "pipelines": list(REGISTRY), "active_jobs": active_count(), "queued_jobs": queued, "max_active_jobs": MAX_ACTIVE_JOBS}


@app.get("/api/pipelines")
def pipelines() -> dict[str, Any]:
    return {
        key: {
            "label": value["label"],
            "description": value.get("description", ""),
            "accent": value.get("accent", "teal"),
            "requires_barcode": value["requires_barcode"],
            "ir_options": value["ir_options"],
            "stages": value["stages"],
            "stage_labels": value.get("stage_labels", {}),
            "form_fields": value.get("form_fields", []),
        }
        for key, value in REGISTRY.items()
    }


@app.get('/api/defaults')
def form_defaults():
    return {'pipeline_root': str(PIPELINE_ROOT),
            'output_base': str(DEFAULT_OUTPUT_ROOT),
            'barcode_csv': str(PIPELINE_ROOT / 'reference' / '8bp_barcodes.csv')}


@app.get('/api/preflight')
def preflight(pipeline: str):
    if pipeline not in REGISTRY:
        raise HTTPException(400, 'Unknown pipeline')
    config_path = PIPELINE_ROOT / Path(REGISTRY[pipeline]['runner']).parent / '00.pipeline_config.env'
    settings = {}
    if config_path.is_file():
        for line in config_path.read_text(encoding='utf-8').splitlines():
            match = re.match(r'^(PYTHON_BIN|SCIGBLAST_PANDASEQ_BIN|SCIGBLAST_IGBLAST_BIN|IGBLAST_BIN)=(.*)$', line)
            if match:
                value = shlex.split(match[2], comments=True)
                if len(value) == 1:
                    settings[match[1]] = value[0]
    commands = {'python': settings.get('PYTHON_BIN', 'python3'), 'fastp': 'fastp',
                'pandaseq': settings.get('SCIGBLAST_PANDASEQ_BIN', 'pandaseq'),
                'igblastn': settings.get('IGBLAST_BIN', settings.get('SCIGBLAST_IGBLAST_BIN', 'igblastn'))}
    if os.environ.get('SCIGBLAST_RUNTIME_BIN_DIR'):
        runtime = Path(os.environ['SCIGBLAST_RUNTIME_BIN_DIR'])
        commands = {name: str(runtime / ('python3' if name == 'python' else name)) for name in commands}
    checks = {name: shutil.which(command) for name, command in commands.items()}
    modules = ['openpyxl']
    if pipeline in {'ir_split', '10x_split'}:
        modules += ['Bio', 'pandas', 'numpy']
    if pipeline == 'ir_split':
        modules += ['parmap', 'skbio']
    missing = []
    if checks['python']:
        code = 'import importlib.util,json; print(json.dumps([m for m in ' + repr(modules) + ' if importlib.util.find_spec(m) is None]))'
        try:
            result = subprocess.run([checks['python'], '-c', code], capture_output=True, text=True, timeout=15)
            missing = json.loads(result.stdout) if result.returncode == 0 else ['Python 环境检查失败']
        except (OSError, subprocess.TimeoutExpired, ValueError):
            missing = ['Python 环境检查失败']
    return {'commands': checks, 'missing_modules': missing, 'max_active_jobs': MAX_ACTIVE_JOBS,
            'notice': '仅检查入口工具和 Python 库；数据库路径及 cgroup 内存仍需在 Linux 部署验收。300g 是整个容器共享上限。'}


@app.get("/api/browse")
def browse(kind: str = "input", path: str | None = None) -> dict[str, Any]:
    """List a single safe directory level for the path picker."""
    return browse_directory(kind, path)


@app.post("/api/submissions/import")
def import_submission(request: dict):
    path = validate_submission(str(request.get("path", "")))
    files = sorted(p for p in path.glob("*.xlsx") if not p.name.startswith("~$")) if path.is_dir() else [path]
    if len(files) > 50:
        raise HTTPException(400, "Select at most 50 workbooks")
    try:
        return submission.create(STATE_ROOT, files, str(path))
    except Exception as exc:
        raise HTTPException(400, f"Cannot read workbook: {exc}") from exc


@app.post("/api/submissions/upload")
async def upload_submission(request: Request, filename: str = "submission.xlsx"):
    if Path(filename).suffix.lower() != ".xlsx":
        raise HTTPException(400, "Only .xlsx is supported")
    with tempfile.TemporaryDirectory(prefix="scigblast-upload-") as folder:
        path = Path(folder) / "submission.xlsx"
        total = 0
        with path.open("wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > submission.MAX_BYTES:
                    raise HTTPException(413, "XLSX exceeds 20 MB")
                handle.write(chunk)
        try:
            return submission.create(STATE_ROOT, [path], filename)
        except Exception as exc:
            raise HTTPException(400, f"Invalid XLSX: {exc}") from exc


@app.get("/api/submissions")
def get_submission(revision: str):
    try:
        return submission.inspect(STATE_ROOT, revision)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/submissions/revise")
def revise_submission(request: dict):
    try:
        changes = request.get("changes", [])
        old = submission.inspect(STATE_ROOT, request["revision"])
        notes = {(s["file"], s["sheet"], c["cell"]) for s in old["sheets"] for r in s["rows"]
                 for c in r["editable"] if c["kind"] == "note"}
        for change in changes:
            if (change["file"], change["sheet"], change["cell"]) in notes:
                for value in re.split(r"[\r\n;,]+", change["value"]):
                    if value.strip():
                        validate_input_path(value.strip(), "Note")
        return submission.revise(STATE_ROOT, request["revision"], changes)
    except (KeyError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/submissions/download")
def download_submission(revision: str, filename: str, original: bool = False):
    try:
        folder = submission.resolve(STATE_ROOT, revision)
        if original:
            metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
            while metadata.get('parent'):
                folder = submission.resolve(STATE_ROOT, metadata['parent'])
                metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if filename not in [p.name for p in folder.glob("*.xlsx")]:
        raise HTTPException(404, "Workbook not found")
    return FileResponse(folder / filename, filename=filename)


@app.post("/api/jobs/{job_id}/rematch")
def rematch(job_id: str, request: dict):
    with PROCESS_LOCK:
        row = get_job_row(job_id)
        if row["status"] in {"QUEUED", "RUNNING", "MATCHING", "STOPPING"}:
            raise HTTPException(409, "Wait for this attempt to finish")
        token = request.get("revision", "")
        try:
            folder = submission.resolve(STATE_ROOT, token)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        options = json.loads(row["options_json"])
        # Re-match is safe; confirmation below prevents changing any previously
        # approved assignment while allowing formerly ERROR records to be fixed.
        options["submission_revision"] = token
        env = json.loads(row["env_json"])
        env.update(SCIGBLAST_RUN_SUBMISSION_PATHS=str(folder), SCIGBLAST_WEB_MATCH_ONLY="1", SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN="1")
        update_job(job_id, status="QUEUED", submission_path=str(folder), options_json=json.dumps(options), env_json=json.dumps(env), last_error=None)
        add_action(job_id, row["operator"], "rematch", token)
    schedule()
    return snapshot(get_job_row(job_id))


@app.post("/api/jobs/{job_id}/review")
def annotate_match(job_id: str, request: dict):
    with PROCESS_LOCK:
        row = get_job_row(job_id)
        result = match_preview(job_id)
        if request.get("revision") != result.get("revision") or row["status"] != "WAITING_REVIEW":
            raise HTTPException(409, "Review is not current")
        label = request.get("label", "")
        if label not in {"", "已核对", "待补资料"}:
            raise HTTPException(400, "Invalid review label")
        keys = request.get('row_keys', [request.get('row_key')])
        if not isinstance(keys, list) or not 1 <= len(keys) <= 5000 or any(not isinstance(k, str) or not k.isdecimal() for k in keys):
            raise HTTPException(400, 'Select between 1 and 5000 valid rows')
        keys = set(keys)
        with Path(result['path']).open(encoding='utf-8-sig', newline='') as handle:
            valid = {str(i) for i, record in enumerate(csv.DictReader(handle))
                     if any(str(v or '').strip() for v in record.values())}
        if not keys <= valid:
            raise HTTPException(400, 'Selection contains rows not in this Match revision')
        with db() as connection:
            connection.executemany(
                'INSERT INTO reviews VALUES(?,?,?,?,?) ON CONFLICT(job_id,revision,row_key) '
                'DO UPDATE SET label=excluded.label, note=CASE WHEN ? THEN excluded.note ELSE reviews.note END',
                [(job_id, request['revision'], key, label, str(request.get('note', ''))[:2000], 'note' in request) for key in sorted(keys)])
        add_action(job_id, row["operator"], "review", json.dumps(request, ensure_ascii=False))
    return {"ok": True, "updated": len(keys)}


@app.get("/api/jobs/{job_id}/results")
def results(job_id: str, offset: int = 0, limit: int = 50, query: str = "", errors_only: bool = False):
    path = result_file(get_job_row(job_id))
    return read_table(path, offset, limit, query, errors_only) if path else {"path": None, "rows": [], "columns": [], "total": 0}


@app.get('/api/jobs/{job_id}/stage-summary')
def stage_summary(job_id: str, kind: str, offset: int = 0, limit: int = 50, query: str = ''):
    path = stage_summary_file(get_job_row(job_id), kind)
    if not path:
        return {'path': None, 'rows': [], 'columns': [], 'total': 0}
    return read_table(Path(path), offset, limit, query)


@app.get("/api/jobs/{job_id}/download")
def download_result(job_id: str, kind: str = "results"):
    row = get_job_row(job_id)
    path = stage_summary_file(row, kind)
    if path is None or not is_under(Path(path), [Path(row["output_root"])]):
        raise HTTPException(404, "Summary not available")
    return FileResponse(path, filename=Path(path).name)


@app.post("/api/jobs")
def create_job(request: CreateJob) -> dict[str, Any]:
    if not request.submission_revision:
        source = validate_submission(request.submission_path)
        files = sorted(source.glob("*.xlsx")) if source.is_dir() else [source]
        try:
            request.submission_revision = submission.create(STATE_ROOT, files, str(source))["revision"]
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
    job, _ = build_job(request)
    with PROCESS_LOCK:
        if not request.output_root:
            base = Path(job['output_root'])
            candidate, number = base, 1
            with db() as connection:
                while candidate.exists() or connection.execute('SELECT 1 FROM jobs WHERE output_root=?', (str(candidate),)).fetchone():
                    number += 1
                    candidate = base.with_name(f'{base.name}_{number:02d}')
            job['output_root'] = str(candidate)
            env = json.loads(job['env_json'])
            env.update(SCIGBLAST_OUTPUT_ROOT=str(candidate), SCIGBLAST_RUN_OUTPUT_ROOT=str(candidate))
            job['env_json'] = json.dumps(env)
        with db() as connection:
            collision = connection.execute("SELECT id FROM jobs WHERE output_root=? AND dataset=?", (job["output_root"], job["dataset"])).fetchone()
        if collision:
            raise HTTPException(409, f"Output/dataset already belongs to job {collision['id']}; use its rematch/resume or choose another output")
        insert_job(job)
    add_action(job["id"], job["operator"], "create", json.dumps(request.model_dump(), ensure_ascii=False))
    schedule()
    return snapshot(get_job_row(job["id"]))


@app.post('/api/jobs/{job_id}/delete')
def delete_job(job_id: str, request: dict):
    """Delete only an inactive job's exclusive output, never an input or shared root."""
    with PROCESS_LOCK:
        row = get_job_row(job_id)
        if request.get('output_root') != row['output_root'] or request.get('confirm') != job_id:
            raise HTTPException(400, '请确认任务和将删除的输出目录')
        if row['status'] in {'QUEUED', 'RUNNING', 'MATCHING', 'STOPPING'} or job_id in PROCESSES:
            raise HTTPException(409, '请先停止任务，等待进程退出后再删除')
        # A restarted server may have marked a still-live process INTERRUPTED.
        if row['pgid'] and hasattr(os, 'killpg'):
            try:
                os.killpg(row['pgid'], 0)
            except ProcessLookupError:
                pass
            except PermissionError:
                raise HTTPException(409, '无法确认任务进程已退出，暂不允许删除')
            else:
                raise HTTPException(409, '任务进程组仍存在，暂不允许删除')
        raw = Path(row['output_root'])
        output = validate_output(str(raw))
        if raw.absolute() != output or any(p.is_symlink() for p in (raw, *raw.parents)):
            raise HTTPException(409, '输出路径包含链接，禁止递归删除')
        roots = configured_roots('SCIGBLAST_ALLOWED_OUTPUT_ROOTS', str(DEFAULT_OUTPUT_ROOT))
        if output in [*roots, DEFAULT_OUTPUT_ROOT, Path(output.anchor), PIPELINE_ROOT, STATE_ROOT]:
            raise HTTPException(409, '不能删除公共输出根目录；请使用每任务独立目录')
        with db() as connection:
            all_rows = connection.execute('SELECT * FROM jobs').fetchall()
        for other in all_rows:
            if other['id'] != job_id:
                other_output = Path(other['output_root']).resolve()
                if is_under(output, [other_output]) or is_under(other_output, [output]):
                    raise HTTPException(409, '该输出目录与其他任务共享或嵌套，不能整目录删除')
            options = json.loads(other['options_json'])
            for value in (other['input_path'], other['submission_path'], other['barcode_csv'], options.get('submission_path')):
                if value:
                    protected = Path(value).resolve()
                    if is_under(protected, [output]) or is_under(output, [protected]):
                        raise HTTPException(409, '输出与原始数据或 Submission 路径重叠，禁止删除')
        if is_under(STATE_ROOT, [output]) or is_under(PIPELINE_ROOT, [output]):
            raise HTTPException(409, '输出包含程序或任务数据库，禁止删除')
        if output.exists() and (not output.is_dir() or (
                any(output.iterdir()) and not (output / '01.match' / row['dataset']).is_dir()
                and not state_dir(row).is_dir())):
            raise HTTPException(409, '未找到该任务的阶段目录，无法安全确认输出归属')
        job_state = STATE_ROOT / 'jobs' / job_id
        if job_state.is_symlink() or not is_under(job_state, [STATE_ROOT / 'jobs']):
            raise HTTPException(409, '任务日志目录归属异常')
        try:
            if output.exists():
                shutil.rmtree(output)
            if job_state.exists():
                shutil.rmtree(job_state)
        except OSError as exc:
            raise HTTPException(500, f'删除未完成，任务记录已保留，可检查权限后重试：{exc}') from exc
        with db() as connection:
            connection.execute('DELETE FROM reviews WHERE job_id=?', (job_id,))
            connection.execute('DELETE FROM actions WHERE job_id=?', (job_id,))
            connection.execute('DELETE FROM jobs WHERE id=?', (job_id,))
        STOP_REQUESTED.discard(job_id)
        return {'deleted': job_id, 'output_root': str(output)}


@app.post("/api/validate")
def validate_job(request: CreateJob) -> dict[str, Any]:
    """Validate form paths without creating a job or starting a runner."""
    job, _ = build_job(request)
    return {
        "valid": True,
        "pipeline": job["pipeline"],
        "dataset": job["dataset"],
        "input_path": job["input_path"],
        "submission_path": job["submission_path"],
        "barcode_csv": job["barcode_csv"],
        "output_root": job["output_root"],
        "runner_path": job["runner_path"],
    }


@app.get("/api/jobs")
def list_jobs(
    status: str | None = None,
    pipeline: str | None = None,
    operator: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        values = [item.strip().upper() for item in status.split(",") if item.strip()]
        if values:
            clauses.append("status IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
    if pipeline:
        clauses.append("pipeline = ?")
        params.append(pipeline)
    if operator:
        clauses.append("operator LIKE ?")
        params.append(f"%{operator}%")
    if query:
        clauses.append("(id LIKE ? OR dataset LIKE ? OR input_path LIKE ?)")
        params.extend([f"%{query}%"] * 3)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with db() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM jobs{where}", tuple(params)).fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        stats = connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status IN ('QUEUED','RUNNING','MATCHING','STOPPING') THEN 1 ELSE 0 END) AS active, "
            "SUM(CASE WHEN status = 'WAITING_REVIEW' THEN 1 ELSE 0 END) AS review, "
            "SUM(CASE WHEN status IN ('SUCCEEDED','FAILED','STOPPED','INTERRUPTED','COMPLETED_WITHOUT_MARKER') THEN 1 ELSE 0 END) AS done "
            "FROM jobs"
        ).fetchone()
    counts = {key: int(stats[key] or 0) for key in ("total", "active", "review", "done")}
    return {"jobs": [snapshot(row) for row in rows], "total": total, "limit": limit, "offset": offset, "counts": counts, "active_jobs": active_count(), "max_active_jobs": MAX_ACTIVE_JOBS}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    row = get_job_row(job_id)
    options = json.loads(row['options_json'])
    options.pop('approved_assignments', None)
    return {"job": snapshot(row), "options": options, "actions": actions_for(job_id), "match_summary": summary_files(row)}


def actions_for(job_id: str) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute("SELECT operator, action, details, created_at FROM actions WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str, offset: int = 0, max_bytes: int = 262144, source: str = '') -> JSONResponse:
    row = get_job_row(job_id)
    path = log_path(row)
    if not path.is_file():
        path = STATE_ROOT / "jobs" / job_id / f"launcher-{row['attempt_no']}.log"
    try:
        size = path.stat().st_size
        identity = f"{row['attempt_no']}:{path}"
        reset = source != identity or offset < 0 or offset > size
        read_from = 0 if reset else offset
        max_bytes = max(4096, min(max_bytes, 1_048_576))
        with path.open("rb") as handle:
            handle.seek(read_from)
            payload = handle.read(max_bytes)
        next_offset = read_from + len(payload)
        content = payload.decode("utf-8", errors="replace")
    except OSError:
        return JSONResponse({"content": "pipeline log not created yet\n", "next_offset": 0, "reset": True, "size": 0})
    return JSONResponse({"content": content, "next_offset": next_offset, "reset": reset, "size": size, "source": identity})


@app.get("/api/jobs/{job_id}/match-preview")
def match_preview(job_id: str, offset: int = 0, limit: int = 50, query: str = "", errors_only: bool = False) -> dict[str, Any]:
    row = get_job_row(job_id)
    files = summary_files(row)
    if not files:
        return {"path": None, "columns": [], "rows": []}
    path = Path(files[0])
    try:
        result = read_table(path, offset, limit, query, errors_only)
        result["revision"] = f"{row['attempt_no']}:{sha256_file(path)}"
        options = json.loads(row['options_json'])
        current = matched_assignments(path)
        previous = set(options.get('approved_assignments', []))
        result['changes'] = {'new_ok_records': len(current - previous), 'removed_or_changed_ok_records': len(previous - current)}
        with db() as connection:
            annotations = connection.execute("SELECT * FROM reviews WHERE job_id=? AND revision=?", (job_id, result["revision"])).fetchall()
        result["reviews"] = {r["row_key"]: {"label": r["label"], "note": r["note"]} for r in annotations}
        return result
    except (OSError, csv.Error) as exc:
        raise HTTPException(500, f"cannot read match summary: {exc}") from exc


@app.post("/api/jobs/{job_id}/confirm-match")
def confirm_match(job_id: str, request: dict) -> dict[str, Any]:
    with PROCESS_LOCK:
        row = get_job_row(job_id)
        if row["status"] != "WAITING_REVIEW":
            raise HTTPException(409, "job is not waiting for match confirmation")
        match = match_preview(job_id)
        if request.get("revision") != match.get("revision"):
            raise HTTPException(409, "Match changed; reload and review again")
        if not match.get("counts", {}).get("ok"):
            raise HTTPException(409, "No usable matched records")
        assignments = matched_assignments(Path(match['path']))
        options = json.loads(row['options_json'])
        if not set(options.get('approved_assignments', [])).issubset(assignments):
            raise HTTPException(409, 'Previously approved sample assignments changed. Use a new output to avoid reusing stale results.')
        options['approved_assignments'] = sorted(assignments)
        env = json.loads(row["env_json"])
        env.update(SCIGBLAST_WEB_MATCH_ONLY="0", SCIGBLAST_RUN_MATCH_ONLY_FIRST_RUN="0")
        update_job(job_id, status="QUEUED", last_error=None, env_json=json.dumps(env), options_json=json.dumps(options))
        add_action(job_id, row["operator"], "confirm-match", match["revision"])
    schedule()
    return snapshot(get_job_row(job_id))


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict[str, Any]:
    with PROCESS_LOCK:
        row = get_job_row(job_id)
        if row["status"] not in {"FAILED", "STOPPED", "INTERRUPTED", "COMPLETED_WITHOUT_MARKER"}:
            raise HTTPException(409, "Use Match review to confirm this job")
        update_job(job_id, status="QUEUED", last_error=None, ended_at=None)
    add_action(job_id, row["operator"], "resume")
    schedule()
    return snapshot(get_job_row(job_id))


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, Any]:
    with PROCESS_LOCK:
        row = get_job_row(job_id)
        if row["status"] == "QUEUED":
            update_job(job_id, status="STOPPED", ended_at=now(), last_error="stopped before start")
        elif row["status"] in {"RUNNING", "MATCHING"}:
            process = PROCESSES.get(job_id)
            STOP_REQUESTED.add(job_id)
            update_job(job_id, status="STOPPING")
            if process:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                threading.Thread(target=finish_stop, args=(job_id, process), daemon=True).start()
        else:
            raise HTTPException(409, "job is not running")
    add_action(job_id, row["operator"], "stop")
    return snapshot(get_job_row(job_id))


def finish_stop(job_id, process):
    # Only the process group created for this attempt; never scan other jobs.
    time.sleep(15)
    with PROCESS_LOCK:
        if PROCESSES.get(job_id) is process:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def matched_assignments(path):
    fields = ('sample_id', 'file_path', 'r1_path', 'r2_path', 'pair_id', 'barcode_name',
              'barcode_sequence', 'igblast_chains', 'chains', 'chain', 'species')
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return {json.dumps([r.get(k, '') for k in fields], ensure_ascii=False)
                for r in csv.DictReader(handle) if r.get('status') == 'OK'}


@app.get('/api/jobs/{job_id}/metadata-review')
def metadata_review(job_id: str):
    row = get_job_row(job_id)
    token = json.loads(row['options_json']).get('submission_revision')
    if not token:
        return {'rows': []}
    files = summary_files(row)
    known = set()
    if files:
        with Path(files[0]).open(encoding='utf-8-sig', newline='') as handle:
            known = {r.get('sample_id') for r in csv.DictReader(handle) if r.get('sample_id')}
    missing = []
    raw = row['input_path'].replace('\\', '/').rstrip('/')
    for sheet in submission.inspect(STATE_ROOT, token)['sheets']:
        for record in sheet['rows']:
            fields = {cell['kind']: value for cell, value in zip(record['editable'], record['values']) if cell['kind']}
            notes = [p.strip().replace('\\', '/').rstrip('/') for p in re.split(r'[\r\n;,]+', fields.get('note', '')) if p.strip()]
            if notes and not any(raw == p or raw.startswith(p + '/') or p.startswith(raw + '/') for p in notes):
                continue
            sample = fields.get('sample_id', '')
            if sample and sample not in known:
                missing.append({'sample_id': sample, 'note': fields.get('note', ''), 'file': sheet['file'], 'sheet': sheet['sheet'], 'row': record['row'], 'reason': '提交表已登记，但匹配清单未识别该样本；请核对原文件是否存在以及文件名/索引'})
    return {'rows': missing}


@app.get("/api/jobs/{job_id}/artifacts")
def artifacts(job_id: str) -> dict[str, Any]:
    row = get_job_row(job_id)
    output = Path(row["output_root"])
    candidates = [
        output / "01.match" / row["dataset"],
        output / "logs" / row["dataset"] / "pipeline.log",
        output / ".pipeline_state" / row["dataset"],
    ]
    final_summary = result_file(row)
    if final_summary:
        candidates.append(final_summary)
    return {
        "output_root": str(output),
        "reports": stage_reports(row),
        "files": [{"path": str(path), "exists": path.exists(), "is_dir": path.is_dir(), "size": path.stat().st_size if path.is_file() else None} for path in candidates],
    }
