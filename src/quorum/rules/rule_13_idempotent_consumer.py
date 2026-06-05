"""RULE_13 — Idempotent consumer missing (no deduplication before stateful write)."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_13",
    name="Idempotent Consumer Missing",
    description=(
        "A message consumer performs a stateful write (DB insert/update, external API call, "
        "email send) without first checking whether this specific message has already been "
        "processed. Since all major brokers (Kafka, SQS, RabbitMQ, Google Pub/Sub) guarantee "
        "at-least-once delivery, consumer restarts and network partitions cause message "
        "redelivery. Without a processed-message-id check, every restart causes duplicate "
        "side effects: double charges, double inventory decrements, duplicate notifications. "
        "Fix: check a processed_events table (INSERT ... ON CONFLICT DO NOTHING), a Redis "
        "SET NX on message_id, or an idempotency key before executing the side effect."
    ),
    reference="Confluent — Exactly-Once Semantics; AWS — Handling Duplicate Messages",
    reference_url="https://www.confluent.io/blog/exactly-once-semantics-are-possible-heres-how-apache-kafka-does-it/",
    surface_keywords=[
        # Python kafka-python / aiokafka message body
        "message.value", "record.value", "msg.body",
        # Python SQS
        "sqs_message", "msg['body']", "message['body']",
        # Java
        "consumerrecord", "consumerpayload",
        # JS/TS kafkajs
        "eachmessage", "eachbatch",
        # State mutation verbs that signal a side effect
        "session.commit", "session.add", "repository.save",
        "db.execute", "db.insert", "db.run",
        "requests.post", "httpx.post", "aiohttp.clientsession",
        "send_email", "send_notification",
    ],
    surface_patterns=[
        # Python kafka-python record consumption
        r'message\.value\b',
        r'record\.value\s*\(\)',
        # pika RabbitMQ body
        r'\bmsg\.body\b',
        # boto3 SQS body
        r"sqs_message\s*\[\s*['\"]Body['\"]\s*\]",
        # Java ConsumerRecord generic
        r'ConsumerRecord\s*<',
        # JS/TS kafkajs eachMessage destructuring message
        r'eachMessage\s*:\s*async\s*\(\s*\{[^}]*message',
        # State mutation in what looks like a consumer context
        r'await\s+(?:db|session|repo|repository)\.\s*(?:save|insert|execute|create)\s*\(',
        r'(?:session|db)\s*\.\s*commit\s*\(',
    ],
    search_query_templates=[
        "processed_messages table processed_event_ids message_id unique constraint",
        "idempotency key check before processing deduplication",
        "redis SET NX setIfAbsent on message_id",
        "INSERT ON CONFLICT DO NOTHING OR INSERT IGNORE processed_events",
    ],
    reasoning_guidance=(
        "For every consumer handler in the diff that performs a stateful write:\n"
        "1. Call semantic_code_search for 'processed_messages OR processed_events OR "
        "message_id unique' — if a deduplication table exists anywhere in the codebase, PASS.\n"
        "2. Call semantic_code_search for 'SET NX message_id OR setIfAbsent OR "
        "INSERT ... ON CONFLICT DO NOTHING' — if found on message_id, PASS.\n"
        "3. Check the diff for an idempotency check: "
        "'if not db.exists(message_id)' / 'findByMessageId' / 'SET NX' / "
        "'ON CONFLICT DO NOTHING' before the write. If present, PASS.\n"
        "4. If no deduplication evidence found AND the handler contains a direct "
        "session.commit(), repository.save(), requests.post(), or similar state-mutating "
        "call without a prior message_id check: FLAG.\n"
        "5. Severity: CRITICAL if the handler processes financial data (charge, payment, "
        "order, invoice, balance); HIGH otherwise — duplicate emails, duplicate inventory "
        "decrements.\n"
        "Key distinction from RULE_08: RULE_08 is about offset-commit timing; "
        "RULE_13 is about whether the SAME message can be processed twice across restarts. "
        "Fixing RULE_08 does NOT fix RULE_13 — they are orthogonal."
    ),
)
