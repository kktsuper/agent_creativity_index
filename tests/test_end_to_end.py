"""Register -> submit -> desk screen -> review (mock committee) -> rebuttal -> decision -> publish -> index."""
import datetime as dt
import json

from tests.conftest import make_paper


def register(client, name, lab="independent"):
    r = client.post("/api/v1/authors/register", json={"name": name, "lab": lab, "description": "test agent",
                                                       "developer_contact": "dev@example.org"})
    assert r.status_code == 201, r.text
    return r.json()["api_key"], r.json()["author"]["slug"]


def submit(client, key, title, embargo=0, related=""):
    r = client.post("/api/v1/submissions", headers={"Authorization": f"Bearer {key}"},
                    data={"title": title, "abstract": "We study " + "explicit constructions and witnesses " * 12,
                          "paper_type": "result", "field": "combinatorics", "keywords": "ramsey, bounds",
                          "embargo_months": embargo, "related": related},
                    files=[("paper", ("paper.md", make_paper(title).encode(), "text/markdown")),
                           ("code", ("check.py", b"print('verify')\n", "text/x-python"))])
    assert r.status_code == 201, r.text
    return r.json()


def drain():
    from acr.jobs import process_available, tick
    from acr.db import db_session
    with db_session() as db:
        tick(db)
    return process_available()


def close_rebuttals():
    from acr.db import db_session
    from acr.models import ReviewRun
    with db_session() as db:
        for r in db.query(ReviewRun).filter(ReviewRun.stage == "rebuttal").all():
            r.rebuttal_deadline = dt.datetime.utcnow()


def test_full_pipeline(client):
    key, slug = register(client, "Cartographer-7")
    sub = submit(client, key, "Sparse witnesses for Ramsey lower bounds")
    acr_id = sub["acr_id"]
    assert acr_id.startswith("ACR-") and len(sub["content_hash"]) == 64

    # not public yet: read API hides it, page shows stub
    assert client.get(f"/api/v1/papers/{acr_id}").status_code == 404
    assert "not public yet" in client.get(f"/papers/{acr_id}").text

    drain()  # anchor + desk screen + start review + prior art + reviews -> rebuttal opens
    me = client.get(f"/api/v1/submissions/{acr_id}", headers={"Authorization": f"Bearer {key}"}).json()
    assert me["status"] == "rebuttal", me
    assert len(me["review"]["reviews"]) == 2
    labs = {r["lab"] for r in me["review"]["reviews"]}
    assert labs == {"anthropic", "openai"} and me["review"]["committee"]["chair"]["lab"] in labs

    # rebuttal: token cap enforced, then accepted
    too_long = client.post(f"/api/v1/submissions/{acr_id}/rebuttal", headers={"Authorization": f"Bearer {key}"},
                           json={"text": "word " * 3000})
    assert too_long.status_code == 422
    ok = client.post(f"/api/v1/submissions/{acr_id}/rebuttal", headers={"Authorization": f"Bearer {key}"},
                     json={"text": "Reviewer 2 asks about pruning. We verified every witness independently."})
    assert ok.status_code == 200, ok.text
    drain()  # discussion -> decision -> done -> publish (no embargo)

    from acr.harness.defaults import DEFAULT_VERSION
    mine = client.get(f"/api/v1/submissions/{acr_id}", headers={"Authorization": f"Bearer {key}"}).json()
    assert mine["decision"] in ("accept", "reject") and mine["scores"]["creativity"] >= 0
    assert mine["harness_version"] == DEFAULT_VERSION
    assert mine["review"]["rebuttal"]["text"].startswith("Reviewer 2")
    assert len(mine["review"]["transcripts"]) >= 11  # 2 searchers x 2 + query gen + 2 reviews + author + chair + 2 discussion + decision
    assert all(t["harness_version"] == DEFAULT_VERSION for t in mine["review"]["transcripts"])
    pa = mine["review"]["prior_art"]
    assert pa["items"] and all(it["date"] < pa["cutoff"][:10] for it in pa["items"])   # date rule
    assert any(it["kind"] == "patent" for it in pa["items"]) and pa["dropped"]          # general search, undated dropped
    assert {s["lab"] for s in pa["searchers"]} == {r["lab"] for r in mine["review"]["reviews"]} | {mine["review"]["committee"]["chair"]["lab"]}

    if mine["decision"] == "reject":
        # rejected: private to the author; invisible to the public record
        assert mine["status"] == "rejected" and mine["public_at"] is None
        assert client.get(f"/api/v1/papers/{acr_id}").status_code == 404
        assert client.get(f"/papers/{acr_id}").status_code == 404
        assert acr_id not in client.get("/papers").text and acr_id not in client.get("/api/v1/feed").text
        # submit again until we get an accepted one for the rest of the checks
        for k in range(4):
            sub2 = submit(client, key, f"Sparse witnesses for Ramsey lower bounds, attempt {k}")
            drain(); close_rebuttals(); drain()
            m2 = client.get(f"/api/v1/submissions/{sub2['acr_id']}", headers={"Authorization": f"Bearer {key}"}).json()
            if m2["decision"] == "accept":
                acr_id = sub2["acr_id"]
                break
    d = client.get(f"/api/v1/papers/{acr_id}").json()
    assert d["decision"] == "accept" and d["review"]["transcripts"]
    assert any(c["acr_id"].endswith("2025-000001") for c in d["cites"])  # cited by ID in references
    page = client.get(f"/papers/{acr_id}")
    assert page.status_code == 200 and "Transcripts" in page.text and "Reviewer 1" in page.text and "patent" in page.text
    assert 'class="radar"' in page.text and page.text.count("<polygon") == 3   # chair's final shape + 2 reviewer outlines
    assert "chair's final scores" in page.text and "Final scores: Originality" in page.text
    assert "Reviewer 1 · anthropic" in page.text or "Reviewer 1 · openai" in page.text   # named legend entries
    assert 'href="/static/style.css?v=' in page.text   # cache-busted stylesheet so CSS changes reach cached browsers

    idx = client.get("/api/v1/index?window=30").json()
    assert any(r["author"]["slug"] == slug for r in idx["rows"])
    for path in ["/", "/papers", "/authors", "/index", "/review", "/submit", "/rules", "/api/v1/feed", "/api/v1/stats",
                 f"/api/v1/harness/{DEFAULT_VERSION}", f"/review/harness/{DEFAULT_VERSION}", f"/api/v1/papers/{acr_id}/citations",
                 f"/api/v1/papers/{acr_id}/source", f"/api/v1/papers/{acr_id}/anchor.ots", "/api/v1/authors",
                 f"/api/v1/authors/{slug}", f"/authors/{slug}"]:
        assert client.get(path).status_code == 200, path


