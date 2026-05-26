"""RULE_05 — @Transactional wrapping a distributed lock acquisition."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_05",
    name="@Transactional Wraps Distributed Lock",
    description=(
        "A Spring (or similar framework) @Transactional annotation is applied to a method "
        "that acquires a distributed lock. Spring's AOP proxy calls the @Transactional advice "
        "*before* entering the method body, meaning the DB transaction is committed *after* "
        "the method returns — but the distributed lock is released *inside* the method, before "
        "the transaction commits. This creates a window where another thread can acquire the lock, "
        "read stale data (the transaction hasn't committed yet), and act on it."
    ),
    reference="Leapcell — 10 Hidden Pitfalls of Redis Distributed Locks (2025)",
    reference_url="https://leapcell.io/blog/redis-distributed-lock-pitfalls",
    surface_keywords=[
        "@transactional", "transactional", "@transaction", "setnx", "set nx",
        "acquire_lock", "redislock", "redisson", "jedis",
    ],
    surface_patterns=[
        r'@Transactional',
        r'@transactional',
        r'@Transaction\b',
    ],
    search_query_templates=[
        "@Transactional method that also acquires Redis or distributed lock",
        "lock acquire inside Transactional annotated method",
        "Transactional and lock in same method",
    ],
    reasoning_guidance=(
        "Look for methods that are *both* annotated with @Transactional *and* call a lock "
        "acquisition (setnx, lock.acquire, redisson.getLock, etc.) in the same method body. "
        "The diff may add the @Transactional annotation or add the lock call — either creates the bug. "
        "Check search results for the other half. "
        "Flag CRITICAL when both exist in the same method. Flag HIGH when they exist on the same class."
    ),
)
