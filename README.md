# Quorum

[![CI](https://github.com/KaustubhUp025/quorum/actions/workflows/ci.yml/badge.svg)](https://github.com/KaustubhUp025/quorum/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/quorum.svg)](https://pypi.org/project/quorum/)
[![Python](https://img.shields.io/pypi/pyversions/quorum.svg)](https://pypi.org/project/quorum/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![codecov](https://codecov.io/gh/KaustubhUp025/quorum/branch/main/graph/badge.svg)](https://codecov.io/gh/KaustubhUp025/quorum)

**Distributed-coordination MR linter powered by Gemini 2.5 Pro and GitLab MCP.**

Quorum reviews your merge requests for coordination anti-patterns that static linters and generic AI reviewers miss — missing fencing tokens, incomplete saga compensations, retries without jitter, and more. It posts structured findings directly as GitLab MR comments and can block the pipeline on critical issues.

> *"I spent 3 weeks manually debugging distributed-lock race conditions in production. Quorum catches them in 30 seconds before they merge."*

---

## How it works

```
GitLab MR opened
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  GitLab CI: quorum-review job                               │
│                                                             │
│  1. get_merge_request_diffs ──► GitLab MCP server           │
│                                                             │
│  2. Surface detector  (regex pre-filter, zero API calls)    │
│     Detects: locks · sagas · retries · idempotency · kafka  │
│                                                             │
│  3. Gemini 2.5 Pro agent loop                               │
│     ┌──────────────────────────────────────┐               │
│     │  Gemini reasons about diff           │               │
│     │        │                             │               │
│     │        ▼  tool call                  │               │
│     │  semantic_code_search ──► GitLab MCP │               │
│     │        │                             │               │
│     │        ▼  search results             │               │
│     │  Gemini reasons about cross-repo     │               │
│     │  context + diff together             │               │
│     │        │                             │               │
│     │        ▼  JSON findings              │               │
│     └──────────────────────────────────────┘               │
│                                                             │
│  4. create_workitem_note ──► post comment on MR             │
│                                                             │
│  5. Exit 1 if CRITICAL findings (blocks merge)              │
└─────────────────────────────────────────────────────────────┘
```

The `semantic_code_search` MCP call is what makes Quorum **better than a regex linter**. It can find the missing compensation handler in a different service, or confirm that a fencing token is never passed downstream anywhere in the project.

---

## Rules

Quorum ships with 8 named rules. Each is a standalone Python module in `src/quorum/rules/` — adding a new rule is a single-file contribution.

| ID | Rule | Severity | Reference |
|---|---|---|---|
| RULE_01 | Fencing Token Missing | 🔴 CRITICAL | [Kleppmann (2016)](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html) |
| RULE_02 | Wall-Clock TTL in Lock Lease | 🟠 HIGH | [antirez — Is Redlock safe?](http://antirez.com/news/101) |
| RULE_03 | Saga Compensation Missing | 🔴 CRITICAL | [microservices.io — Saga pattern](https://microservices.io/patterns/data/saga.html) |
| RULE_04 | Idempotency Key Generated Internally | 🟠 HIGH | [AWS Builders' Library](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotency-tokens/) |
| RULE_05 | @Transactional Wraps Distributed Lock | 🔴 CRITICAL | [Leapcell — Redis Lock Pitfalls (2025)](https://leapcell.io/blog/redis-distributed-lock-pitfalls) |
| RULE_06 | Retry Without Jitter | 🟠 HIGH | [AWS — Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) |
| RULE_07 | Sleep in Tests | 🟡 MEDIUM | [Jepsen — Latency tolerance](https://jepsen.io/analyses) |
| RULE_08 | Kafka Auto-Commit With Manual Ack | 🔴 CRITICAL | [Confluent — Offset Management](https://docs.confluent.io/platform/current/clients/consumer.html) |

---

## Example output

```
## Quorum · Distributed Coordination Review

> Scanned 2 coordination surfaces · 1 critical, 1 high · checked 2 rules

---

### 🔴 CRITICAL — RULE_01: Lock acquired without fencing token
**Confidence: 91%** | `src/order/service.py:142`

In service.py:L142, you acquire a Redis lock with a static value "locked".
If this process pauses (GC, network) and the lock TTL expires, another process
acquires the lock — and when this process resumes, the DB has no way to reject
the stale write without a fencing token.

**In your diff:**
  redis_client.set(f"order:{order_id}", "locked", nx=True, px=30000)
  write_order_to_db(order_id)  # ← no version/token passed

**Found via semantic search:**
  # OrderRepository.save(order) — no if_version parameter found in project

**Suggested fix:** Return the lock value (a UUID or counter) and pass it as a
conditional check to every write while the lock is held.

**Reference:** Kleppmann (2016) — How to do distributed locking
```

---

## GitLab MCP Client Tiers

Quorum supports three GitLab client tiers — pick the one that fits your setup:

| Tier | Command | Tools | Semantic search | Requirements |
|---|---|---|---|---|
| **glab** (recommended) | `glab mcp serve` | 191 | ✅ `glab_search_semantic` (needs GitLab Duo/Ultimate) | `glab` v1.80+ on PATH |
| **zereight** (community) | `@zereight/mcp-gitlab` | 107 | ❌ (REST lexical fallback) | Node.js + npx |
| **rest** (fallback) | GitLab REST API | — | ❌ (lexical) | Nothing extra |

Auto-detection: if `glab` is on PATH → uses `glab`; otherwise → `rest`.

Override with `QUORUM_MCP_MODE=glab|zereight|rest` in your `.env`.

**Why the community server?**  
The `@zereight/mcp-gitlab` package was used as a working fallback while investigating the official server's authentication requirements. It remains the recommended choice for contributors and CI environments that don't have `glab` installed. The official `glab mcp serve` is used for the primary demo because it is the GitLab partner's own tooling and provides semantic code search via GitLab Duo.

---

## Quickstart

### Prerequisites

- Python 3.10+
- A Gemini API key **or** a Google Cloud project with Vertex AI enabled
- For best results: `glab` CLI v1.80+ ([install](https://gitlab.com/gitlab-org/cli/-/releases)) and a GitLab Ultimate plan/trial (for semantic search)

### Install

```bash
pip install quorum
```

Or from source:

```bash
git clone https://github.com/KaustubhUp025/quorum
cd quorum
pip install -e ".[dev]"
```

### Configure

Create a `.env` file (never commit this):

```bash
# Required
QUORUM_GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
QUORUM_GEMINI_API_KEY=AIza...

# Optional — defaults shown
QUORUM_GITLAB_URL=https://gitlab.com
QUORUM_GEMINI_MODEL=gemini-2.5-pro
QUORUM_MIN_CONFIDENCE=60
QUORUM_BLOCK_ON_CRITICAL=true
```

For Vertex AI instead of the Gemini API:

```bash
QUORUM_USE_VERTEX_AI=true
QUORUM_GOOGLE_CLOUD_PROJECT=my-gcp-project
QUORUM_GOOGLE_CLOUD_LOCATION=us-central1
# No QUORUM_GEMINI_API_KEY needed — uses Application Default Credentials
```

### Run a review locally

```bash
quorum review --project-id myorg/myrepo --mr-iid 42
```

Dry-run (prints the comment without posting it):

```bash
quorum review --project-id myorg/myrepo --mr-iid 42 --dry-run
```

List available rules:

```bash
quorum list-rules
```

---

## GitLab CI integration

Add this to your project's `.gitlab-ci.yml`:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/KaustubhUp025/quorum/main/.gitlab-ci.yml'
```

Or copy the job definition directly:

```yaml
quorum-review:
  stage: review
  image: python:3.12-slim
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  before_script:
    - pip install --quiet quorum
  script:
    - quorum review
  variables:
    QUORUM_BLOCK_ON_CRITICAL: "true"
    QUORUM_MIN_CONFIDENCE: "60"
```

Set these CI/CD variables in your GitLab project settings:

| Variable | Required | Description |
|---|---|---|
| `QUORUM_GITLAB_TOKEN` | ✅ | GitLab PAT with `read_api` and `write_repository` scopes |
| `QUORUM_GEMINI_API_KEY` | ✅ | Gemini API key (or use Vertex AI) |
| `QUORUM_GOOGLE_CLOUD_PROJECT` | ✅ | GCP project (if using Vertex AI) |

---

## Google Cloud deployment (webhook mode)

Quorum can also run as a persistent Cloud Run service that receives GitLab webhooks — useful for reviewing MRs across many projects without configuring CI in each.

### Deploy to Cloud Run

```bash
# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Store the GitLab token as a secret
echo -n "glpat-xxx" | gcloud secrets create quorum-gitlab-token --data-file=-

# Build and deploy
./deploy/cloud_run.sh YOUR_PROJECT_ID us-central1
```

### Configure the GitLab webhook

1. Go to your GitLab group or project → **Settings → Webhooks**
2. Add the Cloud Run URL: `https://<your-service>.run.app/webhook/gitlab`
3. Enable **Merge request events**
4. Optionally add a secret token

---

## Configuration reference

All settings are read from environment variables (prefix `QUORUM_`) or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `QUORUM_GEMINI_API_KEY` | — | Gemini API key |
| `QUORUM_USE_VERTEX_AI` | `false` | Use Vertex AI instead of Gemini API |
| `QUORUM_GOOGLE_CLOUD_PROJECT` | — | GCP project (Vertex AI) |
| `QUORUM_GOOGLE_CLOUD_LOCATION` | `us-central1` | GCP region |
| `QUORUM_GEMINI_MODEL` | `gemini-2.5-pro` | Model to use |
| `QUORUM_GITLAB_URL` | `https://gitlab.com` | GitLab instance URL |
| `QUORUM_GITLAB_TOKEN` | — | GitLab PAT or CI job token |
| `QUORUM_GITLAB_MCP_PATH` | `/api/v4/mcp` | MCP server path |
| `QUORUM_MIN_CONFIDENCE` | `60` | Minimum confidence to report a finding (0–100) |
| `QUORUM_BLOCK_ON_CRITICAL` | `true` | Fail CI on CRITICAL findings |
| `QUORUM_MAX_SEARCH_RESULTS` | `5` | Max snippets per semantic search call |
| `QUORUM_MAX_TOOL_ROUNDS` | `10` | Hard cap on Gemini tool-call rounds per review |
| `QUORUM_PORT` | `8080` | Port for the Cloud Run webhook server |
| `QUORUM_LOG_LEVEL` | `INFO` | Log level |

---

## Adding a new rule

Contributing a rule is a single-file addition. See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for the full guide.

**Quick version:**

1. Create `src/quorum/rules/rule_09_your_rule_name.py`
2. Define a `RULE = Rule(...)` instance with keywords, patterns, search queries, and reasoning guidance
3. Add a bad fixture to `tests/fixtures/bad/` and a good fixture to `tests/fixtures/good/`
4. Add a surface-detector assertion to `tests/test_detector.py`
5. Open a PR — the registry auto-discovers new rules at startup

---

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

---

## Architecture notes

- **Two-phase design:** The surface detector (regex/keyword) is a cheap pre-filter. Gemini and MCP are only invoked when the diff actually touches a coordination pattern. On a typical CRUD MR, Quorum exits in milliseconds without any API calls.
- **Rules are data, not code:** Each rule contributes keywords, regex patterns, MCP search templates, and reasoning guidance. Detection intelligence lives in the Gemini system prompt. This makes rules easy to review and contribute.
- **`semantic_code_search` is load-bearing:** A finding that only looks at the diff has a high false-positive rate. Cross-project search is what lets Quorum say "this lock has no fencing token anywhere in the project" with confidence.
- **Confidence threshold:** Gemini self-reports confidence (0–100) per finding. Findings below `QUORUM_MIN_CONFIDENCE` are suppressed. Tunable per project.

---

## Tech stack

| Component | Technology |
|---|---|
| LLM | Gemini 2.5 Pro (`google-genai 2.6.0`) |
| MCP client | `mcp 1.27.1` (streamable-HTTP transport) |
| Partner MCP server | [GitLab MCP Server](https://docs.gitlab.com/user/gitlab_duo/model_context_protocol/) |
| Data models | Pydantic 2.13.4 |
| Webhook server | FastAPI 0.136.3 + Uvicorn 0.48.0 |
| CLI | Click 8.4.1 |
| Cloud deployment | Google Cloud Run + Vertex AI Agent Engine |

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

*Built for the Google Cloud × GitLab Hackathon 2026.*
