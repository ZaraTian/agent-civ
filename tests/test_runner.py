import json
import unittest
from runner import NoRedirect, choose, turn

class TestRunner(unittest.TestCase):
    def test_redirects_are_not_followed(self):
        self.assertIsNone(NoRedirect().redirect_request(
            None, None, 302, "", {}, "https://example.com/redirect"))

    def test_only_permitted_actions(self):
        self.assertIsNone(choose('{"action":"pass"}'))
        for response in ('{"action":"exec","text":"x"}', '{"action":"post","text":"x","url":"https://example.org"}',
                         '{"action":"post","text":"x","parent":true}', '{"action":"remember","text":42}'):
            with self.assertRaises(ValueError):
                choose(response)

    def test_one_model_request_and_bounded_reply(self):
        turns = []
        def fake(url, token, method="GET", body=None):
            turns.append((url, token, method, body))
            if url.endswith("/identity"):
                return {"agent": "echo"}
            if "posts?" in url:
                return {"posts": [{"id": 7, "body": "A riddle"}]}
            if url.endswith("/memory"):
                return {"text": ""}
            if url == "https://example.org/model":
                self.assertEqual(token, "model-token")
                self.assertNotIn("forum-token", json.dumps(body))
                return {"choices": [{"message": {"content": '{"action":"post","text":"A reply","parent":7}'}}]}
            self.assertTrue(url.endswith("/posts"))
            self.assertEqual((token, method, body), ("forum-token", "POST", {"text": "A reply", "parent": 7}))
            return {"id": 8}
        self.assertEqual(turn("http://127.0.0.1:8700", "forum-token", "https://example.org/model",
                              "model-token", "model", request=fake), {"id": 8})
        self.assertEqual(len(turns), 5)
