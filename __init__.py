"""SolverForge Planner for Hermes: proven-feasible scheduling as agent tools.

A SolverForge engine (``engine/``, shipped as an attested release binary)
places tasks into free working time; this package keeps the planner's state,
exposes it to the agent, gates calendar writes behind Hermes's approval prompt
and ships the Desktop surfaces in ``desktop/``.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .commands import cli_handler, cli_setup, slash_handler
from .engine_bin import Engine
from .store import Store
from .tools import Planner, tool_table

NAME = "solverforge-planner"
TOOLSET = "solverforge-planner"
# Tools that write to the calendar. Each needs a human yes, every time.
GATED = {"planner_apply", "planner_task_unschedule"}

_lock = threading.Lock()
_planner: Planner | None = None


def data_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(NAME)


def broadcast(what: str, detail: dict) -> None:
    """Push a change to the Desktop half; a no-op where no Desktop is attached."""
    from hermes_cli.plugin_events import broadcast_plugin_event

    broadcast_plugin_event(NAME, "changed", {"what": what, **detail})


def planner(get_config=None) -> Planner:
    """The process-wide planner. The dashboard backend shares it with the tools."""
    global _planner
    with _lock:
        if _planner is None:
            root = data_dir()
            config = get_config or (lambda key, default=None: default)
            _planner = Planner(
                Store.at(root / "planner.db"),
                lambda: Engine(root, override=str(config("binary", "") or ""),
                               timeout_s=int(config("solve_timeout", 120) or 120)),
                root,
                on_change=broadcast,
            )
        return _planner


def register(ctx) -> None:
    p = planner(ctx.get_config)

    for schema, handler in tool_table(p):
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=handler, emoji="🗓")

    def gate(tool_name: str = "", args: dict | None = None, **_kwargs):
        if tool_name not in GATED:
            return None
        args = args or {}
        # Scope "always allow" to this exact proposal or task, so one approval can never
        # silently cover a later, different write.
        return {
            "action": "approve",
            "message": p.approval_text(tool_name, args),
            "rule_key": f"{tool_name}:{args.get('id', '')}",
        }

    ctx.register_hook("pre_tool_call", gate)

    ctx.register_command(
        "planner",
        lambda raw="", **kw: slash_handler(p, raw),
        description="SolverForge Planner: status, install, inbox, plan, show [id], apply <id>",
        args_hint="[status|install|inbox|plan|show|apply]",
    )
    ctx.register_cli_command(
        name=NAME,
        help="Install and check the SolverForge planning engine",
        setup_fn=cli_setup,
        handler_fn=lambda args: cli_handler(p, args),
    )

    ctx.register_skill("planner", Path(__file__).parent / "skills" / "planner" / "SKILL.md")

    ctx.register_automation_blueprint(
        "morning",
        title="Morning plan",
        description="Each workday, refresh busy time, solve the coming days and send the proposal for approval.",
        category="work",
        schedule_template="{minute} {hour} * * 1-5",
        prompt_template=(
            "Morning planning run. Load the solverforge-planner:planner skill. Call planner_status. "
            "If a calendar tool or skill is available to you, read today's and the next {days} days' events "
            "and pass them to planner_busy_set (source 'calendar'). Then call planner_optimize. Report the "
            "score, the scheduled blocks by day and every task that did not fit with its reason, and end with "
            "the proposal id so I can approve it. Do not call planner_apply in this run."
        ),
        slots=[
            {"name": "time", "type": "time", "label": "What time?", "default": "07:30"},
            {"name": "days", "type": "enum", "label": "Look-ahead days", "default": "2",
             "options": ["1", "2", "5", "7"]},
            {"name": "deliver", "type": "enum", "label": "Where to deliver?", "default": "origin",
             "options": ["origin", "local"], "strict": False},
        ],
        skills=["solverforge-planner:planner"],
        tags=["planning", "calendar", "daily"],
    )
