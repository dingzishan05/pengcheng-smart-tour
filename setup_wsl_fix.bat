@echo off
chcp 65001 >nul
title 彭城智游 - WSL2 安装修复（自动，勿关闭）
cd /d "%~dp0"
set LOG=C:\Users\lenovo\Desktop\彭城智游agent\wsl_install.log

echo [%date% %time%] 开始 WSL2 修复安装 > "%LOG%"

net session >nul 2>nul || (
    echo [错误] 需要管理员权限，请右键以管理员身份运行 >> "%LOG%"
    exit /b 1
)
echo [OK] 管理员权限确认 >> "%LOG%"

echo 正在重新启用 Windows Update 服务（被优化软件禁用导致安装失败）...
sc config wuauserv start= demand >> "%LOG%" 2>&1
net start wuauserv >> "%LOG%" 2>&1
echo [OK] wuauserv 状态处理完成 >> "%LOG%"

echo 正在继续安装 WSL2 + Ubuntu（下载约300MB，请耐心等待窗口自动关闭）...
wsl --install -d Ubuntu >> "%LOG%" 2>&1
echo [wsl --install 退出码 %errorlevel%] >> "%LOG%"

powershell -Command "cmd /c 'wsl --status > C:\Users\lenovo\Desktop\wsl_chk2.txt 2>&1'"
echo [%date% %time%] 修复脚本结束，请查看日志 >> "%LOG%"
exit /b 0