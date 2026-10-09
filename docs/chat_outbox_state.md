# Chat outbox state: one durable authority for partial-post delivery

This is the design note for #19 and pull request #20. It is design only:
nothing here is implemented, and implementing it needs a separate owner
decision.

PR #20 (head `e658bb8`) repairs a long post that was reposted on every outbox
flush. Its fifth review found four remaining defects. All four come from one
cause: outbox state is split across several files and processes.

- Part progress lives in rewritten claimed outbox files.
- Confirmed parts whose msgid is unknown live in `outbox-reserved.jsonl`.
- Msgids already counted live in `outbox-attributed.jsonl`.
- Record completeness lives in `record-coverage.json`.

`pchat`, `agentcli.notify`, the bridge's `announce` and the bridge's flush
each write part of that state, under different locks. The flush reads it as
snapshots taken when it starts, and prunes it by wall-clock age.

This note replaces the split state with one SQLite database. Every transport
caller records its intent there before it sends anything. Reconciliation
decides delivery inside a single transaction, from the current state. Evidence
is kept for as long as anything still depends on it.

The delivery contract is #19's and does not change: confirmed parts are never
resent, unsent parts stay durable, and an uncertain part is checked against
the record and resent only when its absence is proved. Delivery is
at-least-once with that check. Nothing here claims exactly-once delivery, and
nothing can: no transaction spans both the database and the chat server.

Every example uses invented data: projects `alpha` and `beta`, the owner
`pat`, the assistant `sam`, and the agent `alp-solver-2`.

## Contents

