# Routine inbox core contract

Routine manager clarifications go to an assistant that answers only what existing
approved evidence settles and escalates everything else through the manager
(owner decision 2026-10-02). Vision V-13 places this independently runnable shared
tool below the web application.

This delivery contains the transport-neutral core, protocol codec, adapter
contract and fake adapter. It contains no real chat adapter, chat-tool staging,
manager-liveness preflight, installation or scheduling. Those follow the owner's
separate source/update/install decision. The command interface refuses operation
without a supplied adapter. Approval of this core does not activate anything.

## Adapter boundary

A reviewed future host supplies an object implementing `adapter.Adapter` directly
to `Inbox(state_directory, configuration, adapter)` or `main(argv, adapter)`.
There is no dynamic module loader, executable transport or installation assumption.
The core writes only its own state directory; the fake opens no files or sockets.
Only the adapter can communicate with a transport.

All methods are synchronous. An adapter must bound its reads and send duration.
`read_messages(selector, cursor)` returns `ReadBatch(records, cursor)`. A selector
contains `channel`, `prefix`, or both from one project's configuration. It covers
all actual targets matching either value, including newly discovered targets.
Records are returned in server order, never skipped on replay, with an opaque
JSON-serializable cursor. Independent reads must be replayable from a previously
returned cursor. An unreadable, truncated, replaced or conflicting history raises
`AdapterError`; an empty batch is reserved for a successful empty read.

Each message record has:

| Field | Meaning |
| --- | --- |
| `msgid` | Nonempty server-assigned identity, stable across re-reads |
| `at` | ISO-8601 server time with a timezone |
| `target` | Actual target where the server recorded the message |
| `account` | Server-attested account, or null/absent; never derived from nick, text or client tags |
| `text` | One physical message line, including its protocol header |
| `tags` | Client-tag dictionary; empty when absent |
| `reply_to` | Server message ID from the reply tag, or null/absent |

The optional nick is informational. For a repeated `msgid`, normalized fields
must remain identical; adapters must not add changing observation metadata.
Client tags and text do not authenticate anyone. Only an attested configured
manager introduces questions. The assistant's own messages are delivery evidence,
never questions. Unknown accounts and absent attestation confer no authority.

`read_acknowledgements(cursor)` returns `ReadBatch(records, cursor)` independently
of message reads. Each record contains `msgid` (the message being acknowledged),
`at`, `target`, and the acknowledger's server-attested `account`. An absent account
is not acceptance evidence. An unavailable source raises `AdapterError`.

`send(target, text, tags)` accepts one logical reply. The target is exactly where
the question's first part was recorded. The adapter splits long text into ordered
physical messages, repeats the request header and reply correlation, and applies
part numbers with one logical action key. It returns `SendResult`:

- `sent`: submission only. It does not prove read-back or manager acceptance.
- `failed`: positive proof that nothing went out, supplied in nonempty `proof`.
- `uncertain`: every other outcome. Timeout, partial send, unknown result and a
  failure without proof must never become an automatic retry.

A transport with an existing durable outbox must report its queued result as
uncertain. It must retain the logical key and metadata on retry. Its own retries
must not be confused with authorization for a second send from the core.

All message/ack evidence and their independent cursors commit together. A failed
read commits no cursor or state based on that read. Evidence already collected,
including an acknowledgement arriving before reply read-back, survives restart.

## Protocol

The header is `[<type> <request-id>]`, where type is `request`, `question`,
`blocked`, `status`, `decision`, `done` or `answer`. IDs contain ASCII letters,
digits, underscore or hyphen. A missing ID is tolerated for legacy quarantine,
never for an automatic answer. Compatible repeated identical headers are stripped.

Legacy addressing is recognized only at the start of the header's body. An
optional `mgr:` or `<lane>/L<number>:` or `<lane>/R<number>:` nametag can precede it;
a lane begins with a letter and then uses letters, digits, underscore or hyphen.
Leading `@alias` tokens, or `alias:` / `alias,` followed by whitespace, address
that alias. Recipient comparison is case-insensitive. Mentions in prose, quotation,
backticks or transcripts do not route. A physical continuation begins `… ` before
its repeated header and never introduces a legacy recipient.

The structured envelope is all-or-none:

| Tag | Value |
| --- | --- |
| `+plateia/to` | Comma-separated aliases, each 1–64 ASCII letters/digits/underscore/hyphen |
| `+plateia/key` | Stable logical key, 1–80 characters from the same alphabet |
| `+plateia/part` | One-based integer part number |
| `+plateia/parts` | Total parts, 1–100 |
| `+plateia/auto` | Additional automatic-reply marker: `settled` or `escalate` |
| `+draft/reply` | Reply-to server msgid; normalized as `reply_to` on reads |

