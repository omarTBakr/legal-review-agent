"""Durable claims for retry-safe upload requests."""

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class SubmissionClaim:
    key: str
    fingerprint: str
    task_id: str
    pdf_keys: list[str]
    pdf_bucket: str
    project_id: str
    is_new: bool = False


class IdempotencyConflict(ValueError):
    """Raised when a key is reused for a different request."""


class IdempotencyStore:
    """SQLite-backed request claims; safe across API threads and restarts."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS submission_claims (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    pdf_keys TEXT NOT NULL,
                    pdf_bucket TEXT NOT NULL,
                    project_id TEXT NOT NULL
                )
                """)

    def claim(self, key: str, fingerprint: str) -> SubmissionClaim:
        task_id = uuid.uuid4().hex[:8]
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO submission_claims VALUES (?, ?, ?, ?, ?, ?)",
                    (key, fingerprint, task_id, "[]", "", ""),
                )
        except sqlite3.IntegrityError:
            existing = self._read(key)
            if existing.fingerprint != fingerprint:
                raise IdempotencyConflict("Idempotency-Key was already used for a different request") from None
            return SubmissionClaim(
                existing.key,
                existing.fingerprint,
                existing.task_id,
                existing.pdf_keys,
                existing.pdf_bucket,
                existing.project_id,
                is_new=False,
            )

        return SubmissionClaim(key, fingerprint, task_id, [], "", "", is_new=True)

    def complete(self, key: str, pdf_keys: list[str], pdf_bucket: str, project_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE submission_claims SET pdf_keys = ?, pdf_bucket = ?, project_id = ? WHERE idempotency_key = ?",
                (json.dumps(pdf_keys), pdf_bucket, project_id, key),
            )

    def remove(self, key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM submission_claims WHERE idempotency_key = ?", (key,))

    def _read(self, key: str) -> SubmissionClaim:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT idempotency_key, fingerprint, task_id, pdf_keys, pdf_bucket, project_id "
                "FROM submission_claims WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return SubmissionClaim(row[0], row[1], row[2], json.loads(row[3]), row[4], row[5])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


@lru_cache(maxsize=8)
def get_store(path: Path) -> IdempotencyStore:
    """
    The store for a database path, built once.

    Constructing one runs CREATE TABLE and opens a connection, and the routes
    were doing that per request. Cached by path, so a test pointing at its own
    tmp_path still gets its own store.
    """
    return IdempotencyStore(path)


async def fingerprint_uploads(uploads: list, values: dict[str, str]) -> str:
    """Hash form values and upload bytes without consuming the upload streams."""
    digest = hashlib.sha256()
    for name, value in sorted(values.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(value.encode())
    for upload in uploads:
        digest.update((upload.filename or "").encode())
        digest.update(b"\0")
        while chunk := await upload.read(1024 * 1024):
            digest.update(chunk)
        await upload.seek(0)
    return digest.hexdigest()
