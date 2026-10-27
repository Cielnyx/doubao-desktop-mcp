"""Run each background operation in a separate process with a bounded timeout."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


async def invoke(action: str, **parameters) -> dict:
    if action == "ask":
        deadline = float(parameters.get("timeout", 120)) + 45
    else:
        deadline = {"read_history": 90, "open_conversation": 60}.get(action, 50)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "doubao_mcp.driver", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=ROOT, env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps({"action": action, **parameters}).encode("utf-8")),
            timeout=deadline,
        )
    except (TimeoutError, asyncio.CancelledError):
        if proc.returncode is None:
            proc.kill()
        await proc.communicate()
        if asyncio.current_task().cancelling():
            raise
        return {"ok": False, "code": "DRIVER_TIMEOUT",
                "message": "桌面驱动超时。操作可能已执行；请先读取当前回复，不要自动重复发送。"}
    try:
        return json.loads(stdout.decode("utf-8"))
    except (ValueError, UnicodeError):
        return {"ok": False, "code": "DRIVER_FAILED",
                "message": stderr.decode("utf-8", errors="replace")[-2000:]
                or f"桌面驱动异常退出（{proc.returncode}）。"}

