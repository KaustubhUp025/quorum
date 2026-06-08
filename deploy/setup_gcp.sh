#!/usr/bin/env bash
# One-time GCP setup for Quorum.
# Reads secrets from the local .env file and stores them in Secret Manager.
# Run this ONCE before the first Cloud Run or Agent Engine deployment.
#
# Usage: ./deploy/setup_gcp.sh <GCP_PROJECT_ID> [REGION]
#
# Prerequisites:
#   gcloud auth login
#   gcloud auth application-default login

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <GCP_PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"

# Load .env — only extract the three secret values (never echo them)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../.env"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: .env not found at ${ENV_FILE}"
  echo "Copy .env.example to .env and fill in your credentials."
  exit 1
fi

# Source .env quietly (values stay in env vars, never printed)
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

echo "==> Configuring project: ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" --quiet

echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  containerregistry.googleapis.com \
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
      | gcloud secrets create "${name}" --data-file=- --project "${PROJECT_ID}" --quiet
    echo "    CREATED ${name}"
  fi
}

_upsert_secret "quorum-gitlab-token"  "${QUORUM_GITLAB_TOKEN:-}"
_upsert_secret "quorum-gemini-key"    "${QUORUM_GEMINI_API_KEY:-}"
_upsert_secret "quorum-github-token"  "${QUORUM_GITHUB_TOKEN:-}"

# Optional webhook secret — generate one if not set
if [[ -z "${QUORUM_WEBHOOK_SECRET:-}" ]]; then
  WEBHOOK_SECRET="$(openssl rand -hex 32)"
  echo "    NOTE: QUORUM_WEBHOOK_SECRET not set in .env; generating random value."
  echo "    Add this to your GitLab webhook configuration:"
  echo "      QUORUM_WEBHOOK_SECRET=${WEBHOOK_SECRET}"
else
  WEBHOOK_SECRET="${QUORUM_WEBHOOK_SECRET}"
fi
_upsert_secret "quorum-webhook-secret" "${WEBHOOK_SECRET}"

echo ""
echo "==> Granting Cloud Run service account access to secrets..."
# Cloud Run uses the compute default service account unless a dedicated SA is created
SA_EMAIL="${PROJECT_ID}@appspot.gserviceaccount.com"
SA_COMPUTE="$(gcloud iam service-accounts list \
  --project "${PROJECT_ID}" \
  --filter "displayName:Compute Engine default service account" \
  --format "value(email)" 2>/dev/null | head -1)"

for SA in "${SA_EMAIL}" "${SA_COMPUTE}"; do
  [[ -z "${SA}" ]] && continue
  for SECRET in quorum-gitlab-token quorum-gemini-key quorum-github-token quorum-webhook-secret; do
    gcloud secrets add-iam-policy-binding "${SECRET}" \
      --member "serviceAccount:${SA}" \
      --role "roles/secretmanager.secretAccessor" \
      --project "${PROJECT_ID}" \
      --quiet 2>/dev/null || true
  done
done

echo ""
echo "✅ GCP setup complete for project: ${PROJECT_ID}"
echo ""
echo "Next steps:"
echo "  1. Build + deploy Cloud Run webhook:"
echo "     cd $(dirname "${SCRIPT_DIR}")"
echo "     ./deploy/cloud_run.sh ${PROJECT_ID} ${REGION}"
echo ""
echo "  2. Deploy Agent Engine:"
echo "     python deploy/agent_engine.py --project ${PROJECT_ID} --region ${REGION} --build"
echo ""
echo "  3. Configure GitLab webhook on quorum-demo:"
echo "     URL:    <Cloud Run URL>/webhook/gitlab"
echo "     Secret: ${WEBHOOK_SECRET:0:8}... (see above)"
