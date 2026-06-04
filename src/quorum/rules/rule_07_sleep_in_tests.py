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
        "1. SLEEP COORDINATION — Distinguish intentional timing stimuli from flaky polling:\n"
        "   - INTENTIONAL STIMULUS (do NOT flag or lower confidence to 70%): The sleep "
        "duration is a function parameter, e.g. `time.Sleep(listenDelay)` where `listenDelay` "
        "is passed in by the caller, or the sleep is guarded by `if delay > 0 { sleep(delay) }`. "
        "This pattern deliberately delays a server/goroutine to create a race scenario for the "
        "test — it is the stimulus, not a wait. Lower confidence to 70% and explain the distinction "
        "if you still flag it.\n"
        "   - FLAKY POLLING (flag MEDIUM): A hardcoded sleep is used to wait for async readiness, "
        "e.g. `time.Sleep(500 * time.Millisecond)` followed immediately by an assertion or a call "
        "to the system under test. This is too short on slow CI and wasteful on fast CI.\n"
        "   Flag LOW if a project wait utility (Awaitility, tenacity, errgroup) exists per search "
        "results and the developer just didn't use it.\n"
        "2. GOROUTINE SAFETY — Flag HIGH if a goroutine calls t.Error/t.Fatal/t.Log without "
        "the test body waiting for it (no WaitGroup, no channel drain, no t.Cleanup with a join). "
        "If the test exits early (e.g. t.Fatalf before a waitServer call), the goroutine will "
        "outlive the test and panic. Use get_file_contents to check whether the test "
        "always joins all goroutines before returning."
    ),
)
