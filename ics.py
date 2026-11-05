"""Minimal iCalendar reading (busy time) and writing (applied blocks), stdlib only.

Reading covers what busy time needs: VEVENT DTSTART/DTEND in UTC, with a TZID
or floating, and all-day dates. Recurring events (RRULE) are counted and
skipped rather than half-expanded; the caller says so to the user.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STAMP = "%Y-%m-%d %H:%M:%S"


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _value(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), dict(p.split("=", 1) for p in params if "=" in p), value


def _unescape(value: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _moment(value: str, params: dict[str, str], tz: ZoneInfo) -> tuple[datetime | date, bool]:
    """Parse a DTSTART/DTEND value; returns (value, is_all_day)."""
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").date(), True
    if value.endswith("Z"):
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    else:
        parsed = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(params["TZID"])) if "TZID" in params else parsed.replace(tzinfo=tz)
        except (ZoneInfoNotFoundError, ValueError):
            parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz), False


def busy_events(text: str, timezone_name: str) -> tuple[list[dict], int]:
    """Busy blocks (planner-local wall clock) from an iCalendar body, plus a count of skipped recurring events."""
    tz = ZoneInfo(timezone_name)
    events: list[dict] = []
    skipped = 0
    current: dict | None = None
    for line in _unfold(text):
        name, params, value = _value(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current = {}
        elif name == "END" and value.upper() == "VEVENT" and current is not None:
            if current.get("rrule"):
                skipped += 1
            elif current.get("transparent") or current.get("cancelled"):
                pass  # free or cancelled time is not busy
            elif "start" in current:
                start, all_day = current["start"]
                end = current.get("end", (None, all_day))[0]
                if all_day:
                    last = end if isinstance(end, date) else start + timedelta(days=1)
                    events.append({"title": current.get("title", "Busy"), "start": f"{start} 00:00:00",
                                   "end": f"{max(last, start + timedelta(days=1))} 00:00:00"})
                elif end is not None and end > start:
                    events.append({"title": current.get("title", "Busy"), "start": start.strftime(STAMP),
                                   "end": end.strftime(STAMP)})
            current = None
        elif current is not None:
            if name == "DTSTART":
                current["start"] = _moment(value, params, tz)
            elif name == "DTEND":
                current["end"] = _moment(value, params, tz)
            elif name == "SUMMARY":
                current["title"] = _unescape(value)[:200] or "Busy"
            elif name == "RRULE":
                current["rrule"] = True
            elif name == "TRANSP" and value.upper() == "TRANSPARENT":
                current["transparent"] = True
            elif name == "STATUS" and value.upper() == "CANCELLED":
                current["cancelled"] = True
    return events, skipped


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
        lines += ["BEGIN:VEVENT", f"UID:{block['task_id']}@solverforge-planner", f"DTSTAMP:{stamp}",
                  f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{end:%Y%m%dT%H%M%SZ}",
                  f"SUMMARY:{_escape(block.get('title') or 'Planned work')}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