The automatic marker requires a complete envelope. Malformed or partial envelopes
are rejected and cannot fall back to legacy addressing or establish acceptance.
Part numbers satisfy `1 <= part <= parts`. The adapter repeats envelope metadata
on every physical part. A new question uses `[question <id>]` addressed to the
configured assistant `alias`. Its identity is part one's server `msgid`. All parts
must be consistent and present before admission; earlier fragments remain durable.
Legacy/unframed questions (including headerless leading addresses), missing-ID
questions and helper-addressed blocked reports
are quarantined and visible. Question kind, recipients, automatic marker, request
ID and total count must agree across every fragment. Inconsistent assembly remains
quarantined; a later fragment cannot remove the earlier quarantine classification. They require manager reconciliation or a structured
resend, which is a separate item. They are never claimed automatically.

A reply uses `[answer <id>]`, `+draft/reply=<question-part-one-msgid>`, the item's
stable action key, and `+plateia/to=<project.recipient>`. It carries the automatic
marker and this exact human marker: `Automatic routine clarification (settled):`
or `Automatic routine clarification (escalate):`. The text includes the answer or
unresolved choice, its source evidence, and the authority ceiling. Literal owner
mentions are refused, including quoted mentions.

The configured manager accepts **each physical reply part** by replying to that
part's msgid or by an attested acknowledgement record. Terminal display, submission,
delivery and acceptance are different facts. Unrelated messages never count.
A manager or configured owner resolves the question with a complete structured
`[answer <id>]` or `[decision <id>]` replying to the original question's part-one
msgid. It cannot be confused with acceptance, which references the reply's parts.
Partial or unframed resolution evidence is visible as a problem and suspends
claiming and sending until reconciled, so periodic runs do not repeatedly fail
at the same send.

For repeated sends with the same action key, read-back must contain an ordered
complete copy: parts `1..N`, with no second part one interrupting it. Acceptance
must cover every msgid of **one** complete copy; it cannot combine acknowledgements
from different copies. The full same-key sequence is validated before filtering
markers, headers, recipients or correlation; an invalid intervening part cannot
disappear and join different copies. All complete copies must agree on text and framing. An
interleaved or ambiguous sequence remains unknown because this envelope has no
per-attempt identity. A future adapter must preserve this ordering constraint;
the core does not infer which retry supplied a missing part. Missing fragments and
conflicting evidence are different: an incomplete trailing copy waits, while an
invalid, interleaved or contradictory sequence creates a durable `evidence_conflict`.
This stays visible even if a previous poll recorded delivery, acceptance or waiting
for the owner; the earlier state is retained in the audit record. No automatic
claim or resend is permitted. A complete correlated resolution can resolve the
question; the conflict history remains in evidence and audit records.

## Configuration and activation

`routing.json.example` defines every required field. Values are read at runtime
from a private configuration, never from credentials:

- `alias`: assistant destination; `assistant` and `owner`: authenticated accounts.
  The assistant account must differ from the owner and every configured manager.
- `claim_seconds`: integer from 1 through 86400; the example uses 600.
- `projects`: nonempty mapping. Each project has `channel`, `prefix`, or both;
  `manager` is its attested account and `recipient` is the alias placed in replies.
- `request_prefix`: the literal request-ID prefix belonging to that project,
  conventionally its project name followed by `-`. An ID starting another known
  project's prefix is rejected. An otherwise unfamiliar ID remains legitimate.

Channels/prefixes and request prefixes must be unambiguous across projects.
Configured account and alias tokens use the alphabet above. Other string-valued
server-attested accounts are unrecognized by this configuration and are ignored
for authority; they do not block unrelated questions. Non-string accounts are
malformed adapter records and block the read. Channels/prefixes begin `#` and
contain lowercase letters, digits, underscore or hyphen. Real routing identities,
messages, decisions and state stay outside this public repository.

The state path is an explicit argument. The host must supply the approved adapter;
its actual source/install route is deferred. A standalone invocation without an
adapter exits 75 without creating state. Never substitute live transport code to
bypass this gate.

Explicit `initialize` creates schema version 3 and a cutover time. It admits no
history. Subsequent reads consume history through adapter cursors but admit only
messages at or after cutover. Do not reset cutover to recover a problem. The
configuration digest is pinned; changes require explicit `repin-config` reconciliation.
This is an operator-only command, never part of polling. It takes an authorization
reason, first reads/reconciles the old configuration, then audits and pins the new
one. Every incomplete or non-terminal question must still map to the same project,
target and manager, with unchanged assistant/owner/alias/manager-recipient identities.
Conflicting changes are refused without discarding evidence or advancing cursors.
Existing claim expiry times and action keys are preserved; a lifetime change applies
to future claims. New projects start at the reconciliation time, without enrolling
old history. Terminal and quarantined records retain their original authority context.
A completed project's manager may rotate without rewriting that history. Changing
an existing target selector needs a separately designed history migration and is
refused by this command; adding a distinct project is supported.
Older experimental state is not automatically migrated or overwritten.

## State transitions and fences

