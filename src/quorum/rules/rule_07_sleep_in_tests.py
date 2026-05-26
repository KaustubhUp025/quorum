"""RULE_07 — Bare time.sleep() / Thread.sleep() in test code."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_07",
    name="Sleep in Tests",
    description=(
        "A test uses a bare sleep (Thread.sleep, time.sleep, asyncio.sleep) to wait for "
        "an async or distributed side-effect to become observable. Sleep-based waits are "
        "flaky by construction: too short on slow CI machines, wasteful on fast ones. "
        "The correct pattern is an explicit poll-with-timeout (Awaitility, tenacity, "
        "custom `wait_until`) or a test double that makes the side-effect synchronous."
    ),
    reference="Jepsen — Latency tolerance in distributed tests",
    reference_url="https://jepsen.io/analyses",
    surface_keywords=[
        "time.sleep", "thread.sleep", "asyncio.sleep", "sleep(",
        "test", "unittest", "pytest", "@test",
    ],
    surface_patterns=[
        r'time\.sleep\s*\(',
        r'Thread\.sleep\s*\(',
        r'asyncio\.sleep\s*\(',
        r'sleep\s*\(\s*\d+',
    ],
    search_query_templates=[
        "poll with timeout wait_until in tests",
        "Awaitility or tenacity usage in test suite",
        "test wait for async event without sleep",
    ],
    reasoning_guidance=(
        "Only flag this rule if the sleep appears inside a test file "
        "(path contains 'test', 'spec', 'it_', class name contains 'Test'). "
        "Flag MEDIUM for bare sleeps in tests. "
        "Flag LOW if a project-level wait utility (Awaitility, tenacity) exists per search results "
        "and the developer just didn't use it."
    ),
)
