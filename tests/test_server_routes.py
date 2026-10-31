"""Public MCP defaults must keep formal downloads strict and navigation explicit."""
import unittest
from unittest.mock import AsyncMock, patch

from doubao_mcp import server


class ServerRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_download_requires_unwatermarked_and_allows_detail_navigation(self):
        with patch.object(server, "invoke", new_callable=AsyncMock, return_value={"ok":True}) as invoke:
            await server.doubao_video_download(directory="V:/output",message_id="123",task_id="456")
        args=invoke.call_args.kwargs
        self.assertEqual(invoke.call_args.args,("video_download",))
        self.assertEqual(args["resource_kind"],"unwatermarked")
        self.assertIs(args["open_detail_if_needed"],True)
        self.assertIsNone(args["video_index"])
        self.assertEqual((args["message_id"],args["task_id"]),("123","456"))

    async def test_explicit_preview_and_index_are_forwarded_without_changing_identity(self):
        with patch.object(server, "invoke", new_callable=AsyncMock, return_value={"ok":True}) as invoke:
            await server.doubao_video_download(directory="V:/output",conversation_id="100",message_id="200",task_id="300",
                resource_kind="preview",open_detail_if_needed=False,video_index=0)
        args=invoke.call_args.kwargs
        self.assertEqual(args["resource_kind"],"preview")
        self.assertIs(args["open_detail_if_needed"],False)
        self.assertEqual(args["video_index"],0)
        self.assertEqual((args["conversation_id"],args["message_id"],args["task_id"]),("100","200","300"))

    async def test_open_detail_keeps_all_explicit_target_ids(self):
        with patch.object(server, "invoke", new_callable=AsyncMock, return_value={"ok":True}) as invoke:
            await server.doubao_video_open_detail(conversation_id="100",message_id="200",task_id="300",timeout=20)
        self.assertEqual(invoke.call_args.args,("video_open_detail",))
        self.assertEqual(invoke.call_args.kwargs,{"request_id":None,"conversation_id":"100","message_id":"200",
                                                "task_id":"300","timeout":20})

    async def test_detail_and_download_are_advertised_as_navigation_writes(self):
        tools={tool.name:tool for tool in await server.mcp.list_tools()}
        for name in ("doubao_video_open_detail","doubao_video_download"):
            self.assertIs(tools[name].annotations.readOnlyHint,False)
        schema=tools["doubao_video_download"].inputSchema["properties"]
        self.assertEqual(schema["resource_kind"]["default"],"unwatermarked")
        self.assertIs(schema["open_detail_if_needed"]["default"],True)
        self.assertIsNone(schema["video_index"]["default"])


if __name__ == "__main__":
    unittest.main()
