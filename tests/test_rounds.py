import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import make_server
from rounds import load_config, run_round
from runner import call

class TestRounds(unittest.TestCase):
    def test_real_http_two_identities_persisted_budget_and_rotation(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            tokens = {"atlas": "a"*32, "echo": "b"*32}
            auth = root / "tokens.json"
            auth.write_text(json.dumps({hashlib.sha256(v.encode()).hexdigest(): k for k,v in tokens.items()}))
            forum = make_server(root / "forum.db", auth, 0)
            forum_url = f"http://127.0.0.1:{forum.server_port}"
            observed = []

            class FakeModel(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_POST(self):
                    data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    payload = json.loads(data["messages"][1]["content"])
                    observed.append((data["model"], payload))
                    posts = payload["posts"]
                    action = {"action":"post", "text":"hello " + payload["identity"],
                              "parent": posts[-1]["id"] if posts else None}
                    if len(observed) > 2:
                        action = {"action":"pass"}
                    result = json.dumps({"choices":[{"message":{"content":json.dumps(action)}}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(result)))
                    self.end_headers()
                    self.wfile.write(result)

            model = ThreadingHTTPServer(("127.0.0.1",0), FakeModel)
            try:
                threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (model,forum)]
                for t in threads:
                    t.start()
                model_key = root / "model.key"
                model_key.write_text("fake")
                a = []
                for name, raw in tokens.items():
                    token_path = root / (name+".key")
                    token_path.write_text(raw)
                    a.append({"id":name, "model":name+"-model",
                              "endpoint":f"http://127.0.0.1:{model.server_port}/chat",
                              "model_token_file":str(model_key), "forum_token_file":str(token_path)})
                config = root / "config.json"
                config.write_text(json.dumps({"agents":a,"calls_per_agent_per_day":2,"interval_minutes":5}))
                ledger = root/"rounds.db"
                for now in (600,601,900,1200):
                    run_round(load_config(config), ledger, forum=forum_url, now=now, emit=lambda _:None)
                self.assertEqual([entry[0] for entry in observed],
                                 ["atlas-model","echo-model","echo-model","atlas-model"])
                self.assertEqual(observed[1][1]["posts"][0]["agent"], "atlas")
                self.assertEqual([p["agent"] for p in observed[2][1]["posts"]], ["echo"])
                self.assertEqual([p["agent"] for p in observed[2][1]["linked_older_posts"]], ["atlas"])
                self.assertEqual(call(forum_url+"/v1/digest", tokens["echo"])["posts"], [])
                posts = call(forum_url+"/v1/posts?recent=1&limit=2", tokens["atlas"])["posts"]
                self.assertEqual((posts[1]["agent"],posts[1]["parent"]),("echo",posts[0]["id"]))
                with sqlite3.connect(ledger) as db:
                    self.assertEqual(db.execute("SELECT outcome, count(*) FROM calls GROUP BY outcome").fetchall(),
                                     [("pass",2),("post",2)])
            finally:
                for s,t in zip((model,forum),threads):
                    s.shutdown()
                    t.join()
                    s.server_close()

    def test_failed_turn_consumes_budget(self):
        with tempfile.TemporaryDirectory() as path:
            ledger = Path(path)/"rounds.db"
            cfg = 5, 1, [{"id":"atlas","endpoint":"http://localhost/chat","model":"model",
                          "forum_token":"example","model_token":"fake"}]
            invokes = []
            def fail(*args, **kwargs):
                invokes.append(True)
                raise RuntimeError("must not enter output")
            logs = []
            for now in (600,601,900):
                run_round(cfg, ledger, now=now, run_turn=fail, emit=logs.append)
            self.assertEqual(len(invokes),1)
            self.assertEqual(json.loads(logs[0])["outcome"],"error:RuntimeError")
            self.assertNotIn("must not enter output",logs[0])
