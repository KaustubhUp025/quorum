# Quorum — Complete Codebase Walkthrough

This document explains exactly what every file in the project does, why it was written the way it was, and how the files connect to each other. Read it top-to-bottom to build a full mental model of the system before making changes.

---

## Project layout at a glance

```
quorum/
├── pyproject.toml                   ← project metadata, dependencies, tool config
├── .gitignore                       ← files git must never track
├── .gitlab-ci.yml                   ← CI job that runs Quorum on every MR
├── Dockerfile                       ← container image for Cloud Run deployment
├── LICENSE                          ← Apache-2.0
├── README.md                        ← public-facing documentation
│
├── deploy/
│   └── cloud_run.sh                 ← one-command GCP deploy script
│
├── docs/
│   ├── RULES.md                     ← detailed per-rule reference
│   ├── CONTRIBUTING.md              ← how to add a new rule
│   └── CODEBASE.md                  ← this file
│
├── src/quorum/                      ← the Python package
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── models.py
│   ├── detector.py
│   ├── prompts.py
│   ├── gitlab_client.py
│   ├── agent.py
│   ├── formatter.py
│   ├── cli.py
│   ├── app.py
│   └── rules/
│       ├── __init__.py
│       ├── base.py
│       ├── registry.py
│       ├── rule_01_fencing_token.py
│       ├── rule_02_wall_clock_ttl.py
│       ├── rule_03_saga_compensation.py
│       ├── rule_04_idempotency_key.py
│       ├── rule_05_transactional_lock.py
│       ├── rule_06_retry_jitter.py
│       ├── rule_07_sleep_in_tests.py
│       └── rule_08_kafka_autocommit.py
│
└── tests/
    ├── conftest.py
    ├── test_detector.py
    ├── test_formatter.py
    ├── test_integration.py
    └── fixtures/
        ├── bad/   ← code that should trigger each rule
        └── good/  ← code that should NOT produce a finding
```

---

## Configuration files

### `pyproject.toml`

The single source of truth for the Python project. It does four things:

**1. Package metadata** — name, version, description, license, Python version floor (`>=3.10`), author, keywords, PyPI classifiers. This is what appears on PyPI if the package is published.

**2. Pinned dependencies** — every runtime dependency is pinned to its exact latest version at the time the project was created. This matters for reproducibility: anyone who clones the repo and runs `pip install -e .` gets exactly the same versions. The key dependencies are:
- `google-genai==2.6.0` — the official Google Generative AI Python SDK (new v2 API) used to call Gemini 2.5 Pro
- `mcp==1.27.1` — the official Model Context Protocol Python SDK, used to connect to the GitLab MCP server
- `pydantic==2.13.4` and `pydantic-settings==2.14.1` — data validation and env-var config loading
- `fastapi==0.136.3` + `uvicorn==0.48.0` — the async HTTP server for Cloud Run webhook mode
- `click==8.4.1` — CLI framework for the `quorum review` / `quorum serve` commands
- `structlog==25.5.0` — structured JSON-friendly logging
- `tenacity==9.1.4` — retry library with exponential backoff, used on Gemini API calls
- `rich==15.0.0` — coloured terminal output for the summary table printed after a review

**3. Dev dependencies** — `pytest`, `pytest-asyncio`, `pytest-mock`, `ruff`, `mypy` used only during development, not shipped with the package.

**4. Tool configuration** — `[tool.ruff]` configures the linter, `[tool.mypy]` configures the type checker, `[tool.pytest.ini_options]` tells pytest to use `asyncio_mode = "auto"` (so all `async def test_*` functions run without manual event-loop setup), and points `testpaths = ["tests"]`.

---

### `.gitignore`

Lists files that git must never commit. Key entries:
- `__pycache__/`, `*.pyc`, `*.pyd` — Python bytecode, regenerated automatically
- `.venv/`, `venv/`, `env/` — virtual environments, machine-specific
- `.env` — **critically important**: the `.env` file contains real secrets (`QUORUM_GITLAB_TOKEN`, `QUORUM_GEMINI_API_KEY`). If this were committed, credentials would be public on GitHub. The `.gitignore` is the last line of defence against that.
- `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` — tool caches, not source
- `*.pem`, `*.key`, `service_account.json`, `gcp_credentials.json` — GCP credential files

---

### `.gitlab-ci.yml`

Defines the GitLab CI job that runs Quorum on every merge request. Key design decisions:

```yaml
rules:
  - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```
