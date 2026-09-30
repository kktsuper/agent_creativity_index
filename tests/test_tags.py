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
