+# Good: Kafka consumer routes failed messages to a dead-letter queue
+from kafka import KafkaConsumer, KafkaProducer
+
+consumer = KafkaConsumer(
+    "payments",
+    bootstrap_servers="kafka:9092",
+    enable_auto_commit=False,
+    group_id="payment-processor",
+)
+dlq_producer = KafkaProducer(bootstrap_servers="kafka:9092")
+
+def process_payments():
+    for message in consumer:
+        try:
+            event = json.loads(message.value)
+            charge_customer(event["customer_id"], event["amount"])
+            consumer.commit()
+        except Exception as e:
+            logger.error("Failed to process message: %s", e)
+            # Route poison pill to DLQ — partition unblocked, message preserved
+            dlq_producer.send(
+                "payments-dlq",
+                message.value,
+                headers=[("error", str(e).encode())],
+            )
+            consumer.commit()
