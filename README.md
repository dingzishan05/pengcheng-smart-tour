# 彭城智游 · Pengcheng Smart Tour

> 基于大模型与实时数据融合的徐州智慧文旅 Agent
> —— 大学生计算机设计大赛 / AI 创新比赛参赛项目

用户用一句自然语言说出需求（人数、出发地、时间、预算、兴趣），系统自动提取约束，检索徐州本地文旅知识，查询票价与地图路线，生成**预算内、时间可行**的个性化行程，并支持追问与改路线。

**设计原则**：LLM 负责理解与解释，结构化数据与可信 API 负责事实，优化算法负责计算。模型不编造票价与营业时间，无官方实时接口的数据一律标注「估算 / 最近核验时间」。

## 功能

- **自然语言规划**：一句话生成 3 套可对比方案（省钱 / 经典 / 深度）
- **约束求解**：预算、时间窗、交通偏好、少走路等多约束下的路线优化（OR-Tools）
- **RAG 文旅知识库**：本地史料与景点介绍分块向量化，回答附带出处
- **实时数据**：高德地图路线与 POI、票价、天气，带数据新鲜度与降级策略
- **可解释预算**：门票 / 交通 / 餐饮 / 预留金分项可视，超预算自动替换项
- **降级兜底**：LLM 超时、Redis 宕机、地图限流均有对应降级路径

## 技术栈

| 层级 | 技术 |
|---|---|
| 前端 | Vue 3 + Vite + TypeScript + Element Plus + Pinia |
| 后端 | Python 3.13 + FastAPI + Pydantic + Uvicorn |
| Agent | LangGraph（多步状态机、工具调用、重试） |
| 数据 | PostgreSQL + SQLAlchemy + Alembic + pgvector |
| 缓存 / 任务 | Redis + APScheduler |
| 地图 / 外部数据 | 高德地图 JS API / Web 服务、天气接口、OpenAI 兼容 LLM |
| 优化 | OR-Tools |
| 部署 | Docker Compose（nginx + frontend + backend + postgres + redis） |

## 目录结构

```text
.
├── backend/            # FastAPI 后端
│   ├── app/            # api / agents / tools / models / schemas / services / jobs / core / db
│   ├── alembic/        # 数据库迁移
│   └── tests/          # pytest（含降级与混沌演练）
├── frontend/           # Vue 3 前端
│   └── src/            # views / components / api / stores
├── nginx/              # 反向代理与静态资源
├── scripts/            # 备份、预演检查、密钥扫描
├── data/               # 合法采集的样例数据与来源清单
├── docs/               # 架构图、验证材料
├── docker-compose.yml
├── .env.example
└── 彭城智游_Agent_完整项目设计方案.md
```

## 快速开始

### 1. 配置密钥

```bash
cp .env.example .env
```

在 `.env` 中填入 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`AMAP_WEB_KEY`、`AMAP_JS_KEY`、`AMAP_SECURITY_CODE`、`JWT_SECRET` 等。

> ⚠️ `.env` 已被 `.gitignore` 排除，**任何情况下都不要提交**。

### 2. Docker 一键启动（推荐）

```bash
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python -m scripts.seed
```

浏览器打开 `http://localhost/`，后端健康检查 `http://localhost/health`。

### 3. 本地开发模式

```bash
# 后端
cd backend
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev
```

Windows 用户也可直接双击 `start_local.bat`。

## 测试

```bash
cd backend && pytest            # 单元 + 集成（-m "not perf" 默认跳过压测）
cd frontend && npm run test:e2e # Playwright 端到端
python scripts/scan_secrets.py  # 发布前密钥泄露扫描
```

## 部署与运维

详见 [DEPLOY.md](DEPLOY.md)：冷启动、发布、回滚、备份恢复、演示保障。

## 文档

- [完整项目设计方案](彭城智游_Agent_完整项目设计方案.md) —— 需求、架构、算法、数据库、API、实施计划

## 免责声明

项目中的景点票价、营业时间等数据仅供学习演示，实际出行请以景区官方发布为准。
