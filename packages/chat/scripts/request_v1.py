"""request/v1: a generic request as one readable chat line, its canonical
digest, and request-scoped authority validation (COLLAB-1, #27).

A pure library. It does no I/O and reads no configuration, chat record or
clock: every fact about the outside world (the current post's transport
evidence, the assistant list, a cited post, the policy grant, the time, the
expiry and revocation status) is passed in. Nothing stores anything, and
nothing imports another plateia module. Nothing calls it yet (COLLAB-2 on).

The line (the `[request <request-id>]` convention of `card-move/v1`, D-8)::

    [request <request-id>] request/v1 op=<op> rev=<n> act=<action> tgt=<target>
        scope=<tokens> [gates=<tokens>] policy=<name>:<sha256> [exp=<time>]
        [relay=<account>] [cite=<msgid>] [trace=<token>]

(one physical line; the layout above is wrapped only for reading). The grammar
is closed, and there is exactly one spelling of any request:

- The whole line is ASCII, at most MAX_LINE_BYTES bytes counting the
  `[request ...]` prefix, with no control character (a newline included), and
  exactly one space between pieces: no leading, trailing or doubled space, no
  tab. It is one line; a second line is refused, not ignored.
- `[request <request-id>]`: the request ID is `<project>-<yyyymmdd>-<n>` (D-37):
  a project TOKEN starting with a letter, a real calendar date, and a positive
  integer with no leading zero. Its character set is [a-z0-9-].
- `request/v1` is the format declaration. Any other value is refused.
- Fields follow in this fixed order, each at most once, as `name=value` with
  no space around `=`. The six required fields are always present:

      op      TOKEN. The client's idempotency key (D-33, D-37).
      rev     INTEGER. The revision: positive, so 1 or more.
      act     TOKEN. The action.
      tgt     TOKEN. The target.
      scope   COLLECTION, required, so at least one token. The scope.
      gates   COLLECTION, optional (absent means no gates). The gates that
              travel with the request.
      policy  `<name>:<digest>`: a policy reference, a TOKEN name and the
              policy's content digest as 64 lowercase hex digits. Nothing else.
      exp     EXPIRY, optional. The request's expiry.
      relay   TOKEN, optional. A CLAIM of the relaying assistant's account.
      cite    MSGID, optional. The message ID of a previously posted authority
              record; its presence makes this a later hop.
      trace   TOKEN, optional. A trace reference to the owner's conversation,
              kept for audit. It is never proof.

- TOKEN is lowercase letters and digits in groups joined by single hyphens
  ([a-z0-9]+(-[a-z0-9]+)*), at most MAX_TOKEN characters. No free text.
- MSGID is an opaque server message ID: [A-Za-z0-9][A-Za-z0-9._-]*, at most
  MAX_MSGID characters.
- INTEGER is canonical decimal: [1-9] then digits, at most MAX_INT_DIGITS
  digits. No sign, no leading zero, no zero, no fraction, no exponent.
- COLLECTION is TOKENs joined by commas, with no space and no empty item,
  strictly ascending in byte order (so no duplicate), at most MAX_COLLECTION
  items. An unsorted or repeated list is refused, never normalized.
- EXPIRY is `YYYYMMDDTHHMMSSZ`: a real UTC date and time, digits only.

parse() refuses anything else with a RequestError whose `code` names the
reason (CODES). For every accepted line, encode(parse(line)) == line.

What is hashed. The digest is the sha256, in lowercase hex, of the ASCII bytes
returned by logical_bytes(): a fixed header line, then one `name=value` line,
each ending in "\\n", for id, rev, op, act, tgt, scope, gates, policy and exp,
in that order. An absent gates or exp is an empty value, and the collections
are in their canonical spelling. Changing the request ID, revision, operation,
action, target, scope, gates, policy reference or expiry therefore changes the
digest. Delivery and hop provenance is not hashed: the relay and cite and trace
fields, and the transport facts a validator is given (the post's account and
message ID), are not part of the logical content. A repost or a relay of the
same content under a new message ID, or a later hop that cites it, keeps the
digest. The execution key is (request ID, revision).

Transport evidence is injected, never encoded. A request cannot contain its
own not-yet-assigned message ID, so the current post's account, verified flag
and message ID reach validate() as a Post. Whatever the line says about an
account (`relay`) or a post (`cite`) is a claim, correlated with evidence.

Validation (validate) returns one of three results:

- REFUSED: some check definitely fails (an unverified or non-assistant author,
  a scope wider than the grant, a sender label or a trace reference offered as
  proof, a mismatched correlation, a known-expired or known-revoked grant, the
  expiry reached). Every refusal found is listed; a refusal stays a refusal
  even when another check is unresolved.
- UNRESOLVED: nothing is refused, but some evidence is missing or unknown (the
  cited post, the assistant list, the grant, the time, the expiry or revocation
  status), so authority is not established.
- VERIFIED: every check passed on supplied evidence. Missing evidence never
  gets here.

A first hop (no `cite`) is authorized by the current post itself: it must be
from a verified account on the assistant list. A later hop (`cite`) is
authorized by the cited post alone, whoever posts it: the cited post must be
from a verified assistant, and its content must be a first-hop request that
matches this one in request ID, revision, digest, scope, gates, expiry and
policy reference, so a later hop keeps exactly the cited restrictions. In
both, the policy name and digest must equal the independently supplied grant's,
the scope must be a subset of the grant's, the action and target must be ones
the grant lists, and the request's gates must include the grant's. The
assistant list only says who may relay; it never widens a scope.
"""
import calendar
import datetime
import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Optional

