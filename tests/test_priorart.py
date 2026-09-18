import datetime as dt

from acr.priorart import latest_date, filter_general_items, merge_items


def test_latest_date_is_conservative():
    assert latest_date("2020") == dt.datetime(2020, 12, 31, 23, 59, 59)
    assert latest_date("2020-02") == dt.datetime(2020, 2, 29, 23, 59, 59)
    assert latest_date("2020-02-03").date() == dt.date(2020, 2, 3)
    assert latest_date("") is None and latest_date("unknown") is None and latest_date("99") is None


def test_filter_applies_date_rule():
    cutoff = dt.datetime(2026, 9, 3, 3, 0, 0)
    raw = [
        {"title": "Old patent", "kind": "patent", "date": "2017-03-02", "date_evidence": "filing date", "url": "https://patents.example/1", "source_name": "USPTO", "snippet": "", "relevance": "same mechanism"},
        {"title": "Same-year bare date", "kind": "news", "date": "2026", "date_evidence": "guess", "url": "https://n.example/2", "source_name": "", "snippet": "", "relevance": ""},
        {"title": "Undated blog", "kind": "web", "date": "", "date_evidence": "", "url": "https://b.example/3", "source_name": "", "snippet": "", "relevance": ""},
        {"title": "Too new", "kind": "product", "date": "2026-09-10", "date_evidence": "release note", "url": "https://p.example/4", "source_name": "", "snippet": "", "relevance": ""},
        {"title": "Month-only ok", "kind": "code", "date": "2026-08", "date_evidence": "commit", "url": "https://c.example/5", "source_name": "", "snippet": "", "relevance": ""},
    ]
    kept, dropped = filter_general_items(raw, cutoff, "anthropic", verify=False)
    assert [k["title"] for k in kept] == ["Old patent", "Month-only ok"]
    assert {d["title"] for d in dropped} == {"Same-year bare date", "Undated blog", "Too new"}
    assert kept[0]["date_source"].startswith("model-reported") and kept[0]["found_by"] == ["anthropic"]


def test_merge_dedupes_by_url_and_tracks_finders():
    a = [{"title": "X", "url": "https://arxiv.org/abs/1234.5678v2", "found_by": ["openai"], "date": "2020-01-01"}]
    b = [{"title": "X (arXiv)", "url": "http://www.arxiv.org/pdf/1234.5678", "found_by": ["google"], "date": "2020-01-01"},
         {"title": "Y", "url": "https://y.example", "found_by": ["arxiv"], "date": "2019-01-01"}]
    items = merge_items(a, b)
    assert len(items) == 2 and items[0]["found_by"] == ["openai", "google"] and items[0]["ref"] == "P1" and items[1]["ref"] == "P2"
