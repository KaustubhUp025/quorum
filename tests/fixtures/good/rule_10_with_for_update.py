"""GOOD: Read-modify-write with SELECT FOR UPDATE — prevents lost update."""

import psycopg2

conn = psycopg2.connect("dbname=mydb")


def transfer_funds(from_account: str, to_account: str, amount: float) -> None:
    with conn.cursor() as cur:
        # Pessimistic lock: FOR UPDATE prevents concurrent reads on these rows
        cur.execute(
            "SELECT balance FROM accounts WHERE id = %s FOR UPDATE",
            (from_account,),
        )
        from_balance = cur.fetchone()[0]

        cur.execute(
            "SELECT balance FROM accounts WHERE id = %s FOR UPDATE",
            (to_account,),
        )
        to_balance = cur.fetchone()[0]

        if from_balance < amount:
            raise ValueError("Insufficient funds")

        cur.execute(
            "UPDATE accounts SET balance = %s WHERE id = %s",
            (from_balance - amount, from_account),
        )
        cur.execute(
            "UPDATE accounts SET balance = %s WHERE id = %s",
            (to_balance + amount, to_account),
        )
    conn.commit()