FORMAT = "request/v1"
MAX_LINE_BYTES = 512  # the whole line, `[request ...]` prefix included
MAX_TOKEN = 48
MAX_MSGID = 64
MAX_COLLECTION = 8
MAX_INT_DIGITS = 9

CODES = ("not-text", "over-length", "multiple-lines", "control-character", "non-ascii", "bad-spacing",
         "bad-prefix", "bad-request-id", "unsupported-format", "extra-text", "unknown-field",
         "repeated-field", "field-order", "missing-field", "bad-token", "bad-integer", "bad-collection",
         "bad-policy", "bad-expiry", "non-canonical-request")

VERIFIED, UNRESOLVED, REFUSED = "verified", "unresolved", "refused"
VALID, INVALID, UNKNOWN = "valid", "invalid", "unknown"  # a lifecycle fact: known good, known bad, not known
REPEAT, CONFLICT, DISTINCT = "repeat", "conflict", "distinct"

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MSGID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_REQUEST_ID = re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-([0-9]{8})-([1-9][0-9]*)")
_INTEGER = re.compile(r"[1-9][0-9]*")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_EXPIRY = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z")
_HEADER = b"plateia request/v1 logical content\n"

# (name, required) in the one canonical order
_ORDER = (("op", True), ("rev", True), ("act", True), ("tgt", True), ("scope", True), ("gates", False),
          ("policy", True), ("exp", False), ("relay", False), ("cite", False), ("trace", False))
_POSITION = {name: i for i, (name, _) in enumerate(_ORDER)}


