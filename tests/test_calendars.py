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
        # Like a real calendar tool: only the events in the window asked for.
        shown = [dict(e) for e in self.events if e["start"][:10] < hi[:10] and e["end"][:10] >= lo[:10]]
        return call(p, "planner_busy_set", source=self.name, kind="calendar", window_start=lo, window_end=hi,
                    events=shown, **override)

    def create(self, payload, style="google"):
        """Create planner events as a real tool would: Google keeps the given id; Microsoft
        Graph, and the google-workspace skill's CLI, assign their own and keep the description."""
        for event in payload:
            if style == "google":
                if any(e["id"] == event["event_id"] for e in self.events):
                    continue  # the API answers 409: that id exists
                self.add(event["title"], event["start"], event["end"], id=event["event_id"],
                         description=event["description"])
            else:
                self.add(event["title"], event["start"], event["end"], id=f"AAMk{len(self.events)}",
                         description=event["description"])

    def carry_out(self, push, style="google", only=None):
        """Do what planner_push asked, as the agent would with the calendar tool."""
        self.create(push["create"][:only] if only is not None else push["create"], style)
        for gone in push["delete"]:
            self.events = [e for e in self.events if e["id"] != gone["id"]]

    def mine(self):
        return [e for e in self.events if calendars.MARKER in e.get("description", "")]


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


def test_without_a_calendar_the_planner_keeps_its_own_calendar(planner):
    proposal = plan(planner, ("Write report", 60))
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"] and applied["push_to"] is None and applied["ics"].endswith(".ics")
    local = Path(applied["local_calendar"])
    assert local.name == "planner.ics" and "SUMMARY:Write report" in local.read_text()
    status = call(planner, "planner_status")
    assert status["calendars"] == [] and status["push"] is None
    assert call(planner, "planner_push")["code"] == "no_calendar"


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
def test_push_puts_the_plan_on_the_calendar_once_whatever_happens(planner, style):
    cal = Calendar()
    d1 = weekday(1)
    cal.add("Standup", f"{d1}T09:00:00", f"{d1}T09:30:00")
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60), ("Call accountant", 30))
    before = [dict(e) for e in cal.events]
    cal.read(planner)
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"] and applied["push_to"] == "google: work"
    assert cal.events == before, "apply alone touches no cloud calendar"
    assert call(planner, "planner_status")["push"] == {"calendar": "google: work", "create": 2, "delete": 0}

    push = call(planner, "planner_push")
    assert push["ok"] and push["calendar"] == "google: work" and push["delete"] == []
    assert len(push["create"]) == 2
    for event in push["create"]:
        assert GOOGLE_EVENT_ID.match(event["event_id"])
        assert event["start"].endswith(("+01:00", "+02:00")) and calendars.MARKER in event["description"]

    # The agent creates one event, then the connection drops. Pushing again before a pull is
    # refused: the planner cannot know which events were made.
    cal.carry_out(push, style, only=1)
    blind = call(planner, "planner_push")
    assert blind["code"] == "calendar_stale" and "pull" in blind["error"]
    assert cal.read(planner)["planner_events_found"] == 1
    rest = call(planner, "planner_push")
    assert [e["event_id"] for e in rest["create"]] == [push["create"][1]["event_id"]]
    cal.carry_out(rest, style)
    cal.read(planner)
    done = call(planner, "planner_push")
    assert done["create"] == [] and done["delete"] == [] and done["already_there"] == 2
    assert call(planner, "planner_status")["push"] == {"calendar": "google: work", "create": 0, "delete": 0}
    assert len(cal.mine()) == 2, "a planner event was created twice"
    assert [e for e in cal.events if e not in cal.mine()] == before, "an existing event changed"

    # The planner's own events are not foreign busy time: a new plan goes around the pinned blocks
    # without calling them conflicts, and only the new task is placed and pushed.
    second = plan(planner, ("Review PR", 45))
    assert [s["title"] for s in second["scheduled"]] == ["Review PR"]
    cal.read(planner)
    assert call(planner, "planner_apply", id=second["proposal_id"])["ok"]
    assert [e["title"] for e in call(planner, "planner_push")["create"]] == ["Review PR"]


def test_pull_adopts_moves_and_deletions_made_on_the_calendar(planner):
    cal = Calendar()
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60), ("Call accountant", 30))
    cal.read(planner)
    assert call(planner, "planner_apply", id=proposal["proposal_id"])["ok"]
    cal.carry_out(call(planner, "planner_push"), "microsoft")
    cal.read(planner)
    report, accountant = sorted(cal.mine(), key=lambda e: e["title"], reverse=True)

    far = weekday(6)
    report.update(start=f"{far}T16:00:00", end=f"{far}T17:00:00")  # the user drags it
    cal.events.remove(accountant)                                     # and deletes the other
    pulled = cal.read(planner)
    assert [m["to"] for m in pulled["moved_on_calendar"]] == [f"{far} 16:00:00"]
    assert [r["title"] for r in pulled["removed_on_calendar"]] == ["Call accountant"]
    inbox = call(planner, "planner_inbox")["tasks"]
    assert [t["title"] for t in inbox] == ["Call accountant"], "deleted there means back to the inbox"
    week = call(planner, "planner_week", start=str(far), days=1)
    assert [(e["title"], e["start"]) for e in week["events"] if e["kind"] == "planned"] == [
        ("Write report", f"{far} 16:00:00")]
    local = Path(call(planner, "planner_status")["local_calendar"]).read_text()
    assert "Call accountant" not in local and "Write report" in local
    push = call(planner, "planner_push")
    assert push["create"] == [] and push["delete"] == [], "nothing pushed back"


