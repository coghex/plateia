# Chat outbox state: one durable authority for partial-post delivery

This is the design note for #19 and pull request #20. It is design only:
nothing here is implemented, and implementing it needs a separate owner
decision.

This is revision 5. Revision 3 answered design review round 2. Revision 4
aligned the note with the owner's second amendment to #19:

- the release-packaging scope;
- the corrections and additions from the canonical issue review of the first
  amendment.

Revision 5 answers design review round 3: directory durability on every
recovery path, bounded lock acquisition, durable alert identities, the
baseline of the code it describes, and the fallback test. It also carries the
approving issue rereview's corrections.

Section 12 lists what changed and why. The owner's decisions of 2026-10-09 are
recorded in section 11, and in D-73 of [plateia_design.md](plateia_design.md).

## Background

PR #20 (head `e658bb8`) repairs a long post that was reposted on every outbox
flush. Its fifth review found four remaining defects. All four come from one
cause: at that head, outbox state is split across several files and
processes. (Master, `a12d99c`, has none of these files: it queues and reposts
whole posts, which is #19's defect.)

- Part progress lives in rewritten claimed outbox files.
- Confirmed parts whose msgid is unknown live in `outbox-reserved.jsonl`.
- Msgids already counted live in `outbox-attributed.jsonl`.
- Record completeness lives in `record-coverage.json`.

Several processes write that state, under different locks. The bridge's flush
reads it as start-of-flush snapshots, and prunes it by wall-clock age.

This note replaces the split state with one SQLite database:

- every transport caller records its intent there before sending any byte;
- reconciliation decides inside one transaction, from the current state;
- evidence is kept while anything depends on it.

The delivery contract is #19's, as amended by the owner on 2026-10-09
(section 11):

- confirmed parts are never resent;
- unsent parts stay durable;
- an uncertain part is resent only when its absence is proved.

Delivery is at-least-once with that check. Nothing claims exactly-once: no
transaction spans both the database and the chat server.

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
8. [Mapping to #19](#8-mapping-to-19)
9. [Future tests](#9-future-tests)
10. [Feasibility and risks](#10-feasibility-and-risks)
11. [Owner decisions (2026-10-09)](#11-owner-decisions-2026-10-09)
12. [Revisions](#12-revisions)

## 1. Terms and constants

| Name | Value | Meaning |
|---|---|---|
| `A` (`CLOCK_ALLOWANCE`) | 5 s | The allowed difference between the writer's wall clock and the server's message time. |
| `PART_WAIT` | 30 s | The absolute deadline for the server to confirm one part (as in PR #20). |
| `DRAIN_WAIT` | 30 s | After `PART_WAIT`, how much longer the writer keeps reading for the server's late in-order reply before closing (section 5.2). |
| `LOGIN_WAIT` | 30 s | The absolute deadline for connecting, capability negotiation, SASL and the welcome, from opening the socket (section 5.1). |
| `CHANNEL_WAIT` | 15 s | The absolute deadline for creating or joining a channel and seeing the bridge in it, after a 403 (section 5.1). |
| `UNDECIDED_MAX` | 24 h | How long a part may stay undecided, counted from its attempt's write. Then it is dead-lettered. |
| `GC_MARGIN` | 1 h | The extra age before evidence may be collected (I-7). |
| `LOCK_WAIT` | 5 s | The absolute deadline for the bridge, or a direct writer's optional export, to acquire `L_outbox` (section 2.3). |
| `BUSY_WAIT` | 5 s | SQLite's busy timeout: the most any transaction waits for the database's write lock (section 2.1, C-8). |

PR #20's `SETTLE` bound is **not used**: absence needs attempt-specific
finality (section 5.2), not a settling time.

- **Entry.** One logical post or acknowledgement that the transport owes. It
  is created by `pchat post`, `pchat ack`, `agentcli.notify` or `announce`,
  or by import (section 6).
- **Part.** A fixed piece of a post that the server publishes as one message:
  one `draft/multiline` batch or one line.
  - It has an index `n`, its *wire lines* (each a piece and a concat flag),
    and its exact *server-visible text*, which is what the bridge records.
  - A part is **multi-line** if its batch has more than one wire line.
  - An acknowledgement has one pseudo-part, the TAGMSG.
- **Attempt.** One try at sending one part over one connection, numbered by a
  per-part generation. Only an attempt can produce a message.
- **Text key.** (account, channel case-folded, exact text).
- **Writer.** The flow holding an attempt's connection. It is identified by
  the boot id, the pid, the process start time, and a connection id that
  includes the local TCP port and the server's address.
- **Finality.** The writer received, on that connection, the server's in-order
  reply to a line sent after the part: the PONG to `PING :round` (section
  5.2).
- **Message.** A verified message in the bridge's record: msgid, channel,
  account, exact text and server time.
- **Accounted for.** A message is accounted for, at a moment, when either:
  - it has an attribution row; or
  - it is **forced**: the confirmed attempts without a msgid of its own text
    key, connected through closed windows, cannot be fully matched to
    distinct messages without it, while coverage is complete over their
    hull.

  A message that is not accounted for is **unexplained** (section 7.2).

## 2. The authority

### 2.1 One database

All durable outbox state lives in one SQLite database, `outbox.db`, in the
chat state directory, using Python's standard `sqlite3`. It replaces:

- the progress inside claimed outbox files;
- `outbox-reserved.jsonl`;
- `outbox-attributed.jsonl`;
- `record-coverage.json`.

**Settings:**

- WAL journal mode;
- `synchronous=FULL`, so a commit survives a power loss. On macOS, where
  `fsync` alone does not reach the disk, also `fullfsync` and
  `checkpoint_fullfsync`;
- the busy timeout `BUSY_WAIT`;
- **the database's own files are durable too.** SQLite's unix VFS syncs the
  directory when it creates a journal or WAL file. The implementation checks
  that for the SQLite it ships with. Otherwise it makes a durable sync of
  `outbox.db` and `outbox.db-wal` itself after opening, before the first
  commit anything relies on;
- every write is a `BEGIN IMMEDIATE` transaction, so each decision is read
  and applied in one serialized transaction.

**No network I/O inside a transaction.** Logging in, sending, waiting,
draining and closing all happen between transactions.

**Not a second authority.** These are boundaries, not sources of truth:

- **Imports.** `outbox.jsonl` and `outbox.claimed-*.jsonl` (section 6).
- **Exports.**
  - `dead-letters.jsonl`;
  - the owner alert, which goes on the bridge's pending-delivery queue,
    `pending.json`. The ledger `deliveries.jsonl` records delivery outcomes.

  Each is written after its terminal state commits, by the protocol in
  section 3.6.
- **The chat record.** The channel logs stay the chat record that other
  readers use. For the outbox, the bridge also indexes verified messages into
  the database (V1), so evidence and coverage are ordered with outbox state.

**Durable file writes.** Every file write the proofs rely on is made durable
before anything depends on it.

- **Flush.** In this note, "`fsync`" means the platform's full flush to
  stable storage: `fcntl(F_FULLFSYNC)` on macOS, and `os.fsync` elsewhere.
- **Durable sync of a file.** `fsync` the file, then `fsync` its parent
  directory. That makes both the content and the directory entry durable,
  **whoever created the file and whenever**. No step relies on knowing that
  the file was "just created", or on an earlier process having synced the
  directory (F5, design round 3).
- **Append:** the line, then a durable sync of the file.
- **Replace:** write a temporary file, `fsync` it, rename it into place, then
  `fsync` the directory.
- **Rename or unlink:** the change, then `fsync` of the directory.
- **Recovery.** A record found already present, in any file, is durably
  synced (file and directory) before anything is acknowledged or marked on
  the strength of it. Seeing a record proves only that it is visible, not
  that it is durable.
- **The state directory.** The chat state directory exists before cutover. If
  this code ever creates it, the parent directory is synced too.

So no acknowledgement (`pchat` exit 3 for a queued post) and no export mark is
ever committed for a record whose content or directory entry could still be
lost to a power failure.

### 2.2 Tables

The columns listed are the minimum the proofs rely on.

| Table | Key | Holds |
|---|---|---|
| `meta` | name | The schema version, the cutover marker (section 6.1), and `import_epoch`, the number of completed import passes. |
| `accounts` | account | `designated_at`, `suspended_at`, why it was suspended, and `suspension_seq`, the number of suspensions so far (R1, section 7.4). |
| `alerts` | `alert_key` | One row per alert event: its kind (`undecided`, `held` or `suspended`), what it refers to, its content-free text, when it was raised, and `exported_at` (NULL until X2 completes). See section 3.6. |
| `entries` | `id` | See below. Entry rows are **never deleted** (I-7), so a missing id always means no entry was ever created. |
| `parts` | (`entry_id`, `n`) | The kind, the wire lines, the server-visible text, `multi_line`, `state` (`unsent`, `inflight`, `uncertain`, `confirmed` or `refused`), the current generation, and the msgid once attributed. |
| `attempts` | `id` | `entry_id`, `n`, `generation`, the text key, `multi_line`, the writer identity, `conn_id`, `state`, `written_at`, `final_at` (NULL without finality), `ended_at`, `end_kind` (`closed`, `writer_gone` or `connection_closed`), and a detail. UNIQUE (`entry_id`, `n`, `generation`). `state` is one of `writing`, `confirmed`, `refused`, `rejected`, `ended`, `delivered`, `absent`, `dead` or `retired` (an ack only). |
| `attributions` | `msgid` | `attempt_id` (UNIQUE), and when it was made. |
| `messages` | `msgid` | The channel, the account, the exact text and the server time, for verified messages only. |
| `coverage` | channel | `since`, `through` and `mark`, as PR #20 keeps them. |
| `imports` | claim file name | The line count, the content sha256, the epoch, and when it was imported. A name in this table is never used again. |
| `held` | (file name, ordinal), or the file name alone | The raw row, or a reference to the file, and why it was held. |

**What an entry row holds:**

- its kind (`post` or `ack`) and origin;
- the account, the channel, the original text, `cont`, `reply_to` and its
  written-at time;
- `owner_kind` (`direct` or `outbox`), and the owner's writer identity for a
  direct entry;
- `state`: `open`, `done`, `terminal` or `abandoned`;
- for an abandoned entry, `abandon_epoch`;
- the terminal reason;
- the export version `export_version`, and `dead_letter_exported_version`;
- its import occurrence: claim file, line ordinal and row sha256;
- its handoff reference, UNIQUE when present.

### 2.3 Actors and guards

| Actor | Who | May do |
|---|---|---|
| **P** (direct writer) | a `pchat post` or `pchat ack` process; the `agentcli.notify` flow inside its host process; the bridge flow running `announce` | Create its entry, or queue it before any connection exists (Q0). Write its own attempts. Hand its entry to the outbox. Export a dead letter. |
| **B** (bridge) | the single `chatbridge` service process | Index messages and advance coverage. Import and apply handoffs. Flush outbox entries. Reconcile. End attempts of a gone writer or a closed connection. Make entries terminal. Export. Collect evidence. Suspend designations. |
| **S** (status) | `pchat status` | Read only. |
| **O** (owner) | the owner, by explicit decision | Designate an account or lift a suspension. Only at a separately approved activation (section 11). |

| Guard | What it is |
|---|---|
| `TX` | A `BEGIN IMMEDIATE` transaction. Every row condition is checked inside the transaction that changes the row. |
| `L_flush` | The non-blocking flock `outbox.flush.lock`. One flusher at a time, held for the whole flush, including import and export. This lock exists at PR #20's head `e658bb8`, but **not** on master `a12d99c`, whose `flush_outbox` takes no flush lock. The implementation adds it, and declares it in the new `outbox-db` format's path (section 10.1). |
| `L_outbox` | The flock `outbox.lock`, which exists on master (`chatlib.py`'s `_outbox_locked`) and at `e658bb8`. It orders every write to `outbox.jsonl`, claims, imports, and every write to `dead-letters.jsonl` (section 3.6). The bridge and every export acquire it only with a bounded wait; only a direct writer's own fallback append waits as today (below). |
| **owner** | The acting flow's writer identity equals the row's. |
| **gone**, **conn-closed** | Section 5.4. |

**Bounded lock acquisition.** The bridge, and a direct writer's export,
acquire `L_outbox` by repeatedly trying a non-blocking lock until the
absolute deadline `LOCK_WAIT`. If the deadline passes:

- **The bridge** skips only the steps that need `L_outbox` in this flush:
  claim and import (I1–I3), X1, and `Deliveries`' dead-letter appends. It
  continues with all other work: every eligible database entry's attempts
  (E4, A1–A5), reconciliation, A8 and A8c, E7, alerts (X2, which needs only
  `L_flush`), and collection. The skipped steps run at a later flush.
  `pchat status` shows that import or export is deferred because the lock is
  busy, and since when.
- **A direct writer's export (X1)** is left to the bridge.
- **A direct writer's fallback append** is the one acquisition that waits
  for the lock as today. Only that writer's own call waits, and no other flow
  depends on it. Giving up instead could leave a post unqueued where today it
  is queued, and #19's requirement 9 forbids reducing delivery preservation
  (section 6.4).

**No lock is ever stolen.** Nobody breaks, removes or ignores another
process's `L_outbox` or `L_flush`, and nobody ends a writer's attempt or
connection because it holds a lock. A holder that resumes finishes its step;
a holder that dies releases the flock with its process.

## 3. State and transition tables

Every transition is a conditional update inside one `TX`:
`UPDATE … WHERE id = ? AND state = ? [AND generation = ?]`. A change that
matches no row has no effect, and the actor stops work on that attempt or
entry.

**Export version.** Any change to a part or attempt of a `terminal` entry
(A2–A5, A8 or A8c on an attempt still `writing`) increments the entry's
`export_version` in the same `TX` (F6).

### 3.1 Entry

| # | From | To | Actor | Guard | Durable write (one `TX`) | After commit |
|---|---|---|---|---|---|---|
| Q0 | (none) | `open`, `outbox`, no parts | P | Entry *X* does not exist. The failure came before any byte was written: no connection, a failed login or setup, or a failed E1. Silent-run refusal is checked first and commits nothing. | `INSERT` entry *X* with the post's or ack's legacy fields and no parts. If *X* already exists, because an E1 that reported failure had in fact landed, nothing is inserted, and P runs E3 on *X* instead. If this `TX` fails, P makes the durable fallback append (section 6.4). | exit 3, as today: the whole post is queued once |
| E1 | (none) | `open`, `direct` | P | after logging in | `INSERT` entry *X*, and its parts as `unsent`, fixed now from the connection's capabilities. An ack has one pseudo-part. | A1 for part 0 |
| E2 | (none) | `open`, `outbox` | B, by import | `L_flush` + `L_outbox` | The entry with its occurrence identity, committed with the `imports` row. | I3 |
| E3 | `open`, `direct` | `open`, `outbox` | P, owner | — | `owner_kind = 'outbox'` (the handoff). | exit 3, as today |
| H1 | `open`, `direct`; or `abandoned` with `abandon_epoch = import_epoch` | `open`, `outbox` | B, by importing a handoff row | `L_flush` + `L_outbox`. The row's entry id **and** writer identity equal the entry's. | `owner_kind = 'outbox'`, and `state = 'open'` again if it was `abandoned`. | none |
| E4 | `open`, `outbox`, parts not yet fixed | the same, with parts | B | `L_flush`; no parts exist | The fixed parts, `unsent`. A legacy post's "(delayed; written …)" marker is fixed here, once. | A1 |
| E5 | `open` | `done` | P, owner; or B | every part is `confirmed` | `done` | none |
| E6 | `open` | `terminal` (refused) | the writer recording A3 | — | The terminal reason, in A3's `TX`; `export_version = 1`. | X1 |
| E7 | `open` or `abandoned` | `terminal` (undecided) | B | Some part's attempt, `writing` or `ended`, is undecided more than `UNDECIDED_MAX` after its `written_at`. | Terminal; `export_version = 1`; the alert row `outbox:<entry id>` (section 3.6). An `ended` attempt becomes `dead` (A10). A `writing` attempt is **left** `writing`. | X1, X2 |
| E8 | `open`, `direct` | `abandoned` | B | The owner is **gone**, and none of its attempts is `writing` (A8 first). | `abandoned`, `abandon_epoch = import_epoch`. Its unsent remainder is not adopted (D1). Its unknown parts still go to E7. | none |

**One identity per call.** Each `pchat post`, `pchat ack`, `notify` or
`announce` call generates one random entry id *X* before its first `TX`. E1,
Q0, E3 and the fallback row all name *X*, and `entries.id` is the primary key.
So whichever creation lands first wins, and every later attempt to create *X*
is a no-op or a handoff, never a second obligation. Two separate calls, even
with identical text, have distinct ids, so they stay distinct occurrences
(F5 of the issue review).

`done` and `terminal` are final. An `abandoned` entry leaves only by:

- H1, before the next import pass completes (I-7 explains why that is the
  only window in which a handoff row can exist); or
- E7.

### 3.2 Part

| # | From | To | Caused by |
|---|---|---|---|
| P1 | `unsent` | `inflight` | A1 (generation + 1) |
| P2 | `inflight` | `confirmed` | A2 |
| P3 | `inflight` | `refused` | A3 |
| P4 | `inflight` | `unsent` | A4, the server's rejection before acceptance; or A11, an ack's retry |
| P5 | `inflight` | `uncertain` | A5, A8 or A8c |
| P6 | `uncertain` | `confirmed`, msgid set | A6 |
| P7 | `uncertain` | `unsent` | A7, proved absence |
| P8 | `confirmed` | `confirmed`, msgid set | A9 |

`confirmed` and `refused` are final. A post's part returns to `unsent` only by
P4 or P7.

### 3.3 Attempt

| # | From | To | Actor | Guard | Durable write (one `TX`) | Network outside the `TX` |
|---|---|---|---|---|---|---|
| A1 | (none) | `writing` | the entry's owner: P for `direct`; B holding `L_flush` for `outbox` | The entry is `open`; the part is `unsent`. No attempt of this part is `writing` or `ended`. Every earlier part is `confirmed`. | The attempt (writer, `conn_id`, generation, text key, `written_at = now`); the part becomes `inflight`. | After the commit: the part's lines, then `PING :round`. |
| A2 | `writing` | `confirmed` | writer | owner; the state and generation match | `final_at` = when the PONG arrived; the part becomes `confirmed`. | Before: a PONG within `PART_WAIT` or the drain, with no FAIL and no 4xx or 5xx reply. After the drain the call stops, and hands off any remaining parts. |
| A3 | `writing` | `refused` | writer | as A2 | The part becomes `refused`; E6. | Before: a FAIL. |
| A4 | `writing` | `rejected` | writer | as A2 | The part goes back to `unsent`. | Before: a 403, 404 or 482 reply with no FAIL. The existing single channel recreation on 403 then repeats A1. |
| A5 | `writing` | `ended` (`closed`) | writer | as A2 | `ended_at`; `final_at` if a PONG came with another 4xx or 5xx reply, else NULL; the part becomes `uncertain`. | Before: the connection was **closed first**. |
| A6 | `ended` | `delivered` | B | R1, in its `TX` | An attribution; the part becomes `confirmed` with that msgid. | none |
| A7 | `ended` | `absent` | B | R2, in its `TX` | The part goes back to `unsent`. | none |
| A8 | `writing` | `ended` (`writer_gone`) | B | **gone** | `ended_at` = when it was observed; `final_at` NULL; the part becomes `uncertain`. | none |
| A8c | `writing` | `ended` (`connection_closed`) | B | **conn-closed** | as A8 | none |
| A9 | `confirmed` | `confirmed` + attribution | B | R1, in its `TX` | An attribution. | none |
| A10 | `ended` | `dead` | B | E7 | — | none |
| A11 | `ended` (an ack) | `retired` | the entry's owner (P for `direct`; B for `outbox`) | The attempt is an ack's, and it is `ended` | `retired`, and the pseudo-part goes back to `unsent`, in one `TX`. A1's guard counts only `writing` and `ended`, so the retry is now allowed. | Then A1 for the retry, as today (section 8). |

`delivered`, `absent`, `refused`, `rejected`, `dead` and `retired` are final.
A `dead` attempt remains evidence (section 7).

### 3.4 Evidence and coverage

| # | Change | Actor | Guard | Durable write | Notes |
|---|---|---|---|---|---|
| V1 | Index a message | B, on recording a verified PRIVMSG or multiline message, live or replayed | `TX` | `INSERT OR IGNORE` by msgid. The designation check of section 7.4 runs in the same `TX`. | The checkpoint and the coverage `mark` advance past a message only after this commits. |
| V2 | Coverage begins | B, when a catch-up finishes or a new channel is joined | `TX` | `since` and `mark`. `since` carries over only from the stored `mark` (as in PR #20). | none |
| V3 | Coverage advances | B, on the PONG for the sync PING before each flush | `TX`; the channel is live; every earlier V1 for it committed | `through = max(through, ping_sent_at)` | none |
| V4 | Coverage stops | B, on KICK or PART, a disconnect, a stale catch-up reply, or a failed V1 | in memory | The channel leaves the live set. The checkpoint stays before the failed message. | none |

### 3.5 Import, export and collection

| # | Change | Actor | Guard | Durable write or effect |
|---|---|---|---|---|
| I1 | Claim | B | `L_flush` + `L_outbox` | Rename `outbox.jsonl` to a new unique `outbox.claimed-<ns>-<random>.jsonl`. The new name is not in `imports` and does not exist on disk. Then a durable sync of **every** claim file present: the new one, and any left by an earlier pass, a crash, or the old tools before cutover (F3; design round 3). |
| I2 | Import | B | `L_flush` + `L_outbox`; `TX`; only after I1's durable sync of that claim file | The occurrences, held rows, handoffs (H1), any alert row (section 6.2) and the `imports` row, in **one** `TX` (section 6). After the last file of a pass, `import_epoch` + 1, in that pass's last `TX`. |
| I3 | Unlink | B | after I2 commits | Unlink the claim file, then `fsync` the directory. |
| X1 | Export a dead letter | P or B | `L_outbox`, acquired within `LOCK_WAIT` | Section 3.6. |
| X2 | Export an alert row | B only | `L_flush` | Section 3.6. |
| G1 | Collect evidence | B | `L_flush`; `TX` | The deletions I-7 allows. |

### 3.6 Export and sink protocol

**Every writer of `dead-letters.jsonl` follows the same protocol under
`L_outbox`** (F5). The writers are X1 (P or B) and the bridge's existing
delivery dead letters (`Deliveries._dead`).

1. Acquire `L_outbox` within the writer's deadline (section 2.3). If it is
   not acquired, write nothing and mark nothing; the export stays pending.
2. If the file does not end in a newline, append one. A torn last line then
   becomes an unreadable line, which readers skip.
3. Append the record, unless a parseable line with the same key already
   exists.
4. **Always**, whether the record was just written or found already present,
   and whoever created the file, make a durable sync of the file: `fsync` the
   file, **then `fsync` the directory** (section 2.1). Only then may anything
   record the export as done.

**`Deliveries`' dead letters** follow the same steps. `Deliveries` removes an
item from `pending.json` only after its dead-letter line is durable. If
`L_outbox` is not acquired, the item stays pending, unchanged, and the dead
letter is tried again at a later pass.

**X1, the dead letter of entry *e*, version *v***:

1. In a `TX`, read *e*. If `dead_letter_exported_version ≥ export_version`,
   stop. Otherwise let *v* = `export_version`.
2. Write the record keyed (entry id, *v*) by the sink protocol, including its
   durable sync. The record holds:
   - the entry, with every part's text and state;
   - every attempt's state, write time, `final_at` and msgid;
   - the terminal reason;
   - *v*, and `supersedes: v − 1` when *v* > 1.

   If `L_outbox` is not acquired, stop here; the mark stays unset, and a
   later X1 tries again.
3. In a `TX`, set `dead_letter_exported_version = v`, unless it is already
   higher.

A late outcome after an export (F6) increments `export_version`. The next X1
appends version *v* + 1, which supersedes the earlier record. Readers
(`pchat status`, and the bridge's own checks) use the highest version per
entry id. The entry stays terminal, and never becomes retryable.

**Alert rows.** Every owner alert is one row of `alerts`, inserted in the
same `TX` as the event that raises it. Its key names the event, so the event
can raise it only once:

| Kind | Raised by, in the same `TX` | `alert_key` | Example text (content-free) |
|---|---|---|---|
| `undecided` | E7 | `outbox:<entry id>` | "an outbox post from alp-solver-2 to #alpha could not be confirmed for 24 h; dead-lettered" |
| `held` | I2, or the hold of a changed claim file (section 6.3) | `held:<claim file name>`, or `held:<claim file name>:<sha256>` for a changed file | "queued outbox rows could not be imported and are held; see pchat status" |
| `suspended` | the V1 that suspends a designation (section 7.4) | `suspended:<account>:<suspension_seq>` | "sender designation of alp-solver-2 suspended: an unexplained message in #alpha" |

A claim file name is never reused, an entry reaches E7 once, and
`suspension_seq` increases by exactly one in the `TX` that suspends. So each
event has exactly one key, and a crash before that `TX` commits leaves no row
and no event, both of which happen again together.

**X2, export alert row *a*** (B only, because the bridge's `Deliveries`
object owns `pending.json`):

1. In a `TX`, read *a*. If `exported_at` is set, stop.
2. If a pending item already carries *a*'s key, make `pending.json` durable
   (a durable replace, which syncs the directory) and go to step 5.
3. If a ledger line in `deliveries.jsonl` carries the key, make a durable sync
   of the ledger (`fsync` the file, then the directory) and go to step 5.
4. Otherwise add the content-free `push` item under that key, and write
   `pending.json` durably.
5. In a `TX`, set *a*'s `exported_at`, unless it is already set.

**The pending queue's lifecycle.** Every `Deliveries` save of `pending.json`
is a durable replace, and every ledger append is a durable append (section
2.1). An item leaves `pending.json` only after its ledger line is durable. A
crash or power loss at any point therefore leaves the alert either still
pending, or recorded in the ledger, and never lost.

Each alert row is enqueued exactly once, because its key is checked in
pending and in the ledger before an item is added, and it is delivered at
least once. Its text names only accounts, channels and file names, never a
message's content.

## 4. Invariants and proofs

### I-1. Intent before bytes

Every transport caller commits a `writing` attempt (A1) before any byte of a
part or an ack is sent. A failure before that point writes nothing: Q0 only
queues the call.

**Why it holds.** Posting and acknowledging both go through the attempt
protocol, including today's separate `chatlib.ack` send. A1 is the only way
to obtain an attempt, and its writer sends only after the commit.

**Consequence.** Any message an attempt produces carries a time of at least
`written_at − A` (C-1).

### I-2. Fencing

Only an attempt's own writer moves it out of `writing`, except that B may do
so (A8, A8c) once the writer is proven gone or the connection proven closed.

No attempt is created for a part that has a `writing` or `ended` attempt. An
ack's `ended` attempt is first `retired` (A11), and only after its connection
is closed or its writer is gone.

**Why it holds.**

- A2–A5 require the owner. A8 and A8c require their proofs, and A1's guard
  does the rest.
- A writer paused after A1 holds an open socket: it is not gone, and its
  connection is not closed, so no one ends or retries it. E7 can make its
  entry terminal after 24 hours, but leaves its attempt `writing`.
- Checks before sending, and leases, cannot close the gap between a check and
  the send. The fence therefore relies only on two facts: a closed socket
  cannot send, and a dead process cannot send.

### I-3. Confirmed parts are never resent

**Why it holds.**

- A1 requires `unsent`.
- A post's part becomes `unsent` again only by P4, where the server rejected
  it and nothing was accepted, or by P7, after absence was proved.
- `confirmed` is final.
- A part is fixed once, and never re-split.

### I-4. No message satisfies two obligations

Attribution rows are the only way a message satisfies an obligation, and both
`msgid` and `attempt_id` are UNIQUE.

R1 attributes all the members of a component at once, by one perfect matching
in one `TX`. R2 never attributes.

A confirmed attempt without a msgid needs a message of its own, and R1 and R2
both count it. Attributed messages are excluded from every later candidate
set.

### I-5. Storage errors preserve outcomes

A storage failure never turns a written part into an `unsent` one, and never
discards a committed obligation.

**Why it holds.**

- **A1 fails:** nothing is written. If entry *X* exists, P hands it off
  (E3), so the existing E1 entry is kept and no second obligation is created.
  Otherwise P queues by Q0.
- **A2–A5 fail:** the writer closes the connection if it is still open, keeps
  the observed outcome in memory, and retries the commit:
  - within the call, up to `PART_WAIT`;
  - then the bridge retries at the start of each flush, and `pchat` and
    `agentcli` retry at each later `chatlib` call and at process exit.
- **The commit never lands:** the attempt stays `writing`, until A8 (writer
  gone) or A8c (connection closed, for example in a long-lived `agentcli`
  host) ends it **without finality**. It is then `uncertain`, R2 never calls
  it absent, and only R1 or E7 resolve it.
  - A refusal whose A3 never committed is therefore UNKNOWN. It is never
    retried, and is dead-lettered after 24 hours (N6, an owner decision).
- **E3 fails:** the handoff fallback (section 6.4), applied by H1.
- **Q0 fails:** the durable fallback append (section 6.4). P reports the post
  as queued (exit 3) only after a durable sync of `outbox.jsonl`, file **and**
  directory, so a power loss cannot remove a row P has acknowledged, even when
  another process created the file and never synced the directory (design
  round 3). If the append cannot be made durable, P fails as today when the
  outbox cannot be written; it never reports a queued post that is not
  durable.
- **Never:** a post's original whole text is never requeued after a byte was
  written. That is round-5 finding 2.

### I-6. Ownership is visible before a competing flush can use a message

**Why it holds.**

- A direct post's A1 commits before its bytes are sent.
- The bridge can index the message (V1) only after the server publishes it.
- Reconciliation's `TX` starts after V1, so it sees that attempt as `writing`,
  or in a later state.
- R1 refuses a component with a `writing` member.
- R2's forcing argument cannot be broken by a member that only adds messages
  (section 7.3).

That is round-5 finding 3.

### I-7. Evidence is collected only when nothing depends on it

A row may be deleted only when every condition for its kind holds.

**Dependency components.** For each unresolved attempt U (`writing`,
`ended`, or `dead` with an open window), take its *dependency component*. That
is every attempt of the same account and channel, of **any** text key and any
state, connected to U through overlapping windows, transitively. The *hull* is
the span from the earliest window start to the latest window end of that
component; it is unbounded if any window is open.

The component includes everything R1 and R2 may count for U:

- the members of U's own reconciliation component, including confirmed
  attempts without a msgid;
- the confirmed attempts R2 condition 5 uses to account for other texts;
- a confirmed member whose own message lies outside U's window but inside its
  own (issue review addition).

1. **Messages.**
   - The message's time lies inside the hull of no dependency component.
     This covers every fragment and every cross-key dependency, because all
     of them share the account and channel.
   - The message's time is before `now − A − GC_MARGIN`.
2. **Attributions.** The row is deleted only in the same `TX` as its message
   row.
3. **Parts and attempts of an entry.** All of these hold:
   - the entry is `done`, `terminal` or `abandoned`;
   - if terminal, `dead_letter_exported_version = export_version`, and the
     entry's alert row, if any, has `exported_at` set. Both marks are
     committed only after a durable sync of the record's file and directory,
     including when recovery found the record already present (sections 2.1
     and 3.6). So a mark implies that the diagnostics survive a power loss,
     and collection never removes the only durable copy;
   - if abandoned, `import_epoch > abandon_epoch`. An import pass has then
     completed that started after the writer was proven gone. Every handoff
     row that writer could have written was durable before it died, so that
     pass claimed it and applied H1. After that pass, H1 can never apply to
     the entry, and its progress is no longer reopenable (F4);
   - none of its attempts is `writing`, `ended`, or `dead` with an open
     window;
   - none of its attempts belongs to any dependency component;
   - their windows ended before `now − A − GC_MARGIN`.
4. **Never collected:**
   - entry rows, which are kept as tombstones;
   - coverage spans;
   - `imports` rows;
   - `held` rows;
   - `alerts` rows.

Any later attempt has `written_at ≥ now`, so its window starts at or after
`now − A`, and it cannot depend on a collected row.

A 26-hour outage collects nothing an unresolved attempt needs. That is
round-5 finding 4.

### I-8. Decisions read current state

Every verdict is computed and applied in one `TX`, from the current attempts,
attributions, messages, coverage and designations. No snapshot from the start
of the flush is used. That is round-5 finding 1.

### I-9. Import keeps occurrences and is idempotent

See section 6.5.

### I-10. Independent flow

An entry interacts with others only through same-account, same-channel
evidence. An undecided, failing or refused entry never stops the flush from
attempting every other open outbox entry.

Each entry's work is isolated, and each entry's turn in a flush is bounded by
absolute deadlines that unrelated traffic cannot extend:

- `LOGIN_WAIT` for setup;
- `PART_WAIT + DRAIN_WAIT` per part;
- `CHANNEL_WAIT` for one channel recreation.

**Locks are bounded too** (design round 3). The flush waits for `L_outbox` at
most `LOCK_WAIT` per step, and nothing that delivers a database entry needs
`L_outbox` (section 2.3).

- **A process paused while holding `L_outbox`**, for example a direct writer
  that committed a refusal and paused inside X1, costs the flush at most
  `LOCK_WAIT`. The flush skips import and export for that pass, and still
  attempts every eligible database entry, reconciles, applies E7, and
  exports alerts.
- **The paused holder keeps everything it owns.** Its lock, its connection
  and its attempts are untouched. When it resumes it finishes its step; if it
  dies, the kernel releases its flock.
- **What waits.** Rows still in `outbox.jsonl`, and dead-letter files, wait
  for the lock, because claiming a file that a writer may be appending to
  could lose that writer's row. They are durable, shown in `pchat status` as
  deferred, and imported or written at the first flush after the holder
  releases the lock. Nothing is lost, resent or decided while they wait.

`L_flush` is held only by the single bridge, so no other process can delay a
flush through it. The database's own write lock is bounded by `BUSY_WAIT`;
C-8 states what a process stopped inside a transaction can and cannot do.

### How each round-5 finding is closed

| Round-5 finding at `e658bb8` | Closed by |
|---|---|
| 1. Satisfied keys are loaded once per flush (`chat-bridge:1104-1107`). | I-8: one `TX` per component, with no snapshot. |
| 2. A `reserve()` failure escapes without parts, and the whole text is requeued (`chatlib.py:517`, `539-546`). | I-5: A2 is the confirmation record. A failure keeps the outcome, or leaves the part uncertain, and never requeues the whole text. |
| 3. The reservation is written after the commit (`chatlib.py:516`). | I-1 and I-6. |
| 4. Reservations are pruned by wall-clock time (`chat-bridge:947`). | I-7: collection by dependency. |

## 5. Connections, finality, assumptions and crashes

### 5.1 Connections

- **One flow per connection.** A connection belongs to the one flow that
  opened it, in one `post()` or ack call. It carries at most one unresolved
  attempt at a time.
- **Bounded setup.** Every wait has an absolute monotonic deadline, checked
  before every read, as PR #20 already does for `PART_WAIT`. Unrelated
  traffic, such as server notices or other channels' messages, never extends
  one:
  - connecting, capability negotiation, SASL and the welcome share one
    `LOGIN_WAIT` deadline;
  - creating or joining a channel after a 403, and waiting to see the bridge
    join it, share one `CHANNEL_WAIT` deadline.

  Missing a setup deadline is a failure before any byte of the next part is
  written:
  - a direct call queues by Q0, or hands off its entry by E3;
  - a flush records nothing new and moves on to the next entry.
- **Close before `ended`.** The writer closes the socket (shutdown, then
  close), and only then commits A5.
- **After closing.** The process cannot send on that socket again. Bytes
  already in the kernel's buffer may still reach the server, so an attempt
  without finality keeps an open window.

### 5.2 Finality

The proof uses the boundary PR #20 already relies on to confirm a part. The
server processes one connection's lines in order, and answers a PING sent
after a part only once it has processed the part's lines (C-3).

When the PONG arrives at time `f`, therefore:

- every line of the part has been processed;
- any message they produced was published before that, with a time of at most
  `f + A`;
- nothing published later can come from that attempt.

**How the writer gets it:**

- **Within `PART_WAIT`:** the PONG gives A2, A3, A4, or A5 with finality when
  another 4xx or 5xx reply came with it.
- **After `PART_WAIT`:** the writer stops writing, and keeps reading the same
  connection for up to `DRAIN_WAIT`.
  - A PONG then gives the same outcomes, with finality, and the call stops.
  - If no PONG arrives, the writer closes, and A5 commits without finality.

**No finality.** These cases have no finality, and R2 never applies to them:

- a write error;
- a broken connection;
- no PONG through the drain;
- a gone writer;
- a closed connection;
- a bare timeout.

**Absence recovery.** Automatic absence recovery applies only to attempts with
finality (R2):

- a part answered with an error reply;
- a part whose PONG arrived during the drain.

Anything else is decided by R1 alone (designated accounts), or dead-lettered
after 24 hours with its progress kept (N7, an owner decision).

### 5.3 Assumptions

Anything these do not establish stays undecided.

- **C-1, clocks.** The writer, the bridge and the chat server run on one
  machine. The server's message time and the writer's wall clock differ by at
  most `A`, and the clock does not step by more than `A` during a window.
- **C-3, in-order processing and replies (PR #20's boundary).**
  - The server processes one connection's lines in order.
  - The PONG to a PING sent after a part follows the processing of the
    part's lines.
  - A PONG with no FAIL and no 4xx or 5xx reply means the part was published
    as exactly one message, of its exact text.
  - A FAIL, or a 403, 404 or 482 reply with no FAIL, means nothing of the part
    was published.
- **C-4, no model of partial publication.** This note does **not** assume how
  a multi-line batch could be partly published. It makes no claim about which
  texts a partial publication could show, whether lines, blank lines, joined
  or reordered pieces, or anything else.

  So any same-account, same-channel message in a multi-line part's window
  that is not accounted for blocks absence (R2, condition 5). Any multi-line
  attempt that is still unconfirmed blocks attribution for its account and
  channel inside its window (R1, condition 4). This is the conservative
  choice the owner approved (F1).

  A single wire line is processed whole or not at all, because IRC processes
  complete lines only. A single-line part therefore has no partial result
  other than its exact text.
- **C-5, a complete record.** Coverage `[since, through]` for a channel means
  every verified message the server published there, with a time in that
  interval, is in `messages` (V1–V4).
- **C-6, identity.** The `account` tag is the server-verified sender, and only
  verified messages are evidence.

  Untracked producers are possible, and are not assumed away: the old live
  tools before activation, a person, or another client using the account.
- **C-7, designated senders (owner decision D2).** For an account designated
  at a separately approved activation, every message from `designated_at`
  onward is produced by a tracked attempt.
  - Only R1 uses this assumption.
  - It is monitored, and suspended on the first message the record shows is
    unexplained (section 7.4).
  - It is never assumed for an account that is not designated.
- **C-8, the database's single writer.** SQLite serializes writers: a process
  holding a write transaction makes every other writer wait. This note
  bounds and limits that, and does not assume it away:
  - **Transactions are short and local.** No transaction contains network
    I/O, a file lock wait, or any other wait (section 2.1). A writer that is
    waiting on the network, draining, paused between steps, or holding
    `L_outbox` holds no database lock. Every pause the proofs consider,
    including a paused writer after A1 and a paused `L_outbox` holder, is
    outside a transaction.
  - **Waits are bounded.** Every transaction waits at most `BUSY_WAIT`. If
    the bridge's transaction cannot start, it commits nothing, sends nothing
    (I-1), logs it, and tries again at the next flush. Nothing is lost,
    resent or decided.
  - **What remains.** A process stopped by the operating system, or by a
    debugger, *inside* one of these short transactions blocks every database
    write until it resumes or dies. When it dies, SQLite rolls its
    transaction back. That is a host-level stall of the single authority the
    owner chose (section 11, decision 1), not an entry's state. No entry that
    is uncertain, waiting, failing, refused or paused can cause it, so it
    does not contradict I-10. It is never resolved by breaking the lock.

### 5.4 Gone writers and closed connections

**A writer is gone** when either holds:

- the boot id differs from the recorded one;
- no process with the recorded pid exists, or that pid's process has a
  different start time.

**A connection is closed** when the writer's process exists with the recorded
start time, but holds no TCP socket with the recorded local port to the
recorded server address. The bridge reads this with the operating system's
process tools.

That lets B end a closed flow inside a long-lived host (`agentcli`) without
the host exiting. A reused port can only make a closed connection look open,
which is the safe direction.

If either probe cannot be read, nothing is proved. A paused writer still holds
its socket open, so neither proof applies to it.

### 5.5 A crash at every boundary

Each transition and external step is listed with the state a crash leaves just
before it (or before its commit) and just after it. "→" names the recovery.
"Power loss" means the files changed but did not reach the disk.

#### Writer side (P, or B as a flusher)

| Boundary | Crash just before | Crash just after |
|---|---|---|
| login, or connection setup (bounded by `LOGIN_WAIT`) | nothing durable, nothing sent | → Q0 on *X* if P is still running |
| Q0 `TX` | nothing durable, as today | `open`, `outbox` *X* → flushed |
| Q0 `TX` reported failure but landed | — | *X* exists. P's fallback row names *X*, so its import is a no-op, never a second entry. |
| Q0 fallback append, then the durable sync (file, then directory) | nothing durable and nothing acknowledged, as today. A power loss before the directory `fsync` is the same: P has not exited 3 yet. | durable → P exits 3 → imported as *X* (section 6.4) |
| Q0 fallback append to an `outbox.jsonl` another process created and never directory-synced | — | P's durable sync covers the directory entry too, so the row survives a power loss after exit 3 (design round 3). |
| E1 | nothing durable, nothing sent → Q0 on *X* | `open`, `direct` *X*, all `unsent` → if P is still running, E3 hands *X* off. If the writer is gone, E8, nothing unknown → retired as today. |
| E1 reported failure but landed | — | P finds *X* before Q0 → E3 on *X*: one obligation |
| A1 | the previous state → Q0 if nothing was written, else E3 | `writing` → A8 → `uncertain`, no finality → R1 or E7 |
| sending lines and PING | `writing` → A8 | same |
| `PART_WAIT` and the drain | `writing` → A8 | same |
| close | `writing` → A8 | `writing` → A8, or A8c while the host lives |
| A2 | `writing` → A8 → uncertain, never `unsent` | `confirmed` |
| A3 + E6 | `writing` → A8 → UNKNOWN (N6), never retried → E7 | `terminal` → X1 |
| A4 | `writing` → A8 → UNKNOWN → E7 | `unsent` → retried by the owner, or E8 |
| A5 | `writing` → A8, no finality | `ended`, with `final_at` as observed |
| A11 (ack) | `ended` ack → A11 next time | `retired`, `unsent` → A1 retry |
| E3 | `direct` → gone → E8, or H1 if the fallback row is durable | `outbox` |
| the fallback handoff append and its durable sync | E8 → remainder not adopted (D1). A power loss before the directory `fsync` is the same. | durable → H1 at the next pass |
| E5 | all parts confirmed → B sets `done` | `done` |

#### Bridge side

| Boundary | Crash just before | Crash just after |
|---|---|---|
| E4 | no parts → E4 again | parts `unsent` |
| A8, A8c | again at the next flush | `ended` |
| R1, R2 `TX` | unchanged → decided again | applied atomically |
| E7 | → E7 again | `terminal` → X1, X2 |
| a late outcome on a terminal entry | — | `export_version` + 1 → X1 writes version *v* + 1 |
| E8 | as before | `abandoned`, `abandon_epoch` |
| I1 rename | `outbox.jsonl` intact | claim file C |
| I1 directory `fsync` | **power loss:** `outbox.jsonl` may reappear and C vanish. No I2 has committed, so it is simply claimed again under a new name. | C durable → I2 |
| a claim file left by an earlier pass, a crash, or the old tools, possibly never synced | — | I1 makes a durable sync of it before its I2, so an import never commits from a claim whose directory entry could vanish. |
| I2 | no rows, no `imports` row → C imported again with the same occurrence ids | rows and the `imports` row for C → I3 |
| I3 unlink, then directory `fsync` | C present, `imports` matches → unlink | **power loss:** C may reappear → `imports` matches → unlink only |
| H1 (in I2) | rolled back with I2 | applied |
| `import_epoch` + 1 | the pass is not counted → another pass runs before collection | counted |
| V1 | not indexed; the checkpoint has not moved → replayed, `INSERT OR IGNORE` | indexed |
| the checkpoint file write | the stored `mark` disagrees with the checkpoint → V2 starts a new span (safe) | consistent |
| V2, V3 | the old span | the new span |
| a bridge restart | the live set is empty → coverage advances only after a catch-up | — |
| X1 append | → X1 again | **power loss before the `fsync`:** the line may vanish; the mark is unset → X1 again. A crash with the line present: X1 finds it, makes a durable sync (file, then directory), then marks. |
| X1 creates `dead-letters.jsonl`, appends and `fsync`s the file, then crashes before the directory `fsync` | — | The mark is unset. The next X1 finds the line, `fsync`s the file **and the directory**, and only then marks. A power loss after the mark keeps the record (design round 3). |
| X1 mark | the record is durable, the mark unset → X1 finds it, syncs the file and directory, marks | done |
| `Deliveries._dead` append | the item is still pending → retried by `Deliveries` | durable by the sink protocol, file and directory → the item leaves pending |
| X2 steps | → repeated: found pending or in the ledger, made durable (the ledger with a directory `fsync` too), then marked | — |
| a ledger line found by X2 in a `deliveries.jsonl` whose creation was never directory-synced | — | X2 syncs the file and the directory before marking, so a power loss after the mark cannot lose the line (design round 3). |
| the `TX` that raises an alert row (E7, I2, a changed-file hold, a suspending V1) | no event and no row → both happen again together | one row, one key → X2 |
| a `Deliveries` save or ledger append | the previous durable state | durable, file and directory. The ledger line is made durable before the item leaves pending. |
| G1 | rolled back | the allowed deletions |

### 5.6 Interleavings

1. **A direct post and a flush (round-5 finding 3).** `alp-solver-2` has an
   outbox part U with the text "[status] build green". U ended with finality,
   and never published. The account then posts the same text directly as D.
   - D's A1 commits, the server publishes D's message `m`, and B indexes `m`.
   - B's flush runs while D waits for its PONG.
   - R1 refuses, because `W` is not empty. R2 cannot rule U absent, because
     `m` is not forced. U stays undecided.
   - After D's A2, `m` is forced to D, and R2 rules U absent once coverage
     covers U's window. U is resent. `m` never counted for both.
2. **Two flushers.** `L_flush` makes the second skip. A1's guard allows only
   one attempt per part anyway.
3. **A paused writer.** P stops after A1. No one ends or retries its attempt.
   - After 24 hours, E7 makes the entry terminal and exports version 1, and
     leaves the attempt `writing`.
   - If P resumes and commits A2, `export_version` becomes 2, X1 exports the
     refreshed record, and only then may G1 collect anything.
4. **A hung bridge.** A second bridge skips the flush while the first holds
   `L_flush`. When the first exits, A8 applies.
5. **Handoff versus abandonment.** P confirms part 0, then E3 fails. P makes
   the handoff row durable and exits. B commits E8 before its next pass,
   with `abandon_epoch = e`.
   - That next pass claims the row, and H1 reopens the entry, because the
     epoch is still `e` and the ids match.
   - G1 could not have collected part 0 in between: `import_epoch` was not
     yet greater than `e`.
   - An unrelated killed post wrote no row, so after the pass it stays
     abandoned and becomes collectible.
6. **`pchat status` during a flush.** It reads one snapshot.
7. **P exports while B does.** `L_outbox` serializes them. The second finds
   the line present, makes it durable, and sets the mark.
8. **`Deliveries` writes a delivery dead letter during X1.** `L_outbox`
   serializes them, so no two appends interleave.
9. **A paused `L_outbox` holder (design round 3).** `alp-solver-2`'s direct
   post P is refused: A3 and E6 commit. P acquires `L_outbox` for X1 and
   pauses. Meanwhile `sam` has an open outbox entry for `#beta` in the
   database.
   - B's flush takes `L_flush`, then tries `L_outbox` for import until
     `LOCK_WAIT` passes. It skips import and export for this pass.
   - B still gives `sam`'s entry its attempt in the same flush, reconciles,
     applies any E7, and exports alert rows with X2.
   - P's lock and connection are untouched. When P resumes, it finishes X1;
     if it never resumes and dies, its flock is released. The next flush
     after either imports and exports as usual.
10. **A direct writer appending while the bridge waits.** P holds `L_outbox`
    for a fallback append. B's claim waits at most `LOCK_WAIT`, then defers;
    P's row is claimed by the next flush, never lost and never split.

## 6. Importing the old outbox

### 6.1 A new rule: frozen snapshots

Neither base makes a claimed file immutable:

- **At PR #20's head `e658bb8`**, `_Claimed.save()` (`chat-bridge:1030-1036`)
  rewrites claimed files with part progress.
- **On master `a12d99c`** there is no `_Claimed`: `flush_outbox` reads a
  claimed file whole, requeues what failed to a fresh outbox, and unlinks the
  claim. Nothing there states or enforces that a claim never changes.

Immutability is therefore a new rule of this design, whichever base the
implementation starts from:

- **The cutover marker.** A `meta` row, written when the schema is created.
  Once it exists, no code path writes a claimed file. If the implementation
  builds on PR #20's head, `_Claimed.save()` and its callers are removed.
- **Quiescing the old writers.** The running bridge and the old tools' flush
  are stopped **only** at the separately approved activation, before the first
  live import. Until then, plateia's copy touches no live state.

A claim file the old code may have rewritten is imported once, as it stands
under the locks.

### 6.2 Supported formats and the hold policy

Import supports exactly:

- the running tools' text post rows: `channel`, `as`, `text`, and optionally
  `cont`, `reply_to` and `at`;
- their ack rows: `channel`, `as`, `ack` and `at`;
- this design's fallback rows (section 6.4).

**Anything else is held** in `held`, with its raw text, and shown in `pchat
status`. Each claim file that holds anything raises **one** content-free
alert row, keyed by the claim file's name, in the same I2 `TX` as its held
rows (section 3.6). Held rows include:

- rows with `parts`, `terminal`, or any `id` that is not a fallback row's.
  These are PR #20 formats, which never ran live;
- unreadable lines.

**PR #20's side files are never imported:** `outbox-reserved.jsonl`,
`outbox-attributed.jsonl` and `record-coverage.json`. If any exists at
cutover, it is left untouched and reported. Coverage starts fresh, from the
first catch-up after cutover.

**Legacy rows.** A legacy text row carries no attempt history. Whatever the
old tools may already have published of it is outside this design's evidence,
and it is delivered as #19 R3 requires ("as today").

### 6.3 The fenced import

The import runs only in B's flush, holding `L_flush` and `L_outbox`, and does
only local work.

0. **Acquire `L_outbox`** within `LOCK_WAIT`. If it is not acquired, skip
   the whole import this flush (section 2.3).
1. **Claim (I1).** Rename a non-empty `outbox.jsonl` to a new, never-used
   claim name. List every `outbox.claimed-*.jsonl`, and make a durable sync
   of each one (file, then directory), including claims left by an earlier
   pass, a crash, or the old tools before cutover.
2. **Read** each claim file whole, with its sha256 and line count.
3. **Check** for an `imports` row with that name:
   - **the same hash:** go to step 6;
   - **a different hash:** in one `TX`, record the file in `held` and raise
     its alert row `held:<name>:<sha256>`, unless that row already exists.
     Neither import nor unlink it.
4. **Import (I2)** in one `TX`, for each line ordinal `i`:
   - **The occurrence id is always derived** from (claim file name, `i`),
     for example `sha256(name + ":" + i)`, and is UNIQUE. An explicit id
     never replaces it.
   - **A text or ack row** becomes an `open`, `outbox` entry (E2).
   - **A fallback row** is applied by its entry id *X* (section 6.4).
   - **Any other row** is held.
   - **Record the import:** insert the `imports` row (name, line count,
     sha256, epoch).
5. **Commit.** At the end of the pass, increment `import_epoch` in that
   pass's last `TX`.
6. **Unlink (I3)**, then `fsync` the directory.

### 6.4 Fallback rows

Every fallback append is a durable append under `L_outbox`, which P waits
for as today (section 2.3): the line, then `fsync` of `outbox.jsonl`, then
`fsync` of the directory, always (section 2.1). P exits 3, reporting the post
as queued, only after that sync. If the sync fails, P fails as described at
the end of this section.

There is one fallback row shape, for both Q0 and E3. It holds the post's or
ack's legacy fields, plus `fallback: true`, the call's entry id *X*, and P's
writer identity when P had a connection. P appends it when Q0's or E3's `TX`
cannot commit. Import applies it by *X*, never by occurrence:

- **Entry *X* exists and is `open`, `direct`, or `abandoned` with H1's
  guard:** apply H1. The existing E1 entry and its progress are kept.
- **Entry *X* exists and is already `outbox`, `done` or `terminal`:** a no-op.
  This includes a Q0 commit that reported failure but had landed. Nothing is
  merged, and no second entry is created.
- **Entry *X* exists, and H1's identity guard fails:** hold the row.
- **Entry *X* does not exist:** E1 never committed, so by I-1 nothing of this
  call was sent. Import inserts entry *X* (`open`, `outbox`, no parts),
  delivering the whole post once, as today. The row's occurrence (file,
  ordinal) is recorded with it.

Every creation is an `INSERT` on the primary key *X*, so an ambiguous commit
that becomes visible later, whether E1's or Q0's, collides with the import's
insert instead of duplicating it. Legacy rows from the old tools carry no *X*,
and keep their occurrence-derived ids (section 6.3).

If a fallback append fails too, P fails as it does today when the outbox
cannot be written. Whatever already committed still prevents a blind resend.

### 6.5 Why the import is idempotent and keeps occurrences

- **A crash, or power loss, before the commit** leaves no rows. The file is
  durable under its claim name, so the next pass derives the same occurrence
  ids.
- **After the commit** the claim name is durable, so a power loss cannot bring
  back `outbox.jsonl` with rows already imported. A reappearing claim file
  matches its `imports` row, so only the unlink remains.
- **A changed hash** is held, never merged.
- **Every line** is its own occurrence: duplicates are kept, never dropped as
  replays.
- **Claim names** are never reused.

## 7. Reconciliation rules

### 7.1 Windows

| Attempt | Window |
|---|---|
| `writing` | `[written_at − A, +∞)`, open |
| `confirmed` with no msgid | `[written_at − A, final_at + A]`, closed |
| `ended` or `dead`, with finality | `[written_at − A, final_at + A]`, closed |
| `ended` or `dead`, without finality | `[written_at − A, +∞)`, open |

### 7.2 Components and accounted messages

For text key *k*, the **members** are its attempts that are `writing`,
`ended` or `dead`, or `confirmed` without a msgid. Members with overlapping
windows are connected, and a **component** is a connected set of members.

- `K`: the confirmed members. Each published exactly one message, of its
  exact text, in its window (C-3).
- `G`: the `ended` members.
- `D`: the `dead` members.
- `W`: the `writing` members.
- `M`: the unattributed messages of key *k* whose time lies in some member's
  window.

**Forced.** A message `m` of key *t* is forced when both hold:

- coverage is complete over the hull of the closed windows of `K_t`, the
  confirmed members of key *t* whose windows contain `m`, together with their
  closed-window connections;
- `K_t` can be fully matched to distinct messages, but not without `m`.

*Argument.* Every member of `K_t` published exactly one message of text *t*
inside its window, and coverage puts all of them in `messages`. If `m` were
not one of them, `K_t` could be matched without it. So a forced message is the
message of a confirmed attempt, and nothing else's.

### 7.3 Verdicts

Each verdict is decided per component, in one `TX`.

#### R1, DELIVERED (the whole component at once)

All of these must hold:

1. The account is designated (D2), with `designated_at` no later than the
   component's earliest window start, and it is not suspended.
2. `W` and `D` are empty.
3. `|M| = |K| + |G|`, and a perfect matching of `K ∪ G` onto `M` respects
   every window.
4. **No multi-line hazard.** No unconfirmed multi-line attempt of the same
   account and channel, from another text key, has a window containing any
   message of `M`. "Unconfirmed" means `writing`, `ended` or `dead`.

   No fragment model is assumed (C-4). Such an attempt might have produced
   any text, so it blocks attribution in its window.

Then every `G` member becomes `delivered` (A6), and every `K` member gets its
matched msgid (A9).

**Argument.**

- **Every message in `M` came from a member of the component.** By C-7, every
  message in `M` came from a tracked attempt. That attempt is either:
  - an attempt of key *k*, whose window contains the message, which by
    definition is a member; or
  - an unconfirmed multi-line attempt of another key, which condition 4
    excludes; a confirmed one published only its exact text (C-3).
- **Every member published exactly one.** Each member published at most one
  message of text *k*, and `K` published exactly `|K|`. So `|M| = |K| + |G|`
  means every member published exactly one, and all of them are in `M`. No
  member can publish another later, even with an open window.
- **The matching is valid.** All members are attributed together, so any
  matching is a valid assignment. Later attempts send after their A1, which
  is after these messages were indexed, so none of them is a later attempt's
  message.
- **Why conditions 2 and 1 are there.** `W` must be empty because B may not
  decide a live writer's attempt, and `D` because a dead outcome is never
  decided. Without designation R1 never fires, so an untracked identical
  message cannot retire an unpublished part.

#### R2, ABSENT (one `ended` member `Y`)

All of these must hold:

1. `Y` has finality: its window is closed.
2. Coverage is complete over the hull of `Y`'s window and of `K_c`, the
   confirmed members of key *k* connected to `Y` through closed windows.
3. `K_c` can be fully matched into `M`. If not, the verdict is undecided and
   an anomaly is logged.
4. **Every** message of `M` inside `Y`'s window is forced to `K_c`.
5. **If `Y` is multi-line:** every unattributed message of the same account
   and channel inside `Y`'s window, of **any** text other than *k*, is forced
   to the confirmed attempts of its own text key (section 7.2).

Then `Y` becomes `absent` (A7), and its part goes back to `unsent`.

**Argument.**

- **What `Y` could have published.** By finality and C-1, anything `Y`
  published has a time inside `Y`'s closed window. By C-5 it is indexed.
  - If `Y` is single-line, the only possible publication is its exact message
    `m_Y` (C-4).
  - If `Y` is multi-line, it is `m_Y` or some message `g` of unknown text.
- **It is unattributed.** Attributions come only from R1, and `Y` existed
  before anything it published (I-1).
  - An R1 holding `m_Y` would have had `Y` as a member, and would have decided
    it. But `Y` is still `ended`.
  - An R1 holding `g` would have failed condition 4 while `Y` was
    unconfirmed.
- **It breaks the conditions.**
  - `m_Y` is not forced: `K_c`'s own messages are distinct and present. So
    condition 4 fails.
  - `g` is not forced either: forced messages belong to confirmed attempts
    (section 7.2), and `g` belongs to `Y`. So condition 5 fails. If `g`
    happens to have text *k*, condition 4 already fails.
- **No assumption about untracked producers.** The argument makes none. An
  extra message is never forced, so it can only block absence.

**Example.** `alp-solver-2`'s multi-line part `Y` has the lines "north", ""
and "south". During `Y`'s window, the record shows:

- a message "north\n" from that account, not attributed and not forced;
- `Y`'s own earlier part, confirmed, whose message is forced to that part.

The first message blocks condition 5, so `Y` is undecided. Blank lines,
joined pieces, reordering, or any other shape are treated the same way, with
no model needed.

#### R3, UNDECIDED: everything else

The part stays `uncertain`, is shown in `pchat status` as awaiting a delivery
check, and is checked again each flush.

At `UNDECIDED_MAX`, E7 dead-letters the entry with its unconfirmed part texts
and per-part records (X1), and raises the content-free alert (X2). This
applies to any entry, whether direct, outbox or abandoned. It is never resent.

### 7.4 Conservatism, in brief

- **Identity.** Same verified account, same case-folded channel, exact text,
  time inside the window, msgid not yet attributed.
- **Untracked and competing producers.** R2 never relies on their absence. R1
  relies on it only for a designated account (C-7).
  - **Suspension.** At V1, a message of a designated account, timed after
    `designated_at`, is *unexplained* when no tracked attempt can explain it:
    - no attempt of its exact text key has a window containing it; and
    - no unconfirmed multi-line attempt of that account and channel has a
      window containing it.

    An unexplained message suspends the designation in the same `TX`: it
    sets `suspended_at`, increments `suspension_seq`, and inserts the alert
    row `suspended:<account>:<suspension_seq>` (section 3.6), which X2
    exports. A message for an account that is already suspended raises
    nothing. A replayed V1 is `INSERT OR IGNORE`, and suspends nothing new.
  - **What remains.** An identical untracked message inside a tracked window
    is indistinguishable. C-7 covers it only where the owner has designated
    the account; everywhere else it leaves the part UNKNOWN.
- **Coverage.** C-5. Absence needs coverage through `final_at + A`.
- **Absence recovery.** Kept where finality proves it, with no tags, echo,
  labeled responses or new history capability.
- **What stays UNKNOWN.** Each of these is visible, never resent, and
  dead-lettered with its text and progress after 24 hours:
  - indistinguishable identical text;
  - an unexplained same-account message near a multi-line part;
  - missing finality, including a bare timeout;
  - a live writer;
  - incomplete coverage.
- **Unrelated obligations keep flowing** (I-10).
- **A 26-hour outage keeps the evidence** (I-7).
- **No protocol experiments.**

### 7.5 Worked example

`alp-solver-2` posts a three-part question to `#alpha`. Parts 0 and 1 are
confirmed.

- **Case 1: a late PONG.** Part 2's PONG comes during the drain, with no
  error. That is A2. No part remains, so the entry is `done` (E5), and
  nothing is queued or resent.
- **Case 2: an error reply.** Part 2's PONG comes with a 5xx reply. That is A5
  with finality, and the entry is handed off.
  - Coverage reaches `final_at + 5 s` with no copy of the part and nothing
    unexplained: R2 rules it absent, and it is resent once.
  - Its message is there: R1 marks it delivered if the account is
    designated. Otherwise it stays undecided, and is dead-lettered at 24
    hours.
- **Case 3: a broken connection.** A5, without finality.
  - The account is designated and the message is logged: R1, delivered.
  - Otherwise it stays undecided. At 24 hours it is dead-lettered with part
    2's text and every part's record, and `pat` is alerted. It is never
    resent.

## 8. Mapping to #19

#19 is amended by the owner's 2026-10-09 amendment (section 11), whose text is
posted on the issue. The mapping is to the amended issue.

| #19 | Where it is met |
|---|---|
| R1, per-part outcome | A2, A5 and A4, and the part states. `PART_WAIT` is absolute, and the drain is bounded. |
| R2, no resend of a confirmed part | I-3; E3, H1 and Q0 queue only what has no committed attempt. |
| R3, fixed parts; legacy rows as today | E1 and E4; section 6.2. |
| R4, durable progress | A1 before bytes; A2 or A5 before the next part; A8 and A8c; I-5. A failure before any write queues the whole post once (Q0 on *X*, as today). An existing E1 entry is kept, and handed off by E3. |
| R5, reconciliation, as amended (D2, N6, N7) | R1, R2, R3, E7, X1 and X2. |
| R6, independent flow | I-10; bounded setup (section 5.1); bounded lock acquisition (section 2.3), so a paused `L_outbox` holder cannot stop database entries from flowing. C-8 states the database's single-writer limit. |
| R7, refusals | A3 and E6; N6 for a refusal that could not be recorded. |
| R8, visibility | `pchat status` from the database: waiting to post, awaiting a check, held rows, suspended designations, and import or export deferred by a busy lock. |
| R9, no exactly-once | Stated here. |
| R10–R11, R13 | Unchanged. |
| R12, contract documentation | Satisfied by D-72 and D-73, which are updated as needed. No new D-number. |
| R14, the SQLite authority | Sections 2–7. |
| R15, release packaging (second amendment) | Section 10.1. |
| Acceptance 1, 2, 4–6, 8–10 | Behavior unchanged. For acceptance 2, the test designates its fake account (D2). |
| Acceptance 3 | As amended (AD-2, corrected by the second amendment). The fixture gives part 3 a **non-refusal** error reply together with its matching completion PONG, complete coverage, and no unexplained message. Companion checks: a late PONG without an error is A2, confirmed and not resent; a FAIL is A3, dead-lettered and not retried; a bare timeout, crash or broken connection never proves absence. |
| Acceptance 7 | As amended (AD-1): the test recovers the SQLite crash state, with the same assertions. |

**Narrowings inside UNKNOWN.** Each of these ends as UNKNOWN, never as a
resend:

- **N1:** R1 needs an exact count, with no live writer and no dead member.
- **N2/N8:** an unexplained same-account message near a multi-line part
  blocks R2. An unconfirmed multi-line attempt blocks R1 for its account and
  channel inside its window. For an attempt without finality, that window
  never closes.
- **N4:** a timed-out call returns up to `DRAIN_WAIT` later.
- **N5:** a dead member, or one without finality, keeps an open window.

N6 and N7 are owner decisions (section 11).

**Acknowledgements** commit E1 and A1 before their bytes, and are retried
after an uncertain outcome as today, by A11. A repeated acknowledgement is
harmless.

## 9. Future tests

These are deterministic tests to write later. None is written or run as part
of this note.

**Setup.**

- `FakeServer`/`FakeConn`;
- injected wall and monotonic clocks;
- a fake process and socket table;
- a fake filesystem layer that can drop changes that were not `fsync`ed, to
  simulate power loss;
- `_isolation`;
- invented data only.

Every scenario runs on both transports, `line` and `multiline`, where they
differ.

1. **The round-5 regressions.**
   1. **Same flush.** A has a confirmed part and an uncertain part of one
      text; B has a delivered part of the same text; the account is
      designated. One flush resolves them all, with no resend.
   2. **Storage failure at confirmation.** A2 fails after part 1 of 3, through
      `pchat`, `notify` and `announce`. Nothing is requeued whole, part 1 is
      never rewritten, and the outcome is retried or ends UNKNOWN.
   3. **A direct post during a flush.** As in section 5.6, case 1.
   4. **A 26-hour outage.** The evidence survives.
2. **Untracked substitution.**
   - Undesignated: an identical untracked message leaves U UNKNOWN until
     its 24-hour dead letter.
   - Designated: an unexplained message suspends the designation and alerts
     once.
3. **Unique attribution.** Covers identical texts within and across entries,
   the UNIQUE constraints, and a crash between components.
4. **Finality.**
   - An error reply, and a late PONG during the drain, give finality.
   - A bare timeout, a write error, a gone writer and a closed connection
     each give no finality, and R2 never applies.
   - A FAIL during the drain is a refusal.
5. **Fragments (F1).** For a multi-line part with lines "north", "" and
   "south", each of these unexplained messages blocks R2 and R1:
   - "north\n";
   - "south";
   - "\n";
   - "south\nnorth";
   - a joined non-adjacent concat piece;
   - an unrelated text.

   A forced earlier part of the same post does not block.
6. **Acknowledgements (F2).** A failed ack, a restart, then A11 and a retry
   that succeeds. Also a crash before A11 and after it.
7. **Durable claim (F3).** A power loss after the rename without a directory
   `fsync`; after I2; after the unlink without a directory `fsync`. No
   occurrence is duplicated or lost.
8. **Reopenable progress (F4).** A delayed prefix, the import pass, then E8,
   G1 and H1: progress is kept, and H1 reopens the entry with its confirmed
   prefix. An unrelated abandoned entry is collected only after
   `import_epoch` advances.
9. **Export durability (F5).** For X1, a power loss after the append and
   before the `fsync`. For X2, a power loss after the rename and before the
   directory `fsync`. A `Deliveries._dead` write concurrent with X1. A
   pending item that leaves after its ledger line. (Directory durability on
   recovery is test 22.)
10. **Late outcomes (F6).** A paused writer resumes after E7's export: version
    2 is exported, and supersedes version 1, before G1 may collect anything.
    The entry stays terminal.
11. **Failure before E1 (F7), and ambiguous creation commits.** For `pchat
    post`, `pchat ack`, `notify` and `announce`:
    - a failed login;
    - a failed E1;
    - a crash before and after Q0;
    - Q0's fallback append, and power loss on either side of its `fsync`;
    - **an E1 commit that reports failure but lands.** P finds *X* and hands
      it off. Restart, then import.
    - **a Q0 commit that reports failure but lands, then a fallback row.**
      Import, restart, import again: one entry *X*.

    Each queues the whole post once, as today. Two separate calls with
    identical text are still two entries.
19. **Collection, then delayed reconciliation.** A confirmed attempt K has no
    msgid, and its message lies before the window of an unresolved attempt U,
    but K's window overlaps U's. Run G1 when K's message is older than
    `GC_MARGIN`, then reconcile U. K's message and attempt are kept, and U's
    verdict is the same as without G1.
20. **Bounded setup.** On an injected clock, the fake server trickles
    unrelated lines during each of these phases:
    - capability negotiation;
    - SASL;
    - the welcome;
    - NAMES or INVITE in channel creation.

    Each phase fails at its absolute deadline. Another entry in the same
    flush still gets its attempt.
21. **Release packaging (future, under R15).** The release inventory test ties
    markers in the shipped code to the new format entries:
    - `outbox.db` to the outbox store format, read and written;
    - the fallback rows to the outbox format version, read and written;
    - versioned post dead letters to the delivery records version;
    - each new host probe to its read-only format.

    The built payload contains every new runtime module. Every other format
    entry, and the release behavior, are unchanged.
12. **Crash at every boundary.** One case per row of section 5.5.
13. **A paused writer, and the liveness probes.** Never ended or retried. E7
    at 24 hours. A missing pid, a changed start time and a changed boot id are
    each proof of a gone writer. Unreadable probes prove nothing.
14. **Import.**
    - Duplicates, with or without the same explicit id, become distinct
      occurrences.
    - A changed hash is held.
    - Unsupported rows are held, with one alert row for their claim file
      (test 24).
    - **Fallback rows, by section 6.4's single shape.** A fallback row whose
      entry *X* does not exist creates *X* (`open`, `outbox`, no parts) and
      delivers the whole post once. A fallback row whose *X* exists as
      `open`, `direct` (or `abandoned`, within H1's guard) with a matching
      writer identity applies H1. A fallback row whose *X* exists but fails
      H1's identity guard is held. A fallback row whose *X* is already
      `outbox`, `done` or `terminal` is a no-op.
15. **Collection.** Messages near unresolved attempts are kept. Attributions
    go with their messages. Terminal rows stay until their current version is
    exported. Open-window dead attempts are kept.
16. **Coverage.** Each of these stops coverage, and R2 waits: KICK or PART, a
    disconnect, a stale reply, a failed V1, a checkpoint mismatch, a restart.
17. **Independent flow.** Another account, and the same account with another
    text, flow while one entry is undecided, refused or failing, and while
    `L_outbox` is held by a paused process (test 23).
18. **Unchanged paths.**
    - Legacy and ack entries.
    - A slow but confirmed post.
    - Silent-run suppression.
    - `pchat` exit codes and messages.
    - The amended #19 acceptance 1–10.
22. **Directory durability on recovery (design round 3, finding 1).** The
    fake filesystem keeps a file's content and its directory entry apart, and
    a simulated power loss drops whatever was not synced. In each case, a
    process crash comes first, then recovery, then a power loss:
    - **Dead letters.** X1 creates `dead-letters.jsonl`, appends, `fsync`s
      the file, and crashes before the directory `fsync`. The next X1 finds
      the record, syncs the file and the directory, then marks. Power loss,
      then restart: the record is present, and G1 had nothing to collect
      that was not durable.
    - **The ledger.** A `Deliveries` ledger append creates `deliveries.jsonl`
      and crashes before the directory `fsync`. X2 finds its key in the
      ledger, syncs the file and the directory, then marks. Power loss: the
      line is present.
    - **`Deliveries`' dead letters.** The same schedule for a delivery dead
      letter: the item stays pending until the line is durable.
    - **Fallback.** Another process creates `outbox.jsonl` without a
      directory `fsync`. P's Q0 fallback appends to it, syncs file and
      directory, exits 3. Power loss: the row is present and is imported as
      *X*.
    - **Recovered claims.** A claim file left unsynced by a crashed pass, or
      by the old tools, is synced before its I2. Power loss after I2: no
      occurrence is lost or duplicated.
    - **A negative control.** The same schedules with the directory `fsync`
      removed lose the record, so the test can detect the defect.
23. **A paused lock holder (design round 3, finding 2).** With a fake lock
    table and an injected monotonic clock:
    - A fake P holds `L_outbox` and never releases it. B's flush tries for
      `LOCK_WAIT`, skips import and export, and in the same flush delivers an
      unrelated open database entry for `#beta`, applies an E7 that falls
      due, and exports its alert row. `pchat status` shows import deferred.
    - B never removes or breaks the lock, and never ends P's attempt or
      connection.
    - P releases the lock: the next flush imports and exports as usual. P
      dies instead: the lock is released with it, and the next flush does
      the same.
    - A second direct writer's fallback append waits for the lock as today,
      and exits 3 only after its row is durable, once the holder releases
      it. The flush is not delayed by that waiting writer.
    - The flush's total time stays within the sum of its absolute deadlines
      while the lock is held.
24. **Alerts, exactly one per event (design round 3, finding 3).** For each
    kind (an E7 entry, a claim file with held rows, a changed claim file,
    and a suspended designation):
    - a crash before the event's `TX`, then a restart: one row, one item;
    - a crash after the `TX` and before X2: one item after restart;
    - a crash after the pending item is written, and after the ledger line,
      each before X2's mark: still one item, and the mark is set;
    - the import replayed after a crash before I2's commit: one alert row
      for the claim file;
    - a second unexplained message while already suspended: no new row;
      a later suspension after the owner lifts the first: a new row with
      the next `suspension_seq`.

## 10. Feasibility and risks

**Feasibility.** Everything uses the standard library's `sqlite3`, `os.fsync`,
and process and socket probes. The change keeps PR #20's:

- per-part posting;
- fixed parts;
- the refusal and legacy paths;
- the coverage model.

It replaces the file state with the database layer, and adds:

- the drain;
- the setup deadlines;
- the probes;
- durable file writes;
- the import epoch;
- bounded lock acquisition and the `alerts` table;
- the minimal release metadata (section 10.1).

That is about 1,000 to 2,000 changed lines, including tests.

### 10.1 Release packaging (second amendment, R15)

The release contract
([release_contract.md](release_contract.md#the-manifest)) requires
every persistent format, with the versions read and written, to be declared,
and every runtime module to be in the payload. So the future implementation,
in the same PR #20, makes these minimal declarations in
`packages/release/release.json`:

- **A new `state` format for the SQLite authority.**
  - Path: `$CHAT_STATE/outbox.db`, with its `-wal` and `-shm` files, and
    every other new lock or state file the implementation ships. That
    includes the flush lock `outbox.flush.lock` (`L_flush`), which master
    does not have (section 2.3). This follows correction 2 of the approving
    issue rereview, and matches how `release.json` already lists
    `outbox.lock` and `identities.lock` in their formats' paths. It adds no
    version.
  - Read and write versions: `outbox-db/1`.
  - Its version is embedded in the `meta` table's schema-version row.
- **The `outbox` format** (`outbox.jsonl`, `outbox.lock`, claim files). Its
  write version moves to `outbox/2`, which adds the fallback row; it reads
  `outbox/1` and `outbox/2`.
- **The `delivery-records` format.** Versioned post dead letters (`version`,
  `supersedes`) write `delivery-records/2`, and it reads `/1` and `/2`.
- **Every new runtime module** the implementation adds under
  `packages/chat/scripts` is listed in `package.files`. It is also recorded in
  `provenance.json` as a plateia-only file.
- **The interpreter note** says that the standard library's `sqlite3` module
  is required.
- **New host interfaces the probes read:**
  - a TCP socket listing, if it is not the existing `open-files` record;
  - the boot id.

  Each is a read-only `wire` format. Process start time is already declared as
  `process-table`.

Nothing else in the release changes: the builder, verification, staging, other
format entries, the skill, and the package version policy. If declaring these
needs any builder change, the implementer stops and asks.

**Acceptance 11's "unchanged".** Following correction 1 of the approving issue
rereview, acceptance 11 is read as: everything in `release.json` and the built
manifest except exactly the changes listed above (the edits to the `outbox`
and `delivery-records` entries, `package.files` and the interpreter note, and
the new `outbox-db` and host-interface entries) is byte-for-byte unchanged.

**Rollback is not claimed.** Releases before this one do not read
`outbox-db/1`. Rolling back across it is a breaking state migration, which
plateia_design.md's rollback principle reserves for an explicit owner
checkpoint and a reviewed recovery plan. That belongs to activation, which
stays excluded. The release declares the formats honestly, and claims no
rollback compatibility.

**Risks.**

- **More dead letters.** D2 without designation, N2 and N5–N8 add 24-hour dead
  letters in rare cases. Each has an alert, and none is a duplicate.
- **Probe portability** across macOS and Linux. An unreadable probe proves
  nothing.
- **`fsync` cost.** It falls on the outbox path only: rare appends, claims and
  exports.
- **Scope.** Every transport caller and `Deliveries`' writes change. The tests
  in section 9 guard them.
- **Activation.** Live import, quiescing the old writers and designation are
  separately approved.

## 11. Owner decisions (2026-10-09)

The owner approved these on 2026-10-09. They are recorded as D-73 in
[plateia_design.md](plateia_design.md), and as an amendment to #19.

1. **A stdlib SQLite authority.** One SQLite database, through Python's
   `sqlite3`, durably holds all outbox delivery state for plateia's copy.
   JSONL files are only import and export boundaries.
2. **Designated senders (D2).** Only designated sender accounts can have
   delivery confirmed from matching messages in the record (R1).
   - The designation itself, configuring which accounts are designated, is
     made only at a separately approved activation. Nothing is configured now.
   - Until then, and for any account that is not designated, a part whose
     identical message appears stays UNKNOWN, and is dead-lettered after 24
     hours.
3. **Conservative finality (N6, N7).**
   - Absence is proved only with attempt-specific server finality: the
     in-order reply on the attempt's own connection.
   - A crash, a broken connection, or no response through the drain can
     never be proved absent. A timeout never proves a message was not sent.
   - Missing finality does not prevent Delivered. When every designated-sender
     condition of R1 holds, the part is delivered (second amendment).
     Otherwise it stays UNKNOWN: preserved, and dead-lettered after 24 hours
     with its progress kept.
   - A refusal whose durable commit fails is UNKNOWN.
   - Confirmed parts are never resent, and independent obligations keep
     flowing.
4. **D1 is not adopted.** A dead direct writer's unsent remainder is not
   adopted. Its unknown parts still follow the 24-hour rule.
5. **Acceptance fixtures.**
   - **AD-1:** acceptance 7 recovers the SQLite crash state, not a claimed
     file, with the same behavioral assertions.
   - **AD-2:** acceptance 3's absence fixture needs attempt-specific server
     finality (a reply, an error, or a late PONG), not a bare timeout.

6. **Release packaging (second amendment).** #19's implementation includes
   the minimal release metadata and validation of section 10.1, in the same
   PR #20. That resolves the conflict between #19's "packages/chat only" scope
   and its release exclusion. Releases, staging, live migration and activation
   otherwise stay excluded, and no rollback compatibility is claimed. Requirement
   12 is satisfied by D-72 and D-73, with no new D-number.

Implementation is **not** approved by these decisions. It needs a separate
owner decision.

## 12. Revisions

- **Revision 1** (`46c470c`): design review round 1 requested changes.
- **Revision 2** (`69bf467`): design review round 2 requested changes, with
  seven findings open.
- **Revision 3** (`ba5d143`) addressed those seven:
  - **F1, fragments.** No fragment model is assumed. R2 requires every
    same-account, same-channel message in a multi-line part's window to be
    accounted for, attributed or forced, so blank-line, joined, reordered and
    any other fragments are covered. R1 is blocked inside any unconfirmed
    multi-line attempt's window.
  - **F2, ack retry.** A11 now sets the ack attempt to the final state
    `retired`, which releases A1's guard.
  - **F3, durable claims.** I1 makes the claim file and its directory entry
    durable before I2 can commit. The unlink is followed by a directory
    `fsync`, and every append is durable.
  - **F4, reopenable progress.** An abandoned entry is collectible only after
    an import pass that started after its abandonment. Entry rows are never
    deleted. (Revision 3 also said a handoff row for a missing entry is held.
    Revision 4's single fallback shape, section 6.4, superseded that: a
    fallback row for a missing entry creates it, because by I-1 nothing of
    that call was sent. Only a row failing H1's identity guard is held.)
  - **F5, export durability.** Every recovery path makes the sink durable
    before marking. Every `dead-letters.jsonl` writer, `Deliveries._dead`
    included, uses one protocol under `L_outbox`. The pending queue's
    lifecycle is durable. (Design round 3 found that recovery still skipped
    the directory `fsync`; revision 5 completes it.)
  - **F6, late outcomes.** Terminal exports are versioned. A late outcome
    exports a superseding version, and collection waits for the current
    version.
  - **F7, failures before E1.** Q0, with its durable fallback append, covers
    every caller.
- **Section 11** records the owner's decisions. D2, N6 and N7, AD-1 and AD-2
  are no longer open proposals.
- **Revision 4** (`a12d99c`) aligns the note with the owner's second #19
  amendment:
  - **Release packaging:** new section 10.1 and decision 6.
  - **Acceptance 3:** the fixture uses a non-refusal error with the matching
    PONG.
  - **Finality vs Delivered:** missing finality blocks Absent, not
    designated-sender Delivered.
  - **One identity per call:** the call's entry id *X* makes every ambiguous
    creation commit (E1 or Q0) and its fallback row a single obligation, and
    an existing E1 entry is handed off, not replaced.
  - **Collection:** by dependency component, including confirmed members whose
    message lies outside the unresolved window.
  - **Bounded setup:** absolute `LOGIN_WAIT` and `CHANNEL_WAIT` deadlines.
  - **Tests:** 19–21 added.
- **Design review round 3** requested changes on revision 4, with five
  findings.
- **Revision 5** (this revision) addresses those five, and carries the
  approving issue rereview's two corrections:
  - **Directory durability on recovery (P1).** Every append, and every
    recovery that finds a record already present, makes a durable sync of
    the file **and** its directory before anything is acknowledged or marked,
    whoever created the file (section 2.1). That covers X1, X2's ledger and
    pending paths, `Deliveries`' dead letters and ledger, fallback appends,
    and claims left by earlier passes or the old tools. "fsync" is the
    platform's full flush. Sections 3.5, 3.6, I-5, I-7, 5.5 and 6.3–6.4, and
    test 22.
  - **Bounded lock acquisition (P1).** The bridge and exports acquire
    `L_outbox` only within `LOCK_WAIT`, and no lock is ever stolen. A direct
    writer's fallback append still waits as today, delaying only its own
    call. A paused holder delays only import and export; every eligible
    database entry, E7 and the alerts still flow (section 2.3, I-10, section
    5.6 cases 9 and 10, test 23). C-8 states the database's single-writer
    limit, bounded by `BUSY_WAIT`.
  - **Alert identity (P2).** A new `alerts` table gives every alert, whether
    undecided, held or suspended, a key fixed by its event, inserted in the
    event's `TX`. X2 exports alert rows, and `entries.alert_exported` is
    replaced by the row's `exported_at` (sections 2.2, 3.6, 6.2, 6.3, 7.4,
    I-7, test 24).
  - **Baseline (P3).** Sections 2.3 and 6.1 and the Background say which code
    is PR #20's head `e658bb8` and which is master `a12d99c`. Section 10.1
    lists `outbox.flush.lock` and any other new lock or state file in the
    `outbox-db` path (rereview correction 2), and reads acceptance 11's
    "unchanged" as rereview correction 1 says.
  - **The fallback test (P3).** Test 14 now follows section 6.4's single
    fallback shape, and the revision 3 history above says what superseded
    it.
  - **Tests:** 22–24 added; 9, 14 and 17 updated.
