# Deploy

Deployment assets for Quorum's Google Cloud footprint.

| File | Purpose |
|---|---|
| `setup_gcp.sh` | One-time bootstrap: enable APIs, create Secret Manager secrets, grant the Cloud Run service account `roles/aiplatform.user` (Vertex AI ADC). |
| `cloud_run.sh` | Build + push the image and deploy the `quorum` Cloud Run service (webhook + `/demo`). |
| `cloudrun.env.yaml` | Single source of truth for the service's **plain** (non-secret) env, consumed by `cloud_run.sh` via `--env-vars-file`. |
| `agent_engine_adk.py` | Create/update the ADK Agent Engine (`run_review`, `explain_rule`, `list_rules` + remote GitLab MCPToolset). |

## Order

1. `./deploy/setup_gcp.sh <PROJECT_ID>` — once per project.
2. Deploy Cloud Run, either:
   - `./deploy/cloud_run.sh <PROJECT_ID> [REGION]` — full build/push/deploy; env from `cloudrun.env.yaml`, or
   - `gcloud run deploy quorum --source . --region us-central1 --project <PROJECT_ID>` — source build that **preserves the existing env** (use this for code-only redeploys).
3. Push to `main` — the Agent Engine packages from the installed/repo code, so deploy it from a clean tree.
4. `python3 deploy/agent_engine_adk.py …` — create or `--update` the ADK Agent Engine.
5. Deploy the GitLab MCP gateway (`quorum-mcp-gateway`) separately if it changed.

## Env: file vs source deploy

- `gcloud run deploy --source .` **preserves** the live env — safe for code-only pushes.
- `cloud_run.sh` / `--env-vars-file` **replaces** the entire plain-env set on each deploy. Every var
  the service needs must be in `cloudrun.env.yaml`, or it is silently dropped. Secrets are injected
  separately via `--set-secrets` and are not in the yaml.

The yaml's values were verified against the live `quorum` service on 2026-06-16:
`QUORUM_CREATE_FIX_MRS`, `QUORUM_CORRELATE_CI`, `QUORUM_USE_VERTEX_AI`, `QUORUM_GOOGLE_CLOUD_PROJECT`,
`QUORUM_GOOGLE_CLOUD_LOCATION`, `QUORUM_MCP_MODE` (+ 4 secrets).

## ⚠️ Judging freeze (until 2026-07-13)

The hackathon submission is being judged against the **live** services and the `main` branch until
**July 13, 2026**. Do **not** redeploy or mutate live state during this window:

- Cloud Run `quorum` and the ADK Agent Engine `1659372503279075328` stay on their submitted
  revisions; the engine is intentionally pinned to `min_instances=1` (billed) to keep the demo warm.
  Revert it to `0` only **after** judging to save cost.
- The `quorum-mcp-gateway` Cloud Run service stays as-is.
- Post-submission work lands on feature branches (e.g. `post-submission/hardening`) and is **not**
  merged to `main` or deployed until the window closes.
