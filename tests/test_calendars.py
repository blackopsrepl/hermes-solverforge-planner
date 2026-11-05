"""Planning around the user's calendars, read and written by their own calendar tool.

The plugin reads no calendar itself. These tests play the agent: they pass calendar reads
the way the google-workspace skill or the microsoft365 plugin returns them, create the
planner's events the way those tools would, and check what the planner allows.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from solverforge_planner_plugin import calendars, engine_bin
from solverforge_planner_plugin.store import Store
from solverforge_planner_plugin.tools import Planner, tool_table

ROOT = Path(__file__).resolve().parents[1]
BIN = str(ROOT / "engine/target/release/solverforge-planner")
TZ = "Europe/Rome"
GOOGLE_EVENT_ID = re.compile(r"^[a-v0-9]{5,1024}$")  # Calendar API: base32hex, 5-1024 characters


@pytest.fixture
def planner(tmp_path):
    data = tmp_path / "plugin-data"
    p = Planner(Store.at(data / "planner.db"), lambda: engine_bin.Engine(data, override=BIN), data)
    hours = [f"{d}=09:00-18:00" for d in ("mon", "tue", "wed", "thu", "fri")]
    assert call(p, "planner_settings", action="update", timezone=TZ, availability=hours, solve_seconds=2,
                horizon_days=10)["ok"]
    return p


def call(planner, name, **args):
    handlers = {schema["name"]: handler for schema, handler in tool_table(planner)}
    return json.loads(handlers[name](args))


def weekday(n: int) -> date:
    day = date.today()
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def window(p) -> tuple[str, str]:
    lo, hi = p._calendar_window()
    return str(lo.date()), str(hi.date() + timedelta(days=1))


class Calendar:
    """A user's calendar as their calendar tool sees it: events with server ids."""

    def __init__(self, name: str = "google: work"):
        self.name = name
        self.events: list[dict] = []

    def add(self, title, start, end, **extra):
        self.events.append({"id": extra.pop("id", f"g{len(self.events)}"), "title": title, "start": start,
                            "end": end, **extra})

    def read(self, p, **override):
        lo, hi = window(p)
        return call(p, "planner_busy_set", source=self.name, kind="calendar", window_start=lo, window_end=hi,
                    events=[dict(e) for e in self.events], **override)

    def create(self, payload, style="google"):
        """Create planner events as a real tool would: Google keeps the given id; Microsoft
        Graph assigns its own and keeps the description."""
        for event in payload:
            if style == "google":
                if any(e["id"] == event["event_id"] for e in self.events):
                    continue  # the API answers 409: that id exists
                self.add(event["title"], event["start"], event["end"], id=event["event_id"],
                         description=event["description"])
            else:
                self.add(event["title"], event["start"], event["end"], id=f"AAMk{len(self.events)}",
                         description=event["description"])


def plan(p, *tasks):
    # From tomorrow: a block placed in the current slot has started by the time it is applied,
    # and applying a block that has started is rightly refused.
    tomorrow = f"{date.today() + timedelta(days=1)} 00:00"
    for title, minutes in tasks:
        assert call(p, "planner_task_create", title=title, duration_minutes=minutes, earliest_at=tomorrow)["ok"]
    result = call(p, "planner_optimize")
    assert result["ok"], result
    return result


def bump_reads(p, minutes: int) -> None:
    """Pretend every calendar read happened ``minutes`` earlier."""
    with p.store._lock:
        for row in p.store.calendars():
            read = datetime.strptime(row["read_at"], "%Y-%m-%d %H:%M:%S.%f") - timedelta(minutes=minutes)
            p.store._conn.execute("UPDATE calendars SET read_at = ? WHERE source = ?",
                                  (read.strftime("%Y-%m-%d %H:%M:%S.%f"), row["source"]))


def test_recurrence_exceptions_time_zones_and_free_time_are_read_correctly():
    text = (ROOT / "tests/fixtures/tricky.ics").read_text()
    got = [(b["start"], b["end"], b["title"]) for b in
           calendars.busy_blocks(text, TZ, datetime(2026, 11, 9), datetime(2026, 11, 14))]
    assert got == [
        ("2026-11-09 09:30:00", "2026-11-09 09:45:00", "Standup"),
        ("2026-11-10 09:30:00", "2026-11-10 09:45:00", "Standup"),
        ("2026-11-10 14:00:00", "2026-11-10 15:00:00", "Client call"),  # Outlook's Windows time zone name
        ("2026-11-11 12:00:00", "2026-11-11 13:00:00", "Lunch"),        # UTC with a DURATION
        ("2026-11-12 16:00:00", "2026-11-12 16:15:00", "Standup (moved)"),  # RECURRENCE-ID override
        ("2026-11-13 00:00:00", "2026-11-14 00:00:00", "Out of office"),    # all day
        ("2026-11-13 09:30:00", "2026-11-13 09:45:00", "Standup"),
    ]  # 11 Nov is an EXDATE; the free and the cancelled events are not busy


def test_without_a_calendar_the_planner_plans_from_what_it_is_told(planner):
    proposal = plan(planner, ("Write report", 60))
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"] and applied["create_on_calendar"] == [] and applied["ics"].endswith(".ics")
    assert call(planner, "planner_status")["calendars"] == []


