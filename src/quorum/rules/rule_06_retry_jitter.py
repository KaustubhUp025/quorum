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
        # Python
        "retry", "retries", "time.sleep", "asyncio.sleep",
        # Java / Kotlin
        "thread.sleep", "backoff", "maxretries", "max_retries",
        # Go
        "time.sleep", "time.after", "time.newticker",
        # JavaScript / TypeScript
        "settimeout", "setinterval", "await sleep", "delaypromise",
        # Ruby
        "kernel.sleep",
        # Rust
        "tokio::time::sleep", "thread::sleep", "std::thread::sleep",
        # .NET / C#
        "task.delay", "thread.sleep",
        # Language-agnostic signals
        "wait", "attempt", "backoff", "exponential",
    ],
    surface_patterns=[
        # Java/Kotlin: Thread.sleep(n * 1000) or Thread.sleep(delay)
        r'Thread\.sleep\s*\(',
        # Python: time.sleep(5), time.sleep(x*2), time.sleep(variable)
        r'time\.sleep\s*\([^\)\n]{1,80}\)',
        # Python asyncio
        r'asyncio\.sleep\s*\([^\)\n]{1,80}\)',
        # Go: time.Sleep(5 * time.Second) or time.Sleep(backoff)
        r'time\.Sleep\s*\(',
        r'time\.After\s*\(',
        # JavaScript/TypeScript: setTimeout(fn, 1000), await new Promise(resolve => setTimeout(...))
        r'setTimeout\s*\([^,]+,\s*\d',
        r'setTimeout\s*\([^,]+,\s*\w',
        r'await\s+new\s+Promise[^;]{0,60}setTimeout',
        # Ruby: sleep 5, sleep(5), sleep(retry_count * 2)
        r'\bsleep\s+\d+\b',
        r'\bsleep\s*\(\s*(?:\d+|\w)',
        # Rust: tokio::time::sleep(Duration::from_secs(5))
        r'tokio::time::sleep\s*\(',
        r'thread::sleep\s*\(',
        # .NET: Thread.Sleep(5000), await Task.Delay(5000)
        r'Task\.Delay\s*\(',
        # Exponential doubling (all languages): interval *= 2, backoff ** attempt
        r'\w+\s*\*=\s*2\b',
        r'\*\*\s*(?:retry_count|attempt|retries|retry|n_retries|num_retries)\b',
        r'attempt\s*\*\s*\d+',
        r'2\s*\*\*\s*attempt',
        r'for\s+\w+\s+in\s+range.*retry',
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
