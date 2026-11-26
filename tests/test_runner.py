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
            if url.endswith("/digest"):
                if method == "PUT":
                    self.assertEqual(body, {"through": 7})
                    return {"acknowledged": True}
                return {"posts": [{"id": 7, "body": "A riddle"}],
                        "linked": [{"id": 1, "body": "Original question"}],
                        "next_after": 7, "has_more": False}
            if url.endswith("/memory"):
                return {"text": ""}
            if url == "https://example.org/model":
                self.assertEqual(token, "model-token")
                self.assertNotIn("forum-token", json.dumps(body))
                context = json.loads(body["messages"][1]["content"])
                self.assertEqual(context["linked_older_posts"][0]["id"], 1)
                return {"choices": [{"message": {"content": '{"action":"post","text":"A reply","parent":7}'}}]}
            self.assertTrue(url.endswith("/posts"))
            self.assertEqual((token, method, body), ("forum-token", "POST", {"text": "A reply", "parent": 7}))
            return {"id": 8}
        self.assertEqual(turn("https://forum.example.org:8443", "forum-token", "https://example.org/model",
                              "model-token", "model", request=fake), {"id": 8})
        self.assertEqual(len(turns), 6)

    def test_failed_completion_leaves_digest_unacknowledged(self):
        requests = []
        def fake(url, token, method="GET", body=None):
            requests.append((url, method))
            if url.endswith("/identity"):
                return {"agent": "echo"}
            if url.endswith("/digest"):
                return {"posts": [], "linked": [], "next_after": 0, "has_more": False}
            if url.endswith("/memory"):
                return {"text": ""}
            return {"choices": [{"message": {"content": '{"action":"exec"}'}}]}
        with self.assertRaises(ValueError):
            turn("http://127.0.0.1:8700", "forum-token", "https://example.org/model",
                 "model-token", "model", request=fake)
        self.assertFalse(any(method == "PUT" for _, method in requests))

    def test_refuse_cleartext_remote_and_embedded_credentials(self):
        def no_request(*args, **kwargs):
            self.fail("Invalid configuration must be rejected before network I/O")
        for forum, model in (("http://forum.example.org", "https://model.example.org/chat"),
                             ("https://operator@forum.example.org", "https://model.example.org/chat"),
                             ("https://forum.example.org", "https://operator@model.example.org/chat")):
            with self.assertRaises(ValueError):
                turn(forum, "forum-token", model, "model-token", "model", request=no_request)
