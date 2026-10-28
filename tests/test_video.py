import unittest
from unittest.mock import Mock, patch

from doubao_mcp.background import BackgroundDoubao, BackgroundError


class VideoTests(unittest.TestCase):
    def test_invalid_parameters_do_not_touch_the_desktop(self):
        driver = BackgroundDoubao()
        with patch.object(driver, "page") as page:
            for params in ({"model":""}, {"model":123}, {"ratio":"0:9"},
                           {"ratio":"16:9;alert(1)"}, {"duration":True},
                           {"duration":5.5}, {"duration":301}, {"duration":0}):
                with self.subTest(params=params), self.assertRaises(BackgroundError):
                    driver.video_configure(**params)
            page.assert_not_called()

    def test_partial_request_preserves_unspecified_parameters(self):
        driver = BackgroundDoubao()
        with patch.object(driver, "page", return_value={"ok":True}) as page:
            driver.video_configure(ratio="自动")
            page.assert_called_once_with("video_configure", model=None, ratio="auto", duration=None)

    def test_rollback_evidence_reaches_the_caller(self):
        driver = BackgroundDoubao()
        driver.client = Mock()
        driver.client.evaluate.return_value = {"ok":False, "code":"VIDEO_OPTION_UNAVAILABLE",
            "message":"unsupported", "details":{"rollback_succeeded":True, "settings_may_have_changed":False}}
        with self.assertRaises(BackgroundError) as error:
            driver.video_configure(duration=300)
        self.assertTrue(error.exception.details["rollback_succeeded"])
        self.assertFalse(error.exception.details["settings_may_have_changed"])

    def test_ordinary_ask_cannot_insert_or_send_in_video_mode(self):
        driver = BackgroundDoubao()
        driver.client = Mock()
        def reject_video(action, **params):
            if action == "check_text_mode":
                raise BackgroundError("VIDEO_MODE_ACTIVE", "exit video first")
            raise AssertionError(action)
        driver.page = reject_video
        with self.assertRaises(BackgroundError) as error:
            driver.ask("this must never be inserted")
        self.assertEqual(error.exception.code, "VIDEO_MODE_ACTIVE")
        self.assertFalse(driver.sent)
        driver.client.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
