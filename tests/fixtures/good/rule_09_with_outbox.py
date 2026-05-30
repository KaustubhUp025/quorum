"""GOOD: Uses transactional outbox table — Debezium/CDC relay publishes the event."""

from sqlalchemy.orm import Session


class OrderService:
    def __init__(self, db: Session):
        self.db = db

    def place_order(self, order: dict) -> str:
        order_row = Order(**order)
        self.db.add(order_row)

        # Write event to the outbox table in the SAME transaction — no direct broker call
        outbox_entry = OutboxEvent(
            aggregate_type="Order",
            aggregate_id=order_row.id,
            event_type="OrderPlaced",
            payload={"order_id": order_row.id},
        )
        self.db.add(outbox_entry)
        self.db.commit()  # Both order + outbox committed atomically

        # Debezium/CDC relay picks up outbox_entry and publishes to Kafka
        return order_row.id


class Order:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.id = "order-123"


class OutboxEvent:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
