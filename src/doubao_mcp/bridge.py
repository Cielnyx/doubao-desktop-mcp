"""Run each background operation in a separate process with a bounded timeout."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


async def invoke(action: str, **parameters) -> dict:
    if action == "video_submit":
        deadline = 300
    elif action == "video_prepare":
        deadline = 220
    elif action == "video_download":
        deadline = float(parameters.get("timeout", 180)) + 120
    elif action == "video_open_detail":
        deadline = float(parameters.get("timeout", 30)) + 90
    elif action in {"image_upload", "image_bind"}:
        deadline = float(parameters.get("timeout", 90)) + 40
    elif action == "ask":
        deadline = float(parameters.get("timeout", 120)) + 45
    elif action == "switch_account":
        deadline = float(parameters.get("timeout", 30)) + 45
    else:
        deadline = 90 if action.startswith("video_") else {"read_history": 90, "open_conversation": 60}.get(action, 50)
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
        if action == "switch_account":
            return {"ok":False, "code":"DRIVER_TIMEOUT", "switch_attempted":True, "no_retry":True,
                    "message":"账号切换超时，结果未确认。请先列出账号核实当前身份，不要自动重试。"}
        if action in {"video_open_detail", "video_download"}:
            return {"ok":False, "code":"DRIVER_TIMEOUT", "request_id":parameters.get("request_id"),
                    "downloaded":False, "no_resend":True,
                    "message":"视频详情或下载超时；请检查目标任务和输出目录，不要重新生成。已有文件不会被覆盖。"}
        return {"ok": False, "code": "DRIVER_TIMEOUT",
                "request_id": parameters.get("request_id"), "no_resend": action == "video_submit",
                "message": "桌面驱动超时。操作可能已执行；视频请按原request_id查询，其他发送请先读回复，禁止自动重发。"}
    try:
        return json.loads(stdout.decode("utf-8"))
    except (ValueError, UnicodeError):
        return {"ok": False, "code": "DRIVER_FAILED",
                "message": stderr.decode("utf-8", errors="replace")[-2000:]
                or f"桌面驱动异常退出（{proc.returncode}）。"}