This means the job **only fires on MRs**, never on direct pushes to main. This is intentional — reviewing `main` pushes makes no sense for a tool whose input is a diff.

```yaml
image: python:3.12-slim
```
Slim Python image keeps the container small and startup fast. Quorum installs in seconds.

```yaml
before_script:
  - pip install --quiet quorum
```
Installs Quorum from PyPI. When you run from source during development, change this to `pip install -e .` or mount the source.

```yaml
variables:
  QUORUM_GITLAB_URL: "$CI_SERVER_URL"
  QUORUM_BLOCK_ON_CRITICAL: "true"
```
CI variables prefixed `QUORUM_` are picked up automatically by `config.py`. `CI_SERVER_URL`, `CI_PROJECT_PATH`, and `CI_MERGE_REQUEST_IID` are injected by the GitLab runner automatically — `config.py` reads them via the `ci_*` aliases.

`allow_failure: false` means a CRITICAL finding fails the pipeline and blocks the merge.

---

### `Dockerfile`

Builds the container image used in Cloud Run deployment. Key design decisions:

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
```
Copies the `uv` package manager from its official Docker image. `uv` installs dependencies ~10× faster than pip, which matters for Cloud Run cold starts.

```dockerfile
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .
COPY src/ ./src/
```
Dependencies are copied and installed **before** the source code. Docker caches this layer — if only source files change (no new deps), the expensive install step is skipped on rebuild.

```dockerfile
RUN useradd --create-home --shell /bin/bash quorum
USER quorum
```
Runs as a non-root user. Required by most container security policies and Google Cloud Run best practices.

```dockerfile
CMD ["python", "-m", "quorum", "serve"]
```
Default command starts the FastAPI webhook server on port 8080 (Cloud Run's expected port).

---

## Source package: `src/quorum/`

### `__init__.py`

Three lines. Declares `__version__ = "0.1.0"`, `__author__`, and `__license__`. The version string is imported by `cli.py` for the `quorum --version` flag and by `app.py` for the `/health` endpoint response. Keeping it here (rather than in `pyproject.toml` alone) means it's accessible at runtime without parsing TOML.

---

### `__main__.py`

```python
from quorum.cli import main
if __name__ == "__main__":
    main()
