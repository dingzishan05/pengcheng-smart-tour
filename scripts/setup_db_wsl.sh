#!/bin/bash
# WSL2 Ubuntu 内初始化数据库（jammy 22.04，root 执行）：
# PostgreSQL 16 + pgvector（经华为云 PGDG 镜像）+ Redis，预置 pczy/pczy/pczy。
set -e
export DEBIAN_FRONTEND=noninteractive

echo "[1/6] 配置 apt 国内镜像（华为云）..."
if [ -f /etc/apt/sources.list ]; then
    sed -i 's|https\?://archive.ubuntu.com|https://mirrors.huaweicloud.com|g; s|https\?://security.ubuntu.com|https://mirrors.huaweicloud.com|g; s|https\?://cn.archive.ubuntu.com|https://mirrors.huaweicloud.com|g' /etc/apt/sources.list
fi
for f in /etc/apt/sources.list.d/*.sources; do
    [ -f "$f" ] && sed -i 's|https\?://archive.ubuntu.com|https://mirrors.huaweicloud.com|g; s|https\?://security.ubuntu.com|https://mirrors.huaweicloud.com|g' "$f" || true
done

echo "[2/6] 添加 PostgreSQL 16 PGDG 仓库（华为云镜像）..."
apt-get update -qq 2>/dev/null || true
apt-get install -y -qq curl ca-certificates gnupg 2>/dev/null | tail -1 || true
DISTRIB_CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://mirrors.huaweicloud.com/postgresql/repos/apt/ ${DISTRIB_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list
curl -fsSL https://mirrors.huaweicloud.com/postgresql/repos/apt/ACCC4CF8.asc -o /tmp/pgdg.asc 2>/dev/null || curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /tmp/pgdg.asc
gpg --dearmor -o /usr/share/keyrings/pgdg.gpg /tmp/pgdg.asc 2>/dev/null || cp /tmp/pgdg.asc /usr/share/keyrings/pgdg.gpg

echo "[3/6] 安装 PostgreSQL 16 + pgvector + Redis..."
apt-get update -qq
apt-get install -y -qq postgresql-16 postgresql-16-pgvector redis-server 2>&1 | tail -2

echo "[4/6] 配置 PostgreSQL 网络与账号..."
PG_CONF=/etc/postgresql/16/main/postgresql.conf
PG_HBA=/etc/postgresql/16/main/pg_hba.conf
sed -i "s/^#\?listen_addresses.*/listen_addresses = '*'/" "$PG_CONF"
grep -q "host all all 0.0.0.0/0" "$PG_HBA" || echo "host all all 0.0.0.0/0 scram-sha-256" >> "$PG_HBA"

echo "[5/6] 启动服务并建库..."
service postgresql start || pg_ctlcluster 16 main start
sleep 3
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='pczy'" | grep -q 1 || sudo -u postgres psql -c "CREATE USER pczy WITH PASSWORD 'pczy' CREATEDB;"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='pczy'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE pczy OWNER pczy;"
sudo -u postgres psql -d pczy -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "[6/6] 配置 Redis..."
grep -q "^bind 0.0.0.0" /etc/redis/redis.conf || sed -i 's/^bind .*/bind 0.0.0.0/' /etc/redis/redis.conf
grep -q "^protected-mode no" /etc/redis/redis.conf || echo "protected-mode no" >> /etc/redis/redis.conf
service redis-server restart || service redis-server start || redis-server --daemonize yes

redis-cli ping
sudo -u postgres psql -d pczy -tAc "SELECT extversion FROM pg_extension WHERE extname='vector'"
echo SETUP_DB_DONE