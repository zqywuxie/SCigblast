#!/usr/bin/env bash
set -euo pipefail
WEB_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v crontab >/dev/null
DOCKER=$(command -v docker)
# The server is UTC; also support hosts configured directly to Beijing time.
case "$(date +%z)" in
  +0000) hour=19 ;;
  +0800) hour=3 ;;
  *) echo 'Archive cron requires a UTC or UTC+8 host timezone.' >&2; exit 1 ;;
esac
mkdir -p "$HOME/.local/state/scigblast"
"$DOCKER" exec -i scigblast-web sh -c 'mkdir -p /var/lib/scigblast-web/maintenance && cat > /var/lib/scigblast-web/maintenance/archive_results.py' < "$WEB_DIR/archive_results.py"
printf -v job '%q exec scigblast-web python /var/lib/scigblast-web/maintenance/archive_results.py > %q 2>&1' "$DOCKER" "$HOME/.local/state/scigblast/archive-results.log"
existing=$(crontab -l 2>/dev/null || true)
{
  printf '%s\n' "$existing" | sed '/# scigblast-results-archive$/d'
  printf '0 %s * * * %s # scigblast-results-archive\n' "$hour" "$job"
} | crontab -
echo 'Results archive scheduled daily at 03:00 Beijing time (successful jobs older than 14 days).'
