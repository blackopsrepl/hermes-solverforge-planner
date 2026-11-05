"""The plugin's own state: settings, tasks, ordering rules, busy time, applied
blocks and proposals, in one SQLite file under the plugin data directory.

The engine is stateless; everything it is asked to solve is read from here,
and a proposal records a fingerprint of exactly those inputs so applying a
plan built from inputs that have since changed is refused.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    cognitive_load TEXT NOT NULL DEFAULT 'medium',
    earliest_at TEXT,
    deadline_kind TEXT NOT NULL DEFAULT 'none',
    deadline_at TEXT,
    state TEXT NOT NULL DEFAULT 'inbox',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dependencies (
    before TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    after TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (before, after)
);
CREATE TABLE IF NOT EXISTS busy (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applied (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calendars (
    source TEXT PRIMARY KEY,
    read_at TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    seen_in TEXT,
    seen_at TEXT,
    handed_at TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    parts TEXT NOT NULL,
    score TEXT NOT NULL,
    body TEXT NOT NULL,
    applied_at TEXT
);
"""

TASK_FIELDS = ("title", "duration_minutes", "priority", "cognitive_load", "earliest_at",
               "deadline_kind", "deadline_at")

DEFAULT_SETTINGS: dict[str, Any] = {
    "timezone": None,
    "availability": {},
    "horizon_days": 14,
    "slot_minutes": 15,
    "solve_seconds": 5,
    "high_window": None,
    "low_window": None,
}


