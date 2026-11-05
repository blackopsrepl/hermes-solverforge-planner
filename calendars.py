"""Calendar facts the planner works with, whichever tool reads and writes the calendar.

The plugin holds no calendar login. The agent reads the user's calendars with the
tool they use (the google-workspace skill, the microsoft365 plugin, ...) and passes
the events in; it creates the planner's events with the same tool. This module
gives every planned block a fixed event id and a marker, so the planner recognizes
its own events when they come back in a calendar read, and expands iCalendar
files for one-off imports.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

STAMP = "%Y-%m-%d %H:%M:%S"
MARKER = "solverforge-planner:"
# 32 characters from 0-9a-f: valid as a Google Calendar event id (base32hex, 5-1024 chars)
# and as an iCalendar UID.
EVENT_ID = re.compile(r"sfp[0-9a-f]{29}")


def event_id(task_id: str, start: str, end: str, proposal_id: str) -> str:
    """The fixed id of one applied block. A retry of the same apply gets the same id; placing the
    task again later (a new proposal) gets a new one, because calendars keep deleted ids reserved."""
    digest = hashlib.sha256(f"{task_id}|{start}|{end}|{proposal_id}".encode()).hexdigest()
    return "sfp" + digest[:29]


def description(event: str) -> str:
    return f"Planned by SolverForge Planner in Hermes.\n{MARKER}{event}"


def planner_ids(*texts: object) -> set[str]:
    """Planner event ids found in any of an event's fields (id, iCalendar UID, description)."""
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(EVENT_ID.findall(str(text)))
    return found


def _local(value, tz: ZoneInfo) -> datetime:
    """Wall-clock time in ``tz``; floating times are taken as already local."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(tz).replace(tzinfo=None)
    return datetime(value.year, value.month, value.day)


def busy_blocks(text: str, timezone: str, start: datetime, end: datetime) -> list[dict]:
    """Busy time in ``[start, end)`` (naive, planner-local) from an iCalendar body.

    Recurring events are expanded with their exceptions, moved occurrences and time
    zones; free and cancelled events are left out. Each block is ``{"uid", "title",
    "description", "start", "end"}`` with ``YYYY-MM-DD HH:MM:SS`` times. Raises
    ``ValueError`` when the text is not an iCalendar.
    """
    import icalendar
    import recurring_ical_events

    try:
        calendar = icalendar.Calendar.from_ical(text)
    except Exception as exc:  # icalendar raises ValueError and friends on bad input
        raise ValueError(f"not a valid iCalendar ({exc.__class__.__name__})") from None
    tz = ZoneInfo(timezone)
    window = (start.replace(tzinfo=tz), end.replace(tzinfo=tz))
    blocks = []
    for event in recurring_ical_events.of(calendar, skip_bad_series=True).between(*window):
        if str(event.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
            continue  # marked free: not busy
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = event.get("DTSTART")
        if dtstart is None:
            continue
        first = dtstart.dt
        dtend = event.get("DTEND")
        if dtend is not None:
            last = dtend.dt
        elif event.get("DURATION") is not None:
            last = first + event.get("DURATION").dt
        elif isinstance(first, date) and not isinstance(first, datetime):
            last = first + timedelta(days=1)  # RFC 5545: a date-only event lasts one day
        else:
            continue  # an instant occupies no time
        block_start, block_end = _local(first, tz), _local(last, tz)
        if block_end <= block_start:
            continue
        blocks.append({
            "uid": str(event.get("UID", "")),
            "title": str(event.get("SUMMARY", "") or "Busy")[:200],
            "description": str(event.get("DESCRIPTION", "") or "")[:2000],
            "start": block_start.strftime(STAMP),
            "end": block_end.strftime(STAMP),
        })
    blocks.sort(key=lambda b: (b["start"], b["end"], b["title"]))
    return blocks
