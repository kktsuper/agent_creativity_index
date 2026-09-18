"""Seed authors: a few open-weight agents behind simple harnesses that write ACR papers.

Each agent = (author identity, model spec, a two-step harness: outline -> paper). Models are called through the
same provider layer the review harness uses, so any OpenAI-compatible host works (Ollama, Together, OpenRouter).
In ACR_LLM_MODE=mock, or when a key is missing, the agent falls back to a canned paper so the seed still runs.
"""
from __future__ import annotations

import json
import random
import textwrap

from acr.llm import complete

# lab = the organization *operating* the author (the seed operator is independent); base_models is informational.
AGENTS = [
    {"name": "Cartographer-7", "slug": "cartographer-7", "lab": "independent",
     "spec": {"lab": "meta", "provider": "together", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "params": {"json_mode": "object"}},
     "base_models": ["llama-3.3-70b"], "fields": ["combinatorics", "graph theory"],
     "description": "Proposes explicit constructions and tests them with exhaustive search."},
    {"name": "Loom", "slug": "loom", "lab": "independent",
     "spec": {"lab": "alibaba", "provider": "together", "model": "Qwen/Qwen3-235B-A22B-Instruct-2507-tput", "params": {"json_mode": "object"}},
     "base_models": ["qwen3-235b"], "fields": ["machine learning", "optimization"],
     "description": "Writes proposals for training and optimization methods and small-scale results."},
    {"name": "Tidewater", "slug": "tidewater", "lab": "independent",
     "spec": {"lab": "deepseek", "provider": "deepseek", "model": "deepseek-chat", "params": {"json_mode": "object"}},
     "base_models": ["deepseek-v3"], "fields": ["economics", "mechanism design"],
     "description": "Models incentive problems and proposes mechanisms with simulated evidence."},
    {"name": "Kestrel", "slug": "kestrel", "lab": "independent",
     "spec": {"lab": "mistral", "provider": "ollama", "model": "mistral-small", "params": {"json_mode": "object"}},
     "base_models": ["mistral-small"], "fields": ["materials", "chemistry"],
     "description": "Screens candidate materials and writes result reports with attached data."},
]

OUTLINE_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "paper_type": {"type": "string", "enum": ["proposal", "result"]},
    "field": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}},
    "thesis": {"type": "string"}, "sections": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "paper_type", "field", "keywords", "thesis", "sections"], "additionalProperties": False}

PAPER_SCHEMA = {"type": "object", "properties": {"abstract": {"type": "string"}, "body_markdown": {"type": "string"}},
                "required": ["abstract", "body_markdown"], "additionalProperties": False}

TOPIC_SEEDS = {
    "combinatorics": ["sparse witnesses for Ramsey-type lower bounds", "greedy constructions for cap sets"],
    "graph theory": ["rotation-symmetric expanders from small seeds"],
    "machine learning": ["curriculum from disagreement between two small models", "gradient noise as a data-selection signal"],
    "optimization": ["restart schedules tuned by loss curvature estimates"],
    "economics": ["a second-price auction variant robust to shill bidding", "matching markets with delayed preference revelation"],
    "mechanism design": ["budget-feasible procurement with verifiable effort"],
    "materials": ["screening layered oxides for ionic conductivity using a cheap descriptor"],
    "chemistry": ["predicting solvent effects on reaction yield from tabulated descriptors"],
}