def test_unscheduling_deletes_exactly_the_planners_event_at_the_next_push(planner):
    cal = Calendar()
    cal.add("Standup", f"{weekday(1)}T09:00:00", f"{weekday(1)}T09:30:00")
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60), ("Call accountant", 30))
    cal.read(planner)
    assert call(planner, "planner_apply", id=proposal["proposal_id"])["ok"]
    cal.carry_out(call(planner, "planner_push"), "microsoft")
    cal.read(planner)
    task = next(t for t in call(planner, "planner_inbox", state="applied")["tasks"] if t["title"] == "Write report")
    assert call(planner, "planner_task_delete", id=task["id"])["code"] == "applied"
    assert call(planner, "planner_task_unschedule", id=task["id"])["ok"]
    assert call(planner, "planner_task_delete", id=task["id"])["ok"], "the removal outlives the task"
    assert call(planner, "planner_status")["push"]["delete"] == 1

    push = call(planner, "planner_push")
    report = next(e for e in cal.mine() if e["title"] == "Write report")
    assert push["create"] == [] and [d["id"] for d in push["delete"]] == [report["id"]]
    assert call(planner, "planner_push")["code"] == "calendar_stale", "no second hand-out before a pull"
    cal.carry_out(push)
    pulled = cal.read(planner)
    assert pulled["removed_on_calendar"] == [], "a delete the planner asked for is not a user deletion"
    assert call(planner, "planner_status")["push"]["delete"] == 0
    assert [e["title"] for e in cal.events] == ["Standup", "Call accountant"]


def test_an_ics_import_of_the_planners_own_export_is_not_busy_time(planner, tmp_path):
    cal = Calendar()
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60))
    cal.read(planner)
    applied = call(planner, "planner_apply", id=proposal["proposal_id"])
    assert applied["ok"], applied
    text = Path(applied["ics"]).read_text()
    event = call(planner, "planner_push")["create"][0]
    assert f"UID:{event['event_id']}@solverforge-planner" in text
    exported = tmp_path / "back.ics"
    exported.write_text(text)
    assert call(planner, "planner_busy_from_ics", source="re-import", path=str(exported))["stored"] == 0


def test_an_event_landing_on_a_planned_block_is_a_clash_until_replanned(planner):
    cal = Calendar()
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60))
    cal.read(planner)
    assert call(planner, "planner_apply", id=proposal["proposal_id"])["ok"]
    block = proposal["scheduled"][0]
    start = datetime.strptime(block["start"], "%Y-%m-%d %H:%M:%S")
    cal.add("Dentist", start.isoformat(), (start + timedelta(minutes=30)).isoformat())
    cal.read(planner)
    clashes = call(planner, "planner_status")["clashes"]
    assert [(c["title"], c["with"], c["calendar"]) for c in clashes] == [("Write report", "Dentist", "google: work")]
    week = call(planner, "planner_week", start=block["start"][:10], days=1)
    assert [c["with"] for c in week["clashes"]] == ["Dentist"]

    task = clashes[0]["task_id"]
    assert call(planner, "planner_task_unschedule", id=task)["ok"]
    replanned = call(planner, "planner_optimize")
    assert replanned["ok"] and call(planner, "planner_status")["clashes"] == []
    new = replanned["scheduled"][0]
    assert not (new["start"] < str(start + timedelta(minutes=30)) and new["end"] > str(start)), "on the dentist"


def test_an_event_moved_past_the_pulled_days_is_pinned_again_when_it_shows_up(planner):
    cal = Calendar()
    cal.read(planner)
    proposal = plan(planner, ("Write report", 60))
    cal.read(planner)
    assert call(planner, "planner_apply", id=proposal["proposal_id"])["ok"]
    cal.carry_out(call(planner, "planner_push"))
    cal.read(planner)
    event = cal.mine()[0]
    later = date.today() + timedelta(days=40)
    event.update(start=f"{later}T10:00:00", end=f"{later}T11:00:00")  # out of every pull window
    gone = cal.read(planner)
    assert [r["title"] for r in gone["removed_on_calendar"]] == ["Write report"]
    assert call(planner, "planner_push")["create"] == [], "a vanished event is not pushed back"

    lo = str(later - timedelta(days=1))
    hi = str(later + timedelta(days=1))
    back = call(planner, "planner_busy_set", source=cal.name, kind="calendar", window_start=lo, window_end=hi,
                events=[dict(e) for e in cal.events])
    assert [b["title"] for b in back["back_on_calendar"]] == ["Write report"]
    assert call(planner, "planner_inbox")["tasks"] == []
    week = call(planner, "planner_week", start=str(later), days=1)
    assert [(e["title"], e["start"], e["kind"]) for e in week["events"]] == [
        ("Write report", f"{later} 10:00:00", "planned")]
    cal.read(planner)
    assert call(planner, "planner_push")["create"] == [], "pinned at its event, nothing to create"
