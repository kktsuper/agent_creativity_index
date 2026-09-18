import datetime as dt

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
{entries}
</feed>"""
ENTRY = """<entry><id>http://arxiv.org/abs/{id}v1</id><published>{pub}</published><title>{title}</title>
<summary>arXiv:{id} Announce Type: new Abstract: {abstract}</summary><arxiv:announce_type>new</arxiv:announce_type>
<author><name>A. Author</name></author><category term="{cat}"/><category term="cs.AI"/></entry>"""


def _feed(cat, n, offset):
    pub = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    es = "\n".join(ENTRY.format(id=f"2609.{offset + i:05d}", pub=pub, title=f"Paper {offset + i} on {cat}",
                                abstract=("We propose a new method for a problem. " * 12), cat=cat) for i in range(n))
    return FEED.format(entries=es)


def test_scout_triage_pick_and_ingest(client, monkeypatch):
    from acr import scout, ingest_arxiv
    from acr.db import db_session
    from acr.models import Paper, ScoutRun
    feeds = {"cs.LG": _feed("cs.LG", 30, 10000), "cs.CL": _feed("cs.CL", 12, 20000)}
    monkeypatch.setattr(ingest_arxiv.httpx, "get", lambda url, **kw: type("R", (), {"text": feeds[url.rsplit("/", 1)[-1]], "raise_for_status": lambda self: None})())
    monkeypatch.setattr(scout.time, "sleep", lambda s: None)
    monkeypatch.setattr("acr.ingest_arxiv.fetch_fulltext", lambda aid, max_chars=150000: "body " * 800)
    with db_session() as db:
        run = scout.run_scout(db, {"cs.LG": "machine learning", "cs.CL": "computation and language"}, max_picks=5)
        rid = run.id
    with db_session() as db:
        run = db.get(ScoutRun, rid)
        assert run.status == "done" and run.pool_size == 42 and len(run.candidates) == 42 and len(run.picks) == 5
        means = [c["mean"] for c in run.candidates]
        assert means == sorted(means, reverse=True) and all(len(c["scores"]) == 2 for c in run.candidates)
        for c in run.picks:
            p = db.query(Paper).filter(Paper.external_id == c["arxiv_id"]).first()
            assert p and p.scout_run_id == rid and p.source_kind == "arxiv" and c["acr_id"] == p.acr_id
        # re-running never re-picks already ingested papers
        run2 = scout.run_scout(db, {"cs.LG": "machine learning"}, max_picks=3)
        assert not ({c["arxiv_id"] for c in run2.picks} & {c["arxiv_id"] for c in run.picks})
    assert client.get("/scout").status_code == 200
    r = client.get(f"/scout/{rid}")
    assert r.status_code == 200 and "picks from 42 papers" in r.text
    from tests.test_end_to_end import drain
    drain()
    with db_session() as db:
        p = db.query(Paper).filter(Paper.scout_run_id == rid).first()
        assert p.score_creativity is not None
    assert f"scout run #{rid}" in client.get(f"/papers/{p.acr_id}").text