```

Makes the package runnable as `python -m quorum`. When Python sees `-m quorum`, it executes `quorum/__main__.py`. This file just delegates to the Click CLI in `cli.py`. It is intentionally minimal — the real logic lives in `cli.py`.

---

### `config.py`

This is the **only place** in the codebase where configuration is read from the environment. Every other module imports `Settings` from here — nothing else ever reads `os.environ` directly.

It uses `pydantic-settings` which does three things automatically:
1. Reads values from environment variables with the prefix `QUORUM_` (e.g. `QUORUM_GITLAB_TOKEN` → `gitlab_token`)
2. Falls back to reading from a `.env` file in the current directory
3. Validates types and raises a clear error if a required value is missing

Key fields explained:

**`gitlab_token`** — no default, so it is **required**. Pydantic will raise at startup if it is missing. This is the PAT that authenticates both the MCP connection and any REST calls.

**`ci_project_id`, `ci_merge_request_iid`, `ci_project_path`** — these use `alias=` to read from the exact variable names that GitLab CI injects (`CI_PROJECT_ID`, etc.). This means when running inside GitLab CI, `quorum review` works with zero arguments — it reads the project and MR from the environment automatically.

**`min_confidence`** — an integer 0–100 with Pydantic validators (`ge=0, le=100`). Findings returned by Gemini below this threshold are filtered out before being posted. Defaults to 60.

**`max_tool_rounds`** — a safety cap on how many Gemini ↔ MCP back-and-forth turns are allowed. Without this, a misbehaving prompt could loop forever and run up API costs. Defaults to 10.

**`gitlab_mcp_url` property** — computed from `gitlab_url` + `gitlab_mcp_path`. Ensures the URL is always consistent (trailing slash stripped, path slash ensured by the `normalise_mcp_path` validator).

**`get_settings()`** — a factory function that returns a fresh `Settings()` instance. Used by `cli.py` and `app.py` instead of a module-level singleton, so tests can create `Settings` with test values without affecting other tests.

---

### `models.py`

Defines the three core data structures that flow through the entire pipeline. Uses Pydantic `BaseModel` for automatic validation and serialization.

**`Severity` (enum)**

A `str` enum so it serializes as a plain string in JSON (`"CRITICAL"` not `<Severity.CRITICAL: 'CRITICAL'>`). Has two computed properties:
- `.emoji` — returns the coloured circle for that severity level, used in comment formatting (`🔴`, `🟠`, `🟡`, `🔵`, `🟢`)
- `.is_blocking` — returns `True` only for `CRITICAL`, used to decide whether to set `blocked=True` in `ReviewResult`

**`Finding`**

One finding produced by Gemini for one rule. All fields beyond the five required ones (`rule_id`, `rule_name`, `severity`, `confidence`, `title`, `explanation`) are optional with `None` defaults. This is intentional — Gemini may not always provide a file path or diff snippet, and `formatter.py` handles the `None` case by simply omitting those sections from the comment.

- `rule_id` — e.g. `"RULE_01"`. Used as the heading in the comment and for grouping.
- `confidence` — integer 0–100 from Gemini's self-assessment. Validated by Pydantic to be in range.
- `diff_snippet` — the specific lines from the diff that triggered the finding. Rendered as a code block in the comment.
- `search_evidence` — what `semantic_code_search` returned that confirmed the finding. This is the "proof" that the problem is global, not local to the diff. Most important field for judge credibility.

**`ReviewResult`**

The overall outcome of a full MR review. Contains a list of `Finding` objects plus counters and the `blocked` flag. The four `*_count` properties are computed dynamically from the findings list rather than stored — this means they are always consistent with the actual findings and cannot get out of sync.

`blocked` is set by `agent.py` based on `block_on_critical` (from config) and whether any `CRITICAL` findings exist. The CLI reads `result.blocked` and calls `sys.exit(1)` to fail the pipeline.

**`MCPToolCall` and `MCPToolResult`**

Simple models used in tests to represent an MCP tool invocation and its result. Not used in production code paths — only present for test clarity.

---

### `detector.py`

The **fast pre-filter**. This is the first thing that runs on every diff, before any API calls.

```python
def detect_surfaces(diff: str) -> list[Rule]:
```

Iterates over every rule in `REGISTRY` and calls `rule.matches_surface(diff)`. Returns only the rules that matched. If the list is empty, `agent.py` exits immediately without calling Gemini or the MCP server at all — a typical CRUD diff costs zero API calls.

Why this matters: most MRs in a project have nothing to do with distributed coordination. Without this pre-filter, every single MR would invoke Gemini and the MCP server, which would be slow and expensive.

The matching logic lives in `Rule.matches_surface()` (in `base.py`), not here. `detector.py` is just the orchestrator that calls it for all rules. It also logs the result to structlog so the CI output shows which surfaces were detected.

---

### `rules/base.py`

Defines the `Rule` dataclass — the core unit of extensibility.

```python
@dataclass
class Rule:
    id: str
    name: str
    description: str
    reference: str
    reference_url: str
    surface_keywords: list[str]
    search_query_templates: list[str]
    reasoning_guidance: str
    surface_patterns: list[str] = field(default_factory=list)
```

Rules are **pure data** — they do not contain detection logic. This is a deliberate design choice. Detection logic that lives in code (if/else chains, AST parsers) is hard to contribute and hard to review. Detection logic that lives in a Gemini prompt is easy to update and easy to test just by changing the `reasoning_guidance` string.

**`matches_surface(diff)`** — the two-stage pre-filter:
1. First checks if any `surface_keywords` appear in the diff (case-insensitive substring). This is O(n) and costs nothing.
2. Only if a keyword matched, runs the `surface_patterns` regexes (more expensive). If patterns list is empty, the keyword match alone is sufficient.

The two-stage approach means expensive regex is only evaluated when at least one keyword is already present, keeping the pre-filter fast even for large diffs.

**`build_search_queries(diff)`** — currently returns the template list unchanged. The `diff` parameter is there for future rules that want to extract specific identifiers from the diff to make the search queries more targeted (e.g. extracting the saga step name `shipOrder` from the diff to produce the query `"compensation handler for shipOrder"`).

---

### `rules/registry.py`

Auto-discovers all rule modules at import time using Python's `pkgutil.iter_modules`.

```python
def _load_rules() -> dict[str, Rule]:
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if not module_info.name.startswith("rule_"):
            continue
        module = importlib.import_module(f"quorum.rules.{module_info.name}")
        if hasattr(module, "RULE") and isinstance(module.RULE, Rule):
            rules[module.RULE.id] = module.RULE
