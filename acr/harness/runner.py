"""The review engine. Each call to advance(run) performs exactly one stage transition and records every
model call as a Transcript stamped with the harness version. Official runs write the outcome onto the
paper; sandbox runs never do."""
from __future__ import annotations

import datetime as dt
import json
import logging

from sqlalchemy.orm import Session

from ..config import get_settings
from ..llm import complete, LLMError
from ..models import Paper, Review, ReviewRun, Transcript, HarnessVersion, Attachment
from ..llm.search import search_complete, NoSearchTool
from ..priorart import (run_dated_api_search, filter_general_items, merge_items, format_prior_art,
                        EXTRACT_SCHEMA)
from ..pricing import cost_usd, projected_call_usd
from ..scoring import derive, mean_axes, AXES
from ..settings_store import get_setting
from ..storage import get_storage
from . import schemas
from .committee import assign_committee
from .defaults import render, rubric_text
from ..models import utcnow

log = logging.getLogger("acr.runner")


class SpendCapExceeded(RuntimeError):
    pass


def estimate_tokens(text: str) -> int:
    return max(len(text.split()), len(text) // 4)


class Engine:
    def __init__(self, db: Session, run: ReviewRun):
        self.db = db
        self.run = run
        self.paper: Paper = db.get(Paper, run.paper_id)
        self.hv: HarnessVersion = db.get(HarnessVersion, run.harness_version_id)
        self.cfg = self.hv.config
        self._seq = db.query(Transcript).filter(Transcript.run_id == run.id).count()
        self._external_text: str | None = None   # arXiv full text fetched at review time, never stored

    # ------------------------------------------------------------------ helpers
    def _check_budget(self, spec: dict, prompt_chars: int, max_tokens: int, searches: int = 0) -> None:
        cap = float(get_setting(self.db, "spend_cap_per_run_usd", 3.0) or 0)
        if cap <= 0:
            return
        projected = projected_call_usd(spec.get("model", ""), prompt_chars, max_tokens, searches)
        if self.run.total_cost_usd + projected > cap:
            raise SpendCapExceeded(f"spend cap ${cap:.2f}/run: spent ${self.run.total_cost_usd:.2f}, next call "
                                   f"({spec.get('model')}) projected ${projected:.2f}")

    def _stored(self, user: str) -> str:
        """What goes into the transcript. arXiv full text is read at review time but never stored."""
        if self._external_text and self._external_text in user:
            return user.replace(self._external_text, f"[full text of {self.paper.external_url} read at review time; not stored by ACR]")
        return user

    def _call(self, stage: str, role: str, spec: dict, system: str, user: str, schema: dict | None,
              max_tokens: int, note: str = "") -> dict | None:
        messages = [{"role": "user", "content": user}]
        stored = [{"role": "user", "content": self._stored(user)}]
        self._check_budget(spec, len(system) + len(user), max_tokens)
        try:
            res = complete(spec, system, messages, schema, max_tokens=max_tokens)
        except LLMError as e:
            self._seq += 1
            self.db.add(Transcript(run_id=self.run.id, seq=self._seq, stage=stage, role=role, lab=spec.get("lab", ""),
                                   model=spec.get("model", ""), provider=spec.get("provider", ""),
                                   harness_version=self.run.harness_version, system_prompt=system,
                                   messages=stored, response_text="", note=f"ERROR: {e}"))
            self.db.commit()
            raise
        cost = 0.0 if res.provider == "mock" else cost_usd(spec.get("model", ""), res.input_tokens, res.output_tokens)
        self._seq += 1
        self.db.add(Transcript(run_id=self.run.id, seq=self._seq, stage=stage, role=role, lab=spec.get("lab", ""),
                               model=spec.get("model", ""), provider=res.provider, harness_version=self.run.harness_version,
                               system_prompt=system, messages=stored, response_text=res.text, response_json=res.json,
                               input_tokens=res.input_tokens, output_tokens=res.output_tokens, latency_ms=res.latency_ms,
                               cost_usd=cost, note=(note + " " + res.note).strip()))
        self.run.total_input_tokens += res.input_tokens
        self.run.total_output_tokens += res.output_tokens
        self.run.total_cost_usd = round(self.run.total_cost_usd + cost, 6)
        self.db.commit()
        if schema and res.json is None:
            raise LLMError(f"{spec.get('model')} did not return valid JSON for {stage}/{role}")
        return res.json

    def _reviews(self) -> list[Review]:
        """Always query: the ORM relationship on run can be stale within a long-lived session."""
        return self.db.query(Review).filter(Review.run_id == self.run.id).order_by(Review.slot).all()

    def _paper_body(self) -> str:
        lim = self.cfg["limits"]["max_paper_chars"]
        body = self.paper.body_md or ""
        if self.paper.source_kind == "arxiv":
            if self._external_text is None:
                from ..ingest_arxiv import fetch_fulltext
                try:
                    self._external_text = fetch_fulltext(self.paper.external_id) or ""
                except Exception as e:
                    log.warning("could not fetch arXiv full text for %s: %s", self.paper.external_id, e)
                    self._external_text = ""
            body = self._external_text or f"(Full text could not be fetched; review from the abstract and {self.paper.external_url}.)"
        if len(body) > lim:
            body = body[:lim] + f"\n\n[... truncated by harness at {lim} characters; full text is on the paper page]"
        return body

    def _attachments_text(self) -> str:
        atts: list[Attachment] = self.paper.attachments
        if not atts:
            return "(none)"
        lim = self.cfg["limits"]["max_attachment_excerpt_chars"]
        lines, budget = [], lim
        st = get_storage()
        for a in atts:
            lines.append(f"- {a.kind}: {a.filename} ({a.size_bytes} bytes, sha256 {a.sha256[:12]}…)")
            if budget > 0 and a.size_bytes < 400_000 and _looks_texty(a.filename):
                try:
                    txt = st.get(a.blob_key).decode("utf-8", "replace")
                    excerpt = txt[:min(budget, 8000)]
                    budget -= len(excerpt)
                    lines.append("  excerpt:\n  " + excerpt.replace("\n", "\n  "))
                except Exception:
                    pass
        return "\n".join(lines)

    def _paper_fields(self) -> dict:
        p = self.paper
        return dict(acr_id=p.acr_id, paper_type=p.paper_type, field=p.field, title=p.title, abstract=p.abstract,
                    priority_date=p.priority_at.isoformat() + "Z")

    def _system(self) -> str:
        return render(self.cfg["prompts"]["reviewer_system"], rubric=rubric_text(self.cfg["rubric"]))

    def _rebuttal_text(self) -> str:
        if self.paper.skip_rebuttal:
            return "(No rebuttal stage: this paper was ingested from arXiv and its authors did not participate.)"
        return self.run.rebuttal_text or "(The author did not submit a rebuttal.)"

    # ------------------------------------------------------------------ stages
    def advance(self) -> str:
        """Perform one transition. Returns the new stage."""
        st = self.run.stage
        try:
            if st == "assigned":
                self.stage_prior_art()
            elif st == "prior_art":
                self.stage_reviews()
            elif st == "reviews":
                self.stage_open_rebuttal()
            elif st == "rebuttal":
                self.stage_discussion()
            elif st == "discussion":
                self.stage_decision()
            elif st == "decision":
                self.stage_finish()
        except Exception as e:
            self.run.error = f"{type(e).__name__}: {e}"
            log.exception("run %s failed at %s", self.run.id, st)
            self.db.commit()
            raise
        self.run.updated_at = utcnow()
        self.run.error = ""   # a completed stage clears any error left by an earlier failed attempt
        self.db.commit()
        return self.run.stage

    def stage_prior_art(self):
        """General search by the committee's own models, plus dated academic APIs, all filtered by the date rule."""
        pa_cfg = self.cfg.get("prior_art", {})
        prompts = self.cfg["prompts"]
        chair = self.run.committee["chair"]
        fields = self._paper_fields()
        excerpt = (self.paper.body_md or "")[:6000]
        cutoff = self.paper.priority_at
        bundle = {"cutoff": cutoff.isoformat(), "queries": [], "sources": [], "items": [], "errors": [],
                  "dropped": [], "searchers": [], "self_excluded": []}
        self_ref = f"arXiv:{self.paper.external_id}, {self.paper.external_url}" if self.paper.external_id else f"ACR {self.paper.acr_id}: '{self.paper.title}'"
        fields = dict(fields, self_ref=self_ref)

        # 1. native web search by each searcher (committee members or chair only)
        searchers = [chair] if pa_cfg.get("searchers", "committee") == "chair" else self.run.committee["reviewers"] + [chair]
        general = []
        search_prompt = prompts.get("prior_art_search")
        extract_prompt = prompts.get("prior_art_extract")
        if search_prompt and extract_prompt:
            for spec in searchers:
                role = f"searcher-{spec['lab']}"
                if any(t.stage == "prior_art" and t.role == role and t.response_json for t in self._transcripts()):
                    continue   # retry-safe
                try:
                    self._check_budget(spec, len(search_prompt) + len(excerpt), self.cfg["limits"].get("max_tokens_search", 12000),
                                       searches=int(pa_cfg.get("max_searches_per_member", 8)))
                    rep = search_complete(spec, "You run prior-art searches for a peer-review committee.",
                                          render(search_prompt, excerpt=excerpt, **fields),
                                          max_tokens=self.cfg["limits"].get("max_tokens_search", 12000),
                                          max_searches=int(pa_cfg.get("max_searches_per_member", 8)),
                                          cutoff_date=cutoff.date().isoformat())
                except NoSearchTool as e:
                    bundle["errors"].append({"source": spec["lab"], "query": "native search", "error": str(e)})
                    continue
                except LLMError as e:
                    bundle["errors"].append({"source": spec["lab"], "query": "native search", "error": str(e)[:200]})
                    self._record(stage="prior_art", role=role, spec=spec, system="", user=search_prompt[:200],
                                 text="", note=f"ERROR: {e}")
                    continue
                self._record(stage="prior_art", role=role + "-report", spec=spec,
                             system="You run prior-art searches for a peer-review committee.",
                             user=render(search_prompt, excerpt=excerpt, **fields), text=rep.text,
                             json_out={"citations": rep.raw.get("citations", [])}, res=rep, note=rep.note)
                out = self._call("prior_art", role, spec, "You convert search reports into structured records.",
                                 render(extract_prompt, report=rep.text), EXTRACT_SCHEMA,
                                 self.cfg["limits"].get("max_tokens_search", 12000))
                kept, dropped = filter_general_items(out.get("items", []), cutoff, spec["lab"],
                                                     verify=bool(pa_cfg.get("verify_dates", True)))
                general.extend(kept)
                bundle["dropped"].extend(dropped)
                bundle["searchers"].append({"lab": spec["lab"], "model": spec["model"], "kept": len(kept), "dropped": len(dropped)})

        # 2. dated academic APIs from chair-written queries
        api_items = []
        if pa_cfg.get("dated_apis", True):
            out = self._call("prior_art", "chair", chair, "You generate literature search queries.",
                             render(prompts["query_gen"], excerpt=excerpt, **fields),
                             schemas.QUERY_SCHEMA, self.cfg["limits"]["max_tokens_query"])
            queries = [q for q in out.get("queries", []) if isinstance(q, str) and q.strip()][:8] or [self.paper.title]
            api_items, errors = run_dated_api_search(self.db, queries, cutoff, exclude_paper_id=self.paper.id)
            bundle["queries"] = queries
            bundle["query_rationale"] = out.get("rationale", "")
            bundle["errors"].extend(errors)
            bundle["sources"] = [x.strip() for x in get_settings().priorart_sources.split(",") if x.strip()]

        merged = merge_items(general, api_items, limit=int(pa_cfg.get("max_items_shown", 40)) + 5)
        kept = []
        for it in merged:   # the submission itself never counts as its own prior art
            if self._is_self(it):
                bundle["self_excluded"].append({"title": it["title"], "url": it.get("url", "")})
            else:
                kept.append(it)
        for i, it in enumerate(kept[: int(pa_cfg.get("max_items_shown", 40))], 1):
            it["ref"] = f"P{i}"
        bundle["items"] = kept[: int(pa_cfg.get("max_items_shown", 40))]
        self.run.prior_art = bundle
        self.run.stage = "prior_art"

    def _is_self(self, item: dict) -> bool:
        url = (item.get("url") or "").lower()
        title = " ".join((item.get("title") or "").lower().split())
        mine = " ".join(self.paper.title.lower().split())
        if self.paper.external_id and self.paper.external_id.lower() in url:
            return True
        if item.get("acr_id") == self.paper.acr_id:
            return True
        return bool(title) and (title == mine or (min(len(title), len(mine)) >= 30 and (title in mine or mine in title)))

    def _transcripts(self) -> list[Transcript]:
        return self.db.query(Transcript).filter(Transcript.run_id == self.run.id).order_by(Transcript.seq).all()

    def _record(self, stage: str, role: str, spec: dict, system: str, user: str, text: str, json_out=None,
                res=None, note: str = "") -> None:
        """Store a non-schema model call (search reports) as a transcript."""
        searches = len((res.raw or {}).get("citations", [])) if res else 0
        cost = 0.0 if (not res or res.provider == "mock") else cost_usd(spec.get("model", ""), res.input_tokens, res.output_tokens, min(searches, 8))
        self._seq += 1
        self.db.add(Transcript(run_id=self.run.id, seq=self._seq, stage=stage, role=role, lab=spec.get("lab", ""),
                               model=spec.get("model", ""), provider=(res.provider if res else spec.get("provider", "")),
                               harness_version=self.run.harness_version, system_prompt=system,
                               messages=[{"role": "user", "content": self._stored(user)}], response_text=text, response_json=json_out,
                               input_tokens=res.input_tokens if res else 0, output_tokens=res.output_tokens if res else 0,
                               latency_ms=res.latency_ms if res else 0, cost_usd=cost, note=note))
        if res:
            self.run.total_input_tokens += res.input_tokens
            self.run.total_output_tokens += res.output_tokens
            self.run.total_cost_usd = round(self.run.total_cost_usd + cost, 6)
        self.db.commit()

    def stage_reviews(self):
        fields = self._paper_fields()
        user = render(self.cfg["prompts"]["review_task"], body=self._paper_body(), attachments=self._attachments_text(),
                      prior_art=format_prior_art(self.run.prior_art), **fields)
        existing = {r.slot for r in self._reviews()}
        for spec in self.run.committee["reviewers"]:
            if spec["slot"] in existing:
                continue   # retry-safe
            out = self._call("reviews", f"reviewer-{spec['slot']}", spec, self._system(), user,
                             schemas.REVIEW_SCHEMA, self.cfg["limits"]["max_tokens_review"])
            out["scores"] = derive(out.get("scores", {}))
            self.db.add(Review(run_id=self.run.id, slot=spec["slot"], lab=spec["lab"], model=spec["model"],
                               initial=out, final={}))
            self.db.flush()
        self.run.stage = "reviews"

    def stage_open_rebuttal(self):
        now = utcnow()
        hours = float(self.cfg["rebuttal"].get("hours", 24))
        if self.run.sandbox or self.paper.skip_rebuttal:
            hours = 0.0 if self.paper.skip_rebuttal else get_settings().sandbox_rebuttal_hours
        self.run.rebuttal_opens_at = now
        self.run.rebuttal_deadline = now + dt.timedelta(hours=hours)
        self.run.stage = "rebuttal"
        if not self.run.sandbox and self.paper.official_run_id == self.run.id and not self.paper.skip_rebuttal:
            self.paper.status = "rebuttal"

    def rebuttal_open(self) -> bool:
        return (self.run.stage == "rebuttal" and self.run.rebuttal_submitted_at is None
                and self.run.rebuttal_deadline and utcnow() < self.run.rebuttal_deadline)

    def submit_rebuttal(self, text: str) -> None:
        cap = int(self.cfg["rebuttal"].get("token_cap", 1500))
        n = estimate_tokens(text)
        if n > cap:
            raise ValueError(f"Rebuttal is ~{n} tokens; cap is {cap} (estimated as max(words, chars/4)).")
        self.run.rebuttal_text = text
        self.run.rebuttal_tokens = n
        self.run.rebuttal_submitted_at = utcnow()
        self._seq += 1
        self.db.add(Transcript(run_id=self.run.id, seq=self._seq, stage="rebuttal", role="author",
                               harness_version=self.run.harness_version, messages=[{"role": "user", "content": text}],
                               response_text=text, note=f"author rebuttal, ~{n} tokens"))
        self.db.flush()

    def stage_discussion(self):
        chair = self.run.committee["chair"]
        reviews = self._reviews()
        reviews_txt = "\n\n".join(f"### Reviewer {r.slot} ({r.lab} / {r.model})\n{json.dumps(r.initial, indent=1)}"
                                  for r in reviews)
        prior = next((t for t in reversed(self._transcripts()) if t.stage == "discussion" and t.role == "chair"
                      and t.response_json and "superseded" not in (t.note or "")), None)
        if prior is not None:
            summary = prior.response_json   # retry-safe: a completed chair summary is reused, never re-bought
        else:
            summary = self._call("discussion", "chair", chair, "You chair an ACR review committee.",
                                 render(self.cfg["prompts"]["chair_summary"], reviews=reviews_txt,
                                        rebuttal=self._rebuttal_text(), **self._paper_fields()),
                                 schemas.CHAIR_SUMMARY_SCHEMA, self.cfg["limits"]["max_tokens_chair"])
        summary_txt = json.dumps(summary, indent=1)
        rounds = int(self.cfg["discussion"].get("rounds", 1))
        for rnd in range(rounds):
            for r in reviews:
                if r.final and r.final.get("round", -1) >= rnd:
                    continue   # retry-safe
                spec = next(s for s in self.run.committee["reviewers"] if s["slot"] == r.slot)
                others = "\n\n".join(f"### Reviewer {o.slot} ({o.lab})\n{json.dumps(o.initial, indent=1)}"
                                     for o in reviews if o.slot != r.slot)
                out = self._call("discussion", f"reviewer-{r.slot}", spec, self._system(),
                                 render(self.cfg["prompts"]["discussion_turn"], slot=r.slot,
                                        own_review=json.dumps(r.initial, indent=1), other_reviews=others,
                                        rebuttal=self._rebuttal_text(), chair_summary=summary_txt,
                                        **self._paper_fields()),
                                 schemas.DISCUSSION_SCHEMA, self.cfg["limits"]["max_tokens_review"])
                out["scores"] = derive(out.get("scores", {}))
                out["round"] = rnd
                r.final = out
                self.db.flush()
        self.run.stage = "discussion"
        if not self.run.sandbox and self.paper.official_run_id == self.run.id:
            self.paper.status = "discussion"

    def stage_decision(self):
        chair = self.run.committee["chair"]
        reviews = self._reviews()
        finals = "\n\n".join(f"### Reviewer {r.slot} ({r.lab} / {r.model})\nInitial scores: {r.initial.get('scores')}\n"
                             f"Final: {json.dumps(r.final or r.initial, indent=1)}" for r in reviews)
        summary_row = (self.db.query(Transcript).filter(Transcript.run_id == self.run.id, Transcript.stage == "discussion",
                                                        Transcript.role == "chair").order_by(Transcript.seq.desc()).first())
        chair_summary = summary_row.response_text if summary_row else ""
        out = self._call("decision", "chair", chair, "You chair an ACR review committee.",
                         render(self.cfg["prompts"]["chair_decision"], final_reviews=finals,
                                rebuttal=self._rebuttal_text(), chair_summary=chair_summary, **self._paper_fields()),
                         schemas.DECISION_SCHEMA, self.cfg["limits"]["max_tokens_chair"])
        axes = out.get("final_scores") or mean_axes([r.scores for r in reviews])
        for a in AXES:   # guard against missing/garbage axes: fall back to reviewer mean
            if not isinstance(axes.get(a), (int, float)):
                axes[a] = mean_axes([r.scores for r in reviews])[a]
        self.run.final_scores = derive(axes)
        self.run.decision = out.get("decision") if out.get("decision") in ("accept", "reject") else "reject"
        self.run.meta_review = out.get("meta_review", "")
        self.run.stage = "decision"

    def stage_finish(self):
        self.run.stage = "done"
        self.run.finished_at = utcnow()
        if self.run.sandbox or self.paper.official_run_id != self.run.id:
            return
        p, fs = self.paper, self.run.final_scores
        p.decision = self.run.decision
        p.decided_at = self.run.finished_at
        p.harness_version = self.run.harness_version
        p.score_originality, p.score_depth = fs["originality"], fs["depth"]
        p.score_potential_impact, p.score_implementation = fs["potential_impact"], fs["implementation"]
        p.score_novelty, p.score_impact, p.score_creativity = fs["novelty"], fs["impact"], fs["creativity"]
        # Accepted papers wait for the embargo and then publish; rejected papers never enter the public record.
        p.status = "decided" if (p.decision == "accept" or p.source_kind == "arxiv") else "rejected"


def _looks_texty(name: str) -> bool:
    return name.lower().rsplit(".", 1)[-1] in {"py", "md", "txt", "csv", "json", "yaml", "yml", "toml", "js", "ts",
                                               "rs", "go", "c", "cpp", "h", "java", "sh", "ipynb", "tex", "r", "jl"}


def create_run(db: Session, paper: Paper, hv: HarnessVersion, sandbox: bool) -> ReviewRun:
    author = paper.author
    committee = assign_committee(hv.config, author.lab, rotation_index=paper.id)
    run = ReviewRun(paper_id=paper.id, harness_version_id=hv.id, harness_version=hv.version, sandbox=sandbox,
                    stage="assigned", committee=committee)
    db.add(run)
    db.flush()
    if not sandbox:
        paper.official_run_id = run.id
        paper.status = "in_review"
    return run