`prepare` validates a claimed answer and persists its action key/content without
sending. It is a substep of `claimed`, not a delivery state. Each automatic action
has one key. After the first send attempt its content is immutable, including on
operator-authorized resend.

| From | To | Required evidence/action |
| --- | --- | --- |
| Unadmitted fragments | `new` | Complete, valid structured manager question |
| Unadmitted legacy message | `quarantined` | Addressed but unframed, missing-ID or blocked question |
| `new`, `send_failed` | `claimed` | Exclusive claim with a fresh token and expiry |
| `claimed` | `claimed` | Prior claim expired; a new token supersedes it |
| `claimed` | `sending` | Current token, prepared decision, fresh reconciliation; intent committed before Send |
| `sending` | `submitted` | Adapter returns `sent` while claim is valid |
| `sending` | `send_failed` | Adapter returns `failed` with positive no-send proof while claim is valid |
| `sending` | `uncertain` | Ambiguous result/exception while claim is valid, or expired sending lease observed after interruption |
| `submitted`, `uncertain`, `sending` | `delivered` | Complete consistent reply copy read from the assistant in the original target |
| `delivered` | `accepted` | Manager accepts one complete `settled` copy; no more automatic action, but later contradictory evidence stays visible |
| `delivered` | `waiting_owner` | Manager accepts one complete `escalate` copy |
| `new`, `claimed`, `sending`, `send_failed`, `uncertain`, `submitted`, `delivered`, `waiting_owner`, `evidence_conflict` | `resolved` | Complete correlated resolution; terminal |
| Any action state, including `accepted` | `evidence_conflict` | Conflicting or ambiguous read-back, recorded durably with the prior state in audit |
| `uncertain` | `new` | Explicit operator `authorize-resend` with recorded reason; original key/content retained |

One reconciliation can observe delivery and acceptance together. No later state
is inferred from silence. `quarantined` is terminal for automation but remains
visible. Incomplete multipart input is shown by the poll's `incomplete` count.
A partial resolution does not create a terminal state. A completed resolution
read before Send prevents Send entirely.

Expired or superseded claimants cannot send or record results. Expiry is rechecked
at the adapter boundary, after intent commit and immediately before dispatch; if
it expired, no Send occurs and the committed intent remains conservatively unknown. A live `sending`
lease is never moved to uncertain merely because another poll ran. A crash leaves
durable intent; after lease expiry it becomes uncertain, never automatically new.
Only proven `send_failed` permits an automatic new claim. An unsent expired claim
must be reconsidered and prepared again. A filesystem lock serializes operations,
including the bounded send, without waiting on another run.

`authorize-resend` is not available to the periodic workflow by policy. It requires
an explicit operator instruction, records the reason durably, and still needs a
new claim. An unchanged logical key does not itself guarantee transport-level
exactly-once delivery; the operator must consider possible duplicate effects.

## Invocation, outcomes and tests

A host can call `main(argv, adapter)` with the normal options:
`--config <private-json> --state <private-state> <command>`. Commands are
`initialize`, `poll --limit 5`, `claim <id>`,
`prepare <id> --claim <token> --decision <private-json>`,
`send <id> --claim <token>`, and the operator-only
`authorize-resend <id> --reason <explicit-authorization>`, plus operator-only
`repin-config --reason <explicit-authorization>`.

Exit 0 confirms only that command's result. Exit 75 means blocked: invalid or
incomplete configuration, overlap, adapter read error, stale claim, missing adapter
or unreconciled evidence. Invalid configuration creates no state and changes none;
overlap acquires no database connection and changes no state. An adapter read
error advances neither message nor ack cursor and causes no item transition based
on that failed read. A send timeout may already have submitted work, so preserve
its durable intent and reconcile. A blocked send's fresh read is rolled back;
a subsequent poll records any observed resolution without sending.

Polling/refresh never calls Send. Keep routine handling bounded to five items,
one run at a time. Poll output omits claim bearer tokens; only `claim` returns one. A suggested future cadence is ten minutes; no schedule is
created by this package. Follow SKILL.md before answering.

Python 3.10 or newer, standard library only. From the repository root:

```sh
PYTHONPATH=packages PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s packages/routine_inbox/tests
```

Tests use temporary state and `FakeAdapter`; real sockets and subprocesses fail.
They cover identities, checkpoint rollback/restart, multipart assembly, ack retention,
claims, every lifecycle path, uncertain/proven-failed sends, operator resend,
read-only polling and no-state-change exit-75 cases. No live chat access is needed.

## Authority ceiling for assistants and managers

An automatic reply clarifies the existing request only. It grants no new scope,
solve, merge, release, cleanup, permission, credential, trust or security authority.
It cannot widen endpoints or substitute for canonical review. Managers must enforce
this ceiling despite the assistant account's broader standing authority.
Workers continue through their manager. Serious failures and owner-only decisions
use the manager's intentional owner escalation route and do not wait for polling.
