"""BAD: DB write and event publish in same method — no outbox or CDC."""

from sqlalchemy.orm import Session
from kafka import KafkaProducer
import json

producer = KafkaProducer(bootstrap_servers="kafka:9092")


class OrderService:
    def __init__(self, db: Session):
        self.db = db

    def place_order(self, order: dict) -> str:
        # Write to the database
        order_row = Order(**order)
        self.db.add(order_row)
        self.db.commit()  # DB committed

        # Directly publish to Kafka — if this fails, DB is committed but event is lost
        event = {"type": "OrderPlaced", "order_id": order_row.id}
        producer.send("order-events", json.dumps(event).encode())

        return order_row.id


class Order:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.id = "order-123"
