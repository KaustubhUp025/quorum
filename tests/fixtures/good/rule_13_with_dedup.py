+# Good: Kafka consumer uses processed_events table to deduplicate messages
+import json
+from aiokafka import AIOKafkaConsumer
+
+async def consume_payment_events(consumer: AIOKafkaConsumer):
+    async for message in consumer:
+        event = json.loads(message.value)
+        message_id = event.get("event_id") or str(message.offset)
+        async with db.transaction():
+            # Deduplication check — idempotent via processed_events table
+            exists = await db.fetchone(
+                "SELECT 1 FROM processed_events WHERE message_id = $1", message_id
+            )
+            if not exists:
+                await charge_customer(event["customer_id"], event["amount"])
+                await db.execute(
+                    "INSERT INTO processed_events VALUES ($1)", message_id
+                )
+        await consumer.commit()
