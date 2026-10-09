# Chat outbox state: one durable authority for partial-post delivery

This is the design note for #19 and pull request #20. It is design only:
nothing here is implemented, and implementing it needs a separate owner
decision.

This is revision 2. It answers design review round 1, and section 11 lists
what changed and why.

## Background

PR #20 (head `e658bb8`) repairs a long post that was reposted on every outbox
flush. Its fifth review found four remaining defects. All four come from one
cause: outbox state is split across several files and processes.

- Part progress lives in rewritten claimed outbox files.
- Confirmed parts whose msgid is unknown live in `outbox-reserved.jsonl`.
- Msgids already counted live in `outbox-attributed.jsonl`.
- Record completeness lives in `record-coverage.json`.

Several processes write that state, under different locks, and the bridge's
flush reads it as start-of-flush snapshots and prunes it by wall-clock age.

This note replaces the split state with one SQLite database:

- Every transport caller records its intent there before sending any byte.
- Reconciliation decides inside one transaction, from the current state.
- Evidence is kept while anything depends on it.

The delivery contract is #19's:

- confirmed parts are never resent;
- unsent parts stay durable;
- an uncertain part is resent only when its absence is proved.

Delivery is at-least-once with that check. Nothing claims exactly-once: no
transaction spans both the database and the chat server.

Where this note proposes a change to #19's contract or acceptance, section 8
names it as a proposal. None is approved by this note.

Every example uses invented data: projects `alpha` and `beta`, the owner
`pat`, the assistant `sam`, and the agent `alp-solver-2`.

## Contents

