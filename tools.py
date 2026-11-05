"""Tool handlers. Each takes the model's arguments and returns a JSON string.

The handlers translate between the model's vocabulary (titles, ISO times,
"before/after") and the store and engine. They decide nothing about the
schedule: every placement, refusal reason and score comes from the engine.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import calendars, ics
from .engine_bin import Engine, EngineError, install_hint
from .store import Store, now_local

ICS_MAX_BYTES = 10 * 1024 * 1024
DIRECTIVE = '::planner{{proposal="{id}"}}'
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WINDOW = re.compile(r"^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$")
_AVAIL = re.compile(r"^\s*([a-z]{3})[a-z]*\s*=\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$", re.I)
STAMP = "%Y-%m-%d %H:%M:%S"
READ_STAMP = "%Y-%m-%d %H:%M:%S.%f"
# A calendar read this recent counts as the calendar's current state for a plan.
FRESH_MINUTES = 15


def _ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _fail(code: str, message: str, **detail: Any) -> str:
    return json.dumps({"ok": False, "code": code, "error": message, **detail}, ensure_ascii=False)


class Planner:
    """All planner operations over one store and one engine.

    ``engine_fn`` is called per operation so a settings change (binary path,
    timeout) takes effect without a restart. ``on_change`` tells any open
    Desktop surface that planner data moved.
    """

    def __init__(self, store: Store, engine_fn: Callable[[], Engine], data_dir: Path,
                 on_change: Callable[[str, dict], None] | None = None):
        self.store = store
        self._engine_fn = engine_fn
        self.data_dir = data_dir
        self._on_change = on_change

    # -- plumbing ---------------------------------------------------------

    @property
    def engine(self) -> Engine:
        return self._engine_fn()

    @property
    def publish_to(self) -> str | None:
        """The calendar source planner_push writes to, or None when there is no single one yet."""
        try:
            return self._push_target()
        except EngineError:
            return None

    # -- calendars --------------------------------------------------------
    #
    # The agent reads calendars with the user's own calendar tool and passes them to
    # planner_busy_set as calendar sources. From then on every plan needs a fresh read of each
    # one, and every apply a read made after the plan, so a plan never rests on a calendar the
    # planner has not seen.

    def _calendar_window(self, horizon_days: int | None = None) -> tuple[datetime, datetime]:
        """From the start of today to the end of the planning horizon, planner-local."""
        settings = self.store.settings()
        today = datetime.strptime(now_local(settings["timezone"])[:10], "%Y-%m-%d")
        return today, today + timedelta(days=int(horizon_days or settings["horizon_days"]) + 1)

    def _require_fresh(self, window: tuple[str, str], after: datetime | None = None,
                       why: str = "read before this plan was made") -> list[dict]:
        """Every calendar source read over ``window``, within FRESH_MINUTES, or after ``after``.
        Returns the sources; raises naming what to read again."""
        sources = self.store.calendars()
        problems, pull = [], []
        for cal in sources:
            read = datetime.strptime(cal["read_at"], READ_STAMP)
            if after is not None:
                if read <= after:
                    problems.append(f"{cal['source']} ({why})")
                    pull.append(cal["source"])
                    continue
            elif datetime.now() - read > timedelta(minutes=FRESH_MINUTES):
                problems.append(f"{cal['source']} (read {cal['read_at'][:16]})")
                pull.append(cal["source"])
                continue
            if cal["window_start"] > window[0] or cal["window_end"] < window[1]:
                problems.append(f"{cal['source']} (read {cal['window_start'][:10]}–{cal['window_end'][:10]}, "
                                "not the whole window)")
                pull.append(cal["source"])
        if problems:
            raise EngineError("calendar_stale", (
                "read these calendars again with the user's calendar tool and pass them with "
                f"planner_busy_set kind=calendar, window {window[0][:10]} to {window[1][:10]}: "
                + "; ".join(problems) + ". If the user no longer uses one, planner_busy_set "
                "remove=true for it."), pull=pull)
        return sources

    def _conflicts(self, items: list[dict]) -> list[str]:
        """Calendar events now overlapping a block about to be applied."""
        names = {c["source"] for c in self.store.calendars()}
        found = []
        for item in items:
            for b in self.store.busy(item["start_at"], item["end_at"]):
                if b["source"] in names:
                    found.append(f"{b['title']} {b['start'][5:16]}–{b['end'][11:16]}")
        return sorted(set(found))

    def _event_payload(self, event: dict, tz: str) -> dict:
        """One planner event as the user's calendar tool creates it."""
        zone = ZoneInfo(tz)
        iso = lambda s: datetime.strptime(s, STAMP).replace(tzinfo=zone).isoformat()
        return {"event_id": event["event_id"], "title": event["title"], "start": iso(event["start"]),
                "end": iso(event["end"]), "timezone": tz, "description": calendars.description(event["event_id"])}

    def changed(self, what: str, **detail: Any) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(what, detail)
        except Exception:  # noqa: BLE001 - a missing Desktop must never fail a tool call
            pass

    def handler(self, method: Callable[[dict], str]) -> Callable[..., str]:
        """Wrap a method as a registry handler that never raises."""

        def call(args: dict | None = None, **_: Any) -> str:
            try:
                return method(args or {})
            except EngineError as exc:
                return _fail(exc.code, exc.message, **exc.detail)
            except (ValueError, TypeError, KeyError) as exc:
                return _fail("invalid_arguments", str(exc))

        call.__name__ = method.__name__
        return call

    def timezone(self) -> str | None:
        return self.store.settings().get("timezone")

    def wall_clock(self, value: str, tz: str | None) -> str:
        """Normalize a model-supplied time to naive wall clock in the planner timezone."""
        text = str(value).strip().replace("T", " ").replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"unreadable time {value!r}; use 'YYYY-MM-DD HH:MM'")
        if parsed.tzinfo is not None and tz:
            parsed = parsed.astimezone(ZoneInfo(tz))
        return parsed.replace(tzinfo=None).strftime(STAMP)

    def problems(self) -> list[str]:
        settings = self.store.settings()
        problems = []
        if not settings.get("timezone"):
            problems.append("timezone is not set (planner_settings update timezone=...)")
        if not settings.get("availability"):
            problems.append("no working hours (planner_settings update availability=[...])")
        return problems

    def problem(self) -> dict[str, Any]:
        """The engine input, read entirely from the store."""
        s = self.store.settings()
        settings: dict[str, Any] = {
            "availability": s["availability"],
            "horizon_days": int(s["horizon_days"]),
            "slot_minutes": int(s["slot_minutes"]),
            "solve_seconds": int(s["solve_seconds"]),
        }
        for key in ("high_window", "low_window"):
            if s.get(key):
                settings[key] = s[key]
        return {
            "timezone": s["timezone"],
            "now": now_local(s["timezone"]),
            "settings": settings,
            "tasks": [
                {k: t[k] for k in ("id", "duration_minutes", "priority", "cognitive_load", "deadline_kind")}
                | {k: t[k] for k in ("earliest_at", "deadline_at") if t[k]}
                for t in self.store.tasks("inbox")
            ],
            "dependencies": [list(pair) for pair in self.store.dependencies()],
            "busy": [{"id": b["id"], "title": b["title"], "source": b["source"], "start": b["start"],
                      "end": b["end"]} for b in self.store.busy()],
            "applied": [{"task_id": a["task_id"], "start": a["start"], "end": a["end"],
                         "cognitive_load": a["cognitive_load"]} for a in self.store.applied()],
        }

    def describe(self, proposal: dict) -> dict:
        """The compact proposal shape the model, the slash command and the Desktop read."""
        body = proposal["body"]
        titles = {t["id"]: t["title"] for t in self.store.tasks("all")}
        scheduled, unscheduled = [], []
        for item in sorted(body["items"], key=lambda i: i.get("start_at") or "~"):
            entry = {"task_id": item["task_id"], "title": titles.get(item["task_id"], "(deleted task)")}
            if item["scheduled"]:
                start = item["start_at"]
                entry.update(start=start, end=item["end_at"],
                             day=datetime.strptime(start, STAMP).strftime("%a %d %b"),
                             note=item.get("explanation"))
                scheduled.append(entry)
            else:
                entry.update(outcome=item["outcome"], reason=item.get("explanation"),
                             blockers=[f"{b['title'] or 'busy'} {b['start_at'][5:16]}–{b['end_at'][11:16]}"
                                       for b in item.get("busy_blockers", [])])
                unscheduled.append(entry)
        reasons = self.store.stale_reasons(proposal)
        return {
            "proposal_id": proposal["id"],
            "created_at": proposal["created_at"],
            "score": body["score"],
            "hard_rules_kept": bool(body["hard_feasible"]) and body["score"].startswith("0hard"),
            "timezone": body["timezone"],
            "horizon": {"start": body["horizon_start"], "end": body["horizon_end"]},
            "can_apply": not reasons,
            "blocked_because": reasons,
            "applied_at": proposal.get("applied_at"),
            "publish_to": self.publish_to,
            "calendars": [c["source"] for c in self.store.calendars()],
            "scheduled": scheduled,
            "unscheduled": unscheduled,
            "display": DIRECTIVE.format(id=proposal["id"]),
        }

    # -- tools ------------------------------------------------------------

    def status(self, args: dict) -> str:
        engine = self.engine
        binary = engine.binary()
        settings = self.store.settings()
        latest = self.store.proposal()
        problems = self.problems()
        if binary is None:
            problems.insert(0, install_hint())
        return _ok(
            ready=not problems,
            problems=problems,
            engine={"path": str(binary) if binary else None, "version": engine.version() if binary else None},
            timezone=settings["timezone"],
            availability=settings["availability"],
            horizon_days=settings["horizon_days"],
            focus_window=settings.get("high_window"),
            shallow_window=settings.get("low_window"),
            busy_sources=self.store.busy_sources(),
            calendars=[{"source": c["source"], "read_at": c["read_at"][:19],
                        "window": [c["window_start"][:10], c["window_end"][:10]]} for c in self.store.calendars()],
            publish_to=self.publish_to,
            local_calendar=str(self.data_dir / "exports" / "planner.ics"),
            clashes=self.clashes(),
            push=self._push_summary(),
            inbox=len(self.store.tasks("inbox")),
            applied=len(self.store.tasks("applied")),
            latest_proposal=({"id": latest["id"], "score": latest["score"], "created_at": latest["created_at"],
                              "applied_at": latest["applied_at"]} if latest else None),
        )

    def inbox(self, args: dict) -> str:
        state = args.get("state") or "inbox"
        keep = ("id", "title", "state", "duration_minutes", "priority", "cognitive_load", "earliest_at",
                "deadline_kind", "deadline_at")
        titles = {t["id"]: t["title"] for t in self.store.tasks("all")}
        return _ok(tasks=[{k: t[k] for k in keep} for t in self.store.tasks(state)],
                   order_rules=[{"before": titles.get(b, b), "after": titles.get(a, a), "before_id": b,
                                 "after_id": a} for b, a in self.store.dependencies()])

    def _task_fields(self, args: dict, tz: str | None) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if "title" in args:
            title = str(args["title"] or "").strip()
            if not title:
                raise ValueError("title is required")
            fields["title"] = title[:200]
        if args.get("duration_minutes") is not None:
            minutes = int(args["duration_minutes"])
            if not 5 <= minutes <= 24 * 60:
                raise ValueError("duration_minutes must be between 5 and 1440")
            fields["duration_minutes"] = minutes
        for key, allowed in (("priority", ("low", "normal", "high")),
                             ("cognitive_load", ("low", "medium", "high")),
                             ("deadline_kind", ("none", "hard", "soft"))):
            if args.get(key):
                if args[key] not in allowed:
                    raise ValueError(f"{key} must be one of {', '.join(allowed)}")
                fields[key] = args[key]
        for key in ("earliest_at", "deadline_at"):
            if args.get(key):
                fields[key] = self.wall_clock(args[key], tz)
        if fields.get("deadline_at") and not fields.get("deadline_kind"):
            fields["deadline_kind"] = "hard"
        for key in args.get("clear") or []:
            if key in ("earliest_at", "deadline_at"):
                fields[key] = None
                if key == "deadline_at":
                    fields["deadline_kind"] = "none"
        return fields

    def task_create(self, args: dict) -> str:
        if not str(args.get("title") or "").strip():
            raise ValueError("title is required")
        if args.get("duration_minutes") is None:
            raise ValueError("duration_minutes is required")
        task = self.store.create_task(self._task_fields(args, self.timezone()))
        self.changed("tasks")
        return _ok(task=task, next="planner_optimize when the inbox is complete")

    def _task_or_fail(self, task_id: str) -> dict:
        task = self.store.task(str(task_id))
        if task is None:
            raise EngineError("not_found", f"no task with id {task_id!r}; see planner_inbox")
        return task

    def task_update(self, args: dict) -> str:
        task = self._task_or_fail(args["id"])
        fields = self._task_fields({k: v for k, v in args.items() if k != "id"}, self.timezone())
        if not fields:
            raise ValueError("nothing to change")
        if task["state"] == "applied":
            raise EngineError("applied", "that task is on the calendar; planner_task_unschedule it first")
        updated = self.store.update_task(task["id"], fields)
        self.changed("tasks")
        return _ok(task=updated)

    def task_delete(self, args: dict) -> str:
        task = self._task_or_fail(args["id"])
        if task["state"] == "applied":
            raise EngineError("applied", "that task is on the calendar; planner_task_unschedule it first "
                                         "(the user approves taking it off)")
        self.store.delete_task(task["id"])
        self.changed("tasks")
        return _ok(deleted=task["id"], title=task["title"])

    def task_unschedule(self, args: dict) -> str:
        task = self._task_or_fail(args["id"])
        if task["state"] != "applied":
            raise EngineError("not_applied", "that task is not on the calendar")
        on_calendar = any(e["seen_at"] or e["handed_at"] for e in self.store.events(task["id"]))
        updated = self.store.unschedule(task["id"])
        self._write_local_calendar()
        self.changed("tasks")
        return _ok(task=updated, next=("Its event comes off the user's calendar at the next planner_push. "
                                       if on_calendar else "") + "planner_optimize to place the task again.")

    def depend(self, args: dict) -> str:
        before, after = self._task_or_fail(args["before"]), self._task_or_fail(args["after"])
        if before["id"] == after["id"]:
            raise ValueError("a task cannot come before itself")
        if args.get("action") == "add":
            if self._reaches(after["id"], before["id"]):
                raise EngineError("cycle", f"'{after['title']}' already comes before '{before['title']}'")
            self.store.add_dependency(before["id"], after["id"])
        else:
            self.store.remove_dependency(before["id"], after["id"])
        self.changed("tasks")
        return _ok(action=args.get("action"), before=before["title"], after=after["title"])

    def _reaches(self, start: str, goal: str) -> bool:
        edges: dict[str, list[str]] = {}
        for b, a in self.store.dependencies():
            edges.setdefault(b, []).append(a)
        seen, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node == goal:
                return True
            if node not in seen:
                seen.add(node)
                stack.extend(edges.get(node, []))
        return False

    def settings_tool(self, args: dict) -> str:
        if (args.get("action") or "show") == "show":
            return _ok(settings=self.store.settings())
        values: dict[str, Any] = {}
        if args.get("timezone"):
            try:
                ZoneInfo(args["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"unknown timezone {args['timezone']!r}; use an IANA name like Europe/Rome")
            values["timezone"] = args["timezone"]
        if args.get("availability") is not None:
            windows: dict[str, list[dict]] = {}
            for entry in args["availability"]:
                match = _AVAIL.match(str(entry))
                if not match or match.group(1).lower() not in WEEKDAYS:
                    raise ValueError(f"availability entries look like 'mon=09:00-17:00', got {entry!r}")
                day, start, end = match.group(1).lower(), match.group(2).zfill(5), match.group(3).zfill(5)
                if end <= start:
                    raise ValueError(f"{entry!r} ends before it starts")
                windows.setdefault(day, []).append({"start": start, "end": end})
            values["availability"] = windows
        for key, lo, hi in (("horizon_days", 1, 60), ("slot_minutes", 5, 60), ("solve_seconds", 1, 60)):
            if args.get(key) is not None:
                value = int(args[key])
                if not lo <= value <= hi:
                    raise ValueError(f"{key} must be between {lo} and {hi}")
                values[key] = value
        if "publish_calendar" in args:
            values["publish_calendar"] = str(args["publish_calendar"] or "").strip()[:120] or None
        for arg, key in (("focus_window", "high_window"), ("shallow_window", "low_window")):
            if arg in args:
                if not args[arg]:
                    values[key] = None
                    continue
                match = _WINDOW.match(str(args[arg]))
                if not match:
                    raise ValueError(f"{arg} must look like 09:00-12:00")
                values[key] = {"start": match.group(1).zfill(5), "end": match.group(2).zfill(5),
                               "outside_penalty": 10}
        settings = self.store.update_settings(values)
        self.changed("settings")
        return _ok(settings=settings)

    def _busy_rows(self, events: Iterable[dict], tz: str | None) -> tuple[list[dict], dict[str, tuple]]:
        """Busy rows, and the planner's own events among them (event id -> calendar times)."""
        known = self.store.known_event_ids()
        rows, seen = [], {}
        for event in events:
            start, end = str(event.get("start") or ""), str(event.get("end") or "")
            title = str(event.get("title") or "Busy").strip()[:200] or "Busy"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                first = date.fromisoformat(start)
                last = date.fromisoformat(end) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) else first
                last = max(last, first + timedelta(days=1))
                s, e = f"{first} 00:00:00", f"{last} 00:00:00"
            else:
                s, e = self.wall_clock(start, tz), self.wall_clock(end, tz)
                if e <= s:
                    raise ValueError(f"event {title!r} ends before it starts")
            own = calendars.planner_ids(event.get("id"), event.get("uid"), event.get("description")) & known
            if own:
                # A block of ours: pinned as applied, not foreign busy time.
                seen[own.pop()] = (s, e, str(event.get("id") or "") or None)
                continue
            rows.append({"title": title, "start": s, "end": e})
        return rows, seen

    def _source(self, args: dict) -> str:
        source = re.sub(r"\s+", " ", str(args.get("source") or "")).strip()[:60]
        if not source:
            raise ValueError("source is required (e.g. 'google: work' or 'out of office')")
        return source

    def busy_set(self, args: dict) -> str:
        source = self._source(args)
        if args.get("remove"):
            if not self.store.forget_source(source):
                raise EngineError("not_found", f"no busy-time source {source!r}; see planner_status")
            self.changed("busy")
            return _ok(source=source, removed=True)
        kind = args.get("kind") or "calendar"
        if kind not in ("calendar", "other"):
            raise ValueError("kind must be 'calendar' or 'other'")
        tz = self.timezone()
        if kind == "calendar" and not tz:
            raise ValueError("set the planner timezone first")
        window = None
        if kind == "calendar":
            if not (args.get("window_start") and args.get("window_end")):
                raise ValueError("a calendar read needs window_start and window_end: the range you read")
            window = (self.wall_clock(args["window_start"], tz), self.wall_clock(args["window_end"], tz))
            if window[1] <= window[0]:
                raise ValueError("window_end must be after window_start")
        rows, own = self._busy_rows(args.get("events") or [], tz)  # validate before replacing
        if window is None:
            stored = self.store.replace_busy(source, rows)
            self.changed("busy")
            return _ok(source=source, kind=kind, stored=stored, next="planner_optimize")
        events = self.store.events()
        live = {e["event_id"]: e for e in events}
        seen = {k: v for k, v in own.items() if k in live}
        # An event that vanished from an earlier pull and shows up again was moved, not deleted: its task
        # goes back there if it is still waiting in the inbox; otherwise the event is the user's now.
        released = self.store.released()
        tasks = {t["id"]: t for t in self.store.tasks()}
        back, kept = [], []
        for event_id in [k for k in own if k in released]:
            task = tasks.get(released[event_id])
            start, end, cloud = own.pop(event_id)
            if task and task["state"] == "inbox":
                back.append((event_id, task, start, end, cloud))
            else:
                self.store.forget_released(event_id)
                rows.append({"title": task["title"] if task else "(planner event)", "start": start, "end": end})
                kept.append({"title": task["title"] if task else None, "start": start})
        # The planner's own events this calendar showed before and no longer does were deleted there:
        # those tasks go back to the inbox instead of being pushed back.
        gone = [e for e in events if e["seen_in"] == source and e["event_id"] not in seen
                and e["start"] < window[1] and e["end"] > window[0]]
        # Removals the calendar no longer shows are done; those it still shows get their id for the delete.
        removals = [r for r in self.store.removals() if r["calendar"] in (None, source)
                    and r["start"] < window[1] and r["end"] > window[0]]
        done = [r["event_id"] for r in removals if r["event_id"] not in own]
        moved = [{"event_id": k, "title": live[k]["title"], "from": live[k]["start"], "to": v[0]}
                 for k, v in seen.items() if (live[k]["start"], live[k]["end"]) != v[:2]]
        stored = self.store.replace_busy(source, rows, window, seen)
        for r in removals:
            if r["event_id"] in own:
                self.store.set_removal_seen(r["event_id"], source, own[r["event_id"]][2])
        self.store.confirm_removed(done)
        unscheduled = []
        for e in gone:
            self.store.unschedule(e["task_id"])
            self.store.confirm_removed([e["event_id"]])
            self.store.release(e["event_id"], e["task_id"])
            unscheduled.append({"task_id": e["task_id"], "title": e["title"], "was": e["start"]})
        returned = []
        for event_id, task, start, end, cloud in back:
            self.store.readopt(event_id, task["id"], start, end, source, cloud)
            returned.append({"task_id": task["id"], "title": task["title"], "at": start})
        if moved or unscheduled or returned:
            self._write_local_calendar()
        self.changed("busy")
        return _ok(source=source, kind=kind, stored=stored, planner_events_found=len(seen),
                   moved_on_calendar=moved, removed_on_calendar=unscheduled, back_on_calendar=returned,
                   now_the_users=kept,
                   next=("Tell the user which tasks went back to the inbox because their events are gone from "
                         "the calendar (deleted, or moved outside the pulled days; one that shows up again is "
                         "pinned there again). " if unscheduled else "")
                   + "planner_optimize, planner_apply if a plan is waiting, or planner_push.")

    def busy_from_ics(self, args: dict) -> str:
        source = self._source(args)
        url, path = (args.get("url") or "").strip(), (args.get("path") or "").strip()
        if bool(url) == bool(path):
            raise ValueError("pass exactly one of url or path")
        tz = self.timezone()
        if not tz:
            raise ValueError("set the planner timezone first")
        text = self._download_ics(url) if url else Path(path).expanduser().read_text(errors="replace")
        start, end = self._calendar_window()
        known = self.store.known_event_ids()
        events = [b for b in calendars.busy_blocks(text, tz, start, end)
                  if not calendars.planner_ids(b["uid"], b["description"]) & known]
        stored = self.store.replace_busy(source, events)
        self.changed("busy")
        return _ok(source=source, stored=stored, window={"start": str(start), "end": str(end)},
                   next="planner_optimize")

    def _download_ics(self, url: str) -> str:
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        if urllib.parse.urlparse(url).scheme not in ("https", "http"):
            raise ValueError("ICS url must be https, http or webcal")
        request = urllib.request.Request(url, headers={"User-Agent": "hermes-solverforge-planner"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(ICS_MAX_BYTES + 1)
        if len(body) > ICS_MAX_BYTES:
            raise ValueError("ICS feed is larger than 10 MB")
        if b"BEGIN:VCALENDAR" not in body[:4096]:
            raise ValueError("that URL did not return an iCalendar feed")
        return body.decode("utf-8", errors="replace")

    def week(self, args: dict) -> str:
        tz = self.timezone()
        first = (date.fromisoformat(self.wall_clock(args["start"], tz)[:10]) if args.get("start")
                 else date.fromisoformat(now_local(tz)[:10]))
        last = first + timedelta(days=int(args.get("days") or 7))
        lo, hi = f"{first} 00:00:00", f"{last} 00:00:00"
        events = [{"title": b["title"], "start": b["start"], "end": b["end"], "kind": "busy", "source": b["source"]}
                  for b in self.store.busy(lo, hi)]
        events += [{"title": a["title"], "start": a["start"], "end": a["end"], "kind": "planned",
                    "task_id": a["task_id"]} for a in self.store.applied(lo, hi)]
        return _ok(window={"start": str(first), "end": str(last)},
                   events=sorted(events, key=lambda e: e["start"]),
                   clashes=[c for c in self.clashes() if lo <= c["start"] < hi])

    def clashes(self) -> list[dict]:
        """Blocks already on the plan with a pulled calendar event now on top of them."""
        names = {c["source"] for c in self.store.calendars()}
        found = []
        for block in self.store.applied():
            for hit in self.store.busy(block["start"], block["end"]):
                if hit["source"] in names:
                    found.append({"task_id": block["task_id"], "title": block["title"], "start": block["start"],
                                  "end": block["end"], "with": hit["title"], "with_start": hit["start"],
                                  "with_end": hit["end"], "calendar": hit["source"]})
        return found

    def optimize(self, args: dict) -> str:
        problems = self.problems()
        if problems:
            raise EngineError("not_ready", "; ".join(problems))
        if not self.store.tasks("inbox"):
            raise EngineError("empty_inbox", "there are no unscheduled tasks; add some with planner_task_create")
        horizon = int(args.get("horizon_days") or self.store.settings()["horizon_days"])
        lo, hi = self._calendar_window(horizon)
        read = self._require_fresh((lo.strftime(STAMP), hi.strftime(STAMP)))
        problem = self.problem()
        problem["settings"]["horizon_days"] = horizon
        parts = self.store.fingerprint_parts()
        body = self.engine.solve(problem)
        proposal_id = self.store.save_proposal(body, parts)
        summary = self.describe(self.store.proposal(proposal_id))  # type: ignore[arg-type]
        self.changed("proposal", proposal_id=proposal_id)
        return _ok(**summary, calendars_read=[c["source"] for c in read], next=(
            f"Show it by writing {summary['display']} on its own line, list the blocks by day, explain "
            "each unscheduled task, and call planner_apply only after the user agrees."))

    def proposal(self, args: dict) -> str:
        proposal = self.store.proposal(args.get("id") or None)
        if proposal is None:
            raise EngineError("no_proposal", "no proposal yet; call planner_optimize")
        return _ok(**self.describe(proposal))

    def apply(self, args: dict) -> str:
        """Pin a proposal's blocks on the planner's own calendar. Nothing leaves this computer here:
        planner_push puts the planner's calendar on a cloud calendar."""
        proposal = self.store.proposal(str(args["id"]))
        if proposal is None:
            raise EngineError("not_found", f"no proposal {args['id']!r}")
        tz = proposal["body"]["timezone"]
        items = [i for i in proposal["body"]["items"] if i["scheduled"]]
        reasons = self.store.stale_reasons(proposal)
        if reasons:
            raise EngineError("stale", f"cannot apply: {', '.join(reasons)}; run planner_optimize again")
        if items:
            window = (min(i["start_at"] for i in items), max(i["end_at"] for i in items))
            made = (datetime.strptime(proposal["made_at"], READ_STAMP) if proposal.get("made_at")
                    else datetime.strptime(proposal["created_at"], STAMP) + timedelta(seconds=1))
            self._require_fresh(window, after=made)
        conflicts = self._conflicts(items)
        if conflicts:
            raise EngineError("calendar_conflict", "new on the calendar since this plan was made: "
                              + "; ".join(conflicts) + ". Run planner_optimize again.")
        self.store.apply(proposal, {i["task_id"]: calendars.event_id(i["task_id"], i["start_at"], i["end_at"],
                                                                       proposal["id"]) for i in items})
        self.changed("applied", proposal_id=proposal["id"])
        mine = {i["task_id"] for i in items}
        export = self.export_ics(None, f"proposal-{proposal['id'][:8]}.ics", only=mine)
        local = self._write_local_calendar()
        written = [{"task_id": e["task_id"], "title": e["title"], "start": e["start"], "end": e["end"]}
                   for e in self.store.events() if e["task_id"] in mine]
        target = self._push_target()
        return _ok(proposal_id=proposal["id"], written=written, timezone=tz, ics=str(export),
                   local_calendar=str(local), push_to=target,
                   next=(f"The blocks are on the planner's calendar. Offer to put them on the user's '{target}' "
                         "calendar too: planner_push." if target else
                         f"The blocks are on the planner's calendar. Offer the file by writing MEDIA:{export} on its "
                         "own line; it imports into any calendar app, and importing it again updates instead of "
                         "duplicating."))

    def _push_target(self, requested: str | None = None) -> str | None:
        """The calendar source planner_push writes to: the one asked for, the one saved in the settings,
        else the only calendar source there is."""
        names = [c["source"] for c in self.store.calendars()]
        for wanted in (requested, self.store.settings().get("publish_calendar")):
            if wanted:
                if wanted not in names:
                    raise EngineError("not_found", f"no calendar source {wanted!r}; pull it first with "
                                      f"planner_busy_set kind=calendar (calendars: {', '.join(names) or 'none'})")
                return wanted
        return names[0] if len(names) == 1 else None

    def push(self, args: dict) -> str:
        """What to change on a cloud calendar so it shows the planner's calendar: events to create and
        events to delete, all of them the planner's own. Needs a fresh pull of that calendar, and after
        a hand-out a pull showing what arrived, so nothing is ever created twice."""
        target = self._push_target(args.get("calendar"))
        if target is None:
            names = [c["source"] for c in self.store.calendars()]
            raise EngineError("no_calendar", "which calendar? " + (
                f"pass calendar= one of {', '.join(names)}, or save it with planner_settings publish_calendar"
                if names else "pull the user's calendar first with planner_busy_set kind=calendar"))
        cal = next(c for c in self.store.calendars() if c["source"] == target)
        read = datetime.strptime(cal["read_at"], READ_STAMP)
        if datetime.now() - read > timedelta(minutes=FRESH_MINUTES):
            raise EngineError("calendar_stale", f"pull {target} again first (last pull {cal['read_at'][:16]}): a push "
                                                "is worked out from what that calendar shows now")
        events = self.store.events()
        removals = [r for r in self.store.removals() if r["calendar"] in (None, target)]
        # Handed out and not yet confirmed by a pull: created events not seen yet, deletes still there.
        pending = [e["handed_at"] for e in events if e["handed_at"] and not e["seen_at"]]
        pending += [r["handed_at"] for r in removals if r["handed_at"]]
        if pending and read <= datetime.strptime(max(pending), READ_STAMP):
            raise EngineError("calendar_stale", (
                f"changes were handed out at {max(pending)[11:16]}; pull {target} again first, so the planner "
                "sees which were made. Pushing before that could create an event twice."))
        tz = self.timezone() or "UTC"
        create = [e for e in events if not e["seen_at"]]
        delete = [r for r in removals if r["cloud_id"]]
        self.store.mark_handed_out([e["event_id"] for e in create])
        self.store.mark_removal_handed([r["event_id"] for r in delete])
        return _ok(
            calendar=target,
            create=[self._event_payload(e, tz) for e in create],
            delete=[{"event_id": r["event_id"], "id": r["cloud_id"], "title": r["title"], "start": r["start"]}
                    for r in delete],
            already_there=sum(1 for e in events if e["seen_at"]),
            next=(("Make these changes on the user's calendar with their calendar tool: create each event "
                   "in create with its title, start, end and description exactly (and event_id as the "
                   "event's id when the tool takes one; an 'already exists' answer means it is there), and "
                   "delete each event in delete by its id. Then pull the calendar again (planner_busy_set "
                   "kind=calendar) so the planner confirms them.") if create or delete else
                  f"{target} already shows the planner's calendar."))

    def _push_summary(self) -> dict | None:
        """How far the cloud calendar is behind the planner's calendar, as of the last pull."""
        try:
            target = self._push_target()
        except EngineError:
            target = None
        if target is None:
            return None
        return {"calendar": target,
                "create": sum(1 for e in self.store.events() if not e["seen_at"]),
                "delete": sum(1 for r in self.store.removals() if r["calendar"] in (None, target))}

    def _write_local_calendar(self) -> Path:
        """planner.ics: the planner's own calendar, rewritten whenever a block changes."""
        return self.export_ics(None, "planner.ics")

    def export_ics(self, blocks: list[dict] | None, name: str, only: set[str] | None = None) -> Path:
        """Write blocks (default: every applied block, or those of the ``only`` tasks) as an .ics file,
        each under its fixed event id so importing it twice updates instead of duplicating."""
        tz = self.timezone() or "UTC"
        if blocks is None:
            blocks = [{"event_id": e["event_id"], "task_id": e["task_id"], "title": e["title"], "start": e["start"],
                       "end": e["end"]} for e in self.store.events() if only is None or e["task_id"] in only]
        path = self.data_dir / "exports" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ics.calendar(blocks, tz), encoding="utf-8")
        return path

    # -- approval text ----------------------------------------------------

    def approval_text(self, tool_name: str, args: dict) -> str:
        """The sentence the human sees in the approval prompt."""
        if tool_name == "planner_apply":
            proposal = self.store.proposal(str(args.get("id") or ""))
            if proposal is None:
                return "Apply a planner proposal that does not exist"
            summary = self.describe(proposal)
            blocks = "; ".join(f"{s['title']} {s['day']} {s['start'][11:16]}-{s['end'][11:16]}"
                               for s in summary["scheduled"][:8])
            more = len(summary["scheduled"]) - 8
            return (f"Put {len(summary['scheduled'])} block(s) on the planner's calendar, score {summary['score']}: "
                    f"{blocks}{f' (+{more} more)' if more > 0 else ''}")
        if tool_name == "planner_task_unschedule":
            task = self.store.task(str(args.get("id") or ""))
            where = " (its event leaves your calendar at the next push)" if task and self.store.events(task["id"]) else ""
            return f"Take '{task['title'] if task else args.get('id')}' off the calendar and back to the inbox{where}"
        return tool_name


METHODS = {
    "planner_status": "status", "planner_inbox": "inbox",
    "planner_task_create": "task_create", "planner_task_update": "task_update",
    "planner_task_delete": "task_delete", "planner_task_unschedule": "task_unschedule",
    "planner_depend": "depend", "planner_settings": "settings_tool",
    "planner_busy_set": "busy_set", "planner_busy_from_ics": "busy_from_ics",
    "planner_week": "week", "planner_optimize": "optimize",
    "planner_proposal": "proposal", "planner_apply": "apply", "planner_push": "push",
}


def tool_table(resolve: "Planner | Callable[[], Planner]"):
    """Pair each schema with its handler, in schema order.

    ``resolve`` is a planner or a zero-argument function returning one; with a
    function, the planner is looked up on every call, so a process serving
    several Hermes profiles reaches the store of the profile being served.
    """
    from . import schemas as s

    get = resolve if callable(resolve) and not isinstance(resolve, Planner) else (lambda: resolve)
    for schema in s.ALL:
        method = METHODS[schema["name"]]

        def call(args: dict | None = None, _method: str = method, **kw: Any) -> str:
            planner = get()
            return planner.handler(getattr(planner, _method))(args, **kw)

        call.__name__ = method
        yield schema, call
