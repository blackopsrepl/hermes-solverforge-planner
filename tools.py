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
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ics
from .engine_bin import Engine, EngineError, install_hint
from .store import Store, now_local

ICS_MAX_BYTES = 10 * 1024 * 1024
DIRECTIVE = '::planner{{proposal="{id}"}}'
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WINDOW = re.compile(r"^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$")
_AVAIL = re.compile(r"^\s*([a-z]{3})[a-z]*\s*=\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$", re.I)
STAMP = "%Y-%m-%d %H:%M:%S"


def _ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _fail(code: str, message: str) -> str:
    return json.dumps({"ok": False, "code": code, "error": message}, ensure_ascii=False)


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
                return _fail(exc.code, exc.message)
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
        self.store.delete_task(task["id"])
        self.changed("tasks")
        return _ok(deleted=task["id"], title=task["title"])

    def task_unschedule(self, args: dict) -> str:
        task = self._task_or_fail(args["id"])
        if task["state"] != "applied":
            raise EngineError("not_applied", "that task is not on the calendar")
        updated = self.store.unschedule(task["id"])
        self.changed("tasks")
        return _ok(task=updated, next="planner_optimize to place it again")

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

    def _busy_rows(self, events: Iterable[dict], tz: str | None) -> list[dict]:
        rows = []
        for event in events:
            start, end = str(event.get("start") or ""), str(event.get("end") or "")
            title = str(event.get("title") or "Busy").strip()[:200] or "Busy"
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                first = date.fromisoformat(start)
                last = date.fromisoformat(end) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) else first
                last = max(last, first + timedelta(days=1))
                rows.append({"title": title, "start": f"{first} 00:00:00", "end": f"{last} 00:00:00"})
                continue
            s, e = self.wall_clock(start, tz), self.wall_clock(end, tz)
            if e <= s:
                raise ValueError(f"event {title!r} ends before it starts")
            rows.append({"title": title, "start": s, "end": e})
        return rows

    def _source(self, args: dict) -> str:
        source = re.sub(r"\s+", " ", str(args.get("source") or "")).strip()[:40]
        if not source:
            raise ValueError("source is required (e.g. 'google' or 'work')")
        return source

    def busy_set(self, args: dict) -> str:
        source = self._source(args)
        rows = self._busy_rows(args.get("events") or [], self.timezone())  # validate before replacing
        stored = self.store.replace_busy(source, rows)
        self.changed("busy")
        return _ok(source=source, stored=stored, next="planner_optimize")

    def busy_from_ics(self, args: dict) -> str:
        source = self._source(args)
        url, path = (args.get("url") or "").strip(), (args.get("path") or "").strip()
        if bool(url) == bool(path):
            raise ValueError("pass exactly one of url or path")
        tz = self.timezone()
        if not tz:
            raise ValueError("set the planner timezone first")
        text = self._download_ics(url) if url else Path(path).expanduser().read_text(errors="replace")
        events, skipped = ics.busy_events(text, tz)
        stored = self.store.replace_busy(source, events)
        self.changed("busy")
        return _ok(source=source, stored=stored, skipped_recurring=skipped,
                   note=("Recurring events are not expanded from ICS; pass them with planner_busy_set "
                         "from a calendar tool that expands them." if skipped else None),
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
                   events=sorted(events, key=lambda e: e["start"]))

    def optimize(self, args: dict) -> str:
        problems = self.problems()
        if problems:
            raise EngineError("not_ready", "; ".join(problems))
        if not self.store.tasks("inbox"):
            raise EngineError("empty_inbox", "there are no unscheduled tasks; add some with planner_task_create")
        problem = self.problem()
        if args.get("horizon_days"):
            problem["settings"]["horizon_days"] = int(args["horizon_days"])
        parts = self.store.fingerprint_parts()
        body = self.engine.solve(problem)
        proposal_id = self.store.save_proposal(body, parts)
        summary = self.describe(self.store.proposal(proposal_id))  # type: ignore[arg-type]
        self.changed("proposal", proposal_id=proposal_id)
        return _ok(**summary, next=(
            f"Show it by writing {summary['display']} on its own line, list the blocks by day, explain "
            "each unscheduled task, and call planner_apply only after the user agrees."))

    def proposal(self, args: dict) -> str:
        proposal = self.store.proposal(args.get("id") or None)
        if proposal is None:
            raise EngineError("no_proposal", "no proposal yet; call planner_optimize")
        return _ok(**self.describe(proposal))

    def apply(self, args: dict) -> str:
        proposal = self.store.proposal(str(args["id"]))
        if proposal is None:
            raise EngineError("not_found", f"no proposal {args['id']!r}")
        reasons = self.store.stale_reasons(proposal)
        if reasons:
            raise EngineError("stale", f"cannot apply: {', '.join(reasons)}; run planner_optimize again")
        written = self.store.apply(proposal)
        titles = {t["id"]: t["title"] for t in self.store.tasks("all")}
        blocks = [{"task_id": i["task_id"], "title": titles.get(i["task_id"], ""), "start": i["start_at"],
                   "end": i["end_at"]} for i in written]
        export = self.export_ics(blocks, f"proposal-{proposal['id'][:8]}.ics")
        self.export_ics(None, "planner.ics")
        self.changed("applied", proposal_id=proposal["id"])
        return _ok(proposal_id=proposal["id"], written=blocks, timezone=proposal["body"]["timezone"],
                   ics=str(export),
                   next=(f"Offer the calendar file by writing MEDIA:{export} on its own line. If you have a "
                         "calendar tool or skill for the user's calendar, offer to add 'written' there too."))

    def export_ics(self, blocks: list[dict] | None, name: str) -> Path:
        """Write blocks (default: every applied block) as an .ics file; return its path."""
        tz = self.timezone() or "UTC"
        if blocks is None:
            blocks = [{"task_id": a["task_id"], "title": a["title"], "start": a["start"], "end": a["end"]}
                      for a in self.store.applied()]
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
            return (f"Put {len(summary['scheduled'])} block(s) on the calendar, score {summary['score']}: "
                    f"{blocks}{f' (+{more} more)' if more > 0 else ''}")
        if tool_name == "planner_task_unschedule":
            task = self.store.task(str(args.get("id") or ""))
            return f"Take '{task['title'] if task else args.get('id')}' off the calendar and back to the inbox"
        return tool_name


def tool_table(planner: Planner):
    """Pair each schema with its handler, in schema order."""
    from . import schemas as s

    methods = {
        "planner_status": planner.status, "planner_inbox": planner.inbox,
        "planner_task_create": planner.task_create, "planner_task_update": planner.task_update,
        "planner_task_delete": planner.task_delete, "planner_task_unschedule": planner.task_unschedule,
        "planner_depend": planner.depend, "planner_settings": planner.settings_tool,
        "planner_busy_set": planner.busy_set, "planner_busy_from_ics": planner.busy_from_ics,
        "planner_week": planner.week, "planner_optimize": planner.optimize,
        "planner_proposal": planner.proposal, "planner_apply": planner.apply,
    }
    for schema in s.ALL:
        yield schema, planner.handler(methods[schema["name"]])
