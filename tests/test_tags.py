from acr.harness.schemas import DECISION_SCHEMA
from acr.tags import MAX_TAGS, clean_tag, normalize_tags, tag_key


def test_clean_tag():
    assert clean_tag("  Reinforcement   Learning. ") == "reinforcement learning"
    assert clean_tag("#LLMs") == "llms"
    assert clean_tag("") == "" and clean_tag("  --  ") == "" and clean_tag(None) == "" and clean_tag(3) == ""
    assert clean_tag("x" * 41) == ""


def test_near_identical_spellings_share_a_key():
    assert tag_key("llms") == tag_key("LLM") == "llm"
    assert tag_key("world-models") == tag_key("world model")
    assert tag_key("loss") == "loss" and tag_key("gps") == "gps"   # no over-eager plural stripping


def test_normalize_reuses_existing_spelling_and_dedupes():
    existing = ["llm", "world models"]
    assert normalize_tags(["LLMs", "World-Models", "llm", "Robotics"], existing) == ["llm", "world models", "robotics"]
    assert normalize_tags(["LLMs", "LLM"]) == ["llms"]            # no vocabulary yet: first spelling wins
    assert len(normalize_tags([f"topic {i}" for i in range(9)])) == MAX_TAGS
    assert normalize_tags(None) == [] and normalize_tags("llms") == [] and normalize_tags([None, "", 7]) == []


def test_decision_schema_asks_for_tags():
    assert "topic_tags" in DECISION_SCHEMA["required"]
    assert DECISION_SCHEMA["properties"]["topic_tags"]["items"]["type"] == "string"


def test_overlapping_reviews_share_spellings(app):
    """(Tags unique to this test, so runs left by other tests cannot decide the spelling.)
    A run that has decided but not finished has not copied its tags to the paper yet; a second review
    deciding in the meantime must still reuse its spelling. Sandbox runs never shape the vocabulary."""
    import datetime as dt
    from acr.db import db_session
    from acr.harness.defaults import current_harness
    from acr.models import Author, Paper, ReviewRun
    from acr.tags import tags_in_use
    with db_session() as db:
        hv = current_harness(db)
        a = Author(slug="tag-overlap", name="Tag Overlap", lab="independent", api_key_hash="tag-overlap",
                   api_key_prefix="acr_tag")
        now = dt.datetime.utcnow()
        p = Paper(acr_id="ACR-2026-999001", author=a, title="Overlap", abstract="x", paper_type="result",
                  field="ml", format="md", content_hash="0" * 64, priority_at=now, embargo_until=now)
        db.add_all([a, p])
        db.flush()
        db.add_all([ReviewRun(paper_id=p.id, harness_version_id=hv.id, harness_version=hv.version,
                              stage="decision", topic_tags=["zeta function"]),
                    ReviewRun(paper_id=p.id, harness_version_id=hv.id, harness_version=hv.version, sandbox=True,
                              stage="done", topic_tags=["quux widget"])])
        db.flush()
        vocab = tags_in_use(db)
        assert not p.topic_tags
        assert normalize_tags(["Zeta-Functions", "quux widgets"], vocab) == ["zeta function", "quux widgets"]
        db.rollback()
