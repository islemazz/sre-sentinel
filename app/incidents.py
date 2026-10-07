"""Incident log: turn a stream of flagged samples into trackable incidents.

A spike lasts 1-3 samples. Paging someone three times for one spike is noise,
so consecutive flagged samples are grouped into ONE incident:

    open   - first flagged sample
    extend - every further flagged sample (peak value/score are tracked)
    close  - after `close_after` consecutive normal samples

Every state change is also written to `audit_log`, an append-only table: the
database itself refuses UPDATE and DELETE on it (see the triggers), so the
history of "what happened and what was done" cannot be rewritten afterwards.
"""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    status     TEXT    NOT NULL CHECK (status IN ('open', 'closed')),
    start_ts   REAL    NOT NULL,
    end_ts     REAL    NOT NULL,          -- timestamp of the last flagged sample
    samples    INTEGER NOT NULL,
    peak_value REAL    NOT NULL,
    peak_score REAL    NOT NULL,
    direction  TEXT    NOT NULL CHECK (direction IN ('up', 'down')),
    acked_at   REAL,
    ack_note   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    incident_id INTEGER NOT NULL REFERENCES incidents(id),
    event       TEXT    NOT NULL,         -- opened | closed | acknowledged
    detail      TEXT    NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


class IncidentStore:
    """SQLite persistence. All times are passed in (no hidden clock) so it is easy to test."""

    def __init__(self, path=":memory:"):
        # One shared connection guarded by a lock: the sampling loop and the
        # API's worker threads both use it.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            self._close_stale()

    # -- writes ---------------------------------------------------------
    def open_incident(self, ts, value, score) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO incidents (status, start_ts, end_ts, samples, peak_value, peak_score, direction)"
                " VALUES ('open', ?, ?, 1, ?, ?, ?)",
                (ts, ts, value, score, "up" if score >= 0 else "down"),
            )
            incident_id = cur.lastrowid
            self._audit(ts, incident_id, "opened", f"value={value:.1f} score={score:+.1f}")
            return incident_id

    def extend(self, incident_id, ts, value, score):
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT peak_score FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if abs(score) > abs(row["peak_score"]):
                self._db.execute(
                    "UPDATE incidents SET end_ts = ?, samples = samples + 1, peak_value = ?,"
                    " peak_score = ?, direction = ? WHERE id = ?",
                    (ts, value, score, "up" if score >= 0 else "down", incident_id),
                )
            else:
                self._db.execute(
                    "UPDATE incidents SET end_ts = ?, samples = samples + 1 WHERE id = ?",
                    (ts, incident_id),
                )

    def close(self, incident_id, ts):
        with self._lock, self._db:
            self._db.execute("UPDATE incidents SET status = 'closed' WHERE id = ?", (incident_id,))
            samples = self._db.execute(
                "SELECT samples FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()["samples"]
            self._audit(ts, incident_id, "closed", f"{samples} flagged sample(s)")

    def ack(self, incident_id, note, ts) -> str:
        """Record that a human looked at it. Returns 'acked', 'not_found' or 'already_acked'."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT acked_at FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if row is None:
                return "not_found"
            if row["acked_at"] is not None:
                return "already_acked"
            self._db.execute(
                "UPDATE incidents SET acked_at = ?, ack_note = ? WHERE id = ?", (ts, note, incident_id)
            )
            self._audit(ts, incident_id, "acknowledged", note)
            return "acked"

    # -- reads ----------------------------------------------------------
    def get(self, incident_id):
        with self._lock:
            row = self._db.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        return dict(row) if row else None

    def recent(self, limit=20):
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM incidents ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def audit(self, incident_id=None, limit=50):
        sql, args = "SELECT * FROM audit_log", []
        if incident_id is not None:
            sql += " WHERE incident_id = ?"
            args.append(incident_id)
        sql += " ORDER BY id DESC LIMIT ?" if incident_id is None else " ORDER BY id ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # -- internals ------------------------------------------------------
    def _audit(self, ts, incident_id, event, detail=""):
        self._db.execute(
            "INSERT INTO audit_log (ts, incident_id, event, detail) VALUES (?, ?, ?, ?)",
            (ts, incident_id, event, detail),
        )

    def _close_stale(self):
        """An incident still 'open' at startup belongs to a previous run: close it honestly."""
        for row in self._db.execute("SELECT id, end_ts FROM incidents WHERE status = 'open'").fetchall():
            self._db.execute("UPDATE incidents SET status = 'closed' WHERE id = ?", (row["id"],))
            self._audit(row["end_ts"], row["id"], "closed", "closed on service restart")


class IncidentTracker:
    """Groups consecutive flagged samples into incidents (see module docstring)."""

    def __init__(self, store: IncidentStore, close_after=3):
        self.store = store
        self.close_after = close_after
        self.open_id = None
        self._quiet = 0     # consecutive normal samples since the last flagged one

    def observe(self, ts, value, detection):
        """Feed one scored sample. Returns 'opened', 'closed' or None."""
        if detection.is_anomaly:
            self._quiet = 0
            if self.open_id is None:
                self.open_id = self.store.open_incident(ts, value, detection.score)
                return "opened"
            self.store.extend(self.open_id, ts, value, detection.score)
            return None

        if self.open_id is not None:
            self._quiet += 1
            if self._quiet >= self.close_after:
                self.store.close(self.open_id, ts)
                self.open_id, self._quiet = None, 0
                return "closed"
        return None
