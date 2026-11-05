"""Backend for the Desktop half: /api/plugins/solverforge-planner/*.

The Desktop UI reads proposals and the week through these routes. Its two
writes, POST /apply and POST /install, are only reachable from buttons that
ask the user to confirm first; a person clicking them is the approval.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = "hermes_solverforge_planner_dashboard_pkg"
_LOCK = threading.Lock()
_LOADED: list = []


def _package():
    # The dashboard imports this file as a lone module; load the plugin package beside it once.
    # Requests run on a thread pool, so the first load is serialized: the package must sit in
    # sys.modules while it executes (its relative imports need that), and a second request must
    # not see it half-built.
    if _LOADED:
        return _LOADED[0]
    with _LOCK:
        if not _LOADED:
            spec = importlib.util.spec_from_file_location(
                _PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[_PACKAGE] = module
            spec.loader.exec_module(module)
            _LOADED.append(module)
    return _LOADED[0]


def _config(key: str, default=None):
    from hermes_cli.config import load_config_readonly

    entries = (load_config_readonly().get("plugins") or {}).get("entries") or {}
    settings = ((entries.get("solverforge-planner") or {}).get("settings")) or {}
    return settings.get(key, default)


def _planner():
    return _package().planner(_config)


def _call(method: str, args: dict | None = None) -> dict:
    planner = _planner()
    return json.loads(planner.handler(getattr(planner, method))(args or {}))


router = APIRouter()


@router.get("/status")
def status() -> dict:
    return _call("status")


@router.get("/proposal")
def proposal(id: str = "") -> dict:
    return _call("proposal", {"id": id} if id else {})


@router.get("/proposals")
def proposals() -> dict:
    return {"ok": True, "proposals": _planner().store.proposals()}


@router.get("/inbox")
def inbox(state: str = "inbox") -> dict:
    return _call("inbox", {"state": state})


@router.get("/week")
def week(start: str = "", days: int = 7) -> dict:
    return _call("week", {"start": start, "days": days} if start else {"days": days})


@router.post("/optimize")
def optimize(body: dict | None = None) -> dict:
    return _call("optimize", body or {})


@router.post("/apply")
def apply(body: dict) -> dict:
    proposal_id = str((body or {}).get("id") or "").strip()
    if not proposal_id:
        raise HTTPException(status_code=400, detail="id is required")
    return _call("apply", {"id": proposal_id})


@router.post("/install")
def install() -> dict:
    planner = _planner()
    log: list[str] = []
    try:
        planner.engine.install(log=log.append)
        _package().refresh_hermes_toolset_cache()
    except Exception as exc:  # EngineError, network errors: report, never 500 the pane
        return {"ok": False, "error": str(exc), "log": log}
    planner.changed("engine")
    return {"ok": True, "log": log}
