"""End to end through the registered tool handlers, the store and the real engine.

Needs the engine binary: SOLVERFORGE_PLANNER_BIN, else engine/target/release.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pkg = sys.modules["solverforge_planner_plugin"]
from solverforge_planner_plugin import engine_bin, ics  # noqa: E402
from solverforge_planner_plugin.store import Store  # noqa: E402
from solverforge_planner_plugin.tools import Planner, tool_table  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BIN = os.environ.get("SOLVERFORGE_PLANNER_BIN") or str(ROOT / "engine/target/release/solverforge-planner")
TZ = "Europe/Rome"


class Ctx:
    """The slice of Hermes's PluginContext register() uses, recording what it gets."""

    def __init__(self):
        self.tools, self.hooks, self.commands, self.cli, self.skills, self.blueprints = {}, {}, {}, {}, {}, {}

    def get_config(self, key, default=None):
        return {"binary": BIN, "solve_timeout": 120}.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **_):
        self.tools[name] = handler

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_command(self, name, fn, **_):
        self.commands[name] = fn

    def register_cli_command(self, name, **kw):
        self.cli[name] = kw

    def register_skill(self, name, path):
        self.skills[name] = path

    def register_automation_blueprint(self, name, **kw):
        self.blueprints[name] = kw


@pytest.fixture
def planner(tmp_path):
    events = []
    p = Planner(Store.at(tmp_path / "planner.db"), lambda: engine_bin.Engine(tmp_path, override=BIN), tmp_path,
                on_change=lambda what, detail: events.append(what))
    p.events = events
    return p


def call(planner, name, **args):
    handlers = {schema["name"]: handler for schema, handler in tool_table(planner)}
    return json.loads(handlers[name](args))


def next_weekday(n: int) -> date:
    """The n-th Monday-to-Friday day strictly after today."""
    day = date.today()
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def setup_week(planner):
    hours = [f"{d}=09:00-18:00" for d in ("mon", "tue", "wed", "thu", "fri")]
    assert call(planner, "planner_settings", action="update", timezone=TZ, availability=hours,
                solve_seconds=2, horizon_days=10, focus_window="09:00-12:00")["ok"]
    d1, d2, d3 = next_weekday(1), next_weekday(2), next_weekday(3)
    deck = call(planner, "planner_task_create", title="Q4 deck", duration_minutes=240, priority="high",
                cognitive_load="high", deadline_at=f"{d3} 18:00")["task"]
    pr = call(planner, "planner_task_create", title="Review Anna's PR", duration_minutes=60)["task"]
    acc = call(planner, "planner_task_create", title="Call accountant", duration_minutes=30, priority="low",
               cognitive_load="low")["task"]
    assert call(planner, "planner_depend", action="add", before=deck["id"], after=pr["id"])["ok"]
    assert call(planner, "planner_busy_set", source="work",
                events=[{"title": "Offsite", "start": f"{d1} 09:00", "end": f"{d1} 18:00"},
                        {"title": "Standup", "start": f"{d2}T09:00:00+01:00", "end": f"{d2}T09:30:00+01:00"}])["ok"]
    return {"deck": deck, "pr": pr, "acc": acc, "days": (d1, d2, d3)}


