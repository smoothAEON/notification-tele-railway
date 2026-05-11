"""SQLite persistence for alerts and lifecycle events."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from alert_mcp.instruments import normalize_instrument
from alert_mcp.models import Alert, AlertDirection, parse_utc, to_utc_iso

CURRENT_STATUSES = ("PENDING", "FIRING")


class AlertStore:
    """Small SQLite repository for one-shot price alerts."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument TEXT NOT NULL,
                    target_price REAL NOT NULL CHECK(target_price > 0),
                    direction TEXT NOT NULL CHECK(direction IN ('above', 'below')),
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'FIRING', 'FIRED', 'CANCELLED')),
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    fired_at TEXT,
                    trigger_price REAL,
                    last_error TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status_instrument ON alerts(status, instrument)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_fired_at ON alerts(fired_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id INTEGER,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(alert_id) REFERENCES alerts(id)
                )
                """
            )

    def recover_firing_alerts(self) -> int:
        """Move stale FIRING alerts back to PENDING after process restart."""

        now = to_utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM alerts WHERE status = 'FIRING' ORDER BY id").fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE alerts
                    SET status = 'PENDING', updated_at = ?, last_error = ?
                    WHERE id = ?
                    """,
                    (now, "Recovered from stale FIRING state on startup.", row["id"]),
                )
                self._insert_event(conn, row["id"], "RECOVERED", {"previous_status": "FIRING"})
            conn.execute("COMMIT")
            return len(rows)

    def create_alert(
        self,
        *,
        instrument: str,
        target_price: float,
        direction: AlertDirection,
        note: str | None = None,
    ) -> Alert:
        instrument = normalize_instrument(instrument)
        target = float(target_price)
        if target <= 0:
            raise ValueError("target_price must be greater than zero.")
        if direction not in {"above", "below"}:
            raise ValueError("direction must be 'above' or 'below'.")

        now = to_utc_iso()
        clean_note = note.strip() if isinstance(note, str) and note.strip() else None
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT INTO alerts (
                    instrument, target_price, direction, status, note,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'PENDING', ?, ?, ?)
                """,
                (instrument, target, direction, clean_note, now, now),
            )
            alert_id = int(cursor.lastrowid)
            self._insert_event(
                conn,
                alert_id,
                "CREATED",
                {"instrument": instrument, "target_price": target, "direction": direction},
            )
            conn.execute("COMMIT")
            alert = self.get_alert(alert_id)
            if alert is None:
                raise RuntimeError("created alert could not be read back.")
            return alert

    def get_alert(self, alert_id: int) -> Alert | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM alerts WHERE id = ?", (int(alert_id),)).fetchone()
            return self._row_to_alert(row) if row else None

    def list_current_alerts(self, *, instrument: str | None = None) -> list[Alert]:
        params: list[Any] = list(CURRENT_STATUSES)
        sql = "SELECT * FROM alerts WHERE status IN (?, ?)"
        if instrument is not None:
            sql += " AND instrument = ?"
            params.append(normalize_instrument(instrument))
        sql += " ORDER BY id ASC"
        return self._list(sql, params)

    def list_fired_alerts(self, *, instrument: str | None = None, limit: int = 50) -> list[Alert]:
        resolved_limit = max(1, min(int(limit), 500))
        params: list[Any] = ["FIRED"]
        sql = "SELECT * FROM alerts WHERE status = ?"
        if instrument is not None:
            sql += " AND instrument = ?"
            params.append(normalize_instrument(instrument))
        sql += " ORDER BY fired_at DESC, id DESC LIMIT ?"
        params.append(resolved_limit)
        return self._list(sql, params)

    def cancel_alert(self, alert_id: int) -> Alert | None:
        now = to_utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM alerts WHERE id = ? AND status IN ('PENDING', 'FIRING')",
                (int(alert_id),),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE alerts SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
                (now, int(alert_id)),
            )
            self._insert_event(conn, int(alert_id), "CANCELLED", {"previous_status": row["status"]})
            conn.execute("COMMIT")
        return self.get_alert(alert_id)

    def cancel_current_alerts(self, *, instrument: str | None = None) -> list[Alert]:
        target_instrument = normalize_instrument(instrument) if instrument is not None else None
        now = to_utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            params: list[Any] = list(CURRENT_STATUSES)
            sql = "SELECT * FROM alerts WHERE status IN (?, ?)"
            if target_instrument is not None:
                sql += " AND instrument = ?"
                params.append(target_instrument)
            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE alerts SET status = 'CANCELLED', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                self._insert_event(conn, row["id"], "CANCELLED", {"previous_status": row["status"]})
            conn.execute("COMMIT")
            ids = [int(row["id"]) for row in rows]

        return [alert for alert_id in ids if (alert := self.get_alert(alert_id)) is not None]

    def mark_firing(self, alert_id: int, *, trigger_price: float) -> Alert | None:
        now = to_utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM alerts WHERE id = ? AND status = 'PENDING'",
                (int(alert_id),),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE alerts
                SET status = 'FIRING', updated_at = ?, trigger_price = ?, last_error = NULL
                WHERE id = ? AND status = 'PENDING'
                """,
                (now, float(trigger_price), int(alert_id)),
            )
            self._insert_event(conn, int(alert_id), "FIRING", {"trigger_price": float(trigger_price)})
            conn.execute("COMMIT")
        return self.get_alert(alert_id)

    def mark_fired(self, alert_id: int, *, trigger_price: float) -> Alert | None:
        now = to_utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM alerts WHERE id = ? AND status = 'FIRING'",
                (int(alert_id),),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE alerts
                SET status = 'FIRED', updated_at = ?, fired_at = ?, trigger_price = ?, last_error = NULL
                WHERE id = ? AND status = 'FIRING'
                """,
                (now, now, float(trigger_price), int(alert_id)),
            )
            self._insert_event(conn, int(alert_id), "FIRED", {"trigger_price": float(trigger_price)})
            conn.execute("COMMIT")
        return self.get_alert(alert_id)

    def release_firing(self, alert_id: int, *, error: str) -> Alert | None:
        now = to_utc_iso()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM alerts WHERE id = ? AND status = 'FIRING'",
                (int(alert_id),),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                UPDATE alerts
                SET status = 'PENDING', updated_at = ?, last_error = ?
                WHERE id = ? AND status = 'FIRING'
                """,
                (now, error[:1000], int(alert_id)),
            )
            self._insert_event(conn, int(alert_id), "RELEASED", {"error": error[:1000]})
            conn.execute("COMMIT")
        return self.get_alert(alert_id)

    def active_instruments(self) -> tuple[str, ...]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT instrument FROM alerts WHERE status IN ('PENDING', 'FIRING') ORDER BY instrument"
            ).fetchall()
            return tuple(str(row["instrument"]) for row in rows)

    def status_counts(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM alerts GROUP BY status").fetchall()
            counts = {"PENDING": 0, "FIRING": 0, "FIRED": 0, "CANCELLED": 0}
            for row in rows:
                counts[str(row["status"])] = int(row["count"])
            return counts

    def _list(self, sql: str, params: list[Any]) -> list[Alert]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_alert(row) for row in rows]

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        alert_id: int | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO alert_events (alert_id, event_type, created_at, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (alert_id, event_type, to_utc_iso(), json.dumps(payload, sort_keys=True)),
        )

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> Alert:
        return Alert(
            id=int(row["id"]),
            instrument=str(row["instrument"]),
            target_price=float(row["target_price"]),
            direction=row["direction"],
            status=row["status"],
            note=row["note"],
            created_at=parse_utc(row["created_at"]) or parse_utc(to_utc_iso()),
            updated_at=parse_utc(row["updated_at"]) or parse_utc(to_utc_iso()),
            fired_at=parse_utc(row["fired_at"]),
            trigger_price=float(row["trigger_price"]) if row["trigger_price"] is not None else None,
            last_error=row["last_error"],
        )

