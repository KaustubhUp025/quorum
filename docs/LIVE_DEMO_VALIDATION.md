<p align="center">
  <img src="../src/quorum/static/mark.svg" alt="Quorum logo" width="64" />
</p>

# Quorum Live — Real-World Validation Set

A curated set of **6 GitHub PRs + 6 GitLab MRs** from independent open-source projects,
hand-verified to contain (or *deliberately not* contain) the coordination anti-patterns
Quorum detects. Run each one through the live demo and compare Quorum's output against the
**"Expected finding"** column below.

> **▶ Demo:** <https://quorum-3fnjzg6adq-uc.a.run.app/demo> — paste the URL, watch the agent review it.

## How to use this document

1. Copy a **target URL** from the tables below into the demo (or run the CLI: `quorum review <url>`).
2. Wait for Quorum to finish its review.
3. Compare what Quorum reports against the **Expected finding** and **Evidence** for that row.
4. Record the result in the **Match?** column (✅ exact / 🟡 partial / ❌ miss / ⚠️ false-positive).

Each target was verified by fetching the actual PR/MR diff and confirming the offending line is
present in the **added** code (or, for controls, confirming the code is *correct*). Verification
date: **2026-06-11**. Line snippets are quoted verbatim from the diffs.

### Severity legend
🔴 CRITICAL · 🟠 HIGH · 🟡 MEDIUM · 🟢 **Control** (correct code — Quorum should stay silent)

### Rules referenced here
| Rule | Name | Severity | One-liner |
|---|---|---|---|
| RULE_01 | Fencing Token Missing | 🔴 | `SETNX`/`SET NX` lock uses a static value (`"1"`, `"locked"`) instead of a unique token |
| RULE_06 | Retry Without Jitter | 🟠 | Deterministic backoff (`delay *= 2`, fixed `sleep`) → thundering herd |
| RULE_07 | Unsafe Test Coordination | 🟡 | Bare `sleep` as a readiness wait in a test (but **not** an intentional timing stimulus) |
| RULE_08 | Kafka Auto-Commit w/ Manual Ack | 🔴 | `enable_auto_commit=True` together with manual `commit()` → silent message loss |
| RULE_09 | Transactional Outbox Missing | 🔴 | DB write + event publish in one flow, no outbox/CDC → phantom or lost events |
| RULE_10 | Lost Update | 🔴 | Read-modify-write (`SELECT` → compute in app → `UPDATE`) with no `FOR UPDATE`/CAS |
| RULE_14 | Cascading Timeout Missing | 🟠 | HTTP/gRPC call with no explicit timeout → cascading failure |

---

## GitHub — 6 Pull Requests

