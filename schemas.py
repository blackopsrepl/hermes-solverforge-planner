"""Tool schemas: what the model reads to decide when and how to call each tool."""

from __future__ import annotations

_TS = "Wall-clock 'YYYY-MM-DD HH:MM' or ISO 8601 (an offset is converted to the planner timezone)."

_TASK_FIELDS = {
    "title": {"type": "string", "description": "Short task title."},
    "duration_minutes": {"type": "integer", "minimum": 5, "description": "How long the work takes."},
    "priority": {"type": "string", "enum": ["low", "normal", "high"]},
    "cognitive_load": {
        "type": "string",
        "enum": ["low", "medium", "high"],
        "description": "high = deep focus work, low = admin/shallow work.",
    },
    "earliest_at": {"type": "string", "description": f"Do not start before this. {_TS}"},
    "deadline_kind": {
        "type": "string",
        "enum": ["none", "hard", "soft"],
        "description": "hard = must finish by deadline_at; soft = prefer to.",
    },
    "deadline_at": {"type": "string", "description": f"Deadline. {_TS}"},
    "clear": {
        "type": "array",
        "items": {"type": "string", "enum": ["earliest_at", "deadline_at"]},
        "description": "Optional fields to remove.",
    },
}

STATUS = {
    "name": "planner_status",
    "description": (
        "Check that the planner is ready: engine installed, timezone, working hours, focus windows, "
        "the calendars it plans around (with when each was last read), the calendar plans go to, "
        "planned events not yet seen on the calendar, inbox size and the latest proposal. Call this "
        "first in a planning conversation."
    ),
    "parameters": {"type": "object", "properties": {}},
}

INBOX = {
    "name": "planner_inbox",
    "description": "List planner tasks with their state (inbox = not yet scheduled, applied = on the calendar) and task dependencies.",
    "parameters": {
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["inbox", "applied", "all"], "default": "inbox"},
        },
    },
}

TASK_CREATE = {
    "name": "planner_task_create",
    "description": (
        "Add a task to the planner inbox. This does not schedule it; call planner_optimize after "
        "adding or changing tasks."
    ),
    "parameters": {
        "type": "object",
        "properties": {k: v for k, v in _TASK_FIELDS.items() if k != "clear"},
        "required": ["title", "duration_minutes"],
    },
}

TASK_UPDATE = {
    "name": "planner_task_update",
    "description": "Change an inbox task. Pass only the fields to change; use clear to remove optional fields.",
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "Task id from planner_inbox."},
            **_TASK_FIELDS,
        },
        "required": ["id"],
    },
}

TASK_DELETE = {
    "name": "planner_task_delete",
    "description": "Delete a task that is not on the calendar. An applied task is unscheduled first.",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
}

TASK_UNSCHEDULE = {
    "name": "planner_task_unschedule",
    "description": (
        "Move an applied task back to the inbox so the next planner_optimize can place it again. "
        "Returns the planner's events for it in delete_from_calendar, for you to delete with the "
        "user's calendar tool. Requires the user's approval."
    ),
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
}

DEPEND = {
    "name": "planner_depend",
    "description": "Add or remove an ordering rule between two tasks: 'before' must finish before 'after' starts.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove"]},
            "before": {"type": "string", "description": "Task id that comes first."},
            "after": {"type": "string", "description": "Task id that comes second."},
        },
        "required": ["action", "before", "after"],
    },
}

SETTINGS = {
    "name": "planner_settings",
    "description": (
        "Show or change planning settings. Availability replaces the whole working-hours set when "
        "given, as entries like 'mon=09:00-13:00'; repeat a day for split shifts. Focus windows "
        "steer high/low cognitive-load work into preferred hours (soft)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["show", "update"], "default": "show"},
            "timezone": {"type": "string", "description": "IANA name, e.g. Europe/Rome."},
            "availability": {"type": "array", "items": {"type": "string"}},
            "horizon_days": {"type": "integer", "minimum": 1, "maximum": 60},
            "slot_minutes": {"type": "integer", "enum": [5, 10, 15, 30, 60]},
            "solve_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
            "focus_window": {"type": "string", "description": "Preferred hours for high-load work, 'HH:MM-HH:MM'."},
            "shallow_window": {"type": "string", "description": "Preferred hours for low-load work, 'HH:MM-HH:MM'."},
            "publish_calendar": {"type": "string", "description": "Which of the user's calendars planned blocks go to, as they name it, e.g. 'Google: Planner'."},
        },
    },
}

