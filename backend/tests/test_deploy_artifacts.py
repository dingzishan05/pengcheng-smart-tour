"""任务组 15 部署产物验收（design/11 §2/§5/§10）：配置逐项断言 + 密钥扫描执行。

本机无 Docker 时，冷启动/限流触发等运行时验证按 DEPLOY.md 在部署机执行；
此处将可离线验证的验收标准全部落地为断言。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

COMPOSE = ROOT / "docker-compose.yml"
NGINX_CONF = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
BACKEND_DOCKERFILE = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
NGINX_DOCKERFILE = (ROOT / "nginx" / "Dockerfile").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
DEPLOY_DOC = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "REDIS_URL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "EMBEDDING_MODEL",
    "AMAP_WEB_KEY",
    "AMAP_JS_KEY",
    "WEATHER_KEY",
    "ADMIN_INITIAL_PASSWORD",
    "JWT_SECRET",
]


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_four_services_all_healthy_restart_always():
    services = _compose()["services"]
    assert set(services) == {"nginx", "backend", "postgres", "redis"}
    for name, spec in services.items():
        assert spec.get("restart") == "always", f"{name} 未配置 restart: always"
        assert "healthcheck" in spec, f"{name} 缺少 healthcheck"


def test_compose_port_convergence_and_pgvector():
    services = _compose()["services"]
    assert "ports" not in services["postgres"], "postgres 不得映射宿主机端口"
    assert "ports" not in services["redis"], "redis 不得映射宿主机端口"
    assert "ports" not in services["backend"], "backend 不得映射宿主机端口"
    assert services["postgres"]["image"].startswith("pgvector/pgvector:pg16")
    assert services["backend"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["nginx"]["ports"] == [{"target": 80, "published": "80", "protocol": "tcp"}] or \
        services["nginx"]["ports"] == ["80:80"]


def test_compose_versioned_images_and_amap_build_arg():
    services = _compose()["services"]
    for name in ("backend", "nginx"):
        tag = services[name]["image"]
        assert tag.startswith(f"pczy/{name}:v"), f"{name} 镜像未按 git tag 命名: {tag}"
    build = services["nginx"]["build"]
    assert build["dockerfile"] == "nginx/Dockerfile"
    assert build["args"]["AMAP_JS_KEY"] == "${AMAP_JS_KEY}", "前端高德 JS key 须经构建期注入"


def test_backend_dockerfile_multistage_and_dockerignore():
    assert "AS builder" in BACKEND_DOCKERFILE and BACKEND_DOCKERFILE.count("FROM ") == 2
    assert (ROOT / "backend" / ".dockerignore").exists()
    dockerignore = (ROOT / "backend" / ".dockerignore").read_text(encoding="utf-8")
    assert "tests" in dockerignore and ".venv" in dockerignore


def test_nginx_dockerfile_builds_frontend_with_js_key():
    assert "AS build" in NGINX_DOCKERFILE
    assert "ARG AMAP_JS_KEY" in NGINX_DOCKERFILE
    assert "COPY frontend/" in NGINX_DOCKERFILE
    assert "COPY nginx/nginx.conf" in NGINX_DOCKERFILE


def test_nginx_proxy_timeout_45s_and_health_probe():
    assert "proxy_read_timeout 45s" in NGINX_CONF
    assert "location /health" in NGINX_CONF
    assert "proxy_pass http://backend_upstream/health" in NGINX_CONF


def test_nginx_gzip_and_spa_fallback():
    assert "gzip on" in NGINX_CONF
    for mime in ("text/css", "application/javascript", "application/json"):
        assert mime in NGINX_CONF
    assert "try_files $uri /index.html" in NGINX_CONF


def test_nginx_security_headers_csp_allows_amap():
    assert "X-Content-Type-Options nosniff" in NGINX_CONF
    assert "X-Frame-Options SAMEORIGIN" in NGINX_CONF
    assert "Content-Security-Policy" in NGINX_CONF
    assert "https://webapi.amap.com" in NGINX_CONF


def test_nginx_rate_limits_match_design():
    assert "rate=5r/m" in NGINX_CONF and "chat_limit" in NGINX_CONF
    assert "rate=10r/m" in NGINX_CONF and "login_limit" in NGINX_CONF
    assert "location /api/v1/chat" in NGINX_CONF
    assert "location /api/v1/admin/login" in NGINX_CONF


def test_env_example_complete_and_placeholder_only():
    for var in REQUIRED_ENV_VARS:
        assert f"{var}=" in ENV_EXAMPLE, f".env.example 缺少 {var}"
    assert "PCZY_ENV" not in ENV_EXAMPLE, "PCZY_ENV 为无效变量名（settings 字段为 app_env）"
    for secret_var in ("LLM_API_KEY", "AMAP_WEB_KEY", "WEATHER_KEY", "JWT_SECRET"):
        value = next(
            (
                line.split("=", 1)[1].strip()
                for line in ENV_EXAMPLE.splitlines()
                if line.strip().startswith(f"{secret_var}=")
            ),
            "",
        )
        assert value == "" or value.lower().startswith("your-"), (
            f"{secret_var} 在 .env.example 中必须是空值或占位符，当前: {value}"
        )


def test_deploy_doc_covers_init_release_rollback_backup_demo():
    for keyword in (
        "alembic upgrade head",
        "python -m scripts.seed",
        "python -m scripts.verify_seed",
        "pg_restore",
        "predemo_check.sh",
        "backup_db.sh",
        "test_chaos_drill.py",
        "docker compose pull",
    ):
        assert keyword in DEPLOY_DOC, f"DEPLOY.md 缺少关键流程: {keyword}"


def test_predemo_and_backup_scripts_exist_with_core_steps():
    predemo = (ROOT / "scripts" / "predemo_check.sh").read_text(encoding="utf-8")
    for keyword in ("docker inspect", "sources/status", "api/v1/plans", "df -P"):
        assert keyword in predemo, f"predemo_check.sh 缺少检查项: {keyword}"
    backup = (ROOT / "scripts" / "backup_db.sh").read_text(encoding="utf-8")
    for keyword in ("pg_dump", "KEEP_COUNT=7", "docker compose"):
        assert keyword in backup, f"backup_db.sh 缺少步骤: {keyword}"


def test_secret_scan_passes_on_source_and_frontend_dist():
    script = ROOT / "scripts" / "scan_secrets.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"密钥泄露扫描未通过:\n{result.stdout}\n{result.stderr}"
    assert "扫描通过" in result.stdout