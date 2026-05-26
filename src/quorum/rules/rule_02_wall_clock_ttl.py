"""RULE_02 — Lock TTL computed from wall-clock time."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_02",
    name="Wall-Clock TTL in Lock Lease",
    description=(
        "A lock TTL or lease expiry is calculated by adding a duration to the current wall-clock "
        "time (System.currentTimeMillis, time.Now(), datetime.now()). Wall clocks on distributed "
        "hosts can skew by hundreds of milliseconds; using wall-clock arithmetic for TTL means "
        "two nodes can both believe they hold the lock at the same instant. "
        "Correct TTLs are set as a relative duration directly in the lock command (e.g. PX 30000)."
    ),
    reference="antirez — Is Redlock safe? (2016)",
    reference_url="http://antirez.com/news/101",
    surface_keywords=[
        "currenttimemillis", "time.now()", "datetime.now()", "system.currenttime",
        "expiry", "expires_at", "expire_time", "lease_end",
    ],
    surface_patterns=[
        r'System\.currentTimeMillis\(\)',
        r'time\.Now\(\)',
        r'datetime\.now\(\)',
        r'expires?_?at\s*=',
        r'lease_?end\s*=',
        r'expiry\s*=.*[+\-]\s*\d',
    ],
    search_query_templates=[
        "lock TTL expiry wall clock calculation",
        "lease expiration time.Now plus duration",
        "lock timeout absolute timestamp",
    ],
    reasoning_guidance=(
        "Identify whether the TTL is calculated as `now + duration` (wall-clock, problematic) "
        "vs passed as a relative offset directly to the lock command (e.g. Redis PX, ZK sessionTimeout). "
        "Flag HIGH when wall-clock arithmetic is used inside lock acquisition or lease-renewal logic."
    ),
)
