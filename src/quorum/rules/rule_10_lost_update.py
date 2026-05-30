"""RULE_10 — SELECT then UPDATE without FOR UPDATE / optimistic locking / CAS."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_10",
    name="Lost Update (SELECT without FOR UPDATE)",
    description=(
        "A method reads a value from the database (SELECT / query / find), computes a new value, "
        "then writes it back (UPDATE / save / replace) without either: "
        "(a) a pessimistic lock (`SELECT ... FOR UPDATE`, `WITH (UPDLOCK)`), or "
        "(b) an optimistic CAS (`UPDATE ... WHERE balance = old_value`, `@Version`, row version). "
        "Under concurrent writes two transactions can both read the same stale value, compute "
        "independently, and then both commit — the second overwriting the first. Classic lost update."
    ),
    reference="Kleppmann — Designing Data-Intensive Applications §7",
    reference_url="https://dataintensive.net/",
    surface_keywords=[
        # Specific read-then-compute signals (generic SELECT excluded — too broad)
        "fetchone", "fetchall", "find_one", "find_by",
        "get_balance", "get_count", "get_quantity", "get_stock",
        "set_balance", "set_quantity", "set_stock",
        # Mutable numeric fields — presence implies read-modify-write context
        "balance", "quantity", "stock",
        # Optimistic / pessimistic lock signals
        "for update", "FOR UPDATE", "with (updlock)", "WITH (UPDLOCK)",
        "@Version", "row_version", "optimistic", "compare_and_swap",
    ],
    surface_patterns=[
        # ORM row fetch + write (fetchone is the specific signal, not bare SELECT)
        r'\.fetchone\s*\(\s*\)',
        r'(?:find_by|find_one|get_by_id|query\.get)\s*\(',
        # Balance/counter arithmetic — the clearest lost-update signal
        r'(?:balance|quantity|stock)\s*[+\-*/]=',
        r'(?:get_balance|get_count|get_quantity)\s*\(',
        # Pessimistic lock (fires on safe code too — Gemini resolves)
        r'\bFOR\s+UPDATE\b',
        r'\bWITH\s*\(\s*UPDLOCK\s*\)',
        # SQL UPDATE on a numeric field
        r'UPDATE\b.*\bSET\b.*(?:balance|quantity|stock|count)\b',
    ],
    search_query_templates=[
        "FOR UPDATE pessimistic lock on this table or entity",
        "optimistic locking version field or @Version annotation",
        "compare and swap CAS WHERE balance equals old value",
        "row version or etag for concurrent update protection",
    ],
    reasoning_guidance=(
        "Find methods in the diff that read a mutable value (balance, count, quantity, stock) "
        "from the DB and then write an updated value back. "
        "Determine whether any concurrent-update guard is present: "
        "  (a) Pessimistic: `SELECT ... FOR UPDATE` / `WITH (UPDLOCK)` before the read, OR "
        "  (b) Optimistic: CAS in the WHERE clause (`WHERE balance = :old_balance`), OR "
        "  (c) ORM @Version / row version field checked before commit. "
        "Flag CRITICAL if the read-modify-write pattern is present AND none of (a/b/c) is found. "
        "Flag MEDIUM if a version field exists elsewhere but is not used in this specific update. "
        "Do NOT flag pure inserts (no prior read) or read-only SELECT queries."
    ),
)
