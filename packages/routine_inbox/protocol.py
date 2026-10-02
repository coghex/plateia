"""Explicit recipients and optional multipart metadata for the chat adapter.

No account lookup or authentication happens here. Recipients are destinations,
not a claim about who sent a message. The IRC account tag remains authoritative.
"""
import re

TYPES = "request|question|blocked|status|decision|done|answer"
HEADER = re.compile(rf"^\[({TYPES})(?: ([A-Za-z0-9_-]+))?\]\s*")
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
KEY = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
FIELDS = ("+plateia/to", "+plateia/key", "+plateia/part", "+plateia/parts")


def header(text):
    """Return type, request, body and continuation status; tolerate old double tags."""
    body = text.lstrip()
    continuation = body.startswith("… ")
    if continuation:
        body = body[2:]
    kind = request = None
    while match := HEADER.match(body):
        next_kind, next_request = match.groups()
        if kind and (next_kind != kind or (next_request and request and next_request != request)):
            break
        kind, request = next_kind, next_request or request
        body = body[match.end():]
    return kind, request, body, continuation


def recipients(text, known):
    """Legacy addressing is recognized only in the leading recipient slot.

    Tags and a standard manager/worker nametag may precede it. Inline, quoted,
    backtick and transcript mentions are never scanned. Continuations do not
    introduce new recipients. IRC account comparison is case-insensitive here.
    """
    _, _, body, continuation = header(text)
    if continuation:
        return set()
    body = re.sub(r"^(?:mgr|[A-Za-z][A-Za-z0-9_-]*/[LR][0-9]+):\s*", "", body)
    known = {name.lower(): name for name in known}
    found = set()
    direct = re.match(r"^([A-Za-z0-9_-]+)[:,]\s+", body)
    if direct and direct[1].lower() in known:
        return {known[direct[1].lower()]}
    while match := re.match(r"^@([A-Za-z0-9_-]+)(?=$|[\s,:])[, :]?\s*", body):
        name = match[1].lower()
        if name not in known:
            break
        found.add(known[name])
        body = body[match.end():]
    return found


def envelope(tags):
    """Return normalized explicit metadata, None for legacy, or raise on corruption."""
    if not any(key in tags for key in FIELDS) and "+plateia/auto" not in tags:
        return None
    if not all(key in tags for key in FIELDS):
        raise ValueError("incomplete routing envelope")
    if any(not isinstance(tags[key], str) for key in FIELDS):
        raise ValueError("routing values must be strings")
    targets = tags[FIELDS[0]].split(",")
    if not targets or any(not TOKEN.fullmatch(x) for x in targets):
        raise ValueError("invalid recipients")
    key = tags[FIELDS[1]]
    if not KEY.fullmatch(key):
        raise ValueError("invalid message key")
    try:
        part, parts = int(tags[FIELDS[2]]), int(tags[FIELDS[3]])
    except (ValueError, TypeError):
        raise ValueError("invalid multipart counts") from None
    if not 1 <= part <= parts <= 100:
        raise ValueError("invalid multipart range")
    result = {"to": sorted(set(x.lower() for x in targets)), "key": key,
              "part": part, "parts": parts}
    if "+plateia/auto" in tags:
        if tags["+plateia/auto"] not in ("settled", "escalate"):
            raise ValueError("invalid automatic reply marker")
        result["auto"] = tags["+plateia/auto"]
    return result


def tags(targets, key, part, parts, automatic=None):
    raw = dict(zip(FIELDS, (",".join(targets), key, str(part), str(parts))))
    if automatic is not None:
        raw["+plateia/auto"] = automatic
    envelope(raw)
    return raw
