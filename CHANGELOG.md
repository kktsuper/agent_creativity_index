# Changelog

A running record of every change made since the project changed hands. Newest entries at the top of each
section. Keep it short: what changed, why, and where to look.

## Planned

Agreed changes that have not been built yet.

(Nothing agreed is waiting to be built. Topic tags, the last planned item, are done; see Unreleased.)

Under consideration, not yet committed to: deeper prior-art search (read closest items in full, chase their
citations, add OpenAlex / patents / code search), citation reference checking, a prompt-injection test of
the review committee.

## Unreleased

Changes on the working branch that are not yet merged.

### 2026-09-30

- **All site colors from the SuperFoundry design system, with one exception.** Menu text (sf-white, azure-200,
  azure-300), page background (`--surface-app`), text (`--text-primary`, `--text-secondary`), borders
  (`paper-500`, `--border-default`), cards (`--surface-card`), the accepted / rejected badges
  (`--text-success`, `--text-error` on their muted fills) and the reviewer outlines (`char-300`) now use design
  system tokens. Exception: small grey text keeps the site's own `#6d6a62`, because the system's lightest text
  tone (`--text-tertiary`, `#7a7168`) is 4.17:1 on its page background, below the 4.5:1 AA minimum. The two
  faint card shadows are also kept, since the system's shadows are much heavier. Subtle visual change: a
  slightly warmer background and off-white cards.
- **Site blues now come from the SuperFoundry design system.** The system's tokens file is copied unchanged into
  `acr/web/static/sf/tokens.css` (provenance in the README next to it) and loaded before `style.css`, and
  every blue on the site now uses its azure scale: links, buttons and the chart use `azure-550` (the system's
  blue-on-light text role), hover `azure-600`, light backgrounds `azure-100`, the header bar `azure-900`. Nothing
  else changes: layout, neutrals and type stay as they were, and the system's ember orange is not used. All
  new pairings pass WCAG AA contrast. The stylesheet cache-busting now covers the tokens file too.
- **Anthropic judge on Claude Opus 5.5 (harness 1.2.1).** The Anthropic committee model moves from
  `claude-opus-5` to `claude-opus-5-5`, about 20% cheaper per token ($4 / $20 per million input / output tokens,
  against $5 / $25), with effort kept at `high` explicitly (Opus 5.5 would otherwise default to `medium`).
  Published as harness 1.2.1 rather than by editing 1.2.0, so a published version never changes; papers already
  reviewed keep 1.2.0 and their scores. At startup an install still on an older unmodified built-in harness
  moves to 1.2.1; a version published from admin or by the self-improvement loop is never replaced. Opus 5.5
  added to the price table (`acr/pricing.py`) so spend caps count it at its real price. Not yet run live.
- **Score formula drawn as a tree.** The "Score" box on the home page (and the formula on "How review works") is
  now a small hierarchy: creativity on top, novelty and impact beneath it, and the four 0–10 axes the committee
  scores as pills. Also corrects the home page formula, which omitted the ÷ 100. Shared partial
  `acr/web/templates/_score_tree.html`.
- **Topic tags, steps 3 and 4 of 4: public API and backfill. The feature is complete.** Paper JSON in the
  API now carries `topic_tags`, and `/api/v1/papers?tag=llms` filters by exact tag, like the site. New command
  `python -m acr.cli backfill-tags [--dry-run] [--limit N]` tags accepted and arXiv papers whose finished
  official run has none: one small call to the paper's own chair (title, abstract, meta-review), recorded as a
  `tags` transcript on that run and counted against the per-run and daily spend caps; rejected papers are
  skipped; safe to re-run. Checked against a database built by the pre-tags code. Originally suggested by a
  colleague, with the scope (simple tagging, not a category system) set by Jad Tarifi.
- **Topic tags, step 2 of 4: shown on the site.** Paper pages show the tags as clickable labels above the
  keywords; a label opens the Papers listing filtered to that tag (`/papers?tag=llm`), which shows the active
  tag with a "clear tag" link and keeps it across the other filters and pages. Matching is exact (so "llm"
  does not match "llms") and works on both SQLite and Postgres (`has_tag` in `acr/tags.py`). The admin run page
  shows the run's normalized tags. Papers reviewed before step 1 show no labels until the backfill.
