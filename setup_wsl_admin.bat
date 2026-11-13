@echo off
chcp 65001 >nul
title 彭城智游 - WSL2 安装（右键以管理员身份运行）
cd /d "%~dp0"

echo ==============================================
echo   第一步：安装 WSL2 + Ubuntu（自动，需几分钟）
echo ==============================================
echo 本脚本将启用 Windows 虚拟化平台并安装 Ubuntu，
echo 完成后需要【重启电脑一次】。
echo.

net session >nul 2>nul || (
    echo [错误] 权限不足！请在文件资源管理器中右键本文件，
    echo        选择【以管理员身份运行】。
    echo.
    pause
    exit /b 1
)

echo 开始安装（下载约 300MB，国内微软 CDN，通常 2-10 分钟）...
wsl --install -d Ubuntu
if errorlevel 1 (
    echo.
    echo 标准安装失败，尝试启用功能后由系统自行安装...
    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
    echo 功能已请求启用，重启后 WSL 会继续自动完成安装。
)

echo.
echo ==============================================
echo   安装请求已提交，接下来请【重启电脑】
echo ==============================================
echo.
echo 重启完成后，回到本项目文件夹双击 start_local.bat，
echo 数据库会自动完成安装配置（无需任何手动操作）。
echo.
pause