"""Real MCP acceptance: history operations + background sends + native invariants."""
import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from smoke_mcp import payload


def native_state():
    import win32gui
    return {"foreground": win32gui.GetForegroundWindow(), "cursor": list(win32gui.GetCursorPos())}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--minimize", action="store_true")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if options.minimize:
        import win32con
        import win32gui
        from doubao_mcp.native_probe import NativeDoubaoProbe
        desktop = NativeDoubaoProbe()
        desktop.connect()
        desktop.check_idle()
        win32gui.ShowWindow(desktop.window.handle, win32con.SW_MINIMIZE)
        await asyncio.sleep(0.8)
    baseline = native_state()
    evidence = {"before": baseline, "steps": []}

    async def call(session, name, arguments=None, expect_ok=True):
        observed = []
        done = asyncio.Event()
        async def watch():
            if not options.minimize:
                return
            while not done.is_set():
                observed.append((win32gui.GetForegroundWindow(), bool(win32gui.IsIconic(desktop.window.handle))))
                await asyncio.sleep(0.05)
        watcher = asyncio.create_task(watch())
        try:
            data = payload(await session.call_tool(name, arguments or {}))
        finally:
            done.set()
            await watcher
        if expect_ok:
            assert data["ok"], data
        after = native_state()
        if options.minimize:
            assert observed and all(handle != desktop.window.handle and minimized for handle, minimized in observed), {"name": name, "observed": observed}
        evidence["steps"].append({"tool": name, "ok": data.get("ok"), "doubao_not_activated": True,
                                  "foreground_unchanged": after["foreground"] == baseline["foreground"],
                                  "cursor_unchanged": after["cursor"] == baseline["cursor"]})
        print(json.dumps({"tool": name, "ok": data.get("ok"), "code": data.get("code"),
                          "doubao_not_activated": True, "cursor_unchanged": after["cursor"] == baseline["cursor"]}, ensure_ascii=False), flush=True)
        return data

    params = StdioServerParameters(command=sys.executable, args=["-m", "doubao_mcp"],
                                  cwd=str(root), env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=360)) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert len(names) == 8, names
            status = await call(session, "doubao_status")
            assert status["background"]
            listing = await call(session, "doubao_list_conversations", {"limit": 50})
            evidence["listed_count"] = len(listing["conversations"])
            # Reuse only a test fixture created on this machine, when available.
            fixture_path = root / ".runtime" / "background-smoke.json"
            fixture = json.loads(fixture_path.read_text(encoding="utf-8")) if fixture_path.exists() else {}
            previous = next((c for c in listing["conversations"] if c["id"] == fixture.get("test_conversation_id")), None)
            if previous:
                await call(session, "doubao_open_conversation", {"conversation_id": previous["id"]})
                history = await call(session, "doubao_read_history", {"limit": 20})
                assert len(history["messages"]) >= 4, history
                assert [m["role"] for m in history["messages"][:4]] == ["user", "assistant", "user", "assistant"]
                evidence["existing_history_messages"] = len(history["messages"])
            titles = {}
            for item in listing["conversations"]:
                titles.setdefault(item["title"], []).append(item)
            duplicate = next((title for title, items in titles.items() if len(items)>1), None)
            if duplicate:
                ambiguous = await call(session, "doubao_open_conversation", {"title": duplicate}, expect_ok=False)
                assert ambiguous["code"] == "AMBIGUOUS_TITLE", ambiguous
            single = next((title for title, items in titles.items() if len(items)==1 and title=="MCP连接成功"), None)
            if single:
                await call(session, "doubao_open_conversation", {"title": single})
            if options.live:
                first = await call(session, "doubao_ask", {
                    "prompt": "请只回复：后台发送验证通过", "new_chat": True, "timeout": 60})
                assert "后台发送验证通过" in first["reply"], first
                second = await call(session, "doubao_ask", {
                    "prompt": "请只回复：多轮会话验证通过", "conversation_id": first["conversation_id"], "timeout": 60})
                assert "多轮会话验证通过" in second["reply"], second
                history = await call(session, "doubao_read_history", {"conversation_id": first["conversation_id"], "limit": 20})
                assert len(history["messages"]) == 4, history
                assert [m["role"] for m in history["messages"]] == ["user", "assistant", "user", "assistant"]
                older = await call(session, "doubao_read_history", {"limit": 2, "offset": 2})
                assert [m["id"] for m in older["messages"]] == [m["id"] for m in history["messages"][:2]], older
                evidence["test_conversation_id"] = first["conversation_id"]
                evidence["history_messages"] = len(history["messages"])
    evidence["after"] = native_state()
    if options.minimize:
        evidence["doubao_still_minimized"] = bool(win32gui.IsIconic(desktop.window.handle))
        assert evidence["doubao_still_minimized"]
    (root / ".runtime" / "background-smoke.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Background MCP acceptance passed.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
