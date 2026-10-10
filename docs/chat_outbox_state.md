# Chat outbox state: one durable authority for partial-post delivery

This is the design note for #19 and pull request #20. It is design only:
nothing here is implemented, and implementing it needs a separate owner
decision.

This is revision 8. Revision 3 answered design review round 2. Revision 4
aligned the note with the owner's second amendment to #19:

- the release-packaging scope;
- the corrections and additions from the canonical issue review of the first
  amendment.

Revision 5 answers design review round 3: directory durability on every
recovery path, bounded lock acquisition, durable alert identities, the
baseline of the code it describes, and the fallback test. It also carries the
approving issue rereview's corrections.

Revision 6 adopts the owner's decision of 2026-10-10 after design review
round 4. **The bridge is the only process that opens the database.** Every
other caller asks it, over a local socket, to apply its transitions, and still
sends its own bytes to the chat server (section 2.4). So a paused client can
never hold a database lock, R6 holds as approved, and revision 5's C-8
exception is removed. While the bridge's authority is unavailable, posts,
acknowledgements and notices are queued durably instead of sent: a **material
behaviour change** the owner accepted (section 2.4.4). Revision 6 also bounds
the bridge's own `announce` fallback, and applies R2's message accounting to
every part.

Revision 7 adopts the owner's second decision of 2026-10-10, made after the
canonical issue review of #19's third amendment. That review found that a
fallback row already durable in `outbox.jsonl` still waited for a client
stopped while holding `outbox.lock`.

- **One fallback file per entry.** A queueing caller now publishes one
  complete, immutable fallback file per entry, and the bridge imports
  published files without taking `outbox.lock`. A stopped writer can delay
  only its own unfinished publication (sections 2.1, 6.3 and 6.4, and
  I-12).
- **Legacy `outbox.jsonl`.** It is still read, without the lock too. The
  lock only decides when a claimed file may be deleted.
- **The review's five corrections** are carried as well:
  - a writer's no-byte statement now survives the bridge ending its attempt
    first (A12, section 3.3);
  - an `announce` whose entry exists but whose bytes were never sent is
    still delivered after a restart (E9, section 6.4);
  - acceptance 11's comparison, the guidance, and the release suite's
    fixture builds are corrected (sections 8 and 10.1).

Revision 8 adopts the owner's third decision of 2026-10-10, made after the
canonical issue rereview of #19's fourth amendment. It adds three things:

- **Staging test support (option A).** The future implementation may
  minimally change the two staging test-support files, so their fixtures
  stay on the live formats. A new future test shows that #19's real release
  is refused at staging preflight against the live versions (section 10.1).
  Operational staging is unchanged.
- **That review's three wording corrections:**
  - when a fallback file is published;
  - what `L_outbox` still orders;
  - what "send nothing" means while the authority is unavailable.
- **Its three release and test additions** (sections 2.4.4, 6.4, 8 and
  10.1).

Section 12 lists what changed and why. The owner's decisions of 2026-10-09 and
2026-10-10 are recorded in section 11, and in D-73 of
[plateia_design.md](plateia_design.md). The 2026-10-10 decisions approve the
design, its scope and its review only. Implementation, code, tests and the
`release.json` edit still need their own owner decision.

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

