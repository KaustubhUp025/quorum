"""RULE_01 — Lock acquired without a fencing token."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_01",
    name="Fencing Token Missing",
    description=(
        "A distributed lock (Redis SETNX/SET NX, Redlock, ZooKeeper ephemeral node) is acquired "
        "but the lock *value* is a static string rather than a monotonically increasing token. "
        "If the lock-holding process pauses (GC, network partition) and the TTL expires, another "
        "process acquires the lock. When the original process resumes it has no way to detect the "
        "stale situation — the storage layer cannot distinguish the two writers without a fencing token."
    ),
    reference="Kleppmann (2016) — How to do distributed locking",
    reference_url="https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html",
    surface_keywords=[
        "setnx", "set nx", "set_nx", "redlock", "acquire_lock", "acquire lock",
        "trylock", "try_lock", "tryacquire", "reentrantlock",
        "nx=true", "nx=True", ", nx=", '"NX"', "'NX'",
    ],
    surface_patterns=[
        # Java/Go style: SET "key" "locked" NX
        r'SET\s+\S+\s+["\'](?:locked|1|true|acquired)["\']',
        # Java method call: redisClient.SET("key", "locked", "NX", ...)
        r'\.SET\s*\([^)]*["\'](?:locked|1|true|acquired)["\']',
        r'setNX\s*\(',
        r'setnx\s*\(',
        r'SET\s+.*\bNX\b',
        # Python redis-py: client.set(key, "locked", nx=True)
        r'\.set\s*\([^)]*\bnx\s*=\s*True',
        r'Redlock\s*\(',
        r'acquire\s*\(',
    ],
    search_query_templates=[
        "fencing token returned from lock acquisition",
        "lock value UUID or counter passed to storage write",
        "ifVersion or expectedVersion parameter in repository save",
        "monotonic token fencing distributed lock",
    ],
    reasoning_guidance=(
        "Check whether the lock *value* is a static string (e.g. 'locked', '1', 'true') "
        "versus a per-acquisition unique token (UUID, Snowflake ID, monotonic counter). "
        "Then check via search results whether that token is threaded through to downstream "
        "write operations as a conditional check (e.g. ifVersion, CAS). "
        "Flag CRITICAL if the token is static AND downstream writes have no conditional check."
    ),
)
