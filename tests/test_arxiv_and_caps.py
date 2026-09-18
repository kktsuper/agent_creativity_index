"""arXiv ingestion (parsed from a canned feed), review without rebuttal, self-exclusion, and the spend caps."""
import datetime as dt

import pytest

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry>
  <id>http://arxiv.org/abs/2609.01234v1</id>
  <published>{pub}</published>
  <title>Sparse witnesses for Ramsey lower bounds via structured search</title>
  <summary>We give explicit constructions for lower bounds. {filler}</summary>
  <author><name>Ada Lovelace</name></author><author><name>Alan Turing</name></author>
  <arxiv:primary_category term="math.CO"/><category term="math.CO"/><category term="cs.DM"/>
</entry>
<entry>
  <id>http://arxiv.org/abs/2609.09999v2</id>
  <published>{pub}</published>
  <title>A revised paper</title><summary>revision</summary>
  <author><name>X</name></author><arxiv:primary_category term="math.CO"/><category term="math.CO"/>
</entry>
</feed>"""


def _feed():
    pub = (dt.datetime.utcnow() - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return FEED.format(pub=pub, filler="word " * 60)


def test_parse_feed_and_ingest(client, monkeypatch):
    from acr import ingest_arxiv
    from acr.db import db_session
    from acr.models import Paper, Author, ReviewRun, Transcript
    entries = ingest_arxiv.parse_feed(_feed())
    assert entries[0]["arxiv_id"] == "2609.01234" and entries[0]["primary_category"] == "math.CO"
    monkeypatch.setattr(ingest_arxiv, "_get", lambda url, tries=4: type("R", (), {"text": _feed(), "content": b""})())
    monkeypatch.setattr(ingest_arxiv, "fetch_fulltext", lambda aid, max_chars=150000: "FULLTEXT-SENTINEL " + "body " * 800)
    import acr.harness.runner as runner
    monkeypatch.setattr("acr.ingest_arxiv.fetch_fulltext", lambda aid, max_chars=150000: "FULLTEXT-SENTINEL " + "body " * 800)
    with db_session() as db:
        rep = ingest_arxiv.ingest_recent(db, {"combinatorics": "math.CO"}, days=7, per_field=1, sleep_s=0)
    assert rep[0]["arxiv_id"] == "2609.01234"
    acr_id = rep[0]["acr_id"]
    with db_session() as db:
        p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
        a = db.get(Author, p.author_id)
        assert p.source_kind == "arxiv" and p.skip_rebuttal and p.body_md == "" and p.external_url.endswith("2609.01234")
        assert a.kind == "human" and a.slug == "arxiv-2609-01234" and "Lovelace" in a.name
        assert abs((p.priority_at - (dt.datetime.utcnow() - dt.timedelta(days=2))).total_seconds()) < 120
        # idempotent
        rep2 = ingest_arxiv.ingest_recent(db, {"combinatorics": "math.CO"}, days=7, per_field=1, sleep_s=0)
        assert rep2[0]["acr_id"] == acr_id

    from tests.test_end_to_end import drain
    drain()   # desk screen (skipped for arXiv) -> review -> prior art -> reviews -> rebuttal (skipped) -> ... -> publish
    with db_session() as db:
        p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
        run = db.get(ReviewRun, p.official_run_id)
        assert run.stage == "done", run.error
        assert run.rebuttal_text is None and run.rebuttal_deadline <= run.rebuttal_opens_at + dt.timedelta(seconds=1)
        assert len(run.reviews) == 2 and run.committee["chair"]["lab"] in {r.lab for r in run.reviews}
        # full text was used in prompts but never stored
        ts = db.query(Transcript).filter(Transcript.run_id == run.id).all()
        assert all("FULLTEXT-SENTINEL" not in t.messages[0]["content"] for t in ts)
        assert any("read at review time; not stored" in t.messages[0]["content"] for t in ts if t.stage == "reviews")
        # self-exclusion: an item that is the paper itself never appears
        assert all("2609.01234" not in (it.get("url") or "") for it in run.prior_art["items"])
        assert run.total_cost_usd == 0.0   # mock calls cost nothing
        if p.decision == "accept":
            assert p.public_at is not None
    r = client.get(f"/api/v1/papers?authors=human").json()
    with db_session() as db:
        p = db.query(Paper).filter(Paper.acr_id == acr_id).first()
        if p.decision == "accept":
            assert any(x["acr_id"] == acr_id for x in r["papers"]) and r["papers"][0]["author"]["kind"] == "human"
            assert client.get(f"/papers?authors=human").status_code == 200
            assert client.get(f"/api/v1/papers/{acr_id}/source", follow_redirects=False).status_code == 302
            assert "human agents" in client.get(f"/papers/{acr_id}").text
    assert client.get("/papers?authors=agent").status_code == 200


def test_self_exclusion_matches_title_and_id():
    from acr.harness.runner import Engine
    class P: external_id = "2609.01234"; acr_id = "ACR-2026-000001"; title = "Sparse witnesses for Ramsey lower bounds via structured search"
    e = Engine.__new__(Engine); e.paper = P()
    assert e._is_self({"title": "x", "url": "https://arxiv.org/abs/2609.01234v3"})
    assert e._is_self({"title": "Sparse Witnesses for Ramsey Lower Bounds via Structured Search", "url": "https://mirror.example/1"})
    assert not e._is_self({"title": "Sparse witnesses", "url": "https://arxiv.org/abs/1111.11111"})


def test_spend_cap_stops_run(client, monkeypatch):
    from acr.db import db_session
    from acr.harness.runner import Engine, create_run, SpendCapExceeded
    from acr.harness.defaults import current_harness
    from acr.models import Paper
    from acr.settings_store import set_setting
    import acr.harness.runner as runner
    with db_session() as db:
        p = db.query(Paper).filter(Paper.official_run_id.isnot(None)).first()
        run = create_run(db, p, current_harness(db), sandbox=True)
        set_setting(db, "spend_cap_per_run_usd", 0.01)
        eng = Engine(db, run)
        run.total_cost_usd = 0.009
        monkeypatch.setattr(runner, "projected_call_usd", lambda *a, **k: 0.5)
        with pytest.raises(SpendCapExceeded):
            eng.advance()
        assert run.error.startswith("SpendCapExceeded")
        set_setting(db, "spend_cap_per_run_usd", 3.0)


def test_daily_cap_pauses_job():
    from acr.db import db_session
    from acr.jobs import enqueue, run_one, _pick_job
    from acr.models import ReviewRun, Job
    from acr.settings_store import set_setting
    with db_session() as db:
        run = db.query(ReviewRun).filter(ReviewRun.sandbox.is_(True)).order_by(ReviewRun.id.desc()).first()
        run.stage = "assigned"; run.error = ""
        set_setting(db, "spend_cap_daily_usd", 0.000001)
        from acr.models import Transcript
        t = db.query(Transcript).first(); t.cost_usd = 1.0     # pretend we spent a dollar today
        j = enqueue(db, "advance_run", {"run_id": run.id}); jid = j.id
    with db_session() as db:
        job = _pick_job(db); assert job.id == jid
        run_one(db, job)
    with db_session() as db:
        job = db.get(Job, jid)
        assert job.status == "queued" and "daily spend cap" in job.last_error and job.run_after > dt.datetime.utcnow() + dt.timedelta(minutes=50)
        set_setting(db, "spend_cap_daily_usd", 30.0)
        db.delete(job)
