import hashlib
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

from doubao_mcp.media import MediaDriver, MediaError, validate_image
from doubao_mcp.prompts import build_video_prompt


class MediaTests(unittest.TestCase):
    def test_image_validation_requires_real_absolute_image_file(self):
        runtime = Path(__file__).resolve().parents[1]/".runtime"
        runtime.mkdir(exist_ok=True)
        marker = uuid.uuid4().hex
        image = runtime/(marker+"参考.png")
        fake = runtime/(marker+"fake.png")
        try:
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test image fixture")
            info = validate_image(str(image))
            self.assertEqual(info["sha256"], hashlib.sha256(image.read_bytes()).hexdigest())
            self.assertEqual(info["md5"], hashlib.md5(image.read_bytes(),usedforsecurity=False).hexdigest())
            fake.write_text("not an image")
            for path in ("relative.png",str(fake),str(runtime/(marker+"missing.png")),str(runtime)):
                with self.subTest(path=path), self.assertRaises(MediaError):
                    validate_image(path)
        finally:
            image.unlink(missing_ok=True)
            fake.unlink(missing_ok=True)

    def test_invalid_uploads_never_connect_or_modify_the_desktop(self):
        driver = Mock()
        media = MediaDriver(driver)
        for paths in ([],["relative.png"],["missing"]*11,"image.png"):
            with self.subTest(paths=paths), self.assertRaises(MediaError):
                media.upload(paths)
        driver.connect.assert_not_called()
        driver.client.call.assert_not_called()

    def test_completed_upload_is_reused_without_reuploading(self):
        media = MediaDriver(Mock())
        file = {"path":"V:/reference.png","name":"reference.png","size":10,"md5":"digest","sha256":"sha"}
        asset = {"asset_id":"resource","resource_id":"resource","name":"reference.png","size":10,
                 "md5":"digest","uploaded":True}
        media._call = Mock(return_value={"assets":[asset],"context":{"route":"/chat/1"}})
        with patch("doubao_mcp.media.validate_image", return_value=file):
            result = media.upload([file["path"]])
        self.assertTrue(result["assets"][0]["already_uploaded"])
        media.driver.client.call.assert_not_called()

    def upload_fixture(self, returned_md5="digest"):
        media = MediaDriver(Mock())
        file = {"path":"V:/reference.png","name":"reference.png","size":10,"md5":"digest","sha256":"sha"}
        context = {"route":"/chat/1","input_instance_id":"test"}
        target = {"assets":[],"context":context,"accept":".png","selector":"input[type=file]"}
        asset = {"asset_id":"resource","resource_id":"resource","local_key":"new",
                 "name":"reference.png","size":10,"md5":returned_md5,"uploaded":True,"status":"Normal"}
        media._call = Mock(return_value=target)
        media.inspect = Mock(return_value={"assets":[asset],"context":context})
        media.driver.client.call.side_effect = [{"root":{"nodeId":1}},{"nodeId":2},{}]
        return media,file

    def test_new_upload_uses_file_input_and_verifies_real_resource_digest(self):
        media,file = self.upload_fixture()
        with patch("doubao_mcp.media.validate_image", return_value=file):
            result = media.upload([file["path"]])
        self.assertTrue(result["upload_verified"])
        self.assertEqual(result["assets"][0]["resource_id"],"resource")
        self.assertEqual(result["assets"][0]["sha256"],"sha")
        self.assertEqual(media.driver.client.call.call_args.args,
                         ("DOM.setFileInputFiles",{"nodeId":2,"files":[file["path"]]}))

    def test_matching_filename_with_wrong_digest_never_claims_upload_success(self):
        media,file = self.upload_fixture(returned_md5="different file")
        with patch("doubao_mcp.media.validate_image", return_value=file), \
             patch("doubao_mcp.media.time.monotonic", side_effect=[0,0,10]), \
             patch("doubao_mcp.media.time.sleep"):
            with self.assertRaises(MediaError) as error:
                media.upload([file["path"]],timeout=5)
        self.assertEqual(error.exception.code,"IMAGE_UPLOAD_TIMEOUT")
        self.assertTrue(error.exception.details["upload_started"])
        self.assertEqual(sum(call.args[0]=="DOM.setFileInputFiles" for call in media.driver.client.call.call_args_list),1)

    def state(self, names=("reference.png",)):
        return {"assets":[{"asset_id":str(i),"name":name,"uploaded":True} for i,name in enumerate(names)],
                "bindings":[],"draft_text":"","editor_json":{"type":"doc","content":[{"type":"paragraph"}]},
                "unresolved_mentions":[]}

    def test_bind_cannot_rename_a_real_candidate(self):
        media = MediaDriver(Mock())
        media.inspect = lambda:self.state()
        with self.assertRaises(MediaError) as error:
            media.bind("0",label="图片1")
        self.assertEqual(error.exception.code,"IMAGE_LABEL_MISMATCH")
        media.driver.client.call.assert_not_called()

    def test_missing_or_duplicate_reference_is_rejected_before_editor_changes(self):
        media = MediaDriver(Mock())
        media.inspect = lambda:self.state()
        media.bind = Mock()
        for prompt in ("plain filename reference.png", "@reference.pngx", "@unknown.png"):
            with self.subTest(prompt=prompt), self.assertRaises(MediaError):
                media.prepare_prompt(prompt,["0"])
        media.bind.assert_not_called()
        media.inspect = lambda:self.state(("same.png","same.png"))
        with self.assertRaises(MediaError) as error:
            media.prepare_prompt("@same.png moves",["0","1"])
        self.assertEqual(error.exception.code,"AMBIGUOUS_IMAGE_LABEL")

    def test_preparation_preserves_unrelated_user_draft(self):
        media = MediaDriver(Mock())
        state = self.state()
        state["draft_text"] = "user writing"
        state["editor_json"]["content"][0]["content"] = [{"type":"text","text":"user writing"}]
        media.inspect = lambda:state
        media.bind = Mock()
        with self.assertRaises(MediaError) as error:
            media.prepare_prompt("Animate @reference.png",["0"])
        self.assertEqual(error.exception.code,"DRAFT_PRESENT")
        media.bind.assert_not_called()

    def text_state(self, text):
        return {"assets":[],"bindings":[],"draft_text":text,"unresolved_mentions":[],
                "editor_json":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":text}]}]}}

    def test_controlled_prefix_can_replace_exact_original_draft_only(self):
        original = "原文一字不改\n保留第二行。"
        wrapped = build_video_prompt("Seedance 2.0",original)
        media = MediaDriver(Mock())
        media.inspect = Mock(return_value=self.text_state(original))
        media._call = Mock(return_value={"ok":True,"verified":True})
        result = media.prepare_prompt(wrapped,[],source_prompt=original)
        self.assertTrue(result["verified"])
        media._call.assert_called_once_with("prepare",prompt=wrapped,asset_ids=[],source_prompt=original)

        media._call.reset_mock()
        media.inspect.return_value = self.text_state(original+" ")
        with self.assertRaises(MediaError) as error:
            media.prepare_prompt(wrapped,[],source_prompt=original)
        self.assertEqual(error.exception.code,"DRAFT_PRESENT")
        media._call.assert_not_called()

    def test_source_prompt_cannot_authorize_arbitrary_body_replacement(self):
        original = "用户自己的草稿"
        media = MediaDriver(Mock())
        media.inspect = Mock(return_value=self.text_state(original))
        media._call = Mock()
        wrapped_other = build_video_prompt("Seedance 2.0","different body")
        for target in ("unrelated replacement",wrapped_other):
            with self.subTest(target=target[:30]),self.assertRaises(MediaError) as error:
                media.prepare_prompt(target,[],source_prompt=original)
            self.assertEqual(error.exception.code,"INVALID_SOURCE_PROMPT")
        media.inspect.assert_not_called()
        media._call.assert_not_called()

    def test_plain_draft_is_not_replaceable_without_controlled_source(self):
        original = "这段原文只允许受控添加前缀"
        media = MediaDriver(Mock())
        media.inspect = Mock(return_value=self.text_state(original))
        media._call = Mock()
        with self.assertRaises(MediaError) as error:
            media.prepare_prompt(build_video_prompt("Seedance 2.0",original),[])
        self.assertEqual(error.exception.code,"DRAFT_PRESENT")
        media._call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
