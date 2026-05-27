# Changelog

All notable changes to Quorum are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Quorum follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_Changes staged for the next release._

---

## [0.1.0] — 2026-05-26

Initial release — submitted to the Google Cloud × GitLab Hackathon 2026.

### Added

**Core engine**
- Two-phase review architecture: fast regex surface detector (zero API calls) + Gemini 2.5 Pro agent loop
- Multi-turn Gemini tool-calling loop with hard cap (`QUORUM_MAX_TOOL_ROUNDS=10`) and tenacity retry (3 attempts, exponential backoff)
- Confidence threshold filtering (`QUORUM_MIN_CONFIDENCE=60`); PASS findings always shown
- `QUORUM_BLOCK_ON_CRITICAL=true` — exits with code 1 to fail CI pipelines on CRITICAL findings

**Eight coordination rules**
- `RULE_01` — Fencing Token Missing (Kleppmann 2016) — **CRITICAL**
- `RULE_02` — Wall-Clock TTL in Lock Lease (antirez 2016) — **HIGH**
- `RULE_03` — Saga Compensation Missing (microservices.io) — **CRITICAL**
- `RULE_04` — Idempotency Key Generated Internally (AWS Builders' Library) — **HIGH**
- `RULE_05` — @Transactional Wraps Distributed Lock (Spring AOP pitfall) — **CRITICAL**
- `RULE_06` — Retry Without Jitter (AWS Architecture Blog) — **HIGH**
- `RULE_07` — Sleep in Tests (Jepsen) — **MEDIUM**
- `RULE_08` — Kafka Auto-Commit With Manual Ack (Confluent) — **CRITICAL**

**GitLab MCP integration**
- Streamable-HTTP transport (`mcp 1.27.1`, spec 2025-06-18)
- Tools used: `get_merge_request_diffs`, `semantic_code_search`, `create_workitem_note`, `get_merge_request`
- Gemini exposed to `semantic_code_search` and `get_merge_request` only; posting and pipeline gate are Python-controlled

**Deployment**
- `quorum review` CLI — one-shot CI mode (exits 0/1)
- `quorum serve` — FastAPI webhook server for Cloud Run (returns 202, reviews in background)
- `quorum list-rules` — print all rules with severity and references
- `Dockerfile` — `uv`-based, non-root user, port 8080
- `deploy/cloud_run.sh` — one-command GCP deploy script
- `.gitlab-ci.yml` — drop-in CI job template

**Tests**
- 29 tests across surface detector, comment formatter, and full agent loop (mocked Gemini + MCP)
- Test fixtures: 3 bad samples + 3 good samples

**Documentation**
- `README.md` — architecture diagram, quickstart, CI config, Cloud Run deploy, full config reference
- `docs/RULES.md` — per-rule deep-dives with wrong/right code examples
- `docs/CONTRIBUTING.md` — step-by-step guide to adding Rule 9+

---

[Unreleased]: https://github.com/KaustubhUp025/quorum/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/KaustubhUp025/quorum/releases/tag/v0.1.0
