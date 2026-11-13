@echo off
chcp 65001 >nul
title 彭城智游 - WSL2 手动安装（绕过 Windows Update，勿关闭）
cd /d "%~dp0"
set LOG=C:\Users\lenovo\Desktop\彭城智游agent\wsl_manual.log

echo [%date% %time%] 开始手动安装 WSL2 > "%LOG%"

net session >nul 2>nul || (
    echo [错误] 需要管理员权限 >> "%LOG%"
    echo 请右键本文件，选择【以管理员身份运行】
    pause
    exit /b 1
)
echo [OK] 管理员权限确认 >> "%LOG%"

echo.
echo [1/4] 下载 WSL2 内核包（17MB）...
curl.exe -L --retry 3 -o wsl_update_x64.msi "https://wslstorestorage.blob.core.windows.net/wslblob/wsl_update_x64.msi" -# >> "%LOG%" 2>&1
if not exist wsl_update_x64.msi (
    echo [失败] 内核包下载失败，请检查网络后重试 >> "%LOG%"
    pause
    exit /b 1
)
echo [OK] 内核包已下载 >> "%LOG%"

echo.
echo [2/4] 下载 Ubuntu 22.04 LTS（341MB，需几分钟，请勿关闭窗口）...
curl.exe -L --retry 3 -o ubuntu-jammy-wsl.rootfs.tar.gz "https://cloud-images.ubuntu.com/wsl/jammy/current/ubuntu-jammy-wsl-amd64-ubuntu22.04lts.rootfs.tar.gz" -# >> "%LOG%" 2>&1
if not exist ubuntu-jammy-wsl.rootfs.tar.gz (
    echo [失败] Ubuntu 下载失败，请检查网络后重试 >> "%LOG%"
    pause
    exit /b 1
)
echo [OK] Ubuntu rootfs 已下载 >> "%LOG%"

echo.
echo [3/4] 启用 WSL 功能组件（不依赖 Windows Update）...
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart >> "%LOG%" 2>&1
echo [DISM 退出码 %errorlevel%] >> "%LOG%"
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart >> "%LOG%" 2>&1
echo [DISM 退出码 %errorlevel%] >> "%LOG%"

echo.
echo [4/4] 安装 WSL2 内核...
msiexec.exe /i "%~dp0wsl_update_x64.msi" /qn /norestart >> "%LOG%" 2>&1
echo [MSI 退出码 %errorlevel%] >> "%LOG%"

echo.
echo ==============================================
echo   安装完成！请【重启电脑】
echo ==============================================
echo 重启后回到对话中说"重启完了"，
echo 剩余步骤（导入Ubuntu+装数据库）将自动完成。
echo.
echo [%date% %time%] 脚本结束 >> "%LOG%"
pause