BUSY_SET = {
    "name": "planner_busy_set",
    "description": (
        "Tell the planner what is already on the user's time. kind=calendar: the events you just "
        "read from one of the user's calendars with their calendar tool (google-workspace, "
        "microsoft365, ...), every event in window_start..window_end, recurring ones as their "
        "single occurrences. Once a calendar has been passed, every planner_optimize needs it "
        "read again within 15 minutes, and every planner_apply needs it read after the plan was "
        "made. Pass each event's id and description too: that is how the planner finds its own "
        "events. kind=other: time the user tells you about ('I'm out Thursday afternoon'), kept "
        "until replaced. Each call replaces everything stored for that source; remove=true drops "
        "a source the user no longer wants planned around."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Stable name, e.g. 'google: work' or 'out of office'."},
            "kind": {"type": "string", "enum": ["calendar", "other"], "default": "calendar"},
            "window_start": {"type": "string", "description": "kind=calendar: start of the range you read. " + _TS},
            "window_end": {"type": "string", "description": "kind=calendar: end of the range you read. " + _TS},
            "remove": {"type": "boolean", "description": "Forget this source entirely."},
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start": {"type": "string", "description": _TS + " A bare date means all day."},
                        "end": {"type": "string", "description": _TS},
                        "id": {"type": "string", "description": "The calendar's event id."},
                        "description": {"type": "string", "description": "The event's description or notes."},
                    },
                    "required": ["start", "end"],
                },
            },
        },
        "required": ["source"],
    },
}

BUSY_ICS = {
    "name": "planner_busy_from_ics",
    "description": (
        "Load busy time once from an iCalendar file or link the user gives in chat (an https/webcal "
        "URL or a local .ics path), with recurring events expanded. Replaces previously loaded "
        "events for this source. Kept until replaced (kind=other); a calendar the user keeps using "
        "is read with their calendar tool and passed with planner_busy_set kind=calendar instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "url": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["source"],
    },
}

WEEK = {
    "name": "planner_week",
    "description": "List busy time and planned (applied) blocks for a window of days.",
    "parameters": {
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": f"Window start; default today. {_TS}"},
            "days": {"type": "integer", "minimum": 1, "maximum": 31, "default": 7},
        },
    },
}

OPTIMIZE = {
    "name": "planner_optimize",
    "description": (
        "Solve the schedule: place every inbox task into free working time around the busy time, "
        "the tasks already applied, deadlines, ordering rules and focus windows. Refused with "
        "calendar_stale until every calendar source has been read again (see planner_busy_set). "
        "Creates a PROPOSAL only; nothing is written until planner_apply. Returns the score (0hard "
        "= every hard rule kept), each placement and why any task did not fit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "horizon_days": {"type": "integer", "minimum": 1, "maximum": 60},
        },
    },
}

PROPOSAL = {
    "name": "planner_proposal",
    "description": "Show a proposal by id, or the most recent one, with whether it can still be applied.",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
    },
}

APPLY = {
    "name": "planner_apply",
    "description": (
        "Pin a proposal's blocks after the user approves. Needs every calendar source read again "
        "after the plan was made (calendar_stale otherwise), and is refused if anything there now "
        "overlaps a block, or if tasks or settings changed. Returns create_on_calendar: the events "
        "for you to create with the user's calendar tool, each with a fixed event_id. Calling it "
        "again on an applied proposal returns only the events the calendar does not show yet, so "
        "nothing is ever added twice. Also returns an .ics file."
    ),
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Proposal id from planner_optimize."}},
        "required": ["id"],
    },
}

ALL = [
    STATUS, INBOX, TASK_CREATE, TASK_UPDATE, TASK_DELETE, TASK_UNSCHEDULE, DEPEND,
    SETTINGS, BUSY_SET, BUSY_ICS, WEEK, OPTIMIZE, PROPOSAL, APPLY,
]
