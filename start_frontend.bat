@echo off
chcp 65001 >nul
title 彭城智游 - 前端
cd /d "%~dp0frontend"

set "VITE_AMAP_JS_KEY="
set "VITE_AMAP_SECURITY_CODE="
if exist "..\backend\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("..\backend\.env") do (
        if /i "%%a"=="AMAP_JS_KEY" set "VITE_AMAP_JS_KEY=%%b"
        if /i "%%a"=="AMAP_SECURITY_CODE" set "VITE_AMAP_SECURITY_CODE=%%b"
    )
)

echo 前端启动中... 浏览器访问 http://localhost:5173
echo 关闭本窗口即可停止前端。
echo.
call npm run dev
pause