"""BAD: Read-modify-write on account balance without FOR UPDATE or CAS."""

import sqlite3

conn = sqlite3.connect(":memory:")


def transfer_funds(from_account: str, to_account: str, amount: float) -> None:
    cur = conn.cursor()

    # Read current balances — no FOR UPDATE, no version check
    cur.execute("SELECT balance FROM accounts WHERE id = ?", (from_account,))
    from_balance = cur.fetchone()[0]

    cur.execute("SELECT balance FROM accounts WHERE id = ?", (to_account,))
    to_balance = cur.fetchone()[0]

    if from_balance < amount:
        raise ValueError("Insufficient funds")

    # Write back — no CAS WHERE clause, both concurrent txns will silently overwrite each other
    cur.execute(
        "UPDATE accounts SET balance = ? WHERE id = ?",
        (from_balance - amount, from_account),
    )
    cur.execute(
        "UPDATE accounts SET balance = ? WHERE id = ?",
        (to_balance + amount, to_account),
    )
    conn.commit()
