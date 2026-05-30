"""RULE_09 — DB write and event publish in same method without outbox pattern."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_09",
    name="Transactional Outbox Missing",
    description=(
        "A service method writes to the database AND publishes a domain event in the same "
        "execution path without a transactional outbox table or Change Data Capture (CDC) relay. "
        "If the event broker is unavailable after the DB commit, the event is silently lost. "
        "If the DB write rolls back after a successful publish, a phantom event triggers downstream "
        "consumers. Either failure mode causes permanent data inconsistency across services."
    ),
    reference="microservices.io — Transactional Outbox pattern",
    reference_url="https://microservices.io/patterns/data-management/transactional-outbox.html",
    surface_keywords=[
        # DB write signals
        "session.add", "repo.save", "repository.save", ".save(", "db.add(",
        "session.commit", ".flush(", "bulk_save", "insert_one(", "insertone",
        # Event publish signals
        "publish(", "emit(", "produce(", "send_event(", "dispatch(",
        "eventbus", "event_bus", "messagebus", "message_bus",
        "publisher.publish", "producer.send", "channel.send",
        # CDC / outbox signals — presence of these in the same file is OK
        "outbox", "cdc", "debezium", "change_data_capture",
    ],
    surface_patterns=[
        # Both a save/commit AND a publish/emit appear in the same diff hunk
        r'(?:session\.add|repo\.save|\.save\s*\(|db\.add\s*\()',
        r'(?:publish|emit|produce|dispatch)\s*\(',
        r'(?:eventPublisher|eventBus|messageBus|producer)\.(?:publish|send|emit)\s*\(',
        r'@Transactional\b.*\n(?:.*\n){0,20}.*\.publish\s*\(',
        # Python: session.add(...) ... session.commit() ... producer.send(...)
        r'session\.commit\s*\(\s*\).*\n(?:.*\n){0,10}.*\.send\s*\(',
    ],
    search_query_templates=[
        "outbox table insert for event relay",
        "transactional outbox pattern CDC debezium relay",
        "outbox OR inbox table in database schema",
        "change data capture event relay for this service",
    ],
    reasoning_guidance=(
        "Identify methods in the diff that BOTH write to a DB (session.add, repo.save, .flush, "
        "session.commit, insert_one) AND publish an event (eventPublisher.publish, producer.send, "
        "eventBus.emit) in the same code path. "
        "Then use search to check whether an outbox table, a Debezium connector, or a CDC relay "
        "process exists anywhere in the project. "
        "Flag CRITICAL if the method does both DB write + event publish AND no outbox/CDC pattern "
        "is found in the project. "
        "Flag MEDIUM if an outbox table exists but the method bypasses it (writes directly to broker). "
        "Do NOT flag if the method only writes to an outbox table (not the broker directly)."
    ),
)
