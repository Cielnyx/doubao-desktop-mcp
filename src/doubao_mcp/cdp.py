"""Small loopback-only CDP client; never activates a native window."""
import json
import os
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from websockets.sync.client import connect


class CDPError(RuntimeError):
    pass


class CDP:
    def __init__(self):
        self.port = int(os.environ.get("DOUBAO_CDP_PORT", "9229"))
        if not 1024 <= self.port <= 65535:
            raise CDPError("DOUBAO_CDP_PORT 必须在 1024–65535 之间。")
        self.socket = None
        self.sequence = 0
        self.target = None

    def open(self):
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(f"http://127.0.0.1:{self.port}/json/list", timeout=2) as response:
                targets = json.load(response)
        except Exception as error:
            raise CDPError("豆包后台通道未启用。请运行 scripts/enable_background.py --restart；不会回退为前台输入。") from error
        targets = [t for t in targets if t.get("type") == "page"
                   and t.get("url", "").startswith("doubao://doubao-chat/chat")]
        if len(targets) != 1:
            raise CDPError(f"无法确定唯一的豆包对话页面（发现 {len(targets)} 个）。")
        self.target = targets[0]
        uri = self.target["webSocketDebuggerUrl"]
        parsed = urlparse(uri)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.port != self.port:
            raise CDPError("拒绝连接非本机的后台通道。")
        self.socket = connect(uri, proxy=None, open_timeout=3, close_timeout=1, max_size=16 * 1024 * 1024)
        # Keep renderer layout/observers alive while the native app is minimized.
        # This emulates renderer focus only; it never activates the OS window.
        self.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
        return self

    def call(self, method, params=None, timeout=15):
        self.sequence += 1
        sequence = self.sequence
        self.socket.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = json.loads(self.socket.recv(timeout=max(0.01, deadline - time.monotonic())))
            if message.get("id") != sequence:
                continue
            if "error" in message:
                raise CDPError(str(message["error"]))
            return message.get("result", {})
        raise CDPError(f"后台调用超时：{method}")

    def evaluate(self, expression, timeout=15):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                           "awaitPromise": True, "timeout": int(timeout * 1000)}, timeout + 1)
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise CDPError(detail.get("exception", {}).get("description", detail.get("text", "页面执行异常")))
        return result.get("result", {}).get("value")

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
