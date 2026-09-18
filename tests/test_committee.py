import pytest
from acr.harness.committee import assign_committee, CommitteeError
from acr.harness.defaults import default_config


def test_two_judges_rotating_chair():
    cfg = default_config()
    a = assign_committee(cfg, "independent", rotation_index=0)
    b = assign_committee(cfg, "independent", rotation_index=1)
    labs = {r["lab"] for r in a["reviewers"]}
    assert labs == {"anthropic", "openai"} and a["chair"]["lab"] in labs and "slot" not in a["chair"]
    chairs = {assign_committee(cfg, "independent", i)["chair"]["lab"] for i in range(4)}
    assert chairs == {"anthropic", "openai"}          # chair rotates between the judges
    assert a["reviewers"][0]["lab"] != b["reviewers"][0]["lab"]


def test_recusal_leaves_single_judge():
    cfg = default_config()
    c = assign_committee(cfg, "openai", rotation_index=3)
    assert [r["lab"] for r in c["reviewers"]] == ["anthropic"] and c["chair"]["lab"] == "anthropic"
    assert "short committee" in c["note"]
    cfg["committee"]["allow_short"] = False
    with pytest.raises(CommitteeError):
        assign_committee(cfg, "openai", 0)


def test_distinct_chair_mode_still_works():
    cfg = default_config()
    cfg["committee"]["labs"] += [{"lab": "google", "provider": "google", "model": "g"}, {"lab": "xai", "provider": "xai", "model": "x"}]
    cfg["committee"].update(reviewers_per_paper=3, chair_mode="distinct")
    c = assign_committee(cfg, "independent", 2)
    labs = [r["lab"] for r in c["reviewers"]] + [c["chair"]["lab"]]
    assert len(set(labs)) == 4
