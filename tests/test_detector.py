"""Unit tests for the coordination surface detector (no API calls)."""

import pytest
from quorum.detector import detect_surfaces
from quorum.rules.registry import REGISTRY


class TestSurfaceDetector:
    def test_empty_diff_triggers_nothing(self):
        assert detect_surfaces("") == []

    def test_plain_crud_diff_triggers_nothing(self):
        diff = """
+def get_user(user_id: str):
+    return db.query("SELECT * FROM users WHERE id = ?", user_id)
"""
        assert detect_surfaces(diff) == []

    def test_setnx_triggers_rule_01(self, bad_fixture):
        diff = bad_fixture("rule_01_no_fencing_token.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_01" in ids

    def test_fencing_token_good_still_triggers_detector(self, good_fixture):
        # The detector is a pre-filter, not a judge — good code can trigger it
        diff = good_fixture("rule_01_with_fencing_token.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        # setnx is in the good fixture, so RULE_01 should still be triggered
        assert "RULE_01" in ids

    def test_retry_no_jitter_triggers_rule_06(self, bad_fixture):
        diff = bad_fixture("rule_06_retry_no_jitter.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_06" in ids

    def test_idempotency_key_triggers_rule_04(self, bad_fixture):
        diff = bad_fixture("rule_04_internal_idempotency_key.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_04" in ids

    def test_kafka_autocommit_triggers_rule_08(self):
        diff = """
+consumer = KafkaConsumer(
+    bootstrap_servers='localhost:9092',
+    enable_auto_commit=True,
+)
"""
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_08" in ids

    def test_transactional_annotation_triggers_rule_05(self):
        diff = """
+@Transactional
+public void processOrder(String orderId) {
+    redisClient.SET("lock:" + orderId, "locked", "NX", "PX", 30000);
+    orderRepo.save(order);
+}
"""
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_05" in ids
        assert "RULE_01" in ids  # SETNX with static value also fires

    def test_sleep_in_test_triggers_rule_07(self):
        diff = """
+def test_event_propagation():
+    publish_event("OrderCreated", {"id": "123"})
+    time.sleep(2)  # wait for consumer
+    assert db.get_order("123") is not None
"""
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_07" in ids

    def test_outbox_missing_triggers_rule_09(self, bad_fixture):
        diff = bad_fixture("rule_09_no_outbox.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_09" in ids

    def test_lost_update_triggers_rule_10(self, bad_fixture):
        diff = bad_fixture("rule_10_lost_update.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_10" in ids

    def test_for_update_good_still_triggers_detector(self, good_fixture):
        # Detector is a pre-filter — good code with SELECT/UPDATE still fires it
        diff = good_fixture("rule_10_with_for_update.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_10" in ids

    def test_kafka_consumer_no_dlq_triggers_rule_12(self, bad_fixture):
        diff = bad_fixture("rule_12_no_dlq.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_12" in ids

    def test_dlq_good_fixture_still_triggers_detector(self, good_fixture):
        # consumer.poll is still present in good fixture — detector fires, Gemini clears
        diff = good_fixture("rule_12_with_dlq.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_12" in ids

    def test_consumer_no_dedup_triggers_rule_13(self, bad_fixture):
        diff = bad_fixture("rule_13_no_dedup.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_13" in ids

    def test_requests_no_timeout_triggers_rule_14(self, bad_fixture):
        diff = bad_fixture("rule_14_no_timeout.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_14" in ids

    def test_httpx_with_timeout_still_triggers_detector(self, good_fixture):
        # httpx keyword still fires the pre-filter; Gemini clears it
        diff = good_fixture("rule_14_with_timeout.py")
        triggered = detect_surfaces(diff)
        ids = [r.id for r in triggered]
        assert "RULE_14" in ids

    def test_registry_fully_loaded(self):
        assert len(REGISTRY) == 14
        expected_ids = {f"RULE_{i:02d}" for i in range(1, 15)}
        assert set(REGISTRY.keys()) == expected_ids
