#!/usr/bin/env bash
# One-shot GCP deployment: Cloud Run (web + worker), Cloud SQL Postgres, GCS bucket, Secret Manager.
# Usage: PROJECT=my-proj REGION=us-central1 ./deploy/deploy.sh      (idempotent; re-run after adding secrets)
set -euo pipefail
PROJECT=${PROJECT:?set PROJECT}
REGION=${REGION:-us-central1}
SVC=acr
BUCKET=${BUCKET:-$PROJECT-acr-blobs}
SQL_INSTANCE=${SQL_INSTANCE:-acr-pg}
DB_PASS=${DB_PASS:-$(openssl rand -hex 16)}
IMAGE=$REGION-docker.pkg.dev/$PROJECT/acr/acr:$(date +%Y%m%d%H%M%S)

gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com sqladmin.googleapis.com storage.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudbuild.googleapis.com

# --- artifact registry + image
gcloud artifacts repositories describe acr --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create acr --repository-format=docker --location="$REGION"
gcloud builds submit --tag "$IMAGE" .

# --- storage
gsutil ls -b "gs://$BUCKET" >/dev/null 2>&1 || gsutil mb -l "$REGION" "gs://$BUCKET"

# --- cloud sql (postgres)
if ! gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  gcloud sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --tier=db-g1-small --region="$REGION"
  gcloud sql databases create acr --instance="$SQL_INSTANCE"
  gcloud sql users create acr --instance="$SQL_INSTANCE" --password="$DB_PASS"
  echo "DB password: $DB_PASS  (stored in Secret Manager as acr-db-url)"
fi
CONN=$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')
DB_URL="postgresql+psycopg://acr:$DB_PASS@/acr?host=/cloudsql/$CONN"

# --- secrets (created if missing; model keys are yours to add, see the NOTE lines)
mk_secret() { gcloud secrets describe "$1" >/dev/null 2>&1 || printf '%s' "$2" | gcloud secrets create "$1" --data-file=-; }
mk_secret acr-db-url "$DB_URL"
mk_secret acr-secret-key "$(openssl rand -hex 32)"
mk_secret acr-admin-password "${ADMIN_PASSWORD:-$(openssl rand -hex 8)}"
for k in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY XAI_API_KEY DEEPSEEK_API_KEY; do
  gcloud secrets describe "$k" >/dev/null 2>&1 || echo "NOTE: create secret $k:  printf '%s' \"\$$k\" | gcloud secrets create $k --data-file=-"
done

SA=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor -q >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role=roles/cloudsql.client -q >/dev/null
gsutil iam ch "serviceAccount:$SA:objectAdmin" "gs://$BUCKET"

COMMON_ENV="ACR_ENV=prod,ACR_STORAGE_BACKEND=gcs,ACR_GCS_BUCKET=$BUCKET,ACR_MISSING_KEY_POLICY=fail,ACR_ANCHOR_BACKEND=opentimestamps"
SECRETS="ACR_DATABASE_URL=acr-db-url:latest,ACR_SECRET_KEY=acr-secret-key:latest,ACR_ADMIN_PASSWORD=acr-admin-password:latest"
for k in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY XAI_API_KEY DEEPSEEK_API_KEY; do
  gcloud secrets describe "$k" >/dev/null 2>&1 && SECRETS="$SECRETS,$k=$k:latest"
done

# --- web service (stateless; scales to zero)
gcloud run deploy "$SVC" --image "$IMAGE" --region "$REGION" --allow-unauthenticated \
  --add-cloudsql-instances "$CONN" --set-env-vars "$COMMON_ENV,ACR_EMBEDDED_WORKER=0" --set-secrets "$SECRETS" \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 10 --timeout 300
URL=$(gcloud run services describe "$SVC" --region "$REGION" --format='value(status.url)')
gcloud run services update "$SVC" --region "$REGION" --update-env-vars "ACR_BASE_URL=$URL"

# --- worker service (always on; runs the job loop; reviews can take many minutes)
gcloud run deploy "$SVC-worker" --image "$IMAGE" --region "$REGION" --no-allow-unauthenticated \
  --add-cloudsql-instances "$CONN" --set-env-vars "$COMMON_ENV,ACR_BASE_URL=$URL" --set-secrets "$SECRETS" \
  --command python --args=-m,acr.cli,worker --cpu 1 --memory 1Gi --min-instances 1 --max-instances 1 --no-cpu-throttling --timeout 3600

echo "Deployed: $URL   admin: $URL/admin (password in secret acr-admin-password)"
echo "More review throughput: raise --max-instances on $SVC-worker (jobs are claimed with FOR UPDATE SKIP LOCKED)."