def now_local(timezone: str | None = None) -> str:
    """Wall-clock now in ``timezone`` (the planner's), else in the system zone."""
    if timezone:
        try:
            return datetime.now(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M:%S")
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Store:
    """Thread-safe access to the plugin database. One instance per process."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None  # transactions are explicit in _write
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(proposals)")}
            if "made_at" not in columns:  # databases from 0.1.0
                self._conn.execute("ALTER TABLE proposals ADD COLUMN made_at TEXT")
            self._conn.commit()

    @classmethod
    def at(cls, path: Path) -> "Store":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        return cls(conn)

    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, tuple(params)).fetchall()]

    def _write(self, statements: Iterable[tuple[str, Iterable[Any]]]) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    self._conn.execute(sql, tuple(params))
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # -- settings -----------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        stored = {row["key"]: json.loads(row["value"]) for row in self._rows("SELECT * FROM settings")}
        return {**DEFAULT_SETTINGS, **stored}

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        self._write(("INSERT INTO settings(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value))) for key, value in values.items())
        return self.settings()

    # -- tasks --------------------------------------------------------------

    def tasks(self, state: str | None = None) -> list[dict]:
        if state in (None, "all"):
            return self._rows("SELECT * FROM tasks ORDER BY created_at, rowid")
        return self._rows("SELECT * FROM tasks WHERE state = ? ORDER BY created_at, rowid", (state,))

    def task(self, task_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return rows[0] if rows else None

    def create_task(self, fields: dict[str, Any]) -> dict:
        task_id = str(uuid.uuid4())
        stamp = now_local()
        values = {key: fields.get(key) for key in TASK_FIELDS}
        values["priority"] = values["priority"] or "normal"
        values["cognitive_load"] = values["cognitive_load"] or "medium"
        values["deadline_kind"] = values["deadline_kind"] or "none"
        self._write([(
            "INSERT INTO tasks (id, title, duration_minutes, priority, cognitive_load, earliest_at, "
            "deadline_kind, deadline_at, state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'inbox',?,?)",
            (task_id, *(values[key] for key in TASK_FIELDS), stamp, stamp),
        )])
        return self.task(task_id)  # type: ignore[return-value]

    def update_task(self, task_id: str, fields: dict[str, Any]) -> dict | None:
        changes = {key: fields[key] for key in TASK_FIELDS if key in fields}
        if changes:
            assignments = ", ".join(f"{key} = ?" for key in changes)
            self._write([(f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?",
                          (*changes.values(), now_local(), task_id))])
        return self.task(task_id)

    def delete_task(self, task_id: str) -> bool:
        exists = self.task(task_id) is not None
        self._write([("DELETE FROM tasks WHERE id = ?", (task_id,))])
        return exists

    def unschedule(self, task_id: str) -> dict | None:
        self._write([
            ("DELETE FROM applied WHERE task_id = ?", (task_id,)),
            ("DELETE FROM events WHERE task_id = ?", (task_id,)),
            ("UPDATE tasks SET state = 'inbox', updated_at = ? WHERE id = ?", (now_local(), task_id)),
        ])
        return self.task(task_id)

    # -- dependencies -------------------------------------------------------

    def dependencies(self) -> list[tuple[str, str]]:
        return [(row["before"], row["after"]) for row in self._rows("SELECT * FROM dependencies")]

    def add_dependency(self, before: str, after: str) -> None:
        self._write([("INSERT OR IGNORE INTO dependencies(before, after) VALUES (?, ?)", (before, after))])

    def remove_dependency(self, before: str, after: str) -> None:
        self._write([("DELETE FROM dependencies WHERE before = ? AND after = ?", (before, after))])

    # -- busy time ----------------------------------------------------------

    def busy(self, start: str | None = None, end: str | None = None) -> list[dict]:
        if start and end:
            return self._rows("SELECT * FROM busy WHERE start < ? AND end > ? ORDER BY start", (end, start))
        return self._rows("SELECT * FROM busy ORDER BY start")

    def replace_busy(self, source: str, events: list[dict], window: tuple[str, str] | None = None,
                     seen: dict[str, tuple[str, str]] | None = None) -> int:
        """Replace one source's busy time. With a ``window`` the source is a calendar read just now
        over that window, and must be read again before later plans and applies; without one it is
        busy time that stays until replaced. ``seen`` maps the planner's own event ids found in the
        read to the times the calendar shows for them."""
        # System clock like proposals' created_at, to the microsecond so "read after the plan" is exact.
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        statements: list[tuple[str, Iterable[Any]]] = [("DELETE FROM busy WHERE source = ?", (source,))]
        for index, event in enumerate(events):
            statements.append(("INSERT INTO busy(id, source, title, start, end) VALUES (?,?,?,?,?)",
                               (f"{source}:{index}", source, event["title"], event["start"], event["end"])))
        if window is None:
            statements.append(("DELETE FROM calendars WHERE source = ?", (source,)))
        else:
            statements.append(("INSERT INTO calendars(source, read_at, window_start, window_end) VALUES (?,?,?,?) "
                               "ON CONFLICT(source) DO UPDATE SET read_at = excluded.read_at, "
                               "window_start = excluded.window_start, window_end = excluded.window_end",
                               (source, stamp, *window)))
            # The calendar is the truth for the planner's own events: one moved there moves its
            # task's block here; one no longer there is no longer seen in this calendar.
            statements.append(("UPDATE events SET seen_in = NULL, seen_at = NULL WHERE seen_in = ? "
                               "AND start < ? AND end > ?", (source, window[1], window[0])))
            for event, (start, end) in (seen or {}).items():
                statements.append(("UPDATE events SET seen_in = ?, seen_at = ?, start = ?, end = ? WHERE event_id = ?",
                                   (source, stamp, start, end, event)))
                statements.append(("UPDATE applied SET start = ?, end = ? WHERE task_id = "
                                   "(SELECT task_id FROM events WHERE event_id = ?)", (start, end, event)))
        self._write(statements)
        return len(events)

    def forget_source(self, source: str) -> bool:
        known = source in self.busy_sources() or any(c["source"] == source for c in self.calendars())
        self._write([("DELETE FROM busy WHERE source = ?", (source,)),
                     ("DELETE FROM calendars WHERE source = ?", (source,)),
                     ("UPDATE events SET seen_in = NULL, seen_at = NULL WHERE seen_in = ?", (source,))])
        return known

    def calendars(self) -> list[dict]:
        """Calendar sources: read by the agent, and required fresh before plans and applies."""
        return self._rows("SELECT * FROM calendars ORDER BY source")

    # -- the planner's events on the user's calendar --------------------------

    def events(self, task_id: str | None = None) -> list[dict]:
        sql = "SELECT e.*, t.title FROM events e JOIN tasks t ON t.id = e.task_id"
        if task_id:
            return self._rows(sql + " WHERE e.task_id = ? ORDER BY e.start", (task_id,))
        return self._rows(sql + " ORDER BY e.start")

    def mark_handed_out(self, event_ids: list[str]) -> None:
        """Record that these events were given to the agent to create, so asking for them again
        first needs a calendar read showing which ones arrived."""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        self._write(("UPDATE events SET handed_at = ? WHERE event_id = ?", (stamp, e)) for e in event_ids)

    def known_event_ids(self) -> set[str]:
        return {row["event_id"] for row in self._rows("SELECT event_id FROM events")}

    def busy_sources(self) -> dict[str, int]:
        return {row["source"]: row["n"] for row in
                self._rows("SELECT source, count(*) AS n FROM busy GROUP BY source")}

    # -- applied blocks -----------------------------------------------------

    def applied(self, start: str | None = None, end: str | None = None) -> list[dict]:
        sql = ("SELECT a.*, t.title, t.cognitive_load FROM applied a JOIN tasks t ON t.id = a.task_id")
        if start and end:
            return self._rows(sql + " WHERE a.start < ? AND a.end > ? ORDER BY a.start", (end, start))
        return self._rows(sql + " ORDER BY a.start")

    # -- proposals ----------------------------------------------------------

    def fingerprint_parts(self) -> dict[str, str]:
        """One digest per input family, so a refusal can say what changed."""
        return {
            "inbox_tasks": _digest([{k: t[k] for k in ("id", *TASK_FIELDS)} for t in self.tasks("inbox")]),
            "dependencies": _digest(sorted(self.dependencies())),
            # Calendars are read again before an apply and checked for overlaps with the plan, so
            # an unrelated new meeting does not invalidate it; other busy time is checked here.
            "busy_time": _digest([(b["source"], b["start"], b["end"]) for b in self.busy()
                                  if b["source"] not in {c["source"] for c in self.calendars()}]),
            "applied_blocks": _digest([(a["task_id"], a["start"], a["end"]) for a in self.applied()]),
            "settings": _digest(self.settings()),
        }

    def save_proposal(self, body: dict, parts: dict[str, str]) -> str:
        proposal_id = str(uuid.uuid4())
        self._write([(
            "INSERT INTO proposals(id, created_at, made_at, fingerprint, parts, score, body) VALUES (?,?,?,?,?,?,?)",
            (proposal_id, now_local(), datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"), _digest(parts),
             json.dumps(parts), body.get("score", ""), json.dumps(body)),
        )])
        return proposal_id

    def proposal(self, proposal_id: str | None = None) -> dict | None:
        if proposal_id:
            rows = self._rows("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        else:
            rows = self._rows("SELECT * FROM proposals ORDER BY created_at DESC, rowid DESC LIMIT 1")
        if not rows:
            return None
        row = rows[0]
        return {**row, "parts": json.loads(row["parts"]), "body": json.loads(row["body"])}

    def proposals(self, limit: int = 20) -> list[dict]:
        return self._rows("SELECT id, created_at, score, applied_at FROM proposals "
                          "ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))

    def stale_reasons(self, proposal: dict) -> list[str]:
        """Why a proposal can no longer be applied; empty when it still can."""
        reasons = []
        if proposal.get("applied_at"):
            reasons.append("already_applied")
        current = self.fingerprint_parts()
        reasons += [f"{name}_changed" for name, digest in proposal["parts"].items()
                    if current.get(name) != digest]
        now = now_local(self.settings().get("timezone"))
        if any(item["scheduled"] and item["start_at"] < now for item in proposal["body"]["items"]):
            reasons.append("starts_in_the_past")
        return reasons

    def apply(self, proposal: dict, event_ids: dict[str, str]) -> list[dict]:
        """Pin the proposal's blocks; ``event_ids`` maps each task to its block's fixed event id."""
        stamp = now_local()
        scheduled = [item for item in proposal["body"]["items"] if item["scheduled"]]
        statements: list[tuple[str, Iterable[Any]]] = []
        for item in scheduled:
            statements.append(("INSERT OR REPLACE INTO applied(task_id, start, end, proposal_id, applied_at) "
                               "VALUES (?,?,?,?,?)",
                               (item["task_id"], item["start_at"], item["end_at"], proposal["id"], stamp)))
            statements.append(("UPDATE tasks SET state = 'applied', updated_at = ? WHERE id = ?",
                               (stamp, item["task_id"])))
            statements.append(("INSERT OR IGNORE INTO events(event_id, task_id, start, end) VALUES (?,?,?,?)",
                               (event_ids[item["task_id"]], item["task_id"], item["start_at"], item["end_at"])))
        statements.append(("UPDATE proposals SET applied_at = ? WHERE id = ?", (stamp, proposal["id"])))
        self._write(statements)
        return scheduled
