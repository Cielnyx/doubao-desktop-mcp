"""Submission invariants: one attempt, verified preflight, original-task lookup."""
import unittest
import tempfile
from unittest.mock import Mock, patch

from doubao_mcp.background import BackgroundError
from doubao_mcp.media import MediaError
from doubao_mcp.tasks import TaskError, TaskStore
from doubao_mcp.prompts import build_video_prompt
from doubao_mcp.workflow import VideoWorkflow


class WorkflowTests(unittest.TestCase):
    def fixture(self, attempted=False, existing=False, record_conversation="12345678901234567"):
        record={"request_id":"test-request", "submission_attempted":attempted,
                "conversation_id":record_conversation, "user_message_id":None,
                "payload":{"duration":4}}
        store=Mock()
        state={"exists":existing or attempted}
        def reserve(request_id,payload,conversation=None,before=None):
            created=not state["exists"]
            state["exists"]=True
            if created:
                record["conversation_id"]=conversation
            record["payload"]=payload
            return {"created":created,"record":record}
        def mark(request_id):
            record["submission_attempted"]=True
            return record
        def update(request_id,**fields):
            record.update(fields)
            return record
        store.reserve.side_effect=reserve
        store.mark_attempt.side_effect=mark
        store.update.side_effect=update
        def get(request_id):
            if not state["exists"]:
                raise TaskError("REQUEST_NOT_FOUND","no prior request")
            return record
        store.get.side_effect=get
        driver=Mock()
        driver.sent=False
        def page(action,**params):
            if action == "snapshot":
                return {"conversation_id":"12345678901234567","messages":[{"id":"9007199254740993"}]}
            if action == "video_preflight":
                return {"ok":True,"verified":True,"proof":{"hash":"verified-proof"},
                        "conversation_id":"12345678901234567","media":{"context":{
                            "route":"/chat/12345678901234567","input_instance_id":"input-instance-real"}}}
            if action == "video_current":
                return {"ok":True,"current":{"model":"Seedance 2.0 Mini","ratio":"16:9","duration":4}}
            if action == "video_send_once":
                return {"ok":True,"clicked":True,"conversation_id":"12345678901234567"}
            raise AssertionError(action)
        driver.page.side_effect=page
        reader=Mock()
        reader.query.return_value={"ok":True,"status":"generating","message_id":"message-real",
                                   "task_id":"task-real","conversation_id":record_conversation,"videos":[]}
        media=Mock()
        media.inspect.return_value={"context":{"route":"/chat/12345678901234567","input_instance_id":"input-instance-real"}}
        with patch("doubao_mcp.workflow.VideoTaskReader",return_value=reader):
            workflow=VideoWorkflow(driver,store)
        return workflow,store,driver,reader,media

    def submit(self,workflow,**changes):
        options={"request_id":"test-request","prompt":"animate @image.png gently",
                 "model":"Seedance 2.0 Mini","ratio":"16:9","duration":4,
                 "image_asset_ids":["asset-real"],"wait_seconds":0,**changes}
        return workflow.submit(**options)

    def test_repeated_submit_never_prepares_or_sends_again(self):
        workflow,store,driver,reader,media=self.fixture()
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            first=self.submit(workflow)
            second=self.submit(workflow)
        self.assertTrue(first["send_clicked"] and second["no_resend"] and second["reused_request"])
        self.assertEqual(media.prepare_prompt.call_count,1)
        self.assertEqual(store.mark_attempt.call_count,1)
        self.assertEqual(sum(call.args[0]=="video_send_once" for call in driver.page.call_args_list),1)

    def test_submit_adds_actual_model_prefix_and_preserves_reference_prompt(self):
        workflow,store,driver,reader,media=self.fixture()
        original="animate @image.png gently\nkeep the orange square still"
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            self.submit(workflow,prompt=original)
        complete=media.prepare_prompt.call_args.args[0]
        self.assertTrue(complete.startswith("帮我用Seedance 2.0 Mini生成视频。"))
        self.assertTrue(complete.endswith(original))
        self.assertIn("不要再次询问",complete)
        self.assertEqual(media.prepare_prompt.call_args.kwargs["source_prompt"],original)
        preflight=next(c for c in driver.page.call_args_list if c.args[0]=="video_preflight")
        self.assertEqual(preflight.kwargs["prompt"],complete)

    def test_preparation_infers_actual_model_and_returns_original_and_wire_text(self):
        workflow,store,driver,reader,media=self.fixture()
        media.prepare_prompt.return_value={"ok":True,"verified":True}
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            result=workflow.prepare("animate @image.png",["asset-real"])
        self.assertEqual(result["user_prompt"],"animate @image.png")
        self.assertEqual(result["model"],"Seedance 2.0 Mini")
        self.assertEqual(result["effective_prompt"],build_video_prompt(result["model"],result["user_prompt"]))
        store.mark_attempt.assert_not_called()

    def test_wrapped_prompt_replay_keeps_original_idempotency_payload(self):
        workflow,store,driver,reader,media=self.fixture()
        original="animate @image.png gently"
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            self.submit(workflow,prompt=original)
            replay=self.submit(workflow,prompt=build_video_prompt("Seedance 2.0 Mini",original))
        self.assertTrue(replay["no_resend"])
        self.assertEqual(media.prepare_prompt.call_count,1)
        self.assertEqual(store.reserve.call_args.args[1]["prompt"],original)

    def test_legacy_attempted_request_is_still_query_only_after_prefix_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            store=TaskStore(directory)
            payload={"prompt":"animate @image.png gently","draft_text":"animate @image.png gently",
                     "model":"Seedance 2.0 Mini","ratio":"16:9","duration":4,"image_asset_ids":["asset-real"]}
            store.reserve("test-request",payload)
            store.mark_attempt("test-request")
            driver=Mock()
            workflow=VideoWorkflow(driver,store)
            workflow.reader=Mock()
            workflow.reader.query.return_value={"ok":True,"status":"completed","task_id":"real-task"}
            with patch("doubao_mcp.workflow.MediaDriver") as factory:
                result=self.submit(workflow)
            self.assertTrue(result["reused_request"] and result["no_resend"])
            driver.page.assert_not_called()
            factory.assert_not_called()
            self.assertEqual(store.get("test-request")["attempt_count"],1)

    def test_query_timeout_after_send_keeps_original_request_and_never_retries(self):
        workflow,store,driver,reader,media=self.fixture()
        reader.query.side_effect=TimeoutError("original lookup timed out")
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            result=self.submit(workflow)
        self.assertEqual(result["code"],"VIDEO_STATUS_UNAVAILABLE")
        self.assertEqual(result["request_id"],"test-request")
        self.assertTrue(result["submission_attempted"] and result["send_clicked"] and result["no_resend"])
        self.assertEqual(sum(call.args[0]=="video_send_once" for call in driver.page.call_args_list),1)

    def test_duplicate_lookup_timeout_still_reports_attempted_request(self):
        workflow,store,driver,reader,media=self.fixture(attempted=True,existing=True)
        reader.query.side_effect=TimeoutError("original lookup unavailable")
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            result=self.submit(workflow)
        self.assertEqual(result["request_id"],"test-request")
        self.assertTrue(result["submission_attempted"] and result["no_resend"])
        self.assertFalse(result["ok"])
        media.prepare_prompt.assert_not_called()
        store.mark_attempt.assert_not_called()
        self.assertFalse(any(call.args[0]=="video_send_once" for call in driver.page.call_args_list))

    def test_uncertain_send_is_only_attempted_once(self):
        workflow,store,driver,reader,media=self.fixture()
        normal=driver.page.side_effect
        def send_timeout(action,**params):
            if action=="video_send_once":
                raise TimeoutError("send acknowledgment missing")
            return normal(action,**params)
        driver.page.side_effect=send_timeout
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            first=self.submit(workflow)
            second=self.submit(workflow)
        self.assertEqual(first["code"],"VIDEO_SUBMISSION_UNCERTAIN")
        self.assertTrue(first["no_resend"] and second["no_resend"])
        self.assertEqual(media.prepare_prompt.call_count,1)
        self.assertEqual(store.mark_attempt.call_count,1)
        self.assertEqual(sum(call.args[0]=="video_send_once" for call in driver.page.call_args_list),1)

    def test_binding_and_parameter_failures_never_mark_or_send(self):
        for phase in ("binding","parameters"):
            with self.subTest(phase=phase):
                workflow,store,driver,reader,media=self.fixture()
                if phase=="binding":
                    media.prepare_prompt.side_effect=MediaError("IMAGE_BINDING_UNVERIFIED","no real mention")
                else:
                    normal=driver.page.side_effect
                    def mismatch(action,**params):
                        if action=="video_preflight":
                            raise BackgroundError("VIDEO_SETTING_MISMATCH","ratio changed")
                        return normal(action,**params)
                    driver.page.side_effect=mismatch
                with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
                    with self.assertRaises((MediaError,BackgroundError)):
                        self.submit(workflow)
                store.mark_attempt.assert_not_called()
                self.assertFalse(any(call.args[0]=="video_send_once" for call in driver.page.call_args_list))
                self.assertFalse(driver.sent)

    def test_missing_or_empty_preflight_proof_is_not_a_send_attempt(self):
        for proof in ({"ok":True,"verified":True},{"ok":True,"verified":True,"proof":None},
                      {"ok":True,"verified":True,"proof":""},{"ok":True,"verified":True,"proof":{}}):
            with self.subTest(proof=proof):
                workflow,store,driver,reader,media=self.fixture()
                normal=driver.page.side_effect
                driver.page.side_effect=lambda action,**params:proof if action=="video_preflight" else normal(action,**params)
                with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
                    with self.assertRaises(BackgroundError):
                        self.submit(workflow)
                store.mark_attempt.assert_not_called()
                self.assertFalse(any(call.args[0]=="video_send_once" for call in driver.page.call_args_list))

    def test_prepared_request_cannot_move_to_another_conversation(self):
        workflow,store,driver,reader,media=self.fixture(existing=True,record_conversation="99999999999999999")
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            with self.assertRaises(BackgroundError):
                self.submit(workflow)
        media.prepare_prompt.assert_not_called()
        store.mark_attempt.assert_not_called()

    def test_non_integer_seconds_fail_before_desktop_or_ledger(self):
        workflow,store,driver,reader,media=self.fixture()
        with self.assertRaises(BackgroundError):
            self.submit(workflow,duration=4.5)
        driver.page.assert_not_called()
        store.reserve.assert_not_called()

    def test_conversation_change_during_preparation_is_not_marked_or_sent(self):
        workflow,store,driver,reader,media=self.fixture()
        normal=driver.page.side_effect
        def changed(action,**params):
            result=normal(action,**params)
            if action=="video_preflight":
                result["conversation_id"]="99999999999999999"
                result["media"]["context"]={"route":"/chat/99999999999999999","input_instance_id":"other-input"}
            return result
        driver.page.side_effect=changed
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            with self.assertRaises(BackgroundError):
                self.submit(workflow)
        store.mark_attempt.assert_not_called()
        self.assertFalse(any(call.args[0]=="video_send_once" for call in driver.page.call_args_list))

    def test_input_instance_change_during_preparation_is_not_marked_or_sent(self):
        workflow,store,driver,reader,media=self.fixture()
        normal=driver.page.side_effect
        def changed(action,**params):
            result=normal(action,**params)
            if action=="video_preflight":
                result["media"]["context"]["input_instance_id"]="replacement-input"
            return result
        driver.page.side_effect=changed
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            with self.assertRaises(BackgroundError):
                self.submit(workflow)
        store.mark_attempt.assert_not_called()
        self.assertFalse(any(call.args[0]=="video_send_once" for call in driver.page.call_args_list))

    def test_send_receipt_from_wrong_conversation_is_uncertain_and_never_repeated(self):
        workflow,store,driver,reader,media=self.fixture()
        normal=driver.page.side_effect
        def changed(action,**params):
            result=normal(action,**params)
            if action=="video_send_once":
                result["conversation_id"]="99999999999999999"
            return result
        driver.page.side_effect=changed
        with patch("doubao_mcp.workflow.MediaDriver",return_value=media):
            first=self.submit(workflow)
            repeated=self.submit(workflow)
        self.assertEqual(first["code"],"VIDEO_SUBMISSION_UNCERTAIN")
        self.assertTrue(first["no_resend"] and repeated["no_resend"])
        self.assertEqual(store.mark_attempt.call_count,1)
        self.assertEqual(media.prepare_prompt.call_count,1)
        self.assertEqual(sum(call.args[0]=="video_send_once" for call in driver.page.call_args_list),1)

    def test_incomplete_failed_or_ambiguous_tasks_are_not_downloaded(self):
        for state in ("queued","generating","failed","quota_exceeded","confirmation_required","ambiguous"):
            with self.subTest(state=state):
                workflow,*_=self.fixture()
                workflow.status=Mock(return_value={"ok":True,"status":state,"videos":[]})
                with patch("doubao_mcp.workflow.download_video") as download:
                    result=workflow.download("V:/output",request_id="test-request")
                self.assertEqual(result["code"],"VIDEO_NOT_COMPLETED")
                self.assertFalse(result["downloaded"])
                download.assert_not_called()

    def test_unverified_resource_source_is_not_downloaded(self):
        workflow,*_=self.fixture()
        workflow.status=Mock(return_value={"ok":True,"status":"completed","videos":[
            {"url":"https://untrusted.example/fake.mp4","source":"user_supplied_url"}]})
        with patch("doubao_mcp.workflow.download_video") as download:
            with self.assertRaises(BackgroundError) as error:
                workflow.download("V:/output",request_id="test-request")
        self.assertEqual(error.exception.code,"VIDEO_RESOURCE_UNVERIFIED")
        download.assert_not_called()

    def test_completed_download_uses_task_resource_and_preserves_real_ids(self):
        workflow,store,*_=self.fixture(existing=True)
        workflow.status=Mock(return_value={"ok":True,"status":"completed","conversation_id":"real-conversation",
            "message_id":"real-message","task_id":"real-task","videos":[
                {"url":"https://media.example/signed.mp4","source":"client_card_metadata","duration":4,
                 "width":1280,"height":720,"expected_bytes":1234}]})
        with patch("doubao_mcp.workflow.download_video",return_value={"ok":True,"downloaded":True,"path":"V:/output/result.mp4"}) as download:
            result=workflow.download("V:/output",request_id="test-request",filename="result.mp4")
        self.assertEqual(download.call_args.args,("https://media.example/signed.mp4","V:/output"))
        self.assertEqual(download.call_args.kwargs["expected_metadata"],{"duration":4,"width":1280,"height":720,"expected_bytes":1234})
        self.assertEqual(result["task_id"],"real-task")
        self.assertEqual(result["message_id"],"real-message")
        self.assertEqual(result["request_id"],"test-request")

    def confirmation_fixture(self):
        workflow,store,driver,reader,_=self.fixture(attempted=True,existing=True)
        record=store.get("test-request")
        record.update(user_message_id="original-user-message",confirmation_message_id="assistant-confirmation",
                      state="confirmation_required",confirmation_attempted=False,
                      payload={"model":"Seedance 2.0 Mini","ratio":"16:9","duration":4,
                               "prompt":"animate @image.png gently","image_asset_ids":["asset-real"]})
        pending={"ok":True,"status":"confirmation_required","request_id":"test-request",
                 "message_id":"assistant-confirmation","conversation_id":"12345678901234567",
                 "message_text":"确认后开始生成视频\n模型：Seedance 2.0 Mini\n时长：4 秒\n比例：16:9"}
        active={"ok":True,"status":"generating","request_id":"test-request","task_id":"real-task-after-confirmation",
                "message_id":"assistant-video","conversation_id":"12345678901234567"}
        reader.query.side_effect=lambda **params:active if record["confirmation_attempted"] else pending
        def mark(request_id,prompt,before_message_ids):
            if record["confirmation_attempted"]:
                raise TaskError("DUPLICATE_CONFIRMATION","already confirmed")
            record.update(confirmation_attempted=True,confirmation_prompt=prompt,
                          confirmation_before_message_ids=before_message_ids)
            return record
        store.mark_confirmation_attempt.side_effect=mark
        normal=driver.page.side_effect
        def page(action,**params):
            if action=="focus_editor":
                return {"ok":True}
            if action=="video_confirmation_preflight":
                return {"ok":True,"verified":True,"proof":{"confirmation_message_id":"assistant-confirmation"},
                        "conversation_id":"12345678901234567"}
            if action=="video_confirm_once":
                return {"ok":True,"clicked":True,"conversation_id":"12345678901234567"}
            return normal(action,**params)
        driver.page.side_effect=page
        return workflow,store,driver,reader,record,pending

    def test_confirmation_parameter_mismatch_never_inserts_or_sends(self):
        for old,new in (("Seedance 2.0 Mini","Seedance 2.0"),("4 秒","5 秒"),("16:9","9:16")):
            with self.subTest(parameter=old):
                workflow,store,driver,reader,record,pending=self.confirmation_fixture()
                pending["message_text"]=pending["message_text"].replace(old,new)
                with self.assertRaises(BackgroundError) as error:
                    workflow.confirm("test-request")
                self.assertEqual(error.exception.code,"CONFIRMATION_PARAMETER_MISMATCH")
                store.mark_confirmation_attempt.assert_not_called()
                driver.page.assert_not_called()
                driver.client.call.assert_not_called()

    def test_confirmation_without_original_user_receipt_is_not_sent(self):
        workflow,store,driver,reader,record,pending=self.confirmation_fixture()
        record["user_message_id"]=None
        with self.assertRaises(BackgroundError) as error:
            workflow.confirm("test-request")
        self.assertEqual(error.exception.code,"CONFIRMATION_UNTRACKED")
        store.mark_confirmation_attempt.assert_not_called()
        driver.page.assert_not_called()
        driver.client.call.assert_not_called()

    def test_confirmation_is_attempted_at_most_once_for_the_request(self):
        workflow,store,driver,reader,record,pending=self.confirmation_fixture()
        first=workflow.confirm("test-request")
        repeated=workflow.confirm("test-request")
        self.assertEqual(first["request_id"],"test-request")
        self.assertTrue(first["confirmation_attempted"] and first["no_resend"])
        self.assertTrue(repeated["reused_confirmation"] and repeated["no_resend"])
        self.assertEqual(store.mark_confirmation_attempt.call_count,1)
        self.assertEqual(sum(call.args[0]=="video_confirm_once" for call in driver.page.call_args_list),1)
        self.assertEqual(driver.client.call.call_count,1)

    def test_confirmation_query_timeout_keeps_original_request_and_no_resend(self):
        workflow,store,driver,reader,record,pending=self.confirmation_fixture()
        def lookup(**params):
            if record["confirmation_attempted"]:
                raise TimeoutError("confirmation receipt query timed out")
            return pending
        reader.query.side_effect=lookup
        first=workflow.confirm("test-request")
        repeated=workflow.confirm("test-request")
        self.assertFalse(first["ok"])
        self.assertEqual(first["request_id"],"test-request")
        self.assertTrue(first["confirmation_attempted"] and first["no_resend"])
        self.assertTrue(repeated["no_resend"])
        self.assertEqual(repeated["request_id"],"test-request")
        self.assertEqual(store.mark_confirmation_attempt.call_count,1)
        self.assertEqual(sum(call.args[0]=="video_confirm_once" for call in driver.page.call_args_list),1)

    def test_confirmation_receipt_from_wrong_conversation_is_uncertain_and_not_repeated(self):
        workflow,store,driver,reader,record,pending=self.confirmation_fixture()
        normal=driver.page.side_effect
        def wrong_receipt(action,**params):
            result=normal(action,**params)
            if action=="video_confirm_once":
                result["conversation_id"]="other-conversation"
            return result
        driver.page.side_effect=wrong_receipt
        first=workflow.confirm("test-request")
        repeated=workflow.confirm("test-request")
        self.assertEqual(first["code"],"CONFIRMATION_UNCERTAIN")
        self.assertTrue(first["no_resend"] and repeated["no_resend"])
        self.assertEqual(store.mark_confirmation_attempt.call_count,1)
        self.assertEqual(sum(call.args[0]=="video_confirm_once" for call in driver.page.call_args_list),1)

    def test_confirmation_invalid_preflight_proof_does_not_mark_attempt(self):
        for proof in (None,{},"unstructured-proof",[]):
            with self.subTest(proof=proof):
                workflow,store,driver,reader,record,pending=self.confirmation_fixture()
                normal=driver.page.side_effect
                def invalid(action,**params):
                    result=normal(action,**params)
                    if action=="video_confirmation_preflight":
                        result["proof"]=proof
                    return result
                driver.page.side_effect=invalid
                with self.assertRaises(BackgroundError):
                    workflow.confirm("test-request")
                store.mark_confirmation_attempt.assert_not_called()
                self.assertFalse(any(call.args[0]=="video_confirm_once" for call in driver.page.call_args_list))

    def test_confirmation_is_not_sent_for_unsubmitted_request(self):
        workflow,store,driver,reader,record,pending=self.confirmation_fixture()
        record["submission_attempted"]=False
        with self.assertRaises(BackgroundError) as error:
            workflow.confirm("test-request")
        self.assertEqual(error.exception.code,"NOT_AWAITING_CONFIRMATION")
        store.mark_confirmation_attempt.assert_not_called()
        driver.client.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
