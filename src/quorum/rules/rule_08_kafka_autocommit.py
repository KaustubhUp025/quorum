"""RULE_08 — Kafka enable.auto.commit=true with manual acknowledgement."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_08",
    name="Kafka Auto-Commit With Manual Ack",
    description=(
        "A Kafka consumer sets enable.auto.commit=true (or relies on the default) while also "
        "performing manual message acknowledgement (consumer.commitSync, consumer.commitAsync, "
        "Acknowledgment.acknowledge(), ack.commit()). The auto-commit fires on a timer and "
        "commits offsets regardless of whether the message was processed successfully. "
        "This silently breaks at-least-once delivery: messages can be lost on consumer crash "
        "between auto-commit and processing completion."
    ),
    reference="Confluent — Kafka Consumer Offset Management",
    reference_url="https://docs.confluent.io/platform/current/clients/consumer.html#offset-management",
    surface_keywords=[
        # Config keys (Java properties / YAML — all languages use these strings)
        "enable.auto.commit", "auto.commit",
        # Python kafka-python / confluent-kafka
        "enable_auto_commit",
        # Java / Kotlin Spring
        "enableautocommit", "committedoffset", "commitsync", "commitasync",
        "acknowledgment", "ack.commit", "acknowledge()",
        "kafkalistener", "@kafkalistener",
        # Go sarama / kafka-go / confluent-kafka-go
        "autocommit", "CommitMessages", "commitoffsets",
        "AutoCommit",
        # kafka-go: non-zero CommitInterval enables periodic auto-commit (0 = disabled/manual)
        # JavaScript / TypeScript kafkajs
        "eachMessage", "eachBatch", "autoCommit",
        # Ruby ruby-kafka / karafka
        "auto_commit", "automatically_mark_as_processed",
        # .NET Confluent.Kafka
        "EnableAutoCommit", "Commit(",
    ],
    surface_patterns=[
        # Java / YAML config: enable.auto.commit=true
        r'enable\.auto\.commit\s*[=:]\s*["\']?true',
        # Python kafka-python: enable_auto_commit=True
        r'enable_auto_commit\s*=\s*True',
        # Java Spring / generic: enableAutoCommit: true / enableAutoCommit = true
        r'enableAutoCommit\s*[=:]\s*true',
        # Java manual commit
        r'consumer\.commitSync\s*\(',
        r'consumer\.commitAsync\s*\(',
        r'acknowledgment\.acknowledge\s*\(',
        r'ack\.commit\s*\(',
        # Go sarama: cfg.Consumer.Offsets.AutoCommit.Enable = true
        r'AutoCommit\.Enable\s*=\s*true',
        # Go kafka-go: non-zero CommitInterval enables periodic auto-commit
        # CommitInterval: 0 is the SAFE value (disables auto-commit) — do NOT flag that
        r'CommitInterval\s*:\s*(?!0\b)\w',
        # JavaScript kafkajs: { autoCommit: true } or autoCommit: false (either triggers investigation)
        r'autoCommit\s*:\s*(?:true|false)',
        # Ruby karafka: consumer.automatically_mark_as_processed = true
        r'automatically_mark_as_processed\s*=\s*true',
        # .NET Confluent.Kafka: EnableAutoCommit = true
        r'EnableAutoCommit\s*=\s*true',
    ],
    search_query_templates=[
        "Kafka consumer enable.auto.commit configuration",
        "manual commitSync or commitAsync in Kafka consumer",
        "Kafka acknowledgment.acknowledge manual offset management",
        "KafkaConsumer configuration auto commit disabled",
    ],
    reasoning_guidance=(
        "Check two things: (1) Is enable.auto.commit set to true anywhere in the diff or config? "
        "(2) Does the same consumer code (or per search results the consumer config) also call "
        "commitSync/commitAsync/acknowledge? "
        "Flag CRITICAL if both are true in the same consumer. "
        "Flag HIGH if auto-commit is true but manual ack is in a related consumer (per search results)."
    ),
)
