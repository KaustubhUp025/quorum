"""GOOD: Idempotency key supplied by the caller."""

from dataclasses import dataclass


@dataclass
class PaymentRequest:
    amount: int
    currency: str
    idempotency_key: str  # client provides this


class PaymentService:
    def __init__(self, db) -> None:
        self._db = db

    def charge(self, request: PaymentRequest) -> str:
        # Deduplication check inside the same transaction as the write
        existing = self._db.find_charge(request.idempotency_key)
        if existing:
            return existing.charge_id
        return self._call_stripe(request.idempotency_key, request.amount, request.currency)

    def _call_stripe(self, idempotency_key: str, amount: int, currency: str) -> str:
        return "charge_id_123"