class RequestError(ValueError):
    """A line or request that is not canonical; `code` is one of CODES."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Request:
    request_id: str
    op: str
    rev: int
    action: str
    target: str
    scope: tuple
    policy_name: str
    policy_digest: str
    gates: tuple = ()
    expiry: Optional[str] = None
    relay: Optional[str] = None
    cite: Optional[str] = None
    trace: Optional[str] = None

    @property
    def execution_key(self):
        return (self.request_id, self.rev)


def _token(raw, what):
    if len(raw) > MAX_TOKEN or not _TOKEN.fullmatch(raw):
        raise RequestError("bad-token", f"{what}: {raw[:60]!r} is not a token")
    return raw


def _msgid(raw, what):
    if len(raw) > MAX_MSGID or not _MSGID.fullmatch(raw):
        raise RequestError("bad-token", f"{what}: {raw[:60]!r} is not a message ID")
    return raw


def _integer(raw, what):
    if len(raw) > MAX_INT_DIGITS or not _INTEGER.fullmatch(raw):
        raise RequestError("bad-integer", f"{what}: {raw[:60]!r} is not a canonical positive integer")
    return int(raw)


def _collection(raw, what):
    items = raw.split(",")
    for item in items:
        if item == "":
            raise RequestError("bad-collection", f"{what}: an empty item")
        _token(item, what)
    if len(items) > MAX_COLLECTION:
        raise RequestError("bad-collection", f"{what}: more than {MAX_COLLECTION} items")
    if len(set(items)) != len(items):
        raise RequestError("bad-collection", f"{what}: a repeated item")
    if items != sorted(items):
        raise RequestError("bad-collection", f"{what}: not in ascending order")
    return tuple(items)


def _policy(raw):
    name, colon, digest = raw.partition(":")
    if not colon or not _DIGEST.fullmatch(digest):
        raise RequestError("bad-policy", "policy: expected <name>:<64 lowercase hex digits>")
    return _token(name, "policy name"), digest


def expiry_epoch(expiry):
    """Seconds since the epoch of an EXPIRY string; RequestError if it is not a real UTC time."""
    match = _EXPIRY.fullmatch(expiry) if isinstance(expiry, str) else None
    if not match:
        raise RequestError("bad-expiry", "exp: expected YYYYMMDDTHHMMSSZ")
    try:
        moment = datetime.datetime(*map(int, match.groups()))
    except ValueError as error:
        raise RequestError("bad-expiry", f"exp: {error}") from None
    return calendar.timegm(moment.timetuple())


def _request_id(raw):
    match = _REQUEST_ID.fullmatch(raw) if isinstance(raw, str) else None
    if match and len(match.group(1)) <= MAX_TOKEN and len(match.group(3)) <= MAX_INT_DIGITS:
        stamp = match.group(2)
        try:
            datetime.date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
            return raw
        except ValueError:
            pass
    raise RequestError("bad-request-id", "expected <project>-<yyyymmdd>-<n> with a real date")


def parse(line):
    """The Request a canonical line spells; RequestError (a named code) for anything else."""
    if not isinstance(line, str):
        raise RequestError("not-text", type(line).__name__)
    if len(line) > MAX_LINE_BYTES:  # also bounds the bytes: a non-ASCII line is refused below
        raise RequestError("over-length", f"more than {MAX_LINE_BYTES} bytes")
    if "\n" in line:
        raise RequestError("multiple-lines")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in line):
        raise RequestError("control-character")
    if any(ord(c) > 0x7E for c in line):
        raise RequestError("non-ascii")
    pieces = line.split(" ")
    if "" in pieces:
        raise RequestError("bad-spacing", "leading, trailing or doubled space")
    if pieces[0] != "[request" or len(pieces) < 2 or not pieces[1].endswith("]"):
        raise RequestError("bad-prefix", "expected [request <request-id>]")
    request_id = _request_id(pieces[1][:-1])
    if len(pieces) < 3 or pieces[2] != FORMAT:
        raise RequestError("unsupported-format", f"expected {FORMAT}")
    seen, last = {}, -1
    for piece in pieces[3:]:
        name, equals, raw = piece.partition("=")
        if not equals:
            raise RequestError("extra-text", f"{piece[:60]!r} is not a name=value field")
        if name not in _POSITION:
            raise RequestError("unknown-field", name[:60])
        if name in seen:
            raise RequestError("repeated-field", name)
        if _POSITION[name] < last:
            raise RequestError("field-order", name)
        last = _POSITION[name]
        seen[name] = raw
    for name, required in _ORDER:
        if required and name not in seen:
            raise RequestError("missing-field", name)
    policy_name, policy_digest = _policy(seen["policy"])
    expiry = seen.get("exp")
    if expiry is not None:
        expiry_epoch(expiry)
    return Request(
        request_id=request_id,
        op=_token(seen["op"], "op"),
        rev=_integer(seen["rev"], "rev"),
        action=_token(seen["act"], "act"),
        target=_token(seen["tgt"], "tgt"),
        scope=_collection(seen["scope"], "scope"),
        policy_name=policy_name,
        policy_digest=policy_digest,
        gates=_collection(seen["gates"], "gates") if "gates" in seen else (),
        expiry=expiry,
        relay=_token(seen["relay"], "relay") if "relay" in seen else None,
        cite=_msgid(seen["cite"], "cite") if "cite" in seen else None,
        trace=_token(seen["trace"], "trace") if "trace" in seen else None)


def encode(request):
    """The canonical line for a Request. RequestError if it has no canonical
    spelling (a non-canonical field, or a line over MAX_LINE_BYTES)."""
    try:
        pieces = [f"[request {request.request_id}]", FORMAT, f"op={request.op}", f"rev={request.rev}",
                  f"act={request.action}", f"tgt={request.target}", f"scope={','.join(request.scope)}"]
        if request.gates:
            pieces.append(f"gates={','.join(request.gates)}")
        pieces.append(f"policy={request.policy_name}:{request.policy_digest}")
        for name, value in (("exp", request.expiry), ("relay", request.relay), ("cite", request.cite),
                            ("trace", request.trace)):
            if value is not None:
                pieces.append(f"{name}={value}")
    except (AttributeError, TypeError) as error:
        raise RequestError("non-canonical-request", str(error)) from None
    line = " ".join(pieces)
    if parse(line) != request:  # the spelling must read back as exactly this request
        raise RequestError("non-canonical-request", "the request does not equal its own canonical reading")
    return line


def logical_bytes(request):
    """The exact bytes digest() hashes (see the module docstring)."""
    encode(request)  # refuses a request with no canonical spelling
    fields = (("id", request.request_id), ("rev", str(request.rev)), ("op", request.op),
              ("act", request.action), ("tgt", request.target), ("scope", ",".join(request.scope)),
              ("gates", ",".join(request.gates)), ("policy", f"{request.policy_name}:{request.policy_digest}"),
              ("exp", request.expiry or ""))
    return _HEADER + "".join(f"{name}={value}\n" for name, value in fields).encode("ascii")


def digest(request):
    """The sha256 (lowercase hex) of the request's logical content."""
    return hashlib.sha256(logical_bytes(request)).hexdigest()


