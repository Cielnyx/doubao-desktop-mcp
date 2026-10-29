import unittest
from unittest.mock import Mock, patch
from websockets.exceptions import ConnectionClosed

from doubao_mcp.accounts import AccountDriver
from doubao_mcp.background import BackgroundError
from doubao_mcp.cdp import CDPError


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.a = {"account_id":"101", "nickname":"主账号", "current":True}
        self.b = {"account_id":"202", "nickname":"工作账号", "current":False}
        self.data = {"ok":True, "current_account":self.a, "accounts":[self.a,self.b]}
        self.driver = Mock()
        self.subject = AccountDriver(self.driver)
        self.sequence = []
        self.observations = [{"current_account":self.b, "editor_available":True, "dialog_present":False}]
        self.clicked = 0
        self.send_error = None

        def page(action, **params):
            self.sequence.append(action)
            if action == "account_list":
                return self.data
            if action == "account_switch_preflight":
                return {"proof":{"account_id":params["account_id"], "from_account_id":"101", "route":"/chat/1"}}
            if action == "account_switch_once":
                self.clicked += 1
                if self.send_error:
                    raise self.send_error
                return {"clicked":True}
            if action == "account_observe":
                return self.observations.pop(0) if len(self.observations)>1 else self.observations[0]
            raise AssertionError(action)

        self.driver.page.side_effect = page

    def test_invalid_targets_and_timeout_do_not_touch_desktop(self):
        for args in ({}, {"account_id":"202", "nickname":"x"}, {"account_id":202},
                     {"account_id":"../202"}, {"nickname":" "}, {"account_id":"202", "timeout":True},
                     {"account_id":"202", "timeout":4}, {"account_id":"202", "timeout":61}):
            with self.subTest(args=args), self.assertRaises(BackgroundError):
                self.subject.switch(**args)
        self.driver.page.assert_not_called()

    def test_current_account_is_a_noop_even_with_a_draft(self):
        result = self.subject.switch(account_id="101")
        self.assertTrue(result["already_active"])
        self.assertFalse(result["switch_attempted"])
        self.assertEqual(self.sequence,["account_list"])

    def test_nickname_is_exact_and_duplicate_names_need_id(self):
        self.data["accounts"] += [{**self.b,"account_id":"303"}]
        with self.assertRaises(BackgroundError) as caught:
            self.subject.switch(nickname="工作账号")
        self.assertEqual(caught.exception.code,"AMBIGUOUS_ACCOUNT")
        self.assertEqual(len(caught.exception.details["candidates"]),2)
        self.assertEqual(self.clicked,0)

    def test_missing_account_does_not_add_or_logout(self):
        with self.assertRaises(BackgroundError) as caught:
            self.subject.switch(nickname="工作")
        self.assertEqual(caught.exception.code,"ACCOUNT_NOT_FOUND")
        self.assertEqual(self.sequence,["account_list"])

    def test_success_requires_target_identity_and_ready_editor(self):
        self.observations = [
            {"current_account":self.a,"editor_available":True},
            {"current_account":self.b,"editor_available":False},
            {"current_account":self.b,"editor_available":True,"dialog_present":True},
            {"current_account":self.b,"editor_available":True,"dialog_present":False},
        ]
        with patch("doubao_mcp.accounts.time.sleep"):
            result = self.subject.switch(nickname="工作账号")
        self.assertTrue(result["identity_verified"])
        self.assertEqual(result["current_account"]["account_id"],"202")
        self.assertEqual(self.clicked,1)
        self.assertEqual(self.sequence.count("account_observe"),4)

    def test_interrupted_click_ack_is_only_polled_never_clicked_again(self):
        self.send_error = CDPError("document replaced")
        result = self.subject.switch(account_id="202")
        self.assertTrue(result["switched"])
        self.assertEqual(self.clicked,1)
        self.driver.close.assert_called_once()

    def test_socket_closed_by_real_client_reload_reconnects_without_clicking_again(self):
        self.send_error = ConnectionClosed(None,None)
        result = self.subject.switch(account_id="202")
        self.assertTrue(result["identity_verified"])
        self.assertEqual(self.clicked,1)
        self.driver.close.assert_called_once()

    def test_guard_failure_does_not_poll_or_click_again(self):
        self.send_error = BackgroundError("DRAFT_PRESENT","a draft appeared")
        with self.assertRaises(BackgroundError) as caught:
            self.subject.switch(account_id="202")
        self.assertFalse(caught.exception.details["switch_attempted"])
        self.assertNotIn("account_observe",self.sequence)

    def test_stale_preflight_account_never_clicks(self):
        original = self.driver.page.side_effect
        self.driver.page.side_effect = lambda action,**kw: ({"proof":{"account_id":"202",
            "from_account_id":"303","route":"/chat/1"}} if action=="account_switch_preflight" else original(action,**kw))
        with self.assertRaises(BackgroundError) as caught:
            self.subject.switch(account_id="202")
        self.assertEqual(caught.exception.code,"ACCOUNT_CONTEXT_CHANGED")
        self.assertEqual(self.clicked,0)

    def test_login_required_does_not_attempt_to_submit_credentials(self):
        self.observations = [{"login_required":True,"current_account":self.a}]
        result = self.subject.switch(account_id="202")
        self.assertEqual(result["code"],"ACCOUNT_LOGIN_REQUIRED")
        self.assertTrue(result["no_retry"])
        self.assertEqual(self.clicked,1)

    def test_timeout_returns_uncertainty_instead_of_old_identity_success(self):
        self.observations = [{"current_account":self.a,"editor_available":True}]
        with patch("doubao_mcp.accounts.time.monotonic",side_effect=[0,0,10]), patch("doubao_mcp.accounts.time.sleep"):
            result = self.subject.switch(account_id="202",timeout=5)
        self.assertEqual(result["code"],"ACCOUNT_SWITCH_UNCONFIRMED")
        self.assertTrue(result["no_retry"])
        self.assertEqual(self.clicked,1)


if __name__ == "__main__":
    unittest.main()
