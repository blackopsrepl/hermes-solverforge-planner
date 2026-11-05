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
        "busy-time sources, inbox size and the latest proposal. Call this first in a planning conversation."
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
    "description": "Delete a task from the planner (and its calendar block, if applied).",
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    },
}

TASK_UNSCHEDULE = {
    "name": "planner_task_unschedule",
    "description": (
        "Move an applied task back to the inbox, deleting its calendar block so the next "
        "planner_optimize can place it again. Requires the user's approval."
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
        },
    },
}

BUSY_SET = {
    "name": "planner_busy_set",
    "description": (
        "Tell the planner about time that is already taken (meetings and events from the user's "
        "own calendar, read with whatever calendar tool or skill is available). Replaces every "
        "busy event previously stored for this source, so always pass the full list for the "
        "planning window. Call before planner_optimize."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Label for where these came from, e.g. 'google' or 'work'."},
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start": {"type": "string", "description": _TS + " A bare date means all day."},
                        "end": {"type": "string", "description": _TS},
                    },
                    "required": ["start", "end"],
                },
            },
        },
        "required": ["source", "events"],
    },
}

BUSY_ICS = {
    "name": "planner_busy_from_ics",
    "description": (
        "Load busy time from an iCalendar feed (an https/webcal URL every calendar app can export, "
        "or a local .ics path). Replaces previously loaded busy events for this source. Recurring "
        "events are not expanded from ICS; they are counted and reported."
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
        "Solve the schedule: place every inbox task into free working time, respecting busy "
        "time, deadlines, ordering rules and focus windows. Creates a PROPOSAL only; nothing is "
        "written to the calendar until planner_apply. Returns the score (0hard = every hard rule "
        "kept), each placement, and why any task did not fit."
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
        "Put a proposal's blocks on the planner calendar. The user is asked to approve first. Refused "
        "when tasks, busy time or settings changed since the solve; re-run planner_optimize then. "
        "Returns the written blocks and an .ics file so they can go into the user's own calendar."
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
