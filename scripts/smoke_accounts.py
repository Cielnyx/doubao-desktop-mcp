"""Real stdio account smoke check. Default only lists and verifies a current-account no-op.

Use --target-account-id only when intentionally testing a switch to another saved account.
Does not add accounts, log out, send messages or generate videos.
"""
import argparse
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import win32gui

from smoke_mcp import payload


def desktop_handles():
    found = []

    def collect(handle, _):
        title = win32gui.GetWindowText(handle)
        if title == "豆包" or title.endswith(" - 豆包"):
            found.append(handle)

    win32gui.EnumWindows(collect, None)
    return found


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-account-id")
    parser.add_argument("--restore", action="store_true", help="跨账号切换成功后切回测试开始时的账号")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = {"steps":[], "cross_account_switch_tested":False}
    initial_foreground = win32gui.GetForegroundWindow()
    assert initial_foreground not in desktop_handles(), "请从其他应用执行后台验收，避免豆包一开始就在前台。"

    async def call(session, name, arguments=None):
        observed = []
        done = asyncio.Event()

        async def watch():
            while not done.is_set():
                observed.append(win32gui.GetForegroundWindow() in desktop_handles())
                await asyncio.sleep(.05)

        watcher = asyncio.create_task(watch())
        try:
            data = payload(await session.call_tool(name, arguments or {}))
        finally:
            done.set()
            await watcher
        assert observed and not any(observed), "豆包意外成为前台窗口。"
        evidence["steps"].append({"tool":name, "ok":data.get("ok"), "code":data.get("code"),
                                  "foreground_samples":len(observed), "doubao_activated":False})
        return data

    params = StdioServerParameters(command=sys.executable, args=["-m","doubao_mcp"], cwd=str(root),
                                  env={"PYTHONUTF8":"1","PYTHONIOENCODING":"utf-8"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=120)) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            assert len(tools) == 21 and {"doubao_list_accounts","doubao_switch_account"} <= tools, tools
            evidence["tool_count"] = len(tools)
            before = await call(session,"doubao_status")
            assert before["ok"], before
            accounts = await call(session,"doubao_list_accounts")
            assert accounts["ok"] and accounts["accounts"], accounts
            target = options.target_account_id or accounts["current_account"]["account_id"]
            result = await call(session,"doubao_switch_account",{"account_id":target})
            assert result["ok"] and result["current_account"]["account_id"] == target, result
            evidence["cross_account_switch_tested"] = target != accounts["current_account"]["account_id"]
            if not evidence["cross_account_switch_tested"]:
                assert result["already_active"] and not result["switch_attempted"], result
            missing = "0"
            while any(p["account_id"]==missing for p in accounts["accounts"]):
                missing += "0"
            invalid = await call(session,"doubao_switch_account",{"account_id":missing})
            assert invalid["code"] == "ACCOUNT_NOT_FOUND", invalid
            if evidence["cross_account_switch_tested"] and options.restore:
                restored = await call(session,"doubao_switch_account",
                                      {"account_id":accounts["current_account"]["account_id"]})
                assert restored["ok"] and restored["identity_verified"], restored
                assert restored["current_account"]["account_id"] == accounts["current_account"]["account_id"], restored
                evidence["restore_result"] = restored
            after = await call(session,"doubao_status")
            assert after["ok"], after
            if not evidence["cross_account_switch_tested"]:
                assert after["conversation_id"] == before["conversation_id"], after
                assert after["has_draft"] == before["has_draft"], after
            evidence.update(passed=True, accounts=accounts, switch_result=result)
    target_file = root/".runtime"/"account-smoke.json"
    target_file.parent.mkdir(exist_ok=True)
    target_file.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":True,"tool_count":len(tools),"account_count":accounts["count"],
                      "cross_account_switch_tested":evidence["cross_account_switch_tested"],
                      "restored":bool(evidence.get("restore_result")),
                      "foreground_samples":sum(step["foreground_samples"] for step in evidence["steps"])},ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
