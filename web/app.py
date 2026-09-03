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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel


APP_DIR = Path(__file__).resolve().parent
PIPELINE_ROOT = Path(os.environ.get("SCIGBLAST_PIPELINE_ROOT", "/opt/scigblast")).resolve()
STATE_ROOT = Path(os.environ.get("SCIGBLAST_STATE_DIR", "/var/lib/scigblast-web")).resolve()
DB_PATH = STATE_ROOT / "scigblast.sqlite3"
DEFAULT_OUTPUT_ROOT = Path(
    os.environ.get("SCIGBLAST_DEFAULT_OUTPUT_ROOT", "/colddata/zqy/SCigblast/results/web_output")
).resolve()
MAX_ACTIVE_JOBS = max(1, int(os.environ.get("SCIGBLAST_MAX_ACTIVE_JOBS", "2")))


def load_registry() -> dict[str, dict[str, Any]]:
    with (APP_DIR / "pipeline_registry.json").open(encoding="utf-8") as handle:
        return json.load(handle)


REGISTRY = load_registry()
PROCESS_LOCK = threading.RLock()
PROCESSES: dict[str, subprocess.Popen[bytes]] = {}
STOP_REQUESTED: set[str] = set()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


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
    if not value:
        if required:
            raise HTTPException(400, "barcode_csv is required for this pipeline")
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise HTTPException(400, "barcode_csv must be an existing .csv file")
    if not is_under(path, configured_roots("SCIGBLAST_ALLOWED_BARCODE_ROOTS", "/colddata")):
        raise HTTPException(400, "barcode_csv is outside the allowed roots")
    return path


def validate_output(value: str | None) -> Path:
    raw = value.strip() if value else ""
    path = Path(raw).expanduser() if raw else DEFAULT_OUTPUT_ROOT
    if not path.is_absolute():
        raise HTTPException(400, "output_root must be an absolute server path")
    resolved = path.resolve()
    if not is_under(resolved, configured_roots("SCIGBLAST_ALLOWED_OUTPUT_ROOTS", str(DEFAULT_OUTPUT_ROOT))):
        raise HTTPException(400, "output_root is outside the allowed output roots")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


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
    roots = configured_roots(env_name, "/colddata")
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
    submission_path: str
    barcode_csv: str | None = None
    output_root: str | None = None
    dataset_label: str = ""
    ir_input_mode: str = "raw"
    ir_variant: str = "representative"
    run_preprocessing: str = "auto"


def build_job(request: CreateJob) -> tuple[dict[str, Any], dict[str, str]]:
    if request.pipeline not in REGISTRY:
        raise HTTPException(400, "unknown pipeline")
    config = REGISTRY[request.pipeline]
    input_path = validate_input_path(request.input_path, "input_path")
    submission_path = validate_submission(request.submission_path)
    barcode = validate_barcode(request.barcode_csv, bool(config["requires_barcode"]))
    output_root = validate_output(request.output_root)
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
    marker = state_dir(row) / ".match_review.done"
    try:
        return marker.is_file() and "READY_FOR_REVIEW" in marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def pipeline_done(row: sqlite3.Row) -> bool:
    config = REGISTRY[row["pipeline"]]
    state = state_dir(row)
    if config["completion"] == "pipeline_done" and (state / ".pipeline.DONE").is_file():
        return True
    if config["completion"] == "last_stage":
        stage_names = config["stages"]
        if not stage_names:
            return False
        final = stage_names[-1]
        for marker in (state / f".pipeline_stage_{final}.DONE", state / ".pipeline.DONE"):
            if marker.is_file() and "status=DONE" in marker.read_text(encoding="utf-8", errors="replace"):
                return True
    return False


def snapshot(row: sqlite3.Row) -> dict[str, Any]:
    config = REGISTRY[row["pipeline"]]
    state = state_dir(row)
    done = []
    for stage in config["stages"]:
        marker = state / f".pipeline_stage_{stage}.DONE"
        if marker.is_file() and "status=DONE" in marker.read_text(encoding="utf-8", errors="replace"):
            done.append(stage)
    current = row["current_stage"] or (done[-1] if done else "")
    progress = int(row["progress"] or 0)
    if not progress and done:
        progress = int(len(done) * 100 / len(config["stages"]))
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
    config = REGISTRY[row["pipeline"]]
    path = log_path(row)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[-250_000:]
    except OSError:
        return row["current_stage"] or "", int(row["progress"] or 0)
    current = row["current_stage"] or ""
    for match in re.finditer(r"\[(?:IR|10X|BASE|PIG)(?:[^\]]*)?(\d+)/(\d+)\]", text):
        number, total = int(match.group(1)), int(match.group(2))
        if total:
            current = config["stages"][min(number - 1, len(config["stages"]) - 1)]
    progress = int(row["progress"] or 0)
    percentages = re.findall(r"(?:progress|worker_done|\[\s*\d+/\d+)\D{0,20}(\d{1,3})%", text)
    if percentages:
        progress = max(0, min(100, int(percentages[-1])))
    elif current in config["stages"]:
        progress = max(progress, int(config["stages"].index(current) * 100 / len(config["stages"])))
    return current, progress


def active_count() -> int:
    return sum(1 for process in PROCESSES.values() if process.poll() is None)