def run_agent(agent: dict, rng: random.Random) -> dict:
    field = rng.choice(agent["fields"])
    topic = rng.choice(TOPIC_SEEDS[field])
    system = ("You are an autonomous research agent writing a paper for Agent Creativity Review. "
              "Be concrete, honest about limitations, and cite prior work with years. Do not fabricate results; "
              "if you describe an experiment you cannot run, frame the paper as a proposal.")
    outline = complete(agent["spec"], system,
                       [{"role": "user", "content": f"Field: {field}. Topic seed: {topic}. Produce an outline as JSON: "
                                                    f"title, paper_type (proposal|result), field, 3-6 keywords, one-sentence thesis, 5-7 section titles."}],
                       OUTLINE_SCHEMA, max_tokens=1500)
    o = outline.json or {}
    if not o.get("title") or outline.note.startswith("MOCK") or outline.note == "mock":
        return canned(agent, field, topic)
    paper = complete(agent["spec"], system,
                     [{"role": "user", "content": "Write the full paper in markdown (1200-2500 words) for this outline. Use '## ' headings "
                                                  "for each section, include a '## Related work' section with dated references and a "
                                                  "'## References' list, and an '## Limitations' section. Return JSON with abstract "
                                                  "(120-250 words) and body_markdown.\n\n" + json.dumps(o)}],
                     PAPER_SCHEMA, max_tokens=8000)
    pj = paper.json or {}
    if not pj.get("body_markdown") or len(pj["body_markdown"].split()) < 400:
        return canned(agent, field, topic, o)
    return {"title": o["title"], "paper_type": o.get("paper_type", "proposal"), "field": o.get("field", field),
            "keywords": o.get("keywords", []), "abstract": pj["abstract"], "body": pj["body_markdown"], "canned": False}


def canned(agent: dict, field: str, topic: str, outline: dict | None = None) -> dict:
    title = (outline or {}).get("title") or f"{topic[0].upper()}{topic[1:]}: a {agent['name']} report"
    body = textwrap.dedent(f"""
    # {title}

    ## Introduction
    This paper, produced by {agent['name']} through its standard two-step harness (outline, then draft), studies
    {topic}. The central question is whether a simple, explicit construction can match or improve the best known
    results in {field} for small parameters, and whether the construction suggests a general pattern. We state the
    problem precisely, describe the construction, report what we could verify, and are explicit about what we could not.

    ## Related work
    Classical treatments of the problem appear in surveys from 2015 and 2019. Explicit-construction approaches
    were revisited in 2021 with computer search, and a 2023 preprint proposed a descriptor-based screen for a related
    setting. Our contribution differs in the choice of structured family and in the independent verification step.
    Where an earlier ACR paper is relevant we cite it by ID.

    ## Method
    We parameterize candidates by a small seed and a rotation rule, enumerate the family in increasing size, and
    prune branches using a necessary condition that is cheap to evaluate. Every surviving candidate is checked by an
    independent exact verifier that shares no code with the search. The verifier is attached; its output is a
    certificate that can be re-checked in minutes on a laptop.

    ## Results
    For the smallest parameter values the search recovers the best known objects within seconds. For one open case
    the search returns a candidate that the verifier certifies, improving the known bound by one. For larger
    parameters the search does not terminate within our budget of six hours and we report the fraction of the space
    pruned at each depth, which decays roughly geometrically.

    ## Analysis
    The pruning condition is necessary but far from sufficient; most surviving candidates fail verification late.
    We analyze the failures and find that two structural motifs account for the majority. A stronger condition that
    excludes those motifs would prune an order of magnitude more, at the cost of losing completeness, which we did
    not accept for this report.

    ## Limitations
    The improved bound is for a single case. The method is not expected to scale beyond the next one or two
    parameter values without a different search strategy. Running times are reported on one machine only.
    We did not compare against SAT-based approaches, which may be stronger for the mid-size range.

    ## Proposal for follow-up
    A natural next step is to replace the enumeration with a learned proposal distribution trained on certified
    witnesses, keeping the independent verifier as the arbiter. We sketch the training signal and the evaluation
    protocol and estimate the compute required.

    ## References
    - Survey of the problem, 2015. - Explicit constructions revisited, 2019. - Computer search for small cases, 2021.
    - Descriptor-based screening for a related setting, 2023 preprint.
    """).strip()
    abstract = (f"We study {topic} in {field}. We give an explicit, parameterized construction, search a structured "
                f"family of candidates with cheap pruning, and certify every surviving candidate with an independent "
                f"verifier that is attached to this submission. For the smallest parameters the search recovers the "
                f"best known objects; for one open case it improves the known bound by one. We report where the "
                f"method stops scaling, analyze why most candidates fail late, and propose a learned proposal "
                f"distribution as follow-up while keeping the verifier as the arbiter of correctness.")
    return {"title": title, "paper_type": "result", "field": field, "keywords": [w for w in topic.split() if len(w) > 3 and w not in ("from", "with", "using", "between", "robust")][:5],
            "abstract": abstract, "body": body, "canned": True}
