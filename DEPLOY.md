# 部署与演示保障手册

> 对齐 `design/11-部署方案.md`；镜像 tag 与 git tag 一致（当前基线 `v1.0.0`）。

## 1. 首次部署（冷启动，目标 ≤30min）

| 步骤 | 命令 | 预期 |
|---|---|---|
| 1. 配置 | `cp .env.example .env` 后填入 LLM/高德/天气密钥 | `.env` 不入库 |
| 2. 拉起 | `docker compose up -d --build` | 4 容器启动，backend 等待 postgres/redis 健康 |
| 3. 建表 | `docker compose exec backend alembic upgrade head` | 表 + pgvector 扩展 + 索引就绪 |
| 4. 种子 | `docker compose exec backend python -m scripts.seed` | admin → POI → 知识库（分块向量化）→ 路线矩阵预热 |
| 5. 自检 | `docker compose exec backend python -m scripts.verify_seed` | POI 数 / 知识块数 / 矩阵完整率输出 |
| 6. 冒烟 | 浏览器 `http://host/`：聊天 → 三方案 → 地图 | 四步演示主流程可走通 |

冷启动计时：从步骤 1 开始计时，步骤 6 完成记录总时长（RTO 验证，`design/11` §10.1）。

## 2. 发布流程

1. `docker compose build`（版本号变更时同步修改 `docker-compose.yml` 的 `image:` tag）；
2. 测试环境 `docker compose up -d` + Playwright 冒烟：`cd frontend && npx playwright test`（含演示主流程与降级 UI 场景）；
3. 密钥泄露扫描：`python scripts/scan_secrets.py`（退出码 0 方可发布）；
4. 分发：`docker compose push` 或 `docker save pczy/backend:v1.0.0 pczy/nginx:v1.0.0 | gzip > pczy_v1.0.0.tar.gz` 离线导出；
5. 生产：`docker compose pull && docker compose up -d`；
6. 有数据库迁移时：`docker compose exec backend alembic upgrade head`。

## 3. 回滚流程

```bash
# 1. 修改 docker-compose.yml 中 image tag 为上一版本（如 v0.9.0），或：
docker compose down
docker tag pczy/backend:v0.9.0 pczy/backend:rollback
# 2. 重新拉起（旧镜像 + 新表结构，迁移只增列向前兼容，design/05 §5.4）
docker compose up -d
# 3. 验证：curl http://host/health 与三方案生成
```

回滚演练验收：升级到 v1.0.0（含新迁移）后回滚 v0.9.0 镜像，核心接口（/health、POST /api/v1/plans、POST /api/v1/chat）仍 200。

## 4. 备份与恢复

### 4.1 每日备份（宿主机 crontab）

```
30 3 * * * cd /opt/pengcheng-tour-agent && bash scripts/backup_db.sh >> backups/backup.log 2>&1
```

保留最近 7 份；`.env` 与镜像版本手工备份（密钥库）。

### 4.2 恢复演练（RTO ≤15min，演练记录在案）

```bash
# 1. 停 backend（避免写入竞争）
docker compose stop backend
# 2. 恢复到新卷（演练时可临时改 compose 的 pgdata 卷名）
docker compose cp backups/pczy_YYYYMMDD_HHMMSS.dump postgres:/tmp/restore.dump
docker compose exec postgres pg_restore -U pczy -d pczy --clean --if-exists /tmp/restore.dump
# 3. 重启并校验
docker compose up -d backend
docker compose exec backend python -m scripts.verify_seed   # 数据完整性校验
```

记录每次演练的：备份文件、耗时、verify_seed 结果。整机故障时 `docker compose up -d` + 恢复最新备份即一键重建（RTO ≤30min，DFX 4.2.2）。

## 5. 演示环境保障（比赛专项）

1. **演示前夜**：
   - `bash scripts/predemo_check.sh`（4 容器 / 数据源 / 三方案 / 备份 / 磁盘全绿）；
   - 混沌演练三场景：`cd backend && pytest tests/faults/test_chaos_drill.py -q`（LLM 超时 / Redis 宕机 / 地图限流，验证降级矩阵）；
2. **演示数据**：种子含演示 POI/知识集，演示文案（"预算300元一天历史文化"等）由 E2E 冒烟覆盖；
3. **四步演示主流程**（spec 3.1.3）：输入需求 → 查看三方案 → 调整预算 → 触发重规划，可重复演练；
4. **网络备份链路**：演示机需出网访问 LLM / 高德 / 天气；预先用手机热点验证三个域名可达，主网络故障时切热点后 `docker compose restart nginx backend`；
5. **离线兜底**：高德 JS API 失败 → 前端地图占位（时间轴完整）；外部 LLM 长时间不可用属超控范围，演示前检查项覆盖。

## 6. 常用命令速查

```bash
docker compose ps                          # 容器状态（应 4 个 healthy）
docker compose logs -f backend             # 跟踪后端日志（JSON 行式）
docker compose exec backend python -c "from app.core import metrics; print(metrics.snapshot())"   # 运行指标
docker compose restart backend             # 单容器恢复（restart: always 自动重启）
python scripts/scan_secrets.py             # 密钥泄露扫描
bash scripts/backup_db.sh                  # 手动触发备份
bash scripts/predemo_check.sh              # 演示前检查清单
```