def launch_job(job_id: str) -> bool:
    row = get_job_row(job_id)
    env = json.loads(row["env_json"])
    env = {str(key): str(value) for key, value in env.items()}
    env.update({key: value for key, value in os.environ.items() if key not in env})
    attempt = int(row["attempt_no"] or 0) + 1
    try:
        process = subprocess.Popen(
            ["bash", row["runner_path"]],
            cwd=str(Path(row["runner_path"]).parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pgid = os.getpgid(process.pid)
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
        slots = max(0, MAX_ACTIVE_JOBS - active_count())
    if slots <= 0:
        return
    with db() as connection:
        rows = connection.execute(
            "SELECT id FROM jobs WHERE status = 'QUEUED' ORDER BY created_at"
        ).fetchall()
    for row in rows[:slots]:
        with PROCESS_LOCK:
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
    row = get_job_row(job_id)
    current, progress = parse_progress(row)
    with PROCESS_LOCK:
        stopped = job_id in STOP_REQUESTED
        PROCESSES.pop(job_id, None)
        STOP_REQUESTED.discard(job_id)
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
    update_job(job_id, status=status, current_stage=current, progress=progress, ended_at=now(), exit_code=exit_code, pid=None, pgid=None, last_error=error)
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
        if path.is_file():
            result.append(str(path))
    return result


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
    return {"status": "ok", "pipelines": list(REGISTRY), "active_jobs": active_count()}


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


@app.get("/api/browse")
def browse(kind: str = "input", path: str | None = None) -> dict[str, Any]:
    """List a single safe directory level for the path picker."""
    return browse_directory(kind, path)


@app.post("/api/jobs")
def create_job(request: CreateJob) -> dict[str, Any]:
    job, _ = build_job(request)
    insert_job(job)
    add_action(job["id"], job["operator"], "create", json.dumps(request.model_dump(), ensure_ascii=False))
    schedule()
    return snapshot(get_job_row(job["id"]))


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
    return {"job": snapshot(row), "options": json.loads(row["options_json"]), "actions": actions_for(job_id), "match_summary": summary_files(row)}


def actions_for(job_id: str) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute("SELECT operator, action, details, created_at FROM actions WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str, offset: int = 0, max_bytes: int = 262144) -> JSONResponse:
    row = get_job_row(job_id)
    path = log_path(row)
    try:
        size = path.stat().st_size
        reset = offset < 0 or offset > size
        read_from = 0 if reset else offset
        max_bytes = max(4096, min(max_bytes, 1_048_576))
        with path.open("rb") as handle:
            handle.seek(read_from)
            payload = handle.read(max_bytes)
        next_offset = read_from + len(payload)
        content = payload.decode("utf-8", errors="replace")
    except OSError:
        return JSONResponse({"content": "pipeline log not created yet\n", "next_offset": 0, "reset": True, "size": 0})
    return JSONResponse({"content": content, "next_offset": next_offset, "reset": reset, "size": size})


@app.get("/api/jobs/{job_id}/match-preview")
def match_preview(job_id: str) -> dict[str, Any]:
    row = get_job_row(job_id)
    files = summary_files(row)
    if not files:
        return {"path": None, "columns": [], "rows": []}
    path = Path(files[0])
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = []
            total = ok = error = unmatched = 0
            for item in reader:
                total += 1
                status = str(item.get("status", "")).strip().upper()
                if status == "OK":
                    ok += 1
                else:
                    error += 1
                    message = f"{item.get('error', '')} {item.get('status', '')}".lower()
                    if any(token in message for token in ("match", "unmatched", "missing", "ambiguous")):
                        unmatched += 1
                if len(rows) < 100:
                    rows.append(item)
            return {"path": str(path), "columns": reader.fieldnames or [], "rows": rows, "counts": {"total": total, "ok": ok, "error": error, "unmatched": unmatched}}
    except (OSError, csv.Error) as exc:
        raise HTTPException(500, f"cannot read match summary: {exc}") from exc


@app.post("/api/jobs/{job_id}/confirm-match")
def confirm_match(job_id: str) -> dict[str, Any]:
    row = get_job_row(job_id)
    if row["status"] != "WAITING_REVIEW":
        raise HTTPException(409, "job is not waiting for match confirmation")
    update_job(job_id, status="QUEUED", last_error=None)
    add_action(job_id, row["operator"], "confirm-match")
    schedule()
    return snapshot(get_job_row(job_id))


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str) -> dict[str, Any]:
    row = get_job_row(job_id)
    if row["status"] in {"RUNNING", "QUEUED", "STOPPING"}:
        raise HTTPException(409, "job is already active")
    update_job(job_id, status="QUEUED", last_error=None, ended_at=None)
    add_action(job_id, row["operator"], "resume")
    schedule()
    return snapshot(get_job_row(job_id))


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, Any]:
    row = get_job_row(job_id)
    if row["status"] == "QUEUED":
        update_job(job_id, status="STOPPED", ended_at=now(), last_error="stopped before start")
    elif row["status"] in {"RUNNING", "MATCHING"}:
        with PROCESS_LOCK:
            process = PROCESSES.get(job_id)
            STOP_REQUESTED.add(job_id)
        try:
            pgid = int(row["pgid"] or (os.getpgid(process.pid) if process else 0))
            if pgid > 0:
                os.killpg(pgid, signal.SIGTERM)
            elif process:
                process.terminate()
        except (OSError, ProcessLookupError):
            pass
        update_job(job_id, status="STOPPING")
    else:
        raise HTTPException(409, "job is not running")
    add_action(job_id, row["operator"], "stop")
    return snapshot(get_job_row(job_id))


@app.get("/api/jobs/{job_id}/artifacts")
def artifacts(job_id: str) -> dict[str, Any]:
    row = get_job_row(job_id)
    output = Path(row["output_root"])
    candidates = [
        output / "01.match" / row["dataset"],
        output / "logs" / row["dataset"] / "pipeline.log",
        output / ".pipeline_state" / row["dataset"],
    ]
    return {
        "output_root": str(output),
        "files": [{"path": str(path), "exists": path.exists(), "is_dir": path.is_dir(), "size": path.stat().st_size if path.is_file() else None} for path in candidates],
    }
