"""Real stdio MCP video-settings acceptance. Never submits video generation."""
import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from smoke_mcp import payload


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimize", action="store_true")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = {"submitted":False, "steps":[]}
    desktop = None

    async def call(session, name, arguments=None, expect_ok=True):
        observed = []
        done = asyncio.Event()
        async def watch():
            if desktop is None:
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
        if desktop is not None:
            assert observed and all(h != desktop.window.handle and minimized for h, minimized in observed), {"tool":name,"observed":observed}
        evidence["steps"].append({"tool":name,"ok":data.get("ok"),"code":data.get("code"),
                                  "samples":len(observed),"doubao_not_activated":desktop is not None})
        print(json.dumps({"tool":name,"ok":data.get("ok"),"current":data.get("current"),"code":data.get("code")}, ensure_ascii=False), flush=True)
        return data

    params = StdioServerParameters(command=sys.executable, args=["-m","doubao_mcp"], cwd=str(root),
                                  env={"PYTHONUTF8":"1","PYTHONIOENCODING":"utf-8"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=360)) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert len(names) == 21, names
            status = await call(session, "doubao_status")
            assert status["editor_available"] and not status["has_draft"] and not status["generating"], status
            if options.minimize:
                import win32con
                import win32gui
                from doubao_mcp.native_probe import NativeDoubaoProbe
                desktop = NativeDoubaoProbe()
                desktop.connect()
                win32gui.ShowWindow(desktop.window.handle, win32con.SW_MINIMIZE)
                await asyncio.sleep(0.8)
            original = None
            try:
                initial = await call(session, "doubao_video_options")
                original = initial["current"]
                evidence["models"] = [m["value"] for m in initial["models"] if not m["disabled"]]
                assert evidence["models"], initial
                evidence["ranges"] = []
                for model in evidence["models"]:
                    info = await call(session, "doubao_video_configure", {"model":model})
                    ratios = [r["value"] for r in info["ratios"] if not r["disabled"]]
                    assert ratios and info["durations"], info
                    ratio = next((r for r in ("9:16","16:9") if r in ratios), ratios[0])
                    duration = info["durations"][min(1,len(info["durations"])-1)]
                    selected = await call(session, "doubao_video_configure", {"ratio":ratio,"duration":duration})
                    assert selected["current"] == {"model":model,"ratio":ratio,"duration":duration}, selected
                    assert selected["submitted"] is False
                    evidence["ranges"].append({"model":model,"duration_range":info["duration_range"],"ratios":ratios})
                before_error = (await call(session, "doubao_video_options"))["current"]
                alternate = next((m for m in evidence["models"] if m != before_error["model"]), before_error["model"])
                invalid_duration = await call(session, "doubao_video_configure", {"model":alternate,"duration":300}, expect_ok=False)
                assert invalid_duration["code"] == "VIDEO_OPTION_UNAVAILABLE" and invalid_duration["rollback_succeeded"], invalid_duration
                after_error = await call(session, "doubao_video_options")
                assert after_error["current"] == before_error, after_error
                rejected = await call(session, "doubao_ask", {"prompt":"Video-mode guard check: this text must not be inserted."}, expect_ok=False)
                assert rejected["code"] == "VIDEO_MODE_ACTIVE" and rejected["sent"] is False, rejected
                unchanged = await call(session, "doubao_status")
                assert not unchanged["has_draft"] and unchanged["conversation_id"] == status["conversation_id"], unchanged
            finally:
                try:
                    if original is not None:
                        await call(session, "doubao_video_configure", original)
                finally:
                    if status.get("input_mode") != "video":
                        await call(session, "doubao_video_exit")
            final = await call(session, "doubao_status")
            assert final["input_mode"] == status["input_mode"] and not final["has_draft"] and not final["generating"], final
    output = root / ".runtime" / "video-smoke.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("Video settings MCP acceptance passed; no generation submitted.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