1. [Terms and constants](#1-terms-and-constants)
2. [The authority](#2-the-authority)
3. [State and transition tables](#3-state-and-transition-tables)
4. [Invariants and proofs](#4-invariants-and-proofs)
5. [Crashes and interleavings](#5-crashes-and-interleavings)
6. [Importing the old outbox](#6-importing-the-old-outbox)
7. [Reconciliation rules](#7-reconciliation-rules)
8. [Mapping to #19](#8-mapping-to-19)
9. [Future tests](#9-future-tests)
10. [Feasibility and risks](#10-feasibility-and-risks)

## 1. Terms and constants

These constants keep the values PR #20 already uses.

| Name | Value | Meaning |
|---|---|---|
| `A` (`CLOCK_ALLOWANCE`) | 5 s | The allowed difference between the writer's wall clock and the server's message time. |
| `SETTLE` | 600 s | How long after an attempt ends the server may still publish bytes that attempt sent (section 5.3). |
| `UNDECIDED_MAX` | 24 h | How long an uncertain part may stay undecided, counted from its attempt's write. After that it is dead-lettered. |
| `PART_WAIT` | 30 s | The absolute deadline for the server to confirm one part. |
| `GC_MARGIN` | 1 h | Extra age before evidence may be collected (invariant I-7). |

- **Entry.** One logical post, or an acknowledgement, that the transport owes.
  It is created by `pchat post`, `pchat ack`, `agentcli.notify` or
  `announce`, or imported from the old outbox (section 6).
- **Part.** A fixed piece of an entry's text that the server publishes as one
  message with one msgid: one `draft/multiline` batch, or one line. Each part
  has an index `n` and its exact *server-visible text*: the text the bridge
  records for that message.
- **Attempt.** One try at sending one part over one connection. A part may
  have several attempts, numbered by a per-part *generation*. Only an attempt
  can produce a message.
- **Text key.** The triple (account, channel case-folded, exact text). Only
  attempts and messages with the same text key can be confused with each
  other.
- **Writer.** The process and flow that holds an attempt's connection. It is
  identified by (boot id, pid, process start time) plus a per-connection id.
- **Window.** The interval of server times in which an attempt's message, if
  it has one, must carry its time (section 7.1).
- **Message.** A verified message from the bridge's record: msgid, channel,
  account, exact text and server time.

## 2. The authority

### 2.1 One database

All durable outbox state lives in one SQLite database, `outbox.db`, in the
chat state directory. It replaces:

- the progress inside claimed outbox files;
- `outbox-reserved.jsonl`;
- `outbox-attributed.jsonl`;
- `record-coverage.json`.

**Settings:** WAL journal mode, `synchronous=FULL` so that a committed
transaction survives a power loss, and a bounded busy timeout. Every write is a
`BEGIN IMMEDIATE` transaction, which takes the database's single write lock
when it begins. Writes are therefore serialized, and every decision is read and
written inside one transaction.

**No network I/O inside a transaction.** Every transaction reads and writes
local state only. Logging in, sending, waiting for confirmation and closing
all happen between transactions.

**Not a second authority.** These files remain, but none of them is a source of
truth:

- `outbox.jsonl` and `outbox.claimed-*.jsonl` are an **import** boundary only
  (section 6).
- `dead-letters.jsonl` and the owner alert, a `push` delivery in
  `deliveries.jsonl`, are **exports**. Each is written after the terminal
  state commits, and is idempotent by entry id.
- The channel logs remain the chat record, which other readers use. For the
  outbox, the bridge indexes verified messages into the database as it records
  them (the `messages` table below). Evidence and coverage are therefore
  ordered with the outbox state.

### 2.2 Tables

The column lists are the minimum the proofs rely on. The exact schema is the
implementation's choice.

| Table | Key | Holds |
|---|---|---|
| `meta` | name | The schema version and the cutover marker (section 6.1). |
| `entries` | `id` | The origin (`pchat`, `notify`, `announce`, `ack` or `import`); account, channel, original text, `cont`, `reply_to`, written-at; `owner_kind` (`direct` or `outbox`) and, for `direct`, the owner writer's identity; `state` (`open`, `done`, `terminal`, `abandoned`); the terminal reason and alert; export marks (`alert_exported`, `dead_letter_exported`); and import identity (claim file, line ordinal, row sha256). |
| `parts` | (`entry_id`, `n`) | The kind, the lines, the exact server-visible text, `state` (`unsent`, `inflight`, `uncertain`, `confirmed`, `refused`), the current `generation`, and the msgid once attributed. |
| `attempts` | `id` | `entry_id`, `n`, `generation`, the text key, the writer identity, `conn_id`, `state` (`writing`, `confirmed`, `refused`, `rejected`, `ended`, `delivered`, `absent`, `dead`), `written_at`, `confirmed_at`, `ended_at`, `end_kind` (`closed` or `writer_gone`), and a detail. UNIQUE (`entry_id`, `n`, `generation`). |
| `attributions` | `msgid` | `attempt_id` (UNIQUE), and when it was attributed. |
| `messages` | `msgid` | Channel, account, exact text and server time, for verified messages only. |
| `coverage` | channel | `since`, `through` and `mark`, as PR #20's `Coverage` keeps them. |
| `imports` | claim file name | The line count, the content sha256, and when it was imported. |
| `held` | (file name, ordinal) | The raw row and why it was held: unreadable, an id collision, or a changed file hash. |

### 2.3 Actors and guards

| Actor | Who | May do |
|---|---|---|
| **P** (direct writer) | a `pchat post` or `agentcli.notify` process, or the bridge running `announce` | Create its own entry. Write its own entry's parts. Hand its entry to the outbox. |
| **B** (bridge) | the single `chatbridge` service process | Index evidence and advance coverage. Import. Flush outbox-owned entries. Reconcile. Abandon attempts whose writer is gone. Make entries terminal, export, and collect evidence. |
| **S** (status) | `pchat status` | Read only. |

| Guard | What it is |
|---|---|
| `TX` | A `BEGIN IMMEDIATE` transaction. Every row condition below is checked inside the transaction that performs the change. |
| `L_flush` | The existing non-blocking flock `outbox.flush.lock`. One flusher at a time; held for a whole flush. |
| `L_outbox` | The existing flock `outbox.lock`. It orders claim, import and any legacy append to `outbox.jsonl`. |
| **owner** | The row's writer identity equals the acting flow's own (boot id, pid, start time, `conn_id`). |
| **gone** | The writer is proven gone (section 5.2). |

## 3. State and transition tables

A transition happens only inside a `TX`, conditioned on the row's current
state and, for attempts, on its `generation`: `UPDATE … WHERE id = ? AND state
= ? AND generation = ?`. A change that matches no row does nothing. The actor
then stops all work on that attempt or entry.

### 3.1 Entry

| # | From | To | Actor | Guard | Durable write (one `TX`) | After commit |
|---|---|---|---|---|---|---|
| E1 | (none) | `open`, `direct` | P | none; silent-run refusal checked first | The entry, plus its parts as `unsent` with generation 0. The parts are fixed now, after logging in, from the connection's capabilities. | Send part 0 (A1). |
| E2 | (none) | `open`, `outbox` | B (import) | `L_flush` + `L_outbox` | The entry with its import identity, plus its parts if the row has them; else the parts are fixed at the first attempt (E4). Committed together with the `imports` row. | Unlink the claim file. |
| E3 | `open`, `direct` | `open`, `outbox` | P, owner | `owner_kind = 'direct'` and the owner matches | `owner_kind = 'outbox'` (the handoff). | Exit status 3, as today. |
| E4 | `open`, `outbox`, parts not yet fixed | the same, with parts | B | `L_flush`; no parts exist | The fixed parts, `unsent`. A legacy entry's "(delayed; written …)" marker is fixed into the text here, once. | Attempt part 0 (A1). |
| E5 | `open` | `done` | P, owner; or B | every part is `confirmed` | `state = 'done'`. | none |
| E6 | `open` | `terminal` (refused) | the actor that recorded A3 | A part is `refused` | The terminal reason, the parts and their attempts, all kept. | X1 export. |
| E7 | `open`, `outbox` | `terminal` (undecided) | B | A part has an `ended` attempt older than `UNDECIDED_MAX` that rule R3 leaves undecided | Terminal with a content-free alert; that attempt becomes `dead` (A10). | X1 export. |
| E8 | `open`, `direct` | `abandoned` | B | The owner is **gone**, and none of its attempts is `writing` (A8 first) | `state = 'abandoned'`: the entry is kept as evidence and is not sent (section 8, difference D1). | none |
| E9 | `open`, `outbox` (ack) | `done` | B | `L_flush` | `done`, after the TAGMSG round trip. | none |
| E10 | (none) | `open`, `outbox` (ack) | P | after a failed `pchat ack`, as today's queueing | The ack entry: no parts, and no text obligation. | Exit status 3, as today. |
| E11 | `abandoned` | `abandoned` | B | An `ended` attempt of the entry is still undecided more than `UNDECIDED_MAX` after its `written_at` | That attempt becomes `dead` (A10). There is no export and no alert, as today for a killed `pchat` (D1). | none |

`done`, `terminal` and `abandoned` are final. No transition leaves them except
evidence collection (G1), which deletes rows only.

### 3.2 Part

| # | From | To | Caused by |
|---|---|---|---|
| P1 | `unsent` | `inflight` | A1, which creates the attempt; generation + 1 |
| P2 | `inflight` | `confirmed` | A2 |
| P3 | `inflight` | `refused` | A3 |
| P4 | `inflight` | `unsent` | A4: the server rejected it before accepting anything |
| P5 | `inflight` | `uncertain` | A5 (`ended`) or A8 (writer gone) |
| P6 | `uncertain` | `confirmed` (msgid set) | A6 (`delivered`) |
| P7 | `uncertain` | `unsent` | A7 (`absent`): proved never published |
| P8 | `confirmed` | `confirmed` (msgid set) | R1 attributes the msgid of a part confirmed without one |

**`confirmed` never leaves `confirmed`**, and `refused` is final. A part
becomes `unsent` again only by P4 or P7, each of which proves that no message
was published.

### 3.3 Attempt

The writer column marks who may make each change. Only the writer itself may
end a `writing` attempt (A2–A5), unless the writer is proven gone (A8).

| # | From | To | Actor | Guard | Durable write (one `TX`) | Network outside the `TX` |
|---|---|---|---|---|---|---|
| A1 | (none) | `writing` | the entry's owner: P for `direct`, or B (holding `L_flush`) for `outbox` | The part is `unsent`. No attempt of this part is `writing` or `ended`. Every earlier part is `confirmed`. The entry is `open`. | The attempt row (writer, `conn_id`, generation, text key, `written_at = now`), and the part becomes `inflight`. | **After** commit: write the part's lines, then `PING :round`. |
| A2 | `writing` | `confirmed` | writer | owner; `state = 'writing'`; generation matches | `confirmed_at`, and the part becomes `confirmed`. | **Before:** the matching PONG arrived, with no FAIL and no 4xx/5xx reply. |
| A3 | `writing` | `refused` | writer | as A2 | The part becomes `refused`, and E6 runs in the same transaction. | **Before:** a FAIL was seen. |
| A4 | `writing` | `rejected` | writer | as A2 | The part goes back to `unsent` (403, 404 or 482 with no FAIL: nothing was accepted). | **Before:** that reply. The existing one channel recreation on 403 then repeats A1. |
| A5 | `writing` | `ended` (`closed`) | writer | as A2 | `ended_at = now`, `end_kind = 'closed'`, and the part becomes `uncertain`. | **Before:** the connection was **closed first** (section 5.1). |
| A6 | `ended` | `delivered` | B | Inside the reconciliation `TX`, rule R1 | An attribution row with msgid UNIQUE and `attempt_id` UNIQUE; the part becomes `confirmed` with that msgid. | none |
| A7 | `ended` | `absent` | B | Inside the reconciliation `TX`, rule R2 | The part goes back to `unsent`. The next A1 uses generation + 1. | none |
| A8 | `writing` | `ended` (`writer_gone`) | B | The writer is **gone** (section 5.2) | `ended_at` = when the writer was observed gone; `end_kind = 'writer_gone'`; the part becomes `uncertain`. | none |
| A9 | `confirmed` | `confirmed` + attribution | B | Rule R1, for a confirmed attempt without a msgid | An attribution row (msgid UNIQUE). | none |
| A10 | `ended` | `dead` | B | E7 or E11 | none beyond E7 or E11 | none |

`delivered`, `absent`, `refused`, `rejected` and `dead` are final. A `dead`
attempt still counts as evidence: it may own a message (section 7).

### 3.4 Evidence, coverage, import and exports

| # | Change | Actor | Guard | Durable write | Notes |
|---|---|---|---|---|---|
| V1 | Index a message | B, as it records a verified PRIVMSG or multiline message (live or replayed) | `TX` | `INSERT OR IGNORE` into `messages`, keyed by msgid | The checkpoint and the coverage `mark` advance past a message only **after** this commits. |
| V2 | A channel's coverage begins | B, when a catch-up finishes or a new channel is joined | `TX` | `since` and `mark`. `since` carries over only when the catch-up started from the stored `mark` (as in PR #20). | none |
| V3 | Coverage advances | B, on the PONG for the sync PING sent just before a flush | `TX`; the channel is live on this connection; every earlier V1 for the channel committed | `through = max(through, ping_sent_at)` | none |
| V4 | Coverage stops | B, on KICK or PART, a disconnect, or a failed V1 for the channel | in memory; nothing to commit | The channel leaves the live set. The checkpoint stays behind the failed message, so the next catch-up replays it. | none |
| I1 | Claim | B | `L_flush` + `L_outbox` | `rename(outbox.jsonl, outbox.claimed-<ns>-<rand>.jsonl)`, never onto an existing name | A filesystem step, not a database one. |
| I2 | Import | B | `L_flush` + `L_outbox`; `TX` | The entries, parts and held rows, and the `imports` row (name, line count, sha256), in **one** transaction | Section 6 |
| I3 | Unlink | B | after I2 commits | `unlink(claim file)` | Section 6 |
| X1 | Export a terminal entry | B (P too, for E6) | after E6 or E7 commits | 1. If an alert is set and `alert_exported` is unset: add the content-free `push` delivery, then set `alert_exported`. 2. If `dead_letter_exported` is unset: append the dead letter unless one with this id is already there, then set `dead_letter_exported`. | A crash may repeat the alert, but never loses it; the dead letter is written once. The order matches PR #20's `_finish`. |
| G1 | Collect evidence | B | `TX` | Delete the rows that invariant I-7 allows | none |

## 4. Invariants and proofs

### I-1. Intent before bytes

Every byte of a part is preceded by a committed `writing` attempt that names
its writer, its connection and its write time.

**Why it holds.** A1 is the only way to obtain an attempt, and the writer
sends only after A1 commits. This holds for every caller, because `post()` is
the only sending path, and it runs A1 itself, whoever called it.

**Corollary.** Any message an attempt produced carries a server time of at
least `written_at − A` (assumption C-1).

### I-2. Fencing

Only the attempt's own writer, or B once the writer is gone, moves an attempt
out of `writing`. No actor creates a new attempt for a part while any attempt
of that part is `writing` or `ended`.

**Why it holds.** A2–A5 require the owner. A8 requires gone. A1's guard
excludes a part with an unresolved attempt. A writer that has passed A1 and is
paused before sending still owns a `writing` attempt, which no one else may
end or retry.

Database leases and checks made before sending cannot close the gap between
the check and the send. The fence therefore rests on two things. While the
writer may still send, no one else acts on its attempt. Afterwards, either the
writer closed its connection (A5), or it is dead (A8).

### I-3. Confirmed parts are never resent

**Why it holds.**

- A1 requires `unsent`.
- A part reaches `unsent` again only by A4 or A7. A4 means the server rejected
  it with nothing accepted. A7 means its absence was proved.
- `confirmed` is final (section 3.2).
- A part is fixed once (E1 or E4) and never re-split. A retry therefore writes
  the same text, and only for a part whose earlier attempt is proved to have
  published nothing.

### I-4. No message satisfies two obligations

A message satisfies an obligation only by an attribution row. `msgid` is the
primary key of `attributions`, and `attempt_id` is UNIQUE.

Rule R1 attributes inside a single transaction, and only by a perfect
matching between the attempts and the messages of one component (section 7).

A confirmed attempt without a msgid also needs a message of its own, which
rules R1 and R2 count. One message therefore never counts for two attempts.
That includes an attempt confirmed earlier in the same flush, or by another
process.

### I-5. Stored progress survives storage errors

A storage failure never turns a written part into an `unsent` one, and never
discards a remaining obligation that has already been committed.

**Why it holds.**

- If A1 fails, nothing of that part is written.
- If A2–A5 fail, the attempt stays `writing` under its writer. The writer
  closes the connection and stops. Then:
  - a live bridge retries its own pending A5 or A2 on each flush (section
    5.1);
  - any other writer leaves, is then proved gone, and the attempt reaches A8,
    which makes it `uncertain` and reconciled. It is never `unsent`.
- If the E3 handoff fails, the caller falls back to section 6.4, which
  transfers ownership by entry id only.

The caller never re-queues the original whole text after anything was
written. That is round-5 finding 2.

### I-6. Ownership is visible before a competing flush can use a message

**Why it holds.** By I-1, a direct post's attempt row commits before its
bytes. The bridge can index that message (V1) only after it was published,
which follows the send, which follows A1's commit.

Reconciliation runs in a `BEGIN IMMEDIATE` transaction that starts after V1.
It therefore sees the attempt as `writing`, or as whatever came after. A
`writing` member keeps its component from resolving by R1 (section 7.3). That
is round-5 finding 3.

### I-7. Evidence is collected only when nothing depends on it

A row of text key *k* may be deleted only when both hold:

- no attempt of *k* is `writing` or `ended`;
- the row's window, or for a message its time, ends before `now − A −
  GC_MARGIN`.

The rows are a confirmed or `dead` attempt, an attribution, or a message.

Any later attempt of *k* has `written_at ≥ now`, so its window starts at or
after `now − A`. It cannot overlap a collected row. Coverage spans are never
collected.

This holds whatever the outage length. After a 26-hour outage, an `ended`
attempt still pins every row of its text key. That is round-5 finding 4.

### I-8. Decisions read current state

Every verdict is computed and applied inside one transaction, from the rows
current at that moment: attempts, attributions, messages and coverage. No
snapshot from the start of the flush is reused. That is round-5 finding 1.

### I-9. Import keeps duplicates and is idempotent

See section 6.3.

### I-10. Independent flow

An entry's work touches only its own rows. It interacts with other entries
only through components of the same text key (section 7). An undecided or
failing entry therefore never blocks another one, including another entry for
the same account.

Every open outbox entry still gets its attempt in each flush, as in PR #20.
Each entry's work is wrapped so that its failure is logged and the flush moves
on.

### How each round-5 finding is closed

| Round-5 finding | What goes wrong at `e658bb8` | What closes it |
|---|---|---|
| 1. Satisfied keys are stale within a flush (`chat-bridge:1104-1107`) | A key matched earlier in the flush is counted again later. | I-8. Matching and attribution happen in one `TX` per component, with no satisfied-key snapshot. |
| 2. A reservation-write failure loses part outcomes (`chatlib.py:517`) | `queued_entry` requeues the whole text. | I-5. There is no separate reservation write: A2 *is* the confirmation record. A failure leaves the attempt `writing`, which becomes `uncertain`. |
| 3. Ownership is written after the commit (`chatlib.py:516`) | A flush uses a direct post's message before the direct post's reservation exists. | I-1 and I-6. The `writing` row precedes the bytes, and R1 refuses a component with a `writing` member. |
| 4. Reservations are pruned by wall-clock time (`chat-bridge:947`) | After an outage, the evidence an older uncertain entry depends on is gone. | I-7. Evidence is collected by dependency, not age alone. |

## 5. Crashes and interleavings

### 5.1 Connections

- **One flow per connection.** A connection belongs to the single flow that
  opened it, inside one `post()` call. No other code path sends on it. It
  carries at most one unresolved attempt at a time; after A2, the next part
  may use it.
- **Close before `ended`.** For a timeout, a write error, an error numeric, or
  any exception escaping the send or wait:
  1. the writer closes the socket, with `shutdown` followed by `close`, whose
     return is the "torn down" point;
  2. only then does it commit A5.

  After a close, this process sends no further bytes on that socket. Bytes
  already in the kernel's send buffer may still reach the server. That is
  server-delayed delivery, which `SETTLE` covers (C-2).
- **Live bridge, own attempts.** If B's A5 (or A2) commit fails, B keeps an
  in-memory list of its own closed attempts, with their outcome. It retries
  each commit at the start of every flush. It is still the owner, and it has
  closed the socket, so the commit is valid.

  After a bridge restart, the old process is gone, and A8 applies instead.
  Either way, a persistent live bridge recovers its own ended attempts, and
  automatic absence recovery is kept.

### 5.2 Writer gone

A writer is **gone** when any of these holds:

- the machine's boot id differs from the recorded one;
- no process with the recorded pid exists;
- the process with that pid has a different start time.

If the start time cannot be read, the writer is not proved gone.

A8 records `ended_at` as the moment of that observation. This is an upper
bound on when the dead writer could have sent its last byte.

A live writer that is paused or hung is never gone. Its attempt stays
`writing`, and its part stays undecided until the writer resumes or exits:
never resent and never ended by B. If the writer is a dead direct post, its
entry is handled by E8.

### 5.3 Assumptions

Anything these do not prove stays undecided.

- **C-1, clocks.** The writer, the bridge and the chat server run on the same
  machine and read the same wall clock. The server's message time and the
  writer's clock differ by at most `A`. The clock does not step by more than
  `A` within a window.
- **C-2, server-delayed delivery.** The server publishes any message an
  attempt's bytes produce within `SETTLE` after the attempt's `ended_at`.
  - For a closed attempt: after the close.
  - For a gone writer: after it was observed gone. The kernel finishes or
    resets the socket when the process dies, which is before it was observed
    gone.
- **C-3, confirmation.** A PONG for the `PING :round` sent after a part, with
  no FAIL and no 4xx or 5xx reply in between, means the server published that
  part as exactly one message. That message's time is at most the PONG's
  arrival time plus `A`. A FAIL, or a 403, 404 or 482 reply with no FAIL,
  means the part was not published. These are the rules PR #20 already uses.
- **C-4, atomic parts.** A `draft/multiline` batch is published whole, as one
  message, or not at all, and an unterminated line is not published.

  This note does not prove C-4. A same-account message in the window whose
  text is a strict prefix of the part's text therefore blocks absence (rule
  R2). A partly published part stays undecided, rather than being resent.
- **C-5, complete record.** Coverage `[since, through]` for a channel means
  that every message the server published there with a time in that interval
  is in `messages`.

  Coverage begins only after a finished catch-up, or when a new channel is
  joined, and advances only through a sync PING answered while the channel is
  live. Coverage stops on any lost membership or any failed index write. This
  is PR #20's coverage model, now committed in order with the evidence.
- **C-6, identity.** The `account` tag on a message is the server-verified
  sender. Only verified messages are evidence.

  Untracked producers are possible: the old live tools before activation, a
  person using the same account, or another client. They are handled in rule
  R1 and in section 7.4, not assumed away.

### 5.4 A crash at every transition

"Gone" below means B observes the writer's death on its next flush (A8).

| Crash point | What survives | Recovery | Why nothing is lost or resent |
|---|---|---|---|
| P, before E1 commits | nothing | none (as today, a killed `pchat` queued nothing) | Nothing was written. |
| P, after E1, before A1 | `open`, `direct`, all parts `unsent` | gone, then E8 | Nothing was written. |
| P, after A1, before any byte | `writing` | gone → A8 → `uncertain` → R2 proves absence once coverage allows | Never resent unproved (I-3). |
| P, during the send or the wait | `writing` | as above; R1 or R2 decide | as above |
| P, after the PONG, before A2 | `writing` | gone → A8 → R1, delivered when the match is unique | The confirmed part is never blindly resent (I-5). |
| P, after A2 | `confirmed` | none needed | final (section 3.2) |
| P, after the close, before A5 | `writing` | gone → A8 | `ended_at` is later than the real close: a wider, safe window. |
| P, after A5, before E3 | `ended`; entry `direct` | gone → E8; the `ended` attempt is still reconciled for evidence | Same as today: a crash before queueing loses the queueing (D1). |
| P, after E3 | `outbox` | B flushes it | none |
| B, mid-claim (I1) | the renamed file | imported next flush | The rename is atomic. |
| B, during I2 | the claim file, no `imports` row | re-imported to the same ids | The transaction rolled back (section 6.3). |
| B, after I2, before I3 | the `imports` row and the file | the hash matches, so only unlink | idempotent |
| B, after A1 (outbox entry) | `writing` (B's) | the restarted B sees the writer gone → A8 | as for P |
| B, after A5 | `ended` | R1 or R2 next flush | none |
| B, during reconciliation | the `TX` rolled back | decided again next flush | atomic |
| B, after E6 or E7, before X1 | terminal, export marks unset | X1 finishes | The alert may repeat; the dead letter is written once. |
| B, after X1, before its mark | the export written, the mark unset | X1 again | The dead letter is deduplicated by id; the alert repeats harmlessly (as today). |
| B, after V1, before the checkpoint advances | the message indexed | catch-up replays it; `INSERT OR IGNORE` | Coverage never passes an unindexed message. |
| B, during V3 | the old `through` | the next sync | Coverage only grows. |
| B, during G1 | rolled back, or done | none | I-7 is checked inside the `TX`. |

### 5.5 Interleavings

1. **A direct post and a flush (round-5 finding 3).** `alp-solver-2` has an
   uncertain outbox part U with the text `[status] build green`, which it
   never published. It then posts the same text directly as D.

   D's A1 commits, D's message `m` is published, and B indexes `m`. B's flush
   then runs while D waits for its PONG.

   R1 refuses: the component {U, D} has a `writing` member. R2 cannot rule U
   absent: `m` is not forced to a confirmed attempt, because D is not
   confirmed yet. U stays undecided.

   After D's A2, the component {U (`ended`), D (`confirmed`)} has one
   message. That is enough for D alone, so `m` is forced to D. R2 rules U
   absent once coverage covers its window, and U is resent. One message never
   counted for both.
2. **Two flushers.** `L_flush` makes the second one skip. Even without it,
   A1's guard lets only one attempt per part exist, inside a `TX`.
3. **A paused direct writer.** P passes A1, then stops before sending. B never
   ends that attempt (I-2), and no retry exists. If P resumes and sends, the
   message lands in its open window, and A2 or A5 follows.

   Meanwhile another part of the same text key is decided only by R2's
   forcing argument, which a `writing` attempt cannot break. Section 7.3
   shows why.
4. **A hung bridge.** A second bridge started while the first holds `L_flush`
   skips the flush and logs it. The first bridge's attempts stay `writing`
   until it exits; then A8 applies.
5. **`pchat status` during a flush.** It runs a read transaction and sees a
   consistent snapshot.
6. **`pchat` queues during a flush.** New entries are database rows (E1 and
   E3). A legacy append goes to `outbox.jsonl` under `L_outbox`, after or
   before a claim, and is never lost (as today).

## 6. Importing the old outbox

### 6.1 A new rule: frozen snapshots

Today `_Claimed.save()` (`chat-bridge:1030-1036`) rewrites claimed files, so a
claimed file is **not** immutable. Immutability is a new rule, which this
design creates:

- **The cutover marker.** A `meta` row, written when the schema is created.
  Once it exists, no code path writes a claimed file. `_Claimed.save()` and
  its callers are removed, and all progress lives in the database.
- **Old writers.** Quiescing the old writers (the running bridge and the old
  tools' flush) happens **only** at the separately authorized future
  activation, before the new bridge first imports live state. Before then,
  plateia's copy touches no live state. A claimed file the old code may have
  rewritten is imported once, as it stands under the locks.

### 6.2 The fenced import

The import runs only inside B's flush, while holding both `L_flush` and
`L_outbox`. It does local disk work only.

1. **Claim (I1).** If `outbox.jsonl` is not empty, rename it to a fresh unique
   name. Then list every `outbox.claimed-*.jsonl`.
2. **Read** each claim file whole and compute its sha256 and line count.
3. **Check** for an existing `imports` row with the file's name:
   - the same hash: the rows are already in, so go to step 6;
   - a different hash: **hold** the file, recording it once, and alert once
     with no content. Do not import it and do not unlink it.
4. **Import (I2).** In one `TX`, take each row with its line ordinal `i`:
   - **The entry id.** The row's own `id` when it has one. Otherwise it is
     derived from `(claim file name, i)`, for example as
     `sha256(name + ":" + i)`. Two identical lines therefore become two
     entries.
   - **The row's sha256** is stored with the entry.
   - **An id that already exists with a different row sha256** is a
     collision. The row goes to `held` (raw text kept), and is not merged.
     The other rows proceed.
   - **An id that exists with the same row sha256** is a replay, and is
     ignored.
   - **An unreadable line** goes to `held` with its raw text.
   - **A text-only row** becomes an `open`, `outbox` entry with no parts yet
     (E4 fixes them).
   - **A handoff row** (section 6.4) for an existing `direct` entry changes
     only its ownership (E3). No part state is taken from the row.
   - **A row carrying PR #20-style `parts`** (only in test or development
     state; that format never ran live):
     - a `confirmed` part becomes a confirmed attempt with its recorded
       times;
     - a `writing` or `uncertain` part becomes an `ended` attempt with
       `end_kind = 'writer_gone'` and `ended_at` set to the import time.
       The old writer is stopped by then.
     - an `unsent` part stays `unsent`.
   - **The `imports` row** (name, line count, sha256) is inserted in the same
     `TX`.
5. Commit.
6. **Unlink (I3)** the file, only after the commit.

### 6.3 Why the import is idempotent and keeps duplicates

- **A crash before the commit** leaves no rows and no `imports` row. The next
  import derives the same ids from the same name and ordinals.
- **A crash after the commit** leaves the `imports` row with a matching hash,
  so step 3 only unlinks.
- **A file whose hash changed** after its import is never merged; it is held
  and reported.
- **Two identical lines** have different ordinals, so they are two
  obligations.
- **A claim** renames only onto a name that does not exist, so two claims never
  share a name.

### 6.4 The handoff fallback

If E3 cannot commit, P appends one compatibility row to `outbox.jsonl` under
`L_outbox`. The row carries the entry id and the post's legacy fields, plus
`handoff: true`. Import then decides how to treat it:

- **The entry exists** (E1 committed): import applies only the ownership
  change. The database's attempts remain the authority, and any `writing`
  attempt reaches A8 once P exits.
- **The entry does not exist** (E1 never committed, so nothing was sent): the
  row is a whole legacy post, exactly as today.

If that append fails too, P fails as it does today when the outbox cannot be
written. Whatever already committed still prevents a blind resend.

## 7. Reconciliation rules

### 7.1 Windows

| Attempt state | Window `[low, high]` |
|---|---|
| `writing` | `[written_at − A, +∞)`: still open |
| `confirmed` (no msgid) | `[written_at − A, confirmed_at + A]` (C-3) |
| `ended`, `dead` | `[written_at − A, ended_at + SETTLE + A]` (C-2) |

A window is **final** once the attempt has left `writing`. A final window never
changes.

### 7.2 Components

For a text key *k*, the **members** are its attempts that are:

- `writing`, `ended` or `dead`;
- or `confirmed` without a msgid.

Members are connected when their windows overlap. A **component** is a
connected set of members.

A message can be produced only by an attempt whose window contains its time
(I-1, C-2, C-3), so all candidates for a component's messages are inside it.

- **`K`** is the confirmed members without a msgid. Each produced exactly one
  message in its window (C-3).
- **`G`** is the `ended` members: each produced zero or one message.
- **`D`** is the `dead` members: each produced zero or one message, and they
  are never decided again.
- **`W`** is the `writing` members.
- **`M`** is the unattributed messages of key *k* whose time lies in some
  member's window.

### 7.3 The three verdicts

Each verdict is decided per component, inside one `TX` (I-8).

**R1, DELIVERED (the whole component at once).** All of these must hold:

- `W` and `D` are empty, so every window is final and every member decidable;
- `|M| = |K| + |G|`, with no message left unexplained;
- a perfect matching of `K ∪ G` onto `M` exists, with each member's message
  inside its window.

Then every `G` member becomes `delivered` (A6), and every `K` member gets its
matched msgid (A9). Each one is an attribution row.

*Argument.* C-6 allows untracked producers. An untracked same-text message
inside these windows is indistinguishable from ours, and this rule admits
that one residual (section 7.4). Otherwise, every message in `M` came from a
member of the component:

- `K` produced `|K|` of them;
- so `|M| = |K| + |G|` means every `G` member produced one.

Swapping msgids inside the component is harmless:

- all of its messages become attributed together;
- every later attempt is created after they were indexed, so none of them is
  a later attempt's message (I-1).

Requiring `W` to be empty is what makes this sound. A window that is still
open could later need a message the matching gave to someone else. Round-5
finding 3 is that case.

**R2, ABSENT (one `ended` member `Y`).** All of these must hold:

- coverage is complete over the hull of `Y`'s window and every `K` window in
  `Y`'s component, computed over `K ∪ G ∪ D` (final windows only);
- no unattributed same-account, same-channel message in `Y`'s window has text
  that is a strict, non-empty prefix of `Y`'s text (C-4);
- `K` can be fully matched into `M`. If not, the record contradicts a
  confirmation: undecided, logged as an anomaly;
- **every** message of `M` inside `Y`'s window is *forced*: without it, `K`
  can no longer be fully matched.

Then `Y` becomes `absent` (A7), and its part goes back to `unsent` for the
owner's next attempt.

*Argument.* Suppose `Y` did publish a message `m_Y`.

- By C-1 and C-2 its time is in `Y`'s window. By C-5 it is in `messages`.
- It is unattributed:
  - R1 never resolves a component with `Y` open;
  - every attributed message predates every attempt created after its
    attribution (I-1).
- `K`'s own messages are distinct from `m_Y`, and they are all in `M`
  (coverage), so `K` can be fully matched without `m_Y`. `m_Y` is not
  forced, and R2 does not hold. This is a contradiction.

The argument uses no assumption about untracked producers. An extra message
is never forced, so it blocks absence. `W`, `G` and `D` members do not affect
it. A `writing` attempt could only add messages, never remove one of `K`'s.

**R3, UNDECIDED** is everything else. The part stays `uncertain`, is shown in
`pchat status` as awaiting a delivery check, and is checked again each flush.

When an `ended` attempt of an outbox entry is still undecided more than
`UNDECIDED_MAX` after its `written_at`, E7 runs in the same `TX`:

- the entry becomes terminal, and the attempt becomes `dead`;
- the dead-letter export keeps the entry's unconfirmed part texts and its
  per-part records (states, write times, msgids);
- the owner gets the content-free alert, for example "an outbox post from
  alp-solver-2 to #alpha could not be confirmed for 24 h; dead-lettered".

It is never resent.

### 7.4 Conservatism, in brief

- **Identity.** Same verified account, same case-folded channel, exact
  server-visible text, a time inside the window, an msgid not already
  attributed.
- **Competing or untracked producers.**
  - Any message that is not needed makes `|M| > |K| + |G|`, so R1 fails, and
    it is never forced, so R2 fails.
  - The one residual: an untracked identical message from the same account,
    in exactly the window of one of our `G` attempts that published nothing,
    makes R1 count that attempt delivered. The channel then does show that
    exact text from that account at that time. Nothing is duplicated.
- **Complete joined-membership coverage.** C-5. Absence needs coverage through
  `ended_at + SETTLE + A`, so it waits for the attempt to end and settle.
- **Automatic absence recovery is kept.** R2 needs no tags, no history
  capability beyond today's, and no exact-once mechanism. A live bridge
  decides its own closed attempts (section 5.1).
- **Ambiguity stays UNKNOWN.** For example: two `ended` attempts with one
  message both could own; or a `writing` member; or a prefix candidate; or
  incomplete coverage. Each is undecided, visible, never resent, and
  dead-lettered with its text after 24 hours.
- **Unrelated obligations keep flowing.** Components are per text key. Other
  entries, including the same account's, are attempted every flush.
- **A 26-hour outage keeps the evidence.** I-7 pins every row of a text key
  while any attempt of that key is unresolved. Coverage across the outage
  exists only if the catch-up resumed from the stored mark. Otherwise R2
  waits, and the 24-hour rule applies.
- **No protocol experiments.** Nothing assumes client tags, echo-message,
  labeled-response or a CHATHISTORY capability beyond what PR #20 already
  uses.

### 7.5 Worked example

All data is invented. `alp-solver-2` posts a three-part question to
`#alpha`.

1. Parts 0 and 1 get A2. Part 2's PONG never arrives. The writer closes the
   connection and commits A5 (`ended_at = t`). It hands the entry to the
   outbox (E3), and `pchat` exits 3, saying "2 of 3 parts posted".
2. At `t + 40 s`, B indexes part 2's message `m2` (the server was slow). On
   the next flush, the component for part 2's text holds part 2 only (`G`),
   with `M = {m2}`. R1 holds: it is delivered, and `m2` is attributed.
3. Alternatively, `m2` never appears. Once coverage reaches `t + 605 s`, and
   no message or prefix is in the window, R2 holds: the part is absent, and
   B sends it again (generation 1, A1). Parts 0 and 1 are never touched.
4. Alternatively, coverage never completes, because the bridge was kicked from
   `#alpha` and rejoined without a resumable catch-up. Then the part is
   undecided. At `written_at + 24 h` it is dead-lettered with part 2's text,
   and `pat` gets the alert.

## 8. Mapping to #19

#19's requirements and acceptance are **unchanged**. The table maps each one to
this design.

| #19 | Where it is met |
|---|---|
| R1, the per-part outcome | Part states (section 3.2); A2, A5 and A4 give confirmed, uncertain and unsent. `PART_WAIT` is absolute. |
| R2, never resend a confirmed part | I-3; E3 hands off the remainder, and no whole-text requeue remains. |
| R3, fixed parts | E1 and E4 fix the parts once. A legacy entry's marker is fixed into its text then. A part the server can no longer carry stays unsent and visible (as in PR #20). |
| R4, durable progress | A1 before bytes; A2 or A5 before the next part; A8 makes a crashed part `uncertain`. Every caller is covered, not only the bridge. |
| R5, reconcile and never resend blindly | R1, R2, R3 and E7, with the dead-letter content kept by X1. |
| R6, independent flow | I-10. |
| R7, refusals | A3 and E6; the dead letter lists the confirmed parts. |
| R8, visibility | `pchat status` reads the database for its counts: waiting to post, awaiting a delivery check, held rows. |
| R9, no exactly-once | Stated here; the guidance and provenance must keep saying it. |
| R10–R12, capture, guidance, docs | Unchanged. The implementation updates provenance, `SKILL.md`, D-72 and the `docs/chat_capture.md` rows. |
| R13, privacy | All examples and future tests use invented data. |
| Acceptance 1–10 | Each one stays. The tests observe outcomes through `pchat`'s output and exit status, the outbox summary and status, the dead letters, and the fake server's write log, rather than the contents of claimed files. |

### Contract and acceptance differences

**Proposed, not adopted:**

- **D1. Adopting a dead direct writer's remainder.** Today a `pchat` killed
  mid-post queues nothing. This design keeps that: E8 retires the entry as
  evidence only, with no send and no alert.

  Adopting the unsent remainder instead would deliver more posts. It would
  also be a new behavior, and it needs an owner decision.

**Not differences, but observable narrowings inside #19's "Unknown" outcome.**
These are listed for the owner's awareness:

- **N1.** Delivery needs a complete count (R1). A matching message alongside
  an unexplained extra one, or alongside a live same-text writer, now leaves
  the part undecided. #19's literal "Delivered" reading would have accepted
  it. The issue review's amendment ("leave indistinguishable evidence Unknown
  unless distinct messages establish delivery") asks for this.
- **N2.** A same-account message in the window that is a strict prefix of the
  part's text blocks absence (C-4).
- **N3.** A live but hung writer keeps its own part undecided indefinitely.
  The writer is not the bridge, so no dead letter is produced for it. Same-key
  parts of other entries may then be dead-lettered after 24 hours rather than
  resent.

**Owner decision needed at implementation.** Adopting SQLite as the outbox
store of plateia's copy. It would be recorded as a new design decision next
to D-72. Migrating live state belongs to activation, which is separately
authorized.

## 9. Future tests

These are deterministic tests to write later. They are not written or run as
part of this note.

**Common setup.** Each test uses:

- the existing `FakeServer`/`FakeConn` transport;
- an injected wall clock and monotonic clock;
- a fake process table for gone checks (boot id, pid, start time);
- `_isolation` with a temporary state directory;
- invented data only.

None of them uses the network, the home directory or real incident data.

1. **The round-5 regressions.**
   1. **Same flush.** Entry A has a confirmed part with no msgid and an
      uncertain part of the same text; entry B has a delivered part of that
      text. With three messages and complete coverage, one flush retires
      both, and resends nothing.
   2. **Storage failure at confirmation.** A2 fails after part 1 of 3 is
      confirmed, through `pchat`, `notify` and `announce`. No whole-text
      requeue. Part 1 is never rewritten. The remainder is handed off, or
      falls back to section 6.4.
   3. **A direct post during a flush.** An uncertain U and a direct D share a
      text. The flush runs after D's message is indexed and before D's A2. U
      stays undecided. After D's A2, U is ruled absent and resent once.
   4. **A 26-hour outage.** A confirmed direct post, then an identical
      uncertain post with only the first message logged. After the clock
      advances 26 hours, the evidence is still there, and U is not retired as
      delivered.
2. **Unique attribution.** A msgid is never attributed twice. This covers
   identical part texts in one entry and across entries, the `attributions`
   uniqueness constraint, and a crash between two components' transactions.
3. **Every crash boundary.** Inject a kill at each row of section 5.4 for
   `pchat`, and for the bridge's flush. Check that:
   - no confirmed part is rewritten;
   - no entry is lost;
   - each recovery verdict is as listed.
4. **Storage failure and part preservation.** Inject a database error at A1,
   A2, A5, E3 and the X1 marks. In every case:
   - no part becomes `unsent` without A4 or A7;
   - no whole-text requeue happens;
   - a live bridge recovers its own pending A5.
5. **Direct post plus flush, and a paused writer.**
   - A writer paused after A1 is never ended or retried by B. It resumes and
     confirms.
   - A writer proved gone (pid missing, start time changed, boot id changed)
     is ended by A8.
   - An unreadable start time is not treated as gone.
6. **Import.**
   - Two identical lines import as two entries.
   - A crash before the commit, and after the commit but before the unlink,
     both replay idempotently.
   - A changed hash is held.
   - An id collision is held and not merged, while the other rows proceed.
   - Unreadable lines are kept.
   - A handoff row for an existing entry changes ownership only.
   - Claim names never collide.
7. **Membership loss.** A KICK or PART, a disconnect, a failed index write,
   and a stale catch-up reply each stop coverage. R2 waits; R1 is unaffected.
8. **Retention.** I-7 keeps evidence while an attempt of the same text key is
   `writing` or `ended`, and collects it afterwards.
9. **Ambiguity and the 24-hour dead letter.** These stay undecided until 24
   hours, then are dead-lettered with their texts and per-part records, with
   one content-free alert even across a crash during X1:
   - two `ended` attempts with one shared message;
   - an extra untracked message;
   - a prefix message.
10. **Independent flow.** While another entry is undecided, refused or
    failing, entries for another account, and for the same account with
    another text, are delivered in the same flush.
11. **Unchanged paths.**
    - Old-format and ack entries.
    - A failure before any write queues the whole post.
    - A slow but confirmed post succeeds with no queueing.
    - Silent-run suppression still applies.
    - `pchat` exit codes and messages are the same.
    - All of #19's existing acceptance tests pass.

## 10. Feasibility and risks

**Feasibility.** Python's standard `sqlite3` module covers everything here,
with no new dependency. The change keeps PR #20's:

- per-part posting;
- fixed parts;
- the refusal and legacy paths;
- the coverage model;
- the membership-loss handling.

It replaces about 300 lines of file-state code in the bridge and `chatlib`
(reservations, attribution files, `_Claimed`, and matching that relies on
snapshots).

**Risks.**

- **Storage locking.** SQLite locking on a local disk is reliable. The
  bounded busy timeout turns contention into a failure before anything is
  sent, never after.
- **Liveness probes.** Reading the boot id and process start time differs
  between macOS and Linux, and both need fakes in tests. An unreadable value
  always means "not gone".
- **Assumptions C-2 and C-4** are not proved. C-4 is defended by the prefix
  rule; C-2 depends on `SETTLE` being long enough.
- **Narrowings N1–N3** may produce more 24-hour dead letters in rare
  same-text cases. Each one carries an alert, and none is a duplicate.
- **Scope.** Every transport caller changes (`pchat`, `agentcli`,
  `announce`, the bridge). The tests in section 9 are the guard.
- **Activation.** Nothing here touches live state. Quiescing the old writers,
  and the first live import, belong to the separately authorized activation.
