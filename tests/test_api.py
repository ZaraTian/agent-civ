import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from app import make_server

class TestForum(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = Path(self.tmp.name, "tokens.json")
        self.tokens = {hashlib.sha256(k.encode()).hexdigest(): k for k in ("atlas", "echo")}
        self.auth.write_text(json.dumps(self.tokens))
        self.server = make_server(Path(self.tmp.name, "civ.db"), self.auth, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.worker.join)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def api(self, path, token="atlas", method="GET", data=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(self.base + "/v1/" + path, headers=headers, method=method,
                      data=None if data is None else json.dumps(data, ensure_ascii=False).encode())
        try:
            with urlopen(req, timeout=3) as res:
                return res.status, json.load(res)
        except HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_default_closed_and_revocation(self):
        for path in ("manifest", "posts", "memory", "digest", "posts/1"):
            self.assertEqual(self.api(path, token=None)[0], 401)
        self.assertEqual(self.api("identity")[1]["agent"], "atlas")
        self.tokens.pop(hashlib.sha256(b"atlas").hexdigest())
        self.auth.write_text(json.dumps(self.tokens))
        self.assertEqual(self.api("identity")[0], 401)
        self.assertEqual(self.api("identity", token="echo")[0], 200)

    def test_cross_agent_reply_and_provenance(self):
        code, post = self.api("posts", method="POST", data={"text": "One small thought."})
        self.assertEqual(code, 201)
        seen = self.api("posts", token="echo")[1]["posts"]
        self.assertEqual(seen[0]["agent"], "atlas")
        self.assertEqual(self.api("posts", token="echo", method="POST",
                                  data={"text": "A reply.", "parent": post["id"]})[0], 201)
        reply = self.api(f"posts?after={post['id']}&limit=1")[1]["posts"][0]
        self.assertEqual((reply["agent"], reply["parent"]), ("echo", post["id"]))

    def test_private_memory(self):
        self.assertEqual(self.api("memory", method="PUT", data={"text": "private"})[0], 200)
        self.assertEqual(self.api("memory")[1]["text"], "private")
        self.assertEqual(self.api("memory", token="echo")[1]["text"], "")
        self.assertEqual(self.api("posts")[1]["posts"], [])

    def test_reject_bad_parent_and_impersonation(self):
        for data in ({"text": "x", "agent": "echo"}, {"text": "x", "parent": True},
                     {"text": "x"*4001}, {"text": " "}):
            self.assertEqual(self.api("posts", method="POST", data=data)[0], 400)
        self.assertEqual(self.api("posts", method="POST", data={"text": "x", "parent": 123})[0], 404)
        self.assertEqual(self.api("posts?limit=9000")[0], 400)
        self.assertEqual(self.api("posts")[1]["posts"], [])

    def test_digest_is_per_agent_monotone_and_does_not_skip_new_posts(self):
        def post(body):
            return self.api("posts", "echo", method="POST", data={"text": body})[1]["id"]
        anchor = post("starting point")
        self.assertEqual(self.api("digest")[1]["after"], None)
        self.assertEqual(self.api("digest")[1]["after"], None)
        self.assertEqual(self.api("digest", method="PUT", data={"through": anchor})[0], 200)
        ids = [post(f"update {i}") for i in range(10)]
        first = self.api("digest")[1]
        self.assertEqual([row["id"] for row in first["posts"]], ids[:8])
        self.assertTrue(first["has_more"])
        self.assertEqual(self.api("digest", "echo")[1]["after"], None)
        self.assertEqual(self.api("digest", method="PUT", data={"through": first["next_after"]})[0], 200)
        self.assertEqual(self.api("digest", method="PUT", data={"through": anchor})[0], 200)
        second = self.api("digest")[1]
        self.assertEqual([row["id"] for row in second["posts"]], ids[8:])
        self.assertFalse(second["has_more"])
        self.assertEqual(self.api("digest", method="PUT", data={"through": 9000})[0], 404)
        self.assertEqual(self.api("digest", method="PUT", data={"through": True})[0], 400)

    def test_old_lineage_surfaces_with_bounded_body_and_no_duplicate_ids(self):
        def post(body, parent=None):
            return self.api("posts", "echo", method="POST", data={"text": body, "parent": parent})[1]["id"]
        root = post("原" * 4000)
        older = post("earlier branch", root)
        for _ in range(9):
            post("unrelated")
        baseline = self.api("digest")[1]["next_after"]
        self.api("digest", method="PUT", data={"through": baseline})
        new = post("continuation", older)
        self_reply = post("another reply", older)
        digest = self.api("digest")[1]
        self.assertEqual([p["id"] for p in digest["posts"]], [new, self_reply])
        self.assertEqual([p["id"] for p in digest["linked"]], [older, root])
        self.assertTrue(digest["linked"][-1]["truncated"])
        self.assertEqual(len(digest["linked"][-1]["body"]), 700)
        self.assertEqual(len(self.api(f"posts/{root}")[1]["post"]["body"]), 4000)
        self.assertEqual(self.api("posts/999")[0], 404)
