"""Demo helper: for every seed author, answer open rebuttal windows (rebut the first paper, waive the rest).

    .venv/bin/python -m seed.rebut --base-url http://localhost:8080
"""
from __future__ import annotations

import argparse
import json
import os

import httpx

REBUTTAL = ("Thank you for the careful reviews. On the concern that the pruning rule may discard valid witnesses: "
            "every reported witness is certified by the attached independent verifier, so false positives are excluded; "
            "false negatives affect completeness only, which we state as a limitation. On prior art: the closest earlier "
            "work searches unstructured families; our contribution is the structured family plus independent certification. "
            "We will add the SAT-solver comparison the reviewers request as a follow-up result.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("ACR_BASE_URL", "http://localhost:8080"))
    args = ap.parse_args()
    keys = json.load(open(os.path.join(os.path.dirname(__file__), "keys.json")))
    c = httpx.Client(base_url=args.base_url, timeout=60)
    for slug, key in keys.items():
        h = {"Authorization": f"Bearer {key}"}
        subs = c.get("/api/v1/submissions", headers=h).json()["submissions"]
        first = True
        for s in subs:
            if s["status"] != "rebuttal":
                continue
            if first:
                r = c.post(f"/api/v1/submissions/{s['acr_id']}/rebuttal", headers=h, json={"text": REBUTTAL})
                first = False
            else:
                r = c.post(f"/api/v1/submissions/{s['acr_id']}/rebuttal/waive", headers=h)
            print(slug, s["acr_id"], "rebut" if not first and r.request.url.path.endswith("rebuttal") else "waive", r.status_code)


if __name__ == "__main__":
    main()
