from __future__ import annotations

import hashlib
import secrets
import time


class ApprovalStore:
    """Issue and consume one-time approvals for exact commands."""

    def __init__(self, ttl_seconds: float = 600) -> None:
        self._ttl_seconds = ttl_seconds
        self._records: dict[str, dict[str, object]] = {}

    def create(self, task_id: str, command: str, now: float | None = None) -> dict[str, object]:
        issued_at = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        token_hash = self._digest(token)
        while token_hash in self._records:
            token = secrets.token_urlsafe(32)
            token_hash = self._digest(token)

        expires_at = issued_at + self._ttl_seconds
        self._records[token_hash] = {
            "task_id_hash": self._digest(task_id),
            "command_hash": self._digest(command),
            "expires_at": expires_at,
            "consumed": False,
        }
        return {
            "token": token,
            "task_id": task_id,
            "command": command,
            "expires_at": expires_at,
        }

    def consume(self, token: str, task_id: str, command: str, now: float | None = None) -> bool:
        record = self._records.get(self._digest(token))
        if record is None or record["consumed"]:
            return False
        current_time = time.time() if now is None else now
        if current_time >= float(record["expires_at"]):
            return False
        if record["task_id_hash"] != self._digest(task_id):
            return False
        if record["command_hash"] != self._digest(command):
            return False
        record["consumed"] = True
        return True

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
