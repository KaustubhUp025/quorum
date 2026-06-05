+# Bad: Kafka consumer with no dead-letter queue — poison-pill blocks partition forever
+from kafka import KafkaConsumer
+
+consumer = KafkaConsumer(
+    "payments",
+    bootstrap_servers="kafka:9092",
+    enable_auto_commit=False,
+    group_id="payment-processor",
+)
+
+def process_payments():
+    for message in consumer:
+        try:
+            event = json.loads(message.value)
+            charge_customer(event["customer_id"], event["amount"])
+            consumer.commit()
+        except Exception as e:
+            logger.error("Failed to process message: %s", e)
+            # No DLQ routing — same message retried forever, partition stuck
