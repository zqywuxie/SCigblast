#!/usr/bin/env bash
set -Eeuo pipefail

# One-command deployment for the SCigblast web runner on Linux.
# Configure host paths and the initial administrator in web/.env before running this script.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="${SCRIPT_DIR}/web"
COMPOSE_FILE="${WEB_DIR}/docker-compose.yml"
ENV_FILE="${SCIGBLAST_ENV_FILE:-${WEB_DIR}/.env}"
SERVICE="scigblast-web"
BUILD_IMAGE=1
ORIGINAL_ARGS=("$@")

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][ERROR] %s\n' "$*" >&2; exit 1; }

usage() {
  printf '%s\n' \
    '用法：bash deploy.sh [--no-build]' \
    '默认：git pull --ff-only 拉取当前分支上游，再构建镜像并部署。' \
    '--no-build：仍先拉取代码，但使用现有镜像（不包含本次拉取的新代码）。' \
    '配置：web/.env；可通过 SCIGBLAST_ENV_FILE 指定其他配置文件。'
}
case "${1:-}" in
  '') ;;
  --no-build) BUILD_IMAGE=0; shift ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; die "未知参数：$1" ;;
esac
(( $# == 0 )) || die "存在多余参数；运行 bash deploy.sh --help 查看用法。"

# Re-enter the updated script after pulling; never discard local edits.
if [[ "${SCIGBLAST_DEPLOY_PULL_DONE:-}" != "1" ]]; then
  command -v git >/dev/null 2>&1 || die "未找到 git，无法更新仓库。"
  git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "请在 Git 克隆仓库中运行 deploy.sh。"
  git -C "$SCRIPT_DIR" symbolic-ref -q HEAD >/dev/null || die "当前是 detached HEAD，请切换到部署分支。"
  git -C "$SCRIPT_DIR" rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1 || die "当前分支未配置远端上游。"
  if ! git -C "$SCRIPT_DIR" diff --quiet || ! git -C "$SCRIPT_DIR" diff --cached --quiet; then
    die "仓库有未提交的修改。请先备份并处理 git status 中的改动后重试；不会覆盖服务器配置。web/.env 不受拉取影响。"
  fi
  log "拉取当前分支远端更新（仅允许 fast-forward）"
  git -C "$SCRIPT_DIR" pull --ff-only || die "远端拉取失败，未执行构建或替换容器。"
  export SCIGBLAST_DEPLOY_PULL_DONE=1
  exec bash "$SCRIPT_DIR/deploy.sh" "${ORIGINAL_ARGS[@]}"
fi
unset SCIGBLAST_DEPLOY_PULL_DONE

command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Engine。"
docker compose version >/dev/null 2>&1 || die "当前 Docker 不支持 compose 子命令，请安装 Docker Compose v2。"
docker info >/dev/null 2>&1 || die "无法连接 Docker 服务，请检查服务状态和当前用户权限。"
[[ -f "${COMPOSE_FILE}" ]] || die "缺少 ${COMPOSE_FILE}"

if [[ ! -f "${ENV_FILE}" ]]; then
  [[ -f "${WEB_DIR}/.env.example" ]] || die "缺少 ${WEB_DIR}/.env.example"
  cp -- "${WEB_DIR}/.env.example" "${ENV_FILE}"
  log "已创建 ${ENV_FILE}，请先填写宿主机路径和初始管理员账号、密码后重新运行。"
  exit 2
fi

# Persist the host account identity without sourcing the secrets file as shell code.
configure_runtime_id() {
  local key="$1" fallback="$2" value="${!1:-}"
  if [[ -z "$value" ]]; then
    value="$(sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '[:space:]"\047')"
  fi
  if [[ -z "$value" ]]; then
    value="$fallback"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "请在 web/.env 中配置非 root 的 ${key}。"
    if grep -q "^${key}=" "$ENV_FILE"; then
      sed -i "s/^${key}=.*$/${key}=${value}/" "$ENV_FILE"
    else
      printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
  fi
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "${key} 必须是非零数字 ID。"
  printf -v "$key" '%s' "$value"
  export "$key"
}
configure_runtime_id SCIGBLAST_UID "$(id -u)"
configure_runtime_id SCIGBLAST_GID "$(id -g)"
log "运行账号 UID:GID=${SCIGBLAST_UID}:${SCIGBLAST_GID}"
mkdir -p "${WEB_DIR}/runtime"

# --project-directory keeps the relative ./runtime bind mount stable even
# when deploy.sh is called from another directory.
compose=(docker compose --env-file "${ENV_FILE}" --project-directory "${WEB_DIR}" -f "${COMPOSE_FILE}")

log "检查 Compose 配置"
"${compose[@]}" config >/dev/null
check_idle() {
  local running
  running="$("${compose[@]}" ps --status running -q "$SERVICE")" || die "无法检查现有容器状态。"
  if [[ -n "$running" ]]; then
    log "检查现有服务是否还有运行或排队任务"
    "${compose[@]}" exec -T "$SERVICE" python -c 'import json,urllib.request,sys; s=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=5)); sys.exit(1 if s.get("active_jobs",0) or s.get("queued_jobs",0) else 0)' || die "服务仍有任务或无法确认状态；请先在网页停止任务，再部署。"
  fi
}
check_idle
if [[ "$BUILD_IMAGE" == 1 ]]; then
  for branch in IR_split 10X_split Igblast_base PigIgblast; do
    [[ -d "${SCRIPT_DIR}/${branch}/pipeline" ]] || die "缺少 ${branch}/pipeline；只有镜像时请使用 --no-build。"
  done
  [[ -f "${SCRIPT_DIR}/reference/8bp_barcodes.csv" ]] || die "缺少默认 reference/8bp_barcodes.csv。"
  log "构建镜像（首次下载依赖可能较慢），构建失败不会替换现有容器"
  "${compose[@]}" build "$SERVICE"
  # A build may take minutes; recheck tasks immediately before replacement.
  check_idle
fi
log "检查普通用户的状态与结果目录权限"
"${compose[@]}" run --rm --no-deps --entrypoint python "$SERVICE" -c '
import os,tempfile
from pathlib import Path
assert os.getuid() != 0, "Web must run as a non-root user"
for key in ("SCIGBLAST_STATE_DIR", "SCIGBLAST_DEFAULT_OUTPUT_ROOT"):
    path=Path(os.environ[key]);path.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryFile(dir=path) as handle:handle.write(b"permission check")
database=Path(os.environ["SCIGBLAST_STATE_DIR"])/"scigblast.sqlite3"
assert not database.exists() or os.access(database,os.R_OK|os.W_OK), "State database needs ownership migration"
lock=Path(os.environ["SCIGBLAST_RESOURCE_LOCK_FILE"])
assert not lock.exists() or os.access(lock,os.R_OK|os.W_OK), "Shared resource lock needs ownership migration"
Path.home().mkdir(parents=True,exist_ok=True)
print("Runtime permissions OK: %s:%s" % (os.getuid(),os.getgid()))
' || die "普通用户无法访问已有状态或结果，请先通过 Docker 完成旧 root 文件权限迁移；未替换现有容器。"
check_idle
log "启动 ${SERVICE}；部署期间请勿提交新任务"
"${compose[@]}" up -d --no-build --force-recreate "$SERVICE"
log "容器状态"
"${compose[@]}" ps

log "等待 Web 就绪"
for attempt in {1..30}; do
  if "${compose[@]}" exec -T "$SERVICE" python -c 'import json,urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health",timeout=3)); assert s.get("status")=="ok"' >/dev/null 2>&1; then
    binding="$("${compose[@]}" port "$SERVICE" 8000)"
    port="${binding##*:}"
    if ! bash "${WEB_DIR}/install_archive_cron.sh"; then
      log "归档定时任务安装失败，请检查 crontab 和宿主机时区后运行 web/install_archive_cron.sh。"
      exit 1
    fi
    log "服务已就绪；浏览器访问：http://服务器IP:${port}"
    log "如果外部无法访问，请检查服务器防火墙和端口映射。"
    exit 0
  fi
  sleep 2
done

log "健康检查超时，保留容器和结果，请检查日志"
"${compose[@]}" logs --tail 80 "$SERVICE" || true
log "查看日志：${compose[*]} logs -f ${SERVICE}"
exit 1
