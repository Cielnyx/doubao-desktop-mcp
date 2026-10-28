import json
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import Mock

from doubao_mcp.tasks import TaskError, TaskStore, VideoTaskReader, classify_task


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(self.temp.name)

    def test_same_request_is_replayed_without_new_attempt(self):
        payload = {"prompt":"完整提示词", "model":"Mini", "asset_ids":["image-a"]}
        first = self.store.reserve("request-a", payload, conversation_id="9007199254740993")
        self.assertTrue(first["created"])
        self.store.mark_attempt("request-a")
        replay = self.store.reserve("request-a", payload)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["record"]["conversation_id"], "9007199254740993")
        self.assertEqual(replay["record"]["attempt_count"], 1)
        with self.assertRaises(TaskError) as error:
            self.store.mark_attempt("request-a")
        self.assertEqual(error.exception.code, "DUPLICATE_SUBMISSION")

    def test_payload_conflict_does_not_change_old_request(self):
        self.store.reserve("request-a", {"prompt":"first"})
        with self.assertRaises(TaskError) as error:
            self.store.reserve("request-a", {"prompt":"second"})
        self.assertEqual(error.exception.code, "REQUEST_ID_CONFLICT")
        self.assertEqual(self.store.get("request-a")["payload"]["prompt"], "first")

    def test_crash_between_marker_and_ledger_still_blocks_resend(self):
        self.store.reserve("crash", {"prompt":"one"})
        Path(self.temp.name, "crash.attempt").write_text("{}")
        record = self.store.get("crash")
        self.assertTrue(record["submission_attempted"])
        self.assertEqual(record["state"], "uncertain")
        with self.assertRaises(TaskError):
            self.store.mark_attempt("crash")

    def test_concurrent_attempts_have_exactly_one_durable_winner(self):
        self.store.reserve("race", {"prompt":"one"})
        barrier = threading.Barrier(2)
        def attempt():
            barrier.wait()
            try:
                TaskStore(self.temp.name).mark_attempt("race")
                return "attempted"
            except TaskError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(results), ["DUPLICATE_SUBMISSION", "attempted"])
        self.assertEqual(self.store.get("race")["attempt_count"], 1)

    def test_real_binding_cannot_be_replaced_or_attempt_cleared(self):
        self.store.reserve("request-a", {"prompt":"one"})
        self.store.mark_attempt("request-a")
        self.store.update("request-a", task_id="9007199254740995")
        for fields in ({"task_id":"9007199254740996"}, {"submission_attempted":False},
                       {"attempt_count":0}, {"payload":{"prompt":"other"}}):
            with self.subTest(fields=fields), self.assertRaises(TaskError):
                self.store.update("request-a", **fields)

    def test_request_id_cannot_escape_storage_root(self):
        for value in ("../bad", "x/y", "..", "C:\\bad", "", "x.json"):
            with self.subTest(value=value), self.assertRaises(TaskError):
                self.store.reserve(value, {})

    def test_confirmation_is_a_separate_durable_single_attempt(self):
        self.store.reserve("confirm", {"prompt":"video"})
        self.store.mark_attempt("confirm")
        with self.assertRaises(TaskError):
            self.store.mark_confirmation_attempt("confirm", "确认生成", [])
        self.store.update("confirm", state="confirmation_required", user_message_id="101", confirmation_message_id="102")
        record = self.store.mark_confirmation_attempt("confirm", "确认生成", ["101","102"])
        self.assertTrue(record["confirmation_attempted"])
        self.assertEqual(record["confirmation_attempt_count"], 1)
        self.assertEqual(record["attempt_count"], 1)
        with self.assertRaises(TaskError) as error:
            self.store.mark_confirmation_attempt("confirm", "确认生成", ["101","102"])
        self.assertEqual(error.exception.code, "DUPLICATE_CONFIRMATION")
        for fields in ({"confirmation_attempted":False}, {"confirmation_attempt_count":0}):
            with self.assertRaises(TaskError):
                self.store.update("confirm", **fields)

    def test_confirmation_marker_crash_cannot_be_retried(self):
        self.store.reserve("confirm-crash", {"prompt":"video"})
        self.store.mark_attempt("confirm-crash")
        self.store.update("confirm-crash", state="confirmation_required", user_message_id="101")
        Path(self.temp.name, "confirm-crash.confirm-attempt").write_text("{}")
        record = self.store.get("confirm-crash")
        self.assertTrue(record["confirmation_attempted"])
        self.assertEqual(record["state"], "confirmation_unknown")
        with self.assertRaises(TaskError):
            self.store.mark_confirmation_attempt("confirm-crash", "确认生成", ["101"])


