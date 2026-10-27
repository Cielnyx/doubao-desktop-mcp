"""Developer entry point for the same subprocess bridge used by MCP."""
import asyncio
import json
import sys
from doubao_mcp.bridge import invoke


async def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    parameters = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(json.dumps(await invoke(action, **parameters), ensure_ascii=False, indent=2))


asyncio.run(main())
