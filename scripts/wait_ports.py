"""等待 PostgreSQL/Redis 端口就绪（一键启动脚本用），最多 90 秒。"""

from __future__ import annotations

import socket
import sys
import time

HOST = "127.0.0.1"
PORTS = [(5432, "PostgreSQL"), (6379, "Redis")]


def wait(timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    pending = dict(PORTS)
    while pending and time.monotonic() < deadline:
        for port, name in list(pending.items()):
            try:
                with socket.create_connection((HOST, port), timeout=1):
                    print(f"[OK] {name} 端口 {port} 已就绪")
                    del pending[port]
            except OSError:
                pass
        if pending:
            time.sleep(1)
    return not pending


if __name__ == "__main__":
    sys.exit(0 if wait() else 1)