def test_embargo_blocks_publication(client):
    key, slug = register(client, "Embargoed-Agent", lab="anthropic")
    sub = submit(client, key, "A deliberately embargoed result on witness families", embargo=6)
    drain()
    r = client.post(f"/api/v1/submissions/{sub['acr_id']}/rebuttal/waive", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    drain()
    me = client.get(f"/api/v1/submissions/{sub['acr_id']}", headers={"Authorization": f"Bearer {key}"}).json()
    assert me["status"] in ("decided", "rejected") and me["public_at"] is None
    assert client.get(f"/api/v1/papers/{sub['acr_id']}").status_code == 404
    labs = {r["lab"] for r in me["review"]["reviews"]} | {me["review"]["committee"]["chair"]["lab"]}
    assert labs == {"openai"} and "short committee" in me["review"]["committee"]["note"]  # anthropic author -> openai judges alone


def test_desk_screen_rejects_incomplete(client):
    key, _ = register(client, "Sloppy-Agent")
    r = client.post("/api/v1/submissions", headers={"Authorization": f"Bearer {key}"},
                    data={"title": "Too short paper", "abstract": "tiny", "paper_type": "proposal", "field": "x"},
                    files=[("paper", ("p.md", b"# hi\nshort", "text/markdown"))])
    assert r.status_code == 201, r.text
    drain()
    me = client.get(f"/api/v1/submissions/{r.json()['acr_id']}", headers={"Authorization": f"Bearer {key}"}).json()
    assert me["status"] == "desk_rejected" and "400" in me["status_reason"]


def test_rate_limit(client):
    from acr.db import db_session
    from acr.settings_store import set_setting
    key, _ = register(client, "Spammy-Agent")
    with db_session() as db:
        set_setting(db, "rate_limit_submissions_per_day", 1)
    submit(client, key, "First paper within the limit on witness families")
    r = client.post("/api/v1/submissions", headers={"Authorization": f"Bearer {key}"},
                    data={"title": "Second paper over the limit", "abstract": "x " * 50, "paper_type": "result", "field": "f"},
                    files=[("paper", ("p.md", make_paper().encode(), "text/markdown"))])
    assert r.status_code == 429
    with db_session() as db:
        set_setting(db, "rate_limit_enabled", False)
    r = client.post("/api/v1/submissions", headers={"Authorization": f"Bearer {key}"},
                    data={"title": "Third paper with limit off", "abstract": "x " * 50, "paper_type": "result", "field": "f"},
                    files=[("paper", ("p.md", make_paper().encode(), "text/markdown"))])
    assert r.status_code == 201
    with db_session() as db:
        set_setting(db, "rate_limit_enabled", True)
        set_setting(db, "rate_limit_submissions_per_day", 20)
    close_rebuttals(); drain(); close_rebuttals(); drain()


def test_admin_sandbox_review_and_harness_publish(client):
    from acr.db import db_session
    from acr.models import Paper, ReviewRun, HarnessVersion
    assert client.get("/admin", follow_redirects=False).status_code == 303
    assert client.post("/admin/login", data={"password": "pw"}, follow_redirects=False).status_code == 303
    assert "Dashboard" in client.get("/admin").text
    with db_session() as db:
        p = db.query(Paper).filter(Paper.public_at.isnot(None)).first()
        acr_id, official_score, official_run = p.acr_id, p.score_creativity, p.official_run_id
    from acr.harness.defaults import DEFAULT_VERSION
    client.post("/admin/harness/new-draft", data={"from_version": DEFAULT_VERSION})
    with db_session() as db:
        draft = db.query(HarnessVersion).filter(HarnessVersion.is_draft.is_(True)).first()
        cfg = json.loads(json.dumps(draft.config)); cfg["prompts"]["reviewer_system"] += "\nBe terse."
        dv, did, cfg_json = draft.version, draft.id, json.dumps(cfg)
    assert client.post(f"/admin/harness/{dv}/save", data={"config_json": cfg_json, "changelog": "terser"}).status_code == 200
    client.post("/admin/test-review", data={"acr_id": acr_id, "harness_id": did})
    drain()
    with db_session() as db:
        p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
        sandbox = [r for r in db.query(ReviewRun).filter(ReviewRun.paper_id == p.id, ReviewRun.harness_version_id == did).all() if r.sandbox]
        assert sandbox and sandbox[0].stage == "done" and sandbox[0].decision, sandbox and sandbox[0].error
        assert p.official_run_id == official_run and p.score_creativity == official_score  # record untouched
        assert client.get(f"/admin/runs/{sandbox[0].id}").status_code == 200
    client.post(f"/admin/harness/{dv}/publish", data={"new_version": "1.3.0", "changelog": "Terser reviewer prompt."})
    h = client.get("/api/v1/harness").json()
    assert h["current"] == "1.3.0" and any(v["version"] == "1.3.0" for v in h["versions"])
    assert "1.3.0" in client.get("/review").text
    assert client.get(f"/api/v1/papers/{acr_id}").json()["harness_version"] == DEFAULT_VERSION  # never re-scored
    for path in ["/admin/papers", f"/admin/papers/{acr_id}", "/admin/harness", "/admin/harness/1.3.0",
                 "/admin/improve", "/admin/authors", "/admin/jobs"]:
        assert client.get(path).status_code == 200, path


def test_phase3_pairwise_and_improvement(client):
    from acr.db import db_session
    from acr.phase3 import run_pairwise_round, run_improvement
    from acr.models import ImprovementReport, Paper
    key, slug = register(client, "Second-Author")
    submit(client, key, "Another accepted paper on structured families for bounds")
    drain(); close_rebuttals(); drain()
    with db_session() as db:
        accepted_authors = {p.author_id for p in db.query(Paper).filter(Paper.status == "published").all()}
    with db_session() as db:
        n = run_pairwise_round(db, 90, 10)
    idx = client.get("/api/v1/index?window=90").json()
    if len(accepted_authors) >= 2:
        assert n >= 1 and any(r["head_to_head"] is not None for r in idx["rows"])
    with db_session() as db:
        rep = ImprovementReport(base_version="1.3.0"); db.add(rep); db.flush(); rid = rep.id
    with db_session() as db:
        run_improvement(db, rid, calibration_size=1)
        rep = db.get(ImprovementReport, rid)
        assert rep.status == "ready", rep.error
        assert rep.draft_version and rep.calibration["papers"]
        from acr.models import ReviewRun, Review
        for c in rep.calibration["papers"]:   # single-session engine path: discussion turns must exist
            run = db.get(ReviewRun, c["run_id"])
            revs = db.query(Review).filter(Review.run_id == run.id).all()
            assert run.stage == "done" and len(revs) == 2 and all(r.final for r in revs)
            assert db.query(__import__("acr.models", fromlist=["Transcript"]).Transcript).filter_by(run_id=run.id, stage="discussion").count() == 3
    assert client.get("/admin/improve").status_code == 200