```

It scans the `rules/` directory for any Python file whose name starts with `rule_`, imports it, and checks for a module-level `RULE` variable. This means **adding a new rule requires zero changes to existing files** — you just create `rule_09_your_rule.py` with `RULE = Rule(...)` and it is automatically discovered.

The `REGISTRY` dict is sorted by rule ID so the order is always deterministic (`RULE_01` before `RULE_02` etc.) regardless of filesystem ordering.

`_rules` is a module-level cache (`None` until first call, then populated). This means `_load_rules()` is called exactly once per process, not once per review.

---

### `rules/rule_01_fencing_token.py` through `rule_08_kafka_autocommit.py`

Each file defines a single `RULE = Rule(...)` instance. They are all structurally identical — only the content differs. Here is what each field controls in practice:

| Field | Purpose | Effect at runtime |
|---|---|---|
| `id` | Unique identifier | Used as dict key in registry, heading in comment |
| `name` | Short label | Shown in comment heading and `quorum list-rules` table |
| `description` | Full anti-pattern description | Injected into Gemini's investigation prompt |
| `reference` | Citation name | Shown in `**Reference:**` line in MR comment |
| `reference_url` | Citation URL | Linked in `docs/RULES.md` |
| `surface_keywords` | Substring match strings | Stage 1 of pre-filter — cheap O(n) check |
| `surface_patterns` | Regex patterns | Stage 2 of pre-filter — only runs if keywords matched |
| `search_query_templates` | MCP search strings | Suggested to Gemini in the investigation prompt |
| `reasoning_guidance` | Gemini instructions | Injected into the prompt to guide severity thresholds |

A brief summary of each rule:

- **RULE_01** — Redis `SET NX` / `SETNX` called with a static string value (`"locked"`, `"1"`). The lock value must be a unique token so downstream writes can verify the lock is still held.
- **RULE_02** — Lock TTL calculated as `now + duration` using wall-clock time. Wall clocks across hosts can skew, making two nodes simultaneously think the lock has and has not expired. TTL must be a relative offset passed directly to the lock store.
- **RULE_03** — A saga step is added (e.g. `saga.step(shipOrder)`) but `semantic_code_search` finds no matching compensation handler anywhere in the project. Without compensation, a later failure leaves the system permanently in a partial state.
- **RULE_04** — An idempotency key is generated inside the request handler with `uuid.uuid4()` or equivalent. It must come from the client so retries carry the same key.
- **RULE_05** — A `@Transactional` annotation wraps a method that also acquires a distributed lock. Spring AOP commits the transaction after the method returns, but the lock is released inside the method body — a window where another thread can read stale uncommitted data.
- **RULE_06** — A retry loop sleeps for a deterministic interval (`2 ** attempt`, `attempt * 1000`). Under load, all concurrent retriers hit the same interval and retry simultaneously (thundering herd). A random jitter term prevents synchronisation.
- **RULE_07** — `time.sleep()` or `Thread.sleep()` inside a test file. Sleep-based waits are the root cause of flaky tests; the correct pattern is an explicit poll-with-timeout.
- **RULE_08** — A Kafka consumer has `enable.auto.commit=True` (or its default) while also calling `consumer.commit()` or `acknowledgment.acknowledge()`. Auto-commit fires on a timer regardless of processing success, silently breaking at-least-once delivery.

---

### `prompts.py`

Owns all prompt text. Separating prompts from logic makes it easy to iterate on the AI instructions without touching the agent or rule code.

**`SYSTEM_PROMPT`** — the persona and protocol that Gemini follows for every review. It establishes:
1. What Quorum is (its identity and sole purpose)
2. What tools are available and when to use them
3. The investigation protocol (step-by-step: read diff → search → reason → report)
4. The exact JSON output schema Gemini must produce
5. A special instruction to include PASS findings for rules that were checked but cleared — this is important for the demo because it shows judges that Quorum investigated and confirmed correct code, not just silently skipped a rule.

The system prompt ends with: `End your response with the JSON block enclosed in ```json ... ```.` — this makes the JSON reliably extractable by `_extract_json()` in `agent.py` using a simple regex.

**`build_review_prompt()`** — builds the per-review user message. It embeds:
- The project ID and MR IID (context for Gemini)
- A list of triggered rules with their names and first 120 characters of description
- The full diff text between `=== DIFF ===` markers
- Specific search queries to focus Gemini's investigation

The search queries injected at the bottom are the first two `search_query_templates` from each triggered rule. This gives Gemini concrete starting points rather than having it guess what to search for.

---

### `gitlab_client.py`

The **MCP transport layer**. This file is responsible for all communication with the GitLab MCP server.

**Connection lifecycle** — uses an async context manager:
```python
async with client.connect():
    result = await client.semantic_code_search(...)
