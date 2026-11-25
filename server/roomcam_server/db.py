"""Base de datos SQLite. Guarda solo cuentas, dispositivos, códigos de emparejamiento y la hora de los eventos."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    pw_hash     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id              TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('laptop', 'phone')),
    name            TEXT NOT NULL,
    refresh_hash    TEXT,
    refresh_expires REAL,
    alerts_enabled  INTEGER NOT NULL DEFAULT 1,
    push_sub        TEXT,
    created_at      REAL NOT NULL,
    last_seen       REAL
);
CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash   TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    laptop_id   TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    laptop_id   TEXT NOT NULL,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, ts);
"""


class Database:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def purge_old(self, retention_days: int) -> None:
        now = time.time()
        with self._lock:
            self.conn.execute("DELETE FROM events WHERE ts < ?", (now - retention_days * 86400,))
            self.conn.execute(
                "DELETE FROM pairing_codes WHERE expires_at < ? OR used_at IS NOT NULL", (now - 3600,)
            )
