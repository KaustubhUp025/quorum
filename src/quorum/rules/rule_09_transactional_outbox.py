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
        # DB write signals — Python SQLAlchemy / generic ORM
        "session.add", "repo.save", "repository.save", ".save(", "db.add(",
        "session.commit", ".flush(", "bulk_save", "insert_one(", "insertone",
        # DB write signals — Go (database/sql, GORM, sqlx)
        "db.exec(", "tx.exec(", "db.create(", "tx.create(",
        "db.save(", "gorm.create", "sqlx.namedexec",
        # DB write signals — JavaScript / TypeScript (TypeORM, Prisma, Mongoose)
        "repository.save(", "await save(", "prisma.create",
        "model.create(", "findoneandupdate(",
        # DB write signals — Java / Kotlin (JPA, JDBC)
        "entitymanager", "jparepo", "save(", ".persist(",
        # Event publish signals (all languages)
        "publish(", "emit(", "produce(", "send_event(", "dispatch(",
        "eventbus", "event_bus", "messagebus", "message_bus",
        "publisher.publish", "producer.send", "channel.send",
        "producer.produce(", "writer.writemessages(",   # Go confluent / kafka-go
        "eventemitter.emit", "rabbitmq.publish",
        # CDC / outbox signals — presence of these is a GOOD sign (correct implementation)
        "outbox", "cdc", "debezium", "change_data_capture",
    ],
    surface_patterns=[
        # Python: session.add(...) / session.commit() + producer.send(...)
        r'session\.(?:add|commit)\s*\(',
        r'(?:publish|emit|produce|dispatch)\s*\(',
        # Java / Kotlin Spring
        r'(?:eventPublisher|eventBus|messageBus|producer)\.(?:publish|send|emit)\s*\(',
        r'@Transactional\b',
        # Go: db.Exec / tx.Exec or db.Create (GORM)
        r'(?:db|tx)\.(?:Exec|ExecContext|NamedExec|Create|Save)\s*\(',
        # Go: producer.Produce / writer.WriteMessages
        r'(?:producer|writer)\.(?:Produce|WriteMessages|Send)\s*\(',
        # JavaScript/TypeScript: await repository.save() or prisma.create()
        r'await\s+\w+\.(?:save|create|findOneAndUpdate)\s*\(',
        # .NET: context.SaveChanges() + publisher
        r'SaveChanges(?:Async)?\s*\(',
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