```
The `connect()` method opens a `streamablehttp_client` session (MCP 2025-06-18 spec) to the GitLab MCP endpoint at `https://gitlab.com/api/v4/mcp`, runs the MCP handshake (`session.initialize()`), and yields. When the `async with` block exits, the session tears down cleanly. This ensures the HTTP connection is never leaked even if an exception occurs mid-review.

**`_call()` method** — the internal dispatcher. Every public method calls `_call()` with a tool name and arguments. `_call()` handles:
1. Verifying a session is active (raises `RuntimeError` if `connect()` was not called — a programming error, not a runtime error)
2. Calling `session.call_tool()` from the MCP SDK
3. Checking `result.isError` — MCP errors are not Python exceptions, they are a field on the result object
4. Normalising the response content (MCP content items can be text, image, or embedded resources) into a plain string that Gemini can read

**Public methods** each map to one GitLab MCP tool:
- `get_merge_request_diffs` → `get_merge_request_diffs` — gets the full diff content for a specific MR. This is the input to the review.
- `get_merge_request` → `get_merge_request` — gets metadata (title, description). Gemini can call this to understand the intent of the MR.
- `semantic_code_search` → `semantic_code_search` — natural-language code search across the project. This is the **load-bearing differentiator** — what lets Quorum find missing compensation handlers in other files.
- `create_workitem_note` → `create_workitem_note` — posts the review comment to the MR.
- `manage_pipeline` → `manage_pipeline` — can cancel or retry a pipeline (used for advanced blocking scenarios).
- `list_available_tools` — introspects what tools the connected MCP server exposes. Useful for debugging and verifying the GitLab instance supports all required tools.

---

### `agent.py`

The **heart of the system**. This file orchestrates all the moving parts into a single `review()` call.

**`_GEMINI_TOOLS`** — a list of `types.Tool` objects that declare to Gemini what functions it can call. These mirror the MCP tools exposed in `gitlab_client.py`. The `description` fields are what Gemini reads to decide *when* to call each tool — they must be specific enough that Gemini uses them for the right purpose.

Note that only two tools are exposed to Gemini: `semantic_code_search` and `get_merge_request`. The other MCP tools (`create_workitem_note`, `manage_pipeline`) are **not** exposed to Gemini — they are called directly by Python code, not by the AI. This is intentional: you never want an AI to autonomously post comments or cancel pipelines without explicit deterministic code controlling when that happens.

**`_make_gemini_client()`** — factory that creates a `genai.Client` pointed either at the Gemini API (if `use_vertex_ai=False`) or at Vertex AI (if `use_vertex_ai=True`). The caller just uses `client` regardless of which backend is active.

**`_extract_json()`** — parses Gemini's response to extract the JSON findings block. Uses a regex to find the ` ```json ... ``` ` fenced block from the response. Falls back to trying to parse the entire response text as JSON if no fenced block is found (handles edge cases where Gemini doesn't follow the format exactly).

**`_parse_findings()`** — converts the raw JSON dict into a list of validated `Finding` objects. Wraps each item in a `try/except` so one malformed finding does not prevent the rest from being parsed. Logs a warning for each parse failure so it shows in CI logs.

**`QuorumAgent._generate()`** — thin wrapper around `genai.Client.aio.models.generate_content`. The `@retry` decorator from `tenacity` wraps it with exponential backoff (2s → 4s → 8s), retrying up to 3 times on transient errors like rate limits or network issues. `temperature=0.1` keeps Gemini's responses deterministic — low temperature is critical for a code reviewer that should give consistent output.

**`QuorumAgent._run_tool()`** — dispatches a Gemini function call to the correct `GitLabMCPClient` method. Acts as a router: if Gemini calls `semantic_code_search`, this method calls `mcp.semantic_code_search()` with the right parameters. Returns the result as a string that is sent back to Gemini as a `FunctionResponse`.

**`QuorumAgent._agent_loop()`** — the multi-turn conversation loop:

```
Round 1:  Send initial prompt → Gemini responds with tool calls
Round 2:  Execute tool calls → send results → Gemini responds with more tool calls or final answer
Round N:  Gemini responds with no tool calls → extract text → return
```