- **Topic tags: spelling reuse fixed for overlapping reviews.** Step 1 took existing spellings only from tags
  already copied onto papers, which happens when a review finishes, so two reviews in flight at once could store
  "llm" and "llms". Spellings now come from every official run, oldest first; sandbox runs are ignored.
- **Tests no longer inherit a registration token from a local `.env`.** With `ACR_REGISTRATION_TOKEN` set in
  a developer's `.env` or shell, five end-to-end tests failed at author registration (403). `tests/conftest.py`
  now forces it empty, like the other settings it pins.
- **Topic tags, step 1 of 4: the chair writes them and they are stored.** The chair's decision output now
  includes 2 to 5 free-form topic tags (`topic_tags` in `DECISION_SCHEMA`; the instruction is in the field's
  description, so no harness version change). They are normalized in `acr/tags.py` (lowercase, tidy
  punctuation, reuse an existing tag's spelling when a new one differs only by case, spacing, hyphens or a
  plural "s") and stored on the run and, for the official run, on the paper (`topic_tags`, separate from the
  author's `keywords`). New columns are added automatically at startup. Not yet shown on the site or the API;
  existing papers have no tags until the backfill. The mock committee returns tags, so the pipeline test covers
  it. Tests in `tests/test_tags.py` and the end-to-end test.
- **Radar chart: reviewer outlines anonymous again.** Reverted the per-reviewer colors and "Reviewer N · lab"
  legend entries. All reviewer outlines are gray, with a single legend entry "individual reviewers"; hover titles
  no longer name the lab. The review sections below the chart are unchanged.
- **Radar chart of the four score axes on every public paper page.** The score box now shows an inline SVG
  radar: the chair's final scores as the filled shape, each reviewer's final scores as thin gray outlines so
  disagreement is visible at a glance, values under each axis label, hover titles, a legend, and an
  accessible text description. Server-rendered, no JavaScript. Geometry in `acr/web/radar.py`, markup in
  `acr/web/templates/_radar.html`, styles in `acr/web/static/style.css`; tests in `tests/test_radar.py` and
  the end-to-end pipeline test.
- **Stylesheet cache-busting and chart fallback colors.** The stylesheet link now carries the file's
  modification time (`/static/style.css?v=...`) so browsers refetch it after any CSS change; first seen as the
  radar rendering as a black disc on a machine that had cached the old stylesheet. The radar SVG also carries
  its colors as attributes, so it stays legible even with stale or missing CSS.
- Added this changelog.

## Baseline at handover

Recorded 2026-09-22 so later entries have something to compare against.

- Repository had a single commit (`603d642`) and no pull requests. Never deployed; the GCP script in
  `deploy/` had never been run. Live model providers other than Anthropic had never been exercised.
- All 22 tests passed on a fresh Python 3.12 virtual environment.
- Full pipeline verified locally in mock mode: eight seed papers submitted through the API, reviewed by the
  fake committee, rebutted, discussed, decided (six accepted and published, two rejected), and shown on the
  Index.
- Documentation drift noted, not yet fixed: README says 14 tests (there are 22); the scout feature
  (`acr/scout.py`, `scout-arxiv` command, `/scout` pages, `ScoutRun` table) is missing from the README
  layout table; spend cap defaults disagree between `acr/config.py` ($5 / $50), `DECISIONS.md` ($3 / $30)
  and the admin form fallbacks ($3 / $30); `.env.example` sets the missing-key policy to `mock` while the
  code default is `fail`; the `rejected` status comment in `acr/models.py` still says rejected papers are
  public.
- Known functional gaps noted, not yet addressed: no revision concept (a rejected paper resubmits as a new
  paper); sandbox reviews reachable only from admin, not the API; no webhook or callback for authors; no
  job upgrades pending OpenTimestamps proofs to confirmed attestations; pairwise comparison and
  self-improvement calls are not metered against the spend caps; no database migration tool.
