# Writer admission: serialized maintenance ownership of the shared chat state

This is the design for #23. It specifies a **serialized writer-admission
protocol** for the shared chat state (`$CHAT_STATE`, default
`~/.local/state/chat`). With it, an operation such as #21's activation can
take durable, operation-bound **maintenance ownership** that drains every
in-flight write, refuses every new one visibly without losing work, survives
the operation's crash, and covers code that cannot take part (legacy writers)
through verified retirement and inhibition.

**Status.** Design only. #23 was approved with its canonical rereview
(APPROVE, 2026-10-12). Its corrections and spec additions are normative here.
Implementing this design, and its budget, are a later owner decision.
Nothing in this document authorizes building, staging, activating, probing or
changing anything live, and nothing in it changes #19's or #21's contract until
the owner approves the implementation. Where it proposes a change to those
contracts, it says so and enumerates it.

**Implementation (#24; owner decision 2026-10-12, D-75).** The chat package's
side of this protocol is implemented by #24 (pull request #25): the record reader, the
`L_adm` sections, per-transaction admission and the `maintenance` reply, the
refusal sink and its import, the bridge's hold, exit 5, and the owning
operation's primitives (`delivery_store.Owner`). The primitives live in the chat
package, not in the controller: see section 10.2, "Placement".

**#21's side (owner amendment 4, 2026-10-13; APPROVE 6151464851).** The owner
approved #21's contract changes (G-3), and PR #22 implements them: the
controller drives `Owner` under its fenced journal (acquisition, the binding's
boot, `full`, the release and the reconcile in `status`), keeps its own
decisions (retirement, inhibition, targets and the bootstrap before `full`,
the `release` command and its post-check, rollback's record check and its
release after a boot change), and the bridge's `full`-plus-binding start is
one operation-owned section (`delivery_store.Authority.start_section`). The
APPROVE's four clarifications (K1 to K4) are part of that contract; see
[release_staging.md](release_staging.md#activation-plt-14-21). Merge,
deployment, the activation and the cutover stay separately gated, and G-1,
G-2 and G-9 stay the owner's undecided gates. Nothing here waives a gate or
activates anything.

Every example uses invented data: projects `alpha` and `beta`, the owner
`pat`, the assistant `sam`, the agent `alp-solver-2`, the activation operation
`activate-0a1b2c3d4e5f6071`, and the boots `boot-A` and `boot-B`.

## Contents

1. [Source baselines](#1-source-baselines)
2. [Terms](#2-terms)
3. [Writer inventory and the safety boundary](#3-writer-inventory-and-the-safety-boundary)
4. [The protocol](#4-the-protocol)
5. [Refusal](#5-refusal)
6. [Operation-owned writes and the bootstrap](#6-operation-owned-writes-and-the-bootstrap)
7. [Lock ordering and partially completed posts](#7-lock-ordering-and-partially-completed-posts)
8. [Legacy retirement and inhibition](#8-legacy-retirement-and-inhibition)
9. [Ordering with #21, and its quiet-writer clauses](#9-ordering-with-21-and-its-quiet-writer-clauses)
10. [Formats, compatibility and affected paths](#10-formats-compatibility-and-affected-paths)
11. [Changes to #19's contract](#11-changes-to-19s-contract)
12. [Rollback](#12-rollback)
13. [Threat boundary](#13-threat-boundary)
14. [Blocking gates](#14-blocking-gates)
15. [Test designs (invented fixtures)](#15-test-designs-invented-fixtures)
16. [Acceptance map](#16-acceptance-map)
17. [Owner decisions and delivery](#17-owner-decisions-and-delivery)

## 1. Source baselines

The boundary analysis covers exactly these revisions. Every code citation
names its file and line at the revision stated here.

| Source | Revision | What it covers |
|---|---|---|
| The chat package, `packages/chat/` | master `a8ce42bc` (`a8ce42bcbf6c00d683b5e8e513cfb68e6d5b79ec`) | Every new-release write path, client and lock. Citations without a revision are at this one. |
| The activation controller and the bridge's designation bootstrap | PR #22's head `e2cd9cef` (`e2cd9cef49bc0103380f399d5e26fa3f127cd972`) | `packages/release/activate_release.py`, `packages/release/activation.json`, and the bootstrap handling in `chat-bridge`. None of these exists at `a8ce42bc`: no file under `packages/` there names an activation or designation bootstrap. Cited as `path@e2cd9cef:line`. |
| The legacy (D-19 baseline) writers | the capture commits `9542466` (PLT-9) and `937e19e` (PLT-15) | What the legacy code writes and how it locks. The live skills tree is held to these by the drift check (`docs/release_staging.md:59-67`). Cited as `path@9542466:line` or `path@937e19e:line`. |
| The documents | master `a8ce42bc` | Line numbers in `docs/` are those before this design's landing. |

PR #22 changes only `chat-bridge`, `provenance.json` and tests in the chat
package relative to `a8ce42bc`; every other chat-package citation holds at
both revisions.

**Recheck rule.** PR #22 is open and unmerged. If its head moves past
`e2cd9cef`, or master's chat package moves past `a8ce42bc`, the inventory in
section 3 and the controller citations in sections 6 and 9 are rechecked
against the new revisions before #21's cutover. The cutover stays blocked
until that recheck is recorded (gate G-8).

## 2. Terms

- **Writer.** Any process that writes inside the safety boundary (section 3).
- **New-release writer.** A writer running code that implements this
  protocol: the release #21 activates, once it carries this design.
- **Legacy writer.** A writer running the D-19 baseline code, or any other
  code that does not implement this protocol, including a source checkout of
  this repository from before the implementation.
- **Section.** One writer's unit of work that must not straddle a change of
  ownership: a client's whole post, acknowledgement or notice from its first
  state-changing request to its end; one bridge step; one authority
  transaction (section 4.2).
- **`L_adm`.** The admission lock, a flock on `$CHAT_STATE/admission.lock`.
  Writers hold it **shared** for each section. The owning operation takes it
  **exclusive**, never blocking, only to complete a drain.
- **The record.** `$CHAT_STATE/maintenance.json`, format `maintenance/1`
  (section 4.3). Its presence, in any state, means maintenance is held. An
  unreadable or invalid record is **unknown**, which writers treat as held.
- **Owning operation.** The operation the record names. For #21, the
  activation's journaled operation id.
- **Drained.** No section of any new-release writer is open, and none can
  open: the owner held `L_adm` exclusive after the record was durable.
- **Release.** The owning operation's explicit removal of the record, the
  moment writers are admitted again.

## 3. Writer inventory and the safety boundary

### 3.1 The boundary rule

A path is **inside** the safety boundary if any of these holds:

1. **Changed format.** Its format changes in #21's admitted transition, or the
   transition adds it (`activation.json@e2cd9cef:4-19`).
2. **Judged by the cutover.** #21's controller writes it, or judges the first
   new-format write from it (`activation.json@e2cd9cef:20-25`).
3. **Read by a decision.** The outbox authority or the bridge decides from it:
   delivery, reconciliation, coverage, designation, acknowledgement, recovery
   or dead-lettering.
4. **Two meanings.** Old and new code could both write it with different
   meaning or locking.

Everything the bridge writes is inside, except its diagnostic log: the bridge
is held as one unit (section 4.6), so its paths need no separate reasons.

A path is **outside** only when none of the four holds. Each exclusion below
names why. A later maintenance operation that reads or changes an excluded
path must bring it inside first: the exclusions are for this boundary, not
permanent.

### 3.2 New-release write paths

| Path (what it writes) | Write site | Today's serialization | Classification |
|---|---|---|---|
| `outbox.db` and its `-wal`/`-shm` files (`outbox-db/1`) | Authority transactions: `Store.tx` (`delivery_store.py:831-838`), run by `Authority.handle` for every op, read-only `query` and `status` included (`:1843-1869`, `:1901-1903`), and by `run_tx` for in-process requests (`:1905-1934`) | `Store.tx` serializes SQLite transactions on the authority's one connection only | **Inside** (rules 1, 2, 3). Each mutating transaction is a section (section 4.5). |
| The authority lock `outbox.authority.lock` | `Authority._open` (`delivery_store.py:1749-1779`) | See section 3.3 | **Inside**, as part of the authority's start (section 6). |
| `outbox.sock` | `Authority.bind` (`delivery_store.py:1781-1807`) | Bound only while holding the authority lock | **Inside**, as part of the authority's start. |
| `outbox.d/` and `outbox.d/tmp/` (`outbox/2` fallback files) | `publish_fallback` (`delivery_store.py:655-685`), called by `_Call.fallback` (`chatlib.py:534-542`); directories made at authority start (`delivery_store.py:1775-1779`) | **None.** It creates, links and syncs with no admission lock | **Inside** (rules 1, 2). Publication is part of the caller's section. |
| `outbox.jsonl`, `outbox.claimed-*.jsonl`, `outbox.lock` (`outbox/1`, read and retired only) | The bridge's import and retirement (`chat-bridge:1295-1324`) | `L_outbox` for retirement and dead letters (`chat-bridge:1314-1324`) | **Inside** (rules 1, 3, 4). |
| `outbox.flush.lock` | `flush_outbox` (`chat-bridge:1502-1517`) | Single flusher (`:1513`) | **Inside**: bridge. |
| `outbox-status.json` (the status snapshot) | `Flusher.write_status`, `durable_replace` of `paths.status` (`chat-bridge:1484-1492`, at `:1490`); `write_unavailable_status` (`chat-bridge:1520-1530`), outside any transaction | **None** | **Inside** (rule 1: it is in the `outbox-db` path list, `release.json:82-85`). **Deferred** while maintenance is held (section 6.4). |
| `dead-letters.jsonl` (`delivery-records/2`) | `sink_append` under `L_outbox`: `Deliveries._dead` (`chat-bridge:574-585`) and the export (`chat-bridge:1428-1452`) | `L_outbox` | **Inside** (rules 1, 2). |
| `deliveries.jsonl`, `acks.jsonl` (`delivery-records`) | `durable_append` (`chat-bridge:478`, `:1569`) | None across bridges | **Inside** (rules 1, 3): bridge. |
| `pending.json` (`pending/1`) | `Deliveries._save`, a `durable_replace` (`chat-bridge:400-405`) | None across bridges | **Inside** (rules 3, 4): bridge. Two bridges writing it is exactly the double-bridge hazard. |
| `checkpoints.json` (`checkpoints/1`) | `Checkpoints.set`, a plain temporary write and rename (`chat-bridge:266-271`) | None | **Inside** (rule 3: catch-up and coverage start from it, `chat-bridge:1772-1787`): bridge. |
| Channel logs `logs/<channel>.jsonl` (`channel-log/1`) | `Record.append`, an unlocked append (`chat-bridge:217-224`) | **None** | **Inside** (rule 3: the bridge's deduplication and catch-up cursor read them, `chat-bridge:195-245`, and citations of authority are found in them, `chatlib.py:83-113`): bridge. Deferred, never lost: the checkpoint does not move while held, so the bridge's catch-up from the checkpoint after release replays the interval from the chat server's history (`chat-bridge:1772-1777`; gate G-6). |
| `evidence/`, `evidence.jsonl` (`evidence/1`) | `capture_cited` (`chat-bridge:333-363`), inside message handling (`:1672`) | None | **Inside**: bridge (written in the message section). |
| `bridge.log` (`bridge-log/1`) | `log()` (`chat-bridge:130-142`) | None | **Outside.** It is diagnostic and write-only: nothing in the package reads it (`release.json:100-101`, `read: []`), no decision depends on it, its format is unchanged, and it rotates itself. It must stay writable so that a held bridge can say why. |
| `designation-bootstrap.json` (`designation-bootstrap/1`) | Only the activation controller, once (`docs/release_staging.md:668-680`; `activate_release.py@e2cd9cef:479-575`) | The controller's fenced journal | **Inside** (rules 1, 2). An operation-owned write (section 6). |
| `maintenance.json`, `admission.lock` (`maintenance/1`, new) | Only the owning operation writes the record, through `delivery_store.Owner` (shipped in the chat package, section 10.2), under that operation's fence; ordinary writers only read it, and any writer may create the empty lock file (section 4) | This protocol | The protocol's own state (section 4.3). |
| `refused.d/`, `refused.d/tmp/` (the refusal sink, new; `outbox/2` files) | Only refusals, only while maintenance is held (section 5) | None needed: immutable files, published like fallback files | **Inside** (rule 2, once section 12's boundary is adopted). Written only on the refusal path; imported only after release. |
| `identities.json`, `identities.lock` (identity registry, version 1) | `identities.register`, `update` and `sync` (`identities.py:165`, `:193`, `:422`) through `atomic_json` (`:43-60`) | Its own blocking flock (`identities.py:63-71`) | **Outside.** The legacy and new code are byte-identical (`identities.py` is unchanged from `9542466` to `a8ce42bc`), so both write one format under one lock (rule 4 does not hold). The cutover neither reads nor writes it, and the authority never reads it. The bridge reads it only inside its delivery sections, which are held. |
| The chat config `$CHAT_CONFIG` (`chat-config/1`) | `identities.register` (`identities.py:169`, `:183`) | The registry's flock | **Outside.** It is not in `$CHAT_STATE`, its format is unchanged, the cutover reads no configuration (`docs/release_staging.md:147-150`), and the authority never reads it. |
| Agent-run output `agents/<name>/` (`agent-runs/1`) | `agentcli.run_agent` (`agentcli.py:47-48`, `:84-87`, `:95-99`) | None; one directory per allocated identity | **Outside.** Write-only (`release.json:95-96`, `read: []`), unchanged format, one writer per directory, and nothing in the transport or the cutover reads it. |
| Agent-run state `~/.local/state/project-manager/<project>/runs/` (`childrun/2`) | `runstore` (`runstore.py:37`, `:77-82`) | Per-run files | **Outside.** Not in `$CHAT_STATE`, unchanged format, and nothing in the transport or the cutover reads it. `runstore.py` is unchanged since `9542466`, and imports only `binding` (`runstore.py:24-35`): it posts nothing. |
| Ergo's diagnostic log and its rotated copies | `rotate-logs` (`rotate-logs:28-30`, `:40-55`) | None | **Outside.** It writes only `~/.local/state/ergo/` and signals Ergo; it expressly excludes the bridge's logs (`rotate-logs:10-15`). |
| The receipt-experiment marker | Read only (`receipt_experiment.py:7`, `:15`) | — | **Outside**: no write. |
| WeeChat options | `role_colors.py` | — | **Outside**: it writes WeeChat's own options, imports no chat module, and touches no chat state. |
| `binding.py` | — | — | **Outside**: no write. |
| `pchat` (entry) | Through `chatlib` and `identities` only; its status, read and unaccepted commands only read (`pchat:85-195`) | — | Through the modules above. |

**Clients.** `pchat post` and `pchat ack` (`pchat:284`, `:313`), an agent's
notice (`agentcli.py:17-36`), and the bridge's own `announce`
(`chat-bridge:1052-1081`) all write through `chatlib`'s `_Call`: authority
requests, then on failure a fallback file (`chatlib.py:518-542`). Each call
is one section (section 4.2). The client sends its bytes between its
`attempt` and `outcome` requests (`chatlib.py:592-604`).

### 3.3 Locks that exist today

- **The authority lock (`L_auth`).** `Authority._open` takes
  `outbox.authority.lock` with a non-blocking exclusive flock and keeps the
  descriptor (`self.lock_file = f`, `delivery_store.py:1755-1773`). It is
  released only in `close()` (`:1836-1839`). It therefore **excludes a second
  bridge authority for the authority's whole lifetime, and admits or excludes
  no other writer.** Legacy code never takes it.
- **`Store.tx`** serializes SQLite transactions on the one connection only
  (`delivery_store.py:831-838`).
- **`L_flush`** (`outbox.flush.lock`) admits one flusher (`chat-bridge:1513`).
- **`L_outbox`** (`outbox.lock`) orders dead-letter writes and claim-file
  retirement (`chat-bridge:579`, `:1315`). The legacy code takes it, blocking,
  to append and claim (`chatlib.py@9542466:371-402`).
- **`identities.lock`** serializes the identity registry (`identities.py:63-71`).
- **Nothing** serializes fallback publication, channel-log appends, the status
  snapshot, checkpoints, the pending queue or the ledgers across processes.

None of these is an admission barrier. This design adds one, `L_adm`, and
changes none of the above.

### 3.4 Callers and launchers

| Launcher | Starts | Classification |
|---|---|---|
| `~/.local/bin/pchat` | `pchat` | Managed target; bound by #21 (`docs/release_staging.md:41-49`). |
| The chat skill's commands (`~/.codex/skills/chat`, `~/.claude/skills/chat`, the `chat/scripts` import location) | `pchat`, modules by path | Managed targets; bound. |
| The `chat-bridge` LaunchAgent (`com.coghex.chat-bridge`) | the bridge | Managed; bound with `--activation-bootstrap sha256:<D>`. |
| The log-rotation LaunchAgent (`com.coghex.log-rotate`) | `rotate-logs` | Managed; bound. Writes no chat state either way. |
| The WeeChat `role_colors` autoload link | `role_colors.py` | Managed; bound. Writes no chat state either way. |
| The five path-bound importers (`launch-worker`, `childrun`, `report`, `reconcile`, `modelclass`) | modules through `chat/scripts` | Reach the bound import location; processes started before binding keep legacy code loaded (section 8). |
| Unlisted importers found by the launcher search | modules through `chat/scripts` | Reach the bound import location too. The search covers only two roots and marks excluded coverage incomplete (`docs/release_staging.md:91-145`). |
| Agent hooks, other projects' sessions, schedulers, other LaunchAgents, unmanaged copies, source checkouts of this repository | anything | **Unmanaged.** Not visible to a source-based inventory. Gate G-2. |
| Editor and terminal integrations | unknown | **Unknown.** WeeChat's only chat-package code is `role_colors.py` (outside). Anything else is gate G-2. |

### 3.5 Legacy writers

The legacy writers are the D-19 baseline copies in the managed skills tree and
every running process that loaded them. Source facts, at the capture commits:

- `pchat post` and `pchat ack`, an agent's notice, and the bridge's
  announcement append to `outbox.jsonl` under a blocking `L_outbox` when a
  post fails (`chatlib.py@9542466:381-393`; `pchat@937e19e:228`, `:239`;
  `agentcli.py@937e19e:32`; `chat-bridge@937e19e:654`).
- **The only flusher is the bridge**, `flush_outbox`, which claims and
  reposts the outbox (`chat-bridge@937e19e:752-782`, called at `:994`). It
  takes no flush lock and no authority lock, so two legacy bridges would both
  flush.
- The legacy bridge writes every bridge path in section 3.2, under the old
  formats.
- None of it reads `maintenance.json` or takes `L_adm`. It cannot honour the
  protocol, so it is covered only by retirement and inhibition (section 8).

### 3.6 Unknowns

Each unknown the issue names is closed from source where the source can close
it, and otherwise recorded as a blocking gate (section 14). No live inventory
was made.

| Unknown | Status |
|---|---|
| Launchers outside #21's seven managed targets | Open: gate G-2. |
| Whether editor or terminal integrations write chat state | Closed for the package (`role_colors.py` writes none). Open for anything outside it: gate G-2. |
| Whether any write path bypasses `delivery_store` and `chatlib` | Closed for the package: section 3.2 lists every bypass (the bridge's own files, `identities`, `agentcli`'s run output), each classified. Open for programs outside the package that write `$CHAT_STATE`: gate G-2. |
| The full set of flusher instances | Closed from source: in both baselines, only a bridge process flushes (section 3.5; `chat-bridge:1502-1517`). How many legacy bridge processes run is a live fact, covered by retirement (section 8, gate G-1). |
| Whether cached long-lived processes hold open write descriptors | Closed by retirement at a boot boundary, if the owner accepts it (gate G-1). Otherwise open, and the cutover stays blocked. |

## 4. The protocol

### 4.1 Overview

The proposed shape in #23's Requirement 1 is confirmed, with one change: the
drain is per **section**, not per write, so that no post is ever split across
a change of ownership.

- Every new-release writer holds `L_adm` **shared** for each section, and
  reads the record **while holding it**, before the section's first write.
- The owning operation first makes the record durable (state `draining`),
  then takes `L_adm` **exclusive**, without blocking, retrying until a
  deadline. Holding it, it records `partial`. Holding the exclusive lock
  proves that no section is open, and the durable record proves that none
  can start.
- Ownership is the durable record, not a held lock. A crash leaves the record,
  so writers keep refusing.
- Admission is never decided by a timestamp, a recheck or an attestation:
  only by `L_adm` and the record's presence and content.

**Why this serializes.** A section reads the record while holding `L_adm`
shared. The owner writes `partial` while holding `L_adm` exclusive, after the
`draining` record was durable. So every section either:

- read "no record" and holds `L_adm` shared, in which case the owner cannot
  hold it exclusive until the section ends; or
- began after the `draining` record was durable, and read it, so it never
  started its writes.

There is no instant at which a section has checked and not yet acted while
the owner believes it is drained. That is the difference from a quiet window.

### 4.2 Sections and `L_adm`

| Writer | One section is | Begins | Ends |
|---|---|---|---|
| `pchat post`, an agent's notice, the bridge's `announce` (`chatlib.post`, `chatlib.py:635-673`) | one call | before its first `create`, `queue` or fallback publication (after login, which writes nothing) | when the call returns, raises `Queued` or `Refused`, or fails |
| `pchat ack` (`chatlib.ack`, `chatlib.py:683-709`) | one call | as for a post | as for a post |
| The bridge's message handling (`chat-bridge:1582-1673`), including its checkpoint move (`:1743`, `:1783`) | one message | before the message is handled | after its checkpoint is saved |
| `Deliveries.process` (`chat-bridge:715-735`) | one pass | before the pass | after it |
| The flush (`Flusher.run`, `chat-bridge:1295-1312`) | one step, or one entry's delivery (`deliver_entry`, `chatlib.py:716-761`) | before each | after each |
| An authority transaction that mutates (`delivery_store.py:1843-1869`, `:1905-1949`) | one transaction | before `BEGIN IMMEDIATE` | after `COMMIT` or `ROLLBACK` |
| The authority's start (`delivery_store.py:1749-1779`; `chat-bridge@e2cd9cef:2135-2166`) | the start transaction, the `outbox.d/` directories and the socket bind | before the start transaction | after the bind |

Rules for `L_adm`:

- **Never blocking.** Every acquisition, shared or exclusive, is
  `LOCK_NB`, retried until an absolute deadline. A shared acquisition that
  misses `ADMIT_WAIT` (2 s) is treated as held maintenance (refuse, section 5).
  It can miss only while an owner holds the lock exclusive, which lasts one
  durable record write.
- **One open file description per holder.** A flock belongs to the open file
  description, so each section opens the lock file itself. A process holding
  two sections at once (the bridge's flush step and the authority
  transaction it causes) holds two shared locks, which are compatible.
- **The lock file is content-free.** The owner creates it before writing the
  record, so it always exists while maintenance is held. A writer that finds
  it missing creates it, empty: that is the only path of the format the chat
  package ever creates, and it never writes the record.
- **Read-only work takes no section.** `query`, `status`, `pchat status`,
  `pchat read` and the controller's post-check write nothing, so maintenance
  never refuses them (section 6.4).

### 4.3 The record: `maintenance/1`

`$CHAT_STATE/maintenance.json`, a private regular file of this user, read
whole within 64 KiB, opened without following links, canonical JSON:

```json
{"v": "maintenance/1",
 "operation": "activate-0a1b2c3d4e5f6071",
 "kind": "plateia-activation",
 "plan_sha256": "<the sealed plan's digest>",
 "state": "partial",
 "generation": 2,
 "acquired_boot": "boot-A",
 "boot": "boot-A",
 "bound_boot": null,
 "legacy": "unverified",
 "binding": {"release_id": "<release identity>", "bootstrap_digest": null, "permits": []}}
```

- **`state`** is `draining`, `partial` or `full` (section 4.4). No record
  means no maintenance.
- **`generation`** grows by one at every change, and the operation journals
  the generation, the bytes' sha256, and the file's inode and change time
  after each write, as it does for the bootstrap record
  (`docs/release_staging.md:668-680`). A record whose bytes, inode or change
  time differ from the operation's last journaled write is not the
  operation's own: it is unknown to the operation, never adopted.
- **`acquired_boot`, `boot` and `bound_boot`** bind the record to boots
  (read as `kern.bootsessionuuid` or `/proc/sys/kernel/random/boot_id`,
  `delivery_store.py:321-332`). `boot` is the boot in which the operation
  last affirmed ownership; `bound_boot` is the boot in which #21's last target
  was bound (section 8). A boot that cannot be read is unknown.
- **`legacy`** is `unverified` or `retired-and-inhibited` (section 8).
- **`binding`** names what operation-owned writes the record permits
  (section 6). `permits` is empty until `full`.
- **Informational fields** (a creation time, the acting process's pid and
  start time) may be added for display. Nothing decides from a time.

**Writes.** The first write creates a temporary file, syncs it, links it to
`maintenance.json` (never replacing a file), and syncs the directory. Every
later change writes a temporary file, syncs it, renames it over the record and
syncs the directory, as `durable_replace` does (`delivery_store.py:234-240`).
A crash therefore leaves either the old or the new complete record at the
final name. A temporary file left behind is removed on resume only if the
operation journaled it.

**Readers decide only this:** no file means admitted; a valid record means
held, in its state; anything else (unreadable, not a regular file, another
owner, not canonical, an unknown version, state or field type) is
**unknown**, and unknown means held. The record is never rewritten,
removed or repaired by a writer.

### 4.4 Acquisition states (Requirement 8)

| State | What it claims | Permitted while in it | Refused while in it |
|---|---|---|---|
| no record | nothing | everything | nothing |
| `draining` | New sections of new-release writers are refused. Nothing about sections already open, or about legacy writers. | Sections already open continue to their end, including their continuation requests and any fallback publication (section 7.2). Read-only work. The owner's drain. | New sections. A new entry, even from a section already open: nothing of that call has been sent, so refusing it into the sink loses nothing (section 4.5). Every operation-owned write. |
| `partial` | **New-release writers are drained and refused.** Nothing about legacy writers. | Read-only work. The operation-owned writes that do not depend on legacy writers being absent: for #21, the backups, the bootstrap record's publication, journaling D and binding the seven targets (section 6). Refusal sink writes (section 5). | Every section of every new-release writer. The bridge's start commit. |
| `full` | **Every writer is excluded:** new-release writers are drained and refused, and legacy writers are retired and inhibited (section 8). | Everything permitted in `partial`. The operation-owned bridge start bound by `binding` (section 6). | Every ordinary section, of the bridge and of every client. |

**The exact point of full ownership.** The operation writes `full` only
after it has verified, in the current boot, that legacy retirement and
inhibition hold (section 8). That write is the point at which ownership
claims the all-writer guarantee. **Protected cutover work becomes admissible
at that point and not before:** for #21, the bridge's bootstrap commit, the
first `outbox-db/1` write.

**Acquiring** (the owning operation, holding its own journal lock):

1. Read the record. If it is present and names another operation, or is
   unknown, stop: nothing changes. If it names this operation, resume from
   its state (section 4.8).
2. Create `admission.lock` if it is missing. Journal the intent, write the
   record with `state: draining` (link, never replacing), and journal the
   outcome.
3. Try `L_adm` exclusive without blocking, every 50 ms, until `DRAIN_MAX`
   (proposed 300 s, longer than any bounded section: a flush entry's
   `LOGIN_WAIT` plus its parts' `PART_WAIT + DRAIN_WAIT`).
   - **On success**, while holding it: write `state: partial` with this boot,
     sync, journal, then release the exclusive lock.
   - **On the deadline**, hold nothing, leave the record in `draining`, and
     report that the drain is incomplete. The operation may try again later,
     or release. The deadline only bounds the operation's wait; it never
     admits anything.
4. Continue with section 8 to reach `full`.

The exclusive lock is held only for one durable record write. Writers never
wait for a pending exclusive acquisition, because the owner's tries never
queue: a shared acquisition fails only during that one write.

### 4.5 Writers: admit, continue or refuse

At the start of a section, holding `L_adm` shared:

- **No record:** admitted; the section runs to its end.
- **Any record, or unknown:** the section does not start. A client call is
  refused into the refusal sink (section 5). A bridge section is not run, and
  the bridge goes into its hold (section 4.6).

The authority applies the same rule to each mutating transaction, as a
backstop that does not depend on clients behaving:

| Record at the transaction | `create`, `queue` that would create a new entry | Any op that continues an existing open entry of this writer (`attempt`, `outcome`, `retire`, `void`, `handoff`, `done`, and `create` or `queue` replays) | Bridge in-process requests | `query`, `status` |
|---|---|---|---|---|
| none | run | run | run | run |
| `draining` | `maintenance` | run | run (the bridge issues them only from a section already open) | run |
| `partial`, `full`, unknown | `maintenance` | `maintenance` | not run (`CANCELLED`) | run |

- `maintenance` is a new reply result (section 11). It commits nothing.
- In `draining`, a continuation is admitted because its call's section
  opened before the record existed. A new entry is refused even when its
  call's section is open: the authority cannot tell an open section from a
  caller that never checked, and a call that has created nothing has sent
  nothing, so refusing it into the sink is always safe.
- In `partial` and later no section can be open (that is what the drain
  proved), so a continuation can only come from a call that ended
  abnormally, and it is refused like a new call.
- The authority takes `L_adm` shared around each mutating transaction, never
  blocking. If it cannot within `ADMIT_WAIT`, the request is answered
  `maintenance` with nothing committed.

### 4.6 The bridge under maintenance

- **The hold.** Before each section, and in its main loop before it starts
  its authority or connects to the chat server, the bridge reads the record.
  While any record exists, or it is unknown, the bridge:
  - finishes the section it is in (in `draining` only; none can be open
    later), then closes its chat-server connection;
  - does not connect, handle messages, move checkpoints, deliver, import,
    flush, reconcile, export or collect;
  - does not start its authority, except for the operation-owned start that
    `full` permits (section 6);
  - if its authority is already running, keeps it running for read-only
    requests and refuses every mutation (section 4.5);
  - writes no status snapshot (section 6.4);
  - logs the holder and state once per change in `bridge.log`, and checks the
    record again every `HOLD_RECHECK` (proposed 10 s).

  This follows the shape PR #22 already gives an UNKNOWN bootstrap: no
  serving, import or recovery, checked again on a timer
  (`chat-bridge@e2cd9cef:2206-2226`).
- **After release** the bridge reconnects and resumes from its checkpoints:
  each channel catches up from its checkpoint through the chat server's
  history (`chat-bridge:1772-1777`), so the messages of the interval are
  logged, indexed and delivered then. It imports `outbox.d/` and the refusal
  sink, and flushes, as ordinary sections.

### 4.7 Release

Release is explicit, and only the owning operation performs it, with its own
plan.

1. The operation verifies the record is its own (operation, generation and
   journaled bytes). For #21, `release` then runs every post-check check
   itself, read-only, and releases only if all pass; or, after a rollback,
   it follows section 12.
2. It journals the release intent, unlinks `maintenance.json`, syncs the
   directory, and journals the release.
3. **The unlink is the moment of release.** The next section of every writer
   reads "no record" and is admitted.

A crash between steps resumes from what is on disk: a record still present is
unlinked again; a record absent after a journaled intent is a completed
release.

### 4.8 Crash, resume, reconcile

| Event | What happens |
|---|---|
| The owner crashes in `draining` | The record stays `draining`. New sections keep refusing; open sections finish. Resume by the same operation repeats step 3; or it releases. |
| The owner crashes holding `L_adm` exclusive, before `partial` is durable | The kernel releases the flock with the process. The record is still `draining`. Resume repeats the drain. |
| The owner crashes in `partial` or `full` | The record stays. Every writer keeps refusing. Resume continues from the state, re-verifying everything that state claims. |
| Reboot | The record persists. In the new boot, every writer refuses from its first section. A boot change is never a release. Resume by the same operation drains again (trivially: no process of the earlier boot holds `L_adm`), and records the new `boot`. A boot change after binding is also the evidence section 8 uses. |
| A different operation | Its acquire, resume and release are refused, and nothing changes. A new operation needs the first one's release, or an owner decision. |
| Corrupt or unknown record | Every writer refuses. The authority answers read-only requests only. The operation refuses to resume or release, because it cannot show the record is its own, and reports it. Repair is an owner decision; the protocol never rewrites a record it cannot prove. |
| A torn temporary file | Never at the final name. Removed on resume only if journaled. |
| Lost journal or plan | The record still refuses. No other operation can release it. Repair is an owner decision. |

**Reconcile** is read-only. It reports the record (operation, kind, state,
generation, boots, legacy coverage, binding), whether it matches the
operation's journal, whether the acting process of the record's boot is still
alive (by boot, pid and start time, as `HostProbes.gone` decides,
`delivery_store.py:407-428`), the refusal sink's file count, and the next
permitted step. For #21 it is part of `activate_release status`
(`activate_release.py@e2cd9cef:1722-1730`).

## 5. Refusal

**Where refused work survives the caller's exit: the refusal sink,
`$CHAT_STATE/refused.d/`.** A refused call publishes one file there, exactly
an `outbox/2` fallback file (`delivery_store.py:565-569`), by the same
protocol (`publish_fallback`, `delivery_store.py:655-685`) under
`refused.d/tmp/`. Only the directory differs. The sink:

- is written **only** by refusals, and **only** while maintenance is held or
  unknown;
- is never read while maintenance is held, so writing it changes nothing any
  delivery decision depends on; the cutover looks only at whether it exists,
  as a first-write boundary (section 12);
- needs no `L_adm`, because it is the refusal path itself;
- is imported after release, by the bridge, as ordinary sections, through the
  same IF logic as `outbox.d/` (`delivery_store.py:1202-1241`): by entry id
  *X*, idempotently, with H1's identity guard;
- is a first-write boundary for rollback, because its files hold `outbox/2`
  obligations that legacy code cannot read (section 12).

Imported entries are owned by the outbox, so the bridge posts them with
`delayed` set and its "(delayed; written …)" marker (`delivery_store.py:909`,
`chatlib.py:740-741`). The delay is visible in the channel.

### 5.1 By caller

| Caller | When refused | What the caller sees | Where the work is |
|---|---|---|---|
| `pchat post` (short-lived) | At its section's start, or when the authority answers `maintenance` to a new entry | **Exit 5**, and on stderr: held for maintenance by the named operation, kept as a refusal-sink file, posted after the maintenance is released, "don't post it again" | `refused.d/` |
| `pchat ack` | The same | Exit 5, the same message | `refused.d/` |
| An agent's notice (`agentcli.notify`, inside its host) | The same | A stderr line from the host: the notice is held for maintenance and posted after release. Today a `Queued` notice is silent (`agentcli.py:33-34`); a held one must not be. The host continues. | `refused.d/` |
| The bridge's `announce` | Only in `draining`, inside a section already open; never later, because the bridge runs no section | A durable obligation (`Held` is a kind of `Queued`, so `chat-bridge:1076-1077` already treats it as kept) | `refused.d/` |
| Any caller, when `refused.d/` cannot be written | — | As today when the outbox cannot be written: exit 2, "not kept" (`chatlib.py:540-541`) | Nowhere; the caller is told |

`chatlib` raises a new `Held` exception, a subclass of `Queued`, so every
existing caller that keeps queued work already keeps held work; `pchat` and
`agentcli` distinguish it to report it.

### 5.2 The hard cases

- **Short-lived callers that exit.** The file is durable (file, then
  directory) before exit 5, so nothing depends on the caller living.
- **Lost replies.** A missing reply still proves nothing
  (`docs/chat_outbox_state.md:615-616`, 2.4.2). If a `create` reply is lost
  in `draining`, the call's section is still open, so its `queue` by the same
  *X* is a continuation and is admitted (it hands off an *X* that landed, or
  is refused for an *X* that did not). If a request is answered
  `maintenance`, the call publishes its row to the sink with the same *X* and
  writer, and the import's H1 guard hands off an *X* that had landed
  (`delivery_store.py:1233-1241`).
- **Retained partial progress.** A call never straddles `partial`. A call
  with confirmed parts can only be in `draining`, where its continuations are
  admitted, so it finishes as #19 says: done, handed off, or published to
  `outbox.d/` with its attestation (`chatlib.py:518-561`). In `partial` no
  live call with progress can exist, because it would hold its section and
  the drain would not have completed. If a fault ever let one through, its
  continuation is refused, and it publishes its remainder and attestation to
  the sink, which the import applies under H1 exactly as for a fallback file.
  Confirmed parts are never resent (I-3).
- **Retry identity.** The entry id *X* is fixed per call and travels in the
  sink file; the import is keyed by *X*. A caller that runs `pchat` again
  makes a new *X* and a duplicate post, exactly as after exit 3 today, so the
  guidance says not to.
- **Maintenance refusal versus the unavailable authority.** They differ in
  every respect a caller can see:

  | | Unavailable authority (2.4.4) | Maintenance refusal |
  |---|---|---|
  | Trigger | no committed answer | a record, or an unknown one, or a `maintenance` reply |
  | Where kept | `outbox.d/` | `refused.d/` |
  | Exit | 3 | 5 |
  | Delivered | when the bridge answers again | only after an explicit release |
  | Shown | "queued for the bridge" | "held for maintenance by *operation*" |

- **Drafts and queues.** Untouched. Nothing refused is rewritten or removed;
  `outbox.d/`, legacy rows and the database keep everything they hold.

### 5.3 Visibility

- `pchat status` asks the authority. A running authority's `status` reply
  carries the record's operation, state and generation and the sink's count
  (section 11). Without an authority, `pchat status` reads the record and
  counts `refused.d/` itself, read-only, beside the snapshot's age
  (`pchat:154-195`).
- The bridge logs its hold. The operation's `status` reports the record.

### 5.4 Implementation inventory for refusal (named, not edited)

- `packages/chat/SKILL.md`: "When chat is down" (`SKILL.md:263-273`) and
  the "No lost posts" paragraph gain exit 5's meaning: kept, posted after
  maintenance, don't post again.
- `packages/chat/scripts/tests/test_skill_guidance.py`: its #19 guidance
  test (`test_skill_guidance.py:122-136`) gains the held wording.

Both are code (an agent executes the guidance; a test reads it). They change
in the implementation's pull request, not through `docs-push`.

## 6. Operation-owned writes and the bootstrap

### 6.1 The binding

An operation-owned write is admitted only when all of these hold:

- the record is valid and names the writing operation, at the generation the
  operation last journaled;
- the record's state permits that write (section 4.4);
- for the bridge's start, the record's `binding` matches the launch (below).

The controller fences each of its own writes this way, before it, as its
journal fence already checks the root (`docs/release_staging.md:591-601`).

### 6.2 Permitted mutations

| Mutation | `draining` | `partial` | `full` |
|---|---|---|---|
| #21 step 4, backups in the private root | no | yes | yes (resume) |
| #21 step 5, the bootstrap record's publication, and journaling D | no | **yes** | yes (resume) |
| #21 step 6, binding the seven targets | no | yes | yes (resume, entry by entry) |
| Writing `bound_boot`, `legacy`, `binding` into the record | — | yes | — |
| The bridge's start: the start transaction (S2 or S3), the `outbox.d/` directories, binding `outbox.sock` | no | **no** | **yes**, only as bound |
| Ordinary sections of the bridge or any client | open ones finish | no | no |

**Bootstrap publication is allowed in `partial`.** It writes one new file
that no legacy code reads, and no new-release code acts on it before `full`:
a bridge launched with its digest waits in its hold. It must happen before
binding, because the binding carries D (`docs/plateia_design.md:2420-2425`),
and binding must happen before retirement can be verified (section 8). So
publication cannot wait for `full` without reordering #21, and it does not
need to.

### 6.3 The bridge's bootstrap commit under ownership

A bridge started under the new binding **cannot make its bootstrap commit
before full ownership.** That commit, S2, is the first `outbox-db/1` write
and #21's rollback boundary (`docs/plateia_design.md:2355-2359`). The start
(`chat-bridge@e2cd9cef:2135-2166`) gains one condition, checked holding
`L_adm` shared, before the database is opened or created:

- **No record:** as today (S1, S2 or S3 by the bootstrap rules).
- **A record in `full` whose `binding` matches:** the start is admitted as an
  operation-owned section. Matching means: the record's operation equals the
  bootstrap record's operation; its `bootstrap_digest` equals this launch's
  E; its `release_id` equals this bridge's release; and `permits` includes
  `bridge-start`. Then S2 commits (or S3 replays), the directories are made,
  the socket is bound, and the authority serves **read-only** requests. Every
  mutating request is answered `maintenance`. The bridge stays in its hold
  otherwise (no chat connection, import or flush).
- **Any other record, or unknown:** the bridge opens nothing, commits
  nothing, binds nothing, and waits in its hold.

**Crash recovery.** S2 is one transaction: before its commit nothing exists;
after it, a restarted bridge under the same `full` record finds S3, a replay
that changes nothing, and serves again. A crash between the commit and the
bind leaves the socket unbound until the next start. The record is untouched
by the bridge.

### 6.4 The post-check boundary

- **The status request stays answerable.** #21's `postcheck` needs "a live
  answer from its authority to the existing read-only status request over its
  socket" (`docs/release_staging.md:707-721`; `activate_release.py@e2cd9cef:154-164`).
  `status` and `query` write nothing and take no section, so they are answered
  in every state. The implementation must show that a `status` transaction
  writes no database page (`delivery_store.py:1862` runs it inside
  `BEGIN IMMEDIATE`), or run read-only ops in a deferred transaction.
- **The status snapshot is deferred.** Neither `write_status`
  (`chat-bridge:1484-1492`, the `durable_replace` of `paths.status` at
  `:1490`) nor `write_unavailable_status` (`chat-bridge:1520-1530`) writes
  while any record exists or it is unknown. The first pass after release
  writes it. Why deferred, not admitted or refused:
  - **not admitted:** it is a write inside the boundary (it is in the
    `outbox-db` path list) by ordinary bridge activity, and admitting it
    would be the blanket exemption Requirement 9 forbids;
  - **nothing is lost:** it is diagnostic, rebuilt from the database at
    every pass, and nothing decides from it
    (`docs/chat_outbox_state.md:753-780`);
  - **the post-check never uses it:** "An old status snapshot proves nothing"
    (`docs/release_staging.md:718`);
  - **visibility is kept:** `pchat status` shows the snapshot's age and reads
    the record itself (section 5.3), so a stale snapshot is shown as stale.
- **Release follows the post-check.** `postcheck` stays read-only. For #21,
  `release` repeats the post-check's checks itself and releases only if they
  all pass. Until the release the record stays `full`, so nothing ordinary
  has run between the check and the release.

No other bridge or client activity is exempted.

## 7. Lock ordering and partially completed posts

### 7.1 Lock ordering

| Lock | Holder | Mode | Blocking? |
|---|---|---|---|
| The operation's journal lock (private root) | the controller | exclusive, for the whole command | as today |
| `L_adm` | sections: shared; the owner: exclusive | flock | **never**: `LOCK_NB` retried to a deadline |
| `L_auth` | the bridge, for the authority's lifetime | exclusive | never (`delivery_store.py:1757`) |
| `L_flush` | the bridge's flush | exclusive | never (`chat-bridge:1513`) |
| `L_outbox` | the bridge's retirement and dead-letter steps | exclusive | bounded by `LOCK_WAIT` (`chat-bridge:579`, `:1315`) |
| SQLite's lock | the authority's one connection | exclusive mode | only at start, `OPEN_WAIT` |
| `identities.lock` | identity writes | exclusive | blocking (`identities.py:67`); outside the boundary, and never held with `L_adm` |

The order is: journal lock, then `L_adm` exclusive (the owner); and `L_adm`
shared (section), then `L_auth`, `L_flush`, `L_outbox`, `L_adm` shared
(transaction), then the SQLite transaction (writers).

**Why draining cannot deadlock.**

- The owner never waits while holding anything a writer needs: it takes
  `L_adm` exclusive only by non-blocking tries, and while holding it does
  one durable write and releases it.
- No writer waits for `L_adm` either; a miss is a refusal.
- Shared holders are compatible with each other, so a client's section, the
  bridge's flush step and the authority transaction it causes never wait for
  one another through `L_adm`.
- The authority thread still never waits for a client, a socket or a flock
  (`docs/chat_outbox_state.md:618-683`, 2.4.3): its own `L_adm` acquisition
  is non-blocking, and refused on a miss.
- In `draining`, an open section can still need the bridge: a post recreating
  a channel waits for the bridge to join it, within `CHANNEL_WAIT`
  (`chatlib.py:622-630`). That wait is bounded and ends in a handoff, which
  is an admitted continuation. No cycle exists, because the owner waits for
  sections, sections wait only for bounded network steps or the authority,
  and the authority waits for nothing.

The only new effect is on **liveness of acquisition**: a section that never
ends (a paused process) keeps the owner in `draining`. Each attempt is
bounded by `DRAIN_MAX` and reported; nothing is refused that would otherwise
have been admitted, and nothing waits on the owner.

### 7.2 A client paused after its committed attempt reply

A post sends its bytes after its `attempt` reply and before its `outcome`
request (`chatlib.py:592-604`). Suppose `alp-solver-2`'s two-part post has
part 1's attempt committed and its bytes sent, and the process is stopped
before `outcome`:

1. Its call section holds `L_adm` shared since before `create`.
2. The operation writes `draining`. Other new posts are refused into the
   sink from here on.
3. The operation's exclusive tries fail while the paused client holds its
   shared lock. After `DRAIN_MAX` the operation reports "drain incomplete",
   holding nothing, and the record stays `draining`.
4. Nobody ends the paused attempt, steals its lock, or refuses it: I-2's
   fencing is unchanged. The authority keeps serving every other client
   (I-11).
5. The client resumes. Its `outcome` is a continuation and is admitted; its
   part 2 `attempt`, its bytes and `done` are admitted too. Its section ends.
6. The operation's next attempt drains and writes `partial`.

**If the paused client is killed instead,** the kernel releases its flock,
the drain completes, and the post is exactly where a crash leaves it today:
a `writing` attempt that the bridge ends after release (A8), a part whose
delivery is decided against the record before any resend, and #19's
existing treatment of the remainder. Maintenance adds no loss and no resend.

### 7.3 Amendments to I-11 and I-12

These are proposed amendments to `docs/chat_outbox_state.md`, effective with
the implementation.

**I-11 (a paused client cannot stall the authority).** Unchanged in its
claim, with three additions:

- I-11a. A client now holds a flock, `L_adm` shared, for its call. The
  authority never needs it exclusive and never waits for it, so the claim
  still holds.
- I-11b. A paused client inside its call can delay **maintenance
  acquisition**, not the authority. Each acquisition attempt is bounded by
  `DRAIN_MAX` and reported; the paused client's own continuations are never
  refused because of the pending acquisition.
- I-11c. While maintenance is held, the authority answers a mutating request
  `maintenance` without a transaction, as promptly as `busy` today.

**I-12 (a queued obligation never waits for another writer).** It holds
whenever no maintenance record exists, and now covers refusal-sink files as
well as fallback files and legacy rows. While a record exists, or is unknown,
**no obligation is imported or attempted**: every durable obligation waits for
the explicit release, which is an operation's act, not another writer's. The
wait is shown by `pchat status` (section 5.3). Nothing is lost meanwhile:
every obligation stays durable where it is.

## 8. Legacy retirement and inhibition

Legacy writers cannot read the record, so the protocol cannot refuse them.
Ownership reaches `full` only when they are **retired** (every running or
cached legacy process is shown gone) and **inhibited** (none can start for
the rest of the interval).

### 8.1 Retirement: a boot boundary

**Proposed mechanism.** The evidence is a change of boot after binding:

1. #21's `activate` binds the seven targets in boot `boot-A` and the
   operation records `bound_boot: boot-A`.
2. **The owner reboots the machine** (gate G-1). Every process of `boot-A`,
   including every cached legacy writer and every descriptor it held, is gone.
3. In `boot-B`, the operation resumes. It reads the boot: `boot-B` is known,
   `bound_boot` is known, and they differ. That ties retirement to process
   identity by boot, the way `HostProbes.gone` proves a writer gone by a boot
   change (`delivery_store.py:407-415`). It is not a timestamp.

An unreadable boot is unknown, and `full` is refused.

**Who performs it.** `activate` starts, stops and restarts nothing
(`docs/release_staging.md:700-704`), and stays that way. **Retirement is
performed by the owner**, as operator, as two explicit steps of the published
operator plan: stopping the legacy bridge service before `activate` binds
(already required: a running bridge of another release blocks binding,
`activate_release.py@e2cd9cef:167-179`, `:1469-1476`), and the reboot after
`activate` reports the targets bound. The controller only verifies.

**Why not something lighter.** A process-table check cannot see a module a
long-lived Python process imported earlier: nothing in the process table, its
arguments or its open files shows a cached `chatlib`. Acknowledged
checkpoints, rechecks and attestation are exactly the quiet windows the owner
rejected on 2026-10-11. Logging out and in does not change the boot. Without
a reboot, or another mechanism the owner approves that proves every
pre-binding process gone, **the cutover stays blocked.**

### 8.2 Inhibition

From the binding until release, no legacy writer may start.

- **Managed launchers.** #21 rebinds all seven targets before retirement is
  verified, so after the reboot every managed launcher starts new-release
  code, which honours the record. To reach `full`, the operation re-verifies
  every target through its pinned directory, as the post-check does
  (`docs/release_staging.md:707-713`), in the current boot. They are verified
  again by the post-check and by `release`. A target found changed fails that
  check, so nothing is released; the operation's next resume writes the
  record back to `partial` (`legacy: unverified`), and a new retirement is
  required: a legacy writer may have started.
- **Path-bound importers,** listed or unlisted, reach the bound import
  location, so after the reboot they load the release.
- **Unmanaged launchers. No mechanism in this design prevents them.** Agent
  hooks, other projects' sessions, schedulers, other LaunchAgents, login
  items, unmanaged copies of the chat scripts, source checkouts of this
  repository run against the real state, and anything that rewrites a
  managed target during the window (a skills-repository sync, say) are not
  visible to a source-based inventory, and the launcher search deliberately
  never opens private settings (`docs/release_staging.md:106-117`). **The
  cutover stays blocked until gate G-2 is closed:** an owner-authorized
  inventory of every launch point that can run chat tooling against this
  state, and for each one found, one of:
  - it is made a managed target (a D-26 change), so binding covers it;
  - the owner disables it for the interval, by a means the operator plan
    names and the controller can verify;
  - it is shown, from its own source, never to write inside the boundary.

  Until G-2's evidence exists, the operation refuses `full` and says which
  coverage is missing. `partial` remains available, and claims nothing about
  legacy writers.

### 8.3 What `full` requires

All of these, in the current boot, recorded in the record and the journal:

- the record is this operation's, in `partial`, and the drain is repeated in
  this boot (trivial after a reboot);
- `boot` and `bound_boot` are both known and differ;
- every managed target is verified bound;
- G-2's inhibition evidence is present and complete;
- the bootstrap record is reconciled as this operation's, with its D.

Then the operation writes `full`, `legacy: retired-and-inhibited`, and
`binding: {release_id, bootstrap_digest: D, permits: ["bridge-start"]}`.

## 9. Ordering with #21, and its quiet-writer clauses

### 9.1 The sequence

#21's documented order is kept: **seal, the writer step, publish the bootstrap,
journal D, then bind** (`docs/plateia_design.md:2420-2425`;
`docs/release_staging.md:607-629`, `:668-688`). The issue's Integration list
is not normative on order. Nothing is reordered, so no ordering change to
#21's contract is needed.

| # | Step | Who | Record after |
|---|---|---|---|
| 0 | `plan` (read-only); additionally refuses a state that holds a maintenance record or a refusal sink | operator | none |
| — | The legacy bridge service is stopped (existing precondition) | owner | none |
| 1 | `activate`: the journal is fenced (unchanged) | controller | none |
| 2 | The staged release is verified and sealed (unchanged) | controller | none |
| 3 | **Acquire:** `draining`, drain, `partial` (replaces "the old writers are quiet"; section 9.2) | controller | `partial` |
| 4 | Every original is backed up (unchanged) | controller | `partial` |
| 5 | The bootstrap record is published and D journaled (operation-owned, admitted in `partial`) | controller | `partial` |
| 6 | The seven targets are bound (operation-owned, admitted in `partial`); `bound_boot` recorded; `selected` journaled | controller | `partial` |
| 7 | **Reboot** (retirement) | owner | `partial` |
| — | launchd starts the bound bridge; it waits in its hold | — | `partial` |
| 8 | `activate` resumes: retirement and inhibition verified; `full` | controller | `full` |
| 9 | The held bridge sees `full` and its binding; S2 commits; it serves read-only | bridge | `full` |
| 10 | `postcheck` (read-only; live status over the socket) | operator | `full` |
| 11 | **`release`** (new command) | operator | none |
| — | Ordinary operation: catch-up, import of `outbox.d/`, legacy rows and the sink, delivery | everyone | none |

Between steps 3 and 9 no `outbox-db/1` or `delivery-records/2` write is
possible, and the only `outbox/2` files written are refusals in the sink
(section 12): new-release writers are refused, and the only new-release code
that could write those formats otherwise (the bridge) waits for `full`.
Legacy code cannot write those formats at all.

### 9.2 The quiet-writer clauses (Requirement 7)

| Clause | Disposition | The change |
|---|---|---|
| `activate` step 3, "The old writers are quiet" (`docs/release_staging.md:653-663`) | **Replaced** | "Every writer … at an acknowledged checkpoint or verifiably retired": replaced by acquisition to `partial` (new-release writers) and by retirement and inhibition before `full` (legacy writers, section 8). "The evidence must be current: `observed_at` within 15 minutes": removed as a guarantee (the owner's 2026-10-11 decision). "A delivered request is not an acknowledgement, and unknown evidence blocks": replaced by "an unknown record or boot refuses". "Every designated account's producers must be tracked": **kept as an extra precondition**; it protects the designation list's completeness, not quietness, and may keep its current evidence. "Nothing is interrupted, restarted or killed, and the list is never changed": **kept** for `activate`; retirement is the owner's step (section 8.1). In code, `check_quiet` (`activate_release.py@e2cd9cef:182-223`) leaves `activate` (`:1467`) and the binding guard (`:1475`), which instead checks the record (section 6.1). |
| Its A3 test row (`docs/release_staging.md:814-816`) | **Replaced** | By tests 1, 10, 11, 12 and 13 of section 15. Its "untracked producer blocks" case is **kept**, as the producers precondition's test. |
| "`activate` starts, stops and restarts nothing. The operator restarts the bridge at an acknowledged checkpoint" (`docs/release_staging.md:700-704`) | **Amended** | The first sentence is **kept**. "The operator restarts the bridge at an acknowledged checkpoint" becomes "the owner stops the legacy bridge before binding and reboots after it; launchd then starts the bound bridge, which waits for `full`". No acknowledged checkpoint is involved. The rest is **kept**: a running bridge of this operation's release (now: in its hold or serving under `full`) allows only a read-only reconcile, and any other running bridge, or an unknown state, blocks every change. |
| D-74's stop condition, "a protected owner workflow that cannot be shown to be quiet at the window" (`docs/plateia_design.md:2376-2378`) | **Amended** | Quietness is no longer the safety basis: every protected workflow's writes are refused and kept, never lost. The condition becomes "a protected owner workflow that cannot tolerate its chat writes being held for the maintenance window, or a reboot". It still stops for an owner decision. |

The rollback command's precondition that the bridge "must stop at an
acknowledged checkpoint first" (`activate_release.py@e2cd9cef:1656-1658`) is
not a quiet-writer guarantee; it is kept, reading "must be stopped". Its
eligibility check, re-run before every restoring write, also calls
`check_quiet` (`activate_release.py@e2cd9cef:1668`): that call is
**replaced** by "the record is this operation's and still held", checked
the same way and at the same points, beside the unchanged first-write
evidence (section 12).

### 9.3 Effect on #21's operator contract and acceptance

- **Commands.** `activate` gains acquisition and, on resume after the reboot,
  the `full` transition. A new `release` command ends maintenance.
  `postcheck` adds the record's checks. `status` reports the record. `plan`
  refuses an existing record or sink.
- **The operator plan** (A9) adds: stopping the legacy bridge, the reboot,
  G-2's inhibition evidence, the release step, the sink's count to watch, and
  the chat server's history retention against the window (gate G-6).
- **Acceptance.** A3 is replaced (section 9.2). A6 (crashes) gains the crash
  cases of section 15. A7 (rollback) gains the sink as a boundary, and a
  release only after a boot change since the restore. A8 (end to end) runs
  under ownership: refusals into the sink, the held bridge, S2 under `full`,
  release, then delivery.
- **Unchanged:** the one admitted transition's shape (one addition, section
  10), D-26's seven targets, D-27's handoff, the bootstrap rules, publish
  before bind, and "rollback only before the first new-format write".

## 10. Formats, compatibility and affected paths

### 10.1 Formats

| Format | Change | Compatibility | Activation-transition effect |
|---|---|---|---|
| **`maintenance/1`** (new; `$CHAT_STATE/maintenance.json`, `admission.lock`) | New state format, embedded version `v`. The chat package declares **`read: ["maintenance/1"]` and `write: "maintenance/1"`** (#24's review): it ships `delivery_store.Owner`, the owning operation's primitives, which are the only code that writes the record (it links `draining`, replaces for `partial` and `full`, and unlinks to release, for the fenced operation the record names, under that operation's own journal). **Ordinary writers only read it**; they may only create the empty `admission.lock`. `docs/release_contract.md` defines a format as everything shipped code reads or writes, so the shipped primitives are why `write` is not `null`. The controller (#21) calls the primitives and keeps its own decisions. | Legacy tools neither read nor take it, which is why section 8 exists. | Default staging refuses it as a declared format the live tools don't use (`stage_release.py:879-881`). **It is admitted beside #19's delta**, as one more `added` entry in #21's one admitted transition (`activation.json@e2cd9cef:12-18`), as `designation-bootstrap/1` was (`docs/release_staging.md:543-569`). That widens the transition: a #21 contract change. It is **not** a first-write boundary: it holds no work, and a rollback releases it. |
| `outbox` (`outbox/2`, unchanged version) | Its path list gains `refused.d/` and `refused.d/tmp/`. File content is unchanged. | Unchanged reads and writes. | First-write evidence for `outbox/2` becomes "`outbox.d/` **or `refused.d/`** exists" (`activation.json@e2cd9cef:20-24`; section 12): a #21 contract change. |
| `outbox-authority/1` | A new reply result, `maintenance`, and a `maintenance` object in the `status` reply. | No `outbox-authority/1` peer has ever been deployed, and client and authority ship together in one pinned release, so the version stays `/1`. If any `/1` peer were deployed before the implementation, the version would have to become `/2`; both are within the same admitted transition, because the format is `added`, not live. | None beyond its existing `added` entry. |
| `outbox-db/1` | Unchanged schema. The record is not in the database. | — | None. |
| `designation-bootstrap/1` | Unchanged. | — | None. |
| Every other format | Unchanged. | — | None. |

### 10.2 Affected paths for the later implementation

Confirmed or refuted against the issue's expected list:

| Path | Change |
|---|---|
| `packages/chat/scripts/delivery_store.py` | `Paths` gains the record, `admission.lock` and `refused.d/`; the record reader; section helpers for `L_adm`; per-transaction admission and the `maintenance` result in `Authority.handle`, `_run_job` and `run_tx`'s synchronous path; read-only ops outside sections; the start's admission gate; `status` gains the record and the sink's count; sink publication reusing `publish_fallback`; the `Importer` imports the sink after release. **#24 adds the owning operation's primitives (`Owner`)**: create the lock, link `draining`, drain to `partial`, write `partial` and `full` holding the lock, release by unlink and a read-only reconcile, each under a caller-supplied journal or fence (see "Placement" below). |
| `packages/chat/scripts/chatlib.py` | Call sections in `post`, `ack` and `deliver_entry`; the `Held` exception; refusal into the sink; the `maintenance` reply. |
| `packages/chat/scripts/chat-bridge` | The hold; sections around message handling, delivery passes and flush steps; the deferred status snapshot; the start's `full` binding check. |
| `packages/chat/scripts/pchat` | Exit 5; `status` shows the record and the sink. (Not in the issue's list; needed.) |
| `packages/chat/scripts/agentcli.py` | A held notice is reported, not silent. |
| `packages/chat/scripts/identities.py` | **Refuted: no change.** Both writes are outside (section 3.2). |
| `packages/chat/scripts/runstore.py` | **Refuted: no change.** Outside. |
| `packages/chat/scripts/rotate-logs` | **Refuted: no change.** Outside. |
| `packages/chat/SKILL.md`, `packages/chat/scripts/tests/test_skill_guidance.py` | Exit 5's guidance (section 5.4). |
| `packages/chat/provenance.json` | The reasons for every changed captured file. |
| `packages/release/release.json` | The `maintenance` format, **read `maintenance/1` and write `maintenance/1`**; the `outbox` path list; the `outbox-authority` note. |
| `packages/release/tests/test_build.py` | `maintenance` joins the names the unchanged-declarations digest excludes (`test_build.py:441-445`), with a marker that asserts **both** read and write of `maintenance/1` (the primitives ship in the package). |
| `packages/release/activate_release.py` | **Calls the primitives in `delivery_store.py`** for acquisition, `full`, `release` and the reconcile in `status`, under its own fenced journal; `check_quiet` replaced (section 9.2); the record fence in the binding guard; boot verification; `plan` refusing a record or sink; rollback's sink boundary and release. It keeps its own decisions: verifying retirement, inhibition and targets before `full`, the operation's journal, the `release` command and its post-check. |
| `packages/release/activation.json` | `added.maintenance`; the `outbox/2` first-write text. |
| `packages/release/stage_release.py` | Only if its admission does not already accept every `added` entry generically, and so that the plan's first-write evidence includes the sink. To be confirmed in the implementation. |
| `packages/release/staging.json` | No change: `live_versions` stay the D-19 baseline. |
| Tests under `packages/chat/scripts/tests/` and `packages/release/tests/` | Section 15. |

**Placement (#24's review; implemented 2026-10-12).** This section first
placed acquisition, `full`, release and reconcile in `activate_release.py`.
That file exists only on PR #22's unmerged head, so the drain and its crash cases
(tests 3, 7 and 8) could not be implemented or tested on `master`. #24 moves
the **protocol primitives** into `delivery_store.py`. #24's canonical review
ruled that the placement is consistent with the design provided the design and
the format declaration say so accurately (this section, 3.2 and 10.1 are that
amendment), and that it needs no additional owner decision. It stands on three
conditions, which hold:

- **No new authority.** `Owner` writes the record only for the operation the
  record names, only under that operation's caller-supplied journal or fence
  (verification of ownership, a durable intent before each mutation and a
  durable outcome after it), and only on evidence that matches the operation,
  plan, generation, exact bytes, inode and change time of its own last
  journaled write. A failed or unknown fence permits nothing further, and a
  record that is not provably the operation's own is never adopted, repaired or
  released. Ordinary writers still only read the record (section 3.2).
- **The format is declared as it is.** Because the shipped code writes the
  record, `maintenance` is declared `read` and `write` `maintenance/1`, replacing
  this design's earlier `write: null`, and `test_build.py` asserts both.
- **G-3 is not waived.** Default staging still refuses the declared
  `maintenance` format, and admitting it in the one admitted transition, the
  controller's changes, and the cutover remain #21's rework and the owner's
  decision. The controller keeps what the primitives do not decide: verifying
  retirement, inhibition and targets before `full`, the operation's journal, the
  `release` command and its post-check.

## 11. Changes to #19's contract

Each is a proposed change to #19's approved contract
(`docs/chat_outbox_state.md`), effective only with the implementation:

1. **Client post semantics under refusal.** A post, an acknowledgement or a
   notice refused for maintenance is kept in `refused.d/` and reported with
   exit 5 (`Held`), distinct from exit 3 (section 5).
2. **The client holds `L_adm` shared for each call** (section 4.2).
3. **The authority's transaction wrapping.** Each mutating transaction runs
   inside its own `L_adm` shared section and checks the record first; read-only
   ops run outside sections (section 4.5).
4. **A new reply result,** `maintenance`, which commits nothing; the `status`
   reply carries the record (section 10.1).
5. **The authority's start** is gated by the record (section 6.3).
6. **The bridge's hold** pauses every bridge write except its log, including
   imports, flushes and the status snapshot (sections 4.6, 6.4).
7. **I-11 and I-12 are amended** (section 7.3).
8. **A new import source,** `refused.d/`, imported after release like
   `outbox.d/` (section 5).

Unchanged: I-1 to I-10 and every transition's guard, the formats other than
those in section 10.1, delivery at-least-once with the absence check, and
the unavailable-authority behaviour of section 2.4.4 when no maintenance is
held.

## 12. Rollback

**An explicit, unresolved owner gate (G-9).** Whether #21's rollback is
report-only or automatic is the owner's decision (2026-10-12). It blocks
#21's live cutover. It does not block this design, and this design does not
infer or waive it.

Whatever the choice:

- **Never back to legacy code after a new-format write**
  (`docs/plateia_design.md:2355-2365`). This design adds one first-write
  boundary: a file in `refused.d/` is an `outbox/2` obligation that legacy
  code cannot read, so once the sink exists, rollback is refused like after
  any other first write. In practice, any refused post after the binding ends
  the rollback window; the operator plan keeps the interval between binding
  and release short and quiet.
- **Before any first write,** a rollback restores the targets (unchanged)
  and leaves the bootstrap record (unchanged). It requires the record to be
  this operation's, and never touches another operation's record. It does
  **not** release at once: a process started after the binding may hold
  new-release code, and once admitted it would write new formats into a
  system that has returned to legacy code. So the record stays until the
  boot has changed since the restore, which retires every such process, and
  the operation then releases it. A sink file written meanwhile is itself a
  first write: it is kept, reported, and decided by the owner under G-9,
  never deleted.
- **After a first write,** recovery is a reviewed repair or a roll-forward,
  under ownership, which this protocol's crash rules (section 4.8) keep
  refusing writers until an explicit release.

## 13. Threat boundary

**Inside the guarantee:** ordinary concurrent activity of every kind: new,
waking and cached writers, new-release and legacy code, the bridge, clients,
agents and their hooks, editors and other tools, crashes of any process
including the owner, power loss, reboots, and paused processes. Where the
source cannot show that an ordinary launcher is covered, the guarantee is not
claimed and the cutover is blocked (section 14), rather than the launcher
being excluded.

**Outside it, and only this: deliberate same-user tampering.** Any process of
this user can already write the state directory
(`docs/chat_outbox_state.md:573-575`). Deleting or rewriting the record,
editing the operation's journal, forging a boot id, re-pointing a managed
target by hand during the window, writing the state with tools built to
bypass the protocol, or breaking another process's flock are outside it.
Nothing else is excluded.

**A stable state root (#21 owner amendment 7, 2026-10-14).** The chat state
directory keeps its place and identity for the whole activated bridge start
and every maintenance operation. Cooperating plateia tools never move,
replace or restore it then; this concerns the directory on disk, not any
working directory, and no code changes directory to satisfy it.

- **Pinned:** the validated root is pinned by descriptor, identity and
  realpath. Effects the standard library can make directory-relative go
  through that descriptor: the authority lock, `outbox.d`, and removal of a
  stale socket, only when it is a socket of matching identity.
- **Fails closed:** any detected change of the root is UNKNOWN for the
  bridge start, with nothing committed, and refused by the activation.
- **Bounded:** SQLite (its database and its `-wal`, `-shm` and `-journal`
  sidecars) and a socket bind act by pathname text. So a deliberate
  replacement of the directory **during** one such call may land that call's
  file effect in the replacement before the next check detects it. That is
  the deliberate same-user tampering excluded above, as D-71 already
  excludes it for path-based staging steps. No lock or check is claimed to
  prevent an arbitrary rename by a process of this user.

## 14. Blocking gates

These block #21's live cutover. None blocks this design's completion.

| Gate | What closes it | Owner decision? |
|---|---|---|
| **G-1. Retirement by reboot** | The owner accepts a reboot after binding as the retirement step of the operator plan (section 8.1), or approves another mechanism that proves every pre-binding process gone. Without it, the cutover stays blocked. This also closes "cached processes holding write descriptors". | **Yes** |
| **G-2. Unmanaged launchers and writers** | An owner-authorized inventory of every launch point outside the seven managed targets that can run chat tooling or write `$CHAT_STATE` (agent hooks, other projects' sessions, schedulers, other LaunchAgents, login items, unmanaged copies, source checkouts, editor and terminal integrations, anything that rewrites a managed target such as a skills-repository sync), and for each one a covering mechanism (section 8.2). The inventory needs access the launcher search deliberately lacks, so the owner must authorize what it may read. | **Yes** |
| **G-3. #21 contract changes** | The owner approves: `maintenance/1` in the admitted transition; `refused.d/` as an `outbox/2` first-write boundary; step 3's replacement and the A3 row; rollback eligibility's record check in place of `check_quiet`; the amended "starts, stops and restarts nothing" clause; `release`; and D-74's amended stop condition (section 9.2). | **Yes** (with the implementation decision) |
| **G-4. #19 contract changes** | The owner approves the eight changes of section 11, including exit 5 and the I-11 and I-12 amendments. | **Yes** (with the implementation decision) |
| **G-5. The implementation** | Implemented, reviewed, merged by the supported path, carried in the release #21 activates; its tests (section 15) pass. A later owner decision, with its budget. | **Yes** |
| **G-6. The record's gap during the window** | The operator plan states the chat server's history retention and that the window is shorter. Channels created during the window have no checkpoint and so no catch-up, as today when the bridge is down: the plan states how the owner accepts that, or keeps channel creation out of the window. | No (an operator-plan fact), unless the owner wants it decided |
| **G-7. The protected-workflow stop condition** | At the window, the owner decides whether each protected workflow can tolerate its writes being held and a reboot (section 9.2). | **Yes**, at the window |
| **G-8. Source recheck** | Before the cutover, the inventory and citations are rechecked against PR #22's final head and master's chat package, if either moved (section 1). | No |
| **G-9. Rollback mode** | The owner chooses report-only or automatic rollback (section 12). | **Yes** |

**Gate status, 2026-10-14.**
- **Closed:**
  - G-3 is approved (#21's owner amendment 4 and its APPROVE; D-75), and is implemented in PR #22, merged as `c1e2348`;
  - G-4 is closed (#24);
  - G-5 is closed: the chat package's part is merged (#24, pull request #25), and #21's part is merged (PR #22, `c1e2348`, with owner amendment 7).
- **Still open:** G-1, G-2, G-6, G-7, G-8 and G-9. Nothing is installed or activated, and #21's live cutover stays blocked until they close.

## 15. Test designs (invented fixtures)

Every test runs on an invented home and state root, the in-process authority
and #19's invented chat server, with fake boot ids, a fake process table and
fake service reads. No test reads real state. "Requirement" refers to #23's
Requirements.

| # | Case | Fixture | Observable assertions | Requirements |
|---|---|---|---|---|
| 1 | **Draining** | A client of `alpha` with a call section open (stopped by an event after `create`); a second client starting a post after the operation writes `draining`. | The second client's `create` gets `maintenance`, its row lands in `refused.d/` with its *X*, and it exits 5; the first client's `attempt`, `outcome` and `done` all get `ok`; the operation's exclusive tries fail until the first section ends; the record reaches `partial` only after it; the journal shows the drain's attempts. | 2, 8, 10 |
| 2 | **Refusal during ownership** | Record `partial`; `pchat post`, `pchat ack` and an agent's notice from `alp-solver-2`. | Each leaves one sink file and no other change: `outbox.db`, its WAL, `outbox.d/`, `outbox.jsonl`, `pending.json` and every channel log are byte-identical before and after; exits are 5, 5 and a stderr notice line; each message names the operation; `pchat status` shows the record and three held files. After release, each is imported once and posted once with the delayed marker. | 3 |
| 3 | **Crash of the owner** | Crash injected after each record write and journal append of acquisition, `full` and release. | The record stays at the last durable state; every writer keeps refusing (sink files grow, nothing else changes); reconcile reports the operation, state, generation and the acting process gone; resume continues to the same end state. | 4 |
| 4 | **Resume and release** | Record `full` for the operation; resume, post-check, release. | Resume changes nothing but what the state requires; release is refused while any post-check check fails; after release the record is gone, the journal holds intent and outcome, the next post is admitted and posted directly (exit 0), and the bridge imports the sink. A crash between the unlink and the journal resumes to a completed release. Rollback variant (record `partial`, before any first write): the targets are restored, the record stays until the fake boot changes, then is released; a sink file written meanwhile makes the rollback report a first write and keeps the file. | 4 |
| 5 | **A corrupt maintenance record** | Truncated JSON; an unknown `v`; a wrong field type; a symlink; another owner's file; mode 000. | Every writer refuses into the sink; the authority answers `status` and refuses mutations; the bridge holds; the operation refuses resume and release, naming the record unknown; the record's bytes are unchanged. | 4 |
| 6 | **A wrong operation** | A record of `activate-0a1b2c3d4e5f6071`; a second plan with another operation. | The second operation's acquire, resume and release are refused; the record's bytes and generation are unchanged; the first operation can still resume. | 2 |
| 7 | **Reboot recovery** | Record `partial` in `boot-A`; the fake boot becomes `boot-B`. | Writers in `boot-B` refuse from their first section; nothing treats the boot change as a release; resume drains and records `boot: boot-B`; with the boot unreadable, `full` is refused as unknown. | 4, 8 |
| 8 | **A crash during acquisition** | Crashes after the temporary record, after the link, after the exclusive lock is taken, and after `partial` is written but before the journal. | No final name ever holds a torn record; a journaled temporary is removed on resume, an unjournaled one is kept and reported; `partial` never appears without a completed drain; resume reaches `partial`. | 2, 4, 8 |
| 9 | **The bootstrap under ownership** | Record `partial`; the bootstrap published and D journaled; a bridge launched with `--activation-bootstrap sha256:<D>` and a valid record. Then `full`. Variants: a wrong D, another operation, another release. | In `partial`: no `outbox.db`, WAL or socket exists, the bridge logs its hold. In `full`: S2 commits once, the socket binds, `status` answers with the record and the epoch, every mutating request gets `maintenance`, no chat connection or import happens, and `postcheck` passes. A restarted bridge replays S3. Each variant holds without writing. | 8, 9 |
| 10 | **A client paused after its committed attempt reply** | A two-part post stopped after part 1's bytes, before `outcome`. Variant: the client is killed instead. | The drain does not complete while it is paused; other clients' requests are answered meanwhile (I-11); on resume its `outcome`, part 2 and `done` succeed; each part is confirmed once. Killed: the drain completes; after release the attempt is ended by A8 and decided against the record; nothing is resent before that. | 10 |
| 11 | **A legacy process present** | Record `partial`; the fake boot unchanged since binding; a fake legacy writer (a baseline-format client appending to `outbox.jsonl`). | `full` is refused, naming retirement unverified; the bridge's start commit stays refused; the legacy append is not refused (the record says `legacy: unverified`, and nothing claims otherwise); after a boot change and complete inhibition evidence, `full` is reached. | 8 |
| 12 | **An unmanaged launcher** | An invented LaunchAgent outside the managed targets naming a copy of the baseline `pchat` under an invented `~/tools/chat-copy/`; separately, inhibition evidence marked incomplete. | `full` is refused, naming the launcher or the missing coverage; nothing is bound or written by that refusal; `partial` and every refusal continue. | 8 |
| 13 | **A cached writer waking during ownership** | A long-lived new-release host that imported the modules before the record was written, then wakes and sends a notice; a long-lived legacy host in `partial`. | The new-release host reads the record at its section's start (no cached admission) and its notice goes to the sink with a visible line; the legacy host is not refused in `partial`, and its presence keeps `full` unreachable until the boot changes. | 3, 8 |

**Ordering and deadlock schedule (Requirement 10).** One more test drives an
open client section, a bridge flush step causing in-process transactions, and
the owner's acquisition concurrently, with every wait bounded by a fake clock.
It asserts that every thread finishes or reports within its deadline, that
no thread ever blocks on `L_adm`, and that the authority answers clients
throughout.

## 16. Acceptance map

| Item | Where |
|---|---|
| **Acc1.** Every write path and launcher classified, with reasons; unknowns closed or gated; `runstore`, `rotate-logs`, `identities` and the bridge's channel-log and status paths | Sections 3.1-3.6; gates in section 14 |
| **Acc2.** Serialized admission, drain, durable operation-bound ownership, fail-closed visible refusal preserving posts, crash, resume, reconcile and release | Sections 4.1-4.8, 5 |
| **Acc3.** Legacy retirement and inhibition, including unmanaged launchers, or the cutover blocked | Section 8; gates G-1, G-2 |
| **Acc4.** Exact paths, formats with compatibility and transition effects, #19 contract changes, effect on #21's operator contract | Sections 10, 11, 9.3 |
| **Acc5.** Ordering of operation, acquisition, write, bootstrap, refusal and recovery; lock ordering and partial posts; refusal semantics | Sections 4.4, 6, 9.1, 7, 5 |
| **Acc6.** The 13 invented-fixture test designs with assertions tied to Requirements 2-4 and 8-10 | Section 15 |
| **Acc7.** Rollback as an explicit owner gate; no return to legacy after a new-format write | Section 12; gate G-9 |
| **Acc8.** One `docs-push` landing, verified on the remote, then closure | Section 17 (performed by the manager) |
| **Correction 1.** Keep publish, journal D, bind; bootstrap publication is operation-owned and allowed in the intermediate state; no bootstrap commit before full ownership | Sections 9.1, 6.2, 6.3 |
| **Correction 2.** The authority lock is held for the authority's lifetime and excludes other authorities only | Section 3.3 |
| **Addition 1.** Each quiet-writer clause replaced, kept or amended, enumerated; who performs retirement | Sections 9.2, 8.1 |
| **Addition 2.** The post-check's status request stays answerable; the status snapshot's write decided | Section 6.4 |
| **Addition 3.** The source revisions, and the recheck rule for a later PR #22 head | Section 1; gate G-8 |
| **Addition 4.** The owner's dated decisions recorded in the document that owns the topic | Section 17; D-75 in [plateia_design.md](plateia_design.md) |
| Requirement 6, threat boundary | Section 13 |

## 17. Owner decisions and delivery

The owner's decisions this issue carries are recorded, with their dates, in
D-75 of [plateia_design.md](plateia_design.md), the document that owns #21's
activation:

- **2026-10-11:** writer admission is its own design issue (#23), so that
  #21's rollout stays executable with its original safety. Quiet windows
  based on timestamps, rechecks or attestation are rejected as the
  guarantee.
- **2026-10-12:** #23 is one documentation-only delivery unit: one
  `docs-push` landing, then closure once acceptance is satisfied and the
  landing is verified on the remote. Rollback (report-only or automatic)
  stays an explicit owner gate that blocks #21's cutover but not this design.
  Returning to legacy code after a new-format write stays forbidden.

**Amendments in this landing,** each minimal and pointing here:

- [chat_outbox_state.md](chat_outbox_state.md): section 2.4.6 (the proposed
  #19 contract changes and refusal semantics) and notes on I-11 and I-12.
- [release_staging.md](release_staging.md): "Writer admission (#23)", the
  disposition of each quiet-writer clause, the ordering, the post-check's
  status request and the maintenance format's admission.
- [release_contract.md](release_contract.md): the proposed `maintenance`
  declaration and the `outbox` path change, beside the declaration pin.
- [plateia_design.md](plateia_design.md): D-75.

**Delivery.** The manager lands these five documents with one `docs-push`,
verifies the published commit on the remote, and closes #23 with references
to its approval, the publication and this acceptance map. None of these
documents is read by a test or executed by an agent.
