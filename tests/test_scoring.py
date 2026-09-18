from acr.scoring import derive, aggregate, agg_decay_half


def test_derive_formula():
    s = derive({"originality": 8, "depth": 5, "potential_impact": 6, "implementation": 10})
    assert s["novelty"] == 40 and s["impact"] == 60 and s["creativity"] == 24.0


def test_clamp():
    s = derive({"originality": 14, "depth": -3, "potential_impact": 5, "implementation": 5})
    assert s["originality"] == 10 and s["depth"] == 0 and s["creativity"] == 0


def test_decay_half_rewards_quality_over_volume():
    one_great = agg_decay_half([80])
    three_ok = agg_decay_half([40, 40, 40])
    assert one_great > three_ok
    assert agg_decay_half([80, 60]) > one_great          # more good work still helps
    assert abs(agg_decay_half([80, 1]) - one_great) < 1  # padding adds ~nothing


def test_aggregators():
    assert aggregate("top3_mean", [10, 20, 30, 40]) == 30
    assert aggregate("max", [1, 5]) == 5
    assert aggregate("unknown", [10]) == 5.0  # falls back to decay_half
