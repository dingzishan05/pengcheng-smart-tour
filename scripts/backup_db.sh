#!/usr/bin/env bash
# 每日数据库备份（任务 15.4，design/11 §7）：pg_dump -> 宿主机 backups/，保留最近 7 份。
# 宿主机 crontab 配置（每日 03:30 执行）：
#   30 3 * * * cd /opt/pengcheng-tour-agent && bash scripts/backup_db.sh >> backups/backup.log 2>&1
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-backups}"
KEEP_COUNT=7
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="${BACKUP_DIR}/pczy_${STAMP}.dump"

mkdir -p "${BACKUP_DIR}"

echo "[$(date '+%F %T')] 开始备份 -> ${TARGET}"
docker compose exec -T postgres pg_dump -U pczy -d pczy -Fc -f "/tmp/${STAMP}.dump"
docker compose cp "postgres:/tmp/${STAMP}.dump" "${TARGET}"
docker compose exec -T postgres rm -f "/tmp/${STAMP}.dump"

if [ ! -s "${TARGET}" ]; then
    echo "[$(date '+%F %T')] 备份失败：${TARGET} 为空" >&2
    exit 1
fi
echo "[$(date '+%F %T')] 备份完成：$(du -h "${TARGET}" | cut -f1)"

ls -1t "${BACKUP_DIR}"/pczy_*.dump | tail -n "+$((KEEP_COUNT + 1))" | while read -r old; do
    rm -f "${old}"
    echo "[$(date '+%F %T')] 清理过期备份：${old}"
done
echo "[$(date '+%F %T')] 当前保留备份：$(ls -1 "${BACKUP_DIR}"/pczy_*.dump | wc -l) 份"