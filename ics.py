"""Writing applied blocks as an iCalendar file any calendar app imports.

Each block keeps its fixed event id as the UID, so importing the file again
updates the events instead of adding copies. Reading lives in ``calendars.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .calendars import description as calendar_description

STAMP = "%Y-%m-%d %H:%M:%S"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def calendar(blocks: list[dict], timezone_name: str) -> str:
    """An iCalendar body for planned blocks, times in UTC so any app imports them exactly."""
    tz = ZoneInfo(timezone_name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//blackopsrepl//SolverForge Planner//EN",
             "CALSCALE:GREGORIAN", "X-WR-CALNAME:SolverForge Planner"]
    for block in blocks:
        start = datetime.strptime(block["start"], STAMP).replace(tzinfo=tz).astimezone(timezone.utc)
        end = datetime.strptime(block["end"], STAMP).replace(tzinfo=tz).astimezone(timezone.utc)
        uid = block.get("event_id") or block["task_id"]
        lines += ["BEGIN:VEVENT", f"UID:{uid}@solverforge-planner", f"DTSTAMP:{stamp}",
                  f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{end:%Y%m%dT%H%M%SZ}",
                  f"SUMMARY:{_escape(block.get('title') or 'Planned work')}"]
        if block.get("event_id"):
            lines.append(f"DESCRIPTION:{_escape(calendar_description(block['event_id']))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
