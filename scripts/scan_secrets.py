"""密钥泄露扫描（任务 15.1，design/11 §10.3）：发布前对源码与前端产物执行。

扫描目标与规则：
- frontend/dist（前端产物，发布即公开）：严禁出现服务端密钥特征
  （sk- 前缀、LLM_API_KEY/AMAP_WEB_KEY/WEATHER_KEY/JWT_SECRET 等赋值）；
  AMAP_JS_KEY 为前端合法持有（高德域名白名单保护），不视为泄露；
- backend/app 与 nginx 配置：严禁硬编码密钥（sk- 前缀、密钥变量名后跟非占位值）；
- .env.example：必须是占位值。

用法：python scripts/scan_secrets.py（退出码 0 = 通过，1 = 发现泄露）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_VAR_PATTERN = re.compile(
    r"(LLM_API_KEY|AMAP_WEB_KEY|WEATHER_KEY|JWT_SECRET|ADMIN_INITIAL_PASSWORD)"
    r"\s*[:=]\s*[\"']?([^\s\"',)]+)",
    re.IGNORECASE,
)
SK_PATTERN = re.compile(r"sk-[A-Za-z0-9]{16,}")
LONG_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{40,}(?![A-Za-z0-9])")

PLACEHOLDER_MARKS = ("your-", "change-me", "xxxx", "generate-a-long", "<", "填入", "占位")

SCAN_DIRS = [
    (ROOT / "frontend" / "dist", {".js", ".html", ".css", ".svg", ".map"}),
    (ROOT / "backend" / "app", {".py"}),
]
EXTRA_FILES = [ROOT / "nginx" / "nginx.conf", ROOT / ".env.example"]


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(mark in lowered for mark in PLACEHOLDER_MARKS)


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in SK_PATTERN.finditer(line):
            findings.append(f"{path}:{lineno} 疑似 API Key 泄露: {match.group(0)[:12]}***")
        for match in SECRET_VAR_PATTERN.finditer(line):
            value = match.group(2)
            if not is_placeholder(value) and len(value) >= 8:
                findings.append(
                    f"{path}:{lineno} 疑似硬编码密钥: {match.group(1)}={value[:6]}***"
                )
        if path.suffix == ".py":
            for match in LONG_TOKEN_PATTERN.finditer(line):
                token = match.group(0)
                if "http" not in line.lower() and not token.isdigit():
                    findings.append(f"{path}:{lineno} 疑似长密钥串: {token[:8]}***")
    return findings


def main() -> int:
    all_findings: list[str] = []
    scanned = 0
    for directory, suffixes in SCAN_DIRS:
        if not directory.exists():
            print(f"[skip] 目录不存在: {directory}")
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                scanned += 1
                all_findings.extend(scan_file(path))
    for path in EXTRA_FILES:
        if path.exists():
            scanned += 1
            all_findings.extend(scan_file(path))

    env_example = ROOT / ".env.example"
    if env_example.exists():
        for lineno, line in enumerate(env_example.read_text(encoding="utf-8").splitlines(), 1):
            value = line.split("=", 1)[1].strip() if "=" in line else ""
            if value and SK_PATTERN.search(value):
                all_findings.append(f"{env_example}:{lineno} .env.example 含真实密钥特征")

    print(f"已扫描 {scanned} 个文件")
    if all_findings:
        print("发现疑似密钥泄露：")
        for finding in all_findings:
            print(f"  - {finding}")
        return 1
    print("扫描通过：未发现密钥泄露")
    return 0


if __name__ == "__main__":
    sys.exit(main())