"""Test the real stdio protocol; --live submits two non-sensitive prompts."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    if result.isError:
        raise AssertionError(result)
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(item.text for item in result.content if item.type == "text"))


def compact(text):
    return "".join(text.split())


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "doubao_mcp"],
                                  cwd=str(Path(__file__).resolve().parents[1]),
                                  env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=360)) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert len(names) == 8, names
            print(json.dumps({"tools": names}, ensure_ascii=False), flush=True)
            invalid = payload(await session.call_tool("doubao_ask", {"prompt": ""}))
            assert invalid["code"] == "INVALID_PROMPT", invalid
            if "--protocol-only" in sys.argv:
                print("MCP protocol test passed (Doubao client not required).", flush=True)
                return
            status = payload(await session.call_tool("doubao_status"))
            assert status["ok"] and status["editor_available"], status
            print(json.dumps({"status": status}, ensure_ascii=False), flush=True)
            if "--live" in sys.argv:
                prompt = ("请严格原样回复下面两行，不要添加解释，也不要使用 Markdown：\n"
                          "桌面MCP测试通过\n2+3=5；符号 { } [ ] ( ) % ^")
                first = payload(await session.call_tool("doubao_ask", {"prompt": prompt, "timeout": 90, "new_chat": True}))
                print(json.dumps({"first": first}, ensure_ascii=False), flush=True)
                assert first["ok"] and first["complete"], first
                assert compact(first["reply"]) == compact("桌面MCP测试通过\n2+3=5；符号 { } [ ] ( ) % ^"), first
                follow = payload(await session.call_tool("doubao_ask", {
                    "prompt": "上一条回复的第二行是什么？请原样回复这一行，不要解释。",
                    "new_chat": False, "timeout": 90}))
                print(json.dumps({"follow_up": follow}, ensure_ascii=False), flush=True)
                assert follow["ok"] and follow["complete"], follow
                assert follow["conversation_id"] == first["conversation_id"], follow
                assert compact(follow["reply"]) == compact("2+3=5；符号{}[]()%^"), follow
            reply = payload(await session.call_tool("doubao_read_reply"))
            print(json.dumps({"read_reply": reply}, ensure_ascii=False), flush=True)
            print("MCP smoke test passed.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
