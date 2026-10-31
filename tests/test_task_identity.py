"""Conflicting message and task identities must never select another video's row."""
import unittest
from unittest.mock import Mock

from doubao_mcp.tasks import TaskError, VideoTaskReader


class TaskIdentityTests(unittest.TestCase):
    def reader(self, rows):
        driver=Mock()
        driver.status.return_value={"conversation_id":"123"}
        driver.page.return_value={"conversation_id":"123","records":rows,"scan_complete":True}
        store=Mock()
        return VideoTaskReader(driver,store),store

    def row(self, message_id="message-a"):
        return {"message_id":message_id,"role":"assistant","text":"已完成",
                "card":{"task_id":"task-a","is_success":True},
                "task_ids":[{"id":"task-a","source":"test.card"}],"videos":[]}

    def test_task_from_other_message_is_rejected_without_rebinding(self):
        reader,store=self.reader([self.row()])
        with self.assertRaises(TaskError) as caught:
            reader.query(conversation_id="123",message_id="message-b",task_id="task-a")
        self.assertEqual(caught.exception.code,"TASK_BINDING_CONFLICT")
        store.update.assert_not_called()

    def test_matching_message_and_task_keep_the_exact_identity(self):
        reader,_=self.reader([self.row()])
        result=reader.query(conversation_id="123",message_id="message-a",task_id="task-a")
        self.assertEqual((result["conversation_id"],result["message_id"],result["task_id"]),("123","message-a","task-a"))
        self.assertEqual(result["status"],"completed")

    def test_duplicate_card_rows_for_one_task_are_ambiguous(self):
        reader,store=self.reader([self.row(),self.row()])
        with self.assertRaises(TaskError) as caught:
            reader.query(conversation_id="123",task_id="task-a")
        self.assertEqual(caught.exception.code,"TARGET_AMBIGUOUS")
        store.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
