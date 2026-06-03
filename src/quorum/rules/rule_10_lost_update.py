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
        # Python SQLAlchemy / raw psycopg2
        "fetchone", "fetchall", "find_one", "find_by",
        "get_balance", "get_count", "get_quantity", "get_stock",
        "set_balance", "set_quantity", "set_stock",
        # Go (database/sql, GORM, sqlx)
        "QueryRow(", "db.Get(", "tx.Get(", "row.Scan(",
        "db.First(", "db.Find(", "gorm.First",
        # JavaScript / TypeScript (TypeORM, Mongoose, Prisma)
        "findById(", "findOne(", "findOneAndUpdate(",
        "prisma.findUnique(", "prisma.findFirst(",
        # Java / Kotlin (JPA, Hibernate, JDBC)
        "entitymanager.find(", "getById(", "findById(",
        "jpatemplate.queryforobject",
        # Ruby (ActiveRecord)
        "find_by(", ".find(",
        # Mutable numeric fields (all languages — clearest lost-update signal)
        "balance", "quantity", "stock",
        # Concurrency guards — presence of these means code is CORRECT; used by Gemini to PASS
        "for update", "FOR UPDATE", "with (updlock)", "WITH (UPDLOCK)",
        "@Version", "row_version", "optimistic", "compare_and_swap",
        "rowversion", "etag", "concurrencytoken",
    ],
    surface_patterns=[
        # Python: fetchone() or find_by/get_by_id
        r'\.fetchone\s*\(\s*\)',
        r'(?:find_by|find_one|get_by_id|query\.get)\s*\(',
        # Balance/counter arithmetic — clearest lost-update signal in any language
        r'(?:balance|quantity|stock)\s*[+\-*/]=',
        r'(?:get_balance|get_count|get_quantity)\s*\(',
        # SQL guards (correct code — Gemini will PASS, fires anyway to let it verify)
        r'\bFOR\s+UPDATE\b',
        r'\bWITH\s*\(\s*UPDLOCK\s*\)',
        # SQL UPDATE on a numeric field
        r'UPDATE\b.*\bSET\b.*(?:balance|quantity|stock|count)\b',
        # Go: row.Scan(&balance) or db.Get(&entity)
        r'row\.Scan\s*\(',
        r'(?:db|tx)\.(?:Get|First|QueryRow)\s*\(',
        # JavaScript: Model.findById() or repository.findOne()
        r'(?:findById|findOne|findUnique|findFirst)\s*\(',
        # Java JPA: entityManager.find() or @Version annotation
        r'entityManager\.find\s*\(',
        r'@Version\b',
        r'OptimisticLock(?:ing|Exception)',
        # Ruby ActiveRecord: User.find(id)
        r'\b(?:find|find_by)\s*\(',
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
