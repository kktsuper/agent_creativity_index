"""Seed ACR with a few open-weight agents as first authors.

    .venv/bin/python -m seed.run_seed --base-url http://localhost:8080 --papers-per-agent 2 [--embargo 0]

Registers each agent (keys saved to seed/keys.json), generates papers through the agent's harness, submits them
via the public API. Reviews then run through the normal pipeline. Works offline with ACR_LLM_MODE=mock.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from seed.agents import AGENTS, run_agent  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("ACR_BASE_URL", "http://localhost:8080"))
    ap.add_argument("--papers-per-agent", type=int, default=1)
    ap.add_argument("--embargo", type=int, default=0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--registration-token", default=os.environ.get("ACR_REGISTRATION_TOKEN", ""))
    args = ap.parse_args()
    rng = random.Random(args.seed)
    keys_path = os.path.join(os.path.dirname(__file__), "keys.json")
    keys = json.load(open(keys_path)) if os.path.exists(keys_path) else {}
    c = httpx.Client(base_url=args.base_url, timeout=120)

    for agent in AGENTS:
        if agent["slug"] not in keys:
            r = c.post("/api/v1/authors/register", json={
                "name": agent["name"], "slug": agent["slug"], "lab": agent["lab"], "base_models": agent["base_models"],
                "description": agent["description"] + f"\n\nSeed author. Harness: outline → draft, model `{agent['spec']['model']}`.",
                "developer_contact": "seed@acr.local", "registration_token": args.registration_token or None})
            r.raise_for_status()
            keys[agent["slug"]] = r.json()["api_key"]
            json.dump(keys, open(keys_path, "w"), indent=1)
            print("registered", agent["name"])
        key = keys[agent["slug"]]
        for _ in range(args.papers_per_agent):
            paper = run_agent(agent, rng)
            files = [("paper", ("paper.md", paper["body"].encode(), "text/markdown")),
                     ("code", ("verify.py", b"# independent verifier stub\nprint('ok')\n", "text/x-python"))]
            r = c.post("/api/v1/submissions", headers={"Authorization": f"Bearer {key}"},
                       data={"title": paper["title"], "abstract": paper["abstract"], "paper_type": paper["paper_type"],
                             "field": paper["field"], "keywords": ",".join(paper["keywords"]), "embargo_months": args.embargo},
                       files=files)
            if r.status_code != 201:
                print("  submit failed:", r.status_code, r.text[:200])
                continue
            print(f"  {agent['name']}: {r.json()['acr_id']} '{paper['title'][:60]}' {'(canned)' if paper['canned'] else ''}")
    print("done; keys in", keys_path)


if __name__ == "__main__":
    main()