def test_a_calendar_once_passed_must_be_read_fresh_and_whole_before_planning(planner):
    cal = Calendar()
    d1 = weekday(1)
    cal.add("Client day", f"{d1}T09:00:00+01:00", f"{d1}T18:00:00+01:00")
    assert cal.read(planner)["ok"]
    result = plan(planner, ("Write report", 60))
    assert result["calendars_read"] == ["google: work"]
    assert not any(s["start"].startswith(str(d1)) for s in result["scheduled"]), "placed on the client day"

    bump_reads(planner, 16)
    stale = call(planner, "planner_optimize")
    assert stale["code"] == "calendar_stale" and "google: work" in stale["error"]

    lo, _ = window(planner)
    short = call(planner, "planner_busy_set", source="google: work", kind="calendar", window_start=lo,
                 window_end=str(date.today() + timedelta(days=2)), events=[])
    assert short["ok"]
    assert "not the whole window" in call(planner, "planner_optimize")["error"]

    assert call(planner, "planner_busy_set", source="google: work", remove=True)["removed"]
    assert call(planner, "planner_optimize")["ok"], "a calendar the user dropped is no longer required"


def test_apply_needs_a_read_made_after_the_plan_and_refuses_new_overlaps(planner):
    cal = Calendar()
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60))
    refused = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert refused["code"] == "calendar_stale" and "before this plan" in refused["error"]

    block = proposal["scheduled"][0]
    start = datetime.strptime(block["start"], "%Y-%m-%d %H:%M:%S")
    cal.add("Surprise meeting", start.isoformat(), (start + timedelta(minutes=30)).isoformat())
    cal.read(planner)
    conflict = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert conflict["code"] == "calendar_conflict" and "Surprise meeting" in conflict["error"]

    cal.events.pop()
    far = weekday(8)
    cal.add("Unrelated", f"{far}T15:00:00", f"{far}T16:00:00")
    cal.read(planner)
    assert call(planner, "planner_apply", id=proposal["proposal_id"])["ok"]


@pytest.mark.parametrize("style", ["google", "microsoft"])
def test_planned_events_are_created_once_whatever_happens(planner, style):
    cal = Calendar()
    d1 = weekday(1)
    cal.add("Standup", f"{d1}T09:00:00", f"{d1}T09:30:00")
    cal.read(planner)
    assert call(planner, "planner_settings", action="update", publish_calendar="Google: Planner")["ok"]
    proposal = plan(planner, ("Write report", 60), ("Call accountant", 30))
    before = list(cal.events)
    cal.read(planner)
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"] and applied["publish_to"] == "Google: Planner"
    payload = applied["create_on_calendar"]
    assert len(payload) == 2 and applied["already_on_calendar"] == 0
    for event in payload:
        assert GOOGLE_EVENT_ID.match(event["event_id"])
        assert event["start"].endswith(("+01:00", "+02:00")) and calendars.MARKER in event["description"]

    # The agent's first attempt dies after one event. Asking again before reading the calendar
    # is refused: the planner cannot know which events arrived.
    cal.create(payload[:1], style)
    blind = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert blind["code"] == "calendar_stale" and "handed out" in blind["error"]
    read = cal.read(planner)
    assert read["planner_events_found"] == 1
    rest = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert [e["event_id"] for e in rest["create_on_calendar"]] == [payload[1]["event_id"]]
    cal.create(rest["create_on_calendar"], style)
    cal.read(planner)
    done = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert done["create_on_calendar"] == [] and done["already_on_calendar"] == 2
    assert call(planner, "planner_status")["not_on_calendar_yet"] == []

    planner_events = [e for e in cal.events if calendars.MARKER in e.get("description", "")]
    assert len(planner_events) == 2, "a planner event was created twice"
    assert [e for e in cal.events if e not in planner_events] == before, "an existing event changed"

    # The planner's own events are not foreign busy time: a new plan goes around the pinned
    # blocks without calling them conflicts, and only the new task is placed.
    second = plan(planner, ("Review PR", 45))
    assert [s["title"] for s in second["scheduled"]] == ["Review PR"]
    cal.read(planner)
    third = call(planner, "planner_apply", id=second["proposal_id"])
    assert third["ok"] and len(third["create_on_calendar"]) == 1


def test_an_event_moved_in_the_calendar_app_moves_its_block(planner):
    cal = Calendar()
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60))
    cal.read(planner)
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"], applied
    payload = applied["create_on_calendar"]
    cal.create(payload)
    far = weekday(6)
    cal.events[-1].update(start=f"{far}T16:00:00", end=f"{far}T17:00:00")  # the user drags it
    read = cal.read(planner)
    assert read["moved_on_calendar"][0]["to"] == f"{far} 16:00:00"
    week = call(planner, "planner_week", start=str(far), days=1)
    assert [(e["title"], e["start"]) for e in week["events"] if e["kind"] == "planned"] == [
        ("Write report", f"{far} 16:00:00")]


def test_unschedule_hands_back_exactly_the_planners_event(planner):
    cal = Calendar()
    cal.add("Standup", f"{weekday(1)}T09:00:00", f"{weekday(1)}T09:30:00")
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60))
    cal.read(planner)
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"], applied
    payload = applied["create_on_calendar"]
    cal.create(payload)
    cal.read(planner)
    task_id = payload and call(planner, "planner_inbox", state="applied")["tasks"][0]["id"]
    assert call(planner, "planner_task_delete", id=task_id)["code"] == "applied"
    result = call(planner, "planner_task_unschedule", id=task_id)
    assert [e["event_id"] for e in result["delete_from_calendar"]] == [payload[0]["event_id"]]
    assert result["delete_from_calendar"][0]["seen_in"] == "google: work"


def test_an_ics_import_of_the_planners_own_export_is_not_busy_time(planner, tmp_path):
    cal = Calendar()
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60))
    cal.read(planner)
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"], applied
    text = Path(applied["ics"]).read_text()
    assert f"UID:{applied['create_on_calendar'][0]['event_id']}@solverforge-planner" in text
    exported = tmp_path / "back.ics"
    exported.write_text(text)
    assert call(planner, "planner_busy_from_ics", source="re-import", path=str(exported))["stored"] == 0