class ClassificationTests(unittest.TestCase):
    def test_states_are_assistant_scoped_and_positive_completion_wins(self):
        rows = [("正在排队，前方还有3人", "queued"), ("视频生成中", "generating"),
                ("视频生成失败", "failed"), ("今日生成次数已用完", "quota_exceeded"),
                ("确认后我再开始生成视频", "confirmation_required")]
        for text, expected in rows:
            with self.subTest(text=text):
                self.assertEqual(classify_task({"role":"assistant","text":text})["status"], expected)
                self.assertEqual(classify_task({"role":"user","text":text})["status"], "submitted")
        self.assertEqual(classify_task({"role":"assistant","text":"此前生成失败", "card":{"is_success":True}})["status"], "completed")

    def test_unknown_numeric_codes_are_not_guessed_as_success(self):
        result = classify_task({"role":"assistant","card":{"status_code":10},"text":""})
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["terminal"])


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(self.temp.name)
        self.driver = Mock()
        self.driver.status.return_value = {"conversation_id":"111"}
        self.reader = VideoTaskReader(self.driver, self.store)

    def row(self, message_id, role, text="", task_id=None, success=None):
        return {"message_id":message_id, "role":role, "text":text,
                "task_ids":[{"id":task_id,"source":"fixture"}] if task_id else [],
                "card":{"task_id":task_id,"is_success":success} if task_id else None, "videos":[]}

    def snapshot(self, rows):
        self.driver.page.return_value = {"conversation_id":"111","records":rows,"scan_complete":True}

    def test_uncertain_send_binds_only_unique_new_full_prompt_then_real_card(self):
        self.store.reserve("request-a", {"prompt":"full prompt","draft_text":"@图片1 full prompt"}, before_message_ids=["100"])
        self.store.mark_attempt("request-a")
        self.snapshot([self.row("100","user","@图片1 full prompt"),
                       self.row("9007199254740993","user","@图片1 full prompt"),
                       self.row("9007199254740994","assistant","视频生成中","remote-task")])
        result = self.reader.query(request_id="request-a")
        self.assertEqual(result["user_message_id"], "9007199254740993")
        self.assertEqual(result["message_id"], "9007199254740994")
        self.assertEqual(result["task_id"], "remote-task")
        self.assertEqual(result["status"], "generating")
        self.assertEqual(self.store.get("request-a")["task_id"], "remote-task")
        self.driver.open_conversation.assert_not_called()

    def test_duplicate_prompt_candidates_stay_unknown_and_do_not_guess(self):
        self.store.reserve("request-a", {"prompt":"same"})
        self.store.mark_attempt("request-a")
        self.snapshot([self.row("101","user","same"), self.row("102","assistant","done","one",True),
                       self.row("103","user","same"), self.row("104","assistant","done","two",True)])
        result = self.reader.query(request_id="request-a")
        self.assertEqual(result["status"], "submission_unknown")
        self.assertIsNone(result["task_id"])
        self.assertIsNone(self.store.get("request-a")["user_message_id"])

    def test_mention_rendering_requires_full_verified_visible_variant(self):
        self.store.reserve("mention", {"prompt":"@reference.png full prompt"})
        self.store.update("mention", draft_visible_text="reference.png full prompt")
        self.store.mark_attempt("mention")
        self.snapshot([self.row("101","user","reference.png wrong prompt"),
                       self.row("102","assistant","old task","old",True),
                       self.row("103","user","reference.png full prompt"),
                       self.row("104","assistant","视频生成中","new")])
        result = self.reader.query(request_id="mention")
        self.assertEqual(result["user_message_id"], "103")
        self.assertEqual(result["task_id"], "new")

    def test_known_task_does_not_adopt_a_different_completed_task(self):
        self.store.reserve("request-a", {"prompt":"one"}, conversation_id="111")
        self.store.mark_attempt("request-a")
        self.store.update("request-a", task_id="original")
        self.snapshot([self.row("101","assistant","done","other",True)])
        result = self.reader.query(request_id="request-a")
        self.assertEqual(result["status"], "not_visible")
        self.assertEqual(result["task_id"], "original")
        self.assertEqual(result["videos"], [])

    def test_multiple_tasks_are_reported_as_ambiguous(self):
        self.snapshot([self.row("101","assistant","done","one",True), self.row("102","assistant","done","two",True)])
        result = self.reader.query()
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["tasks"], ["one","two"])

    def test_explicit_task_cannot_rebind_a_request_to_another_prompt(self):
        self.store.reserve("request-a", {"prompt":"first"}, conversation_id="111")
        self.store.mark_attempt("request-a")
        self.store.update("request-a", user_message_id="101")
        self.snapshot([self.row("101","user","first"), self.row("102","assistant","done","first-task",True),
                       self.row("103","user","second"), self.row("104","assistant","done","second-task",True)])
        with self.assertRaises(TaskError) as error:
            self.reader.query(request_id="request-a", task_id="second-task")
        self.assertEqual(error.exception.code, "TASK_BINDING_CONFLICT")
        self.assertIsNone(self.store.get("request-a")["task_id"])

    def test_plain_streaming_reply_does_not_lock_request_to_early_message(self):
        self.store.reserve("request-a", {"prompt":"one"})
        self.store.mark_attempt("request-a")
        row = self.row("102","assistant","稍等")
        row["streaming"] = True
        self.snapshot([self.row("101","user","one"), row])
        result = self.reader.query(request_id="request-a")
        self.assertEqual(result["status"], "responding")
        self.assertIsNone(self.store.get("request-a")["message_id"])

    def test_real_client_wrapping_binds_original_input_and_keeps_confirmation_separate(self):
        resource = "tos-cn-example/reference.png"
        serialized = "以[reference.png](attachment://"+resource+"?type=1)为参考"
        self.store.reserve("wrapped", {"prompt":"以@reference.png为参考", "image_asset_ids":[resource]})
        self.store.update("wrapped", draft_serialized_text=serialized)
        self.store.mark_attempt("wrapped")
        attachment = self.row("200","user", "")
        attachment["message_meta"] = {"original_input":serialized,"input_asset_ids":[resource],"primary_message_id":"201"}
        user = self.row("201","user", "生成视频：以 reference.png 为参考，16:9，4s，720p")
        user["message_meta"] = attachment["message_meta"]
        assistant = self.row("100","assistant", "确认后我再开始生成视频")
        assistant["message_meta"] = {"reply_id":"201","confirmation_required":True}
        # IDs are not chronological; the real reply_id is authoritative.
        self.snapshot([assistant,attachment,user])
        result = self.reader.query(request_id="wrapped")
        self.assertEqual(result["status"], "confirmation_required")
        record = self.store.get("wrapped")
        self.assertEqual(record["user_message_id"], "201")
        self.assertEqual(record["confirmation_message_id"], "100")
        self.assertIsNone(record["message_id"])

    def test_confirmation_reply_can_bind_final_task_without_rebinding_initial_confirmation(self):
        self.store.reserve("confirmed", {"prompt":"video"}, conversation_id="111")
        self.store.mark_attempt("confirmed")
        self.store.update("confirmed", state="confirmation_required",user_message_id="101",confirmation_message_id="102")
        self.store.mark_confirmation_attempt("confirmed", "确认按原参数开始生成", ["101","102"])
        old = self.row("102","assistant","确认后生成")
        old["message_meta"] = {"reply_id":"101","confirmation_required":True}
        current = self.row("050","assistant","正在生成","actual-task")
        current["message_meta"] = {"reply_id":"201"}
        confirmed_user = self.row("201","user","确认 按原参数 开始生成")
        confirmed_user["message_meta"] = {"raw_text":"确认按原参数开始生成"}
        self.snapshot([self.row("101","user","video"),old,confirmed_user,current])
        result = self.reader.query(request_id="confirmed")
        self.assertEqual(result["status"], "generating")
        self.assertEqual(result["user_message_id"], "101")
        self.assertEqual(result["confirmation_user_message_id"], "201")
        self.assertEqual(result["task_id"], "actual-task")
        record = self.store.get("confirmed")
        self.assertEqual(record["confirmation_message_id"], "102")
        self.assertEqual(record["message_id"], "050")

    def test_missing_confirmation_receipt_stays_unknown_instead_of_returning_old_card(self):
        self.store.reserve("confirmed", {"prompt":"video"}, conversation_id="111")
        self.store.mark_attempt("confirmed")
        self.store.update("confirmed", state="confirmation_required",user_message_id="101",confirmation_message_id="102")
        self.store.mark_confirmation_attempt("confirmed", "确认生成", ["101","102"])
        self.snapshot([self.row("101","user","video"),self.row("102","assistant","确认后生成")])
        result = self.reader.query(request_id="confirmed")
        self.assertEqual(result["status"], "submission_unknown")
        self.assertTrue(result["confirmation_attempted"])
        self.assertIsNone(result["message_id"])


if __name__ == "__main__":
    unittest.main()
