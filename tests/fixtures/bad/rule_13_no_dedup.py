+# Bad: Kafka consumer charges customer without any idempotency check
+# At-least-once delivery means restarts cause double charges
+import json
+from aiokafka import AIOKafkaConsumer
+
+async def consume_payment_events(consumer: AIOKafkaConsumer):
+    async for message in consumer:
+        event = json.loads(message.value)
+        # No check whether this message_id was already processed
+        await charge_customer(event["customer_id"], event["amount"])
+        await session.commit()