def test_registration_matches_the_manifest(tmp_path, monkeypatch):
    # Outside Hermes there is no plugin_data_dir(); point the package at a temp dir.
    monkeypatch.setattr(pkg, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(pkg, "_planners", {})
    ctx = Ctx()
    pkg.register(ctx)
    manifest = (ROOT / "plugin.yaml").read_text()
    for name in ctx.tools:
        assert f"  - {name}\n" in manifest
    assert len(ctx.tools) == 14 and set(ctx.hooks) == {"pre_tool_call"}
    assert set(ctx.commands) == {"planner"} and set(ctx.cli) == {"solverforge-planner"}
    gate = ctx.hooks["pre_tool_call"]
    assert gate(tool_name="planner_inbox", args={}) is None
    decision = gate(tool_name="planner_apply", args={"id": "abc"})
    assert decision["action"] == "approve" and decision["rule_key"] == "planner_apply:abc"


def test_status_reports_what_is_missing(tmp_path):
    p = Planner(Store.at(tmp_path / "db"), lambda: engine_bin.Engine(tmp_path, override=str(tmp_path / "none")),
                tmp_path)
    status = call(p, "planner_status")
    assert status["ok"] and not status["ready"]
    assert "not installed" in status["problems"][0] and len(status["problems"]) == 3
    assert call(p, "planner_optimize")["code"] == "not_ready"


def test_plan_apply_and_replan(planner):
    w = setup_week(planner)
    d1, d2, d3 = w["days"]
    result = call(planner, "planner_optimize")
    assert result["ok"], result
    assert result["hard_rules_kept"] and result["can_apply"] and not result["unscheduled"]
    by_id = {b["task_id"]: b for b in result["scheduled"]}
    deck, pr = by_id[w["deck"]["id"]], by_id[w["pr"]["id"]]
    assert deck["end"] <= f"{d3} 18:00:00" and pr["start"] >= deck["end"]
    assert not any(b["start"].startswith(str(d1)) for b in result["scheduled"]), "placed during the offsite"
    assert result["display"] == f'::planner{{proposal="{result["proposal_id"]}"}}'

    applied = call(planner, "planner_apply", id=result["proposal_id"])
    assert applied["ok"] and len(applied["written"]) == 3
    exported = Path(applied["ics"]).read_text()
    assert exported.count("BEGIN:VEVENT") == 3 and "SUMMARY:Q4 deck" in exported
    again = call(planner, "planner_apply", id=result["proposal_id"])
    assert again["code"] == "stale" and "already_applied" in again["error"]

    week = call(planner, "planner_week", start=str(date.today()), days=14)
    assert sum(e["kind"] == "planned" for e in week["events"]) == 3

    # A new task is planned around the applied blocks, after its applied predecessor.
    late = call(planner, "planner_task_create", title="Ship deck", duration_minutes=30)["task"]
    call(planner, "planner_depend", action="add", before=w["deck"]["id"], after=late["id"])
    replan = call(planner, "planner_optimize")
    assert replan["ok"] and [b["task_id"] for b in replan["scheduled"]] == [late["id"]]
    assert replan["scheduled"][0]["start"] >= deck["end"]
    overlaps = [b for b in applied["written"]
                if b["start"] < replan["scheduled"][0]["end"] and replan["scheduled"][0]["start"] < b["end"]]
    assert not overlaps


def test_changed_inputs_make_a_proposal_stale(planner):
    w = setup_week(planner)
    result = call(planner, "planner_optimize")
    call(planner, "planner_task_update", id=w["acc"]["id"], duration_minutes=45)
    refused = call(planner, "planner_apply", id=result["proposal_id"])
    assert refused["code"] == "stale" and "inbox_tasks_changed" in refused["error"]
    shown = call(planner, "planner_proposal", id=result["proposal_id"])
    assert not shown["can_apply"] and shown["blocked_because"] == ["inbox_tasks_changed"]


def test_impossible_task_is_explained(planner):
    setup_week(planner)
    d1 = next_weekday(1)
    call(planner, "planner_task_create", title="Marathon", duration_minutes=600, deadline_at=f"{d1} 18:00")
    result = call(planner, "planner_optimize")
    missing = [u for u in result["unscheduled"] if u["title"] == "Marathon"]
    assert missing and missing[0]["outcome"] == "no_hard_feasible_slot" and missing[0]["reason"]


def test_cycles_and_bad_input_are_refused(planner):
    w = setup_week(planner)
    assert call(planner, "planner_depend", action="add", before=w["pr"]["id"], after=w["deck"]["id"])["code"] == "cycle"
    assert call(planner, "planner_settings", action="update", timezone="Mars/Base")["code"] == "invalid_arguments"
    assert call(planner, "planner_task_create", title="x", duration_minutes=2)["code"] == "invalid_arguments"
    assert call(planner, "planner_task_delete", id="nope")["code"] == "not_found"


def test_ics_import_and_export_round_trip(planner, tmp_path):
    setup_week(planner)
    feed = tmp_path / "busy.ics"
    feed.write_text("\r\n".join([
        "BEGIN:VCALENDAR", "BEGIN:VEVENT", "SUMMARY:Dentist", "DTSTART:20261111T130000Z",
        "DTEND:20261111T150000Z", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:Holiday", "DTSTART;VALUE=DATE:20261112", "DTEND;VALUE=DATE:20261113",
        "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:Weekly", "DTSTART;TZID=Europe/Rome:20261110T100000",
        "DTEND;TZID=Europe/Rome:20261110T110000", "RRULE:FREQ=WEEKLY", "END:VEVENT",
        "BEGIN:VEVENT", "SUMMARY:Free", "TRANSP:TRANSPARENT", "DTSTART:20261111T080000Z",
        "DTEND:20261111T090000Z", "END:VEVENT", "END:VCALENDAR"]))
    result = call(planner, "planner_busy_from_ics", source="ics", path=str(feed))
    assert result["stored"] == 2 and result["skipped_recurring"] == 1
    busy = {b["title"]: b for b in planner.store.busy() if b["source"] == "ics"}
    assert busy["Dentist"]["start"] == "2026-11-11 14:00:00"  # UTC -> Rome
    assert busy["Holiday"]["end"] == "2026-11-13 00:00:00"

    text = ics.calendar([{"task_id": "t1", "title": "Deck, v2", "start": "2026-11-11 09:00:00",
                          "end": "2026-11-11 10:00:00"}], TZ)
    assert "DTSTART:20261111T080000Z" in text and "SUMMARY:Deck\\, v2" in text


def test_engine_install_refuses_a_wrong_checksum(tmp_path, monkeypatch):
    payload = b"not the engine"
    triple = engine_bin.target_triple()
    monkeypatch.setitem(engine_bin.CHECKSUMS, triple, hashlib.sha256(b"something else").hexdigest())

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            return payload

    monkeypatch.setattr(engine_bin.urllib.request, "urlopen", lambda *a, **k: Response())
    engine = engine_bin.Engine(tmp_path)
    with pytest.raises(engine_bin.EngineError) as error:
        engine.install(log=lambda m: None)
    assert error.value.code == "checksum_mismatch" and not engine.installed_path.exists()

    monkeypatch.setitem(engine_bin.CHECKSUMS, triple, hashlib.sha256(payload).hexdigest())
    engine.install(log=lambda m: None)
    assert engine.installed_path.read_bytes() == payload and os.access(engine.installed_path, os.X_OK)


def test_slash_command_plans_and_applies(planner):
    from solverforge_planner_plugin.commands import slash_handler

    setup_week(planner)
    shown = slash_handler(planner, "plan")
    assert "score 0hard" in shown and "Apply with: /planner apply" in shown
    proposal_id = shown.rsplit(" ", 1)[1]
    assert slash_handler(planner, f"apply {proposal_id}").startswith("Applied 3 block(s)")


def test_each_profile_home_gets_its_own_planner(tmp_path, monkeypatch):
    # The Desktop backend serves several profiles from one process, re-scoping
    # the Hermes home per request; one profile must never see another's store.
    home = {"dir": tmp_path / "a"}
    monkeypatch.setattr(pkg, "data_dir", lambda: home["dir"])
    monkeypatch.setattr(pkg, "_planners", {})
    a = pkg.planner()
    home["dir"] = tmp_path / "b"
    b = pkg.planner()
    assert a is not b and a.data_dir != b.data_dir
    home["dir"] = tmp_path / "a"
    assert pkg.planner() is a
