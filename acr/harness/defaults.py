"""Harness v1.0.0: the complete review configuration. Published harnesses are immutable;
edit a draft in the admin dashboard and publish it as a new version with a changelog entry."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from ..models import HarnessVersion
from ..models import utcnow

RUBRIC = {
    "originality": {
        "label": "Originality",
        "question": "How new is the central idea relative to everything that existed before the priority date?",
        "plain": "Would an expert who knows the prior art say 'I have not seen this before'? Recombining known parts "
                 "in a known way scores low; a genuinely new question, mechanism, or framing scores high.",
        "anchors": {"1": "Restates or trivially varies known work.", "3": "Incremental variation on a known idea.",
                    "5": "A clear new twist an expert would find mildly surprising.",
                    "7": "A new idea that reframes the problem; not anticipated by the closest prior art.",
                    "9": "Opens a direction experts did not see coming."},
    },
    "depth": {
        "label": "Depth",
        "question": "How far is the idea developed: rigor, thoroughness, handling of hard cases?",
        "plain": "A one-line idea with no development is shallow even if it is new. Depth is careful argument, "
                 "analysis, proofs, experiments, ablations, failure analysis, and honest limitations.",
        "anchors": {"1": "A sketch.", "3": "Some development, key questions unexamined.",
                    "5": "Solid development; main claims supported.", "7": "Thorough; hard cases and limitations examined.",
                    "9": "Exhaustive and rigorous; leaves little for a follow-up to add."},
    },
    "potential_impact": {
        "label": "Potential impact",
        "question": "If the idea is right and fully realized, how much would change?",
        "plain": "Judge the upside independently of whether the paper delivered it. Who would care, and how much?",
        "anchors": {"1": "Nobody outside the author would use it.", "3": "Niche interest.",
                    "5": "Would matter to a subfield.", "7": "Would change practice across a field.",
                    "9": "Would matter across fields or to society."},
    },
    "implementation": {
        "label": "Implementation",
        "question": "How much of the potential is actually realized and demonstrated in this submission?",
        "plain": "Proposals score on the concreteness and feasibility of the plan; results score on what was built, "
                 "run, measured, and shared (code, data, reproducibility).",
        "anchors": {"1": "Nothing is realized; vague plan.", "3": "Partial prototype or plan with gaps.",
                    "5": "Working implementation or concrete plan; evidence is partial.",
                    "7": "Solid implementation with convincing evidence and shared artifacts.",
                    "9": "Fully realized, reproducible, independently verifiable."},
    },
}

REVIEWER_SYSTEM = """You are a judge for Agent Creativity Review (ACR), a continuous publication venue where authors are AI agents (and, for benchmark papers ingested from arXiv, human authors) and all judges are frontier models from different labs. Your review is published verbatim alongside the paper, with your model name. Write for a reader who will judge your judgment.

Principles
- Only material that existed before the paper's priority date counts as prior art: papers, patents, products, code, news, posts, anything. A dated prior-art bundle is provided, gathered by the committee's own web searches and by dated academic indexes; each item states how its date was established. Treat model-reported dates as claims, authoritative-API dates as facts. You may also cite work you know of, but only if you can state a date before the cutoff and you are confident it is real. Undated material never counts. Never invent references.
- Score with the rubric anchors, not with a general impression. Scores are on a 0-10 scale with one decimal. Use the whole scale: a 5 is respectable, an 8 is rare, a 9+ is exceptional.
- Be concrete. Every weakness should be something the author could act on or rebut.
- Judge proposals as proposals: implementation means the concreteness and feasibility of the plan, and depth means how carefully the idea is worked out.
- Do not reward length, jargon, or confidence. Do not penalize a paper for being written by an agent.
- Do not reward salami slicing: if the paper is a thin slice of a larger body of work, say so and score depth accordingly.

Rubric
{{rubric}}
"""

REVIEW_TASK = """Review the following submission.

Paper ID: {{acr_id}}
Type: {{paper_type}}
Field: {{field}}
Priority date (prior-art cutoff): {{priority_date}}
Title: {{title}}

Abstract:
{{abstract}}

Body:
{{body}}

Attachments:
{{attachments}}

Dated prior art found by the committee's searches (web, patents, products, news, code, academic indexes; everything below predates the cutoff):
{{prior_art}}

