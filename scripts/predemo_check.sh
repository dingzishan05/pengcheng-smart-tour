#!/usr/bin/env bash
# 演示前检查清单（任务 15.5，design/11 §8）：比赛前夜/当日执行，全绿方可演示。
# 检查项：4 容器健康 / 数据源状态全绿 / 三方案生成可用 / 备份存在 / 磁盘余量 >20%
# 用法：bash scripts/predemo_check.sh [后端地址，默认 http://localhost]
set -uo pipefail

BASE_URL="${1:-http://localhost}"
FAILED=0

check() {
    local name="$1" ok="$2" detail="${3:-}"
    if [ "${ok}" = "1" ]; then
        echo "[PASS] ${name}"
    else
        echo "[FAIL] ${name} ${detail}"
        FAILED=1
    fi
}

echo "=== 彭城智游 演示前检查（$(date '+%F %T')）==="

for svc in nginx backend postgres redis; do
    health="$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q "${svc}")" 2>/dev/null || true)"
    check "容器 ${svc} 健康" "$([ "${health}" = "healthy" ] && echo 1 || echo 0)" "当前: ${health:-未知}"
done

SOURCES_STATUS="$(curl -sf --max-time 10 "${BASE_URL}/api/v1/sources/status" || true)"
if [ -n "${SOURCES_STATUS}" ]; then
    DEGRADED="$(echo "${SOURCES_STATUS}" | grep -o '"status"[[:space:]]*:[[:space:]]*"\(degraded\|down\)"' | head -1 || true)"
    check "数据源状态全绿" "$([ -z "${DEGRADED}" ] && echo 1 || echo 0)" "发现异常状态"
else
    check "数据源状态接口可访问" 0 "GET /api/v1/sources/status 失败"
fi

PLANS_CODE="$(curl -s -o /tmp/predemo_plans.json -w '%{http_code}' --max-time 35 \
    -X POST "${BASE_URL}/api/v1/plans" \
    -H 'Content-Type: application/json' \
    -c /tmp/predemo_cookie.txt \
    -d '{"request":{"duration_hours":8,"budget":300,"interests":["历史文化"],"short_trip":false}}' || echo 000)"
PLAN_COUNT="$(grep -o '"plan_id"' /tmp/predemo_plans.json 2>/dev/null | wc -l || echo 0)"
check "三方案生成可用" "$([ "${PLANS_CODE}" = "200" ] && [ "${PLAN_COUNT}" -ge 3 ] && echo 1 || echo 0)" \
    "HTTP=${PLANS_CODE} 方案数=${PLAN_COUNT}"

BACKUPS="$(ls -1 backups/pczy_*.dump 2>/dev/null | wc -l || echo 0)"
check "备份存在（${BACKUPS} 份）" "$([ "${BACKUPS}" -ge 1 ] && echo 1 || echo 0)"

DISK_FREE_PCT="$(df -P . | awk 'NR==2{gsub("%","",$5); print 100-$5}')"
check "磁盘余量 >20%（当前 ${DISK_FREE_PCT}%）" "$([ "${DISK_FREE_PCT}" -gt 20 ] && echo 1 || echo 0)"

echo "=== 故障演练提示 ==="
echo "演示前夜请执行混沌演练三场景："
echo "  cd backend && pytest tests/faults/test_chaos_drill.py -q"
echo "（LLM 超时 / Redis 宕机 / 地图限流，对应 design/10 §10.5）"
echo "=== 网络备份链路 ==="
echo "确认热点可访问 LLM/高德/天气三个外网域名；主网络故障时切换热点后重启 nginx 容器。"

if [ "${FAILED}" = "1" ]; then
    echo ">>> 存在未通过项，请处理后再演示 <<<"
    exit 1
fi
echo ">>> 全部通过，可以演示 <<<"