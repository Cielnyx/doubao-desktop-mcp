import unittest

from doubao_mcp.prompts import (
    MAX_VIDEO_PROMPT_CHARS, VIDEO_PROMPT_RULES, PromptError,
    build_video_prompt, extract_user_prompt,
)


class PromptTests(unittest.TestCase):
    def test_prefix_uses_model_and_preserves_original_text_and_references(self):
        original = "  以@参考.png为首帧。\r\n蓝色圆形向右移动。\n\n不加文字。  \n"
        complete = build_video_prompt("Seedance 2.0 Mini", original)
        self.assertTrue(complete.startswith("帮我用Seedance 2.0 Mini生成视频。\n"))
        self.assertIn(VIDEO_PROMPT_RULES, complete)
        self.assertTrue(complete.endswith("用户提示词：\n" + original))
        self.assertEqual(extract_user_prompt("Seedance 2.0 Mini", complete), original)

    def test_repeated_wrapping_is_idempotent(self):
        once = build_video_prompt("Seedance 2.0", "以@reference.png为参考\n保持原色")
        self.assertEqual(build_video_prompt("Seedance 2.0", once), once)
        self.assertEqual(once.count("帮我用Seedance 2.0生成视频。"), 1)

    def test_complete_wrapper_cannot_switch_to_a_different_model(self):
        wrapped = build_video_prompt("Seedance 2.0 Mini", "蓝色圆形移动")
        for action in (build_video_prompt, extract_user_prompt):
            with self.subTest(action=action.__name__), self.assertRaises(PromptError) as error:
                action("Seedance 2.5", wrapped)
            self.assertEqual(error.exception.code, "VIDEO_PROMPT_MODEL_MISMATCH")

    def test_partial_or_quoted_prefix_is_not_mistaken_for_system_wrapper(self):
        raw = "帮我用Seedance 2.0生成视频。\n请保留这句台词。"
        self.assertEqual(extract_user_prompt("Seedance 2.0", raw), raw)
        self.assertTrue(build_video_prompt("Seedance 2.0", raw).endswith("用户提示词：\n" + raw))

    def test_model_and_complete_prompt_bounds_are_validated(self):
        for model in (None, "", " Seedance 2.0", "Seedance\n2.0", "X"*101):
            with self.subTest(model=model), self.assertRaises(PromptError):
                build_video_prompt(model, "test")
        for original in (None, "", " \n", "bad\0text", "x"*MAX_VIDEO_PROMPT_CHARS):
            with self.subTest(original=repr(original)[:30]), self.assertRaises(PromptError):
                build_video_prompt("Seedance 2.0", original)

    def test_maximum_length_applies_to_wrapped_result(self):
        overhead = len(build_video_prompt("Seedance 2.0", "x"))-1
        body = "x"*(MAX_VIDEO_PROMPT_CHARS-overhead)
        wrapped = build_video_prompt("Seedance 2.0", body)
        self.assertEqual(len(wrapped), MAX_VIDEO_PROMPT_CHARS)
        self.assertEqual(build_video_prompt("Seedance 2.0", wrapped), wrapped)


if __name__ == "__main__":
    unittest.main()
