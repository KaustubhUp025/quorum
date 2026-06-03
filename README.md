# Quorum

[![CI](https://github.com/KaustubhUp025/quorum/actions/workflows/ci.yml/badge.svg)](https://github.com/KaustubhUp025/quorum/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/quorum.svg)](https://pypi.org/project/quorum/)
[![Python](https://img.shields.io/pypi/pyversions/quorum.svg)](https://pypi.org/project/quorum/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![codecov](https://codecov.io/gh/KaustubhUp025/quorum/branch/main/graph/badge.svg)](https://codecov.io/gh/KaustubhUp025/quorum)

**AI-powered distributed coordination linter for GitLab MRs and GitHub PRs.**

Quorum reviews merge requests for coordination anti-patterns that static linters and generic AI reviewers can't catch — missing fencing tokens, incomplete saga compensations, retries without jitter, lost updates, transactional outbox violations, and more. It posts structured findings as comments, generates draft fix PRs for critical issues, and outputs SARIF for GitHub Code Scanning.

> *"SonarQube doesn't have rules for saga compensation. Semgrep can't reason across service boundaries. CodeRabbit can't search the full repo. Quorum can."*

---

## Real-world validation

Quorum has found real coordination bugs in real open-source projects across GitHub and GitLab, filed as public issues:

| Project | Language | Platform | Bug found | Rule | Reference |
|---|---|---|---|---|---|
| `atlanhq/atlas-metastore` | Java | GitHub | `AsyncIngestionConsumerService` writes to JanusGraph then publishes to Kafka with no outbox — phantom events / lost writes on failure. `calculateExponentialBackoff()` uses pure `delay * 2.0` multiplier, no jitter | RULE_09 🔴 + RULE_06 🟠 | [PR #6699 review](https://github.com/atlanhq/atlas-metastore/pull/6699) |
| `containerd/nerdbox` | Go | GitHub | `waitForShimPipe` retries with fixed `time.Sleep(retryDelay)` — all shims sleep for exactly the same duration and retry simultaneously on Windows pipe failures | RULE_06 🟠 | [Issue #219](https://github.com/containerd/nerdbox/issues/219) |
| `vllm-project/vllm` | Python | GitHub | `_load_lora_config` retries with `interval *= 2` — no jitter, thundering herd when multiple workers load same LoRA adapter | RULE_06 🟠 | [Issue #44245](https://github.com/vllm-project/vllm/issues/44245) |
| `aio-libs/aiokafka` | Python | GitHub | Fixed retry backoff uses `retry_backoff_ms` with no jitter — thundering herd under leader election | RULE_06 🟠 | [Issue #1165](https://github.com/aio-libs/aiokafka/issues/1165) |
| `thelabnyc/django-logpipe` | Python | GitLab | `ProvisionedThroughputExceededException` retry uses fixed `time.sleep(5)` — all consumers wake simultaneously under Kinesis throttling | RULE_06 🟠 | [Issue #15](https://gitlab.com/thelabnyc/django-logpipe/-/work_items/15) |
| `Alexandre_Toto/architecture-event-driven-cdc` | Python | GitLab | `enable_auto_commit=True` in payment Kafka consumer + lost update on balance | RULE_08 🔴 + RULE_10 🔴 | [Issue #1](https://gitlab.com/Alexandre_Toto/architecture-event-driven-cdc/-/work_items/1) |
| `lhyou/fastapi-test` | Python | GitLab | `AIOKafkaConsumer` with `enable_auto_commit=True` — offset committed before processing | RULE_08 🔴 | [Issue #1](https://gitlab.com/lhyou/fastapi-test/-/work_items/1) |

**7 independent projects · 3 languages (Java, Go, Python) · 2 platforms · zero false positives across all runs.**

---

## How it works

Quorum runs as a three-stage agent pipeline:

```
MR / PR opened
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 1 — SurfaceDetectorAgent  (zero API calls, ~5ms)             │
│                                                                      │
│  Regex + keyword pre-filter on the diff.                             │
│  Detects: locks · sagas · retries · idempotency · kafka · outbox    │
│  → If nothing detected: exits immediately, no Gemini cost            │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ surfaces detected
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 2 — DeepReasoningAgent  (Gemini 2.5 Pro, multi-turn)         │
│                                                                      │
│  Builds investigation prompt → enters tool-calling loop:            │
│                                                                      │
│  ┌─────────────────────────────────────────────────────┐            │
│  │  Gemini reasons about diff                          │            │
│  │      │                                              │            │
│  │      ▼  tool call                                   │            │
│  │  semantic_code_search ──► GitLab MCP / GitHub API   │            │
│  │      │                                              │            │
│  │      ▼  search results                              │            │
│  │  Gemini reasons with cross-repo context             │            │
│  │      │                                              │            │
│  │      ▼  (repeats up to max_tool_rounds)             │            │
│  │  → JSON findings with severity + confidence         │            │
│  └─────────────────────────────────────────────────────┘            │
│                                                                      │
│  Features:                                                           │
│  · thinking_budget=-1  (dynamic reasoning depth per turn)           │
│  · Google Search grounding (CVE / incident report citations)        │
│  · Context-aware severity (RULE_08 → CRITICAL in payment service,   │
│    HIGH in metrics service — same bug, different blast radius)       │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ findings
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 3 — ReportFormatterAgent                                      │
│                                                                      │
│  · Formats findings as structured Markdown comment                   │
│  · Posts comment to MR / PR                                          │
│  · For CRITICAL findings (opt-in): generates corrected file,        │
│    creates branch, commits fix, opens a DRAFT fix MR                │
│  · Outputs SARIF 2.1.0 (--format sarif) for GitHub Code Scanning    │
│  · Exits 1 to block merge if CRITICAL findings found                │
└─────────────────────────────────────────────────────────────────────┘
```

The `semantic_code_search` call is what makes Quorum **better than a regex linter**. It can find the missing compensation handler in a different service, or confirm a fencing token is never passed downstream — anywhere in the project.

---

## Rules

Quorum ships with 10 named rules. Each is a standalone Python module — adding a new rule is a single-file contribution.

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
| RULE_09 | Transactional Outbox Missing | 🔴 CRITICAL | [microservices.io — Transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html) |
| RULE_10 | Lost Update (SELECT without FOR UPDATE) | 🔴 CRITICAL | [Kleppmann — Designing Data-Intensive Applications §7](https://dataintensive.net/) |

---

## Example output

```
## Quorum · Distributed Coordination Review

> Scanned 6 coordination surfaces · 4 critical, 1 high · checked 6 rules

---

### 🔴 CRITICAL — RULE_01: Static lock value — no fencing token
**Confidence: 100%** | `src/orderservice/lock_manager.py:12`

redis.set(key, "locked", nx=True) uses a static string as the lock value.
If this process pauses (GC, network blip) and the TTL expires, a second
process acquires the lock. When the first resumes, the database has no way
to reject the stale write — there is no fencing token to compare against.

**Found via semantic search:**
  Searched entire project for UUID/counter lock values — none found.
  OrderRepository.save() accepts no version parameter.

**Suggested fix:** Use uuid.uuid4() as the lock value and pass it as a
conditional check to every write while the lock is held.

→ Draft fix: MR !8  ← Quorum opened this automatically

---

### 🟢 PASS — RULE_07: Sleep correctly identified as task mock, not flaky test
**Confidence: 100%** | `tests/test_fetcher.py:343`
asyncio.sleep(1000000) replaces a background task — valid test scaffolding.
```

---

## Platform support

Quorum works on both GitLab and GitHub.

### GitLab MCP client tiers

| Tier | Client | Tools | Semantic search | Requirements |
|---|---|---|---|---|
| **glab** (recommended) | `glab mcp serve` | 191 | ✅ AI-semantic (needs Duo/Ultimate) | `glab` v1.80+ on PATH |
| **zereight** (community) | `@zereight/mcp-gitlab` | 107 | ❌ (REST lexical fallback) | Node.js + npx |
| **rest** (fallback) | GitLab REST API | — | ❌ (lexical) | Nothing extra |

Auto-detection: `glab` on PATH → uses `glab`; otherwise → `rest`.  
Override: `QUORUM_MCP_MODE=glab|zereight|rest`

### GitHub support

```bash
quorum review --platform github \
  --project-id owner/repo \
  --mr-iid 42
```

Uses GitHub REST API (`QUORUM_GITHUB_TOKEN`). `project_id` is `owner/repo`, `mr-iid` is the PR number.

---

## SARIF output (GitHub Code Scanning)

```bash
quorum review --platform github \
  --project-id owner/repo --mr-iid 42 \
  --no-comment --format sarif \
  > results.sarif
```

Produces SARIF 2.1.0 JSON on stdout (logs go to stderr). Upload to GitHub Code Scanning for inline PR annotations:

```yaml
# Drop this in .github/workflows/quorum-scan.yml
- name: Run Quorum
  run: |
    quorum review --platform github \
      --project-id "${{ github.repository }}" \
      --mr-iid "${{ github.event.pull_request.number }}" \
      --format sarif --no-comment > results.sarif
  continue-on-error: true

- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

The full workflow is at [`.github/workflows/quorum-scan.yml`](.github/workflows/quorum-scan.yml).

---

## Fix proposal MRs

When `QUORUM_CREATE_FIX_MRS=true` and a CRITICAL finding is confirmed, Quorum:

1. Asks Gemini to generate the corrected file
2. Creates a branch `quorum-fix/rule-XX-<timestamp>` from the source branch
3. Commits the corrected file
4. Opens a **Draft MR** titled `Draft: [Quorum] Fix RULE_XX: <title>`
5. Adds a link to the fix MR inside the findings comment: `→ Draft fix: MR !N`

```bash
QUORUM_CREATE_FIX_MRS=true \
quorum review --project-id myorg/myrepo --mr-iid 42
```

---

## LiteLLM multi-backend

The default backend is Gemini 2.5 Pro (full features: thinking, Google Search grounding, function calling). For contributors who don't have a Gemini key, any LiteLLM-compatible model works:

```bash
# Local Ollama (free, no cloud key)
QUORUM_LLM_BACKEND=ollama/mistral quorum review ...

# OpenAI
QUORUM_LLM_BACKEND=openai/gpt-4o quorum review ...

# Anthropic
QUORUM_LLM_BACKEND=anthropic/claude-3-5-sonnet-20241022 quorum review ...
```

Install the extra dependencies:

```bash
pip install "quorum[alt-backends]"
```

**Requirements for non-Gemini backends:**
- The model must support OpenAI-compatible tool/function calling
- Minimum 16K token context (our prompt + rules + diff is ~6K tokens)
- Recommended: `qwen2.5-coder:7b`, `mistral:7b`, `llama3.1:8b`, `gpt-4o`, or Claude 3.5 Sonnet

Note: Gemini-specific features (dynamic thinking depth, Google Search grounding) are not available on LiteLLM backends. Quality on non-tool-capable models will be lower.

---

## Per-project config (`.quorum.yml`)

Create `.quorum.yml` in your project root to override settings without touching env vars:

```yaml
# .quorum.yml — committed to the repo
rules:
  disabled: [RULE_07]       # skip rules that are noisy for this project

confidence_threshold: 75    # higher bar than the global default of 60

ignore_paths:
  - tests/
  - "**/*_test.py"
  - vendor/

create_fix_mrs: true        # auto-open fix MRs for critical findings
platform: gitlab            # gitlab or github
llm_backend: gemini/gemini-2.5-pro   # override per project
```

Priority: `.quorum.yml` > environment variables > built-in defaults.

---

## Quickstart

### Prerequisites

- Python 3.10+
- A Gemini API key ([get one](https://aistudio.google.com)) — or set `QUORUM_LLM_BACKEND` to use another backend
- For GitLab: `glab` CLI v1.80+ ([install](https://gitlab.com/gitlab-org/cli/-/releases)) recommended for semantic search
- For GitHub: a GitHub PAT with `repo` + `read:discussion` scope

### Install

```bash
pip install quorum
# With LiteLLM backends (ollama, openai, anthropic):
pip install "quorum[alt-backends]"
```

Or from source:

```bash
git clone https://github.com/KaustubhUp025/quorum
cd quorum
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env — fill in QUORUM_GITLAB_TOKEN and QUORUM_GEMINI_API_KEY
```

Minimal `.env` for GitLab:

```bash
QUORUM_GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
QUORUM_GEMINI_API_KEY=AIza...
```

Minimal `.env` for GitHub:

```bash
QUORUM_GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
QUORUM_GEMINI_API_KEY=AIza...
```

### Run a review

**GitLab MR:**

```bash
quorum review --project-id myorg/myrepo --mr-iid 42
quorum review --project-id myorg/myrepo --mr-iid 42 --dry-run    # print comment, don't post
quorum review --project-id myorg/myrepo --mr-iid 42 --rest-only  # no glab required
```

**GitHub PR:**

```bash
quorum review --platform github --project-id owner/repo --mr-iid 7
quorum review --platform github --project-id owner/repo --mr-iid 7 --format sarif
```

**With alternate LLM backend:**

```bash
QUORUM_LLM_BACKEND=ollama/mistral \
  quorum review --project-id myorg/myrepo --mr-iid 42 --dry-run
```

**List all rules:**

```bash
quorum list-rules
```

---

## GitLab CI integration

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

CI/CD variables to set in GitLab project settings:

| Variable | Required | Description |
|---|---|---|
| `QUORUM_GITLAB_TOKEN` | ✅ | PAT with `api` scope |
| `QUORUM_GEMINI_API_KEY` | ✅ | Gemini API key |
| `QUORUM_CREATE_FIX_MRS` | optional | `true` to auto-open fix MRs |
| `QUORUM_DISABLED_RULES` | optional | `RULE_07,RULE_08` to skip rules |

---

## Google Cloud deployment (webhook mode)

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
echo -n "glpat-xxx" | gcloud secrets create quorum-gitlab-token --data-file=-
./deploy/cloud_run.sh YOUR_PROJECT_ID us-central1
```

After deploying:
- GitLab webhook: project → Settings → Webhooks → `https://<service>.run.app/webhook/gitlab`
- Enable: **Merge request events**

---

## Configuration reference

All settings use the `QUORUM_` prefix (e.g. `QUORUM_GITLAB_TOKEN`).

| Variable | Default | Description |
|---|---|---|
| `QUORUM_PLATFORM` | `gitlab` | Target platform: `gitlab` or `github` |
| `QUORUM_GEMINI_API_KEY` | — | Gemini API key |
| `QUORUM_USE_VERTEX_AI` | `false` | Use Vertex AI (Application Default Credentials) |
| `QUORUM_GOOGLE_CLOUD_PROJECT` | — | GCP project (Vertex AI) |
| `QUORUM_GOOGLE_CLOUD_LOCATION` | `us-central1` | GCP region |
| `QUORUM_GEMINI_MODEL` | `gemini-2.5-pro` | Gemini model (used when backend is `gemini/*`) |
| `QUORUM_LLM_BACKEND` | `gemini/gemini-2.5-pro` | LLM backend — prefix `gemini/` uses native SDK; others use LiteLLM |
| `QUORUM_GITLAB_URL` | `https://gitlab.com` | GitLab instance base URL |
| `QUORUM_GITLAB_TOKEN` | — | GitLab PAT with `api` scope |
| `QUORUM_GITHUB_TOKEN` | — | GitHub PAT with `repo` + `read:discussion` scope |
| `QUORUM_MCP_MODE` | auto | GitLab MCP tier: `glab`, `zereight`, or `rest` |
| `QUORUM_MIN_CONFIDENCE` | `60` | Suppress findings below this confidence (0–100) |
| `QUORUM_BLOCK_ON_CRITICAL` | `true` | Exit 1 (fail pipeline) when CRITICAL findings found |
| `QUORUM_MAX_SEARCH_RESULTS` | `5` | Max code snippets per semantic search call |
| `QUORUM_MAX_TOOL_ROUNDS` | `10` | Hard cap on Gemini tool-call rounds per review |
| `QUORUM_DISABLED_RULES` | — | Comma-separated rule IDs to skip (e.g. `RULE_07,RULE_08`) |
| `QUORUM_CREATE_FIX_MRS` | `false` | Auto-open draft fix MRs for CRITICAL findings |
| `QUORUM_FIX_MR_MAX_COUNT` | `1` | Max fix MRs opened per review |
| `QUORUM_CORRELATE_CI` | `false` | Check failing CI pipeline and correlate with findings |
| `QUORUM_PORT` | `8080` | Port for Cloud Run webhook server |
| `QUORUM_LOG_LEVEL` | `INFO` | Log level |

---

## Adding a new rule

Contributing a rule is a single-file addition. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for the full guide.

**Quick version:**

1. Create `src/quorum/rules/rule_11_your_rule_name.py`
2. Define a `RULE = Rule(...)` with keywords, patterns, search query templates, and reasoning guidance
3. Add `tests/fixtures/bad/rule_11_*.py` and `tests/fixtures/good/rule_11_*.py`
4. Add a surface-detector assertion to `tests/test_detector.py`
5. Open a PR — the registry auto-discovers new rules at startup

---

## Security

Quorum has been audited for prompt injection and supply-chain risks. Key protections:

- **Prompt injection**: All externally-sourced content (diff, code search results, file contents, MR description, CI logs) is wrapped in `<untrusted_*>` XML boundary tags. The system prompt contains ABSOLUTE RULES explicitly banning instruction-following from any untrusted content.
- **SSRF**: `QUORUM_GITLAB_URL` rejects requests to cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`), loopback addresses, and RFC 1918 private ranges.
- **Webhook auth**: Incoming webhooks are validated with `hmac.compare_digest` against `QUORUM_WEBHOOK_SECRET`. The server logs a warning at startup if the secret is not set.
- **Input validation**: `.quorum.yml` `llm_backend` is validated against a provider prefix allowlist (`gemini/`, `openai/`, `ollama/`, …) — rejects `file://` and arbitrary URIs.
- **Secret scrubbing**: `_scrub_secrets()` redacts `ghp_`, `glpat-`, `AIzaSy`, and `AKIA` patterns from CI logs and error messages before they reach the LLM or logs.

See [SECURITY.md](SECURITY.md) for the full disclosure policy.

---

## Audit log

Every `quorum review` run is appended to `~/.quorum/audit_log.json`.  
Override the path with `QUORUM_AUDIT_LOG=/path/to/log.json`.

```bash
quorum history              # all runs, most-recent first
quorum history --last 5     # last 5 runs
quorum history --repo vllm  # filter by repo name (partial match)
quorum history --json       # raw JSON for scripting / dashboards
```

Sample output:

```
╭──────────────────────────────────────────────────────────────────╮
│                       Quorum Audit Log                           │
│  8 reviews  ·  12 findings  ·  🔴 5 critical  🟠 4 high  🟢 3   │
╰──────────────────────────────────────────────────────────────────╯

  Findings by severity
  ────────────────────────────────────────────────
  🔴 CRITICAL  ████████████████████░░░░   5  (42%)
  🟠 HIGH      ████████████████░░░░░░░░   4  (33%)
  🟡 MEDIUM    ████░░░░░░░░░░░░░░░░░░░░   1  ( 8%)
  🟢 PASS      ████████████░░░░░░░░░░░░   3  (25%)

  Reviews  (showing 8 of 8)
  ┌────┬────────────┬──────────┬──────────────────────────┬───────┬───┬───┬─────────┬─────────────┐
  │  # │ Date       │ Platform │ Repo                     │ PR/MR │ 🔴│ 🟠│ Blocked │ Issue/Fix   │
  ├────┼────────────┼──────────┼──────────────────────────┼───────┼───┼───┼─────────┼─────────────┤
  │  8 │ 2026-06-01 │ github   │ vllm-project/vllm        │ #34981│ 0 │ 1 │   —     │ Issue #44245│
  │  7 │ 2026-06-01 │ gitlab   │ rjackson-education/...   │  !19  │ 0 │ 1 │   —     │ —           │
  │  4 │ 2026-05-31 │ github   │ aio-libs/aiokafka        │ #1164 │ 0 │ 1 │   —     │ Issue #1165 │
  └────┴────────────┴──────────┴──────────────────────────┴───────┴───┴───┴─────────┴─────────────┘
```

The `--json` flag outputs the raw JSON array, making it easy to pipe into `jq` or import into a dashboard.

---

## Roadmap

The following features are planned for future releases. Community contributions are welcome — see [CONTRIBUTING.md](docs/CONTRIBUTING.md).

### Conversation follow-up (close-the-loop)

When Quorum files an issue or posts a review comment, the issue URL is stored in the audit log. A planned `quorum follow-up` command will:

1. Check every open issue Quorum has filed — has the maintainer pushed a fix?
2. Re-run the surface detector on the updated file at the commit that closed the issue.
3. If the pattern is no longer present, post one final confirmation comment: *"Fixed in `<sha>` — confirmed by Quorum."*

This turns Quorum from a one-shot reviewer into a persistent collaborator that closes the loop without human intervention.

### Per-user multi-tenant tracking

For organisations deploying Quorum as a shared service (GitHub App / GitLab App):

- **OAuth login** — Quorum acts on each user's behalf using their own token; no shared PAT.
- **Per-org dashboard** — repos reviewed, findings by rule and severity, fix-acceptance rate over time.
- **Persistent backend** — Firestore or PostgreSQL stores findings across deployments, enabling trend analysis and regression detection.
- **Automated resolution tracking** — webhook subscription on issues automatically marks a Quorum finding as resolved when the filed issue is closed.

---

## Development

```bash
pip install -e ".[dev]"

pytest                     # run all 144 tests
ruff check src/ tests/     # lint
mypy src/                  # type check
```

---

## Architecture

| Component | Technology |
|---|---|
| LLM (primary) | Gemini 2.5 Pro — `google-genai 2.6.0` — thinking_budget=-1, Google Search grounding |
| LLM (alternate) | Any LiteLLM backend — `litellm 1.86.2` — OpenAI-compatible tool calling |
| GitLab MCP | `glab mcp serve` (191 tools, official) · `@zereight/mcp-gitlab` (107 tools, community) |
| GitHub client | GitHub REST API via `httpx 0.28.1` |
| Data models | Pydantic 2.13.4 |
| Config | pydantic-settings 2.14.1 + `.quorum.yml` (pyyaml 6.0.3) |
| Webhook server | FastAPI 0.136.3 + Uvicorn 0.48.0 |
| CLI | Click 8.4.1 |
| Output formats | Markdown MR comment · SARIF 2.1.0 |
| Cloud deployment | Google Cloud Run + Vertex AI Agent Engine |

**Design principles:**
- **Two-phase design:** Surface detector is a cheap pre-filter (regex/keyword). Gemini is only called when the diff touches a coordination pattern. A typical CRUD MR exits in milliseconds with zero API calls.
- **Rules are data:** Keywords, patterns, search templates, and reasoning guidance — not if/else chains. Detection intelligence lives in the Gemini system prompt. Easy to review and contribute.
- **Cross-repo context:** `semantic_code_search` is load-bearing. A diff-only reviewer has high false-positive rates. Cross-project search is what lets Quorum say "this lock has no fencing token anywhere in the codebase" with 100% confidence.
- **Confidence threshold:** Gemini self-reports confidence (0–100) per finding. Findings below `QUORUM_MIN_CONFIDENCE` are suppressed. Tunable per project via `.quorum.yml`.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

*Built for the Google Cloud × GitLab Hackathon 2026.*
