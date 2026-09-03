#!/usr/bin/env bash
set -Eeuo pipefail

# One-command deployment for the SCigblast web runner on Linux.
# Configure host paths in web/.env before running this script.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="${SCRIPT_DIR}/web"
COMPOSE_FILE="${WEB_DIR}/docker-compose.yml"
ENV_FILE="${SCIGBLAST_ENV_FILE:-${WEB_DIR}/.env}"
SERVICE="scigblast-web"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][ERROR] %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "未找到 docker，请先安装 Docker Engine。"
docker compose version >/dev/null 2>&1 || die "当前 Docker 不支持 compose 子命令，请安装 Docker Compose v2。"
[[ -f "${COMPOSE_FILE}" ]] || die "缺少 ${COMPOSE_FILE}"

if [[ ! -f "${ENV_FILE}" ]]; then
  [[ -f "${WEB_DIR}/.env.example" ]] || die "缺少 ${WEB_DIR}/.env.example"
  cp -- "${WEB_DIR}/.env.example" "${ENV_FILE}"
  log "已创建 ${ENV_FILE}，请先修改宿主机路径后重新运行。"
  exit 2
fi

# --project-directory keeps the relative ./runtime bind mount stable even
# when deploy.sh is called from another directory.
compose=(docker compose --env-file "${ENV_FILE}" --project-directory "${WEB_DIR}" -f "${COMPOSE_FILE}")

log "检查 Compose 配置"
"${compose[@]}" config >/dev/null
log "构建并启动 ${SERVICE}"
"${compose[@]}" up -d --build --force-recreate
log "容器状态"
"${compose[@]}" ps

port="$(grep -E '^[[:space:]]*SCIGBLAST_WEB_PORT[[:space:]]*=' "${ENV_FILE}" | head -n 1 | cut -d= -f2- | tr -d '[:space:]"')"
port="${port:-8000}"
health_url="http://127.0.0.1:${port}/health"

if command -v curl >/dev/null 2>&1; then
  for attempt in {1..20}; do
    if curl --fail --silent --show-error --max-time 3 "${health_url}" >/dev/null 2>&1; then
      log "服务已就绪：${health_url}"
      log "浏览器访问：http://服务器IP:${port}"
      exit 0
    fi
    sleep 2
  done
  log "容器已启动，但健康检查超时：${health_url}"
else
  log "未找到 curl，跳过宿主机健康检查。"
fi

log "查看日志：${compose[*]} logs -f ${SERVICE}"
exit 1
