"""RULE_07 — Test coordination safety: bare sleeps and unsynchronised goroutines."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_07",
    name="Unsafe Test Coordination",
    description=(
        "A test uses a bare sleep or spawns goroutines that call t.Error/t.Fatal/t.Log "
        "without synchronisation. Both make tests flaky: "
        "(1) Sleep-based waits are too short on slow CI machines and wasteful on fast ones. "
        "(2) Goroutines that outlive the test body and then call t.Error/t.Fatal/t.Log panic "
        "with 'testing: t.Errorf called after test finished' — a hard-to-reproduce race. "
        "Correct patterns: use channels to signal goroutine completion, defer a cleanup that "
        "waits for all goroutines, or use sync.WaitGroup."
    ),
    reference="Jepsen — Latency tolerance in distributed tests",
    reference_url="https://jepsen.io/analyses",
    surface_keywords=[
        "time.sleep", "thread.sleep", "asyncio.sleep", "sleep(",
        "test", "unittest", "pytest", "@test",
        # goroutine safety signals
        "t.error", "t.fatal", "t.log", "go func",
    ],
    surface_patterns=[
        r'time\.sleep\s*\(',
        r'Thread\.sleep\s*\(',
        r'asyncio\.sleep\s*\(',
        r'sleep\s*\(\s*\d+',
        # Go: goroutine in test file calling testing.T methods
        r'go\s+func\s*\([^)]*\)\s*\{[^}]*t\.\s*(?:Error|Fatal|Log|Errorf|Fatalf|Logf)',
        # Go: goroutine capturing t by reference without WaitGroup
        r'go\s+func\s*\(\s*\)\s*\{[^}]*t\.\s*(?:Error|Fatal)',
    ],
    search_query_templates=[
        "poll with timeout wait_until in tests",
        "Awaitility or tenacity usage in test suite",
        "test goroutine synchronisation WaitGroup channel",
        "t.Cleanup defer goroutine wait test",
    ],
    reasoning_guidance=(
        "Only flag this rule if the pattern appears inside a test file "
        "(path contains 'test', 'spec', '_test.go', class name contains 'Test').\n"
        "Two sub-checks:\n"
        "1. SLEEP COORDINATION — Flag MEDIUM for bare sleeps used to time goroutine readiness. "
        "Flag LOW if a project wait utility (Awaitility, tenacity, errgroup) exists per search "
        "results and the developer just didn't use it.\n"
        "2. GOROUTINE SAFETY — Flag HIGH if a goroutine calls t.Error/t.Fatal/t.Log without "
        "the test body waiting for it (no WaitGroup, no channel drain, no t.Cleanup with a join). "
        "If the test exits early (e.g. t.Fatalf before a waitServer call), the goroutine will "
        "outlive the test and panic. Use get_file_contents to check whether the test "
        "always joins all goroutines before returning."
    ),
)
