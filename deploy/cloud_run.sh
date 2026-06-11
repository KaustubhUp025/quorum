#!/usr/bin/env bash
# Deploy Quorum to Google Cloud Run.
# Usage: ./deploy/cloud_run.sh <GCP_PROJECT_ID> [REGION]
#   e.g. ./deploy/cloud_run.sh gen-lang-client-0294573094
#
# Prerequisites:
#   Run ./deploy/setup_gcp.sh first (creates secrets, enables APIs).
#   gcloud auth login && gcloud auth configure-docker gcr.io

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <GCP_PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
SERVICE_NAME="quorum"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

# Must be run from the repo root so Docker picks up the Dockerfile
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

echo "==> Configuring Docker auth for gcr.io"
gcloud auth configure-docker gcr.io --quiet

echo "==> Building image: ${IMAGE}"
docker build -t "${IMAGE}" .

echo "==> Pushing to Container Registry"
docker push "${IMAGE}"

# NOTE: --set-env-vars REPLACES the entire env set on each deploy, so the full
# runtime config must be listed here (omitting any var silently drops it from the
# live service). QUORUM_USE_VERTEX_AI=true makes the agent authenticate to Gemini
# via the Cloud Run service-account ADC instead of the depletable API key — this
# requires the SA to hold roles/aiplatform.user, which ./deploy/setup_gcp.sh grants.
echo "==> Deploying Cloud Run service (project=${PROJECT_ID}, region=${REGION})"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --port 8080 \
  --allow-unauthenticated \
  --set-env-vars "\
QUORUM_CREATE_FIX_MRS=true,\
QUORUM_CORRELATE_CI=true,\
QUORUM_USE_VERTEX_AI=true,\
QUORUM_GOOGLE_CLOUD_PROJECT=${PROJECT_ID},\
QUORUM_GOOGLE_CLOUD_LOCATION=${REGION},\
QUORUM_MCP_MODE=glab" \
  --set-secrets "\
QUORUM_GITLAB_TOKEN=quorum-gitlab-token:latest,\
QUORUM_GEMINI_API_KEY=quorum-gemini-key:latest,\
QUORUM_GITHUB_TOKEN=quorum-github-token:latest,\
QUORUM_WEBHOOK_SECRET=quorum-webhook-secret:latest" \
  --min-instances 0 \
  --max-instances 5 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 60

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.url)")"

echo ""
echo "✅ Cloud Run deployed!"
echo "   URL:    ${SERVICE_URL}"
echo "   Health: ${SERVICE_URL}/health"
echo ""
echo "Next: configure GitLab webhook on quorum-demo"
echo "   Webhook URL:   ${SERVICE_URL}/webhook/gitlab"
echo "   Trigger on:    Merge request events"
echo "   Secret token:  (value stored in Secret Manager as quorum-webhook-secret)"
echo ""
echo "Retrieve webhook secret:"
echo "   gcloud secrets versions access latest --secret=quorum-webhook-secret --project=${PROJECT_ID}"
