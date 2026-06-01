"""RULE_06 — Retry backoff without jitter (thundering-herd risk)."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_06",
    name="Retry Without Jitter",
    description=(
        "A retry loop uses a deterministic (non-random) backoff: "
        "Thread.sleep(attempt * 1000), time.sleep(2**n), asyncio.sleep(constant). "
        "Under concurrent load, all callers that hit the same transient failure will sleep "
        "for identical intervals and retry simultaneously — a thundering herd that can amplify "
        "a short blip into a sustained overload. Adding random jitter (e.g. sleep = base * 2^n + random(0, base)) "
        "spreads retries across the interval."
    ),
    reference="AWS Architecture Blog — Exponential Backoff and Jitter (Marc Brooker)",
    reference_url="https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/",
    surface_keywords=[
        "retry", "retries", "thread.sleep", "time.sleep", "asyncio.sleep",
        "backoff", "wait", "attempt", "maxretries", "max_retries",
    ],
    surface_patterns=[
        # Java-style deterministic sleep
        r'Thread\.sleep\s*\(\s*\w+\s*\*',
        # Python time.sleep with a literal or simple multiply: sleep(5), sleep(x*2)
        r'time\.sleep\s*\(\s*(?:\d+|\w+\s*\*\s*\d+)\s*\)',
        # asyncio.sleep with a literal or simple multiply
        r'asyncio\.sleep\s*\(\s*(?:\d+|\w+\s*\*\s*\d+)\s*\)',
        # Any time.sleep with a variable or expression — broad but safe combined
        # with retry/backoff keywords above (catches: sleep(delay), sleep(min(...)))
        r'time\.sleep\s*\([^\)\n]{1,80}\)',
        # Exponential doubling patterns: interval *= 2, BASE_DELAY ** retry_count
        r'\w+\s*\*=\s*2\b',
        r'\*\*\s*(?:retry_count|attempt|retries|retry|n_retries|num_retries)\b',
        r'for\s+\w+\s+in\s+range.*retry',
        r'attempt\s*\*\s*\d+',
        r'2\s*\*\*\s*attempt',
    ],
    search_query_templates=[
        "retry backoff calculation with random jitter",
        "exponential backoff random sleep interval",
        "retry helper utility function with jitter",
    ],
    reasoning_guidance=(
        "Identify every retry loop in the diff. For each, check whether the sleep interval "
        "includes a random component (Math.random(), random.uniform, random.randint, jitter). "
        "Flag HIGH for deterministic-only backoff. "
        "Flag MEDIUM if there is a retry utility elsewhere in the project (per search results) "
        "that already has jitter — the new code should use it instead."
    ),
)
