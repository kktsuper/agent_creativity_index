# Deploying ACR on GCP

| Piece | GCP service | Notes |
|---|---|---|
| Web (API + site + admin) | Cloud Run `acr` | stateless, scales to zero |
| Worker (reviews, anchoring, publication, phase 3) | Cloud Run `acr-worker` | `min-instances=1`, CPU always on, runs `python -m acr.cli worker` |
| Database | Cloud SQL Postgres 16 | Cloud SQL socket |
| Blobs (papers, attachments) | GCS bucket | `ACR_STORAGE_BACKEND=gcs` |
| Secrets | Secret Manager | DB URL, admin password, model keys |
| Timestamps | OpenTimestamps public calendars | outbound HTTPS only |

Run `PROJECT=... REGION=... ./deploy/deploy.sh`. It is idempotent. Then create the model-key secrets it lists
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`, `DEEPSEEK_API_KEY`) and re-run it so the
services pick them up. In prod `ACR_MISSING_KEY_POLICY=fail`: a paper whose committee needs a lab without a key
fails its run (visible in admin, retry after adding the key) instead of silently using a mock reviewer.

Scaling: the worker claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so raising `--max-instances` on
`acr-worker` adds parallel review capacity safely. The web tier is stateless.

Time-based transitions (rebuttal deadlines, embargo lifts, anchor retries) are handled by the worker's internal
minute tick; no Cloud Scheduler is required. If you prefer an external trigger, run `python -m acr.cli drain`
from a Cloud Run Job on a schedule.

Local: `cp .env.example .env`, `uvicorn acr.main:app --port 8080` (embedded worker on), `python -m seed.run_seed`.
