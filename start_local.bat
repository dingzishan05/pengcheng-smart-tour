@echo off
chcp 65001 >nul
title 彭城智游 - 一键启动
cd /d "%~dp0"

echo ==============================================
echo   彭城智游 本地一键启动
echo ==============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 缺少 .venv 虚拟环境，请联系开发者。
    pause
    exit /b 1
)

if not exist "backend\.env" (
    echo [步骤 1/4] 首次运行：生成 backend\.env 配置文件
    copy ".env.example" "backend\.env" >nul
    echo.
    echo 即将打开记事本，请填入 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL /
    echo EMBEDDING_MODEL / AMAP_WEB_KEY / AMAP_JS_KEY / WEATHER_KEY / JWT_SECRET，
    echo 填完后【保存并关闭记事本】，脚本会自动继续...
    echo.
    notepad "backend\.env"
)

set "DB_UP=0"
netstat -an | findstr ":5432 " | findstr "LISTENING" >nul 2>nul && set "DB_UP=1"

if "%DB_UP%"=="1" (
    echo [步骤 2/4] 检测到本机 PostgreSQL 已在运行。
    goto db_ready
)

echo [步骤 2/4] PostgreSQL 未运行，尝试从 WSL2 Ubuntu 启动数据库...
wsl -d Ubuntu -u root --exec service postgresql start >nul 2>nul
wsl -d Ubuntu -u root --exec service redis-server start >nul 2>nul
timeout /t 3 /nobreak >nul
netstat -an | findstr ":5432 " | findstr "LISTENING" >nul 2>nul && set "DB_UP=1"

if "%DB_UP%"=="1" (
    echo [步骤 2/4] WSL2 数据库已启动。
    goto db_ready
)

where docker >nul 2>nul && (
    echo 未找到 WSL 数据库，尝试用 Docker 拉起数据库容器...
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis
    timeout /t 5 /nobreak >nul
    netstat -an | findstr ":5432 " | findstr "LISTENING" >nul 2>nul && set "DB_UP=1"
)

if "%DB_UP%"=="1" (
    echo [步骤 2/4] Docker 数据库容器已启动。
    goto db_ready
)

echo 检测到 WSL2 已安装 Ubuntu，首次为它安装数据库（约 5 分钟，仅此一次）...
wsl -d Ubuntu -u root --exec bash "/mnt/c/Users/lenovo/Desktop/彭城智游agent/scripts/setup_db_wsl.sh"
wsl -d Ubuntu -u root --exec service postgresql start >nul 2>nul
wsl -d Ubuntu -u root --exec service redis-server start >nul 2>nul
timeout /t 3 /nobreak >nul
netstat -an | findstr ":5432 " | findstr "LISTENING" >nul 2>nul && set "DB_UP=1"

if "%DB_UP%"=="0" (
    echo. & echo [错误] 无法启动数据库。两种解决方式： & echo   方式一：右键以管理员身份运行 setup_wsl_admin.bat 安装 WSL2，重启后再双击本脚本 & echo   方式二：安装 Docker Desktop 后重新双击本脚本 & echo. & pause & exit /b 1
)

:db_ready
echo 等待数据库就绪，最多 90 秒...
".venv\Scripts\python.exe" "scripts\wait_ports.py" || (
    echo. & echo [错误] 数据库未就绪。常见原因： & echo   1. WSL2/Docker 尚未完全启动，请稍等后重试 & echo   2. 数据库端口不是 5432/6379 & echo. & pause & exit /b 1
)

echo [步骤 3/4] 数据库建表 + 写入种子数据，已存在会自动跳过...
cd /d "%~dp0backend"
"%~dp0.venv\Scripts\python.exe" -m alembic upgrade head || (
    echo. & echo [失败] 建表失败：请确认数据库已就绪、backend\.env 的 DATABASE_URL 正确。 & pause & exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m scripts.seed || (
    echo. & echo [失败] 种子数据写入失败：常见原因是 .env 密钥未填全，或数据库未就绪。 & echo 处理后重新双击本脚本即可，已写入的数据会自动跳过。 & pause & exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m scripts.verify_seed || (
    echo. & echo [失败] 种子自检未通过，请查看上方报错。 & pause & exit /b 1
)

cd /d "%~dp0"
echo [步骤 4/4] 启动前端新窗口 + 后端当前窗口...
start "彭城智游-前端" cmd /k ""%~dp0start_frontend.bat""
echo.
echo 浏览器访问: http://localhost:5173
echo 后端健康检查: http://localhost:8000/health
echo 停止服务：关闭对应窗口即可。
echo.
cd /d "%~dp0backend"
echo 后端启动中，出现 Application startup complete 即就绪。
"%~dp0.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause