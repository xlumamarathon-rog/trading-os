"""Durable persistence (Wave 9): hash-chained JSONL audit + ledger store.

Production single-node backends that survive restarts WITHOUT requiring the DB
to be up (the DB backends on the VPS implement the same interfaces). Every
write is flushed+fsynced — a crash cannot lose an acknowledged row. Loading
verifies the hash chain; tampering is detected, not ignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

GENESIS = "0" * 64


class ChainTamperedError(RuntimeError):
    pass


class JsonlAuditLog:
    """Same interface as src/risk/pre_trade_gate.AuditLog, but durable."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: list = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        prev = GENESIS
        for i, line in enumerate(self.path.read_text().splitlines()):
            if not line.strip():
                continue
            row = json.loads(line)
            payload = json.dumps({k: v for k, v in row.items() if k != "hash"},
                                 sort_keys=True, default=str)
            if row.get("prev_hash") != prev or \
                    hashlib.sha256((prev + payload).encode()).hexdigest() != row.get("hash"):
                raise ChainTamperedError(f"audit chain broken at line {i + 1}")
            prev = row["hash"]
            self._rows.append(row)

    def append(self, row: dict) -> dict:
        prev = self._rows[-1]["hash"] if self._rows else GENESIS
        body = dict(row)
        body["ts"] = body.get("ts", time.time())
        body["prev_hash"] = prev
        payload = json.dumps({k: v for k, v in body.items() if k != "hash"},
                             sort_keys=True, default=str)
        body["hash"] = hashlib.sha256((prev + payload).encode()).hexdigest()
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._rows.append(body)
        return body

    def verify_chain(self) -> bool:
        try:
            JsonlAuditLog(self.path)
            return True
        except ChainTamperedError:
            return False

    @property
    def rows(self) -> list:
        return list(self._rows)


class JsonlKVStore:
    """Append-only entity store (prediction ledger rows, gate history)."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def load_all(self) -> list:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]
