# Quorum Rule Reference

Detailed documentation for each rule, including the failure mode, detection approach, and canonical references.

---

## RULE_01 — Fencing Token Missing

**Severity:** 🔴 CRITICAL  
**Reference:** [Kleppmann (2016) — How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html)

### What it detects

A distributed lock (Redis SETNX/SET NX, Redlock, ZooKeeper ephemeral node) is acquired but the lock *value* is a static string (`"locked"`, `"1"`, `"true"`) rather than a monotonically increasing token.

### Why it matters

If the lock-holding process pauses (GC pause, network partition) and the TTL expires, another process acquires the lock. When the original process resumes, **both processes believe they hold the lock**. Without a fencing token threaded through to the storage layer, the storage has no way to reject the stale write.

### Wrong

```python
redis_client.set(f"order:{order_id}", "locked", nx=True, px=30_000)
write_order_to_db(order_id)  # no version check
```

### Right

```python
token = str(uuid.uuid4())
redis_client.set(f"order:{order_id}", token, nx=True, px=30_000)
write_order_to_db(order_id, if_version=token)  # storage rejects stale token
```

---

## RULE_02 — Wall-Clock TTL in Lock Lease

**Severity:** 🟠 HIGH  
**Reference:** [antirez — Is Redlock safe? (2016)](http://antirez.com/news/101)

### What it detects

A lock TTL or lease expiry is calculated by adding a duration to the current wall-clock time (`System.currentTimeMillis()`, `time.Now()`, `datetime.now()`).

### Why it matters

Wall clocks on distributed hosts can skew by hundreds of milliseconds. Using wall-clock arithmetic for TTL means two nodes can simultaneously believe the lock has and has not expired.

### Wrong

```python
expiry = datetime.now() + timedelta(seconds=30)
lock_store.set(key, token, expires_at=expiry)
```

### Right

```python
redis_client.set(key, token, nx=True, px=30_000)  # relative TTL, no wall clock
```

---

## RULE_03 — Saga Compensation Missing

**Severity:** 🔴 CRITICAL  
**Reference:** [microservices.io — Saga pattern](https://microservices.io/patterns/data/saga.html)

### What it detects

A saga adds a new forward step (e.g. `ship_order`, `charge_card`) but no compensating transaction (`cancel_shipment`, `refund_card`) exists anywhere in the project.

### Why it matters

Without a compensation, a failure in a later saga step leaves the system permanently in a partially-executed, inconsistent state with no rollback path.

### Wrong

```python
saga.step(ship_order)      # ← no cancel_shipment found anywhere
saga.step(send_email)      # compensation: cancel_confirmation_email ✓
```

### Right

```python
saga.step(ship_order, compensation=cancel_shipment)
saga.step(send_email, compensation=cancel_confirmation_email)
```

---

## RULE_04 — Idempotency Key Generated Internally

**Severity:** 🟠 HIGH  
**Reference:** [AWS Builders' Library — Making retries safe with idempotency tokens](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotency-tokens/)

### What it detects

An idempotency key is generated *inside* the request handler (`uuid.uuid4()`, `UUID.randomUUID()`) rather than received from the client.

### Why it matters

A server-generated key is unique per *request*, not per *client intent*. If the client retries after a network timeout, the server generates a new key and the operation executes twice — defeating the purpose of idempotency entirely.

### Wrong

```python
def charge(request: ChargeRequest) -> str:
    key = str(uuid.uuid4())          # new key on every call
    return stripe.charge(key, request.amount)
```

### Right

```python
def charge(request: ChargeRequest) -> str:
    key = request.idempotency_key    # client supplies and reuses on retry
    existing = db.find_charge(key)
    if existing:
        return existing.charge_id
    return stripe.charge(key, request.amount)
```

---

## RULE_05 — @Transactional Wraps Distributed Lock

**Severity:** 🔴 CRITICAL  
**Reference:** [Leapcell — 10 Hidden Pitfalls of Redis Distributed Locks (2025)](https://leapcell.io/blog/redis-distributed-lock-pitfalls)

### What it detects

A Spring (or equivalent) `@Transactional` annotation is applied to a method that also acquires a distributed lock.

### Why it matters

Spring AOP starts the DB transaction *before* the method body executes and commits it *after* the method returns. The distributed lock is released *inside* the method body — before the transaction commits. This creates a window where:

1. Lock is released
2. Transaction has not committed yet
3. Another thread acquires the lock and reads stale data

### Wrong

```java
@Transactional                                    // transaction starts
public void processOrder(String orderId) {
    redisClient.SET("lock:" + orderId, "locked");  // lock acquired
    Order order = orderRepo.findById(orderId);
    order.setStatus(PROCESSING);
    orderRepo.save(order);
    redisClient.DEL("lock:" + orderId);            // lock released ← HERE
}                                                  // transaction commits ← AFTER
```

### Right

Acquire the lock *outside* the `@Transactional` method, or use a distributed lock library that integrates with the transaction lifecycle.

---

## RULE_06 — Retry Without Jitter

**Severity:** 🟠 HIGH  
**Reference:** [AWS Architecture Blog — Exponential Backoff and Jitter (Marc Brooker)](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)

### What it detects

A retry loop uses a deterministic backoff: `time.sleep(2 ** attempt)`, `Thread.sleep(attempt * 1000)`.

### Why it matters

Under concurrent load, all callers that hit the same transient failure sleep for *identical* intervals and retry *simultaneously* — a thundering herd that can amplify a short blip into a sustained overload cascade.

### Wrong

```python
for attempt in range(5):
    try:
        return call_api()
    except Exception:
        time.sleep(2 ** attempt)   # all callers retry at the same time
```

### Right

```python
for attempt in range(5):
    try:
        return call_api()
    except Exception:
        cap = min(30, 1 * (2 ** attempt))
        time.sleep(random.uniform(0, cap))   # full jitter
```

---

## RULE_07 — Sleep in Tests

**Severity:** 🟡 MEDIUM  
**Reference:** [Jepsen — Latency tolerance in distributed tests](https://jepsen.io/analyses)

### What it detects

A test file uses a bare `time.sleep()` / `Thread.sleep()` to wait for an async or distributed side-effect.

### Why it matters

Sleep-based waits are flaky: too short on slow CI machines (test fails intermittently), wasteful on fast ones (test suite bloated). They are the root cause of the 3.5% CI failure rate reported by Google (Hoang & Berding, FTW 2024).

### Wrong

```python
def test_event_propagation():
    publish("OrderCreated", {"id": "123"})
    time.sleep(2)                              # flaky on slow CI
    assert db.find_order("123") is not None
```

### Right

```python
def test_event_propagation():
    publish("OrderCreated", {"id": "123"})
    wait_until(lambda: db.find_order("123"), timeout=10)   # polls with timeout
    assert db.find_order("123") is not None
```

---

## RULE_08 — Kafka Auto-Commit With Manual Ack

**Severity:** 🔴 CRITICAL  
**Reference:** [Confluent — Kafka Consumer Offset Management](https://docs.confluent.io/platform/current/clients/consumer.html#offset-management)

### What it detects

A Kafka consumer sets `enable.auto.commit=true` (or the equivalent `enable_auto_commit=True` in Python) while also calling `commitSync`, `commitAsync`, `acknowledgment.acknowledge()`, or equivalent manual commit operations.

### Why it matters

`enable.auto.commit` fires on a timer and commits offsets *regardless of whether processing succeeded*. This silently breaks at-least-once delivery guarantees: messages can be lost if the consumer crashes between the auto-commit firing and processing completing.

### Wrong

```python
consumer = KafkaConsumer(
    "orders",
    enable_auto_commit=True,         # fires every 5s by default
)
for msg in consumer:
    process(msg)
    consumer.commit()                # also manually committing — conflict
```

### Right

```python
consumer = KafkaConsumer(
    "orders",
    enable_auto_commit=False,        # disable auto-commit
)
for msg in consumer:
    process(msg)
    consumer.commit()                # commit only after successful processing
```
