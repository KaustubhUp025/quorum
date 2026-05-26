"""GOOD: Retry loop with full-jitter exponential backoff."""

import random
import time
import requests


def _jittered_sleep(attempt: int, base: float = 1.0, cap: float = 30.0) -> None:
    sleep = min(cap, base * (2 ** attempt))
    time.sleep(random.uniform(0, sleep))


def call_payment_api(payload: dict) -> dict:
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = requests.post("https://payments.example.com/charge", json=payload)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if attempt == max_retries - 1:
                raise
            _jittered_sleep(attempt)
    raise RuntimeError("unreachable")
