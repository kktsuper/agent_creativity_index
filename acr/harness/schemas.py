"""JSON schemas for every structured model output in the harness."""

SCORES = {
    "type": "object",
    "properties": {
        "originality": {"type": "number", "minimum": 0, "maximum": 10},
        "depth": {"type": "number", "minimum": 0, "maximum": 10},
        "potential_impact": {"type": "number", "minimum": 0, "maximum": 10},
        "implementation": {"type": "number", "minimum": 0, "maximum": 10},
    },
    "required": ["originality", "depth", "potential_impact", "implementation"],
    "additionalProperties": False,
}

QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8},
                   "rationale": {"type": "string"}},
    "required": ["queries", "rationale"], "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "prior_art_assessment": {
            "type": "object",
            "properties": {
                "closest_items": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"ref": {"type": "string"}, "relation": {"type": "string"}},
                    "required": ["ref", "relation"], "additionalProperties": False}},
                "anticipated": {"type": "boolean"},
                "notes": {"type": "string"},
            },
            "required": ["closest_items", "anticipated", "notes"], "additionalProperties": False,
        },
        "scores": SCORES,
        "score_justification": {
            "type": "object",
            "properties": {"originality": {"type": "string"}, "depth": {"type": "string"},
                           "potential_impact": {"type": "string"}, "implementation": {"type": "string"}},
            "required": ["originality", "depth", "potential_impact", "implementation"], "additionalProperties": False,
        },
        "questions_for_author": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
        "recommendation": {"type": "string", "enum": ["accept", "reject"]},
    },
    "required": ["summary", "strengths", "weaknesses", "prior_art_assessment", "scores", "score_justification",
                 "questions_for_author", "confidence", "recommendation"],
    "additionalProperties": False,
}

CHAIR_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "points_of_disagreement": {"type": "array", "items": {"type": "string"}},
        "rebuttal_assessment": {"type": "string"},
        "questions_to_reviewers": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "points_of_disagreement", "rebuttal_assessment", "questions_to_reviewers"],
    "additionalProperties": False,
}

DISCUSSION_SCHEMA = {
    "type": "object",
    "properties": {
        "response": {"type": "string"},
        "changed_mind_on": {"type": "array", "items": {"type": "string"}},
        "scores": SCORES,
        "confidence": {"type": "integer", "minimum": 1, "maximum": 5},
        "recommendation": {"type": "string", "enum": ["accept", "reject"]},
    },
    "required": ["response", "changed_mind_on", "scores", "confidence", "recommendation"],
    "additionalProperties": False,
}

TOPIC_TAGS = {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5,
              "description": "2 to 5 short topic tags for readers browsing the site, e.g. \"llms\", "
                             "\"reinforcement learning\", \"world models\". Specific research topics, "
                             "lowercase, one to three words each. Not the paper's broad field, and not "
                             "judgments of quality."}

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["accept", "reject"]},
        "meta_review": {"type": "string"},
        "final_scores": SCORES,
        "score_rationale": {"type": "string"},
        "key_prior_art": {"type": "array", "items": {"type": "string"}},
        "topic_tags": TOPIC_TAGS,
    },
    "required": ["decision", "meta_review", "final_scores", "score_rationale", "key_prior_art", "topic_tags"],
    "additionalProperties": False,
}

TAGS_SCHEMA = {   # tag backfill for papers decided before the chair wrote tags
    "type": "object",
    "properties": {"topic_tags": TOPIC_TAGS},
    "required": ["topic_tags"],
    "additionalProperties": False,
}

COMPARISON_SCHEMA = {
    "type": "object",
    "properties": {
        "preference": {"type": "string", "enum": ["a", "b", "tie"]},
        "confidence": {"type": "number", "minimum": 0.5, "maximum": 1.0},
        "rationale": {"type": "string"},
    },
    "required": ["preference", "confidence", "rationale"], "additionalProperties": False,
}

IMPROVER_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "changes": {"type": "array", "items": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "new_value": {"type": "string"}, "why": {"type": "string"}},
            "required": ["path", "new_value", "why"], "additionalProperties": False}},
        "changelog": {"type": "string"},
    },
    "required": ["diagnosis", "changes", "changelog"], "additionalProperties": False,
}
