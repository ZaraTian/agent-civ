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
                      data=None if data is None else json.dumps(data).encode())
        try:
            with urlopen(req, timeout=3) as res:
                return res.status, json.load(res)
        except HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_default_closed_and_revocation(self):
        for path in ("manifest", "posts", "memory"):
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
