from acr.llm.anthropic_p import sanitize_schema
from acr.harness import schemas


def test_sanitize_strips_ranges_everywhere():
    s = sanitize_schema(schemas.REVIEW_SCHEMA)
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                assert k not in ("minimum", "maximum", "minItems", "maxItems")
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(s)
    assert "minimum 0" in s["properties"]["scores"]["properties"]["originality"]["description"]
    assert s["properties"]["recommendation"]["enum"] == ["accept", "reject"]
    assert "minimum" in schemas.REVIEW_SCHEMA["properties"]["scores"]["properties"]["originality"]  # original untouched
