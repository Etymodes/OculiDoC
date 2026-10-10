"""Exercise the frozen GUI executable's redirected worker pipes without hardware."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(executable: Path, *arguments: str, input_text: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(executable), *arguments],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"无法启动冻结进程管道：{error}") from error


def main(executable: Path) -> None:
    probe = _run(executable, "--stdio-smoke", input_text="OCULIDOC_STDIO_PROBE\n")
    if probe.returncode != 0:
        raise RuntimeError(f"冻结标准流自检失败：{probe.returncode}；{probe.stderr[-500:]}")
    try:
        response = json.loads(probe.stdout.strip())
    except ValueError as error:
        raise RuntimeError(f"冻结标准流未返回 JSON：{probe.stdout[-500:]}") from error
    if response.get("status") != "ok" or not response.get("version"):
        raise RuntimeError(f"冻结标准流返回内容无效：{response}")

    thinks = _run(executable, "--thinks-worker", input_text="{}\n")
    packets = [
        json.loads(line.removeprefix("OCULIDOC_THINKS:"))
        for line in thinks.stdout.splitlines()
        if line.startswith("OCULIDOC_THINKS:")
    ]
    if thinks.returncode != 1 or len(packets) != 1 or packets[0].get("event") != "error":
        raise RuntimeError(
            f"ThinksTracker 管道握手失败：exit={thinks.returncode}；"
            f"stdout={thinks.stdout[-500:]}；stderr={thinks.stderr[-500:]}"
        )
    print(f"FROZEN_STDIO_VERIFIED={response['version']}")
    print("THINKS_WORKER_PIPE_VERIFIED=PASS")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: check_frozen_stdio.py <OculiDoC.exe>")
    main(Path(sys.argv[1]))
