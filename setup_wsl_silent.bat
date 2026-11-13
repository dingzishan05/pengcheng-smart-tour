@echo off
chcp 65001 >nul
cd /d "%~dp0"
set LOG=C:\Users\lenovo\Desktop\彭城智游agent\wsl_install.log

echo [%date% %time%] 开始 WSL2 安装 > "%LOG%"

net session >nul 2>nul || (
    echo [错误] 权限不足 >> "%LOG%"
    exit /b 1
)
echo [OK] 管理员权限确认 >> "%LOG%"

echo --- wsl --install -d Ubuntu 开始（下载可能持续数分钟）--- >> "%LOG%"
wsl --install -d Ubuntu >> "%LOG%" 2>&1
echo [wsl --install 退出码 %errorlevel%] >> "%LOG%"

echo --- 功能状态查询 --- >> "%LOG%"
dism.exe /online /get-featureinfo /featurename:Microsoft-Windows-Subsystem-Linux 2>&1 | findstr /C:"State" >> "%LOG%"
dism.exe /online /get-featureinfo /featurename:VirtualMachinePlatform 2>&1 | findstr /C:"State" >> "%LOG%"

echo [%date% %time%] 脚本结束 >> "%LOG%"
exit /b 0