1. [Terms and constants](#1-terms-and-constants)
2. [The authority](#2-the-authority)
3. [State and transition tables](#3-state-and-transition-tables)
4. [Invariants and proofs](#4-invariants-and-proofs)
5. [Connections, finality, assumptions and crashes](#5-connections-finality-assumptions-and-crashes)
6. [Importing the old outbox](#6-importing-the-old-outbox)
7. [Reconciliation rules](#7-reconciliation-rules)
8. [Mapping to #19, and proposed differences](#8-mapping-to-19-and-proposed-differences)
9. [Future tests](#9-future-tests)
10. [Feasibility and risks](#10-feasibility-and-risks)
11. [Revisions](#11-revisions)

## 1. Terms and constants

| Name | Value | Meaning |
|---|---|---|
| `A` (`CLOCK_ALLOWANCE`) | 5 s | The allowed difference between the writer's wall clock and the server's message time. |
| `PART_WAIT` | 30 s | The absolute deadline for the server to confirm one part (as in PR #20). |
| `DRAIN_WAIT` | 30 s | After `PART_WAIT` passes, how much longer the writer keeps reading for the server's late in-order reply before closing (section 5.2). |
| `UNDECIDED_MAX` | 24 h | How long a part may stay undecided, counted from its attempt's write. Then it is dead-lettered. |
| `GC_MARGIN` | 1 h | Extra age before evidence may be collected (I-7). |

PR #20's `SETTLE` bound is **not used**: absence now needs proved finality
(section 5.2), not a settling time.

- **Entry.** One logical post or acknowledgement that the transport owes. It
  is created by `pchat post`, `pchat ack`, `agentcli.notify`, `announce`, or
  by import (section 6).
- **Part.** A fixed piece of a post that the server publishes as one message:
  one `draft/multiline` batch or one line. A part has an index `n`, its
  *lines*, and its exact *server-visible text*, which is what the bridge
  records. A part is **multi-line** if its batch has more than one line.

  An acknowledgement has one pseudo-part, the TAGMSG.
- **Attempt.** One try at sending one part over one connection, numbered by a
  per-part generation. Only an attempt can produce a message.
- **Text key.** The triple (account, channel case-folded, exact text).
- **Writer.** The flow holding an attempt's connection. It is identified by
  the boot id, the pid, the process start time, and a connection id. The
  connection id includes the connection's local TCP port and the server's
  address.
- **Finality.** The writer received, on that connection, the server's in-order
  reply to a line sent after the part. The reply is the PONG to `PING :round`.
  Section 5.2 explains what finality proves.
- **Message.** A verified message in the bridge's record: msgid, channel,
  account, exact text and server time.
- **Fragment test.** A message's text *t* passes the fragment test against a
  part's text *p* when:
  - *t* is not exactly *p*; and
  - every newline-separated segment of *t* is a non-empty substring of *p*.

  It is a superset of every text a partial publication of a multi-line part
  could show, in any order, whether its pieces were joined or not (section
  5.3, C-4).

## 2. The authority

### 2.1 One database

All durable outbox state lives in one SQLite database, `outbox.db`, in the
chat state directory. It replaces:

- the progress inside claimed outbox files;
- `outbox-reserved.jsonl`;
- `outbox-attributed.jsonl`;
- `record-coverage.json`.

**Settings:**

- WAL journal mode;
- `synchronous=FULL`, so a commit survives a power loss;
- a bounded busy timeout;
- every write in a `BEGIN IMMEDIATE` transaction. That takes the single write
  lock at its start, so each decision is read and applied in one serialized
  transaction.

**No network I/O inside a transaction.** Logging in, sending, waiting,
draining and closing all happen between transactions.

**Not a second authority.** These are boundaries, not sources of truth:

- **Imports.** `outbox.jsonl` and `outbox.claimed-*.jsonl` (section 6).
- **Exports.** `dead-letters.jsonl`, and the owner alert, which goes on the
  bridge's pending-delivery queue (`pending.json`). The delivery ledger
  (`deliveries.jsonl`) records outcomes only. Each export is written after
  its terminal state commits, and the export protocol is X1 (section 3.5).
- **The chat record.** The channel logs stay the chat record that other
  readers use. For the outbox, the bridge also indexes verified messages into
  the database (V1), so evidence and coverage are ordered with outbox state.

### 2.2 Tables

The columns listed are the minimum the proofs rely on.

| Table | Key | Holds |
|---|---|---|
| `meta` | name | The schema version and the cutover marker (section 6.1). |
| `accounts` | account | `designated_at`, `suspended_at`, and the suspension reason (R1 and section 7.4). |
| `entries` | `id` | The origin and kind (`post` or `ack`); account, channel, original text, `cont`, `reply_to` and written-at; `owner_kind` (`direct` or `outbox`) and the owner's writer identity; `state` (`open`, `done`, `terminal` or `abandoned`); the terminal reason and its alert flag; the export marks `dead_letter_exported` and `alert_exported`; and its import occurrence (claim file, line ordinal, row sha256). The handoff reference is UNIQUE when present. |
| `parts` | (`entry_id`, `n`) | The kind, the lines, the server-visible text, `multi_line`; `state` (`unsent`, `inflight`, `uncertain`, `confirmed` or `refused`); the current generation; and the msgid once attributed. |
| `attempts` | `id` | `entry_id`, `n`, `generation`, the text key, `multi_line`, the writer identity, `conn_id`; `state` (`writing`, `confirmed`, `refused`, `rejected`, `ended`, `delivered`, `absent` or `dead`); `written_at`, `final_at` (when finality was reached, else NULL) and `ended_at`; `end_kind` (`closed`, `writer_gone` or `connection_closed`); and a detail. UNIQUE (`entry_id`, `n`, `generation`). |
| `attributions` | `msgid` | `attempt_id` (UNIQUE), and when it was made. |
| `messages` | `msgid` | Channel, account, exact text and server time, for verified messages only. |
| `coverage` | channel | `since`, `through` and `mark`, as PR #20 keeps them. |
| `imports` | claim file name | The line count, the content sha256, and when it was imported. |
| `held` | (file name, ordinal), or the file name alone | The raw row, or a reference to the file, and why it was held. |

### 2.3 Actors and guards

| Actor | Who | May do |
|---|---|---|
| **P** (direct writer) | a `pchat post` or `pchat ack` process; the `agentcli.notify` flow inside its host process; the bridge flow running `announce` | Create its entry. Write its own attempts. Hand its entry to the outbox. Export a dead letter. |
| **B** (bridge) | the single `chatbridge` service process | Index messages and advance coverage. Import and apply handoffs. Flush outbox entries. Reconcile. End attempts of a gone writer or a closed connection. Make entries terminal. Export. Collect evidence. Suspend designations. |
| **S** (status) | `pchat status` | Read only. |
| **O** (owner) | the owner, by an explicit decision | Designate an account (`designated_at`), or lift a suspension. This is a future, separately authorized action. |

| Guard | What it is |
|---|---|
| `TX` | A `BEGIN IMMEDIATE` transaction. Every row condition is checked inside the transaction that changes the row. |
| `L_flush` | The existing non-blocking flock `outbox.flush.lock`. One flusher at a time, held for the whole flush, including import and export. |
| `L_outbox` | The existing blocking flock `outbox.lock`. It orders claims, imports, legacy appends, handoff rows and dead-letter exports. |
| **owner** | The acting flow's writer identity equals the row's. |
| **gone** | Section 5.4. |
| **conn-closed** | Section 5.4. |

## 3. State and transition tables

Every transition is a conditional update inside one `TX`:
`UPDATE … WHERE id = ? AND state = ? [AND generation = ?]`. A change that
matches no row has no effect, and the actor then stops all work on that
attempt or entry.

### 3.1 Entry

| # | From | To | Actor | Guard | Durable write (one `TX`) | After commit |
|---|---|---|---|---|---|---|
| E1 | (none) | `open`, `direct` | P | Silent-run refusal is checked first, and commits nothing (as today). | The entry, and its parts as `unsent`. A post's parts are fixed now, after logging in, from the connection's capabilities. An ack has one pseudo-part. | A1 for part 0. |
| E2 | (none) | `open`, `outbox` | B, by import | `L_flush` + `L_outbox` | The entry with its occurrence identity, committed with the file's `imports` row. | I3 |
| E3 | `open`, `direct` | `open`, `outbox` | P, owner | — | `owner_kind = 'outbox'`: the handoff. | exit 3, as today |
| H1 | `open`, `direct`, or `abandoned` | `open`, `outbox` | B, by import of a handoff row | `L_flush` + `L_outbox`. The row's entry id **and** writer identity equal the entry's; the entry is neither `done` nor `terminal`. | `owner_kind = 'outbox'`, and `state = 'open'` again if it was `abandoned`. | none |
| E4 | `open`, `outbox`, parts not yet fixed | the same, with parts | B | `L_flush`; no parts exist | The fixed parts, `unsent`. A legacy post's "(delayed; written …)" marker is fixed into its text here, once. | A1 |
| E5 | `open` | `done` | P, owner; or B | Every part is `confirmed` | `done` | none |
| E6 | `open` | `terminal` (refused) | the writer that records A3 | — | The terminal reason. In the same `TX` as A3. | X1 |
| E7 | `open` or `abandoned` | `terminal` (undecided) | B | Some part has an attempt in `writing`, or `ended`, that R1 and R2 leave undecided more than `UNDECIDED_MAX` after its `written_at`. That includes a live writer's `writing` attempt. | Terminal, with the content-free alert. An `ended` attempt becomes `dead` (A10). A `writing` attempt is **left** `writing`: it is neither ended nor retried (I-2). | X1 |
| E8 | `open`, `direct` | `abandoned` | B | The owner is **gone**, and none of its attempts is `writing` (A8 first). | `abandoned`. The unsent remainder is not adopted (D1). Unknown parts still go to E7. | none |

`done` and `terminal` are final. `abandoned` leaves only by H1, which needs the
writer's own durable handoff, or by E7.

### 3.2 Part

| # | From | To | Caused by |
|---|---|---|---|
| P1 | `unsent` | `inflight` | A1 (generation + 1) |
| P2 | `inflight` | `confirmed` | A2 |
| P3 | `inflight` | `refused` | A3 |
| P4 | `inflight` | `unsent` | A4: the server rejected it before accepting anything; or A11, an ack's retry |
| P5 | `inflight` | `uncertain` | A5, A8 or A8c |
| P6 | `uncertain` | `confirmed`, msgid set | A6 |
| P7 | `uncertain` | `unsent` | A7: absence proved |
| P8 | `confirmed` | `confirmed`, msgid set | A9 |

`confirmed` and `refused` are final. A post's part returns to `unsent` only by
P4 (the server's own rejection) or P7 (proved absence).

### 3.3 Attempt

| # | From | To | Actor | Guard | Durable write (one `TX`) | Network outside the `TX` |
|---|---|---|---|---|---|---|
| A1 | (none) | `writing` | The entry's owner: P for a `direct` entry, B holding `L_flush` for an `outbox` one | The entry is `open` and the part `unsent`. No attempt of this part is `writing` or `ended`. Every earlier part is `confirmed`. | The attempt (writer, `conn_id`, generation, text key, `written_at = now`), and the part becomes `inflight`. | **After** the commit: the part's lines, then `PING :round`. |
| A2 | `writing` | `confirmed` | writer | owner; the state and generation match | `final_at` = when the PONG arrived; the part becomes `confirmed`. | Before: the PONG arrived, within `PART_WAIT` or the drain, with no FAIL and no 4xx/5xx reply. Then the connection may carry the next part, unless the drain was used: then the call stops, and hands any remaining parts off as incomplete. |
| A3 | `writing` | `refused` | writer | as A2 | The part becomes `refused`, and E6 runs in the same `TX`. | Before: a FAIL was seen. |
| A4 | `writing` | `rejected` | writer | as A2 | The part goes back to `unsent`. | Before: 403, 404 or 482 with no FAIL. The existing single channel recreation on 403 then repeats A1. |
| A5 | `writing` | `ended` (`closed`) | writer | as A2 | `ended_at`, and `final_at` if a PONG with another 4xx/5xx reply arrived; else `final_at` is NULL. The part becomes `uncertain`. | Before: the connection was **closed first** (section 5.1). |
| A6 | `ended` | `delivered` | B | R1, inside its `TX` | An attribution (msgid UNIQUE, `attempt_id` UNIQUE); the part becomes `confirmed` with that msgid. | none |
| A7 | `ended` | `absent` | B | R2, inside its `TX` | The part goes back to `unsent`. | none |
| A8 | `writing` | `ended` (`writer_gone`) | B | **gone** | `ended_at` = when the writer was observed gone; `final_at` NULL; the part becomes `uncertain`. | none |
| A8c | `writing` | `ended` (`connection_closed`) | B | **conn-closed** | `ended_at` = when the closure was observed; `final_at` NULL; the part becomes `uncertain`. | none |
| A9 | `confirmed` | `confirmed` + attribution | B | R1, inside its `TX` | An attribution. | none |
| A10 | `ended` | `dead` | B | E7 | — | none |
| A11 | `ended` (an ack) | `ended`, part `unsent` | P owner, or B | The pseudo-part is an ack | The part goes back to `unsent`. | none. Acks are retried as today (section 8). |

`delivered`, `absent`, `refused`, `rejected` and `dead` are final. A `dead`
attempt remains evidence (section 7).

### 3.4 Evidence and coverage

| # | Change | Actor | Guard | Durable write | Notes |
|---|---|---|---|---|---|
| V1 | Index a message | B, on recording a verified PRIVMSG or multiline message, live or replayed | `TX` | `INSERT OR IGNORE` into `messages`, by msgid. In the same `TX`, the designation check of section 7.4. | The channel's checkpoint and coverage `mark` advance past a message only after this commits. |
| V2 | Coverage begins | B, when a catch-up finishes or a new channel is joined | `TX` | `since` and `mark`. `since` carries over only when the catch-up started from the stored `mark` (as in PR #20). | none |
| V3 | Coverage advances | B, on the PONG for the sync PING sent before each flush | `TX`; the channel is live on this connection; every earlier V1 for it committed | `through = max(through, ping_sent_at)` | none |
| V4 | Coverage stops | B, on KICK or PART, a disconnect, a stale catch-up reply, or a failed V1 | in memory | The channel leaves the live set; the checkpoint stays before the failed message, so the catch-up replays it. | none |

### 3.5 Import, export and collection

| # | Change | Actor | Guard | Durable write or effect |
|---|---|---|---|---|
| I1 | Claim | B | `L_flush` + `L_outbox` | Rename `outbox.jsonl` to `outbox.claimed-<ns>-<random>.jsonl`, never onto a name that exists. |
| I2 | Import | B | `L_flush` + `L_outbox`; `TX` | The occurrences, held rows, handoffs (H1) and the `imports` row, in **one** `TX` (section 6). |
| I3 | Unlink | B | after I2 commits | Unlink the claim file. |
| X1 | Export a dead letter | P or B | `L_outbox`; a `TX` reads and later sets the mark | Section 3.6. |
| X2 | Export an alert | B only, through its live `Deliveries` object, which alone writes `pending.json` | `L_flush`; `TX` for the mark | Section 3.6. |
| G1 | Collect evidence | B | `L_flush`; `TX` | The deletions I-7 allows. |

### 3.6 The export protocol

**X1, the dead letter.** The exporter holds `L_outbox`, so P and B are
serialized:

1. In a `TX`, read the entry. If `dead_letter_exported` is set, stop.
2. If `dead-letters.jsonl` does not end in a newline, append one. A torn
   last line then becomes an unreadable line, which readers already skip.
3. If a parseable line with this entry id already exists, skip to step 5.
4. Otherwise append the record. It holds:
   - the entry, with every part's text and state;
   - every attempt's state, write time, `final_at` and msgid;
   - the terminal reason.

   Then `fsync` the file, and the directory if the file was just created.
5. In a `TX`, set `dead_letter_exported`.

A crash at any step repeats the steps, and the record is written once.

**X2, the alert.** Only B runs this, because the bridge's `Deliveries` object
rewrites `pending.json` from memory:

1. If `alert_exported` is set, stop.
2. If a pending item, or a ledger line, already carries the alert key
   `outbox:<entry id>`, skip to step 4.
3. Otherwise add the content-free `push` item under that key. Write
   `pending.json` durably: write the temporary file, `fsync` it, rename it
   over `pending.json`, and `fsync` the directory.
4. In a `TX`, set `alert_exported`.

The alert is therefore enqueued exactly once. Its delivery is then
at-least-once, under the existing delivery rules.

The alert text names the account and channel only. For example: "an outbox
post from alp-solver-2 to #alpha could not be confirmed for 24 h;
dead-lettered".

## 4. Invariants and proofs

### I-1. Intent before bytes

Every transport caller commits a `writing` attempt (A1) before any byte of a
part or an ack is sent. The attempt names its writer, its connection and its
write time.

**Why it holds.**

- Posting and acknowledging both go through the attempt protocol:
  `post()` for parts, and the ack path for its pseudo-part. Today's separate
  `chatlib.ack` send gains the same E1 and A1 steps.
- A1 is the only way to obtain an attempt, and its writer sends only after the
  commit.

**Consequence.** Any message an attempt produces carries a time of at least
`written_at − A` (C-1).

### I-2. Fencing

Only an attempt's own writer moves it out of `writing`, except that B may do
so (A8 or A8c) once the writer is proven gone or the attempt's connection is
proven closed. No attempt is created for a part that has a `writing` or
`ended` attempt.

**Why it holds.** A2–A5 require the owner. A8 and A8c require their proofs.
A1's guard does the rest.

A writer paused after A1 still holds an open socket. It is not gone, and its
connection is not closed, so no one ends it or retries it. E7 can make its
entry terminal after 24 hours, but leaves its attempt `writing`.

Checks before sending, and database leases, cannot close the gap between a
check and the send. The fence therefore relies only on two facts:

- a closed socket cannot send;
- a dead process cannot send.

### I-3. Confirmed parts are never resent

**Why it holds.**

- A1 requires `unsent`.
- A post's part becomes `unsent` again only by P4 (the server's rejection:
  nothing accepted) or P7 (proved absence).
- `confirmed` is final.
- A part is fixed once and never re-split.

### I-4. No message satisfies two obligations

Attribution rows are the only way a message satisfies an obligation, and
`msgid` and `attempt_id` are each UNIQUE.

R1 attributes all members of a component at once, by one perfect matching in
one `TX`. R2 never attributes.

A confirmed attempt without a msgid also needs its own message, and both R1
and R2 count it. Attributed messages are excluded from every later candidate
set.

### I-5. Storage errors preserve outcomes

A storage failure never turns a written part into an `unsent` one, and never
discards a committed obligation.

**Why it holds.**

- If A1 fails, nothing of the part is written.
- If A2, A3, A4 or A5 fails, the writer closes the connection if it is still
  open, keeps the observed outcome in memory, and stops. That outcome may be
  confirmed, refused, rejected, or ended with or without finality. The writer
  retries the commit:
  - within the call, up to `PART_WAIT`;
  - after that:
    - the bridge retries at the start of each flush;
    - `pchat` and `agentcli` retry at each later `chatlib` call and at
      process exit.

  If the commit never lands, the attempt stays `writing`. A8 (writer gone) or
  A8c (connection closed, for example in a long-lived `agentcli` host) then
  ends it **without finality**: it is `uncertain`, R2 can never call it
  absent, and only R1 or E7 resolve it.

  A refusal whose A3 never committed is therefore never retried (#19 R7). It
  is dead-lettered after 24 hours (narrowing N6).
- If the E3 handoff fails, P appends a handoff row (section 6.4), which B
  applies by H1, guarded by the entry id and the writer identity.
- Nothing ever requeues a post's original whole text after a byte was written.
  That is round-5 finding 2.

### I-6. Ownership is visible before a competing flush can use a message

**Why it holds.**

- A direct post's A1 commits before its bytes are sent.
- The bridge can index the message (V1) only after the server publishes it.
- Reconciliation's `TX` starts after V1, so it sees that attempt as `writing`,
  or in a later state.
- R1 refuses a component with a `writing` member.
- R2's forcing argument cannot be broken by a member that only adds a
  message (section 7.3).

That is round-5 finding 3.

### I-7. Evidence is collected only when nothing depends on it

A row may be deleted only when every condition below holds.

1. **Messages.** No attempt of the same account and channel is `writing` or
   `ended`, or is `dead` with an open window, whose window contains the
   message's time. This covers both exact and fragment dependencies, because
   fragments are always from the same account and channel. The message's time
   is also before `now − A − GC_MARGIN`.
2. **Attributions.** The row is deleted only in the same `TX` as its message
   row, so a message never loses its attribution while it can still be a
   candidate.
3. **Attempts and parts:**
   - their entry is `done`, `terminal` or `abandoned`;
   - a terminal entry's exports are marked done;
   - no attempt of the same account and channel that is `writing`, `ended`,
     or `dead` with an open window has a window overlapping theirs;
   - their window has ended before `now − A − GC_MARGIN`.

   A `dead` attempt with an open window is never collected.
4. **Coverage** spans and `imports` rows are never collected.

Any later attempt has `written_at ≥ now`, so its window starts at or after
`now − A`, and it cannot depend on a collected row. A 26-hour outage therefore
removes nothing that an unresolved attempt needs. That is round-5 finding 4,
and the round-1 cross-key and export findings.

### I-8. Decisions read current state

Every verdict is computed and applied inside one `TX`, from the current
attempts, attributions, messages, coverage and designations. There is no
snapshot taken at the start of a flush. That is round-5 finding 1.

### I-9. Import keeps occurrences and is idempotent

See section 6.3.

### I-10. Independent flow

An entry interacts with others only through same-account, same-channel
evidence (section 7). An undecided, failing or refused entry never stops the
flush from attempting every other open outbox entry. Each entry's work is
isolated: its failure is logged, and the flush moves on.

Each attempt's wait is bounded by `PART_WAIT + DRAIN_WAIT`.

### How each round-5 finding is closed

| Round-5 finding at `e658bb8` | Closed by |
|---|---|
| 1. Satisfied keys are loaded once per flush (`chat-bridge:1104-1107`). | I-8: one `TX` per component, with no satisfied-key snapshot. |
| 2. A `reserve()` failure escapes without parts, and the whole text is requeued (`chatlib.py:517`, `539-546`). | I-5: A2 is the confirmation record, a failure keeps the outcome or leaves the part uncertain, and nothing requeues the whole text. |
| 3. The reservation is written after the server commit (`chatlib.py:516`). | I-1 and I-6. |
| 4. Reservations are pruned by wall-clock time (`chat-bridge:947`). | I-7: collection by dependency. |

## 5. Connections, finality, assumptions and crashes

### 5.1 Connections

- **One flow per connection.** A connection belongs to the one flow that
  opened it, inside one `post()` or ack call, and no other code path sends on
  it. It carries at most one unresolved attempt at a time.
- **Close before `ended`.** The writer closes the socket (shutdown, then
  close), and only then commits A5.
- **After closing.** The process cannot send on that socket again. Bytes
  already in the kernel's buffer may still reach the server, so an attempt
  without finality has an open window (section 7.1).

### 5.2 Finality: what proves an attempt can produce nothing more

The proof uses the transport boundary PR #20 already relies on to confirm a
part. The server processes one client's lines in order, and answers the PING
sent after a part only once it has processed the part's lines (C-3). So when
the PONG to `PING :round` arrives at time `f`:

- every line of the part has been processed;
- any message they produced was published before the PONG, with a time of at
  most `f + A`;
- nothing published later can come from that attempt.

That is **finality**.

**How the writer obtains it:**

- **Within `PART_WAIT`:** the PONG gives A2, A3, A4, or A5 with finality when
  another 4xx/5xx reply came with it.
- **After `PART_WAIT`:** the writer stops writing, and keeps reading the same
  connection for up to `DRAIN_WAIT`:
  - a PONG that arrives then gives the same outcomes, with finality, and the
    call stops as incomplete, because the deadline was missed;
  - if no PONG arrives, the writer closes, and A5 commits without finality.

  A FAIL is a refusal whenever it arrives (as in PR #20).

**Without finality:**

- a write error;
- a broken connection;
- no PONG through the drain;
- a gone writer (A8);
- a closed connection (A8c).

The attempt then has no proved end of publication, and **R2 never applies**.

**How automatic absence recovery works.** An attempt with finality and no
message in its closed window, with complete coverage over it and no other
candidate (R2), is proved absent and resent, by its owner or by a live
bridge. The cases where this applies:

- a part whose PONG came back with an error reply;
- a part whose PONG came only during the drain.

A part without finality is decided only by R1, under designation, or
dead-lettered after 24 hours. This is narrowing N7.

### 5.3 Assumptions

Anything these do not establish stays undecided.

- **C-1, clocks.** The writer, the bridge and the chat server run on one
  machine. The server's message time and the writer's wall clock differ by at
  most `A`, and the clock does not step by more than `A` during a window.
- **C-3, in-order processing and replies.** This is the boundary PR #20
  already uses.
  - The server processes one connection's lines in order.
  - A PONG for a PING sent after a part follows the processing of the
    part's lines.
  - A PONG with no FAIL and no 4xx/5xx reply means the part was published as
    exactly one message, of its exact text.
  - A FAIL, or a 403, 404 or 482 reply with no FAIL, means nothing of the
    part was published.
- **C-4, partial publication: not assumed.** This note does not assume that
  a multi-line batch is published whole or not at all. Any text a partial
  publication could show passes the fragment test, so fragment candidates
  block R2 (section 7.3) and are excluded from R1.

  A single line is published whole or not at all: IRC processes complete
  lines only. A single-line part therefore has no fragments other than its
  exact text.
- **C-5, a complete record.** Coverage `[since, through]` for a channel means
  every verified message the server published there with a time in that
  interval is in `messages` (V1–V4; PR #20's coverage model).
- **C-6, identity.** The `account` tag is the server-verified sender, and only
  verified messages are evidence.

  Untracked producers are possible, and are not assumed away. They include:
  - the old live tools before activation;
  - a person using an agent's account;
  - another client.

  Section 7 shows how each rule treats them.
- **C-7, designated accounts.** This is the owner's attestation, and applies
  only to accounts the owner designates. From `designated_at` on, every
  message of a designated account is produced by a tracked attempt.

  R1 uses it, and nothing else does. It is monitored, and suspended on the
  first message the record can show is unexplained (section 7.4). It cannot
  be proved, so it is never assumed for an account the owner has not
  designated.

### 5.4 Gone writers and closed connections

**A writer is gone** when either holds:

- the boot id differs from the recorded one;
- no process with the recorded pid exists, or that pid's process has a
  different start time.

**A connection is closed** when the writer's process exists with the
recorded start time, but holds no TCP socket with the recorded local port to
the recorded server address. The bridge reads the process's open sockets with
the operating system's process tools.

This lets B end the attempt of a closed flow inside a long-lived host, such as
`agentcli`, without the host having to exit. A reused local port can only make
a closed connection look open, which is the safe direction.

If either probe cannot be read, nothing is proved, and the attempt stays
`writing`. A paused writer still holds its socket open, so neither proof
applies to it.

### 5.5 A crash at every boundary

Each transition and external step is listed with the state that survives a
crash just before it (or before its commit) and just after it. "→" names the
recovery.

#### Writer side (P, or B as a flusher)

| Boundary | Crash just before | Crash just after |
|---|---|---|
| E1 | nothing durable, nothing sent | `open`, `direct`, all parts `unsent` → gone → E8, nothing unknown, retired as today |
| A1 | the previous state | `writing` → A8 → `uncertain`, no finality → R1, or E7 |
| sending lines, PING (external) | `writing` → A8 | same |
| `PART_WAIT`, drain (external) | `writing` → A8 | same |
| close (external) | `writing` → A8 | `writing` → A8 (B), or A8c while the host lives |
| A2 | `writing` → A8 → uncertain, never `unsent` | `confirmed` |
| A3 + E6 | `writing` → A8 → uncertain, never retried → E7 | `terminal` → X1 by P's retry or B's next flush |
| A4 | `writing` → A8 → uncertain → E7 (narrowing N6) | `unsent` → retried by the owner, or E8 for a gone direct owner |
| A5 | `writing` → A8, no finality | `ended`, with `final_at` as observed |
| A11 (ack) | `ended` ack → A11 next time | `unsent` ack → retried |
| E3 | `direct` → gone → E8, and the handoff row if one was written → H1 | `outbox` |
| handoff row append (external) | E8 → the remainder is not adopted, as for any crash before queueing today | H1 at the next import |
| E5 | all parts confirmed, `open` → B sets `done` | `done` |

#### Bridge side

| Boundary | Crash just before | Crash just after |
|---|---|---|
| E4 | no parts → E4 again | parts `unsent` → A1 |
| A8, A8c | `writing` → again next flush | `ended` |
| R1, R2 `TX` | nothing changed → again | delivered or absent, atomically |
| E7 | as before → again | `terminal` → X1, X2 |
| E8 | as before | `abandoned` |
| H1 (inside I2) | rolled back with I2 | applied with I2 |
| I1 rename | `outbox.jsonl` intact | the claim file → I2 |
| I2 | no rows, no `imports` row → the same occurrence ids again | rows and `imports` row → I3 |
| I3 unlink | `imports` hash matches → unlink only | done |
| V1 | not indexed; checkpoint not advanced → replayed, `INSERT OR IGNORE` | indexed |
| the checkpoint file write | the stored `mark` and the checkpoint disagree → V2 starts a new span (safe) | consistent |
| V2, V3 | the old span | the new span |
| a bridge restart | the live set is empty → coverage advances only after a catch-up | — |
| X1 steps 2–5 | repeated → written once (section 3.6) | — |
| X2 steps 2–4 | repeated → enqueued once | — |
| G1 | rolled back | the allowed deletions |

### 5.6 Interleavings

1. **A direct post and a flush (round-5 finding 3).** `alp-solver-2` has an
   uncertain outbox part U with the text "[status] build green". U ended with
   finality, and never published. The account then posts the same text
   directly as D:
   - D's A1 commits, the server publishes D's message `m`, and B indexes `m`;
   - B's flush runs while D still waits for its PONG.

   R1 refuses, because D is a `writing` member. R2 cannot rule U absent,
   because `m` is not forced to a confirmed attempt. U stays undecided.

   After D's A2, the component is {U, D}, with `m` only. `m` is forced to D,
   and R2 rules U absent once coverage covers U's window. U is resent. `m`
   never counted for both.
2. **Two flushers.** `L_flush` makes the second skip. A1's guard would allow
   only one attempt per part anyway.
3. **A paused writer.** P stops after A1. No one ends or retries its attempt.
   After 24 hours E7 makes the entry terminal, with an alert, and leaves the
   attempt `writing`.

   If P resumes, its message falls inside its open window, and its own
   commit records the outcome. A late confirmation does not reopen the
   terminal entry, which keeps its dead letter.
4. **A hung bridge.** A second bridge skips the flush while the first holds
   `L_flush`. When the first exits, A8 applies.
5. **Handoff versus abandonment.** P's E3 fails, P appends the handoff row
   and exits, and B commits E8 before importing that row. The import's H1
   then reopens the entry as `outbox`, because the row's entry id and writer
   identity match. An unrelated killed post never wrote such a row, so it is
   never adopted.
6. **`pchat status` during a flush.** It reads one snapshot.
7. **P exports while B does.** `L_outbox` serializes X1, and the second
   exporter sees the mark set.

## 6. Importing the old outbox

### 6.1 A new rule: frozen snapshots

Today `_Claimed.save()` (`chat-bridge:1030-1036`) rewrites claimed files, so a
claimed file is **not** immutable. Immutability is a new rule:

- **The cutover marker.** A `meta` row, written when the schema is created.
  Once it exists, no code path writes a claimed file: `_Claimed.save()` and
  its callers are removed.
- **Old writers.** Quiescing the old writers (the running bridge, and the
  old tools' flush) happens **only** at the separately authorized future
  activation, before the first import of live state. Until then, plateia's
  copy touches no live state.

A claim file that old code may have rewritten is imported once, as it stands
under the locks.

### 6.2 Supported formats and the hold policy

Import supports exactly the formats the running tools write:

- a text post row: `channel`, `as`, `text`, and optionally `cont`,
  `reply_to` and `at`;
- an ack row: `channel`, `as`, `ack` and `at`;
- the new handoff row (section 6.4), which only this design writes.

**Anything else is held, never imported.** That includes:

- a row with `parts`, `terminal` or a non-handoff `id`. Those are PR #20
  formats, which never ran on live state;
- an unreadable line.

A held row is kept raw in `held`, reported in `pchat status`, and alerted
once, with no content.

**PR #20's side files** (`outbox-reserved.jsonl`, `outbox-attributed.jsonl`,
`record-coverage.json`) are never imported. If any exists at cutover, it is
left untouched and reported.

None of that state ever existed live, so no imported obligation can be
reconciled against evidence that was not migrated. Coverage starts fresh,
from the first catch-up after cutover.

**Legacy text rows carry no attempt history.** Whatever the old tools may
already have published of them is outside this design's evidence. They are
delivered as #19 R3 requires ("delivered as today").

### 6.3 The fenced import

Import runs only in B's flush, holding both `L_flush` and `L_outbox`, and does
local work only.

1. **Claim (I1).** Rename a non-empty `outbox.jsonl` to a new unique name.
   List every `outbox.claimed-*.jsonl`.
2. **Read** each claim file whole, with its sha256 and line count.
3. **Check** for an `imports` row with the file's name:
   - the same hash: go to step 6;
   - a different hash: hold the **file** (one `held` row), alert once, and do
     not import or unlink it.
4. **Import (I2), in one `TX`.** For each line ordinal `i`:
   - **The occurrence id is always derived** from (claim file name, `i`),
     for example `sha256(name + ":" + i)`. It is UNIQUE. Explicit ids never
     replace it. Two identical lines, with or without the same explicit id,
     are therefore two occurrences.
   - **A text or ack row** becomes an `open`, `outbox` entry (E2). A post
     gets its parts at E4.
   - **A handoff row** is an instruction, not an obligation.
     - If its referenced entry exists, apply H1 when its guard holds;
       otherwise hold the row.
     - If no such entry exists, E1 never committed, and nothing was sent.
       The row becomes a legacy occurrence carrying the reference as its
       UNIQUE `handoff_ref`.
     - A second row with the same reference is a no-op once H1 has applied.
       Without an entry, it is held.
   - **Any other row** is held.
   - Insert the `imports` row (name, line count, sha256).
5. Commit.
6. **Unlink (I3)** the file, only after the commit.

### 6.4 The handoff fallback

If E3 cannot commit, P appends one row under `L_outbox`. It holds the post's
legacy fields, `handoff: true`, the entry id, and P's writer identity.

If that append fails too, P fails as it does today when the outbox cannot be
written. Whatever already committed still prevents a blind resend.

### 6.5 Why the import is idempotent and keeps occurrences

- **A crash before the commit** leaves no rows. The next import derives the
  same occurrence ids from the same name and ordinals.
- **A crash after the commit** leaves an `imports` row whose hash matches, so
  only the unlink remains.
- **A changed hash** is held, never merged.
- **Every line** is its own occurrence, so a duplicate is never dropped as a
  replay.
- **Claim names** never collide.

## 7. Reconciliation rules

### 7.1 Windows

| Attempt | Window |
|---|---|
| `writing` | `[written_at − A, +∞)`, open |
| `confirmed`, no msgid | `[written_at − A, final_at + A]`, closed |
| `ended` or `dead` with finality | `[written_at − A, final_at + A]`, closed |
| `ended` or `dead` without finality | `[written_at − A, +∞)`, open |

A closed window never changes.

### 7.2 Components

For a text key *k*, the **members** are its attempts that are:

- `writing`, `ended` or `dead`;
- or `confirmed` without a msgid.

Members whose windows overlap are connected, and a **component** is a
connected set of members.

- **`K`**: confirmed members. Each published exactly one message, of its
  exact text, inside its window (C-3).
- **`G`**: `ended` members. Each published at most one message of its exact
  text. A multi-line member without finality may also have published
  fragments.
- **`D`**: `dead` members.
- **`W`**: `writing` members.
- **`M`**: the unattributed messages of key *k* whose time lies in some
  member's window.

### 7.3 Verdicts

Each verdict is decided per component, inside one `TX`.

#### R1, DELIVERED (the whole component at once)

All of these must hold:

1. The account is designated, with `designated_at` no later than the
   component's earliest window start, and it is not suspended.
2. `W` and `D` are empty.
3. `|M| = |K| + |G|`, and a perfect matching of `K ∪ G` onto `M` respects
   every window.
4. There is no fragment hazard. No unconfirmed multi-line attempt X of the
   same account and channel, from another text key, has a window containing a
   message of `M` whose text passes the fragment test against X's text.
   "Unconfirmed" means `writing`, `ended` or `dead`.

Then every `G` member becomes `delivered` (A6), and every `K` member gets its
matched msgid (A9).

**Argument.** By C-7, every message in `M` came from a tracked attempt. By
condition 4 and the fragment test, none is a fragment of another key's
multi-line attempt. So each came from an attempt of key *k* whose window
contains it, which by definition is a member.

Each member published at most one message of text *k*, and `K` published
exactly `|K|`. So `|M| = |K| + |G|` means every member published exactly one,
and all of them are in `M`. No member can publish another one later, even one
with an open window.

All members are attributed together, so any matching is a valid assignment.
Any attempt created later sends after its A1, which is after these messages
were indexed (I-1), so it cannot be their producer.

`W` must be empty because B may not decide a live writer's attempt. `D` must
be empty because a dead member's outcome is never decided.

Without designation, R1 never fires. A single identical message from an
untracked producer therefore cannot retire an unpublished part. That is the
round-1 finding, and the reason for difference D2.

#### R2, ABSENT (one `ended` member `Y`)

All of these must hold:

1. `Y` has finality: its window is closed (section 5.2).
2. Coverage is complete over the hull of `Y`'s window and the windows of `K_c`.
   `K_c` is the confirmed members connected to `Y` through closed windows.
3. `K_c` can be fully matched into `M`. If not, the record contradicts a
   confirmation: the verdict is undecided, and an anomaly is logged.
4. **Every** message of `M` inside `Y`'s window is forced: without it, `K_c`
   cannot be fully matched.
5. No unattributed message of the same account and channel inside `Y`'s
   window passes the fragment test against `Y`'s text.

Then `Y` becomes `absent` (A7). Its part becomes `unsent`, for its owner's
next attempt.

**Argument.** Suppose `Y` published something:

- either its exact message `m_Y`;
- or, if it is multi-line, a fragment `g`.

**It is in `messages`.** By finality and C-1, its time lies in `Y`'s closed
window. By C-5, it is indexed.

**It is unattributed.**

- Attributions are made only by R1, and `Y` existed before anything it
  published (I-1).
- An R1 whose `M` held `m_Y` would have had `Y` as a member, because `m_Y` is
  in `Y`'s window. It would then have decided `Y`, but `Y` is still `ended`.
- An R1 whose `M` held `g` would have failed its fragment condition, because
  `Y` was unconfirmed.

**So the rule fails.**

- If `Y` published `m_Y`: `K_c`'s own messages are distinct from `m_Y` and in
  the record, so `K_c` can be matched without `m_Y`. `m_Y` is not forced, and
  condition 4 fails.
- If `Y` published `g`: condition 5 fails.

The argument needs no assumption about untracked producers. An extra message
is never forced, so it only blocks absence. `W`, `G` and `D` members, other
than `Y`, can only add messages, never remove one of `K_c`'s.

#### R3, UNDECIDED: everything else

The part stays `uncertain`, is shown in `pchat status` as awaiting a delivery
check, and is checked again each flush.

E7 applies at `UNDECIDED_MAX`, to any entry:

- direct, outbox or abandoned;
- with a `writing` or `ended` attempt;
- live writer or not.

It dead-letters the entry (X1) with its unconfirmed part texts and per-part
records, and raises the content-free alert (X2). The part is never resent.

### 7.4 Conservatism, in brief

- **Identity.** Same verified account, same case-folded channel, exact
  server-visible text, a time inside the window, an msgid not yet attributed.
- **Competing and untracked producers.**
  - R2 never relies on their absence.
  - R1 relies on it only for a designated account (C-7).
  - At V1, a message of a designated account with a time after
    `designated_at` is *unexplained* when no tracked attempt can explain it:
    no attempt of its exact text key has a window containing it, and no
    unconfirmed multi-line attempt of that account and channel has a window
    containing it and a text it passes the fragment test against.
  - An unexplained message suspends the designation in the same `TX`, with
    a content-free alert (X2). R1 is then off for that account until the
    owner lifts the suspension.

  An identical message from an untracked producer, inside a tracked window, is
  indistinguishable from ours. C-7 covers it only where the owner has
  designated the account. Elsewhere it leaves the part UNKNOWN.
- **Complete joined-membership coverage.** C-5. Absence needs coverage
  through `final_at + A`, after the attempt has ended.
- **Automatic absence recovery is kept** where finality proves it (section
  5.2). This uses no tags, no echo, no labeled responses, and no CHATHISTORY
  capability beyond PR #20's.
- **Ambiguity stays UNKNOWN.** That includes:
  - indistinguishable identical text;
  - a fragment candidate;
  - missing finality;
  - a live writer;
  - incomplete coverage.

  Each is visible, never resent, and dead-lettered with its text after 24
  hours.
- **Unrelated obligations keep flowing** (I-10).
- **A 26-hour outage keeps the evidence** (I-7). Coverage across an outage
  exists only if the catch-up resumed from the stored mark.
- **No protocol experiments.**

### 7.5 Worked example

`alp-solver-2` posts a three-part question to `#alpha`. Parts 0 and 1 are
confirmed (A2).

- **Case 1, a late PONG.** Part 2's PONG comes during the drain, with no
  error. The outcome is A2, confirmed. No part remains, so the post has
  succeeded and the entry is `done` (E5). Nothing is queued or resent. Had a
  part remained, the call would have stopped there and handed it off.
- **Case 2, an error reply.** Part 2's PONG comes with a 5xx reply. The
  outcome is A5 with finality, and the entry is handed off.
  - If coverage reaches `final_at + 5 s` with no message and no fragment, R2
    holds: part 2 is absent and is resent once.
  - If its message is there instead, the part is delivered by R1 when the
    account is designated. Otherwise it is undecided, and dead-lettered at 24
    hours.
- **Case 3, a broken connection.** The connection breaks: A5, no finality.
  - Its message is logged, the account is designated, and R1 holds: it is
    delivered.
  - Otherwise it is undecided, and at 24 hours dead-lettered with part 2's
    text, with `pat` alerted. It is never resent.

## 8. Mapping to #19, and proposed differences

### 8.1 Mapping

| #19 | Where it is met |
|---|---|
| R1, the per-part outcome | A2, A5 and A4, and the part states. `PART_WAIT` is absolute; the drain is bounded. |
| R2, no resend of a confirmed part | I-3; E3 and H1 hand off the remainder only. |
| R3, fixed parts, and legacy entries delivered as today | E1 and E4; section 6.2. |
| R4, durable progress | A1 before bytes, A2 or A5 before the next part, A8 and A8c; I-5. |
| R5, reconciliation | R1, R2, R3, E7, X1 and X2, subject to D2 and N7. |
| R6, independent flow | I-10 |
| R7, refusals | A3 and E6, with the confirmed parts in the dead letter. N6 covers an unrecorded refusal. |
| R8, visibility | `pchat status`, from the database: waiting to post, awaiting a check, held rows, suspended designations. |
| R9, no exactly-once | Stated here; the guidance and provenance keep saying it. |
| R10–R12, capture, guidance, docs | Unchanged; the implementation updates them. |
| R13, privacy | Invented data only. |
| Acceptance 1, 2, 4–6, 8–10 | The behavioral assertions are unchanged. Acceptance 2 needs the fake account designated (D2). |
| Acceptance 3 | Changed fixture (AD-2). |
| Acceptance 7 | Changed fixture (AD-1). |

### 8.2 Proposed differences: none is approved by this note

- **D1, adopting a dead direct writer's remainder: not adopted.** A killed
  `pchat` queues nothing today. E8 keeps that: the unsent remainder is not
  sent.

  Its unknown parts still follow #19 R5's 24-hour dead letter, through E7.
  Adoption needs an owner decision.
- **D2, Delivered needs a designated account.** Approval item 5 says
  unexplained identical text stays UNKNOWN. Text evidence cannot exclude an
  untracked producer, so R1 fires only for owner-designated accounts (C-7),
  and is monitored by the suspension rule.

  #19 R5's "Delivered" carries no such condition. This is therefore a
  narrowing of #19 R5, and needs the owner's decision.
  - **If the owner designates the transport accounts at activation**,
    delivery behaves as #19 R5 under an attested, monitored assumption.
  - **If not**, uncertain parts with a visible message are dead-lettered
    after 24 hours instead of retired.
- **AD-1, acceptance 7's fixture.** "The left-over claimed file is recovered"
  becomes "the database state left by the crash is recovered". Its assertions
  stay the same: no earlier part rewritten, part *k* uncertain and not
  resent, no entry lost.
- **AD-2, acceptance 3's fixture.** Absence needs finality (N7), so the test's
  uncertain part 3 must end with a server reply, an error reply or a late
  PONG, rather than a bare timeout. Its assertions stay the same.

### 8.3 Narrowings inside #19's UNKNOWN outcome

These are listed for awareness. Each one ends as UNKNOWN, never as a resend.

- **N1.** R1 needs an exact count, with no live writer and no dead member.
- **N2.** A fragment candidate blocks R2 and R1.
- **N4.** A timed-out call returns up to `DRAIN_WAIT` later.
- **N5.** A `dead` member, or one without finality, keeps an open window, so
  later same-key parts of that account and channel may stay UNKNOWN until 24
  hours.
- **N6.** A refusal or rejection whose commit failed becomes UNKNOWN, and is
  dead-lettered after 24 hours rather than at once.
- **N7.** Absence needs finality. A part ended by a crash, a broken
  connection, or no reply through the drain is never ruled absent.

**Acknowledgements are not text obligations.** They gain E1 and A1 before
their bytes, and are retried after an uncertain outcome exactly as today
(A11). A repeated acknowledgement is harmless. This is not an exemption from
approval item 1: their intent is still committed before any byte.

**Owner decision at implementation.** Adopting SQLite as the outbox store of
plateia's copy, to be recorded as a design decision next to D-72. Migrating
live state, quiescing the old writers, and any designation belong to the
separately authorized activation.

## 9. Future tests

These tests are to be written later. None is written or run as part of this
note.

**Setup.** `FakeServer`/`FakeConn`; injected wall and monotonic clocks; a fake
process and socket table for the gone and conn-closed proofs; `_isolation`
with a temporary state directory; invented data only. There is no network,
home directory or real data.

**Transports.** Every scenario runs on both transports, `line` and
`multiline`, wherever parts differ.

1. **The round-5 regressions.**
   1. **Same flush.** Entry A has a confirmed part and an uncertain part of the
      same text; entry B has the same text, delivered; the account is
      designated. One flush resolves both. Nothing is resent.
   2. **Storage failure at confirmation**, through `pchat`, `notify` and
      `announce`. A2 fails after part 1 of 3 is confirmed. Nothing is
      requeued whole. Part 1 is never rewritten. The outcome is retried, or
      ends uncertain.
   3. **A direct post during a flush.** As in section 5.6, item 1.
   4. **A 26-hour outage.** The evidence survives, and U is not retired as
      delivered.
2. **Untracked substitution.**
   - Undesignated: U published nothing, an identical untracked message falls
     in U's window, and U stays UNKNOWN until its 24-hour dead letter.
   - Designated: an unexplained message suspends the designation, and alerts
     once.
3. **Unique attribution.** A msgid is never attributed twice: identical part
   texts in one entry and across entries, the UNIQUE constraints, and a crash
   between two components' transactions.
4. **Finality.**
   - A PONG with an error reply gives finality, and R2 resends.
   - A late PONG in the drain gives A2.
   - No PONG, a write error, a gone writer and a closed connection each give
     no finality, and R2 never applies.
   - A FAIL in the drain is a refusal.
5. **Fragments.** For a multi-line part with lines "north" and "south", each
   of these blocks R2 and R1:
   - "north";
   - "south";
   - "south\nnorth";
   - "north" with a concatenated piece.

   An attributed fragment is never reused.
6. **Crash at every boundary.** One case per row of the tables in section
   5.5, for P and for B, asserting the surviving state and the listed
   recovery.
7. **Storage failure at each commit.**
   - At A1: nothing is sent.
   - At A2, A3, A4 and A5: the outcome is retried. A refusal is never
     retried.
   - At E3: the fallback row, then H1.
   - At E6 and E7, and at the X1 and X2 marks.
   - A live non-bridge host (`agentcli`): the closed flow is ended by A8c
     while the host keeps running.
8. **A paused writer and gone proofs.**
   - A writer paused after A1 is never ended or retried.
   - At 24 hours E7 makes it terminal and leaves the attempt `writing`.
   - A missing pid, a changed start time and a changed boot id each prove
     the writer gone.
   - Unreadable probes prove nothing.
9. **Import.**
   - Identical lines, with or without the same explicit id, give distinct
     occurrences.
   - A crash before the commit, and one after it, replay idempotently.
   - A changed hash is held.
   - Rows with `parts`, `terminal` or another id, and unreadable lines, are
     held.
   - A handoff applies once.
   - Handoff versus E8: adopted by H1.
   - Claim names never collide.
10. **Collection.**
    - Exact and fragment dependencies keep messages.
    - An attribution is deleted only with its message.
    - Terminal rows are kept until their exports are marked done.
    - Open-window dead attempts are kept.
11. **Exports.**
    - X1 writes once across a crash at each step, with a torn last line
      repaired.
    - P and B export concurrently: one record.
    - X2 enqueues exactly once across a crash at each step.
12. **Coverage.** Each of these stops coverage, and R2 waits:
    - KICK or PART;
    - a disconnect;
    - a stale catch-up reply;
    - a failed V1;
    - a checkpoint that disagrees with the stored mark;
    - a bridge restart.
13. **Independent flow.** Another account, and the same account with another
    text, are delivered in the same flush while an entry is undecided,
    refused or failing.
14. **Unchanged paths.** All of these keep #19's acceptance:
    - legacy and ack entries;
    - a failure before any write queues the whole post;
    - a slow but confirmed post;
    - silent-run suppression;
    - `pchat` exit codes and messages;
    - ack retry.

## 10. Feasibility and risks

**Feasibility.** Everything uses the standard library's `sqlite3`, plus
process and socket probes. The change keeps PR #20's:

- per-part posting;
- fixed parts;
- the refusal and legacy paths;
- the coverage model.

It replaces the bridge's and `chatlib`'s file state, about 300 lines, with
the database layer, and adds the drain and the probes.

**Risks.**

- **More dead letters.** D2 without designation, and N5–N7, will dead-letter
  more uncertain parts after 24 hours. Each one has an alert, and none is a
  duplicate.
- **Probe portability.** Process start time, boot id and open sockets are
  read differently on macOS and Linux. An unreadable probe proves nothing,
  so the cost is delay, never a resend.
- **Scope.** Every transport caller changes. The tests in section 9 guard it.
- **Activation.** Live import, quiescing the old writers and designation are
  outside this work, and separately authorized.

## 11. Revisions

**Revision 1** (`46c470c`) was reviewed in design review round 1:
CHANGES_REQUESTED. Revision 2 makes these changes, numbered by that review's
findings.

1. **Untracked substitution in R1.** R1 now requires an owner-designated
   account (C-7), monitored by suspension. Without designation it never
   fires. This is identified as difference D2.
2. **Unproved `SETTLE`.** `SETTLE` is removed. R2 requires finality, proved by
   the in-order reply on the attempt's own connection (section 5.2). The
   drain recovers late replies. Without finality, R2 never applies (N7).
3. **Fragments beyond prefixes.** Atomicity is no longer assumed. The fragment
   test covers every possible partial text. It blocks R2, and excludes R1.
4. **Collection.** I-7 now keys on account and channel windows, covering
   fragment dependencies. It keeps attributions with their messages, keeps
   terminal rows until their exports are done, and never collects open-window
   dead attempts.
5. **Explicit-id duplicates.** Occurrence identity is always (file, ordinal),
   and explicit ids never replace it.
6. **PR #20-format state.** It is never imported; it is held or left
   untouched and reported (section 6.2).
7. **A handoff stranded by abandonment.** H1 is a B transition guarded by the
   entry id and the writer identity, and may reopen an abandoned entry.
8. **Acks.** E1 and A1 now precede the TAGMSG. Ack retry is today's behavior
   (A11).
9. **Refusal storage errors and closed flows.** Pending outcomes are retried.
   An unrecorded refusal degrades to UNKNOWN (N6), never to a retry. A8c ends
   a closed flow inside a live host.
10. **Exports.** P and B are serialized; each write is made durable before
    its mark; a torn line is repaired; the alert goes on the pending queue,
    once.
11. **The N3 exemption.** E7 now applies to `writing` attempts too, without
    ending them. Acceptance 7's fixture change is named (AD-1), and so is
    acceptance 3's (AD-2).
12. **Crash matrix and tests.** Section 5.5 covers every boundary, and
    section 9 adds the round-1 counterexamples and both transports.
