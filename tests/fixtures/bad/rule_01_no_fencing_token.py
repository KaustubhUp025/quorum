"""BAD: Redis lock acquired with static string value — no fencing token."""

import redis

client = redis.Redis()


def process_order(order_id: str) -> None:
    lock_key = f"order:{order_id}"
    acquired = client.set(lock_key, "locked", nx=True, px=30_000)
    if not acquired:
        raise RuntimeError("Could not acquire lock")
    try:
        _write_order_to_db(order_id)
    finally:
        client.delete(lock_key)


def _write_order_to_db(order_id: str) -> None:
    pass