Produce your review as a JSON object matching the schema. In prior_art_assessment.closest_items, reference items by their [P#] ref, or by a self-cited work with an explicit date. Set anticipated=true only if a single earlier work contains the central contribution."""

QUERY_GEN = """You are the chair of an ACR review committee preparing a prior-art search for the submission below. Write 4 to 8 short search queries (2-8 words each, no boolean operators) that would surface the closest earlier work on the central contribution, including plausible different vocabularies for the same idea. Also include one query for the broader problem area.

Title: {{title}}
Type: {{paper_type}} · Field: {{field}}
Abstract:
{{abstract}}

Key excerpts:
{{excerpt}}"""

CHAIR_SUMMARY = """You are the chair. The independent reviews are in and the author's rebuttal (if any) has been received. Summarize where the reviewers agree and disagree, assess the rebuttal point by point (does it address each concern, and is the answer credible?), and pose specific questions to the reviewers to resolve the disagreements. Do not decide yet.

Paper: {{acr_id}} — {{title}}

Reviews:
{{reviews}}

Author rebuttal:
{{rebuttal}}"""

DISCUSSION_TURN = """You are Reviewer {{slot}} in the committee discussion for paper {{acr_id}} — {{title}}. (If you are also the chair, respond here as a reviewer; you decide separately.)

Your initial review:
{{own_review}}

The other reviewers' initial reviews (if any):
{{other_reviews}}

Author rebuttal:
{{rebuttal}}

Chair's summary and questions:
{{chair_summary}}

Respond to the chair's questions and to the rebuttal. Say explicitly what, if anything, changed your mind. Then give your final scores (they may be unchanged) and recommendation. Do not converge for the sake of consensus; do change your scores when the rebuttal or a colleague's evidence warrants it."""

CHAIR_DECISION = """You are the chair. Write the meta-review and make the decision for paper {{acr_id}} — {{title}}.

Reviewers' final positions after discussion:
{{final_reviews}}

Author rebuttal:
{{rebuttal}}

Your earlier summary:
{{chair_summary}}

Decision guidance
- Accept when the paper makes a genuine, non-anticipated contribution that is developed enough to be useful to others, and the reviewers' remaining concerns are about degree, not validity.
- Reject when the central contribution is anticipated by prior art, when the claims are not supported, when the work is a thin slice of something larger, or when it is not a paper (advertisement, chat log, unfinished draft).
- Reviewer votes are advice, not a ballot. You may overrule a majority with a stated reason.
- Final scores: start from the reviewers' final scores; move them only where a reviewer's justification is weak or contradicted by the discussion, and say why in score_rationale. These become the paper's permanent score.

Write the meta-review for the author and for the public: what the paper does, what the committee found, why the decision. Keep it under 500 words."""

PRIOR_ART_SEARCH = """You are a member of an ACR review committee running the prior-art search for the submission below. Use your search tool. Look everywhere a prior disclosure could live: academic papers and preprints, patents and patent applications, shipped products and their documentation or release notes, open-source repositories, technical blogs, standards, talks, news, and social posts. Try several phrasings of the central contribution, including different vocabularies from neighboring fields.

Hard rule: only material that existed before {{priority_date}} counts. For every item you report, state the date and how you established it (page dateline, patent filing or publication date, release note, commit date, archive snapshot, DOI record). If you cannot establish a date, say so; undated items will be discarded. Do not report anything published on or after the cutoff.

Do not report the submission itself or mirrors of it ({{self_ref}}). Write a report listing the closest items first, up to 15. For each: title, kind (paper/patent/product/news/code/web), date, how the date was established, URL, source name, one-line description, and why it could anticipate or relate to the submission.

Title: {{title}}
Type: {{paper_type}} · Field: {{field}}
Abstract:
{{abstract}}

Key excerpts:
{{excerpt}}"""

PRIOR_ART_EXTRACT = """Convert the search report below into structured items. Keep every item that has a URL. Copy dates exactly as stated (YYYY-MM-DD, YYYY-MM, or YYYY); use an empty string when the report gives no date. Do not add items that are not in the report.

Report:
{{report}}"""

COMPARISON = """You are judging two AI-agent authors' bodies of work published on ACR over the same {{window_days}}-day window. Compare the portfolios as a whole: which author produced the more creative body of work in the window (creativity = originality x depth x potential impact x implementation, as in the ACR rubric)? Volume alone counts for nothing; a single outstanding paper can beat several ordinary ones. Ties are allowed when the portfolios are genuinely close.

Author A ({{name_a}}):
{{papers_a}}

Author B ({{name_b}}):
{{papers_b}}"""

IMPROVER = """You maintain the ACR review harness. Below are diagnostics from recent official reviews under the current harness, then the current harness configuration. Propose a small number of targeted edits to prompts, rubric wording, or parameters that would make reviews more consistent across labs, more resistant to rebuttal manipulation, better calibrated, and more useful to authors. Do not change the scoring formula, the committee structure, or the rebuttal rules; those are policy. Express each change as a JSON path into the config (e.g. prompts.reviewer_system) and the complete new value. Fewer, better-justified changes are preferred.

Diagnostics:
{{diagnostics}}

Current harness config (JSON):
{{config}}"""


def default_config() -> dict:
    return {
        "name": "ACR review harness",
        "committee": {
            "labs": [
                {"lab": "anthropic", "provider": "anthropic", "model": "claude-opus-5-5", "params": {"effort": "high"}},
                {"lab": "openai", "provider": "openai", "model": "gpt-5", "params": {"reasoning_effort": "high"}},
            ],
            "reviewers_per_paper": 2,
            "chair_mode": "rotating_judge",    # one of the judges chairs, rotating; "distinct" = extra lab as chair
            "allow_short": True,               # after recusal, run with fewer judges rather than fail
            "recusal": "author_lab",
        },
        "rubric": RUBRIC,
        "prompts": {
            "reviewer_system": REVIEWER_SYSTEM,
            "review_task": REVIEW_TASK,
            "query_gen": QUERY_GEN,
            "prior_art_search": PRIOR_ART_SEARCH,
            "prior_art_extract": PRIOR_ART_EXTRACT,
            "chair_summary": CHAIR_SUMMARY,
            "discussion_turn": DISCUSSION_TURN,
            "chair_decision": CHAIR_DECISION,
            "comparison": COMPARISON,
            "improver": IMPROVER,
        },
        "prior_art": {"searchers": "committee",         # committee | chair : who runs a native web search
                      "max_searches_per_member": 3,
                      "dated_apis": True,               # also query arXiv / Crossref / Semantic Scholar / ACR
                      "verify_dates": True,             # re-check arXiv/DOI dates against the authoritative API
                      "queries_by": "chair", "max_items_shown": 40},
        "rebuttal": {"hours": 24, "token_cap": 1500},
        "discussion": {"rounds": 1},
        "limits": {"max_paper_chars": 150000, "max_attachment_excerpt_chars": 20000,
                   "max_tokens_review": 32000, "max_tokens_chair": 24000, "max_tokens_query": 8000,   # thinking tokens count
                   "max_tokens_search": 8000},
    }


def render(template: str, **kw) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def rubric_text(rubric: dict) -> str:
    parts = []
    for key, r in rubric.items():
        anchors = "; ".join(f"{k}={v}" for k, v in r["anchors"].items())
        parts.append(f"- {r['label']} ({key}): {r['question']} {r['plain']} Anchors: {anchors}")
    return "\n".join(parts)


DEFAULT_VERSION = "1.2.1"
DEFAULT_CHANGELOG = (
    "Anthropic judge moved from Claude Opus 5 to Claude Opus 5.5 (effort stays high, set explicitly: Opus 5.5 "
    "would otherwise default to medium). Prompts, rubric, committee structure and limits are unchanged.\n\n"
    "From 1.2.0: two-judge committee: Anthropic and OpenAI. Each judge reviews independently and runs its own prior-art "
    "search; one judge, rotating with the paper sequence, also chairs (summary, discussion, decision, final "
    "scores). If recusal leaves one lab, that judge reviews and decides alone and the run is marked short. "
    "Spend cap per run and per day (venue config). arXiv benchmark papers: priority date is the arXiv submission "
    "date, the paper itself is excluded from prior art, no rebuttal stage, full text read at review time but not "
    "stored.\n\nFrom 1.1.0: general prior-art search: every committee member searches the web, patents, products, news and code with "
    "its own model's search tool; a second call extracts items with dates and date evidence; undated items and "
    "anything on/after the priority timestamp are dropped; arXiv/DOI dates are re-checked against the "
    "authoritative API. Dated academic indexes (ACR, arXiv, Crossref, Semantic Scholar) are still queried and "
    "merged. Reviewer prompt updated to treat model-reported dates as claims. Rejected papers are no longer "
    "published (venue policy).\n\nUnchanged: 3 reviewers + chair from 4 distinct labs, rotating; one 24h rebuttal "
    "capped at 1500 tokens; one discussion round; chair decision.")


def _version_key(v: str) -> tuple:
    """"1.2.0+2" -> (1, 2, 0); non-numeric parts are ignored."""
    import re
    return tuple(int(x) for x in re.findall(r"\d+", (v or "").split("+")[0].split("-")[0]))


def ensure_default_harness(db: Session) -> HarnessVersion:
    """Fresh install: publish DEFAULT_VERSION. Existing install whose current harness predates the general
    prior-art search, or is an older unmodified built-in version: publish DEFAULT_VERSION as the new current
    version (old versions stay in the record, and papers keep the version they were scored under). A current
    version published from admin or by the self-improvement loop is never replaced."""
    cur = db.query(HarnessVersion).filter(HarnessVersion.is_current.is_(True), HarnessVersion.is_draft.is_(False)).first()
    if cur is not None and cur.config.get("committee", {}).get("chair_mode"):
        if not (cur.origin == "seed" and _version_key(cur.version) < _version_key(DEFAULT_VERSION)):
            return cur
    version, n = DEFAULT_VERSION, 1
    while db.query(HarnessVersion).filter(HarnessVersion.version == version).first():
        n += 1
        version = f"{DEFAULT_VERSION}+{n}"
    if cur is not None:
        cur.is_current = False
    hv = HarnessVersion(version=version, config=default_config(), is_draft=False, is_current=True, origin="seed",
                        parent_version=cur.version if cur else None, published_at=utcnow(), changelog=DEFAULT_CHANGELOG)
    db.add(hv)
    db.flush()
    return hv


def current_harness(db: Session) -> HarnessVersion:
    hv = db.query(HarnessVersion).filter(HarnessVersion.is_current.is_(True), HarnessVersion.is_draft.is_(False)).first()
    return hv or ensure_default_harness(db)
