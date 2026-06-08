#!/usr/bin/env bash
# Deploy Quorum to Google Cloud Run.
# Usage: ./deploy/cloud_run.sh <GCP_PROJECT_ID> [REGION]
# Prerequisites: gcloud CLI authenticated, Docker installed, secrets created via setup_gcp.sh.

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <GCP_PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
SERVICE_NAME="quorum"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "==> Configuring Docker to push to GCR"
gcloud auth configure-docker --quiet

echo "==> Building image: ${IMAGE}"
docker build -t "${IMAGE}" .

echo "==> Pushing to Google Container Registry"
docker push "${IMAGE}"

echo "==> Deploying to Cloud Run (project=${PROJECT_ID}, region=${REGION})"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --port 8080 \
  --allow-unauthenticated \
  --set-env-vars "QUORUM_USE_VERTEX_AI=true,QUORUM_GOOGLE_CLOUD_PROJECT=${PROJECT_ID},QUORUM_GOOGLE_CLOUD_LOCATION=${REGION},QUORUM_CREATE_FIX_MRS=true,QUORUM_CORRELATE_CI=true" \
  --set-secrets "QUORUM_GITLAB_TOKEN=quorum-gitlab-token:latest,QUORUM_GEMINI_API_KEY=quorum-gemini-key:latest,QUORUM_GITHUB_TOKEN=quorum-github-token:latest" \
  --min-instances 0 \
  --max-instances 5 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.url)")

echo ""
echo "==> ✅ Cloud Run deployed!"
echo "    URL: ${SERVICE_URL}"
echo "    Health: ${SERVICE_URL}/health"
echo ""
echo "==> Next: add GitLab webhook"
echo "    URL:    ${SERVICE_URL}/webhook/gitlab"
echo "    Events: Merge request events"
echo "    Secret: (value of QUORUM_WEBHOOK_SECRET)"
