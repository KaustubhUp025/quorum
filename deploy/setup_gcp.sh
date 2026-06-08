#!/usr/bin/env bash
# One-time GCP setup for Quorum.
# Reads secrets from the local .env file and stores them in Secret Manager.
# Run this ONCE before the first Cloud Run or Agent Engine deployment.
#
# Usage: ./deploy/setup_gcp.sh <GCP_PROJECT_ID> [REGION]
#   e.g. ./deploy/setup_gcp.sh gen-lang-client-0294573094
#
# Prerequisites:
#   gcloud auth login
#   gcloud auth application-default login

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <GCP_PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: .env not found at ${ENV_FILE}"
  echo "Copy .env.example to .env and fill in your credentials."
  exit 1
fi

# Source .env quietly (values stay in shell env vars, never printed or written to files)
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

echo "==> Configuring project: ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" --quiet

echo "==> Enabling required APIs (this may take 1-2 minutes)..."
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  --project "${PROJECT_ID}" \
  --quiet

echo "==> Creating/updating Secret Manager secrets..."

_upsert_secret() {
  local name="$1"
  local value="$2"
  if [[ -z "${value}" ]]; then
    echo "    SKIP ${name} (not set in .env)"
    return
  fi
  if gcloud secrets describe "${name}" --project "${PROJECT_ID}" &>/dev/null; then
    printf '%s' "${value}" \
      | gcloud secrets versions add "${name}" --data-file=- --project "${PROJECT_ID}" --quiet
    echo "    UPDATED ${name}"
  else
    printf '%s' "${value}" \
      | gcloud secrets create "${name}" \
          --data-file=- \
          --replication-policy="automatic" \
          --project "${PROJECT_ID}" --quiet
    echo "    CREATED ${name}"
  fi
}

_upsert_secret "quorum-gitlab-token"  "${QUORUM_GITLAB_TOKEN:-}"
_upsert_secret "quorum-gemini-key"    "${QUORUM_GEMINI_API_KEY:-}"
_upsert_secret "quorum-github-token"  "${QUORUM_GITHUB_TOKEN:-}"

# Generate webhook secret if not already in .env
if [[ -z "${QUORUM_WEBHOOK_SECRET:-}" ]]; then
  WEBHOOK_SECRET="$(openssl rand -hex 32)"
  echo ""
  echo "    GENERATED quorum-webhook-secret (not in .env — save this value):"
  echo "      QUORUM_WEBHOOK_SECRET=${WEBHOOK_SECRET}"
  echo ""
else
  WEBHOOK_SECRET="${QUORUM_WEBHOOK_SECRET}"
fi
_upsert_secret "quorum-webhook-secret" "${WEBHOOK_SECRET}"

echo ""
echo "==> Resolving Cloud Run service account..."
# Cloud Run uses the Compute Engine default service account:
#   {PROJECT_NUMBER}-compute@developer.gserviceaccount.com
# NOT the App Engine SA (project-id@appspot.gserviceaccount.com).
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" \
  --format="value(projectNumber)" --quiet)"
CLOUD_RUN_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "    Service account: ${CLOUD_RUN_SA}"

echo "==> Granting Cloud Run SA access to all four secrets..."
for SECRET in quorum-gitlab-token quorum-gemini-key quorum-github-token quorum-webhook-secret; do
  gcloud secrets add-iam-policy-binding "${SECRET}" \
    --member "serviceAccount:${CLOUD_RUN_SA}" \
    --role "roles/secretmanager.secretAccessor" \
    --project "${PROJECT_ID}" \
    --quiet
  echo "    OK ${SECRET}"
done

echo "==> Granting Cloud Run SA permission to call Vertex AI..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${CLOUD_RUN_SA}" \
  --role "roles/aiplatform.user" \
  --quiet
echo "    OK roles/aiplatform.user"

echo ""
echo "✅ GCP setup complete for project: ${PROJECT_ID}"
echo ""
echo "Next steps:"
echo "  1. Deploy Cloud Run webhook:"
echo "     cd ${SCRIPT_DIR}/.."
echo "     ./deploy/cloud_run.sh ${PROJECT_ID} ${REGION}"
echo ""
echo "  2. Deploy Agent Engine:"
echo "     pip install build"
echo "     python deploy/agent_engine.py --project ${PROJECT_ID} --region ${REGION} --build"
echo ""
echo "  3. Configure GitLab webhook on quorum-demo:"
echo "     URL:    <Cloud Run URL>/webhook/gitlab"
echo "     Events: Merge request events"
echo "     Token:  ${WEBHOOK_SECRET:0:8}...  (first 8 chars shown)"