def compare_reuse(prior, incoming):
    """How `incoming` relates to supplied `prior` content (Requests or lines);
    nothing is stored. REPEAT: the same execution key and digest, so a repost,
    relay or replay. CONFLICT: the same execution key with a different digest,
    a conflicting reuse. DISTINCT: another key, so another revision or request
    is never a conflict."""
    prior = parse(prior) if isinstance(prior, str) else prior
    incoming = parse(incoming) if isinstance(incoming, str) else incoming
    if prior.execution_key != incoming.execution_key:
        return DISTINCT
    return REPEAT if digest(prior) == digest(incoming) else CONFLICT


@dataclass(frozen=True)
class Post:
    """Transport evidence for the current post, injected by whatever read it
    from the server's tags: its verified account and its message ID."""
    account: str
    msgid: str
    verified: bool


@dataclass(frozen=True)
class Record:
    """A previously posted authority record, as looked up: its author, whether
    that author was verified, and the text it posted."""
    author: str
    verified: bool
    content: str


@dataclass(frozen=True)
class Grant:
    """The independently supplied policy grant: the policy's name and content
    digest, and what it covers. Nothing here is a wildcard: a token is covered
    only if it is listed."""
    name: str
    digest: str
    scope: frozenset
    actions: frozenset
    targets: frozenset
    gates: frozenset = frozenset()


@dataclass(frozen=True)
class Evidence:
    """Everything validate() knows about the world. Absent means unknown, and
    unknown never verifies.

    post       the current post's transport evidence (a Post)
    assistants the verified assistant accounts (any collection)
    lookup     message ID -> Record, or None when it cannot be established
    grant      the policy grant (a Grant)
    now        the time, as integer seconds since the epoch
    expiry     VALID, INVALID or UNKNOWN: has the grant or record lapsed
    revocation VALID, INVALID or UNKNOWN: has it been revoked
    """
    post: Optional[Post] = None
    assistants: Optional[object] = None
    lookup: Optional[Callable] = None
    grant: Optional[Grant] = None
    now: Optional[int] = None
    expiry: str = UNKNOWN
    revocation: str = UNKNOWN


@dataclass(frozen=True)
class Verdict:
    """status is VERIFIED, UNRESOLVED or REFUSED. reasons name the refusals
    (REFUSED) or the missing evidence (UNRESOLVED); a VERIFIED verdict has
    none and names, in `authority`, the message ID of the post that carries
    the authority."""
    status: str
    reasons: tuple = ()
    authority: Optional[str] = None


def _listed(assistants, account):
    """True or False if the assistant list says, None if there is no list."""
    if assistants is None:
        return None
    try:
        return account in assistants
    except TypeError:
        return None


