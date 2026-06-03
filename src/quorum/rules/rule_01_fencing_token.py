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
        # Redis commands (all languages)
        "setnx", "set nx", "set_nx", "redlock",
        '"NX"', "'NX'", ", nx=", "nx=true", "nx=True",
        # Java / Kotlin
        "acquire_lock", "acquire lock", "trylock", "try_lock",
        "tryacquire", "reentrantlock",
        # Go (go-redis, redigo)
        "setnx(", "setnxex(", "client.set(", "rdb.set(",
        "lock.obtain", "redsync",
        # JavaScript / TypeScript (ioredis, node-redis)
        "redlock", "resource.acquire", "lock.acquire",
        # Ruby (redis-rb, redlock-rb)
        "redis.setnx", "redis.set",
        # .NET (StackExchange.Redis)
        "stringset(", "locktake(",
        # Zookeeper / etcd patterns
        "createephemeral", "trylock", "campaign(",
    ],
    surface_patterns=[
        # Redis CLI / any language: SET key "locked" NX
        r'SET\s+\S+\s+["\'](?:locked|1|true|acquired)["\']',
        r'SET\s+.*\bNX\b',
        # Java/Go: .SET("key", "locked", ...) or setNX(key, value)
        r'\.SET\s*\([^)]*["\'](?:locked|1|true|acquired)["\']',
        r'[Ss]et[Nn][Xx]\s*\(',
        # Python redis-py: client.set(key, "locked", nx=True)
        r'\.set\s*\([^)]*\bnx\s*=\s*True',
        # Go go-redis: client.SetNX(ctx, key, "locked", ttl)
        r'\.SetNX\s*\(',
        # Go: client.Set(ctx, key, "locked", 0) — static value
        r'\.Set\s*\(ctx,\s*\w+,\s*["\'](?:locked|1|true|acquired)["\']',
        # JavaScript/TS ioredis: redis.set(key, 'LOCKED', 'NX') or {NX: true}
        r"""[cC]lient\.set\s*\([^,]+,\s*['"][^'"]{1,20}['"],\s*['"]NX['"]""",
        r"""\.set\s*\([^)]*\{\s*[Nn][Xx]\s*:\s*true""",
        # Ruby: redis.set(key, 'locked', nx: true)
        r"""redis\.set\s*\([^)]*nx:\s*true""",
        # .NET StackExchange.Redis: db.StringSet(key, "locked", when: When.NotExists)
        r'StringSet\s*\([^)]*When\.NotExists',
        r'LockTake\s*\(',
        # Redlock (multi-language library)
        r'Redlock\s*\(',
        r'redlock\s*\.',
        # Generic acquire (catch-all, safe with lock keyword above)
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
