# Changelog

A running record of every change made since the project changed hands. Newest entries at the top of each
section. Keep it short: what changed, why, and where to look.

## Planned

Agreed changes that have not been built yet.

- **Auto-generated topic tags on papers.** The review model emits a few free-form tags per paper
  (e.g. "LLMs", "reinforcement learning", "world models"), stored on the paper, shown as clickable chips on the
  site, filterable in the Papers listing and the public API, backfilled for already-reviewed papers. Plain
  tagging with light normalization (lowercase, reuse near-identical spellings), explicitly not a curated
  category taxonomy. Suggested by a colleague; scope clarified by Jad Tarifi. Step 1 (generate and store) is
  done, see below; still to do: show on the site, filter in Papers and the API, backfill.

Under consideration, not yet committed to: deeper prior-art search (read closest items in full, chase their
citations, add OpenAlex / patents / code search), citation reference checking, a prompt-injection test of
the review committee.

## Unreleased

Changes on the working branch that are not yet merged.

### 2026-09-30

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
