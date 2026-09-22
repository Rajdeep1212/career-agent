"""Durable idempotency receipts stored beside LangGraph checkpoints."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


class TurnConflictError(ValueError):
    pass


class TurnInProgressError(RuntimeError):
    pass


class TurnReceiptStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("""CREATE TABLE IF NOT EXISTS chat_turn_receipts (
            thread_id TEXT NOT NULL,
            turn_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT,
            PRIMARY KEY(thread_id, turn_id)
        )""")
        connection.commit()
        return connection

    @staticmethod
    def request_hash(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def claim(self, thread_id: str, turn_id: str, payload: str) -> dict | None:
        digest = self.request_hash(payload)
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM chat_turn_receipts WHERE thread_id=? AND turn_id=?",
                (thread_id, turn_id),
            ).fetchone()
            if row:
                if row["request_hash"] != digest:
                    raise TurnConflictError("This turn ID was already used for different input.")
                if row["status"] == "complete":
                    return json.loads(row["response_json"])
                raise TurnInProgressError("This turn is already being processed.")
            try:
                conn.execute(
                    "INSERT INTO chat_turn_receipts VALUES (?, ?, ?, 'processing', NULL)",
                    (thread_id, turn_id, digest),
                )
            except sqlite3.IntegrityError as exc:
                raise TurnInProgressError("This turn is already being processed.") from exc
        return None

    def complete(self, thread_id: str, turn_id: str, response: dict) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE chat_turn_receipts SET status='complete', response_json=? "
                "WHERE thread_id=? AND turn_id=? AND status='processing'",
                (json.dumps(response, separators=(",", ":")), thread_id, turn_id),
            )

    def abandon(self, thread_id: str, turn_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM chat_turn_receipts WHERE thread_id=? AND turn_id=? AND status='processing'",
                (thread_id, turn_id),
            )
