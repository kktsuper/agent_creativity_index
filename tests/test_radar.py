from acr.web.radar import AXES, CX, CY, R, point, polygon, radar_geometry


def test_points_scale_with_score():
    assert point(0, 10) == (CX, CY - R)          # originality, top
    assert point(1, 10) == (CX + R, CY)          # depth, right
    assert point(2, 10) == (CX, CY + R)          # implementation, bottom
    assert point(3, 10) == (CX - R, CY)          # potential impact, left
    assert point(0, 0) == (CX, CY) and point(0, 5) == (CX, CY - R / 2)
    assert point(1, 14) == point(1, 10) and point(1, -3) == point(1, 0)   # clamped
    assert point(2, "bad") == (CX, CY)


def test_geometry_shape_and_reviewers():
    final = {"originality": 7.0, "depth": 6.5, "potential_impact": 5.0, "implementation": 4.0}
    revs = [{"label": "Reviewer 1 (anthropic)", "scores": dict(final, originality=8.0)},
            {"label": "Reviewer 2 (openai)", "scores": {"originality": 1}},   # incomplete: skipped
            {"label": "Reviewer 3", "scores": {}}]
    g = radar_geometry(final, revs)
    assert g and len(g["rings"]) == 4 and len(g["axes"]) == 4 and len(g["labels"]) == 4
    assert g["main"]["points"] == polygon(final) and len(g["main"]["markers"]) == 4
    assert [m["value"] for m in g["main"]["markers"]] == ["7.0", "6.5", "4.0", "5.0"]   # clockwise from top
    assert len(g["others"]) == 1 and g["others"][0]["title"].startswith("Reviewer 1")
    assert "Originality 7.0" in g["aria"]
    # every label stays inside the viewBox
    for l in g["labels"]:
        assert 0 <= l["x"] <= g["w"] and all(0 < line["y"] < g["h"] for line in l["lines"])
    assert [k for k, _ in AXES] == ["originality", "depth", "implementation", "potential_impact"]


def test_geometry_missing_scores_is_none():
    assert radar_geometry(None) is None
    assert radar_geometry({"originality": 5}) is None
    assert radar_geometry({"originality": None, "depth": 1, "potential_impact": 1, "implementation": 1}) is None
