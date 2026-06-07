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
| `containerd/nerdbox` | Go | GitHub | `waitForShimPipe` retries with fixed `time.Sleep(retryDelay)` — all shims sleep for exactly the same duration and retry simultaneously on Windows pipe failures. **PR #218 merged with jitter fix applied after Quorum's comment.** | RULE_06 🟠 | [PR #218 (merged)](https://github.com/containerd/nerdbox/pull/218) · [Issue #219](https://github.com/containerd/nerdbox/issues/219) |
| `vllm-project/vllm` | Python | GitHub | `_load_lora_config` retries with `interval *= 2` — no jitter, thundering herd when multiple workers load same LoRA adapter | RULE_06 🟠 | [Issue #44245](https://github.com/vllm-project/vllm/issues/44245) |
| `aio-libs/aiokafka` | Python | GitHub | Fixed retry backoff uses `retry_backoff_ms` with no jitter — thundering herd under leader election. **Draft fix PR opened after maintainer confirmed no response on issue.** | RULE_06 🟠 | [Issue #1165](https://github.com/aio-libs/aiokafka/issues/1165) · [Draft fix PR #1167](https://github.com/aio-libs/aiokafka/pull/1167) |
| `thelabnyc/django-logpipe` | Python | GitLab | `ProvisionedThroughputExceededException` retry uses fixed `time.sleep(5)` — all consumers wake simultaneously under Kinesis throttling | RULE_06 🟠 | [Issue #15](https://gitlab.com/thelabnyc/django-logpipe/-/work_items/15) |
| `Alexandre_Toto/architecture-event-driven-cdc` | Python | GitLab | `enable_auto_commit=True` in payment Kafka consumer + lost update on balance | RULE_08 🔴 + RULE_10 🔴 | [Issue #1](https://gitlab.com/Alexandre_Toto/architecture-event-driven-cdc/-/work_items/1) |
| `lhyou/fastapi-test` | Python | GitLab | `AIOKafkaConsumer` with `enable_auto_commit=True` — offset committed before processing | RULE_08 🔴 | [Issue #1](https://gitlab.com/lhyou/fastapi-test/-/work_items/1) |
| `kamilmazurek/event-driven-architecture-template` | Java | GitLab | `@KafkaListener` on `ITEM_CREATED` topic with no `DeadLetterPublishingRecoverer` or error handler — poison-pill blocks partition. `itemStore.add()` in handler with no idempotency check on `event.getEventId()` — duplicate delivery corrupts store. | RULE_12 🔴 + RULE_13 🔴 | [Issue #1](https://gitlab.com/kamilmazurek/event-driven-architecture-template/-/work_items/1) |
| `TeskaLabs/asab-iris` | Python | GitHub | "Retry Kafka consumer" PR: `delay = min(delay * 2, max_delay)` with no jitter — thundering herd on recovery. `AIOKafkaConsumer` catches all exceptions but has no DLQ — poison-pill messages are silently dropped. | RULE_06 🟠 + RULE_12 🟠 | [PR #122 review](https://github.com/TeskaLabs/asab-iris/pull/122#issuecomment-4643045308) |

**9 independent open-source projects · 3 languages (Java, Go, Python) · 2 platforms · zero false positives across all runs.**

### Multi-language benchmark validation

Validated on four dedicated benchmark MRs in a purpose-built demo repository, confirming surface detection and Gemini reasoning across 4 additional languages:

| Project | Language | Platform | Bug | Rule | Reference |
|---|---|---|---|---|---|
| `quorum-hackathon/multi-lang-coordination-demo` | TypeScript | GitLab | kafkajs `autoCommit:true` — offset committed before `eachMessage` handler completes | RULE_08 🔴 | [MR !1](https://gitlab.com/quorum-hackathon/multi-lang-coordination-demo/-/merge_requests/1) |
| `quorum-hackathon/multi-lang-coordination-demo` | Go | GitLab | `time.Sleep(retryDelay)` — fixed 2s constant, no jitter, thundering herd on inventory service | RULE_06 🟠 | [MR !2](https://gitlab.com/quorum-hackathon/multi-lang-coordination-demo/-/merge_requests/2) |
| `quorum-hackathon/multi-lang-coordination-demo` | Ruby | GitLab | `redis.set(key, "locked")` — static lock value, no fencing token | RULE_01 🔴 | [MR !3](https://gitlab.com/quorum-hackathon/multi-lang-coordination-demo/-/merge_requests/3) |
| `quorum-hackathon/multi-lang-coordination-demo` | Java | GitLab | `Thread.sleep(RETRY_DELAY_MS)` — fixed 3s constant, no jitter on payment gateway retries | RULE_06 🟠 | [MR !4](https://gitlab.com/quorum-hackathon/multi-lang-coordination-demo/-/merge_requests/4) |
| `quorum-hackathon/java-event-driven-benchmark` | Java | GitLab | `new RestTemplate()` without timeout stalls Kafka consumer thread (RULE_14). `@KafkaListener` on payment topic without DLQ (RULE_12). `itemStore.add()` without dedup (RULE_13). Verified Phase B inline diff comments + C3 labels + C4 reviewer suggestion via directory fallback. Phase D: fix MR !4 created → CI `success` → ✅ verified. | RULE_14 🔴 + RULE_13 🔴 + RULE_12 🟠 | [MR !1](https://gitlab.com/quorum-hackathon/java-event-driven-benchmark/-/merge_requests/1) |
| `KaustubhUp025/quorum-github-demo` | Python | GitHub | `requests.post()` with no timeout (RULE_14). `time.sleep(RETRY_DELAY)` fixed delay (RULE_06). Kafka consumer with no DLQ on failure (RULE_12). Phase D: fix PR #4 created → GitHub Actions `success` → ✅ verified. Full GitHub agentic loop end-to-end. | RULE_12 🔴 + RULE_06 🟠 + RULE_14 🟠 | [PR #1](https://github.com/KaustubhUp025/quorum-github-demo/pull/1) |

All review comments posted live. RULE_07 and RULE_11 correctly PASS on the Java MRs (catch-block retry ≠ test sleep, re-interrupt is the correct Go idiom).

### True-negative validation on GitLab's own infrastructure

Quorum was also run (dry-run, no comment posted) on three open MRs in GitLab's own production repositories. All three produced correct **PASS** verdicts — confirming Quorum does not false-positive on well-written distributed systems code:

| Project | Language | MR | Surfaces triggered | Verdict | Reason |
|---|---|---|---|---|---|
| `gitlab-org/gitaly` | Go | [!8812](https://gitlab.com/gitlab-org/gitaly/-/merge_requests/8812) | RULE_06, RULE_09, RULE_11 | 🟢 PASS | Retries route to a different peer each attempt via rendezvous hashing — fixed delays don't cause thundering herd |
| `gitlab-org/gitlab-runner` | Go | [!6806](https://gitlab.com/gitlab-org/gitlab-runner/-/merge_requests/6806) | RULE_02 | 🟢 PASS | `context.WithTimeout` — not a distributed lock; TTL concern doesn't apply |
| `gitlab-org/gitlab` | Ruby | [!233672](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/233672) | RULE_10 | 🟢 PASS | Pessimistic `FOR UPDATE` lock already present in the query |

A full Quorum review comment was also posted on `gitlab-org/gitaly` !8812 (note_id 3424838512): [view comment](https://gitlab.com/gitlab-org/gitaly/-/merge_requests/8812#note_3424838512)

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
│  · Duplicate guard — skips if Quorum already reviewed this MR       │
│    (--force to override)                                             │
│  · Language-aware suppression — mutes Java-only rules on Python     │
│    projects, Go-only rules on pure-Go projects                      │
│  · Posts one inline diff comment per finding (Phase B)              │
│  · Posts one summary table comment linking to all inline threads    │
│  · For CRITICAL findings: suggests reviewer from file/dir history   │
│    via GitLab commit API (C4 — directory fallback for new files)    │
│  · Applies MR label: quorum-review or quorum-review::critical       │
│  · For CRITICAL findings (opt-in): generates corrected file,        │
│    creates branch, commits fix, opens a DRAFT fix MR                │
│  · Outputs SARIF 2.1.0 (--format sarif) for GitHub Code Scanning    │
│  · Exits 1 to block merge if CRITICAL findings found                │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ fix MR created (opt-in)
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 4 — Verification Loop  (--verify-fix, opt-in)                │
│                                                                      │
│  Polls the CI pipeline on the fix branch every 30 s.                │
│  On completion, posts a follow-up comment to the original MR:       │
│                                                                      │
│  ✅ Fix verified — CI passes · The RULE_XX fix is safe to merge.    │
│  ❌ Fix needs review — CI failed · Adjust before merging.           │
│  ⚠️  Pipeline canceled · Check manually before merging.             │
│  ⏱ Verification timed out · Check pipeline status manually.        │
│                                                                      │
│  Closes the agentic loop: read state → decide → act → verify        │
└─────────────────────────────────────────────────────────────────────┘
```

The `semantic_code_search` call is what makes Quorum **better than a regex linter**. It can find the missing compensation handler in a different service, or confirm a fencing token is never passed downstream — anywhere in the project.

---

## Rules

Quorum ships with 14 named rules. Each is a standalone Python module — adding a new rule is a single-file contribution.

| ID | Rule | Severity | Reference |
|---|---|---|---|
| RULE_01 | Fencing Token Missing | 🔴 CRITICAL | [Kleppmann (2016)](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html) |
| RULE_02 | Wall-Clock TTL in Lock Lease | 🟠 HIGH | [antirez — Is Redlock safe?](http://antirez.com/news/101) |
| RULE_03 | Saga Compensation Missing | 🔴 CRITICAL | [microservices.io — Saga pattern](https://microservices.io/patterns/data/saga.html) |
| RULE_04 | Idempotency Key Generated Internally | 🟠 HIGH | [AWS Builders' Library](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotency-tokens/) |
| RULE_05 | @Transactional Wraps Distributed Lock | 🔴 CRITICAL | [Leapcell — Redis Lock Pitfalls (2025)](https://leapcell.io/blog/redis-distributed-lock-pitfalls) |
| RULE_06 | Retry Without Jitter | 🟠 HIGH | [AWS — Exponential Backoff and Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) |
| RULE_07 | Unsafe Test Coordination | 🟡 MEDIUM / 🟠 HIGH | [Jepsen — Latency tolerance](https://jepsen.io/analyses) |
| RULE_08 | Kafka Auto-Commit With Manual Ack | 🔴 CRITICAL | [Confluent — Offset Management](https://docs.confluent.io/platform/current/clients/consumer.html) |
| RULE_09 | Transactional Outbox Missing | 🔴 CRITICAL | [microservices.io — Transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html) |
| RULE_10 | Lost Update (SELECT without FOR UPDATE) | 🔴 CRITICAL | [Kleppmann — Designing Data-Intensive Applications §7](https://dataintensive.net/) |
| RULE_11 | Context Error Masking | 🟠 HIGH | [Go blog — Contexts and structs](https://go.dev/blog/context-and-structs) |
| RULE_12 | Dead Letter Queue Missing | 🟠 HIGH | [Confluent — Error Handling in Apache Kafka](https://www.confluent.io/blog/error-handling-patterns-in-kafka/) |
| RULE_13 | Idempotent Consumer Missing | 🔴 CRITICAL / 🟠 HIGH | [Confluent — Exactly-Once Semantics](https://www.confluent.io/blog/exactly-once-semantics-are-possible-heres-how-apache-kafka-does-it/) |
| RULE_14 | Cascading Timeout Missing | 🟠 HIGH | [Google SRE Book Ch. 22 — Cascading Failures](https://sre.google/sre-book/addressing-cascading-failures/) |

---

## Language support

The surface detector fires on coordination patterns across **6 languages**. Gemini's reasoning is language-agnostic for all of them.

| Language | Detected patterns |
|---|---|
| **Python** | All 14 rules — redis-py, kafka-python, aiokafka, SQLAlchemy, Temporal, Saga, requests, httpx |
| **Java** | RULE_03 (Axon Saga), RULE_06 (Thread.sleep), RULE_08 (@KafkaListener), RULE_09 (JPA/Hibernate), RULE_10 (@Version), RULE_12 (KafkaConsumer DLQ), RULE_13 (ConsumerRecord), RULE_14 (RestTemplate/WebClient) |
| **Go** | RULE_01 (go-redis SetNX), RULE_03 (Temporal workflow.ExecuteActivity), RULE_06 (time.Sleep), RULE_08 (sarama AutoCommit, kafka-go CommitInterval≠0), RULE_09 (GORM), RULE_10 (row.Scan), RULE_11 (select ctx.Done), RULE_14 (http.Get/DefaultClient) |
| **JavaScript / TypeScript** | RULE_01 (ioredis NX), RULE_03 (NestJS @Saga), RULE_06 (setTimeout), RULE_08 (kafkajs autoCommit), RULE_09 (TypeORM repository.save), RULE_10 (findById/findOne), RULE_12 (eachMessage), RULE_13 (eachMessage body write), RULE_14 (fetch/axios) |
| **Ruby** | RULE_01 (redis.setnx), RULE_06 (sleep), RULE_08 (karafka), RULE_10 (find/find_by) |
| **Rust / .NET** | RULE_06 (tokio::time::sleep / Task.Delay), RULE_01 (LockTake/StringSet), RULE_08 (Confluent .NET), RULE_09 (SaveChangesAsync) |

Real-world validated on open-source projects: Python (5), Java (2), Go (1). TypeScript and Ruby validated via dedicated benchmark MRs.

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

<details>
<summary>🟢 Passed checks (1)</summary>

🟢 **PASS** — RULE_07: Sleep correctly identified as task mock, not flaky test · `tests/test_fetcher.py:343` (100%)

</details>
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

## Review quality features

### Inline diff comments (Phase B)

Each non-PASS finding is posted as an **inline discussion thread** on the exact diff line, so the code context is visible alongside the finding. A compact summary table is always posted as a top-level note with `↗` markers linking to the inline threads.

### Duplicate guard (Phase C1)

If Quorum has already reviewed the MR, a second run is a no-op. The guard checks for the `Quorum · Distributed Coordination Review` fingerprint in existing notes before posting.

```bash
quorum review --force  # bypass the duplicate guard
```

Or set `force_review: true` in `.quorum.yml`.

### Language-aware rule suppression (Phase C2)

Rules that only apply to specific languages are automatically suppressed when those languages aren't present in the project. For example, `RULE_05` (Java-only) is muted on a pure Python repo; `RULE_03` is muted on a Go-dominant repo.

### MR label application (Phase C3)

After posting, Quorum applies a label to the MR:

| Outcome | Label applied |
|---|---|
| CRITICAL findings present | `quorum-review::critical` |
| No critical findings | `quorum-review` |

Set `apply_labels: false` in `.quorum.yml` to disable.

### Reviewer suggestion (Phase C4)

For each CRITICAL finding, Quorum queries the GitLab commit history for the affected file and mentions up to 2 recent contributors as suggested reviewers. For new files with no commit history, it falls back to the parent directory's contributors.

```
📋 Suggested reviewer: @alice has recent commits to the `src/consumer/` directory — consider requesting their review.
```

### Verification loop (Phase D)

After a fix MR is created, Quorum doesn't stop. Pass `--verify-fix` and it polls the CI pipeline on the fix branch, then posts a follow-up comment on the **original** MR when CI finishes:

```bash
QUORUM_CREATE_FIX_MRS=true \
quorum review --project-id myorg/myrepo --mr-iid 42 \
  --verify-fix --verify-timeout 300
```

| CI result | Comment posted |
|---|---|
| ✅ success | `Fix verified — CI passes on MR !N. Safe to merge.` |
| ❌ failure | `Fix needs review — CI failed on MR !N. Adjust before merging.` |
| ⚠️ canceled | `Pipeline canceled on MR !N. Check manually.` |
| ⏱ timeout | `Verification timed out. Check pipeline status manually.` |

Works on both GitLab (pipeline status API) and GitHub (Actions workflow runs API). Timeout defaults to 600 s, configurable via `QUORUM_VERIFY_FIX_TIMEOUT`.

Live validated:
- GitLab: `quorum-hackathon/java-event-driven-benchmark` MR !1 → fix MR !4 → pipeline `success` → ✅ [comment](https://gitlab.com/quorum-hackathon/java-event-driven-benchmark/-/merge_requests/1#note_3430520706)
- GitHub: `KaustubhUp025/quorum-github-demo` PR #1 → fix PR #4 → Actions `success` → ✅ [comment](https://github.com/KaustubhUp025/quorum-github-demo/pull/1#issuecomment-4643636819)

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

**File issues for findings in a review:**

```bash
# Preview what would be filed (no API calls)
quorum file-issue --platform github --project-id owner/repo --mr-iid 42 --dry-run

# File issues for all HIGH+ findings (issues disabled → opens draft fix PR instead)
quorum file-issue --platform github --project-id owner/repo --mr-iid 42

# Only CRITICAL findings
quorum file-issue --project-id myorg/myrepo --mr-iid 42 --min-severity CRITICAL
```

Reads the audit log for the given MR, then for each qualifying finding:
1. Opens an issue in the target repo (if issues are enabled).
2. If issues are disabled (e.g. enterprise repos using Jira), opens a draft fix PR with the finding body instead.
3. If both fail, logs a structured warning with the finding text for manual filing.

Enable automatic issue filing after every review with `QUORUM_AUTO_FILE_ISSUES=true`.

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
| `QUORUM_VERIFY_FIX_TIMEOUT` | `600` | Seconds to poll fix branch CI before timing out (used by `--verify-fix`) |
| `QUORUM_CORRELATE_CI` | `false` | Check failing CI pipeline and correlate with findings |
| `QUORUM_WEBHOOK_SECRET` | — | HMAC secret for validating incoming GitLab/GitHub webhooks |
| `QUORUM_AUTO_FILE_ISSUES` | `false` | Auto-file issues after every `quorum review` (opt-in) |
| `QUORUM_AUTO_FILE_ISSUES_MIN_SEVERITY` | `HIGH` | Minimum severity to auto-file: `CRITICAL`, `HIGH`, or `MEDIUM` |
| `QUORUM_AUDIT_LOG` | `~/.quorum/audit_log.json` | Path to the persistent audit log |
| `QUORUM_PORT` | `8080` | Port for Cloud Run webhook server |
| `QUORUM_LOG_LEVEL` | `INFO` | Log level |

---

## Local pre-commit hook

`quorum check` runs Stage 1 only (surface detector — no Gemini, no API calls, ~5 ms). Use it as a git pre-commit hook to catch coordination patterns before they reach CI.

```bash
# Install once per repo
quorum check --install-hook

# Or run manually
quorum check --staged          # default: staged changes
quorum check --last-commit     # last committed diff
git diff main... | quorum check --diff -
```

Exit code 0 = clean. Exit code 1 = surfaces detected → run `quorum review` before merging.

---

## Adding a new rule

Contributing a rule is a single-file addition. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for the full guide.

**Quick version:**

1. Create `src/quorum/rules/rule_12_your_rule_name.py`
2. Define a `RULE = Rule(...)` with keywords, patterns, search query templates, and reasoning guidance
3. Add `tests/fixtures/bad/rule_12_*.py` and `tests/fixtures/good/rule_12_*.py`
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

pytest                     # run all 223 tests
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
- **Resilient tool-call loop:** When using Gemini context caching (tools embedded in the cache), the model occasionally emits tool invocations as plain text instead of structured function-call parts. The agent detects three encoding variants (`call:<name>{}`, `<execute_tool>` XML, `tool_name` in raw text) and nudges the model back to the structured API rather than crashing.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

*Built for the Google Cloud × GitLab Hackathon 2026.*
