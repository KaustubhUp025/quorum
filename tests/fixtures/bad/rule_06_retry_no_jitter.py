"""BAD: Retry loop with deterministic exponential backoff — no jitter."""

import time
import requests


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
            time.sleep(2 ** attempt)  # deterministic — thundering herd under load
    raise RuntimeError("unreachable")
