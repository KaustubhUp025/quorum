"""BAD: Idempotency key generated server-side — retries will duplicate the operation."""

import uuid
from dataclasses import dataclass


@dataclass
class PaymentRequest:
    amount: int
    currency: str


class PaymentService:
    def charge(self, request: PaymentRequest) -> str:
        idempotency_key = str(uuid.uuid4())  # generated internally — wrong!
        return self._call_stripe(idempotency_key, request.amount, request.currency)

    def _call_stripe(self, idempotency_key: str, amount: int, currency: str) -> str:
        return "charge_id_123"
