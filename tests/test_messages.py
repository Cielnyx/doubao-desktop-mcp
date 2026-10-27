import unittest
from doubao_mcp.messages import parse_messages


def text(value):
    return {"type": "Text", "name": value}


def button(value=""):
    return {"type": "Button", "name": value}


class MessageTests(unittest.TestCase):
    def test_does_not_return_prompt_or_suggestions_as_reply(self):
        entries = [text("测试问题"), text(""), button(), button(),
                   text("测试回答"), button(), button("朗读"), button(),
                   {"type": "Group", "name": "推荐问题"}]
        messages = parse_messages(entries)
        self.assertEqual([(m.role, m.text) for m in messages],
                         [("user", "测试问题"), ("assistant", "测试回答")])

    def test_multiline_and_identical_answers_keep_distinct_turns(self):
        entries = [text("问题一"), button(), text("第一行"), text("第二行"), button("朗读"),
                   text("问题二"), button(), text("第一行"), text("第二行"), button("朗读")]
        messages = parse_messages(entries)
        self.assertEqual([m.text for m in messages if m.role == "assistant"],
                         ["第一行\n第二行", "第一行\n第二行"])

    def test_unmarked_text_is_not_accepted_as_completed_answer(self):
        messages = parse_messages([text("提问"), button(), text("仍在生成的文本")])
        self.assertFalse(any(m.role == "assistant" for m in messages))

if __name__ == "__main__":
    unittest.main()
