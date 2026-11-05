"""The /planner slash command and the `hermes solverforge-planner` CLI command."""

from __future__ import annotations

import json

from .engine_bin import ENGINE_VERSION, EngineError, target_triple

USAGE = (
    "/planner status        check the engine and settings\n"
    "/planner install       download the SolverForge engine (verified by checksum)\n"
    "/planner inbox         list unscheduled tasks\n"
    "/planner plan          solve now and show the proposal\n"
    "/planner show [id]     show a proposal (default: latest)\n"
    "/planner apply <id>    put that proposal on the calendar"
)


def render(summary: dict) -> str:
    lines = [f"Proposal {summary['proposal_id'][:8]}  score {summary['score']}"
             + ("" if summary["can_apply"] else f"  (cannot apply: {', '.join(summary['blocked_because'])})")]
    day = None
    for block in summary["scheduled"]:
        if block["day"] != day:
            day = block["day"]
            lines.append(f"  {day}")
        lines.append(f"    {block['start'][11:16]}-{block['end'][11:16]}  {block['title']}")
    for item in summary["unscheduled"]:
        lines.append(f"  not placed: {item['title']} — {item.get('reason') or 'no slot'}")
    if summary["can_apply"] and not summary.get("applied_at"):
        lines.append(f"Apply with: /planner apply {summary['proposal_id']}")
    return "\n".join(lines)


def slash_handler(planner, raw: str) -> str:
    words = (raw or "").split()
    verb, rest = (words[0].lower(), words[1:]) if words else ("status", [])
    try:
        if verb == "status":
            return json.dumps(json.loads(planner.status({})), indent=2)
        if verb == "install":
            # Typing the command is the user's own decision to download the engine.
            messages: list[str] = []
            planner.engine.install(log=messages.append)
            return "\n".join(messages)
        if verb == "inbox":
            tasks = json.loads(planner.inbox({}))["tasks"]
            if not tasks:
                return "Inbox is empty."
            return "\n".join(f"- {t['title']} ({t['duration_minutes']} min, {t['priority']})" for t in tasks)
        if verb == "plan":
            result = json.loads(planner.handler(planner.optimize)({}))
            return render(result) if result.get("ok") else result["error"]
        if verb == "show":
            result = json.loads(planner.handler(planner.proposal)({"id": rest[0]} if rest else {}))
            return render(result) if result.get("ok") else result["error"]
        if verb == "apply":
            if not rest:
                return "Usage: /planner apply <proposal-id>"
            # The user typed the command with the id: that is the approval.
            result = json.loads(planner.handler(planner.apply)({"id": rest[0]}))
            if not result.get("ok"):
                return result["error"]
            lines = [f"Applied {len(result['written'])} block(s):"]
            lines += [f"  {b['start'][5:16]}-{b['end'][11:16]}  {b['title']}" for b in result["written"]]
            if result.get("push_to"):
                lines.append(f"To put them on {result['push_to']}, ask Hermes to update that calendar.")
            lines.append(f"Calendar file: {result['ics']}")
            return "\n".join(lines)
    except EngineError as exc:
        return f"{exc.code}: {exc.message}"
    return USAGE


def cli_setup(parser) -> None:
    subs = parser.add_subparsers(dest="sfp_command")
    subs.add_parser("status", help="Show the engine, settings and inbox")
    subs.add_parser("install", help=f"Download engine {ENGINE_VERSION} for this platform and verify its checksum")


def cli_handler(planner, args) -> int:
    command = getattr(args, "sfp_command", None) or "status"
    if command == "install":
        try:
            planner.engine.install()
        except EngineError as exc:
            print(f"{exc.code}: {exc.message}")
            print(f"platform: {target_triple() or 'unsupported'}")
            return 1
        return 0
    print(json.dumps(json.loads(planner.status({})), indent=2))
    return 0
