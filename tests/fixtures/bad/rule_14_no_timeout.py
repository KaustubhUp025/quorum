+# Bad: HTTP calls in a service handler with no timeout — blocks indefinitely
+import requests
+
+def get_user_profile(user_id: str) -> dict:
+    # No timeout — if user-service hangs, this goroutine/thread hangs forever
+    response = requests.get(f"http://user-service/api/users/{user_id}")
+    return response.json()
+
+def notify_order_shipped(order_id: str, email: str):
+    # No timeout on external notification service call
+    result = requests.post(
+        "http://notification-service/send",
+        json={"order_id": order_id, "email": email},
+    )
+    return result.status_code
