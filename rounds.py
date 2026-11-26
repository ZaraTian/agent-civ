"""One scheduled cohort round. Reserve each model call before dispatch."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from app import USER
from runner import turn

def load_config(path):
    cfg = json.loads(Path(path).read_text())
    agents = cfg["agents"]
    if not isinstance(agents, list) or not 1 <= len(agents) <= 6:
        raise ValueError("Configure one to six agents")
    cadence = cfg.get("interval_minutes", 360)
    budget = cfg.get("calls_per_agent_per_day", 4)
    if type(cadence) is not int or not 5 <= cadence <= 1440:
        raise ValueError("interval_minutes must be 5..1440")
    if type(budget) is not int or not 1 <= budget <= 24:
        raise ValueError("calls_per_agent_per_day must be 1..24")
    result = []
    names = set()
    tokens = set()
    for a in agents:
        name = a["id"]
        if not isinstance(name, str) or not USER.fullmatch(name) or name in names:
            raise ValueError("Invalid or duplicate agent id")
        endpoint = urlsplit(a["endpoint"])
        if (endpoint.username is not None or endpoint.query or endpoint.fragment or
                not endpoint.hostname or not (endpoint.scheme == "https" or
                (endpoint.scheme == "http" and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}))):
            raise ValueError("Model endpoint must be HTTPS or local HTTP without embedded credentials")
        agent_token = Path(a["forum_token_file"]).read_text().strip()
        model_token = Path(a["model_token_file"]).read_text().strip()
        if len(agent_token) < 32 or agent_token in tokens or not model_token or not a["model"]:
            raise ValueError("Missing model settings, duplicate or short forum token")
        names.add(name)
        tokens.add(agent_token)
        result.append({**a, "forum_token": agent_token, "model_token": model_token})
    return cadence, budget, result

def run_round(config, ledger, *, forum="http://127.0.0.1:8700", now=None,
              run_turn=turn, emit=print):
    cadence, budget, agents = config
    now = int(time.time() if now is None else now)
    slot = now // (60 * cadence)
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    shift = slot % len(agents)
    with closing(sqlite3.connect(ledger, isolation_level=None)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS calls(
            agent TEXT NOT NULL, cadence INTEGER NOT NULL, slot INTEGER NOT NULL,
            day TEXT NOT NULL, outcome TEXT NOT NULL, PRIMARY KEY(agent,cadence,slot))""")
        for a in agents[shift:] + agents[:shift]:
            name = a["id"]
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM calls WHERE day=? AND agent=?", (day, name)).fetchone()[0]
            reserved = count < budget and db.execute(
                "INSERT OR IGNORE INTO calls VALUES (?,?,?,?,'reserved')",
                (name, cadence, slot, day)).rowcount == 1
            db.commit()
            if not reserved:
                continue
            try:
                outcome = run_turn(forum, a["forum_token"], a["endpoint"],
                                   a["model_token"], a["model"], expected_identity=name)
                state = "pass" if outcome.get("action") == "pass" else (
                    "post" if "id" in outcome else "remember")
            except Exception as exc:
                # Do not log prompts, API error payloads, token values or endpoints.
                state = "error:" + type(exc).__name__
            db.execute("UPDATE calls SET outcome=? WHERE agent=? AND cadence=? AND slot=?",
                       (state, name, cadence, slot))
            emit(json.dumps({"agent": name, "slot": slot, "outcome": state}))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--forum", default="http://127.0.0.1:8700")
    args = ap.parse_args()
    run_round(load_config(args.config), args.ledger, forum=args.forum)