| # | Target | Lang | State | Predicted rule(s) | Expected finding | Match? |
|---|---|---|---|---|---|:---:|
| G1 | [Brints/spoken-api #71](https://github.com/Brints/spoken-api/pull/71) | Python | merged | RULE_12 🔴 *(always)* · RULE_08 🟠 *(intermittent)* | DLQ-missing on the egress consumer (always); auto-commit data-loss (sometimes elevated) | ✅ verified 2026-06-11 |
| G2 | [WaiMarn/S402017Project #7](https://github.com/WaiMarn/S402017Project/pull/7) | Python | merged | RULE_08 🔴 | Flag auto-commit + manual commit conflict | |
| G3 | [iliya-malecki/edgy #3](https://github.com/iliya-malecki/edgy/pull/3) | Python | open | RULE_08 🔴 | Flag auto-commit in the Kafka runtime | |
| G4 | [CarriedWorldUniverse/nexus #371](https://github.com/CarriedWorldUniverse/nexus/pull/371) | Go | merged | RULE_06 🟠 | Flag deterministic backoff (no jitter) | |
| G5 | [carissafarry/tag-me #17](https://github.com/carissafarry/tag-me/pull/17) | Go | merged | RULE_01 🔴 | Flag static-value Redis lock | |
| G6 | [BasedHardware/omi #7801](https://github.com/BasedHardware/omi/pull/7801) | Python | open | **RULE_01 control 🟢** | **No finding** — lock is correct | |

### G1 — Brints/spoken-api #71 → RULE_12 (always) + RULE_08 (intermittent)
**Why:** The egress Kafka consumer added in this PR has two coordination problems:
```python
enable_auto_commit=True,            # diff flips this from False → True
...
try:
    ... process frame ...
except Exception as frame_err:
    logger.exception("Error processing egress frame: %s", frame_err)  # logs + discards
```
1. **RULE_12 (DLQ missing)** — the `except` just logs and drops the message; a poison-pill is
   silently lost with no dead-letter path. There is **no manual commit**, so this is the precise,
   reliable finding.
2. **RULE_08 (auto-commit data loss)** — auto-commit fires on a timer, so a crash after
   auto-commit but before processing loses the message.

**Observed (2026-06-11):** the result is **non-deterministic** because the Gemini reasoning layer
chooses which surfaced rules to elevate:
- **Cloud Run `/demo`** → reported **RULE_12 MEDIUM** only (declined RULE_08).
- **Vertex Agent Engine** → reported **RULE_08 HIGH** (`ws_router.py:221`) **+ RULE_12 MEDIUM**
  (`ws_router.py:328`), and confirmed RULE_01 / RULE_03 pass.

**Expected:** RULE_12 every run; RULE_08 on some runs. Both are correct — count either as a match.
The earlier doc over-committed to RULE_08-only; reality is RULE_12-always with RULE_08 intermittent.

### G2 — WaiMarn/S402017Project #7 → RULE_08 🔴
**Why:** Textbook RULE_08 — auto-commit **and** a manual commit in the same consumer loop:
```python
enable_auto_commit=True,
...
for msg in consumer:
    ...
    c.commit()
```
The manual `c.commit()` is meaningless because auto-commit already fired on a timer.
**Expected:** RULE_08 CRITICAL; mention the redundant/contradictory manual commit.

### G3 — iliya-malecki/edgy #3 → RULE_08 🔴
**Why:** A Kafka runtime/streaming framework sets `enable_auto_commit=True` in its consumer
factory (`extensions/kafka/runtime_context.py`), so every app built on it inherits at-most-once
semantics on crash:
```python
enable_auto_commit=True,
auto_offset_reset=self.auto_offset_reset,
```
**Expected:** RULE_08 CRITICAL. (Open PR — a clean "review before merge" demo.)

### G4 — CarriedWorldUniverse/nexus #371 → RULE_06 🟠
**Why:** `Runner.InitWithRetry` doubles the delay deterministically, with no randomness:
```go
// runtime/dispatch/runner.go — InitWithRetry
delay := baseDelay
for attempt := 1; attempt <= attempts; attempt++ {
    ...
    if delay *= 2; delay > 30*time.Second {
        delay = 30 * time.Second
    }
}
```
No `rand`/jitter anywhere → all runners that fail together retry in lock-step.
**Expected:** RULE_06 HIGH; suggest full-jitter. (Note: bounded to 5 attempts, single-runner —
Quorum may temper confidence; a partial match here is still correct behavior.)

### G5 — carissafarry/tag-me #17 → RULE_01 🔴
**Why:** A real distributed lock acquired with a **static** value `"1"` and released unconditionally:
```go
lockKey := fmt.Sprintf("qr_gen:%s:%s", ownerID.String(), objectID.String())
lockResult, err := s.redisCmd.SetNX(ctx, lockKey, "1", s.genTTL).Result()
...
defer s.redisCmd.Del(ctx, lockKey)   // deletes even a lock it no longer owns
```
No fencing token: if the holder pauses past `genTTL`, another worker acquires the lock and the
unconditional `Del` can delete *someone else's* lock.
**Expected:** RULE_01 CRITICAL; ideally also notes the unsafe release (`Del` with no token check).

### G6 — BasedHardware/omi #7801 → RULE_01 **control** 🟢
**Why (control):** This popular repo implements the lock **correctly** — it uses a per-acquire
token and checks it on release:
```python
token = secrets.token_hex(...)
acquired = r.set(f'{RUN_LOCK_KEY_PREFIX}{job_id}', token, nx=True, ex=RUN_LOCK_TTL_SECONDS)
...
def release_job_run_lock(job_id, token):  # compare-and-delete on token
```
The separate `r.set(f'{ONCE_KEY_PREFIX}...', '1', nx=True, ...)` keys use a static `'1'`, but those
are **idempotency/once markers** (existence check), not locks — `'1'` is correct there.
**Expected:** **No RULE_01 finding.** This tests Quorum's false-positive discipline — it must
distinguish a token-based lock and a dedup marker from a static-value lock.

---

## GitLab — 6 Merge Requests

| # | Target | Lang | State | Predicted rule(s) | Expected finding | Match? |
|---|---|---|---|---|---|:---:|
| L1 | [lilacashes/music-library-tools !16](https://gitlab.com/lilacashes/music-library-tools/-/merge_requests/16) | Python | merged | RULE_14 🟠 + RULE_06 🟠 | Flag the no-timeout upload + retry w/o backoff | ✅ verified 2026-06-11 |
| L2 | [gitlab-community … 35318847 !7](https://gitlab.com/gitlab-community/community-projects/2026-02-ai-hackathon/35318847/-/merge_requests/7) | Python | merged | RULE_10 🔴 | Flag the stock lost-update | |
| L3 | [gitlab-community … 35519097 !8](https://gitlab.com/gitlab-community/community-projects/2026-02-ai-hackathon/35519097/-/merge_requests/8) | Python | closed | RULE_14 🟠 | Flag the no-timeout payment poll | |
| L4 | [the-microservice-dungeon/…/robot !51](https://gitlab.com/the-microservice-dungeon/core-services/robot/-/merge_requests/51) | Kotlin | merged | RULE_09 🔴 | Flag the DB-tx + Kafka dual-write | |
| L5 | [ska-telescope/ska-dlm-client !2](https://gitlab.com/ska-telescope/ska-dlm-client/-/merge_requests/2) | Python | merged | RULE_06 🟠 | Flag the fixed `sleep(1)` retry | |
| L6 | [gitlab-org/gitlab-elasticsearch-indexer !91](https://gitlab.com/gitlab-org/gitlab-elasticsearch-indexer/-/merge_requests/91) | Go | merged | **RULE_07 control 🟢** | **No MEDIUM+ finding** — timing stimulus | |

### L1 — lilacashes/music-library-tools !16 → RULE_14 🟠 (+ RULE_06 🟠)
**Why:** A retry feature with two issues:
```python
response = requests.post(url, files=files, data=data)   # RULE_14: no timeout
...
except requests.exceptions.ConnectionError as error:
    if max_retry > 0:
        self.upload(name, max_retry=max_retry - 1)        # RULE_06: immediate retry, no backoff
if self.is_rate_limited(response) and max_retry > 0:
    sleep(int(response.json()['error']['retry_after']))   # deterministic, no jitter
```
**Expected:** RULE_14 HIGH on the `requests.post` (no `timeout=`). Bonus if it also raises RULE_06
on the backoff-free recursive retry (up to 100 deep).

**Observed (2026-06-11, Cloud Run `/demo`): ✅ exceeded prediction.** 3 findings:
- RULE_14 HIGH — HTTP request without a timeout (`media_tools/util/mixcloud.py:175`)
- RULE_06 HIGH — immediate retry on ConnectionError without backoff (`mixcloud.py:178`)
- RULE_06 MEDIUM — exponential backoff lacks jitter (`media_tools/backup_lastfm_data.py:55`) —
  a second RULE_06 in a *different file*, found via cross-file `semantic_code_search`.

### L2 — gitlab-community 35318847 !7 → RULE_10 🔴
**Why:** Classic read-modify-write with no row lock or CAS:
```python
def update_stock(self, product_id, quantity_sold):
    # SELECT ... stock ... .fetchone()
    new_stock = product["stock"] - quantity_sold
    self.db.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))
```
Two concurrent sales both read the same `stock`, both compute, both write → one deduction lost.
**Expected:** RULE_10 CRITICAL; suggest `SELECT … FOR UPDATE` or `SET stock = stock - ?`.
*(This file also has SQL-injection f-strings, outside Quorum's scope — ignore those.)*

### L3 — gitlab-community 35519097 !8 → RULE_14 🟠
**Why:** A status-polling loop calls an external API with no timeout:
```python
# Polls payment status until confirmed or timeout
response = requests.get(f"{PAYMENT_API}/{payment_id}/status")
```
If `PAYMENT_API` hangs, the poller blocks indefinitely and the hang cascades upstream.
**Expected:** RULE_14 HIGH on the missing `timeout=`.

### L4 — the-microservice-dungeon/core-services/robot !51 → RULE_09 🔴
**Why:** A Spring `@TransactionalEventListener` publishes to Kafka tied to a DB transaction, with
no outbox table or CDC:
```kotlin
@TransactionalEventListener(fallbackExecution = true, phase = TransactionPhase.BEFORE_COMMIT)
...
kafkaTemplate.send(record).addCallback({ ... })
```
`BEFORE_COMMIT` sends the event *before* the DB commits — if the commit then rolls back, the event
already fired (phantom event); if the send fails after commit, the event is lost. No atomicity.
**Expected:** RULE_09 CRITICAL; recommend a transactional outbox / `AFTER_COMMIT` + relay.

### L5 — ska-telescope/ska-dlm-client !2 → RULE_06 🟠
**Why:** A Kafka connect-retry loop with a fixed delay and no jitter:
```python
async def _start_consumer(consumer, max_retries: int = 5):
    while attempts < max_retries:
        try:
            await consumer.start(); return True
        except aiokafka.errors.KafkaError as e:
            ...
            await asyncio.sleep(1)     # fixed 1s, no backoff, no jitter
```
On a broker blip, all clients reconnect in lock-step every second.
**Expected:** RULE_06 HIGH; suggest exponential backoff with full jitter.

### L6 — gitlab-org/gitlab-elasticsearch-indexer !91 → RULE_07 **control** 🟢
**Why (control):** The added `time.Sleep` is an **intentional timing stimulus**, not a readiness wait:
```go
func TestClientTimeout(t *testing.T) {
    // server handler configured with "client_request_timeout": 1
    time.Sleep(3 * time.Second)   // forces the 1s client timeout to fire
}
```
The sleep exists *to trigger* the timeout being tested. Per Quorum's intentional-stimulus rule
(the containerd/nerdbox #218 lesson), this should **not** be flagged MEDIUM+.
**Expected:** **No RULE_07 MEDIUM finding** (at most a low-confidence note). Tests that Quorum
distinguishes a timing stimulus from a polling-substitute sleep.

---

## Scorecard

| Platform | Positives (expect a finding) | Controls (expect silence) | Rules exercised |
|---|---|---|---|
| GitHub | G1, G2, G3, G4, G5 | G6 | 01, 06, 08 |
| GitLab | L1, L2, L3, L4, L5 | L6 | 06, 07, 09, 10, 14 |

**Accuracy = (matched positives + correctly-silent controls) / 12.**
A strong run = all 10 positives flagged with the right rule **and** both controls left clean
(matching the "zero false positives" claim in the README).

### Tips for interpreting mismatches
- **Quorum flags a *different but valid* rule** → count as 🟡 partial, not a miss (real code often
  trips more than one rule; e.g. L1 is both RULE_14 and RULE_06).
- **Quorum flags a control (G6/L6)** → ⚠️ false positive; note the exact reasoning so the rule's
  intentional-stimulus / token-vs-static logic can be tightened.
- **Severity differs but rule matches** → still a match; severity is reasoning-layer judgment.

---

## Spare / backup targets

Extra verified candidates if any primary target changes or is closed (same verification method):

| Target | Rule | Evidence |
|---|---|---|
| [Allycathe/Tarea2_SistemasDistribuidos #1](https://github.com/Allycathe/Tarea2_SistemasDistribuidos/pull/1) (GitHub) | RULE_08 🔴 | `enable_auto_commit=True` (comment confirms it's intentional) |
| [gitlab-org/labkit !399](https://gitlab.com/gitlab-org/labkit/-/merge_requests/399) (GitLab) | RULE_07 control 🟢 | `time.Sleep(500 * time.Millisecond)` in an httpclient timing test |
| [gitlab-org/…/platform-insights/core !91](https://gitlab.com/gitlab-org/analytics-section/platform-insights/core/-/merge_requests/91) (GitLab) | RULE_07 🟡 | `time.Sleep(200ms)` in a NATS connection-pool test — verify readiness-wait vs stimulus |

> All URLs verified live on 2026-06-11. PR/MR state may change over time; the **diff** each row
> references is immutable, so the predicted finding stays valid even after merge/close.
