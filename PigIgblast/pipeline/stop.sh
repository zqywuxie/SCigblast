#!/usr/bin/env bash
set -u
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PIPELINE_DIR="$SCRIPT_DIR"
BRANCH_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
RUNTIME_OUTPUT_ROOT="${SCIGBLAST_OUTPUT_ROOT:-}"
if [[ -f "${SCRIPT_DIR}/00.pipeline_config.env" ]]; then source "${SCRIPT_DIR}/00.pipeline_config.env" 2>/dev/null || true; fi
resolve_output_root(){ local value="${1:-}"; if [[ -z "$value" ]]; then printf '%s/output\n' "$BRANCH_ROOT"; elif [[ "$value" == /* ]]; then printf '%s\n' "$value"; else printf '%s/%s\n' "$BRANCH_ROOT" "$value"; fi; }
OUTPUT_ROOT="$(resolve_output_root "${RUNTIME_OUTPUT_ROOT:-${SCIGBLAST_OUTPUT_ROOT:-${OUTPUT_ROOT:-}}}")"
STATE_ROOT="${OUTPUT_ROOT}/.pipeline_state"
PID_DIR="${SCRIPT_DIR}/.pids"  # legacy registry
PROCESS_MARKERS=(
  "run_pig_pipeline.sh" "01.match_sample.py" "02.run_fastp.sh"
  "03.clean_header.py" "04.work_pandaseq.sh" "05.work_igblastn.py"
)
children_of() {
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -P "$1" 2>/dev/null || true
  else
    ps -eo pid=,ppid= | awk -v parent="$1" '$2 == parent {print $1}'
  fi
}
pid_alive() {
  local pid="$1" state
  [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  [[ -z "$state" || "$state" != Z* ]]
}
pid_command() {
  if [[ -r "/proc/$1/cmdline" ]]; then tr '\0' ' ' < "/proc/$1/cmdline"; else ps -p "$1" -o args= 2>/dev/null || true; fi
}
pid_cwd() { [[ -e "/proc/$1/cwd" ]] && readlink -f "/proc/$1/cwd" 2>/dev/null || true; }
pid_environment() { [[ -r "/proc/$1/environ" ]] && tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null || true; }
declare -A TARGET_ROOTS=()
[[ -n "$OUTPUT_ROOT" && "$OUTPUT_ROOT" != "/" ]] && TARGET_ROOTS["$OUTPUT_ROOT"]=1
remember_roots() {
  local pid="$1" line value
  while IFS= read -r line; do
    case "$line" in
      SCIGBLAST_OUTPUT_ROOT=*) value="${line#*=}"; [[ -n "$value" && "$value" != "/" ]] && TARGET_ROOTS["$value"]=1 ;;
      OUTPUT_ROOT=*) value="${line#*=}"; [[ -n "$value" && "$value" != "/" ]] && TARGET_ROOTS["$value"]=1 ;;
      SCIGBLAST_*_OUTPUT_DIR=*) value="${line#*=}"; [[ -n "$value" && "$value" != "/" ]] && TARGET_ROOTS["$value"]=1 ;;
    esac
  done < <(pid_environment "$pid")
}
matches_pipeline_process() {
  local pid="$1" command cwd environment marker root
  [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" ]] || return 1
  command="$(pid_command "$pid")"; cwd="$(pid_cwd "$pid")"; environment="$(pid_environment "$pid")"
  for marker in "${PROCESS_MARKERS[@]}"; do
    if [[ "$command" == *"$marker"* ]] && { [[ "$command" == *"$SCRIPT_DIR"* ]] || [[ "$cwd" == "$SCRIPT_DIR" ]]; }; then return 0; fi
  done
  for root in "${!TARGET_ROOTS[@]}"; do
    [[ "$command" == *"$root"* || "$cwd" == "$root" || "$cwd" == "$root"/* ]] && return 0
    [[ "$environment" == *"=$root"* || "$environment" == *"=$root"$'\n'* ]] && return 0
  done
  return 1
}
discover_pipeline_processes() {
  # Avoid the old per-PID cmdline/cwd/environ fork storm.  Build candidates
  # from one ps stream and perform detailed validation only for those PIDs.
  local line pid command marker root candidate
  local -A seen=()
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    pid="${line%%[[:space:]]*}"
    command="${line#*[[:space:]]}"
    [[ "$pid" =~ ^[0-9]+$ && "$pid" != "$$" ]] || continue
    candidate=0
    [[ "$command" == *"$SCRIPT_DIR"* ]] && candidate=1
    if (( candidate == 0 )); then
      for marker in "${PROCESS_MARKERS[@]}"; do
        [[ "$command" == *"$marker"* ]] && { candidate=1; break; }
      done
    fi
    if (( candidate == 0 )) && [[ "$command" == *"python3 -"* || "$command" == *"python -"* || "$command" == *"fastp"* || "$command" == *"pandaseq"* || "$command" == *"igblastn"* ]]; then
      candidate=1
    fi
    if (( candidate == 0 )); then
      for root in "${!TARGET_ROOTS[@]}"; do
        [[ "$command" == *"$root"* ]] && { candidate=1; break; }
      done
    fi
    (( candidate )) || continue
    matches_pipeline_process "$pid" || continue
    [[ -n "${seen[$pid]+yes}" ]] && continue
    seen["$pid"]=1
    printf '%s\n' "$pid"
  done < <(ps -eo pid=,args= 2>/dev/null || true)
}
collect_tree() {
  local root="$1" current child
  local -a queue=("$root")
  local -A seen=()
  while ((${#queue[@]})); do
    current="${queue[0]}"; queue=("${queue[@]:1}")
    [[ "$current" =~ ^[0-9]+$ ]] || continue
    [[ -n "${seen[$current]+yes}" ]] && continue
    seen["$current"]=1; printf '%s\n' "$current"
    while read -r child; do [[ "$child" =~ ^[0-9]+$ ]] && queue+=("$child"); done < <(children_of "$current")
  done
}
stop_tree() {
  local root="$1" i p alive
  local -a pids=()
  mapfile -t pids < <(collect_tree "$root")
  for ((i=${#pids[@]}-1; i>=0; i--)); do p="${pids[$i]}"; pid_alive "$p" && kill -TERM "$p" 2>/dev/null || true; done
  for i in $(seq 1 20); do
    alive=0; for p in "${pids[@]}"; do pid_alive "$p" && alive=1; done
    ((alive == 0)) && return 0; sleep 1
  done
  echo "[PIG] graceful stop timed out; forcing saved process tree" >&2
  for ((i=${#pids[@]}-1; i>=0; i--)); do p="${pids[$i]}"; pid_alive "$p" && kill -KILL "$p" 2>/dev/null || true; done
  sleep 1
  for p in "${pids[@]}"; do pid_alive "$p" && return 1; done
  return 0
}
STOP_FAILED=0
declare -A TARGETS=()
shopt -s nullglob
pid_files=("${PID_DIR}"/*.pid "${SCRIPT_DIR}"/run_pig_pipeline.*.pid)
if [[ -d "$STATE_ROOT" ]]; then
  while IFS= read -r -d '' pid_file; do pid_files+=("$pid_file"); done < <(find "$STATE_ROOT" -type f -path '*/pids/*.pid' -print0 2>/dev/null || true)
fi
for pid_file in "${pid_files[@]}"; do
  pid=$(cat "$pid_file" 2>/dev/null || true)
  if [[ "$pid" =~ ^[0-9]+$ ]] && pid_alive "$pid"; then
    command=$(pid_command "$pid")
    if [[ "$command" != *"run_pig_pipeline.sh"* ]]; then
      echo "[PIG] PID ${pid} does not belong to run_pig_pipeline.sh; refusing to kill it" >&2
      STOP_FAILED=1
      continue
    fi
    remember_roots "$pid"
    echo "[PIG] stopping pipeline process tree pid=$pid"
    stop_tree "$pid" || STOP_FAILED=1
  fi
  (( STOP_FAILED == 0 )) && rm -f "$pid_file"
done
# PID files are authoritative; do not scan unrelated server processes.
if (( ${#TARGETS[@]} == 0 )); then echo "[PIG] no registered pipeline process found" >&2; fi
for pid in "${!TARGETS[@]}"; do
  if pid_alive "$pid"; then echo "[PIG] stopping pipeline/worker process tree pid=$pid"; stop_tree "$pid" || STOP_FAILED=1; fi
done
remaining=""
for pid in "${!TARGETS[@]}"; do pid_alive "$pid" && remaining+="${pid}"$'\n'; done
if [[ -n "$remaining" ]]; then
  echo "[PIG] unable to stop all branch processes (remaining PIDs):" >&2
  echo "$remaining" >&2
  STOP_FAILED=1
fi
if (( STOP_FAILED )); then
  echo "[PIG] stop incomplete; active orphaned processes remain" >&2
  exit 1
fi
rmdir "$PID_DIR" 2>/dev/null || true
echo "[PIG] stop complete"
