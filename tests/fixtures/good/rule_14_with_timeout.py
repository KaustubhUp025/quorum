+# Good: HTTP calls with explicit timeout configured on the client
+import httpx
+
+_HTTP_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=5.0))
+
+async def get_user_profile(user_id: str) -> dict:
+    # Timeout configured on the client — slow upstream cannot block indefinitely
+    response = await _HTTP_CLIENT.get(f"http://user-service/api/users/{user_id}")
+    return response.json()
+
+async def notify_order_shipped(order_id: str, email: str):
+    # Explicit timeout=5.0 — request will fail fast if notification service is slow
+    response = await _HTTP_CLIENT.post(
+        "http://notification-service/send",
+        json={"order_id": order_id, "email": email},
+        timeout=5.0,
+    )
+    return response.status_code
