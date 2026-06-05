"""RULE_12 — Dead Letter Queue (DLQ) not configured on a message consumer."""

from quorum.rules.base import Rule

RULE = Rule(
    id="RULE_12",
    name="Dead Letter Queue Missing",
    description=(
        "A Kafka, SQS, or RabbitMQ consumer processes messages without configuring a "
        "dead-letter queue or bounded retry limit. When a single poison-pill message "
        "causes an exception, the consumer retries indefinitely, blocking the entire "
        "partition or queue. The service appears 'stuck' in monitoring with no obvious "
        "cause and no alerting path. "
        "Fix: configure a DLQ (Kafka: set dead.letter.topic or use DeadLetterPublishingRecoverer; "
        "SQS: set RedrivePolicy with maxReceiveCount; RabbitMQ: x-dead-letter-exchange header)."
    ),
    reference="Confluent — Error Handling in Apache Kafka",
    reference_url="https://www.confluent.io/blog/error-handling-patterns-in-kafka/",
    surface_keywords=[
        # Python kafka-python / aiokafka / confluent
        "consumer.poll", "kafkaconsumer", "aiokafka", "aiokaflkaconsumer",
        "consumer.getmany", "consumer.getone",
        # Python SQS
        "receive_message", "sqs.receive",
        # Python RabbitMQ
        "basic_consume", "channel.basic",
        # Java/Kotlin Spring Kafka
        "@kafkalistener", "simplemessagelistenercontainer",
        "concurrentmessagelistenercontainer", "kafkatemplate",
        # Go
        "consumepartition", "partitionconsumer",
        # JS/TS kafkajs
        "eachmessage", "eachbatch", "consumer.run",
        # Generic DLQ config presence
        "dead_letter", "deadletter", "dlq", "redrive_policy", "maxreceivecount",
        "x-dead-letter",
    ],
    surface_patterns=[
        # Python kafka-python: constructor and bare poll loop
        r'KafkaConsumer\s*\(',
        r'consumer\.poll\s*\(',
        # Python kafka-python / aiokafka iterator style
        r'for\s+\w+\s+in\s+consumer\b',
        r'async\s+for\s+\w+\s+in\s+consumer\b',
        # aiokafka bare getmany / getone
        r'await\s+consumer\.getmany\s*\(',
        r'await\s+consumer\.getone\s*\(',
        # boto3 SQS receive
        r'sqs\.receive_message\s*\(',
        # pika RabbitMQ basic_consume
        r'channel\.basic_consume\s*\(',
        # Java Spring @KafkaListener
        r'@KafkaListener\b',
        r'SimpleMessageListenerContainer\b',
        r'ConcurrentMessageListenerContainer\b',
        # Go sarama ConsumePartition
        r'consumer\.ConsumePartition\s*\(',
        r'partitionConsumer\.Messages\s*\(\)',
        # JS/TS kafkajs consumer.run
        r'consumer\.run\s*\(\s*\{',
        r'eachMessage\s*:\s*async',
        r'eachBatch\s*:\s*async',
    ],
    search_query_templates=[
        "dead letter queue configuration dlq_url DeadLetterPublishingRecoverer",
        "error handler kafka listener setErrorHandler setDeadLetterPublishingRecoverer",
        "x-dead-letter-exchange redrive_policy MaxReceiveCount",
        "DeadLetterPublishingRecoverer OR dead.letter.topic OR dlq_producer",
    ],
    reasoning_guidance=(
        "For every consumer in the diff:\n"
        "1. Call semantic_code_search for "
        "'DeadLetterPublishingRecoverer OR dead.letter.topic OR dlq_producer' — "
        "if found anywhere in the codebase, PASS (DLQ is configured at the app level).\n"
        "2. Call semantic_code_search for "
        "'x-dead-letter-exchange OR redrive_policy OR MaxReceiveCount OR dlq_url' — "
        "if found, PASS.\n"
        "3. Check the diff for a catch/except block that sends failed messages to a DLQ "
        "topic/queue. If present, PASS.\n"
        "4. If no DLQ evidence found AND the consumer has a bare except/catch that swallows "
        "errors or just logs them without routing to a DLQ: FLAG HIGH.\n"
        "5. If the consumer processes financial data (payment, order, charge, invoice): "
        "upgrade to CRITICAL — unbounded retries will block payment processing.\n"
        "Do NOT flag consumers that have explicit retry count limits with fallback logic "
        "or that re-raise exceptions (letting the framework handle DLQ routing)."
    ),
)
