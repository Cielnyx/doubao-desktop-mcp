"""One-time opt-in restart into a loopback-only DevTools session."""
import argparse
import json
from pathlib import Path
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

import psutil
from doubao_mcp.native_probe import NativeDoubaoProbe
from doubao_mcp.locking import desktop_lock


def probe(port):
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            return json.load(response)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--port", type=int, default=9229)
    args = parser.parse_args()
    if probe(args.port):
        print("后台控制通道已启用。")
        return
    if not args.restart:
        raise SystemExit("通道未启用。使用 --restart 在检查空闲状态后重启豆包一次。")
    with desktop_lock():
        desktop = NativeDoubaoProbe()
        desktop.connect()
        desktop.check_idle()
        parent = psutil.Process(desktop.window.process_id())
        executable = parent.exe()
        if Path(executable).name.lower() != "doubao.exe":
            raise SystemExit("目标进程不是豆包，停止。")
        children = parent.children(recursive=True)
        # Close the main window first so Chromium can save its current state.
        desktop.window.close()
        time.sleep(2)
        if parent.is_running():
            parent.terminate()
        _, remaining = psutil.wait_procs([parent, *children], timeout=5)
        for process in remaining:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        subprocess.Popen([executable, f"--remote-debugging-port={args.port}",
                          "--remote-debugging-address=127.0.0.1", "--no-first-run"],
                         cwd=str(Path(executable).parent))
    for _ in range(30):
        result = probe(args.port)
        if result:
            print(json.dumps({"ok": True, "endpoint": f"http://127.0.0.1:{args.port}",
                              "browser": result.get("Browser")}, ensure_ascii=False))
            return
        time.sleep(0.5)
    raise SystemExit("豆包已重新启动，但后台通道未就绪。未修改登录资料。")


if __name__ == "__main__":
    main()
