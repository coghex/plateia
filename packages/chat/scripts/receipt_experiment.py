"""Opt-in verified reviewer-start routing; uncertain evidence fails open."""
import re

import chatlib

EXPERIMENT = "review-start-receipts-v1"
FLAG_PATH = chatlib.STATE_DIR / "experiments" / (EXPERIMENT + ".enabled")
TASK = re.compile(r"PR #[1-9][0-9]*")
REQUEST = re.compile(r"[a-z0-9-]+")


def enabled():
    """Missing, unreadable or malformed marker leaves existing routing intact."""
    try:
        with FLAG_PATH.open(encoding="utf-8") as stream:
            return stream.read(64) == EXPERIMENT + "\n"
    except (OSError, UnicodeError):
        return False


def start_kind(record):
    """Only the wrapper's narrowly specified startup gets an explicit type."""
    if (isinstance(record.get("project"), str) and record["project"]
            and record.get("role") == "reviewer"
            and isinstance(record.get("parent"), str) and record["parent"]
            and isinstance(record.get("request"), str)
            and REQUEST.fullmatch(record["request"])
            and isinstance(record.get("task"), str)
            and TASK.fullmatch(record["task"])
            and enabled()):
        return "receipt-start"
    return "status"


def routine_review_start(entry, project, agents):
    """Match an authenticated wrapper receipt to its existing request owner.

    Never infer routine status from arbitrary prose, status/CI updates or a
    completed process. A stale/missing parent, changed request, legacy post,
    reply, unauthenticated account or additional text retains normal delivery.
    """
    if (not isinstance(project, str) or not project or not entry.get("verified")
            or not entry.get("msgid") or entry.get("reply_to")
            or entry.get("project") != project or entry.get("role") != "reviewer"):
        return False
    actor = agents.get(entry.get("from"), {})
    if not isinstance(actor, dict):
        return False
    keys = actor.get("keys", [])
    if (actor.get("role") != "reviewer" or actor.get("project") != project
            or not isinstance(keys, list)
            or not any(isinstance(key, str) and key.startswith("background:") for key in keys)):
        return False
    parent_name, request, task = (actor.get(k) for k in ("parent", "request", "task"))
    if (not isinstance(parent_name, str) or not parent_name
            or not isinstance(request, str) or not REQUEST.fullmatch(request)
            or not isinstance(task, str) or not TASK.fullmatch(task)):
        return False
    parent = agents.get(parent_name, {})
    if (not isinstance(parent, dict) or parent.get("project") != project
            or parent.get("role") != "solver" or parent.get("status") != "active"
            or not parent.get("surface_id")
            or actor.get("channel") != entry.get("channel")
            or parent.get("channel") != entry.get("channel")
            or parent.get("request") != request):
        return False
    return entry.get("text") == f"[receipt-start {request}] start {task} — launched by {parent_name}"
