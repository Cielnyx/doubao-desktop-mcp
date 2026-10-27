import unittest
from unittest.mock import patch

from doubao_mcp.background import BackgroundDoubao, BackgroundError, normalize_id
from doubao_mcp.cdp import CDPError


class BackgroundTests(unittest.TestCase):
    def test_large_ids_remain_exact_and_invalid_routes_are_rejected(self):
        self.assertEqual(normalize_id("conversation_38449662928819970"), "38449662928819970")
        for value in ("../chat/1", "1?x=2", "https://example.com", "1;alert(1)"):
            with self.assertRaises(BackgroundError):
                normalize_id(value)

    def test_missing_background_channel_never_falls_back(self):
        with patch("doubao_mcp.background.CDP") as factory:
            factory.return_value.open.side_effect = CDPError("channel absent")
            with self.assertRaises(BackgroundError) as error:
                BackgroundDoubao().status()
            self.assertEqual(error.exception.code, "BACKGROUND_UNAVAILABLE")

    def test_duplicate_title_never_opens_an_arbitrary_conversation(self):
        driver = BackgroundDoubao()
        driver.list_conversations = lambda **kw: {"conversations": [
            {"id":"1","title":"同名"}, {"id":"2","title":"同名"}], "scan_complete": True}
        with patch.object(driver, "page") as page:
            with self.assertRaises(BackgroundError) as error:
                driver.open_conversation(title="同名")
            self.assertEqual(error.exception.code, "AMBIGUOUS_TITLE")
            self.assertEqual(len(error.exception.details["candidates"]), 2)
            page.assert_not_called()

    def test_incomplete_title_search_does_not_claim_uniqueness(self):
        driver = BackgroundDoubao()
        driver.list_conversations = lambda **kw: {"conversations": [{"id":"1","title":"标题"}], "scan_complete": False}
        with self.assertRaises(BackgroundError) as error:
            driver.open_conversation(title="标题")
        self.assertEqual(error.exception.code, "SEARCH_INCOMPLETE")

    def test_invalid_request_is_rejected_before_any_input(self):
        driver = BackgroundDoubao()
        with patch.object(driver, "page") as page:
            for options in ({"prompt":"test", "new_chat":True, "conversation_id":"1"},
                            {"prompt":""}, {"prompt":"hello", "timeout":1}):
                with self.assertRaises(BackgroundError):
                    driver.ask(**options)
            page.assert_not_called()

    def test_virtualized_history_keeps_ids_order_and_latest_slice(self):
        driver = BackgroundDoubao()
        def message(number):
            return {"id":str(9007199254740992+number), "role":"assistant" if number%2==0 else "user",
                    "text":f"消息{number}", "row":number, "complete":True}
        state = {"top":100}
        def page(action, **params):
            if action == "snapshot":
                items = [message(3),message(4)] if state["top"] else [message(1),message(2),message(3)]
                return {"conversation_id":"123", "messages":items}
            if action == "scroll_messages":
                if "position" in params:
                    position=params["position"]
                    state["top"] = 100 if position=="bottom" else int(position)
                return {"available":True,"top":state["top"],"height":100,"total":200}
            raise AssertionError(action)
        driver.page = page
        with patch("doubao_mcp.background.time.sleep"):
            result = driver.read_history(limit=2)
        self.assertEqual([m["text"] for m in result["messages"]], ["消息3","消息4"])
        self.assertTrue(result["has_older"])
        self.assertEqual(state["top"], 100)


if __name__ == "__main__":
    unittest.main()
