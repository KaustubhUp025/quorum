"""RULE_04 — Idempotency key generated inside the request handler."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_04",
    name="Idempotency Key Generated Internally",
    description=(
        "An idempotency key is generated *inside* the request handler "
        "(e.g. UUID.randomUUID(), uuid.uuid4(), crypto.randomUUID()) rather than being "
        "received from the calling client. A server-generated key is unique per *request* "
        "not per *client intent* — if the client retries after a network timeout, a new key "
        "is generated and the operation executes twice. "
        "The key must come from the client so retries carry the same key."
    ),
    reference="AWS Builders' Library — Making retries safe with idempotency tokens",
    reference_url="https://aws.amazon.com/builders-library/making-retries-safe-with-idempotency-tokens/",
    surface_keywords=[
        "idempotency", "idempotent", "idempotencykey", "idempotency_key",
        "uuid.randomuuid", "uuid4()", "crypto.randomuuid", "nanoid()",
        "requestid", "request_id",
    ],
    surface_patterns=[
        r'UUID\.randomUUID\(\)',
        r'uuid\.uuid4\(\)',
        r'uuid4\(\)',
        r'crypto\.randomUUID\(\)',
        r'nanoid\(\)',
        r'idempotency_?key\s*=.*(?:uuid|random|generate)',
    ],
    search_query_templates=[
        "idempotency key received from request header or body",
        "client-supplied idempotency token in request handler",
        "deduplication check before write using idempotency key",
        "idempotency key lookup in database before processing",
    ],
    reasoning_guidance=(
        "Determine whether the idempotency key is *generated* (random call inside the handler) "
        "or *received* (read from request header/body). "
        "Also check search results: is there a deduplication lookup before the write? "
        "Is the deduplication check inside the same database transaction as the write? "
        "Flag HIGH for server-generated keys. Flag MEDIUM if key is received but dedup check "
        "happens outside the transaction."
    ),
)
