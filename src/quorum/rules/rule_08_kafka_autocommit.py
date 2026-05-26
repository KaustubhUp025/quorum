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
        "enable.auto.commit", "enableautocommit", "auto.commit",
        "enable_auto_commit",                # Python kafka-python / confluent-kafka
        "committedoffset", "commitsync", "commitasync",
        "acknowledgment", "ack.commit", "acknowledge()",
        "kafkalistener", "@kafkalistener",
    ],
    surface_patterns=[
        r'enable\.auto\.commit\s*[=:]\s*["\']?true',
        r'enable_auto_commit\s*=\s*True',    # Python style
        r'enableAutoCommit\s*[=:]\s*true',
        r'consumer\.commitSync\s*\(',
        r'consumer\.commitAsync\s*\(',
        r'acknowledgment\.acknowledge\s*\(',
        r'ack\.commit\s*\(',
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