Each round appends to `contents` (the conversation history). Gemini receives the full history on each call so it can reason about what it has already searched and what it has found. The loop has a hard cap at `max_tool_rounds` (default: 10) to prevent runaway loops. If the cap is hit, it sends a final "summarise now" message to force Gemini to produce output with whatever context it has gathered.

**`QuorumAgent.review()`** — the public entry point. Orchestrates the full pipeline:

1. **Fetch diff** — calls `mcp.get_merge_request_diffs()`
2. **Detect surfaces** — calls `detector.detect_surfaces()`. If nothing is triggered, posts a "no surfaces" comment and returns early. Zero Gemini calls spent.
3. **Build prompt** — calls `prompts.build_review_prompt()` with the diff and triggered rules
4. **Agent loop** — calls `_agent_loop()` which runs multi-turn Gemini + MCP
5. **Parse** — extracts JSON from Gemini's final response, builds `Finding` objects, filters by `min_confidence`
6. **Block decision** — sets `blocked=True` if any CRITICAL finding exists and `block_on_critical=True`
7. **Post comment** — calls `formatter.format_comment()` then `mcp.create_workitem_note()`
8. **Return `ReviewResult`** — the caller (`cli.py` or `app.py`) reads `result.blocked` to decide on the exit code

---

### `formatter.py`

Converts a `ReviewResult` into the Markdown string that becomes the MR comment.

**`format_comment()`** — the main function. Handles two paths:
- **Empty findings** — posts a brief "All surfaces checked, no issues found" message with the surface and rule counts. This confirms to the developer that Quorum ran and investigated, not that it silently passed.
- **With findings** — builds a full comment with a summary line, an optional "Pipeline blocked" banner, then individual finding blocks sorted by severity (CRITICAL first, PASS last).

**`_finding_block()`** — renders one finding. Conditionally includes each optional field:
- If `file_path` is set, adds a clickable file reference with line number
- If `diff_snippet` is set, renders it in a code block under "In your diff:"
- If `search_evidence` is set, renders it in a code block under "Found via semantic search:" — this is the most important visual element because it shows the judge that Quorum searched the whole project, not just the diff
- If `suggested_fix` is set, adds a "Suggested fix:" line
- If `reference` is set, adds a "Reference:" citation

Findings are sorted by severity index (`priority_order.index(f.severity)`) so CRITICAL always appears at the top of the comment regardless of what order Gemini returned them in.

**`_footer()`** — appends a footer with links to the GitHub repo, the rules docs, and the contributing guide. This is the open-source story: every comment plants the seed for a contributor to click through and add a rule.

---

### `cli.py`

The command-line interface. Exposes three Click commands grouped under the `quorum` entry point:

**`quorum review`** — the primary command, used in CI. Accepts `--project-id` and `--mr-iid` as options, but if they are absent it falls back to `CI_PROJECT_PATH` and `CI_MERGE_REQUEST_IID` from the environment (set by GitLab runner automatically). This dual-source design means the same command works both locally (`quorum review -p myorg/myrepo -m 42`) and in CI (`quorum review` with no args).

After the review completes, it prints a Rich-formatted summary table showing each finding with its rule ID, severity emoji, confidence, and title. If `result.blocked` is `True`, it prints a red "Pipeline blocked" banner and calls `sys.exit(1)` — this is what actually fails the GitLab CI job.

The `--dry-run` flag runs the full analysis but prints the comment to the terminal instead of posting it to the MR. Critical for local development and demos.

**`quorum list-rules`** — prints a Rich table of all registered rules with their ID, name, and reference. No API calls. Useful for understanding what Quorum checks and for validating that a new rule was auto-discovered.

**`quorum serve`** — starts the FastAPI webhook server using Uvicorn. Takes `--host` and `--port` options. The default port is 8080 (required by Cloud Run). This command is what the Dockerfile's `CMD` runs.

**`_configure_logging()`** — sets up `structlog` with coloured console output. Called at the start of each command. Structlog is configured to output human-readable logs during development (via `ConsoleRenderer`) but can be switched to JSON output for production by changing the processors list.

---

### `app.py`

The **FastAPI webhook server** — the Cloud Run deployment mode.

**`create_app()`** — factory function that creates and returns a configured `FastAPI` application. Using a factory (rather than a module-level `app` instance) makes the app testable: tests can call `create_app(settings)` with test settings without affecting a global object.