- only the bridge opens it; every other caller asks the bridge to write
  (section 2.4);
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
11. [Owner decisions (2026-10-09 and 2026-10-10)](#11-owner-decisions-2026-10-09-and-2026-10-10)
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
| `LOCK_WAIT` | 5 s | The absolute deadline for the bridge to acquire `L_outbox` (section 2.3). |
| `OPEN_WAIT` | 5 s | SQLite's busy timeout on the authority's one connection. It bounds only the bridge's attempt, at startup, to take the database's exclusive lock (sections 2.4.1 and C-9). Once the authority runs, no other connection exists, and no transaction waits for a lock. |
| `FRAME_MAX` | 1 MiB | The largest request frame the authority reads (section 2.4.2). |
| `REPLY_MAX` | 64 KiB | The largest reply frame, including `status`. |
| `REQ_WAIT` | 2 s | From accepting a client connection to holding its complete request frame. Then the authority closes the connection (section 2.4.3). |
| `RESP_WAIT` | 2 s | From a reply being ready to its last byte being written. Then the authority closes the connection. |
| `CONN_MAX` | 32 | Open client connections. A connection beyond it is accepted and closed at once. |
| `QUEUE_MAX` | 256 | Complete client requests waiting to run. A request beyond it gets an immediate `busy` reply, with no transaction. |
| `IPC_WAIT` | 5 s | A client's absolute deadline for one request: connect, send, and read the whole reply. |
| `AUTH_WAIT` | 5 s | A bridge thread's absolute deadline for its in-process request to **begin**. A request that has not begun by then is cancelled, never run later (section 2.4.3). |
| `IMPORT_MAX` | 256 | The most fallback files one flush imports, oldest name first. The rest wait for the next flush (section 6.3). |
| `LINES_MAX` | 1,000 | The most legacy lines one import request reads from one claim file (section 6.3). |

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
- **Authority.** The bridge's authority thread: the only holder of the only
  connection to the database (section 2.4).
- **Client.** A process other than the bridge that causes transitions:
  `pchat post`, `pchat ack`, or `agentcli.notify` inside its host. A client
  never opens the database; it sends **requests** to the authority.
- **Fallback file.** One complete, immutable file in `outbox.d/` that
  carries one call's queued entry, or its handoff, when the authority did
  not answer as committed (section 6.4). It is **published** once its name
  appears in `outbox.d/`, and only complete, durable content is ever
  published (section 2.1).
- **Legacy row.** A line of `outbox.jsonl`, or of a claim file, in the old
  tools' format (section 6.2). This release writes none.
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
chat state directory, using Python's standard `sqlite3`. **Only the bridge
opens it**, through one connection held by its authority thread (section 2.4).
It replaces:

- the progress inside claimed outbox files;
- `outbox-reserved.jsonl`;
- `outbox-attributed.jsonl`;
- `record-coverage.json`.

**Settings** (on the authority's one connection, in this order):

- `locking_mode=EXCLUSIVE`, set before the first access to the database, so
  the connection keeps its file lock until it closes and SQLite uses no
  shared-memory index (section 2.4.1);
- WAL journal mode;
- `synchronous=FULL`, so a commit survives a power loss. On macOS, where
  `fsync` alone does not reach the disk, also `fullfsync` and
  `checkpoint_fullfsync`;
- the busy timeout `OPEN_WAIT`, which matters only at startup (C-9);
- **the database's own files are durable too.** SQLite's unix VFS syncs the
  directory when it creates a journal or WAL file. The implementation checks
  that for the SQLite it ships with. Otherwise it makes a durable sync of
  `outbox.db` and `outbox.db-wal` itself after opening, before the first
  commit anything relies on;
- every write is a `BEGIN IMMEDIATE` transaction, run by the authority thread
  only, so each decision is read and applied in one serialized transaction.
  The connection is opened with `isolation_level=None`, and the transaction
  is controlled by explicit `BEGIN IMMEDIATE`, `COMMIT` and `ROLLBACK`. That
  works on Python 3.10, and does not use 3.12's `Connection.autocommit`.

**No network I/O inside a transaction.** Logging in, sending, waiting,
draining and closing all happen between transactions. **Nor any other wait:**
no transaction contains a socket read or write, a file-lock wait, or a wait
for any other thread or process (section 2.4.3).

**Not a second authority.** These are boundaries, not sources of truth:

- **Imports.**
  - The fallback files in `outbox.d/` (section 6.4). Every queueing caller
    writes these, the bridge's `announce` included.
  - The legacy `outbox.jsonl` and its claim files
    `outbox.claimed-*.jsonl` (sections 6.2 and 6.3).

  A fallback file is deleted only after the database holds its content, so
  it is never the only record of anything the database has decided.
- **Exports.**
  - `dead-letters.jsonl`;
  - the owner alert, which goes on the bridge's pending-delivery queue,
    `pending.json`. The ledger `deliveries.jsonl` records delivery outcomes.

  Each is written after its terminal state commits, by the protocol in
  section 3.6.
- **The status snapshot.** `outbox-status.json`, which the bridge replaces
  after each pass for `pchat status` to read while the authority is
  unavailable (section 2.4.5). It is diagnostic only: nothing decides from
  it.
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
- **Publish** (a fallback file, section 6.4):
  1. create a new file under `outbox.d/tmp/` with exclusive creation
     (`O_CREAT | O_EXCL`), under a name that carries the writer's identity;
  2. write the whole content, then `fsync` the file;
  3. link it to its final name in `outbox.d/` with `link()`, which fails
     if that name exists and so never replaces a file. On that failure,
     choose a new final name and link again;
  4. `fsync` `outbox.d/`, then `fsync` the state directory, which also
     makes `outbox.d/`'s own entry durable, whoever created it;
  5. remove the `tmp/` name. This is cleanup only.

  A published name therefore always holds complete content that was durable
  before the name appeared. It is never written again. Only the bridge
  removes it, after importing it (section 6.3).
- **Recovery.** A record found already present, in any file, is durably
  synced (file and directory) before anything is acknowledged or marked on
  the strength of it. Seeing a record proves only that it is visible, not
  that it is durable.
- **The state directory.** The chat state directory exists before cutover. If
  this code ever creates it, the parent directory is synced too. The bridge
  creates `outbox.d/` and `outbox.d/tmp/` at authority startup, and a client
  that finds them missing creates them. Either way it syncs the parent
  afterwards.

So no acknowledgement (`pchat` exit 3 for a queued post) and no export mark is
ever committed for a record whose content or directory entry could still be
lost to a power failure.

### 2.2 Tables

The columns listed are the minimum the proofs rely on.

| Table | Key | Holds |
|---|---|---|
| `meta` | name | The schema version; `writer_model`, which is always `bridge-exclusive/1` (section 2.4.1); the cutover marker (section 6.1); `import_epoch`, the number of completed import passes; and `authority_epoch`, the number of authority starts (section 2.4.2). |
| `accounts` | account | `designated_at`, `suspended_at`, why it was suspended, and `suspension_seq`, the number of suspensions so far (R1, section 7.4). |
| `alerts` | `alert_key` | One row per alert event: its kind (`undecided`, `held` or `suspended`), what it refers to, its content-free text, when it was raised, and `exported_at` (NULL until X2 completes). See section 3.6. |
| `entries` | `id` | See below. Entry rows are **never deleted** (I-7), so a missing id always means no entry was ever created. |
| `parts` | (`entry_id`, `n`) | The kind, the wire lines, the server-visible text, `multi_line`, `state` (`unsent`, `inflight`, `uncertain`, `confirmed` or `refused`), the current generation, and the msgid once attributed. |
| `attempts` | `id` | `entry_id`, `n`, `generation`, the text key, `multi_line`, the writer identity, `conn_id`, `a1_nonce` (section 2.4.2), `state`, `written_at`, `final_at` (NULL without finality), `ended_at`, `end_kind` (`closed`, `writer_gone` or `connection_closed`), and a detail. UNIQUE (`entry_id`, `n`, `generation`). `state` is one of `writing`, `confirmed`, `refused`, `rejected`, `ended`, `delivered`, `absent`, `dead`, `retired` (an ack only) or `void` (A12). |
| `attributions` | `msgid` | `attempt_id` (UNIQUE), and when it was made. |
| `messages` | `msgid` | The channel, the account, the exact text and the server time, for verified messages only. |
| `coverage` | channel | `since`, `through` and `mark`, as PR #20 keeps them. |
| `imports` | file name: a claim file, or a fallback file | `state` (`open` or `closed`); the lines imported so far, and the byte offset after the last of them; the sha256 of those imported bytes; the epoch; and when the file was first seen and last imported. A fallback file's row is `closed` with its one import. A claim file's row stays `open` until the claim is retired (section 6.3). A name in this table is never used again. |
| `held` | (file name, ordinal), or the file name alone | The raw row, or a reference to the file, and why it was held. |

**What an entry row holds:**

- its kind (`post` or `ack`) and origin. The origin is `announce` only when
  the bridge's own in-process E1 or Q0 created the entry; no client request
  and no import can set it (E9);
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
| **P** (direct writer, a client) | a `pchat post` or `pchat ack` process; the `agentcli.notify` flow inside its host process | Never opens the database. By request to the authority (section 2.4): create its entry, or queue it before any byte (Q0); cause its own attempts' transitions; hand its entry to the outbox. When the authority does not answer as committed: publish a fallback file (section 6.4). It sends its own bytes. It never takes `L_outbox`. |
| **B** (bridge) | the single `chatbridge` service process | Run the authority, the only opener of the database (section 2.4). Index messages and advance coverage. Import fallback files and legacy rows, and apply handoffs. Flush outbox entries. Post its own `announce` lines, as a flow of B with its own entry and attempts, and adopt an earlier bridge process's unfinished announcements (E9). Reconcile. End attempts of a gone writer or a closed connection. Make entries terminal. Export (X1, X2). Collect evidence. Suspend designations. |
| **S** (status) | `pchat status` | Read only: a `status` request, or the snapshot when the authority is unavailable (section 2.4.5). |
| **O** (owner) | the owner, by explicit decision | Designate an account or lift a suspension. Only at a separately approved activation (section 11). |

| Guard | What it is |
|---|---|
| `TX` | A `BEGIN IMMEDIATE` transaction, run only by the authority thread. Every row condition is checked inside the transaction that changes the row. |
| `L_auth` | The non-blocking flock `outbox.authority.lock`, taken by the bridge before it opens the database and held for the authority's lifetime (section 2.4.1). A second bridge cannot take it, and so never opens the database or binds the socket. |
| `L_flush` | The non-blocking flock `outbox.flush.lock`. One flusher at a time, held for the whole flush, including import and export. This lock exists at PR #20's head `e658bb8`, but **not** on master `a12d99c`, whose `flush_outbox` takes no flush lock. The implementation adds it, and declares it in the new `outbox-db` format's path (section 10.1). |
| `L_outbox` | The flock `outbox.lock`, which exists on master (`chatlib.py`'s `_outbox_locked`) and at `e658bb8`. In this design it orders only two things: every write to `dead-letters.jsonl` (section 3.6), and the **retirement** of a legacy claim file (section 6.3). No client of this release takes it, and **no import waits for it**. The old tools take it to append to `outbox.jsonl` (`chatlib.py:391` on master). The bridge acquires it only with a bounded wait, and never on the authority thread. Within the legacy outbox its one role is that retirement barrier. It still serializes dead-letter writes within `LOCK_WAIT`, and a busy lock defers them (the canonical issue rereview of #19's fourth amendment, correction 2). |
| **owner** | The acting flow's writer identity equals the row's. |
| **gone**, **conn-closed** | Section 5.4. |

**Bounded lock acquisition.** The bridge acquires `L_outbox` by repeatedly
trying a non-blocking lock until the absolute deadline `LOCK_WAIT`. If the
deadline passes:

- **The bridge** skips, in this flush, only the steps that need `L_outbox`:
  - the retirement (I3) of legacy claim files;
  - X1;
  - `Deliveries`' dead-letter appends.

  It continues with everything else, including **every import**: fallback
  files (IF), the legacy claim (I1) and the legacy rows (I2), all without
  `L_outbox` (section 6.3). Then every eligible database entry's attempts
  (E4, A1–A5), reconciliation, A8 and A8c, E7, alerts (X2, which needs only
  `L_flush`), and collection. The skipped steps run at a later flush.
  `pchat status` shows that retirement or export is deferred because the
  lock is busy, and since when.
- **No client of this release takes `L_outbox`.** A client queues by
  publishing a fallback file, which needs no lock (section 6.4). The canonical
  issue review of #19's third amendment found that revision 6's fallback
  append to `outbox.jsonl` left an already durable row waiting behind a
  stopped lock holder. That append is gone.
- **No bridge flow appends to `outbox.jsonl`.** The bridge's own
  `announce` falls back to a fallback file too (section 6.4).
- **Who can still hold `L_outbox`:** the bridge itself, for those bounded
  steps, and old tools outside this release, such as a legacy `pchat`
  appending a row. A holder stopped mid-append delays only its own row,
  which has no newline yet, and the deletion of claim files. It never
  delays the import of any complete row or published file (I-12).

**No lock is ever stolen.** Nobody breaks, removes or ignores another
process's `L_outbox` or `L_flush`, and nobody ends a writer's attempt or
connection because it holds a lock. A holder that resumes finishes its step;
a holder that dies releases the flock with its process.

### 2.4 Routing: the bridge is the only writer

Revision 5 let every caller open the database. Design review round 4 showed
the consequence: a client stopped by the operating system inside its own
`BEGIN IMMEDIATE` transaction blocks every bridge write, so an unrelated
entry gets no attempt until that client resumes or dies. Revision 5 called
that C-8 and exempted it from R6. The owner rejected the exemption
(2026-10-10). Revision 6 removes the cause instead: **no process but the
bridge ever opens the database**, so no client can hold a database lock at
any instant.

| Caller (source at PR #20's head `e658bb8`) | Transitions it causes | Route |
|---|---|---|
| `pchat post` (`pchat:223-247`, `chatlib.py:432`) | Q0, E1, A1–A5 with E6, E3, E5, A12 | request over the socket |
| `pchat ack` (`pchat:251-257`, `chatlib.py:566`) | Q0, E1, A1–A5, A11, E5, A12 | request |
| `agentcli.notify` (`agentcli.py:17-38`) | as `pchat post` | request |
| the bridge's `announce` (`chat-bridge:678-690`, called from `Deliveries.process` at `:428` and `:446`) | as `pchat post`; after a bridge restart, E9 adopts its unfinished entry | in-process request; a fallback file when the request is not answered as committed |
| the bridge's flush, import, reconciliation, evidence, alerts and collection | every other transition | in-process request |
| `pchat status` | none | `status` request, or the snapshot |

Clients keep their own connections to the chat server, and send their own
bytes. Only their database writes move.

#### 2.4.1 Startup and the exclusive opener

The bridge starts its authority in this order:

1. **Take `L_auth`** (`outbox.authority.lock`) without blocking. If another
   process holds it, this bridge opens nothing, binds nothing, logs it and
   exits.
2. **Open `outbox.db`** with the settings of section 2.1, setting
   `locking_mode=EXCLUSIVE` before anything reads the database. Read back
   `PRAGMA locking_mode` and `PRAGMA journal_mode`. Anything other than
   `exclusive` and `wal` closes the connection; the authority is then
   unavailable.
3. **The start transaction.** One `BEGIN IMMEDIATE` transaction, under the
   busy timeout `OPEN_WAIT`:
   - creates the schema, with `writer_model = 'bridge-exclusive/1'`, if the
     database is new;
   - otherwise requires `writer_model = 'bridge-exclusive/1'`. A database
     without it was made by other code, and is refused, never adopted or
     rewritten;
   - increments `authority_epoch`.

   Its commit takes the database file's exclusive lock, which the connection
   holds until it closes. From then on, any other process that opens the file
   gets `SQLITE_BUSY` at once, and holds nothing.
4. **If step 2 or 3 fails**, because the lock is held (C-9), the database is
   refused, or storage fails, the bridge closes the connection and binds
   nothing. It records "authority unavailable", the reason, and since when,
   in its log and the status snapshot, and tries again at the next pass. It
   never deletes, renames or breaks another holder's lock.
5. **Bind the socket** `$CHAT_STATE/outbox.sock`. Holding `L_auth`, it
   removes a stale socket file, binds with `umask 077`, checks the mode is
   `0600`, and listens.
   - **Path limit.** An AF_UNIX path may hold at most 103 bytes on macOS
     and 107 on Linux. If the absolute path is longer, the bridge binds
     nothing. Its authority serves the bridge alone, every client call queues
     (section 2.4.4), and the status says why. Clients compute the same path
     and limit, and queue without trying to connect.

Until step 3 commits, the bridge makes no database write. V1 fails safely
(V4: coverage stops, and the record replays later), and `announce` publishes
a fallback file (section 6.4).

After step 3, and before the socket is bound, the bridge makes sure
`outbox.d/` and `outbox.d/tmp/` exist, syncing the state directory if it
creates them (section 2.1).

#### 2.4.2 Requests

**Framing.** A client opens a connection, sends one frame, reads one reply
frame, and closes. A frame is a 4-byte big-endian length, then a UTF-8 JSON
object, at most `FRAME_MAX` for a request and `REPLY_MAX` for a reply.

**A request** carries:

- `v`, the protocol version, `outbox-authority/1`;
- `op`;
- `request_id`, random per request;
- `writer`: boot id, pid, process start time, and `conn_id` when the call has
  a chat connection;
- the op's arguments.

**A reply** carries `v`, the same `request_id`, `authority_epoch`, a
`result`, and the op's data. The result is one of:

- `ok`: the transaction committed;
- `replay`: the requested change had already committed, and the reply
  restates it;
- `conflict`: the guard failed, and nothing changed;
- `refused`: the request was invalid, of an unknown version, or from the
  wrong peer, and no transaction ran;
- `busy`: `QUEUE_MAX` was reached, and no transaction ran;
- `error`: the transaction failed. Whether it committed is **unknown**, and
  the client treats the reply as missing.

**Replies follow a durable commit.** The authority writes `ok` or `replay`
only after `COMMIT` has returned, under `synchronous=FULL` and the full
flush. So a reply never reports a change that a crash or power loss could
undo.

**Peer check.** On accepting a connection, the authority reads the peer's
uid from the socket: `LOCAL_PEERCRED` (`getpeereid`) on macOS, `SO_PEERCRED`
on Linux.

- A different uid, or one that cannot be read, closes the connection unread.
- Where the platform also gives the peer's pid, a request whose
  `writer.pid` differs is `refused`.

This checks that a request speaks for its own sender. It is not a security
boundary: any process of the same user can already read and write the state
directory. The tests fake these credentials (section 9).

**Ops.** Every op is idempotent, against the current rows:

| `op` | Transition | Guard, and when it is a `replay` |
|---|---|---|
| `create` | E1 for entry *X*, with its fixed parts | *X* does not exist. If *X* exists with this writer and the same parts: `replay`, with its state. Otherwise `conflict`. |
| `queue` | Q0 for *X* | *X* does not exist. If it exists: `replay`, with its state (a `direct` *X* is then handed off, as Q0 says). |
| `attempt` | A1 for (*X*, *n*), naming the expected current generation *g* and a fresh random `a1_nonce` | A1's guard; creates generation *g* + 1. If that attempt exists with this writer and `a1_nonce`: `replay`. Otherwise `conflict`. |
| `outcome` | A2, A3 with E6, A4 or A5, naming the attempt, its `a1_nonce`, the outcome, `final_at` and the detail | The attempt is `writing`, with this writer and `a1_nonce`. If it is already in that outcome, with the same fields: `replay`. Otherwise `conflict`, which changes nothing. |
| `retire` | A11 | A11's guard; `replay` if this writer's attempt is already `retired`. |
| `void` | A12 | A12's guard (section 3.3), which also accepts an attempt that A8 or A8c has already ended; `replay` if already `void`. |
| `handoff` | E3, with at most one A12 or one attested outcome in the same `TX` (section 6.4) | E3's guard, and the attestation's own guard; `replay` if *X* is already `outbox`. |
| `done` | E5 | E5's guard; `replay` if `done`. |
| `query` | none | Returns *X*'s entry, its parts, and this writer's attempts. |
| `status` | none | The status summary (section 2.4.5). |

A1's `written_at` is the authority's clock at that commit. The client sends a
part's lines only while holding that part's committed `attempt` reply, so
every byte follows `written_at` (C-1 is unchanged: one machine, one clock).

**No op lets a client cause a bridge transition.** E2, H1, E4, E7, E8, E9,
A6–A10, V1–V4, IF, I1–I3, X1, X2, G1, alert rows and suspensions are the
bridge's own, through in-process requests.

**Epochs.** `authority_epoch` grows by one at each authority start. Within one
call, a client:

- keeps the highest epoch it has seen, and treats a reply with a lower one
  as missing;
- after a reply whose epoch is higher than an earlier one in the same call,
  sends `query` before its next mutation, and continues only from the state
  that reply shows.

A higher epoch means the authority restarted. A reply with a lower epoch
could only be a dead authority's buffered reply. Even that reply is true,
because it followed a durable commit, but the client does not build on it.
Every mutation is conditional and safe to replay, so a stale assumption is
never applied.

**A missing reply proves nothing.** It is never taken as evidence of a
commit, of a failure, of absence, or of an ended attempt.

#### 2.4.3 The authority thread

**One thread, one connection.** The authority thread owns the only
connection to `outbox.db`. No other thread of the bridge, and no other
process, calls SQLite on it.

**Its loop** uses `selectors` over:

- the listening socket;
- the client sockets, all non-blocking;
- a wake-up pipe for the in-process queue.

Each turn, it:

1. accepts connections, and closes any beyond `CONN_MAX` at once;
2. reads the bytes available, and closes a connection whose frame exceeds
   `FRAME_MAX`, or is still incomplete `REQ_WAIT` after its accept;
3. checks each complete frame (peer, version, shape), and queues it. A frame
   beyond `QUEUE_MAX` gets `busy`;
4. runs at most **one** in-process request and **one** client request,
   alternating, in arrival order;
5. writes the reply bytes the sockets accept, and closes a connection after
   its last byte, or `RESP_WAIT` after its reply was ready.

**A transaction begins only for a complete, validated request, and ends
(`COMMIT` or `ROLLBACK`) before its reply is written.** No transaction waits
for a socket, a flock, another thread or another process.

**Bounded work.** A client request touches only the rows its frame names,
within `FRAME_MAX`. The bridge splits its own work into bounded requests:

- one fallback file per import request, and at most `IMPORT_MAX` per
  flush;
- at most `LINES_MAX` legacy lines per import request;
- one reconciliation component per request;
- at most 1,000 rows per collection request.

**In-process requests.** Each bridge thread has at most one outstanding. A
request's state moves from `queued` to `running` to `done`, or from `queued`
to `cancelled`, under one mutex:

- the authority moves it to `running` just before `BEGIN`;
- the caller cancels it if it is still `queued` at `AUTH_WAIT`;
- a cancelled request committed nothing. The caller treats it as a failed
  `TX`: it sends nothing, and tries again at a later pass;
- once it is `running`, the caller waits for `done`. The transaction's
  duration then depends on local storage only (C-10), never on another actor.

**The bridge's threads.**

- **The main thread** runs IRC, `Deliveries`, the flush and `announce`. It
  never calls SQLite.
- **The authority thread** never talks to the chat server, never waits for
  `L_outbox` or `L_flush`, and never blocks on a client socket.
- **Shared helpers** that both threads use, such as the bridge's log, are
  made thread-safe.

**Memory** is bounded by `CONN_MAX` × (`FRAME_MAX` + `REPLY_MAX`), plus the
queues.

**Fair progress.** A bridge request waits behind at most one client
transaction. Every client request is answered within `REQ_WAIT`, plus at
most `QUEUE_MAX` client transactions and as many bridge transactions, plus
`RESP_WAIT`. Otherwise it is refused or closed,
and the client queues (section 2.4.4). I-11 gives the proof, and section 5.6,
case 11, gives a schedule.

#### 2.4.4 When the authority is unavailable

A client treats the authority as **unavailable** when:

- the socket path exceeds the limit;
- the connection fails;
- it is refused;
- the reply is `busy` or `error`;
- no complete reply arrives within `IPC_WAIT`. It then closes the
  connection, and reads nothing after that.

Then:

- **It sends no line of a part or acknowledgement without a committed
  `attempt` reply.** That covers `BATCH`, `PRIVMSG` and `TAGMSG` lines, and
  the channel recreation that follows a 403. Lines go out only while it holds
  the committed reply for that part. A channel recreation goes out only after
  the rejected attempt's A4 outcome is committed too. If that `outcome`
  request is not answered, the call recreates nothing, and hands off with the
  attested outcome.
- **Connecting and logging in are not part of any post.** A call may check
  the authority, connect and log in before E1, as E1's "after logging in"
  and Q0's "a failed login or setup" already say. That publishes nothing to
  any channel. "Sends nothing to the chat server" in #19's third amendment
  means no such line (the canonical issue rereview of #19's fourth
  amendment).
- **Before any byte of the call,** it publishes the fallback file (section
  6.4) for *X*, durably, and only then reports the post as queued (exit 3).
  If an `attempt` request went unanswered, the file carries an A12
  attestation for that attempt.
- **After some bytes,** it stops the call and hands off the remainder, by a
  `handoff` request if the authority answers, otherwise by a fallback file
  that carries the handoff. Then it exits 3, as today.
- **An observed outcome whose `outcome` request goes unanswered** is
  retried, as the same replay-safe request, until `PART_WAIT` after the
  outcome was observed. Then it travels as an attested outcome in the
  handoff (section 6.4).
- **Nothing is reported as queued before its file is published and
  durable** (section 2.1, I-5).

**MATERIAL BEHAVIOUR CHANGE, accepted by the owner on 2026-10-10.** Today a
direct post reaches the chat server whether or not the bridge runs. Under
this design, while the authority is unavailable:

- posts, acknowledgements and notices are queued in full instead of sent;
- `pchat` exits 3 instead of 0;
- the bridge delivers them when it returns.

The authority is unavailable while the bridge is stopped or restarting,
refusing its database or socket, or overloaded past `IPC_WAIT`.

Delivery preservation is not reduced. Nothing is lost, R9's obligations
hold, and the fallback files are durable and imported idempotently by *X*,
without any lock a client could hold (section 6.3). But
those posts are delayed, and invisible in the channel, until the bridge
returns, so this design does **not** claim to be equivalent to today.

No smaller design keeps direct sending with one authority and no new service:

- **A client that opens SQLite while the bridge is down** brings C-8 back the
  moment the bridge restarts behind its transaction.
- **A client-side journal** would be a second, concurrently written
  authority.
- **A separate authority daemon** is a new service, and still queues while it
  is down.

#### 2.4.5 Status

`pchat status` sends a `status` request. The reply gives, within
`REPLY_MAX`:

- entries waiting to post, and entries awaiting a delivery check (R8);
- held rows;
- suspended designations;
- fallback files and legacy rows not yet imported, and deferred claim
  retirement or export;
- the authority epoch.

If the authority is unavailable, `pchat status` reads `outbox-status.json`
instead. The bridge replaces that file atomically (a temporary file, then a
rename) after each pass, and whenever its authority becomes unavailable. It
holds:

- the same summary;
- the time it was written;
- the epoch;
- the reason for any unavailability, and since when.

`pchat status` shows the snapshot's age. It also counts, read-only:

- the fallback files published in `outbox.d/`;
- the legacy rows waiting in `outbox.jsonl` and in claim files.

The snapshot is diagnostic, and nothing decides from it.

## 3. State and transition tables

Every transition is a conditional update inside one `TX`:
`UPDATE … WHERE id = ? AND state = ? [AND generation = ?]`. A change that
matches no row has no effect, and the actor stops work on that attempt or
entry.

**Who applies them.** Every `TX` below runs on the authority thread (section
2.4). The *Actor* column names who causes the transition: P by a request
over the socket (section 2.4.2), B by an in-process request. "Owner" checks
compare the request's writer identity, and for an attempt its `a1_nonce`,
with the row's.

**Export version.** Any change to a part or attempt of a `terminal` entry
(A2–A5, A8 or A8c on an attempt still `writing`) increments the entry's
`export_version` in the same `TX` (F6).

### 3.1 Entry

| # | From | To | Actor | Guard | Durable write (one `TX`) | After commit |
|---|---|---|---|---|---|---|
| Q0 | (none) | `open`, `outbox`, no parts | P (`queue` request), or B for `announce` | Entry *X* does not exist. The failure came before any byte was written: no connection, a failed login or setup, or a failed E1. Silent-run refusal is checked first and commits nothing. | `INSERT` entry *X* with the post's or ack's legacy fields and no parts. If *X* already exists, because an E1 whose reply was lost had in fact landed, nothing is inserted, and P runs E3 on *X* instead. If this `TX` is not answered as committed, P publishes a fallback file (section 6.4), and so does B's `announce`. | exit 3, as today: the whole post is queued once |
| E1 | (none) | `open`, `direct` | P (`create`), or B for `announce` | after logging in | `INSERT` entry *X*, and its parts as `unsent`, fixed now from the connection's capabilities. An ack has one pseudo-part. | A1 for part 0 |
| E2 | (none) | `open`, `outbox` | B, by import | `L_flush` | The entry with its occurrence identity, committed with the `imports` row. | the file's removal (section 6.3) |
| E3 | `open`, `direct` | `open`, `outbox` | P, owner (`handoff`) | — | `owner_kind = 'outbox'` (the handoff), with at most one A12 or attested outcome (section 6.4). | exit 3, as today |
| H1 | `open`, `direct`; or `abandoned` with `import_epoch ≤ abandon_epoch + 1` | `open`, `outbox` | B, by importing a fallback file that carries a handoff | `L_flush`. The file's entry id **and** writer identity equal the entry's. | `owner_kind = 'outbox'`, and `state = 'open'` again if it was `abandoned`. | none |
| E4 | `open`, `outbox`, parts not yet fixed | the same, with parts | B | `L_flush`; no parts exist | The fixed parts, `unsent`. A legacy post's "(delayed; written …)" marker is fixed here, once. | A1 |
| E5 | `open` | `done` | P, owner; or B | every part is `confirmed` | `done` | none |
| E6 | `open` | `terminal` (refused) | the writer recording A3 | — | The terminal reason, in A3's `TX`; `export_version = 1`. | X1 |
| E7 | `open` or `abandoned` | `terminal` (undecided) | B | Some part's attempt, `writing` or `ended`, is undecided more than `UNDECIDED_MAX` after its `written_at`. | Terminal; `export_version = 1`; the alert row `outbox:<entry id>` (section 3.6). An `ended` attempt becomes `dead` (A10). A `writing` attempt is **left** `writing`. | X1, X2 |
| E8 | `open`, `direct`, origin not `announce` | `abandoned` | B | The owner is **gone**, and none of its attempts is `writing` (A8 first). | `abandoned`, `abandon_epoch = import_epoch`. Its unsent remainder is not adopted (D1). Its unknown parts still go to E7. | none |
| E9 | `open`, `direct`, origin `announce` | `open`, `outbox` | B | The owner, an earlier bridge process, is **gone**, and none of its attempts is `writing` (A8 first). | `owner_kind = 'outbox'`: the bridge adopts its own earlier announcement, so its unsent parts are flushed (section 6.4). | the flush |

**One identity per call.** Each `pchat post`, `pchat ack` or `notify` call
generates one random entry id *X* before its first request. An `announce`
derives *X* from its pending item's key and its reason (section 6.4), so a
re-raised announce is the same call. E1, Q0, E3 and every fallback file of
the call name *X*, and `entries.id` is the primary key.
So whichever creation lands first wins, and every later attempt to create *X*
is a no-op or a handoff, never a second obligation. Two separate calls, even
with identical text, have distinct ids, so they stay distinct occurrences
(F5 of the issue review).

`done` and `terminal` are final. An `abandoned` entry leaves only by:

- H1, until the second import pass after its abandonment completes (I-7
  explains why that is the only window in which a handoff file can exist);
  or
- E7.

An `announce` entry is never abandoned. When the bridge process that owns it
is gone, E9 hands it to the outbox instead, so its unsent parts are still
delivered. That keeps D1 for ordinary direct callers only (section 6.4).

### 3.2 Part

| # | From | To | Caused by |
|---|---|---|---|
| P1 | `unsent` | `inflight` | A1 (generation + 1) |
| P2 | `inflight` | `confirmed` | A2 |
| P3 | `inflight` | `refused` | A3 |
| P4 | `inflight` | `unsent` | A4, the server's rejection before acceptance; A11, an ack's retry; or A12, an attempt that wrote no byte |
| P5 | `inflight` | `uncertain` | A5, A8 or A8c |
| P6 | `uncertain` | `confirmed`, msgid set | A6 |
| P7 | `uncertain` | `unsent` | A7, proved absence |
| P8 | `confirmed` | `confirmed`, msgid set | A9 |
| P9 | `uncertain` | `unsent` | A12 on an attempt that A8 or A8c had already ended: its writer's attestation that no byte was sent (section 3.3) |

`confirmed` and `refused` are final. A post's part returns to `unsent` only by
P4, P7 or P9.

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
| A12 | `writing`; or `ended` by A8 or A8c (`end_kind` `writer_gone` or `connection_closed`) | `void` | the attempt's own writer's attestation: P's `void` or `handoff` request or fallback file (section 6.4), or B's in-process request or fallback file for its own attempt whose A1 result it did not accept | **All of:** the attempt is `writing`, or `ended` by A8 or A8c, never by A5 and never in a later state; its entry id, part, generation, writer identity and `a1_nonce` equal the attestation's; it is the part's current generation, and the part is `inflight` or `uncertain`; no attribution names it; the entry is `open`, or H1 reopens it in the same `TX`; and the attestation says the writer sent no byte of this attempt. | `void`; `ended_at` if unset; the part goes back to `unsent` (P4 from `inflight`, P9 from `uncertain`). | none: no byte of it was ever sent |

`delivered`, `absent`, `refused`, `rejected`, `dead`, `retired` and `void` are
final. A `dead` attempt remains evidence (section 7).

**Why A12 is safe.** A writer sends a part's lines only while it holds a
committed `attempt` reply for that part that it accepted (section 2.4.4). It
accepts no missing, `error` or lower-epoch reply (section 2.4.2). A writer
that accepted no such reply has therefore sent nothing for that attempt.

Only that writer can say so. The attestation must match the attempt's writer
identity, generation, and the random `a1_nonce` that only the writer and the
database hold. A12 is never inferred from a missing reply, a timeout or a gone
writer. Without an attestation, such an attempt stays `writing` until A8 or
A8c, and then `ended`, as before. A `void` attempt published nothing, so it
is not a reconciliation member (section 7.2).

**When recovery runs first** (the canonical issue review of #19's third
amendment). Take an A1 that commits, but whose reply is lost. The writer
closes its chat connection, and A8c, or A8 after it exits, ends the attempt
without finality. Only then is its handoff, carrying the attestation,
imported. A12 still applies, from `ended`, so the call keeps one deliverable
obligation instead of an UNKNOWN part. This is sound for three reasons:

- **What the attestation states.** It concerns the writer's own sends, and
  A8 and A8c change none of them. They record only that the bridge could no
  longer prove whether anything was sent.
- **Fencing is unchanged.** A closed connection or a gone process cannot
  send. Voiding says only that nothing was sent before, which the writer
  alone knows.
- **Finality is unchanged.** A12 is not an absence verdict. R2 never
  decides an attempt without finality, and nothing here infers absence from
  the record.

The guard admits only A8's and A8c's ends. A5 is the writer's own record that
it wrote and then closed, so an attempt ended by A5 is never voided. An
attempt already `delivered`, `absent` or `dead`, or one with an attribution,
has been decided and is never voided. An entry already `terminal` stays
terminal, so an attestation arriving after E7's 24 hours changes nothing.

**Epochs.** No epoch condition is needed, and none is applied. The statement
is about the writer's own sends, which no authority restart changes. The
writer identity already separates bridge processes, so a restarted bridge
can never attest for its predecessor's attempts. The epoch's role is earlier:
a writer that saw a reply with a lower epoch did not accept it (section
2.4.2), sent nothing on it, and may attest so.

**Every unaccepted A1 gets an attestation.** Whenever a writer holds an A1
whose result it did not accept, it sends nothing for that attempt, and makes
its attestation durable:

- **in order of preference:** by a `void` request, by the `handoff` that ends
  its call, or else by a fallback file (section 6.4);
- **the bridge too,** for its own `announce` and flush attempts, by
  in-process request or else by a fallback file;
- **a crash first:** if the writer crashes before any of these is durable,
  the attempt stays as A8 leaves it, UNKNOWN. That is conservative, and it
  makes no unsupported absence claim.

**Attested outcomes.** A writer whose `outcome` request was never answered
may carry that outcome (A2, A3, A4 or A5, with its `final_at` and detail) in
its handoff (section 6.4). The authority applies it under exactly the
`outcome` request's guard: the attempt is still `writing`, with the same
writer, generation and `a1_nonce`. It is the same statement the writer would
have made by request, only made durable another way. If the attempt has
already moved, for example by A8, an attested outcome changes nothing. It
is not applied from `ended` as A12 is: an observed outcome without the
writer's commit stays as A8 left it, decided by R1 or E7.

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
| IF | Import a fallback file | B | `L_flush`; `TX`. **No `L_outbox`.** | At most `IMPORT_MAX` published files per flush, oldest name first. For each: a durable sync of the file and `outbox.d/`; then, in **one** `TX`, the file applied by its *X* (section 6.4) with its closed `imports` row; then the file unlinked and `outbox.d/` synced. A crash in between re-imports by *X* or finds the `imports` row: a no-op (section 6.3). |
| I1 | Claim the legacy outbox | B | `L_flush`. **No `L_outbox`.** | Rename a non-empty `outbox.jsonl` to a new unique `outbox.claimed-<ns>-<random>.jsonl`. The new name is not in `imports` and does not exist on disk. Then a durable sync of **every** claim file present: the new one, and any left by an earlier pass, a crash, or the old tools before cutover (F3; design round 3). |
| I2 | Import legacy rows | B | `L_flush`; `TX`; only after I1's durable sync of that claim file. **No `L_outbox`.** | The complete lines after the claim's imported offset, at most `LINES_MAX`: their occurrences, held rows and any alert row (section 6.2), with the claim's `imports` row advanced, in **one** `TX`. The prefix already imported must be unchanged, or the claim is held (section 6.3). |
| I3 | Retire a claim file | B | `L_outbox` acquired within `LOCK_WAIT` at some moment **after** B first recorded the claim, then a final I2 that reads through the end of the file | That final I2 also closes the `imports` row. Then the claim file is unlinked and the directory synced. |
| X1 | Export a dead letter | B | `L_outbox`, acquired within `LOCK_WAIT` | Section 3.6. |
| X2 | Export an alert row | B only | `L_flush` | Section 3.6. |
| G1 | Collect evidence | B | `L_flush`; `TX` | The deletions I-7 allows. |
| X3 | Write the status snapshot | B | none | Section 2.4.5. Diagnostic only. |

### 3.6 Export and sink protocol

**Every writer of `dead-letters.jsonl` follows the same protocol under
`L_outbox`** (F5). The writers are X1 and the bridge's existing delivery
dead letters (`Deliveries._dead`), both of B.

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
| `held` | I2 or IF, or the hold of a changed claim file or fallback file (section 6.3) | `held:<file name>`, or `held:<file name>:<sha256>` for a changed file | "queued outbox rows could not be imported and are held; see pchat status" |
| `suspended` | the V1 that suspends a designation (section 7.4) | `suspended:<account>:<suspension_seq>` | "sender designation of alp-solver-2 suspended: an unexplained message in #alpha" |

A claim file or fallback file name is never reused, an entry reaches E7 once,
and `suspension_seq` increases by exactly one in the `TX` that suspends. So each
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
part or an ack is sent, and before any channel recreation for it. A failure
before that point writes nothing: Q0 only queues the call. Connecting and
logging in come before E1, and publish nothing (section 2.4.4).

**Why it holds.** Posting and acknowledging both go through the attempt
protocol, including today's separate `chatlib.ack` send. A1 is the only way
to obtain an attempt, and its writer sends only after the commit. A client
sends only while it holds the committed `attempt` reply, which the authority
writes only after `COMMIT` has returned (section 2.4.2). A missing, late or
`error` reply means no bytes.

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
- **Routing changes no fence** (section 2.4). A request timeout, a closed
  request connection, a `busy` reply or a missing reply never moves an
  attempt. The authority closing an incomplete request ends a request that
  committed nothing, and touches no attempt. A12 moves an attempt only on
  its own writer's attestation that no byte was sent, including one that A8
  or A8c has already ended (section 3.3). That is a statement about bytes
  already sent, never permission to send more, so the fence is unchanged.

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

- **A1 fails, or its reply is missing:** P sends nothing. If entry *X*
  exists, P hands it off (E3), so the existing E1 entry is kept and no second
  obligation is created; if the `attempt` request went unanswered, the
  handoff carries an A12 attestation, so an A1 that did land becomes `void`
  instead of uncertain, even if A8 or A8c ended it first (section 3.3).
  Otherwise P queues by Q0.
- **A2–A5 fail, or their reply is missing:** the writer closes the chat
  connection if it is still open, keeps the observed outcome in memory, and
  retries the same replay-safe request:
  - within the call, up to `PART_WAIT`;
  - then a client carries it as an attested outcome in its handoff (section
    6.4), and the bridge retries its own at the start of each flush.
- **The commit never lands:** the attempt stays `writing`, until A8 (writer
  gone) or A8c (connection closed, for example in a long-lived `agentcli`
  host) ends it **without finality**. It is then `uncertain`, R2 never calls
  it absent, and only R1 or E7 resolve it.
  - A refusal whose A3 never committed is therefore UNKNOWN. It is never
    retried, and is dead-lettered after 24 hours (N6, an owner decision).
- **E3 fails:** a fallback file carrying the handoff (section 6.4), applied
  by H1.
- **Q0 fails, or the authority is unavailable** (section 2.4.4): a fallback
  file (section 6.4). P reports the post as queued (exit 3) only after the
  file is published: its content synced before its name appeared, then
  `outbox.d/` and the state directory synced (section 2.1). So a power loss
  cannot remove a file P has acknowledged, whoever created `outbox.d/` (design
  round 3). If the file cannot be published durably, P fails as today when
  the outbox cannot be written. It never reports a queued post that is not
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
   - if abandoned, `import_epoch ≥ abandon_epoch + 2`. Passes run one at a
     time (section 6.3), and E8 committed while `import_epoch` was
     `abandon_epoch`. So the pass that completed second after E8 started
     after E8, which was after the writer was proven gone. Every fallback
     file that writer could have published was durable before it died, so
     that pass's listing included it, and the pass imported it and applied
     H1. After that pass, H1 can never apply to the entry, and its progress
     is no longer reopenable (F4). A pass may span flushes, because each
     flush imports at most `IMPORT_MAX` files, so the pass in progress at E8
     does not count;
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

**No client can stall the authority** (design round 4; I-11). Every database
write runs on the bridge's authority thread, and no client ever holds a
database lock (section 2.4). A client paused at any point, inside or between
its requests, delays a bridge request by at most one client transaction.

**No queued obligation waits for a lock** (the canonical issue review of
#19's third amendment; I-12). Every import, whether of fallback files,
the legacy claim or legacy rows, runs without `L_outbox`. A stopped process
can delay only its own unfinished publication (section 6.3).

**Locks are bounded too** (design round 3). The flush waits for `L_outbox` at
most `LOCK_WAIT` per step. It needs `L_outbox` only to retire a claim file
and to write dead letters, and nothing that imports or delivers an entry
needs it (section 2.3). No bridge flow waits for `L_outbox` without a
deadline: `announce` falls back to a fallback file (section 6.4).

- **A process paused while holding `L_outbox`**, for example an old tool
  stopped inside its append to `outbox.jsonl`, costs the flush at most
  `LOCK_WAIT`. The flush still imports every published fallback file and
  every complete legacy row, gives every eligible entry its attempt,
  reconciles, applies E7, and exports alerts.
- **The paused holder keeps everything it owns.** Its lock, its connection
  and its attempts are untouched. When it resumes it finishes its step; if it
  dies, the kernel releases its flock.
- **What waits.** Only these:
  - the holder's own row, which has no newline yet;
  - the deletion of claim files, already imported;
  - the writing of dead letters: X1's, and `Deliveries`' own.

  None of them is a queued obligation that another entry's attempt depends
  on. What they record is durable meanwhile, in the database, the claim
  files or `pending.json`. It is shown in `pchat status` as deferred, and
  retired or written at the first flush after the holder releases the lock.
  Nothing is lost, resent or decided while they wait.

`L_flush` is held only by the single bridge, so no other process can delay a
flush through it. The database's lock is held only by the authority (section
2.4.1), so no other process can delay a flush through that either. Revision
5's C-8 exemption stays removed: R6 holds without it, over the database and
over every already durable fallback file and legacy row.

### I-11. A paused client cannot stall the authority

A client that stops at any point, whether before, during or after a request,
or while sending its bytes, never prevents the bridge from giving every other
entry its attempt.

**Why it holds.**

- **The client holds no lock the authority needs.** It never opens the
  database (section 2.4), so it holds no SQLite lock. The authority never
  waits for `L_outbox`, `L_flush` or any flock of a client.
- **A transaction never waits for a client.** It begins only for a complete,
  validated request, and ends before its reply is written (section 2.4.3).
- **Reading is bounded.** An incomplete frame costs only its buffer, until
  `REQ_WAIT`, when the connection closes with nothing committed. A frame
  over `FRAME_MAX` closes it at once.
- **Writing is bounded.** Replies go to non-blocking sockets. A client that
  stops reading costs only its buffered reply, until `RESP_WAIT`. The
  transaction committed before the reply, and a lost reply is recovered by
  replay (section 2.4.2).
- **Queues are bounded.** `CONN_MAX` and `QUEUE_MAX` cap clients, and the
  excess is refused at once, without a transaction. Each request is bounded
  work.
- **The bridge is never starved.** The authority alternates between the
  in-process queue and client requests, so a bridge request waits behind at
  most one client transaction, and each bridge request either begins within
  `AUTH_WAIT` or is cancelled, never run late.
- **The paused client keeps everything it owns.** Its committed `writing`
  attempt is fenced by I-2: it is ended only by A8 or A8c, or by its own
  A12. Nobody kills it, steals from it, gives it a lease or checks before
  sending.
- **A client stopped while publishing a fallback file** holds no lock
  either. Its unfinished file sits under `outbox.d/tmp/`, which the bridge
  never reads, so it delays only its own call (I-12).

**What remains** is the speed of local storage (C-10), which every writer of
any design depends on. It is not caused by any client, entry or lock holder.

Section 5.6, case 11, gives a schedule.

### I-12. A queued obligation never waits for another writer

An obligation that is already durable, whether a published fallback file or
a complete legacy row, is imported and attempted whatever any other process
is doing. A stopped writer, client or old tool, delays only its own
unfinished publication.

**Why it holds.**

- **Publication is atomic and needs no lock** (section 2.1). A fallback file
  is written whole under `outbox.d/tmp/`, synced, then linked to its final
  name. Before the link, the bridge does not see it. After the link, its
  content is complete and durable. A writer stopped before its link delays
  only its own call. A writer stopped after it has already published.
- **Import takes no lock a writer could hold** (section 6.3). IF, I1 and I2
  hold only `L_flush`, which only the bridge takes, and they wait for no
  client, file lock or socket.
- **A legacy appender can delay only its own row.** The old tools append a
  whole line while holding `L_outbox` (`chatlib.py:391` on master). The
  bridge claims and reads without that lock, and imports only lines that
  end in a newline. A row whose newline is not yet written is that
  appender's own unfinished publication. A line appended to the claim file
  after the rename, by an appender that opened `outbox.jsonl` earlier, is
  read at a later flush. The lock is needed only to know that no such
  appender remains, before the claim file is deleted (I3).
- **The work is bounded and fair.** Each flush imports at most `IMPORT_MAX`
  fallback files, oldest name first, and at most `LINES_MAX` lines per
  claim file per request. A file's name begins with its publication time,
  so a file waits only behind files published before it, or at most `A`
  after it (C-1), never behind an unbounded stream of later ones.
- **Import is idempotent and frozen** (section 6.5):
  - a fallback file is never written after publication;
  - a claim file's imported prefix is checked unchanged before each read;
  - every creation is keyed by *X* or by occurrence;
  - a file is deleted only after its `TX` commits.

  So an interrupted or repeated import never loses or duplicates owed work.

**What remains** is local storage (C-10, C-11), as for every writer of any
design.

Section 5.6, case 9, gives a schedule.

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
- **C-8 is removed** (revision 6). Revision 5 exempted, from R6, a client
  stopped inside its own database transaction. In this design no client opens
  the database (section 2.4), so there is no such client and no exemption.
- **C-9, who opens the database.** Only the bridge's authority opens
  `outbox.db`. Every program this release ships follows that rule, and a
  future test checks that no client module opens it (section 9, test 31).
  - **While the authority runs,** it holds the file's exclusive lock (section
    2.4.1). Any other opener gets `SQLITE_BUSY` at once and holds nothing.
  - **While the bridge is down,** a program outside this design (a person's
    `sqlite3` shell, say) could open the file and keep a lock. The bridge's
    start then fails within `OPEN_WAIT`. Status reports "authority
    unavailable: database busy", and the start is retried each pass. Clients
    queue meanwhile (section 2.4.4). Nothing breaks or removes that lock.
  - **This is not an R6 exemption.** No actor of this design (P, B, S or O)
    can be that process. It is the same class of event as someone stopping
    the bridge or deleting the state directory: outside the system, and
    visible.
- **C-10, local storage.** On a healthy host, each SQLite commit and each
  file sync of the state directory completes in bounded time.
  - The authority thread waits for nothing else: no client, no flock after
    startup, no socket, no other thread (section 2.4.3).
  - So a stuck storage device can stall it, as it would stall any storage of
    any design, and nothing else can.
  - **A paused client is never counted under C-10.** I-11 shows it cannot
    delay the authority at all.
- **C-11, the fallback directory's storage.** The chat state directory,
  `outbox.d/` and `outbox.d/tmp/` lie on one local POSIX filesystem, used by
  one user, that provides:
  - **exclusive creation** (`O_CREAT | O_EXCL`);
  - **`link()`, atomic and never replacing.** It fails if the target name
    exists;
  - **`rename()`, atomic within the directory;**
  - **durable `fsync`** of files and directories: `F_FULLFSYNC` on macOS,
    `os.fsync` elsewhere (section 2.1);
  - **directory listing**, which shows every name whose link has returned.

  APFS and the usual Linux filesystems provide these.
  - **Without hard links,** publication fails. The writer then fails as today
    when the outbox cannot be written, and never reports a queued post.
  - **No fallback to `rename()`.** It could replace a published file, so
    nothing falls back to it.
  - **What the implementation checks.** It checks these on its own platforms
    in its tests (section 9, test 33).
  - **No assumption about other writers.** None is made about processes
    outside this design that write into `outbox.d/`. An unreadable file there
    is held, never guessed (section 6.3).

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
| Q0 `TX` landed, but its reply was lost or `error` | — | *X* exists. P's fallback file names *X*, so its import is a no-op, never a second entry. |
| Q0 fallback file: exclusive create under `tmp/`, write, `fsync` | nothing published, nothing acknowledged. A leftover `tmp/` file is never imported, and is removed only once its writer is gone (section 6.4). | the same: still unpublished |
| the file's `link()` to its final name | as above | **published:** visible, complete and durable in content. The bridge may import it even though P has not exited 3. A power loss before the directory `fsync` may drop the name, but P has not exited 3 yet. |
| the `fsync` of `outbox.d/`, then of the state directory | as above | durable → P exits 3 → imported as *X* (section 6.4) |
| a fallback file in an `outbox.d/` that another process created and never synced into the state directory | — | P's publication syncs the state directory too, so the file survives a power loss after exit 3 (design round 3). |
| E1 | nothing durable, nothing sent → Q0 on *X* | `open`, `direct` *X*, all `unsent` → if P is still running, E3 hands *X* off. If the writer is gone, E8, nothing unknown → retired as today. For B's `announce`, E9 instead: adopted and delivered (section 6.4). |
| E1 landed, but its reply was lost or `error` | — | P's retry of `create` gets `replay`, or its `queue` finds *X* → E3 on *X*: one obligation |
| A1 | the previous state → Q0 if nothing was written, else E3 | `writing` → A8 → `uncertain`, no finality → R1 or E7 |
| A1 landed, its reply lost; P alive | — | P sends nothing for it. Its `attempt` retry gets `replay` and it proceeds, or it hands off with an A12 attestation → `void`, part `unsent` → delivered by B. |
| A1 landed, its reply lost; P dies before any handoff | — | `writing` → A8 → uncertain, no finality → R1 or E7. Conservative: nothing proves the absence of bytes. |
| A1 landed, its reply lost; P closes its connection; A8c (or A8 after P exits) ends the attempt before P's handoff is imported | — | the handoff's attestation applies A12 from `ended` → `void`, part `unsent` → delivered by B, once (section 3.3; the canonical issue review of #19's third amendment). |
| sending lines and PING | `writing` → A8 | same |
| `PART_WAIT` and the drain | `writing` → A8 | same |
| close | `writing` → A8 | `writing` → A8, or A8c while the host lives |
| A2 | `writing` → A8 → uncertain, never `unsent` | `confirmed` |
| an outcome landed, its reply lost | — | P's replay gets `replay`; or its handoff's attested outcome is a no-op, because the attempt has moved |
| A3 + E6 | `writing` → A8 → UNKNOWN (N6), never retried → E7 | `terminal` → X1 |
| A4 | `writing` → A8 → UNKNOWN → E7 | `unsent` → retried by the owner, or E8 |
| A5 | `writing` → A8, no finality | `ended`, with `final_at` as observed |
| A11 (ack) | `ended` ack → A11 next time | `retired`, `unsent` → A1 retry |
| E3 | `direct` → gone → E8, or H1 if the handoff's fallback file is published | `outbox` |
| the handoff's fallback file, from exclusive create to the directory `fsync` | unpublished → E8 → remainder not adopted (D1). A power loss before the directory `fsync` is the same. | published and durable → H1, within the two passes after E8 (I-7) |
| E5 | all parts confirmed → B sets `done` | `done` |

#### Requests and the authority

| Boundary | Crash just before | Crash just after |
|---|---|---|
| a client sends part of a frame | nothing committed; the connection closes at `REQ_WAIT` | — |
| the authority's `TX` for a request | rolled back; no reply → the client retries, or queues or hands off | committed; the reply is written next |
| the reply is written | committed; the client sees EOF → it replays the same request on a new connection: `replay` | the client has it |
| the authority (the bridge) crashes mid-`TX` | SQLite rolls it back; clients see EOF, and the next epoch answers their replays | — |
| the authority's start transaction | no epoch change; no socket; clients queue | the epoch has grown; the socket is bound next |
| a client after a restart | — | its next reply has a higher epoch → `query`, then continue from the state shown |
| a fallback file with an attestation, then the client dies | not published: no attestation → the attempt stays `writing` → A8 | published → imported at the start of the next flush, normally **before** A8 (section 6.3). If A8 or A8c ran first, A12 still applies from `ended` (section 3.3). |

#### Bridge side

| Boundary | Crash just before | Crash just after |
|---|---|---|
| E4 | no parts → E4 again | parts `unsent` |
| A8, A8c | again at the next flush | `ended` |
| R1, R2 `TX` | unchanged → decided again | applied atomically |
| E7 | → E7 again | `terminal` → X1, X2 |
| a late outcome on a terminal entry | — | `export_version` + 1 → X1 writes version *v* + 1 |
| E8 | as before | `abandoned`, `abandon_epoch` |
| E9 (an `announce` of a gone bridge process) | `open`, `direct` → E9 again at the next flush | `outbox` → flushed: its unsent parts are delivered, its uncertain ones follow R1, R2 or E7 |
| IF: the durable sync of a fallback file F | F unchanged → imported later | the same |
| IF: its `TX` | rolled back → F imported again later, by *X* | applied, with F's closed `imports` row |
| IF: unlink F, then `fsync` `outbox.d/` | F present; its `imports` row matches → unlink only | **power loss:** F may reappear → its `imports` row matches → unlink only |
| two fallback files for the same *X* | — | the second applies nothing new: *X* exists (section 6.4) |
| I1 rename (no `L_outbox`) | `outbox.jsonl` intact | claim file C. An old appender that opened `outbox.jsonl` before the rename may still append to C; I2 reads that line later. |
| I1 directory `fsync` | **power loss:** `outbox.jsonl` may reappear and C vanish. No I2 has committed, so it is simply claimed again under a new name. | C durable → I2 |
| a claim file left by an earlier pass, a crash, or the old tools, possibly never synced | — | I1 makes a durable sync of it before its I2, so an import never commits from a claim whose directory entry could vanish. |
| I2 | no rows; C's `imports` row unadvanced → the same lines read again, with the same occurrence ids | rows, and C's `imports` row advanced past them |
| I2 reading a line with no newline yet | — | not imported: it is that appender's own unfinished row |
| I3: the barrier acquisition of `L_outbox` | C stays `open` → retired at a later flush | the final I2 reads through the end of C; an unterminated last line is held |
| I3 unlink, then directory `fsync` | C present, `imports` closed → unlink only | **power loss:** C may reappear → `imports` closed → unlink only |
| H1 (in I2) | rolled back with I2 | applied |
| `import_epoch` + 1 | the pass is not counted → another pass runs before collection | counted |
| V1 | not indexed; the checkpoint has not moved → replayed, `INSERT OR IGNORE` | indexed |
| the checkpoint file write | the stored `mark` disagrees with the checkpoint → V2 starts a new span (safe) | consistent |
| V2, V3 | the old span | the new span |
| a bridge restart | the live set is empty → coverage advances only after a catch-up | — |
| `announce`'s fallback file, from exclusive create to the directory `fsync` | unpublished; `held_announced` unset → raised again with the same *X* | published → imported by *X* at the next flush |
| `announce`'s E1 committed, A1 not | — | `open`, `direct`, origin `announce`. If the bridge lives, it hands *X* off (E3) or publishes a fallback file. After a restart, E9 adopts *X*. Either way its parts are delivered once (section 6.4). |
| `Deliveries` saves `held_announced` | the flag is unset → the announce is raised again with the same *X*: it finds the existing obligation and adds none | saved, after the announce's obligation was durable (section 6.4) |
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
5. **Handoff versus abandonment.** P confirms part 0, then E3 fails. P
   publishes a fallback file carrying the handoff, and exits. B commits E8,
   with `abandon_epoch = e`, while a pass is in progress whose listing
   missed the file.
   - That pass completes (`import_epoch` = *e* + 1). The next pass lists the
     file, and H1 reopens the entry, because `import_epoch ≤ e + 1` and the
     ids match.
   - G1 could not have collected part 0 in between: `import_epoch` had not
     reached *e* + 2.
   - An unrelated killed post published no file, so after those passes it
     stays abandoned and becomes collectible.
6. **`pchat status` during a flush.** Its `status` request runs in one read
   `TX` on the authority, between other transactions.
7. **Two bridge exports.** X1 runs only on B. An X1 retried after a crash
   finds the line present, makes it durable, and sets the mark.
8. **`Deliveries` writes a delivery dead letter during X1.** `L_outbox`
   serializes them, so no two appends interleave.
9. **A stopped `L_outbox` holder, with durable obligations behind it (design
   round 3; the canonical issue review of #19's third amendment).** An old
   tool's `pchat`, outside this release, is stopped while it holds
   `L_outbox`, half-way through appending a row for `pat` to `outbox.jsonl`.
   Already durable are:
   - `sam`'s fallback file for `#beta` in `outbox.d/`, published while
     `sam`'s bridge was restarting;
   - a complete legacy row for `alp-solver-2` in `outbox.jsonl`, appended
     before the stop.

   B's flush takes `L_flush`, and does this without `L_outbox`:
   - IF imports `sam`'s file;
   - I1 renames `outbox.jsonl` to a claim C;
   - I2 imports `alp-solver-2`'s row, and stops before `pat`'s half row,
     which has no newline.

   Both new entries get their attempts in the same flush, with every other
   eligible entry. Then:
   - B tries `L_outbox` for I3 and X1 until `LOCK_WAIT` passes, and defers
     only those;
   - it reconciles, applies any E7, and exports alert rows with X2.

   The stopped tool's lock, connection and file are untouched.
   - **When it resumes,** it finishes `pat`'s row, in C, because it opened
     `outbox.jsonl` before the rename. The next flush's I2 imports that row.
     A later flush acquires `L_outbox` and retires C.
   - **If it never resumes and dies,** its flock is released. The next flush
     retires C, and holds the unterminated half row.
10. **A client stopped while publishing.** `pat`'s `pchat post` finds the
    authority unavailable. It writes its fallback file under `outbox.d/tmp/`
    and is stopped before the `link()`. The bridge never reads `tmp/`, and
    every published file and entry flows as usual. `pat` has not exited 3,
    so nothing is reported. When `pat` resumes, it links, syncs, exits 3, and
    the next flush imports the file. If `pat` dies instead, nothing was
    published or reported, as when today's `pchat` is killed before its
    append. Its `tmp/` file is removed once `pat` is proved gone (section
    6.4).
11. **A paused client (design round 4, finding 2).** `pat`'s `pchat post` to
    `#alpha`, and `sam`'s queued entry for `#beta`, on an injected clock:

    | t (s) | `pat`'s `pchat post` | the bridge |
    |---|---|---|
    | 0.0 | connects, sends half of its `create` frame, and is stopped (SIGSTOP) | holds a partial buffer; no `TX` |
    | 0.1 | stopped | the flush asks for A1 on `sam`'s entry; the authority runs it at once |
    | 0.2–1.0 | stopped | sends `sam`'s part, gets the PONG; A2 and E5 commit |
    | 2.0 | stopped | `REQ_WAIT`: the connection closes; nothing of `pat`'s call committed |
    | 9.0 | resumes, and sees EOF | — |
    | 9.0–14.0 | resends `create` with the same *X*: `ok`; continues. If unanswered within `IPC_WAIT`: Q0's fallback file, then exit 3 | served like any request |

    The same holds when `pat` stops at other points:
    - **after a complete frame:** its `TX` runs and commits. Its reply waits
      in the socket buffer, or is dropped at `RESP_WAIT`, and `pat`'s replay
      gets `replay`;
    - **after its A1 reply, while sending:** it owns a `writing` attempt and
      no lock, and I-2 fences it.

    In every case `sam`'s entry is delivered in the same flush.
12. **Announce during a lock holder and a storage fault (design round 4,
    finding 1).** An old tool holds `L_outbox` and is stopped. The bridge's
    `announce` for a held instruction fails before any byte, and its Q0
    in-process request fails.
    - `announce` publishes a fallback file for *X*, by section 2.1's steps.
      It never touches `L_outbox`.
    - `Deliveries` then saves `held_announced`.
    - The same flush delivers `sam`'s unrelated entry. The next flush's IF
      imports the announcement, and it is delivered once.
    - If the publication fails too, the flag stays unset, and the next pass
      raises the same *X* again.
13. **A bridge restart in the middle of a direct post.** `alp-solver-2` has
    part 0 confirmed. The bridge restarts while it sends part 1.
    - Its `outcome` request for part 1 meets a refused connection, and is
      retried until the new authority answers at a higher epoch.
    - It sends `query`, sees its attempt still `writing`, and replays the
      outcome: `ok`.
    - Its `attempt` for part 2 then proceeds.

    Had the new authority not answered by `PART_WAIT`, it would have handed
    off with the attested outcome, and exited 3.
14. **Recovery before the handoff (correction (d) of the canonical issue
    review of #19's third amendment).** `alp-solver-2`'s `pchat post` sends
    `attempt` for part 1 of *X*. The authority commits A1, generation 1, with
    nonce `n1`, but the reply is lost to a bridge restart.
    - `pchat` retries until `IPC_WAIT` with no answer, so it sends nothing. It
      closes its chat connection, publishes a fallback file for *X* that
      carries the handoff and the attestation (*X*, 1, 1, `n1`, "no byte
      sent"), exits 3, and is gone.
    - The new bridge's first flush runs A8c (or A8) on that attempt before it
      lists the file, because the file landed after the flush's listing: the
      attempt is `ended`, the part `uncertain`.
    - The next flush's IF imports the file: H1 hands *X* off, and A12 applies
      from `ended`, because the identity, generation and nonce match and no
      attribution exists. The attempt is `void`, and the part is `unsent`.
    - B delivers part 1 once. Part 0, confirmed earlier, is never resent.

    With a wrong nonce, another writer, a newer generation, an attempt ended
    by the writer's own A5, an attempt already `delivered`, `absent` or
    `dead`, or a `terminal` entry, the file changes nothing beyond H1.
15. **An announcement whose entry exists but was never sent (correction (e)
    of the canonical issue review of #19's third amendment).** The bridge
    raises an announcement for a held instruction. Its *X* is derived from
    the instruction's key and reason. E1 commits *X* (`open`, `direct`,
    origin `announce`), and the bridge is killed before A1.
    - The new bridge process starts. Its flush finds *X* `open` and `direct`,
      owned by a gone bridge process with no `writing` attempt, so E9 hands
      it to the outbox. The same flush gives part 0 its A1, and delivers the
      announcement once.
    - `held_announced` was unset, so `Deliveries` raises the same
      announcement, with the same *X*. It finds *X* existing and owned by the
      outbox, adds no second obligation, and saves the flag.
    - Had the flag been saved after E1, before the kill, E9 would still
      deliver *X*: the entry is the obligation, and its existence alone never
      suppresses its unsent parts.
    - An ordinary `pchat post` killed after E1 is still abandoned (E8, D1),
      as before.

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

A claim file the old code may have rewritten before cutover is imported as
it stands. After the rename, this design never rewrites a claim file. An old
appender can still add whole lines to the end of one (section 6.3), so I2
reads a claim incrementally, and checks that what it already imported is
unchanged.

**The legacy boundary after cutover.**

- **This release writes no legacy row.** Its clients and its bridge queue
  only by fallback files (section 6.4), and none of them takes `L_outbox`
  to queue.
- **It still reads legacy rows, as `outbox/1`.** That covers rows left at
  cutover, and rows that a program outside this release appends later. The
  bridge reads them without `L_outbox` (section 6.3), so a row already in
  `outbox.jsonl` is never stuck behind a stopped appender.
- **No old format is dropped,** and none needs an exemption.

### 6.2 Supported formats and the hold policy

Import supports exactly:

- in `outbox.jsonl` and claim files (`outbox/1`):
  - the running tools' text post rows: `channel`, `as`, `text`, and
    optionally `cont`, `reply_to` and `at`;
  - their ack rows: `channel`, `as`, `ack` and `at`;
- in `outbox.d/` (`outbox/2`): this design's fallback files (section 6.4).

**Anything else is held** in `held`, with its raw text, and shown in `pchat
status`. Each claim file that holds anything raises **one** content-free
alert row, keyed by the claim file's name, in the same I2 `TX` as its held
rows (section 3.6). Held rows include:

- in `outbox.jsonl` or a claim file, rows with `parts`, `terminal`, `id`,
  or `fallback`. These are PR #20's formats, or revision 6's fallback row,
  and none of them ever ran live;
- unreadable lines, including an unterminated last line when a claim is
  retired;
- in `outbox.d/`, an unreadable or invalid file. It is held under its file
  name, with its raw content, and then removed like an imported file.

**PR #20's side files are never imported:** `outbox-reserved.jsonl`,
`outbox-attributed.jsonl` and `record-coverage.json`. If any exists at
cutover, it is left untouched and reported. Coverage starts fresh, from the
first catch-up after cutover.

**Legacy rows.** A legacy text row carries no attempt history. Whatever the
old tools may already have published of it is outside this design's evidence,
and it is delivered as #19 R3 requires ("as today").

### 6.3 The fenced import

The import runs only in B's flush, holding `L_flush`, and **never takes
`L_outbox`**. It does only local work. It runs at the **start** of each flush,
before A8, A8c and reconciliation, so a durable attestation (section 6.4) is
normally applied before A8 could end its attempt without finality. If A8 or
A8c ran first, A12 still applies from `ended` (section 3.3). Each file, or
each batch of at most `LINES_MAX` legacy lines, is one in-process request to
the authority (section 2.4.3).

**1. Fallback files (IF).**

1. **List** the published names in `outbox.d/`, never `tmp/`, and sort them.
   A name is `<t>-<X>-<r>.json`: the writer's publication time in
   nanoseconds, zero-padded to 20 digits, then the entry id, then a random
   suffix. So name order is publication order, up to the clock allowance
   `A` (C-1).
2. **Take** the oldest `IMPORT_MAX` names not yet imported in this flush.
   The rest wait for the next flush, so none waits behind files published
   later.
3. **For each file F:**
   1. If `imports` has F's name with F's sha256, it was imported and its
      removal was interrupted: go to step 6.
   2. If `imports` has F's name with another sha256, something outside this
      design wrote it. In one `TX`, hold it with its alert row
      `held:<name>:<sha256>`, unless that row exists, then go to step 6.
   3. Make a durable sync of F, and of `outbox.d/` (section 2.1). A file a
      crashed writer linked but never synced is thereby made durable before
      anything relies on it.
   4. Read F whole, and compute its sha256.
   5. **Import**, in one `TX`:
      - apply F by its *X* (section 6.4). If it is unreadable or invalid,
        hold it instead, with its alert row `held:<name>`;
      - insert F's `imports` row, `closed`.
   6. **Remove** F: unlink it, then `fsync` `outbox.d/`.

**2. The pass and `import_epoch`.** A pass is the set of names that step 1
listed when the pass began. It ends when every one of them has been removed,
which may take several flushes. Its last `TX` increments `import_epoch`. If
the listing was empty, a `TX` of its own does. Passes run one at a time, and
a bridge restart abandons the pass in progress, uncounted. I-7 relies on
this.

**3. Legacy rows (I1, I2, I3).**

1. **Claim (I1).** Rename a non-empty `outbox.jsonl` to a new, never-used
   claim name, **without** `L_outbox`. List every `outbox.claimed-*.jsonl`,
   and make a durable sync of each one (file, then directory). That includes
   claims left by an earlier pass, a crash, or the old tools before
   cutover. A claim name enters `imports` (`open`, nothing imported yet) in
   the first I2 `TX` that reads it.
2. **Import (I2)**, for each open claim C, in requests of at most
   `LINES_MAX` lines:
   1. **Check the prefix.** Read C from the start. The bytes up to C's
      imported offset must have the sha256 its `imports` row records. If
      they do not, something outside this design rewrote C. In one `TX`,
      hold the file with its alert row `held:<name>:<sha256>`, unless that
      row exists. Import nothing more from C, and never unlink it.
   2. **Take** the complete lines after the offset, up to the last newline.
      A last line without a newline is not read: an appender may still be
      writing it.
   3. **Import them**, in one `TX`, for each line ordinal `i`:
      - **The occurrence id is always derived** from (claim file name, `i`),
        for example `sha256(name + ":" + i)`, and is UNIQUE. An explicit id
        never replaces it.
      - **A text or ack row** becomes an `open`, `outbox` entry (E2).
      - **Any other row** is held, with C's one alert row (section 6.2).
      - **Advance** C's `imports` row: the line count, the new offset, the
        sha256 of the bytes through it, and the epoch.
3. **Retire (I3)** C once it is **quiet**:
   1. **The barrier.** B has acquired `L_outbox`, within `LOCK_WAIT`, at
      some moment after it first recorded C. It may release the lock at
      once.
   2. **A final I2,** after that acquisition, reads C through its end, holds
      an unterminated last line as unreadable, and closes C's `imports` row
      in the same `TX`.
   3. **Unlink** C, then `fsync` the directory.

**Why the barrier suffices.** An old appender opens `outbox.jsonl` by name only
while holding `L_outbox`, and closes it before releasing the lock
(`chatlib.py:391` on master). So any appender that could still write to C
held the lock when C was renamed, and released it before B's later
acquisition. After the barrier, nothing can add to C. The claiming itself
needs no lock:

- a line appended to C after the rename is complete when its appender
  releases the lock, and I2 reads it at a later flush;
- an appender that takes the lock after the rename opens a new
  `outbox.jsonl`.

**What a stopped holder of `L_outbox` delays:** its own unterminated row, and
I3's deletion of claim files. Never the import of a complete row, or of any
published fallback file (I-12).

**The old tools' flush** is stopped at activation, before the first live
import (section 6.1). If one ran anyway, it would claim and deliver rows
itself, which cutover forbids.

### 6.4 Fallback files

Every fallback is one **fallback file**, published by section 2.1's steps:

1. exclusive creation under `outbox.d/tmp/`;
2. the whole content, then `fsync`;
3. `link()` to its final name, which never replaces a file;
4. `fsync` of `outbox.d/` and of the state directory.

P exits 3, reporting the post as queued, only after step 4. No lock is taken,
so a writer stopped at any point delays only its own call (I-12). If
publication fails, P fails as described at the end of this section.

**When a writer publishes one, and only then** (the canonical issue
rereview of #19's fourth amendment, correction 1):

- its `queue` or `handoff` request is not answered as committed: the
  authority is unavailable, or the reply is missing, `busy` or `error`
  (section 2.4.4); or
- it must make an attestation durable, and no request is answered (section
  3.3).

A Q0 or E3 that commits needs no file. A call publishes at most one fallback
file, because publishing one ends the call.

**Its name** is `<t>-<X>-<r>.json` (section 6.3): the publication time, the
entry id *X*, and a random suffix. If `link()` finds the name taken, the writer
draws a new suffix. The obligation's identity is *X*, which is fixed per call
(section 3.1). The name only identifies the occurrence, and is never reused.

There is one fallback file shape, for both Q0 and E3. It holds, as one JSON
object:

- `v: "outbox/2"`;
- the post's or ack's legacy fields;
- the call's entry id *X*;
- the writer identity, when the writer had a connection;
- `epoch_seen`, the highest authority epoch the writer saw in the call. It
  is recorded for diagnosis, and gates nothing (section 3.3);
- **at most one attestation** (section 3.3), naming the attempt by (*X*,
  *n*, generation, `a1_nonce`). It is either:
  - `void`: the writer sent no byte of that attempt, because it accepted no
    committed `attempt` reply for it;
  - an outcome the writer observed but could not commit: A2, A3, A4 or A5,
    with its `final_at` and detail.

  An attempt still `writing` is the last one the writer made, and it makes
  no further attempt once it hands off, so one attestation is enough.

Import applies the file by *X*, never by occurrence:

- **Entry *X* exists and is `open`, `direct`, or `abandoned` with H1's
  guard:** apply H1. The existing E1 entry and its progress are kept. In the
  same `TX`, apply the attestation, if any, under its guard: A12's (section
  3.3), which also accepts an attempt that A8 or A8c has ended, or the
  `outcome` request's. If its guard fails, the attestation changes nothing.
- **Entry *X* exists and is already `open`, `outbox`:** H1 is a no-op, and
  the attestation, if any, is applied under the same guard. This includes a
  Q0 or E3 commit that reported failure but had landed. It also includes an
  attestation for an attempt of the bridge's own flush.
- **Entry *X* exists and is `done` or `terminal`:** a no-op. These are final.
- **Entry *X* exists, is `direct` or `abandoned`, and H1's guard fails** (its
  identity, or its epoch check): hold the file.
- **Entry *X* does not exist:** E1 never committed, so by I-1 nothing of this
  call was sent. Import inserts entry *X* (`open`, `outbox`, no parts),
  delivering the whole post once, as today. The file's occurrence (its name)
  is recorded with it.

Every creation is an `INSERT` on the primary key *X*, so an ambiguous commit
that becomes visible later, whether E1's or Q0's, collides with the import's
insert instead of duplicating it. Two fallback files with the same *X*, such
as a repeated announcement's, are one obligation for the same reason. Legacy
rows from the old tools carry no *X*, and keep their occurrence-derived ids
(section 6.3).

**Unfinished publications.** A file under `tmp/` is never imported. Its name
carries its writer's identity (boot id, pid, start time). The bridge removes
it only once that writer is **gone** (section 5.4), never by age, and never
while the writer is merely stopped. A `tmp/` file with a second link was
already published, so its `tmp/` name may be removed at any time.

If publication fails too, P fails as it does today when the outbox cannot be
written. Whatever already committed still prevents a blind resend.

**The bridge's own fallback (`announce`).** The bridge queues exactly as a
client does, by a fallback file in `outbox.d/`. It never appends to
`outbox.jsonl`, and so never waits for `L_outbox` (design review round 4,
finding 1). Revision 6's bridge-private file `outbox.bridge.jsonl`, its
mutex, and its import I0 are removed. IF imports these files like any
other.

- **Identity.** An `announce` call's *X* is derived from its pending item's
  key and its reason, for example `sha256(key + ":" + reason)`. A re-raised
  announce is therefore the same obligation, never a second one.
- **The rules.** `announce` follows section 2.4.4's rules through in-process
  requests:
  - it queues by Q0 before any byte, and hands off by E3 after some;
  - when that Q0 or E3 request fails, or is cancelled, it publishes the
    fallback file for *X*;
  - an in-process request is either cancelled before `BEGIN`, having
    committed nothing, or returns its result. Only an `error` leaves its
    commit unknown, and a fallback file by *X* is safe either way, because
    import applies it by *X*;
  - like a client, `announce` sends a part's bytes only after an A1 it
    accepted. If an A1 result is unknown, no byte follows it, and the bridge
    records its attestation (section 3.3).
- **A durable entry is an obligation, not proof of sending** (correction (e)
  of the canonical issue review of #19's third amendment). When an
  announcement is raised and *X* already exists, the bridge never treats its
  existence alone as delivery. It acts on *X*'s state:
  - **`open`, `outbox`:** the flush owes it. Nothing more is sent now.
  - **`open`, `direct`, owned by this bridge process:** an earlier raise of
    this process stopped part-way. It hands *X* off by E3, or publishes a
    fallback file.
  - **`open`, `direct`, owned by a gone bridge process:** E9 adopts it, in
    this request or at the next flush.
  - **`done` or `terminal`:** delivered, or dead-lettered with its alert.
    Nothing more is sent.

  In every case, the unsent parts have exactly one owner that will deliver
  them, and no second entry is made.
- **E9.** An `announce` entry is never abandoned. When its owning bridge
  process is gone, A8 ends its `writing` attempts, and then E9 makes it an
  outbox entry. The flush delivers its unsent parts in order, after its
  earlier parts are confirmed. Uncertain parts follow R1, R2 or E7 as usual.
  E8 and D1 are unchanged for every other direct entry, so a killed `pchat`
  still does not have its remainder adopted.
- **`held_announced`.** `Deliveries` saves its flag only after the
  announcement's obligation is durable: *X* committed in the database, in
  any state above, or its fallback file published. If neither can be
  written, the flag stays unset, the instruction stays in `pending.json`,
  and the next pass raises the same *X* again. Nothing is dropped, and
  nothing is reported before it is durable. A flag saved after E1 alone is
  safe, because E9 delivers *X* even if the bridge then crashes.
- **No unbounded wait.** Each in-process request is bounded by `AUTH_WAIT`
  and C-10. Publication takes no lock (C-11). The chat setup and send of
  `announce` keep their absolute deadlines (section 5.1).

### 6.5 Why the import is idempotent and keeps occurrences

- **A crash, or power loss, before an import commits** leaves no rows. The
  fallback file, or the claim's unimported lines, are still there. A claim
  file is durable under its claim name, so the next pass derives the same
  occurrence ids.
- **After the commit,** the `imports` row is durable. A reappearing fallback
  file matches its closed row, so only its removal remains. A claim file
  continues after its recorded offset. A power loss cannot bring back
  `outbox.jsonl` with lines already imported, because the claim name was
  durable before I2.
- **Frozen inputs.** A published fallback file is never written again. A
  claim's imported prefix is checked against its recorded sha256 before
  every read. A changed fallback file or claim prefix is held, never merged.
- **By *X*.** Every fallback file applies by its *X*, so a duplicate file, or
  a commit that became visible late, never makes a second entry.
- **Every legacy line** is its own occurrence: duplicates are kept, never
  dropped as replays.
- **Names** of claim files and fallback files are never reused.
- **Removal follows the commit.** A file is unlinked only after the `TX` that
  holds its content commits, so the database holds every obligation before
  its file goes.

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
5. **For every part, single-line or multi-line:** every unattributed message
   of the same account and channel inside `Y`'s window, of **any** text other
   than *k*, is forced to the confirmed attempts of its own text key (section
   7.2). This is #19's first amendment, A3: every message from the same
   account in that channel inside the span is accounted for (design review
   round 4, finding 3).

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
- **Single-line parts.** The argument needs condition 5 only for a
  multi-line `Y`, because a single-line `Y` can publish nothing but `m_Y`
  (C-4). Condition 5 still applies to it, because A3 requires every message
  to be accounted for. A single-line part with one unexplained message of
  another text in its window therefore stays UNKNOWN. That is more
  conservative, never less.
- **No assumption about untracked producers.** The argument makes none. An
  extra message is never forced, so it can only block absence.

**Example.** `alp-solver-2`'s multi-line part `Y` has the lines "north", ""
and "south". During `Y`'s window, the record shows:

- a message "north\n" from that account, not attributed and not forced;
- `Y`'s own earlier part, confirmed, whose message is forced to that part.

The first message blocks condition 5, so `Y` is undecided. Blank lines,
joined pieces, reordering, or any other shape are treated the same way, with
no model needed.

**Single-line example.** `alp-solver-2`'s single-line part `Y`, "deploy
done", has finality and complete coverage, and no "deploy done" appears.
But the record shows an unattributed "beta status" from that account in
`#alpha` inside `Y`'s window, which no confirmed attempt accounts for.
Condition 5 fails, so `Y` stays UNKNOWN and is never resent. Had that message
been attributed, or forced to a confirmed "beta status" attempt, `Y` would be
absent and resent once.

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
  - an unexplained same-account message near any part;
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
| R6, independent flow | I-10, I-11 and I-12. **The database:** the bridge is its only writer (section 2.4), so a paused client holds no database lock; requests are bounded and turns fair (section 2.4.3). **Already durable obligations:** fallback files are published atomically and imported without `L_outbox`, and legacy rows are claimed and read without it (sections 6.3 and 6.4), so a stopped lock holder or publisher delays only its own unfinished publication. Setup is bounded (section 5.1), and so is lock acquisition (section 2.3). `announce` falls back to a fallback file (section 6.4). No exemption: C-8 stays removed. |
| R7, refusals | A3 and E6; N6 for a refusal that could not be recorded. |
| R8, visibility | `pchat status` from the authority: waiting to post, awaiting a check, held rows, suspended designations, fallback files and legacy rows not yet imported, and claim retirement or export deferred by a busy lock. While the authority is unavailable, the dated snapshot and the waiting fallback files and legacy rows (section 2.4.5). |
| R9, no exactly-once; delivery preservation | Stated here. A transport failure before any part is written still queues the whole post once. **Changed by the owner's 2026-10-10 decision:** while the authority is unavailable, a post, ack or notice is queued in full instead of sent, and delivered when the bridge returns (section 2.4.4). Nothing is lost, and it is not claimed equivalent to today. |
| R10, capture record | Unchanged, plus: captured tests whose assertions encode replaced behaviour are updated in the implementation's pull request, with requirement 10 provenance entries, keeping their protective intent. That covers ack rows queued to `outbox.jsonl`, which no client of this release writes, and direct sending while no authority runs. For `test_silence.py` (`:152-171`, `:175-185`): a silent run's post is never queued in any form, including fallback files and `queue` or `handoff` requests; its acknowledgements are still kept; and a pre-run entry keeps its delivery (addition 3). |
| R13 | Unchanged. |
| R11, guidance | In the implementation's pull request, `packages/chat/SKILL.md` also explains, in plain words with invented examples, that posts, acknowledgements and notices are queued while the bridge's authority cannot answer, **even when the chat server is reachable**, and are delivered when it returns (correction (b) of the canonical issue review of #19's third amendment). That file is read by agents and by `test_skill_guidance.py`, so it changes only in that pull request, never by `docs-push`, and it is not changed now. |
| R12, contract documentation | Satisfied by D-72 and D-73, which are updated as needed. No new D-number. |
| R14, the SQLite authority | Sections 2–7. The bridge is its only opener and writer (section 2.4; the 2026-10-10 decision). |
| R15, release packaging (second amendment) | Section 10.1, including the new read-write `outbox-authority` interface, which extends B1 under the 2026-10-10 decision. |
| Acceptance 1, 2, 4–6, 8–10 | Behavior unchanged. For acceptance 2, the test designates its fake account (D2). |
| Acceptance 3 | As amended (AD-2, corrected by the second amendment). The fixture gives part 3 a **non-refusal** error reply together with its matching completion PONG, complete coverage, and no unexplained message. Companion checks: a late PONG without an error is A2, confirmed and not resent; a FAIL is A3, dead-lettered and not retried; a bare timeout, crash or broken connection never proves absence. |
| Acceptance 7 | As amended (AD-1): the test recovers the SQLite crash state, with the same assertions. |
| Acceptance 11 | As corrected (section 10.1): the comparison excludes normally generated fields, and the release suite's isolated fixture builds and installs are allowed. |
| Acceptance 12 (third amendment), with the fourth and fifth amendments' additions | Tests 25–40 (section 9). |

**Narrowings inside UNKNOWN.** Each of these ends as UNKNOWN, never as a
resend:

- **N1:** R1 needs an exact count, with no live writer and no dead member.
- **N2/N8:** an unexplained same-account message near any part blocks R2. An
  unconfirmed multi-line attempt blocks R1 for its account and channel inside
  its window. For an attempt without finality, that window never closes.
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
- the authority driven in-process, or over `socket.socketpair()`. That
  connects nothing, and `_isolation` refuses every `socket.connect`
  (`tests/_isolation.py:53-54`). A test that needs a real AF_UNIX connect
  needs a provenance-recorded `_isolation.py` change first;
- faked peer credentials, boot id, process and socket probes; `ps` and
  `lsof` stay refused commands (`tests/_isolation.py:19-21`);
- every database a test opens is proved to lie under `_isolation.CHAT_STATE`
  (the canonical approval's clarification). Designation rows are set before
  the authority starts, or through an in-process request;
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
    - Q0's fallback file, and power loss on either side of each step of its
      publication;
    - **an E1 commit that reports failure but lands.** P finds *X* and hands
      it off. Restart, then import.
    - **a Q0 commit that reports failure but lands, then a fallback file.**
      Import, restart, import again: one entry *X*.

    Each queues the whole post once, as today. Two separate calls with
    identical text are still two entries. For `announce`, see test 29.
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
    - the fallback files to the outbox format version, read and written;
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
    - **Fallback files, by section 6.4's single shape.** A file whose entry
      *X* does not exist creates *X* (`open`, `outbox`, no parts) and
      delivers the whole post once. A file whose *X* exists as `open`,
      `direct` (or `abandoned`, within H1's guard) with a matching writer
      identity applies H1. A file whose *X* exists but fails H1's guard is
      held. A file whose *X* is already `outbox` applies only its
      attestation, under its guard. A file whose *X* is `done` or `terminal`
      is a no-op.
    - A legacy row with `fallback`, `id`, `parts` or `terminal` in
      `outbox.jsonl` is held.
15. **Collection.** Messages near unresolved attempts are kept. Attributions
    go with their messages. Terminal rows stay until their current version is
    exported. Open-window dead attempts are kept.
16. **Coverage.** Each of these stops coverage, and R2 waits: KICK or PART, a
    disconnect, a stale reply, a failed V1, a checkpoint mismatch, a restart.
17. **Independent flow.** Another account, and the same account with another
    text, flow while one entry is undecided, refused or failing, while
    `L_outbox` is held by a paused process (tests 23 and 32), while a client
    is stopped at any point of a request (test 25), and while a writer is
    stopped in the middle of publishing (test 33).
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
    - **Fallback.** Another process creates `outbox.d/` without syncing
      the state directory. P publishes its Q0 fallback file there, syncs
      `outbox.d/` and the state directory, and exits 3. Power loss: the
      file is present and is imported as *X*.
    - **Recovered claims.** A claim file left unsynced by a crashed pass, or
      by the old tools, is synced before its I2. Power loss after I2: no
      occurrence is lost or duplicated.
    - **A negative control.** The same schedules with the directory `fsync`
      removed lose the record, so the test can detect the defect.
23. **A paused lock holder (design round 3, finding 2).** With a fake lock
    table and an injected monotonic clock:
    - A fake old-tool process holds `L_outbox` and never releases it. B's
      flush still runs IF, I1 and I2. It gives an unrelated open database
      entry for `#beta` its attempt, applies an E7 that falls due, and exports
      its alert row. It tries `L_outbox` only for I3 and X1, for at most
      `LOCK_WAIT`. `pchat status` shows retirement and export deferred.
    - B never removes or breaks the lock, and never ends the holder's
      attempt or connection.
    - The holder releases the lock: the next flush retires the claim and
      exports as usual. It dies instead: the lock is released with it, and
      the next flush does the same.
    - No client of this release ever waits for `L_outbox`: a fake `pchat`
      queueing while the authority is unavailable publishes its fallback file
      and exits 3 while the lock is held.
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
25. **A paused client (design round 4, finding 2).** On an injected clock,
    with `sam`'s queued `#beta` entry due in the same flush, a fake client
    stops:
    - before connecting;
    - after half a frame;
    - after a complete frame;
    - while its reply is unread;
    - after its A1 reply;
    - while sending its part.

    In every case `sam`'s entry is delivered in that flush, and no `TX` waits
    for the client. Nothing commits for an incomplete frame, and the
    connection closes at `REQ_WAIT`. An unread reply is dropped at
    `RESP_WAIT`, and the client's replay gets `replay`. A client stopped
    after A1 keeps its `writing` attempt: it is never ended, voided or
    retried by time.

    **Floods:** `CONN_MAX` + 1 connections, and `QUEUE_MAX` + 1 complete
    requests, get closed or `busy` at once, with no `TX`. Bridge requests
    still run at every other turn.

    **Cancellation:** an in-process request still queued at `AUTH_WAIT` is
    cancelled and never runs.

    **A negative control:** the same schedule with a client holding a real
    `BEGIN IMMEDIATE` on a sandboxed database shows the stall that this
    design removes.
26. **Requests at every crash point.** One case per row of section 5.5's
    request table:
    - each op's reply lost after its commit, then a replay: `replay`, with
      the same state;
    - an `error` reply treated as missing;
    - a `conflict` that changes nothing;
    - an authority restart between two requests of one call. The higher
      epoch makes the client `query`, then continue;
    - a reply with a lower epoch, which is ignored;
    - two separate calls with identical text: two entries.

    No reply is ever produced before its `COMMIT`. The fake connection
    records the order.
27. **A12 and attested outcomes.** A `void` applies only with the same
    writer, generation and `a1_nonce`, to an attempt still `writing` or
    ended by A8 or A8c, with no attribution, in an `open` entry (or one H1
    reopens in the same `TX`). An attested outcome applies only to an attempt
    still `writing`. A wrong nonce, another writer, a newer generation, an
    attempt ended by A5, or one already `delivered`, `absent` or `dead`, or a
    `terminal` entry, each change nothing. A client whose A1 reply was lost
    sends no byte, and its handoff makes the attempt `void`, so the part is
    delivered by B, once. The import of a durable attestation normally runs
    before A8 in the same flush. Test 35 covers the case where it does not.
28. **Authority unavailable: the material change.** For `pchat post`, `pchat
    ack` and `notify`, test each of these:
    - no socket;
    - a socket path over the limit;
    - a refused connect;
    - a wrong peer uid;
    - an unknown version;
    - `busy`;
    - `error`;
    - no reply within `IPC_WAIT`.

    In each, the call sends no byte without a committed `attempt` reply, and
    queues the whole call (exit 3) only after its fallback file is published
    and durable (section 2.1). After the bridge returns, it is delivered once.
    Mid-post, the remainder is handed off, and confirmed parts are never
    resent. A Q0 that landed with its reply lost, plus its fallback file, is
    one entry.
29. **Announce fallback (design round 4, finding 1).** A fake old tool holds
    `L_outbox` and never releases it. `announce`'s in-process E1 and Q0 fail
    before any byte.
    - `announce` publishes a fallback file for *X* in `outbox.d/`, without
      touching `L_outbox`.
    - `held_announced` is saved only afterwards.
    - `sam`'s unrelated entry is delivered in the same flush, and IF imports
      the announcement once at the next flush.
    - With the publication failing too, the flag stays unset, and the next
      pass raises the same *X*: one entry.
    - A crash between the publication and the flag, or between IF's `TX` and
      the unlink, still gives one entry.
    - The flush's time stays within its absolute deadlines.
30. **Message accounting on every part (design round 4, finding 3).**
    - A single-line `Y` with finality, complete coverage and no match, plus
      one unexplained "beta status" message from the same account in `#alpha`
      inside `Y`'s window: UNKNOWN, never resent, dead-lettered at 24 hours.
    - The same with that message attributed, or forced to a confirmed "beta
      status" attempt: ABSENT, and resent once.
    - Both cases repeated for a multi-line `Y`, as a control.
31. **One opener.**
    - A second connection to the sandboxed database, while the authority
      runs, gets `SQLITE_BUSY` at once.
    - A foreign holder at startup makes the start fail within `OPEN_WAIT`:
      no socket, status says why, the lock is untouched, and the start is
      retried next pass.
    - A database without `writer_model = 'bridge-exclusive/1'` is refused.
    - A second bridge cannot take `L_auth`.
    - `PRAGMA locking_mode` and `journal_mode` read back `exclusive` and
      `wal`.
    - No client module opens `outbox.db`. Test this statically, and with an
      audit hook on the `sqlite3.connect` event while the client paths run.
    - The connection works on Python 3.10, with no `autocommit` attribute.

Tests 32–37 are the fourth amendment's future regressions. Like every test
above, they are specifications for the implementation, not runs. None is
written or run now.

32. **Already durable obligations behind a stopped lock holder.** A fake old
    tool holds `L_outbox`, stopped half-way through a row, with no newline.
    Already durable are a published fallback file for `sam`'s `#beta` post
    and a complete legacy row for `alp-solver-2`. In the same flush:
    - IF imports the file, and I1 and I2 import the complete row;
    - both entries get their attempts and are delivered;
    - the half row is not imported.

    Retirement and X1 wait at most `LOCK_WAIT`. Then the holder appends the
    rest of its row into the claim file, after the rename. The next flush
    imports it once. A later flush, after acquiring the lock, retires the
    claim. A variant where the holder dies holds the half row as unreadable
    at retirement. In no variant is an obligation lost or doubled.
33. **Publications.** With a fake filesystem that can drop unsynced changes,
    and an injected clock:
    - **A writer stopped before `link()`:** its `tmp/` file is never
      imported; every other file and entry flows; nothing is reported.
    - **A crash after `link()` and before the directory `fsync`:** the file
      may be imported; after a power loss it may vanish, but the writer had
      not exited 3.
    - **A power loss after exit 3:** the file is present.
    - **Two files with the same *X*,** for example a repeated announcement:
      one entry.
    - **A taken name:** `link()` fails, a new suffix is drawn, and nothing
      is replaced.
    - **A `tmp/` file of a gone writer** is removed. That of a stopped
      writer is kept.
    - **A filesystem without hard links:** the publication fails, and no exit
      3 is reported.
    - **An unreadable published file** is held with one alert row.
34. **Import and crash boundaries.**
    - **Crashes:** before IF's `TX`, after it and before the unlink, and
      after the unlink and before the directory `fsync`. No obligation is
      lost or doubled.
    - **Fair and bounded:** with more than `IMPORT_MAX` files and a steady
      inflow of new ones, every file is imported, oldest first, and none
      waits behind files published after it.
    - **Passes:** `import_epoch` grows only when a pass's whole listing is
      imported. An E8 in the middle of a pass is followed by H1 for the
      writer's handoff file, and the entry is not collected before
      `import_epoch ≥ abandon_epoch + 2`.
    - **Legacy claims:** a claim read in several requests keeps its
      occurrence ids. A changed imported prefix holds the claim. A claim is
      never unlinked before a barrier acquisition that followed its first
      import.
35. **A no-byte attestation that recovery overtakes (correction (d)).** A1
    commits, and its reply is lost. The client closes its chat connection,
    and A8c ends the attempt (and, in a variant, the client exits and A8
    does). Then the client's handoff arrives with the matching `void`
    attestation, once by `handoff` request and once by fallback file. The
    attempt becomes `void`, the part `unsent`, and B delivers it once.
    Negative controls, each changing nothing beyond H1:
    - a wrong nonce, writer or generation;
    - an attempt ended by A5;
    - an attempt already `delivered`, `absent` or `dead`;
    - an attempt with an attribution;
    - a `terminal` entry.
36. **An announcement after E1 and before A1 (correction (e)).** The bridge
    is killed after an announcement's E1 commits, before A1, with
    `held_announced` unset and, in a variant, already saved.
    - After the restart, E9 adopts *X*, and the announcement is delivered
      once.
    - The re-raised announcement, with the same *X*, finds the obligation
      and adds no second entry. Its flag is then saved.
    - A variant with the old bridge's A1 committed and its result unknown
      keeps that part UNKNOWN unless its attestation was made durable.
    - An ordinary `pchat post` killed after E1 is still abandoned (E8), with
      its remainder not adopted (D1).
37. **Release packaging and guidance, as corrected (corrections (a)–(c)).**
    These are future release-suite and guidance tests.
    - **Acceptance 11's comparison** ignores the normally generated
      source, builder, release, artifact and hash fields, and passes across
      two commits. It fails when any declarative entry other than those
      section 10.1 lists changes.
    - **`test_skill_guidance.py`** checks that `SKILL.md` explains queueing
      while the bridge's authority cannot answer, even with the chat server
      reachable.
    - **The release suite's builds and installs** use only invented fixture
      releases in isolated temporary directories, under
      `_support.py`/`test_install.py`'s existing isolation.

Tests 38–40 are the fifth amendment's future regressions. They too are
specifications, not runs.

38. **Staging, with #19's formats (option A).**
    - `test_stage`'s happy-path fixtures, built by the changed
      `test_stage.py` and `_staging.py`, use the live baseline formats, and
      still reach `staged`. Every existing protective staging test keeps its
      assertion.
    - **The refusal test.** The release built from #19's own
      `release.json` is refused at preflight against the real
      `staging.json` baseline. Preflight names `outbox` (`outbox/2` against
      `outbox/1`), `delivery-records` (`/2` against `/1`), and each new
      format the live tools don't use. Nothing is staged.
    - `staging.json` and `stage_release.py` are unchanged. The test reads
      them as they are.
39. **Release metadata.**
    - Every new runtime module's wheel path passes `STATE_NAMES` unchanged.
      A negative control named `outbox_*.py` is refused by the build.
    - The `outbox` entry declares `embedded_version: true` with `field: "v"`.
      The format-marker test ties `"outbox/2"` in the shipped code to it.
40. **Captured tests, updated with provenance.** `test_silence.py`, and every
    other captured test whose assertion encodes replaced behaviour, is
    updated in the pull request with a requirement 10 provenance entry. Each
    keeps its protection:
    - a silent run's post is never queued in any form: no fallback file, no
      `queue` or `handoff` request, and no legacy row;
    - its acknowledgements are kept;
    - an entry from before the run keeps its delivery.

    No case is weakened to pass.

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
- the minimal release metadata (section 10.1);
- the authority thread, its socket protocol and the clients' request layer
  (section 2.4);
- fallback files and their lock-free import, the incremental legacy import,
  E9, and A12 from `ended` (sections 3, 6.3 and 6.4).

That is about 1,800 to 3,500 changed lines, including tests.

### 10.1 Release packaging (second amendment, R15)

The release contract
([release_contract.md](release_contract.md#the-manifest)) requires
every persistent format, with the versions read and written, to be declared,
and every runtime module to be in the payload. So the future implementation,
in the same PR #20, makes these minimal declarations in
`packages/release/release.json`:

- **A new `state` format for the SQLite authority.**
  - Path: `$CHAT_STATE/outbox.db`, with its `-wal` file (and `-shm`, should
    SQLite ever create one), and every other new lock or state file the
    implementation ships. That includes:
    - the flush lock `outbox.flush.lock` (`L_flush`), which master does not
      have (section 2.3);
    - the authority lock `outbox.authority.lock` (`L_auth`);
    - the socket `outbox.sock`;
    - the status snapshot `outbox-status.json` (section 2.4).

    This follows correction 2 of the approving issue rereview, and matches how
    `release.json` already lists `outbox.lock` and `identities.lock` in their
    formats' paths. It adds no version.
  - Read and write versions: `outbox-db/1`.
  - Its version is embedded in the `meta` table's schema-version row.
    `outbox-db/1` means a bridge-exclusive database: `writer_model =
    'bridge-exclusive/1'` (section 2.4.1). No earlier `outbox-db` was ever
    shipped, so nothing is renumbered.
- **The `outbox` format.** Its path lists `outbox.jsonl`, `outbox.lock`, the
  claim files, and the fallback directory `outbox.d/` with its staging
  directory `outbox.d/tmp/`. Revision 6's `outbox.bridge.jsonl` is no longer
  listed: the bridge uses fallback files too.
  - It writes `outbox/2`: the per-entry fallback file, with its optional
    attestation (section 6.4).
  - It reads `outbox/1` (the legacy rows in `outbox.jsonl` and claim files)
    and `outbox/2`.
  - This release never writes `outbox/1`.
  - **Its embedded version.** An `outbox/2` file carries `v: "outbox/2"`, so
    the entry's `embedded_version` becomes `true`, with `field: "v"`.
    `outbox/1` rows carry no version, and the entry says so in its `covers`
    text. This is part of the same minimal `outbox` edit under acceptance 11,
    not an unlisted change. `docs/release_contract.md`'s list of formats that
    carry an embedded version names `outbox-db`, `outbox-authority` and
    `outbox/2` as #19's future formats (the canonical issue rereview of #19's
    fourth amendment, addition 2).
- **A new `wire` format, `outbox-authority`: the read-write interface the
  owner approved on 2026-10-10.** It covers the request protocol on
  `$CHAT_STATE/outbox.sock` between the clients (`pchat`, `agentcli`) and the
  bridge's authority, including the peer credentials read from that socket.
  - It reads `outbox-authority/1` and writes `outbox-authority/1`, with an
    embedded version, the frame's `v` field.
  - A peer of an unknown version is refused, and the client queues.

  Amendment 2's B1 allowed only **read-only** host interfaces. This
  read-write format is outside that allowance. It is added by the owner's
  2026-10-10 decision, and by #19's third amendment that records it.
- **The `delivery-records` format.** Versioned post dead letters (`version`,
  `supersedes`) write `delivery-records/2`, and it reads `/1` and `/2`.
- **Every new runtime module** the implementation adds under
  `packages/chat/scripts` is listed in `package.files`. It is also recorded in
  `provenance.json` as a plateia-only file.
  - **Its name** must not match the builder's unchanged privacy scan,
    `build_release.STATE_NAMES` (`build_release.py:81-82`). That scan flags
    any wheel member whose base name starts with `outbox` or ends in
    `.jsonl`, among other state names, and the build refuses such an
    artifact (`:316-319`). So a module is named, say, `delivery_store.py`,
    never `outbox_store.py`. The scan is not changed (addition 1).
- **The interpreter note** says that the standard library's `sqlite3` module
  is required.
- **New host interfaces the probes read:**
  - a TCP socket listing, if it is not the existing `open-files` record;
  - the boot id.

  Each is a read-only `wire` format. Process start time is already declared as
  `process-table`.

Nothing else in the release changes: the builder, verification, staging, other
format entries, the skill, and the package version policy. If declaring these
needs any builder change, the implementer stops and asks. The one exception is
the staging test support below.

**Staging test support (option A; the owner's decision of 2026-10-10, 12:29
UTC).** The canonical issue rereview of #19's fourth amendment found that
these declarations make the required `test_stage` suite fail.

- **Why it fails.**
  - `test_stage` builds its fixture release from the checkout's own
    `release.json` (`test_stage.py:40-43`).
  - It then stages that fixture against `staging.json`'s `live_versions`
    (`_staging.py:116-120`).
  - Preflight refuses a release that writes `outbox/2` or
    `delivery-records/2` where the live tools use `/1`, and refuses any
    declared format the live tools don't use (`stage_release.py:838-885`;
    [release_staging.md](release_staging.md)).
- **The exception.** The future implementation may minimally change exactly
  two test-support files: `packages/release/tests/test_stage.py`, and the
  helper `packages/release/tests/_staging.py` under `tests/` (not
  `stage_release.py`).
  - **What the change does.** Their invented, isolated fixture releases are
    built with the **baseline** formats that the staging happy-path tests
    need: the live versions.
  - **What it must never do.** A fixture never presents #19's changed
    production manifest as if its new formats were already live.
- **A new future test: #19's real release is refused.** The release built
  from #19's own `release.json` is **refused** at preflight:
  - against the live `outbox/1` and `delivery-records/1` versions;
  - against the existing declared live-format set, for `outbox-db`,
    `outbox-authority` and the new host interfaces.

  That refusal is the correct safety outcome until a separately approved
  activation and cutover. Nothing makes a refusal look like a successful
  stage.
- **What stays.** The existing protective staging tests keep their intent,
  and compatibility is not weakened to pass the suite.
- **Unchanged:** `staging.json`, `stage_release.py`, operational staging,
  the builder and its privacy scan, activation, and the sequencing of
  PLT-12 and PLT-14.
- **Not a project.** This is not a staging or activation prerequisite
  project. Until activation provides a migration path, no plateia release
  that includes #19 passes staging preflight against the live baseline. That
  is consistent with "Rollback is not claimed".

**Fixture builds and installs** (correction (c) of the canonical issue review
of #19's third amendment):

- **What the exclusion covers.** The exclusion of building, staging,
  installing and activating a release covers **operational** releases only.
- **What it allows.** The future implementation's release suite may build
  and install **invented fixture releases** in isolated temporary
  directories, as acceptance 11 and the local release-suite gate require.
  These are `packages/release/tests/_support.py:54-72` and
  `test_install.py:43-61`.
- **What it does not allow.** No operational build, staging, install or
  activation. And nobody runs any of it now, not the manager, not the
  solver, and not a reviewer.

**Acceptance 11's "unchanged".** Following correction 1 of the approving issue
rereview, and correction (a) of the canonical issue review of #19's third
amendment, acceptance 11 is read this way:

- **What may change.** Only these:
  - the edits to the `outbox` and `delivery-records` entries,
    `package.files` and the interpreter note;
  - the new `outbox-db`, `outbox-authority` and host-interface entries.
- **What is not compared.** The fields the builder generates from the
  commit, the payload and the build environment
  (`packages/release/build_release.py:300-336`): the source, builder,
  release, artifact and hash fields. The test does not require identical
  manifests across different source commits.
- **What stays the same.** Everything else, in `release.json` and in the
  built manifest's declarative content: every other format entry, the
  package version policy, and the release behaviour.

The format-marker test also ties the shipped code's protocol version string
to `outbox-authority`.

**Cutover, separately authorized.** Moving the live system to this release is
quiescent, and belongs to activation:

- the running bridge and the old tools' flush are stopped first;
- no client of this release opens the database;
- any other writer is refused, by the exclusive lock and the `writer_model`
  check (section 2.4.1, C-9), so a mixed old writer that uses SQL directly
  cannot join;
- old tools that only append `outbox.jsonl` stay an import boundary. The
  bridge reads their rows without `L_outbox`, and needs the lock only to
  delete a claim file (section 6.3). Their flush, which claims and delivers
  rows itself, must be stopped.

No live migration is claimed.

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
- **Bridge-down queueing.** While the authority is unavailable, posts wait
  for the bridge instead of reaching the channel (section 2.4.4). The owner
  accepted this on 2026-10-10.
- **Latency.** Each transition of a direct post is one local request, and
  one full flush of the disk. Every wait stays within its absolute deadline.
- **Threads.** The bridge gains one thread. Only that thread touches SQLite,
  and shared helpers are made thread-safe (section 2.4.3).
- **Hard links.** Fallback publication needs `link()` on the state
  directory's filesystem (C-11). Without it, a post that cannot reach the
  authority fails as today when the outbox cannot be written, instead of
  queueing.
- **Leftover files.** A claim file waits for the lock barrier before
  deletion, and a stopped writer's `tmp/` file stays until it is gone. Both
  cost only disk space, and `pchat status` shows them.

## 11. Owner decisions (2026-10-09 and 2026-10-10)

The owner approved decisions 1–6 on 2026-10-09, and decisions 7 to 9 on
2026-10-10. They are recorded as D-73 in
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

7. **The bridge is the only outbox authority (2026-10-10).** The owner
   approved the write-routing proposal made after design review round 4,
   with its tradeoffs and review plan.
   - **The writer.** The bridge is the only process that opens or writes the
     database. Clients send complete, validated, bounded requests over a
     local socket, and keep their own sends to the chat server (section
     2.4).
   - **R6 and C-8.** R6 is preserved as approved, and revision 5's C-8
     exemption is rejected and removed.
   - **Queued while unavailable.** While the authority is unavailable,
     posts, acknowledgements and notices are queued durably instead of sent.
     This is a material behaviour change, accepted with its preserved
     delivery obligations (section 2.4.4).
   - **A new interface.** The new read-write interface `outbox-authority/1`
     extends the release scope beyond B1's read-only host interfaces
     (section 10.1).
   - **The review plan.** One refreshed canonical review of #19, as amended
     a third time. Then, only if it approves, the remaining final design
     review round 5. There is no further round.

   This approves the design, its scope and its review only.

8. **Per-entry fallback files and the review's corrections (2026-10-10,
   11:31 UTC).** The canonical issue review of #19's third amendment found
   that a fallback row already durable in `outbox.jsonl` still waited for a
   client stopped while holding `outbox.lock`. The owner approved the
   manager's repair, with five further review rounds at most:
   - **Per-entry fallback files.** A queueing caller publishes one complete,
     immutable fallback file per entry, and the bridge imports published
     files, and legacy rows, without `outbox.lock`. A stopped writer delays
     only its own unfinished publication (sections 6.3 and 6.4, I-12).
   - **R6 is preserved** over the database and over already durable
     fallback obligations. It is not narrowed: no exemption, lock stealing,
     lease, or killing of holders. Everything decision 7 adopted stands.
   - **The five corrections:**
     - (a) acceptance 11's comparison excludes generated fields (section
       10.1);
     - (b) future guidance on queueing while the authority cannot answer
       (section 8, R11);
     - (c) isolated fixture builds and installs for the future release suite
       (section 10.1);
     - (d) a no-byte attestation that survives recovery (A12, section 3.3);
     - (e) an announcement's durable entry is an obligation, not proof of
       sending (E9, section 6.4).
   - **Review budget.** At most five further review launches, shared by the
     canonical issue review of #19 and the design review. The unused design
     round 5 is one of them, not a sixth.

   This approves the specification, the design and their review only.

9. **Staging test support and the rereview's corrections (2026-10-10, 12:29
   UTC).** The canonical issue rereview of #19's fourth amendment found that
   the required release declarations make the required `test_stage` suite
   fail, while every file that could fix it was excluded. The owner approved
   option A:
   - **Staging test support.** The future implementation may minimally change
     exactly `packages/release/tests/test_stage.py` and
     `packages/release/tests/_staging.py`. Their invented fixture releases
     then use the live baseline formats.
   - **The refusal test.** A future test shows that #19's real release is
     refused at preflight against the live versions.
   - **Unchanged:** `staging.json`, `stage_release.py`, operational staging,
     the builder, activation, and PLT-12 and PLT-14 sequencing (section
     10.1).
   - **The rereview's three corrections and three additions** are carried
     (sections 2.4.4, 6.4, 8 and 10.1, and tests 38–40).
   - **The review budget is unchanged:** one of five used, four remaining.

   This approves the specification, the design and that test-support scope
   only.

Implementation is **not** approved by these decisions. Implementation, code,
tests and the `release.json` edit need their own owner decision, and so does
activation.

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
- **Revision 5** (`abfd9e0`) addresses those five, and carries the
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
    5.6 cases 9 and 10, test 23). C-8 stated the database's single-writer
    limit, bounded by `BUSY_WAIT`. (Revision 6 removed both.)
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
- **Design review round 4** requested changes on revision 5, with three
  findings.
- **Revision 6** (`6dfc420`) adopts the owner's 2026-10-10 decision
  (section 11, decision 7), which answers all three:
  - **A paused client stalls the database (P1).** The bridge is now the only
    process that opens or writes the database. Clients send bounded,
    validated requests over a local socket, and the authority thread runs
    every transaction, never waiting for a client (section 2.4). R6 holds
    with no exemption: C-8 is removed, and C-9 (who opens the database) and
    C-10 (local storage) state exactly what remains. The other parts:
    - replies only after a durable commit, replay-safe ops and epochs
      (section 2.4.2);
    - A12 and attested outcomes, applied only on the writer's own
      nonce-bound statement (sections 3.3 and 6.4);
    - exclusive locking, the authority lock, the socket path limit and the
      peer check (section 2.4.1);
    - the status snapshot (section 2.4.5);
    - I-11, and section 5.6, cases 11 and 13.

    While the authority is unavailable, clients queue instead of sending.
    The owner accepted that as a material behaviour change (section 2.4.4,
    and R9 in section 8).
  - **The announce fallback blocks the bridge (P1).** No bridge flow appends
    to `outbox.jsonl`. `announce` falls back to the bridge-private
    `outbox.bridge.jsonl` with a derived *X*, imported by I0, and
    `held_announced` is saved only after durability (sections 2.3 and 6.4,
    and section 5.6, case 12).
  - **Single-line accounting (P2).** R2 condition 5 applies to every part,
    as #19's A3 requires (section 7.3).
  - **Release.** Section 10.1 adds the `outbox-authority` read-write wire
    format, the new state paths, the attestation in `outbox/2`, and a
    quiescent cutover that refuses any other writer.
  - **Tests.** 25–31 added; 11 and 17 and the setup updated.
  - **Revision 5's history** above mentions C-8. Revision 6 removes it.
- **The canonical issue review of #19's third amendment** requested changes
  on revision 6. Its open decision: a fallback row already durable in
  `outbox.jsonl` waited for a client stopped while holding `outbox.lock`. It
  also made five corrections and additions.
- **Revision 7** (`bb3cd76`) adopts the owner's decision of 2026-10-10,
  11:31 UTC (section 11, decision 8):
  - **Per-entry fallback files (the open decision).** Every queueing caller,
    the bridge's `announce` included, publishes one complete, immutable file
    in `outbox.d/`, by exclusive creation, `fsync`, a `link()` that never
    replaces, and directory syncs (section 2.1). IF imports published files
    without `L_outbox`, at most `IMPORT_MAX` per flush, oldest first. Legacy
    `outbox.jsonl` is claimed and read without the lock, incrementally, and
    the lock is only a barrier before a claim file is deleted (section 6.3).
    No client takes `L_outbox`. Revision 6's fallback append,
    `outbox.bridge.jsonl`, its mutex and I0 are removed. Sections 2.1–2.3,
    3.1, 3.5, 6.1–6.5, I-5, I-7, I-10 and I-11 are updated, and I-12 and C-11
    are new. H1 and collection now count two import passes after E8, because
    a pass may span flushes.
  - **(a)–(c)** in sections 8 and 10.1.
  - **(d)** A12 also applies to an attempt that A8 or A8c has ended, under
    exact identity, nonce, generation and state guards. P9 is new (section
    3.3).
  - **(e)** E9 adopts a gone bridge process's `announce` entries, and an
    existing entry never suppresses unsent parts (sections 3.1 and 6.4). E8
    and D1 are unchanged for every other direct entry.
  - **Section 5:** the crash tables, and cases 9, 10, 12, 14 and 15.
  - **Tests:** 32–37 added; 11, 14, 17, 21–23 and 27–29 updated.
  - **Reassessment.** Nothing earlier is weakened, and no classification is
    lowered:

    | Item | Revision 7 |
    |---|---|
    | F1, fragments; R2 condition 5 on every part | Unchanged (sections 5.3 and 7.3). |
    | F2, ack retry | Unchanged (A11). |
    | F3, durable claims | Kept: I1 still makes every claim durable before I2. Claiming no longer needs `L_outbox`, and retirement waits for the barrier (section 6.3). |
    | F4, reopenable progress | Kept, with two passes after E8, because a pass may span flushes (I-7). |
    | F5, export durability | Unchanged. Publication adds the same file-then-directory rule (section 2.1). |
    | F6, late outcomes | Unchanged. |
    | F7, failures before E1 | Kept: Q0, or a fallback file, for every caller. |
    | I-1 to I-4, I-6, I-8, I-9 | Unchanged. A `void` attempt published nothing, so I-3 and I-4 are untouched. |
    | I-2, fencing | Unchanged. A12 from `ended` records past sends, and permits none (section 3.3). |
    | I-5, storage errors | Fallback files replace the fallback append (section 6.4). |
    | I-7, collection | Two passes after E8 (above). |
    | I-10, I-11 | Strengthened: no import waits for any lock. I-12 is new. |
    | C-9, C-10 | Unchanged. C-11 states the fallback directory's storage. |
    | Third amendment's acceptance 12, items 1–6 | Tests 25, 26, 28, 29, 30 and 31, with 29 now using fallback files. |
    | The canonical approval's clarifications | Unchanged: the implementation gate, no designation interface, SQLite and sockets inside the test sandbox only, the release suite among the local gates, and failing-first evidence that shows the defect. |
- **The canonical issue rereview of #19's fourth amendment** requested
  changes on revision 7. It resolved every point of the previous review, and
  found one open decision: the required release declarations break the
  required `test_stage` suite, and every file that could fix it was excluded.
  It also made three corrections and three additions.
- **Revision 8** (this revision) adopts the owner's decision of 2026-10-10,
  12:29 UTC (section 11, decision 9):
  - **Option A (the open decision).** Section 10.1 allows minimal future
    changes to `test_stage.py` and `_staging.py` under
    `packages/release/tests/`, so their fixtures use the live baseline
    formats. It adds a future test that #19's real release is refused at
    preflight, and keeps operational staging unchanged.
  - **Correction 1:** a fallback file is published only when no `queue` or
    `handoff` request is answered as committed, or to make an attestation
    durable (section 6.4).
  - **Correction 2:** `L_outbox` still serializes dead-letter writes; its
    legacy role is only the retirement barrier (section 2.3).
  - **Correction 3:** while the authority is unavailable, no line of a part,
    acknowledgement or channel recreation goes out without a committed
    attempt, and logging in before E1 is allowed (sections 2.4.4 and I-1).
  - **Additions:**
    - module names that avoid `STATE_NAMES`;
    - `outbox/2`'s embedded version in the `outbox` entry and in
      [release_contract.md](release_contract.md);
    - captured tests such as `test_silence.py` updated with provenance
      (sections 8 and 10.1).
  - **Tests:** 38–40 added.
  - **Unchanged:** everything revision 7 adopted. No resolved finding is
    reopened.
