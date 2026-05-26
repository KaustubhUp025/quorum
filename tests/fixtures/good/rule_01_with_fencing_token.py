"""GOOD: Redis lock uses UUID fencing token, threaded through to the write."""

import uuid
import redis

client = redis.Redis()

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def process_order(order_id: str) -> None:
    lock_key = f"order:{order_id}"
    token = str(uuid.uuid4())
    acquired = client.set(lock_key, token, nx=True, px=30_000)
    if not acquired:
        raise RuntimeError("Could not acquire lock")
    try:
        _write_order_to_db(order_id, fencing_token=token)
    finally:
        client.eval(_RELEASE_SCRIPT, 1, lock_key, token)


def _write_order_to_db(order_id: str, fencing_token: str) -> None:
    pass