**`GET /health`** — returns `{"status": "ok", "version": "0.1.0"}`. Required by Cloud Run — the platform periodically calls this endpoint to verify the container is alive. Returns immediately with no dependencies.

**`POST /webhook/gitlab`** — receives GitLab webhook payloads. Does several things:
1. Checks `X-Gitlab-Event` header — ignores everything that is not a merge request event
2. Checks `mr_action` — only reviews `open`, `reopen`, and `update` actions (not `close`, `merge`, `approved`, etc.)
3. Extracts `project_id` and `mr_iid` from the payload
4. Calls `background_tasks.add_task()` to schedule the review asynchronously

The critical design here is **asynchronous background processing**. The webhook endpoint returns `{"status": "accepted"}` immediately, before the review runs. This is required because:
- GitLab expects webhooks to respond within 10 seconds
- A full Quorum review (multiple Gemini API calls + MCP calls) can take 30–60 seconds

`_run_review_background()` runs the review in FastAPI's background task executor. It wraps the entire review in a `try/except` so a failure on one MR does not crash the server.

---

## Test files

### `tests/conftest.py`

Provides two pytest fixtures used across test files:

- **`bad_fixture(name)`** — reads a file from `tests/fixtures/bad/` and returns its content as a string. Used in detector tests to assert that bad code triggers the expected rule.
- **`good_fixture(name)`** — reads from `tests/fixtures/good/`. Used to confirm the detector pre-filter fires on good code too (it is not a linter — it is a pre-filter; the AI does the actual judging).

---

### `tests/test_detector.py`

Unit tests for `detector.detect_surfaces()`. No API calls — these are pure Python tests that run in milliseconds.

Tests verify:
- An empty diff and a plain CRUD diff trigger nothing (zero false positives on common code)
- Each major fixture triggers its expected rule
- The full rule registry loads exactly 8 rules
- Multi-rule fixtures (e.g. `@Transactional` + `SET NX`) trigger multiple rules simultaneously

These tests directly validate the `surface_keywords` and `surface_patterns` in each rule module. When a pattern fix is needed (as happened with `nx=True` and `enable_auto_commit=True`), these tests catch it immediately.

---

### `tests/test_formatter.py`

Unit tests for `formatter.format_comment()`. No API calls — these operate entirely on constructed `Finding` and `ReviewResult` objects.

Tests verify:
- Empty findings produce a "pass" comment without crashing
- CRITICAL findings appear with the `🔴` emoji and a "Pipeline blocked" message
- PASS findings appear with `🟢`
- Findings are ordered CRITICAL → HIGH → MEDIUM → LOW → PASS regardless of input order
- The footer containing project links is always present

---

### `tests/test_integration.py`

Integration tests for the full `agent.review()` pipeline. These are the most important tests because they exercise the complete code path from diff input to `ReviewResult` output, including the multi-turn Gemini loop and MCP tool dispatch.

**No real API calls are made.** The tests use `unittest.mock` to replace:
- `QuorumAgent._generate` — the async method that calls Gemini. Replaced with `AsyncMock` that returns pre-constructed fake response objects.
- `GitLabMCPClient` methods — replaced with `AsyncMock` returning test strings.

**Fake Gemini response objects** — Gemini's response objects (`GenerateContentResponse`, `Candidate`, `Content`, `Part`, `FunctionCall`) are deeply nested. The helper functions at the top of the test file (`_make_function_call`, `_make_part`, `_make_content`, `_make_candidate`, `_make_response`) build `MagicMock` objects that duck-type as Gemini response objects. The agent loop reads `.candidates[0].content.parts`, `.function_call`, `.text`, etc. — and `MagicMock` satisfies all of these attribute accesses.

Key integration scenarios covered:

1. **Two-turn loop** — Gemini calls `semantic_code_search` in turn 1, receives results, returns JSON findings in turn 2. Verifies the MCP `semantic_code_search` was called exactly once and the comment was posted.
2. **Single turn, no tools** — Gemini returns findings directly without calling any tools. Verifies `semantic_code_search` is not called.
3. **No surfaces** — diff has no coordination patterns. Verifies Gemini's `_generate` is never called (pre-filter works).
4. **Only HIGH, not CRITICAL** — result has `blocked=False` even though there is a HIGH finding.
5. **Confidence threshold** — a finding with `confidence=45` is filtered out when `min_confidence=60`.
6. **PASS findings bypass filter** — PASS findings always appear regardless of confidence.
7. **Malformed JSON** — Gemini returns plain text instead of JSON. Review completes with empty findings, does not crash.
8. **`block_on_critical=False`** — CRITICAL finding present but `blocked=False` because the setting is off.