def _first_hop(request, evidence, refused, unresolved):
    post = evidence.post
    if (not isinstance(post, Post) or not isinstance(post.account, str)
            or not isinstance(post.msgid, str) or not _MSGID.fullmatch(post.msgid)):
        unresolved.append("transport-evidence-unavailable")
        return None
    if post.verified is not True:
        refused.append("relay-unverified")
    listed = _listed(evidence.assistants, post.account)
    if listed is None:
        unresolved.append("assistants-unavailable")
    elif not listed:
        # the post cites nothing, so all it offers is who sent it, or a pointer into a conversation
        refused.append("trace-reference-only" if request.trace is not None else "sender-label-only")
    if request.relay is not None and request.relay != post.account:
        refused.append("relay-claim-mismatch")
    return post.msgid


def _later_hop(request, evidence, refused, unresolved):
    try:
        record = evidence.lookup(request.cite) if callable(evidence.lookup) else None
    except Exception:  # an injected lookup that fails has established nothing
        record = None
    if not isinstance(record, Record):
        unresolved.append("cited-record-unavailable")
        return request.cite
    if record.verified is not True:
        refused.append("cited-author-unverified")
    listed = _listed(evidence.assistants, record.author)
    if listed is None:
        unresolved.append("assistants-unavailable")
    elif not listed:
        refused.append("cited-author-not-assistant")
    try:
        cited = parse(record.content)
    except RequestError as error:
        refused.append(f"cited-content-malformed:{error.code}")
        return request.cite
    if cited.cite is not None:
        refused.append("cited-record-not-relay")
    for label, mine, theirs in (
            ("request-id", request.request_id, cited.request_id), ("revision", request.rev, cited.rev),
            ("scope", request.scope, cited.scope), ("gates", request.gates, cited.gates),
            ("expiry", request.expiry, cited.expiry),
            ("policy", (request.policy_name, request.policy_digest), (cited.policy_name, cited.policy_digest))):
        if mine != theirs:
            refused.append(f"cited-{label}-mismatch")
    if digest(request) != digest(cited):
        refused.append("cited-digest-mismatch")
    if request.relay is not None and request.relay != record.author:
        refused.append("relay-claim-mismatch")
    return request.cite


def _check_grant(request, grant, refused, unresolved):
    if not isinstance(grant, Grant):
        unresolved.append("grant-evidence-unavailable")
        return
    if (request.policy_name, request.policy_digest) != (grant.name, grant.digest):
        refused.append("policy-mismatch")
    if not set(request.scope) <= set(grant.scope):
        refused.append("scope-wider-than-grant")
    if request.action not in grant.actions:
        refused.append("action-not-granted")
    if request.target not in grant.targets:
        refused.append("target-not-granted")
    if not set(grant.gates) <= set(request.gates):
        refused.append("gate-dropped")


def _check_lifecycle(request, evidence, refused, unresolved):
    if request.expiry is not None:
        now = evidence.now
        if not isinstance(now, int) or isinstance(now, bool):
            unresolved.append("time-unavailable")
        elif now >= expiry_epoch(request.expiry):  # the boundary itself is expired
            refused.append("expired")
    for label, state, lapsed in (("expiry", evidence.expiry, "grant-expired"),
                                 ("revocation", evidence.revocation, "revoked")):
        if state == INVALID:
            refused.append(lapsed)
        elif state != VALID:  # UNKNOWN, and any value that is not one of the three
            unresolved.append(f"{label}-unknown")


def validate(request, evidence):
    """Validate a request (a line or a Request) against injected Evidence."""
    if isinstance(request, str):
        try:
            request = parse(request)
        except RequestError as error:
            return Verdict(REFUSED, (f"malformed:{error.code}",))
    try:
        encode(request)
    except (RequestError, AttributeError):
        return Verdict(REFUSED, ("malformed:non-canonical-request",))
    if not isinstance(evidence, Evidence):
        evidence = Evidence()  # no evidence at all is every fact unknown
    refused, unresolved = [], []
    hop = _first_hop if request.cite is None else _later_hop
    authority = hop(request, evidence, refused, unresolved)
    _check_grant(request, evidence.grant, refused, unresolved)
    _check_lifecycle(request, evidence, refused, unresolved)
    if refused:  # a definite refusal is not softened by unresolved evidence
        return Verdict(REFUSED, tuple(refused))
    if unresolved:
        return Verdict(UNRESOLVED, tuple(unresolved))
    return Verdict(VERIFIED, (), authority)
