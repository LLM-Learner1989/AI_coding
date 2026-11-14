from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable

DEFAULT_PORTS = (2024, 3000)


def find_pids_by_ports(ports: Iterable[int] = DEFAULT_PORTS) -> list[int]:
    """根据端口查找监听进程 PID（macOS 使用 lsof）。

    start_all.py 默认占用：
    - 2024：FastAPI/Uvicorn 后端
    - 3000：Vite 前端
    """

    pids: set[int] = set()
    for port in ports:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pids.add(int(line))
    return sorted(pids)


def stop_pids(pids: Iterable[int]) -> list[int]:
    """停止指定 PID，并返回实际发出停止命令的 PID 列表。"""

    stopped: list[int] = []
    for pid in sorted(set(pids)):
        if pid <= 0:
            continue
        subprocess.run(["kill", str(pid)], capture_output=True)
        stopped.append(pid)
    return stopped


def stop_ports(ports: Iterable[int] = DEFAULT_PORTS) -> list[int]:
    """停止指定端口上的监听进程。"""

    return stop_pids(find_pids_by_ports(ports))


def main() -> None:
    """清理课程项目常用的后端和前端服务端口。"""

    ports = DEFAULT_PORTS
    stopped = stop_ports(ports)
    if stopped:
        print(f"已停止端口 {ports} 对应进程：{stopped}")
    else:
        print(f"端口 {ports} 当前没有监听进程。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