---

## Test fixtures

### `tests/fixtures/bad/`

Short Python code samples that exhibit a specific anti-pattern. They are **realistic** (use actual Redis/Kafka/HTTP client APIs) but **minimal** (no boilerplate beyond what's needed to show the pattern).

- `rule_01_no_fencing_token.py` — Redis `client.set(key, "locked", nx=True)` with no UUID token
- `rule_04_internal_idempotency_key.py` — `uuid.uuid4()` called inside `charge()` method
- `rule_06_retry_no_jitter.py` — `time.sleep(2 ** attempt)` inside a retry loop

### `tests/fixtures/good/`

The same scenarios implemented correctly.

- `rule_01_with_fencing_token.py` — `token = str(uuid.uuid4())` used as lock value, Lua script for atomic release
- `rule_04_client_idempotency_key.py` — key comes from `request.idempotency_key`, dedup check before write
- `rule_06_retry_with_jitter.py` — `random.uniform(0, sleep)` added to backoff

Good fixtures are important because they validate that the **surface detector** is a pre-filter, not a decision-maker. Good code that uses `redis.set(..., nx=True)` will still trigger RULE_01 in the detector — that is correct. The Gemini reasoning step is what distinguishes a fencing-token-present pattern from an absent one.

---

## Deployment files

### `deploy/cloud_run.sh`

A shell script that deploys Quorum to Google Cloud Run in four steps:

1. `docker build` — builds the container image and tags it `gcr.io/<PROJECT_ID>/quorum:latest`
2. `docker push` — pushes to Google Container Registry
3. `gcloud run deploy` — creates or updates the Cloud Run service with:
   - `--allow-unauthenticated` — the webhook endpoint must be reachable by GitLab without OAuth
   - `--set-secrets` — reads the GitLab token from Google Secret Manager (never passed as a plain env var)
   - `--set-env-vars` — sets `QUORUM_USE_VERTEX_AI=true` so the deployed service uses Vertex AI credentials (no API key needed — Cloud Run uses the service account)
   - `--min-instances 0` — scales to zero when idle (cost-free)
   - `--max-instances 5` — caps horizontal scale to control cost
4. Prints the service URL

---

## How the files connect: the full request flow

Here is the complete path from a developer opening a GitLab MR to the review comment appearing:

```
Developer opens MR
        │
        ▼
GitLab CI runner triggers quorum-review job (.gitlab-ci.yml)
        │
        ▼
cli.py review_cmd() reads config.py Settings (env vars from CI runner)
        │
        ▼
gitlab_client.py GitLabMCPClient.connect() opens MCP HTTP session
        │
        ▼
agent.py QuorumAgent.review()
   │
   ├─ gitlab_client.get_merge_request_diffs()
   │         └─ GitLab MCP server → returns diff text
   │
   ├─ detector.detect_surfaces(diff)
   │         └─ rules/*.py Rule.matches_surface() for each of 8 rules
   │            → returns [RULE_01, RULE_06] (example: 2 triggered)
   │
   ├─ prompts.build_review_prompt(diff, triggered_rules)
   │         └─ embeds diff + rule descriptions + search query hints
   │
   ├─ _agent_loop(prompt)
   │    │
   │    ├─ _generate(contents) → Gemini 2.5 Pro
   │    │       └─ Gemini responds: call semantic_code_search("fencing token...")
   │    │
   │    ├─ _run_tool("semantic_code_search", args)
   │    │       └─ gitlab_client.semantic_code_search() → GitLab MCP → code snippets
   │    │
   │    ├─ _generate(contents + search results) → Gemini 2.5 Pro
   │    │       └─ Gemini responds: ```json { "findings": [...] } ```
   │    │
   │    └─ returns final_text (Gemini's response with embedded JSON)
   │
   ├─ _extract_json(final_text) → raw dict
   ├─ _parse_findings(raw) → list[Finding]
   ├─ filter by min_confidence
   │
   ├─ formatter.format_comment(result) → Markdown string
   │
   └─ gitlab_client.create_workitem_note(project_id, mr_iid, comment)
             └─ GitLab MCP server → comment appears on MR
        │
        ▼
cli.py reads result.blocked
   → sys.exit(1) if True  (pipeline fails, merge blocked)
   → sys.exit(0) if False (pipeline passes)
```
