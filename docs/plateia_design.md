# Plateia design

Plateia is the owner's workplace for directing project work through chat and
seeing its real progress. This design serves the accepted [vision](vision.md),
V-1 through V-13. It does not amend it. The first exploration follows one
invented issue card through Solve, Hold, resume, review, merge, cleanup and Done.

Design state: `exploring`

Status legend: `[ ]` unprocessed · `[#N]` linked to issue N · `[no-issue]`
reviewed and deliberately not tracked separately · `[deferred]` blocked on a
concrete precondition

## Processing status

- [ ] EPIC. Make plateia the owner's trustworthy chat and work board
- [ ] PLT-9. Capture the shared chat identity and transport core
- [ ] PLT-15. Capture the shared chat bridge
- [ ] PLT-11. Build immutable pinned shared-package releases
- [ ] PLT-12. Stage releases without changing active tools or services
- [ ] PLT-14. Add guarded activation and state-preserving rollback
- [ ] PLT-10. Expose one shared chat API with compatible non-web entry points
- [ ] PLT-16. Add normalized, attested chat evidence to the shared API
- [ ] PLT-13. Adapt the approved clarification inbox to shared chat
- [ ] PLT-1. Add durable logical card-move submission to shared chat
- [ ] PLT-17. Add card moves to the terminal and the record
- [ ] PLT-3. Reconcile ordered card intent before manager dispatch
- [ ] PLT-4. Pause and resume workers and review loops at safe boundaries
- [ ] PLT-5. Build shared finalization from the finalize contract
- [ ] PLT-6. Project the card's real lifecycle from shared evidence
- [ ] PLT-7. Persist board intent and reconcile shared receipts in plateia
- [ ] PLT-2. Compare page frameworks with the same recovery prototype
- [ ] PLT-8. Deliver the first browser card lifecycle on phone and desktop

These IDs are stable conversation and processing cursors; existing PLT-2
retains its bake-off meaning despite appearing later in dependency order.
The owner approved shared prerequisites before the plateia card slice (D-5).
The seventeen proposed boundaries below are not all approved, and none is ready
for issue processing; PLT-15 (2026-10-08) holds the bridge half of the former
PLT-9 (D-20), PLT-16 the evidence half of the former PLT-10 (D-28), and PLT-17 the
terminal and record half of the former PLT-1 (D-38). New code is plateia-owned (D-7); the merger starts fresh
from finalize (D-9), rather than porting the existing drainer. Finalizer runtime
ownership is approved (D-12); approval continuity and its metadata-only initial
boundary are selected (D-14/D-18). D-15/D-16/D-17 settle distribution, the
Python API deployment boundary and focused migration scope. Remaining gate,
wire, projection and one-PR boundary details (Q-16/Q-14/Q-6/Q-11) must be
settled before their affected slices are processed.
Remaining mid-term scope has
not been decomposed; this ledger does not claim to cover the whole vision.
No tracker artifacts have been created.

D-15/D-16 select pinned plateia packages in a private Python environment, thin
CLI/skill entry points and one versioned Python chat API in existing processes.
D-17 selects focused shared-chat/inbox migration and necessary hooks first.
Editable deployment, a new initial IPC daemon and private
patched snapshots are rejected. This is a design direction, not installation
or activation authority. PR #1 merged on 2026-10-04 as `ef86f17`, with its
approved `f9aef08` head unchanged; the inbox core is on `master` under
`packages/routine_inbox/`. No new slice supersedes or modifies that core by
inference.

**Status update, 2026-10-08.** Since the decisions below were recorded:
PR #1 (issue #2) merged; CI exists (`docs/ci_design.md`, PR #6/#9), and the
drainer now requires `build-test` and `review-approved` for plateia. No issue
or PR is open. Decision entries keep their original wording as the record of
what was approved; where one describes PR #1 as open, this note supersedes the
status, not the decision. Still no tracker artifacts exist for this design.

## Epic contract

- **Goal:** the owner can direct work from a phone or desktop through chat,
  with a board that distinguishes requested intent from verified progress.
- **Done when:** the vision's mid-term chat, board, notification and setup
  scope works on both devices; V-12 recovery guarantees are demonstrated;
  workflow delivery and execution continue without plateia (V-13).
- **Users and operators:** one owner; existing managers, workers, review
  services and the drainer carry out the work.
- **Arc label:** None proposed.

## Current state and evidence

Read-only investigation; no live chat, manager handoffs, account configuration
or session transcripts were used. All examples below are invented.

| Surface | Verified evidence | What it establishes, and its limit |
| --- | --- | --- |
| Plateia | `AGENTS.md`, `README.md`, `docs/vision.md`, `packages/routine_inbox/`; `master` head `852d1a5`, refreshed 2026-10-08 | Python server, localhost/private network, chat-only actions, owner authority and the accepted vision. The routine-inbox package (PR #1) is the only code; there is no web application yet. |
| Tracker | Read-only inventory of `coghex/plateia`, refreshed 2026-10-08 | Issues #2, #3, #5, #8 closed; PRs #1, #4, #6, #9 merged; throwaway PR #7 closed unmerged. Nothing open; no epic for this design. Repeat the overlap check at readiness. |
| CI and merge gate | `docs/ci_design.md`; `.github/workflows/ci.yml`; `.drain-prs.json`; PR #6, PR #9 | Every PR and `master` push reports `build-test`; `.drain-prs.json` sets `required_ci_check` to `build-test`, so the drainer requires `build-test` and `review-approved`. New slices land under that gate. |
| Chat CLI | `~/.codex/skills/chat/SKILL.md`; `scripts/pchat`, `main`, `unaccepted` | Explicit sender identity; typed/request-tagged posts; exact-message reply/ack; search, trace and health views. A failed post is appended to the outbox and exits 3. A successful post returns a line count, not a durable card-operation receipt. |
| IRC boundary | Chat skill's `scripts/chatlib.py`, `post`, `ack`, `outbox_append`, `outbox_claim` | IRC posting and reply tags; locked outbox appends; rename-and-recover claimed files. No card identity, card revision or caller-supplied idempotency key in this interface. |
| Chat bridge | Chat skill's `scripts/chat-bridge`, `Record`, `HistoryPager`, `Deliveries`, `ring_manager`, `flush_outbox`, `handle`, `run` | History catch-up in pages, message-ID deduplication, durable pending wake-ups before logging, delivery polling, manager retargeting, acceptance reminders and dead letters. The source explicitly permits duplicated wake-ups after a crash. |
| Bridge tests | Chat skill's `scripts/tests/test_bridge.py`, `OutboxTests`, `AcceptanceTests`, `CatchUpTests`, `SplitTests` | Existing coverage for concurrent outbox appends, recovery of claimed files, exact-manager acceptance, cursor ordering and paging. Read as evidence; not run in this design session. |
| Manager | `~/.codex/skills/project-manager/SKILL.md`, Startup, Live state, Chat, Requests, Keep solvers until their PR merges | Reconciliation from tracker and sessions; acceptance before dispatch; request IDs and authorized endpoints; claims, worktrees and PR checks before redispatch; canonical reviews; reuse/resume of solvers. Its handoff is coordination memory, not tracker truth. |
| Drainer startup policy gap | Same manager skill, Multi-issue requests, step 3 | Current instructions say to start a stopped drainer when a request includes merging. D-6 explicitly supersedes that behavior for this design: a Solve/merge endpoint is insufficient; starting requires the owner's explicit request. |
| cmux | `~/.codex/skills/cmux-supervisor/SKILL.md`; installed `cmux agent message --help`, `agent inbox --help`, `sessions --help` | Hook-based messages avoid half-written prompts; inbox states are queued/delivered/read; session records support discovery and recovery. CLI inspected: `0.64.25-nightly`, commit `cade2e2`. This is not evidence of a new card-specific pause protocol. |
| Review and merge | Manager skill; `~/work/kanban/tools/drain_prs.py`, `process_pr`, at repository head `7f3534f` | Existing eligibility, fresh approval/check/head gates and merge reconciliation remain authoritative. The inspected drainer path has no card-Hold check. Stopping a solver would not by itself prevent an already approved PR merging. |
| Cleanup | Same drainer, `plan_cleanup`, `advance_pending_cleanup`, `complete_pending_cleanup`, `record_interrupted_merge` | Cleanup obligations are persisted and retried, and unresolved cleanup can become an incident. Obligations include linked issues, worktree, local/remote branches and default-branch update. The plateia adapter and claim-release proof do not yet exist. |
| Finalize starting contract | Installed kanban skill `finalize/SKILL.md`; tracked source `~/work/kanban/tools/command_sources/finalize.md`; `tools/test_finalize_workflow.py` identified for follow-up | Manual, one-PR fallback: repository/target resolution; fail-closed current opposite-brand approval and successful-check gate; immediate gate rerun; head-bound merge commit; verified merge before identity-safe cleanup. It has no durable scheduler, incident record or post-merge audit. D-9 chooses a fresh implementation from this step, not a port of the old drainer. No finalize or merge was invoked here. |
| Existing approval carry/recovery | `~/work/kanban/tools/drain_prs.py`, `branch_update_carried_approval`, `update_branch`, `recover_stale_approval`; kanban and plateia `.github/workflows/review-gate.yml`; `tools/test_drain_prs.py`, approval-carry tests | Carry requires the exact updated head, successful stale-approval policy and retained approval label; the policy tests overlap with PR-owned files before/after the update, not just merge conflicts. A stripped label is not restored merely because the merge was clean. The drainer waits for an available canonical re-review rather than racing it; its fallback reviewer only handles its separate eligible route. Label removal without a head change is treated as deliberate revocation. Finalize itself refuses stale/missing approval and never runs a reviewer. Source/test evidence read, not executed. |

Important distinction: deduplicating a replayed IRC `msgid` is not deduplicating
a repeated logical request. Reposting the same text can produce a different
`msgid`. The current outbox can resend after a crash between send and file
retirement. Neither it nor the manager's natural-language reconciliation alone
establishes V-12's no-duplicate-work guarantee for card moves. The inspected
JSONL writes also do not establish a transaction spanning board intent and
chat submission. This design must close those gaps rather than inherit a
stronger guarantee than the evidence supports.

Current notification delivery uses ntfy. V-9 requires browser push instead;
the replacement and its delivery ownership need later design. Plateia must
not make shared delivery depend on its own uptime while replacing that sink.

### Concurrent workflow work: preserve and coordinate

Read-only source refresh, 2026-10-02: the installed chat skill now supplies
stable role/session identities, durable identity allocation, local nick-color
decoration and newer bridge delivery/acceptance recovery. These are evolving
shared-system capabilities, not plateia replacements. Preserve the owner's
identity, color and workflow fixes; do not freeze, overwrite or privately fork
the installed scripts to satisfy this design.

PLT-1/PLT-3 overlap shared routing, provenance and acceptance; PLT-4 overlaps
worker coordination; PLT-6 overlaps identity/presence and recovery evidence.
Use authenticated identity metadata rather than nick patterns or colors.
Reviewer completion alone is not approval: the canonical coordinator remains
the publisher of validated review evidence. Recheck source and coordinate
these overlaps with the owner before implementation or migration.

Plateia PR #1 (`f9aef08`, inspected 2026-10-02; merged 2026-10-04 as
`ef86f17`) contains a bounded clarification-inbox core, protocol, fake adapter
and offline tests; it defers real adapters, installation and scheduling. That
core is now on `master` under `packages/routine_inbox/`. Its routing/aggregation contracts overlap
Q-14 and PLT-1/PLT-3: reconcile them instead of creating a competing codec or
duplicate inbox. No private runtime or install choice is implied by that merge.
No implementation, identity registration, install or service change was made
in this design session.

Further source refresh for Q-12, 2026-10-02: `scripts/identities.py` reserves
never-reused worker numbers before network provisioning, retains role/project
and resume bindings, and uses a locked, fsynced atomic registry write. The
existing `scripts/install-identities` is a specialized identity rollout, not
a package installer: its apply path provisions identities, edits private/server
configuration, loads WeeChat colors, can graft a local reviewer wrapper and
restarts the bridge. Do not reuse those apply effects as release installation.
Existing owner-approved local previews are protected active work; D-15 rejects
new private runtime forks, not an instruction to remove those previews.

Recovery source refresh, 2026-10-02: the bridge now checks a cited original
owner/assistant message for manager-to-manager delegation and separately marks
its verified or unverified authority. Preserve the actual sender identity and
that provenance; quoted text alone is not authority. Identity discovery also
refuses an empty process inventory and retains established roles. Capture
these newer fixes, rather than freezing the earlier inspected source. Existing
legacy interrupt dispatch is an integration overlap: plateia Hold and upgrade
coordination still use D-10's safe checkpoints and never become interrupt
commands or a new browser interrupt control.

### Shared chat source inventory for PLT-9, 2026-10-08

Read-only inventory; nothing was modified, run or restarted. Private backups,
registry contents and task notes were located but not read into this document.

**Where the source lives.** One live copy, in the local skills repository at
`~/.codex/skills` (`chat/` with `SKILL.md` and `scripts/`). That repository
has **no remote**. `~/.claude/skills/chat` is a symlink to it. `pchat` on
PATH, the WeeChat role-color autoload scripts and the bridge and log-rotation
LaunchAgents all point straight into that working tree, so there is no
installed copy to drift from it. The `modelclass` launch hook imports
`chat/scripts` to register identities before launching an agent.

**Committed versus live.** The repository's `master` head is `f010533`
(2026-10-04). The live tree carries uncommitted edits to `pchat`, `chatlib.py`,
`identities.py`, `agentcli.py`, `chat-bridge`, `SKILL.md` and three test
files, plus untracked `runstore.py`, `binding.py`, `receipt_experiment.py`,
`tests/_isolation.py` and three new tests. `pchat` imports `runstore`, which
imports `binding`: **capturing only committed files yields a `pchat` that
fails at startup.** The live chat tree equals a reviewed commit (`c572bad`,
26 commits past `f010533`) whose history exists only in Codex task clones and
a detached worktree under `~/Documents/Codex/`, not on any branch of the
skills repository. One live-only edit (a `cmux-supervisor` SKILL.md paragraph)
matches no located commit.

**Identity discovery and the live-session conflict.** Without an override,
`pchat` resolves its sender by syncing a cmux process inventory into the
identity registry and picking the active record for its surface.
`CHAT_AGENT_ID` returns that registry record directly and skips discovery,
which is why it worked around the conflict. `register()` refuses with
"`<name>` already has a live session" when an active record's still-running
process differs from the one being registered; in `sync()` that one refusal
aborts the whole sync, and at launch it exits the hook. The trigger was a
detached Codex app-server daemon on a guide's tab: the old inventory picked it
as the tab's agent and collided with the live guide. **The fix (exclude
processes with no terminal; prefer foreground and the tab's own process; hold
cross-group claims) is uncommitted in the live tree**, with a regression test.
The refusal guard itself is unchanged and correct.

**Runtime drift.** The running bridge predates the latest live-tree install
and was not restarted, so its loaded modules are older than the files on disk.
A reviewer-start receipt experiment is enabled for all projects. When a
solver's background reviewer posts its exact, authenticated start notice for
the solver's own request, the bridge skips waking the manager for that one
post; the post stays in the channel record, and anything that doesn't match
exactly gets normal delivery. A marker file switches it on; its code and
marker appeared together on 2026-10-05.

**Fixes PLT-9 must preserve.**

| Fix | State |
| --- | --- |
| Permanent identities, never-reused counters, empty-inventory refusal | Committed |
| Prefix-only role colors; WeeChat FIFO framing | Committed |
| Catch-up checkpoints, resumable catch-up, crash replay, dead-letter alerts | Committed |
| Acceptance recovery and `pchat unaccepted` | Committed |
| Cited-delegation provenance (`--authority`) | Committed |
| Busy-Codex delivery; role from workspace; hook-record placement | Committed |
| Bounded logs (`rotate-logs`) | Committed |
| Daemon exclusion and held placements (the live-session fix) | Uncommitted; reviewed only in task clones |
| Test isolation guard against touching live state | Uncommitted; reviewed only in task clones |
| Silent child-run post refusal; run store and `CHAT_RUN_ID` launch evidence | Uncommitted; reviewed only in task clones |
| Reviewer-start receipt experiment (enabled) | Uncommitted; in task-clone history only inside an "installed baseline" snapshot commit, with no located review |
| Relayed-approval paragraph in `cmux-supervisor` | Live tree only |

Manager reconcile v2, child-run, launch-worker and lifecycle changes in the
`project-manager` skill are also uncommitted. They use the chat code but are
broader manager migration (D-17), not PLT-9 source.

**Consumers a source move would affect.** Path-bound: the `pchat` symlink,
bridge and log-rotation LaunchAgents, WeeChat autoload symlinks, and four
`project-manager`/`model-classes` scripts that import `chat/scripts` relative
to their own location. PATH-bound: `pchat` callers in `project-manager`,
`recover` and kanban's reviewer runner (`review_pr.py`, including its plugin
caches). No private `chatlib` fork exists in kanban or plateia. The
installed reviewer runner already carries the reviewer identity hook, so the
identity rollout's reviewer graft is no longer needed. `role_colors.py` and
`install-identities` hard-code the owner and assistant names; PLT-9 must
resolve them at runtime instead.

The owning checkout has no existing shared-package release builder or versioned
installer. The current inbox developer runner is not evidence of a deployment
mechanism. Installation/API guarantees below are design contracts, not claims
about today's live system.

## Desired experience: walk one card through its whole life

The fictional project is `alpha`, issue `#41`; its later PR is `#57`.
Handles such as `alpha-manager-1` and `alpha-solver-12`, room names and request
IDs are examples, not existing accounts. The issue is already ready to solve;
the issue-approval path is outside this particular walk.

### 1. The owner moves Inbox → Solve

The card records the requested basket separately from observed work. It shows
a pending Solve request; it does not claim a worker started. The move becomes
a chat request posted as the verified owner, addressed to the manager. Card
order is only presentation, never dispatch priority (V-7, V-8).

An HTTP response or a drag animation is not manager acceptance. Plateia must
define the exact durable receipt and handoff recovery rule (D-3, Q-9). If the
browser loses the response, retrying that **same move** must find its existing
receipt, not create a new instruction. A later intentional move is different.

### 2. The request reaches chat and the manager

The owner can see queued, delivered and accepted separately. Chat posting,
bridge wake-up and cmux delivery are different observations. A request sent
while chat is unavailable remains durably queued and visibly undelivered.
The bridge catches up after reconnecting; it never turns a failed or unknown
delivery into success merely because a connection came back (V-6, V-12).

The manager reads the verified sender and original message identity. It
accepts or refuses that exact request before dispatch, using the existing
reply/ack mechanism. Acceptance proves responsibility for handling it, not
that Solve is already satisfied. An unrelated manager status is insufficient.
The request room becomes the enduring record; its exact opening/routing
convention remains part of Q-9.

### 3. The manager accepts and reconciles

The manager checks current card intent, tracker eligibility, claims, existing
worktree/PR and sessions. If Solve → Hold → Solve accumulated before it acts,
it acts on the latest instruction, preserving the intervening record (V-7).
An obsolete request still needs a terminal disposition such as superseded;
otherwise acceptance reminders can survive work that should never start.

The manager chooses scheduling; plateia supplies no worker-launch command.
If work already exists, it is attached or resumed rather than duplicated.
A refusal or need for owner input remains visible on the card and in chat.

### 4. A worker claims and works

The worker uses the existing canonical claim, isolated worktree and review
pipeline. The card learns which session is working from reconciled facts and
correlated status, not from the owner-requested basket. Its issue remains the
same card when PR #57 appears. A worker's chat word `done` may mean its own
endpoint was reached; it does **not** mean the card is Done (V-7, V-8).

If the owner starts work by hand, the board observes it too. No second worker
is dispatched merely because the board has no plateia-originated start event.
An active session alone cannot prove ownership of #41; correlation with
claims, worktree/PR and the request record is needed.

### 5. The owner moves Solve → Hold

The requested state becomes Hold while observed state remains working,
labelled pause pending. The manager accepts the Hold and conveys it through
the shared delivery path. The worker finishes its current safe step, saves
its recovery context and reports a safe pause. No terminal kill, forced
prompt edit or midway interruption is involved (V-7, V-13).

The branch, worktree, uncommitted work, PR, claim and approval evidence are
kept. Holding does not mean withdrawing an approval. D-4 establishes shared
whole-pipeline inhibition; D-10 settles the action/checkpoint boundary.
The integration must expose these boundaries and inhibit the whole pipeline:
solver, reviewer, queued follow-up, manager redispatch and drainer. A solver's
pause report alone cannot prove that the card is held.

### 6. Connections drop while Hold is pending

Use three independent failures, including combinations:

- **Plateia restarts after acknowledging Hold:** the acknowledged move and
  receipt survive. Shared delivery and running work continue without it.
  On startup plateia reconciles; it does not issue a new Hold with a new ID.
- **Chat bridge restarts after sending a wake-up but before recording its
  result:** replay or duplicate delivery may happen. The logical Hold still
  has one identity and one effect. The latest revision wins; a delayed older
  Solve must not restart held work. A pause report posted while the bridge
  is down can be replayed later, with its original evidence and provenance.
- **The browser disconnects before receiving the Hold response:** it shows
  connection/state uncertainty and reconnects automatically. On reconnect
  it queries the operation's receipt and a fresh snapshot before retrying.
  Offline cache content is labelled stale. An unsent gesture is not shown
  as acknowledged (whether to allow offline gestures is open in Q-6).

Until reconciled, the card might say “Hold requested; last observed working;
connection lost.” It must not say “paused” just because the pause request was
accepted. If Hold can no longer be delivered, retain the failure and notify
under V-9. A browser reconnect or routine successful pause does not notify.

### 7. The card is actually held

Only verified safe-pause and pipeline-inhibition evidence resolves the pending
Hold. If the worker vanishes before reporting, show unknown/interrupted rather
than treating absence as a safe pause. Keep the recoverable work. A restarted
manager must recover the Hold before scheduling a next stage.

If PR #57 merged before Hold took effect, do not manufacture a held card.
Follow the merge and cleanup facts; show Hold as overtaken. This is the
vision's explicit best-effort race, not an undo operation (V-7, V-8).

### 8. The owner moves Hold → Solve

This is a new move with a new revision, linked to the same card and existing
work. The request asks to resume, not create another branch or PR. The manager
reconciles before reusing the worker; if its surface is gone, recovery uses
the existing session/worktree context where possible, under cmux's safety
rules. The board stays resume pending until resumed work is observed.

Kept approvals are still subject to canonical freshness checks. A changed
head/spec/base can require review under existing gates; Hold does not grant
permanent approval. D-6 authorizes review, merge and cleanup, but only an
already running drainer merges automatically. Solve/resume never starts one.

### 9. PR review → Merge

The PR's issue relationship is verified from tracker data; #41 and #57 share
one card with links to both. Open PR and an active review loop become In
review; fresh approval and the pipeline's actual eligibility become Merge.
These are status baskets, not new owner card-move commands (V-7, V-8).
If the drainer is stopped, an approved card stays Merge, visibly waiting for
the drainer; it must not claim that merging is in progress. This does not by
itself warrant a push notification: V-9 names a stopped drainer with an
incident, not ordinary intentional inactivity. Notification triggers remain
those in the vision.

Review rounds, changes, revisions and checks follow the existing canonical
skills. A review failure or exhausted loop stays visible; a busy or missing
reviewer is not “approved.” Hold received here applies to the same card and
preserves the PR and review evidence. D-10/D-12 and the pending Q-16 gate
contract must cover review and merge races,
not merely the earlier solver example.

### 10. The running shared merge worker finalizes

The explicitly started shared merge worker (D-12) performs gated single-PR
finalization independently of the web process. Plateia reads the result.
A move to Solve, a resume, an approval or a pending Merge
card never starts the drainer. Starting it requires a separate explicit owner
request in chat, handled by the manager/shared tooling (D-6, V-5).
A lost merge response is settled by the shared finalization path against GitHub/head
facts, not retried by plateia. Approval labels alone never authorize plateia
to merge or to promise a merge. A late Hold is shown as overtaken if the merge
already committed. Unknown tracker/merge state stays unknown until reconciled.

### 11. Post-merge cleanup → Done

Merge is not card completion. Show merged, cleanup pending while branches,
worktree or claims remain. The new finalization implementation needs durable
cleanup obligations and recovery under V-12; the current drainer is evidence
of the required observations, not the chosen implementation to port (D-9).
Do not infer completion from disappearance from the open-PR list. Restarted
cleanup resumes remaining obligations rather than repeating the solve.

Once a merge is confirmed, a later readable Hold is overtaken and required
cleanup continues (D-13). If control is unreadable, D-10 still blocks the next
managed action until it is readable; the cleanup remains visibly incomplete.

Done requires issue #41 closed and post-merge cleanup complete. The closed-issue
fallback in V-7 applies only where cleanup cannot be tracked reliably, not
where a normally available service is temporarily down (Q-7). Known cleanup
debt remains visible. If the issue reopens, its card returns to the active
board; merged work is never undone. The room is archived, not deleted (V-2).

## V-12 obligations at every boundary

The five guarantees are abbreviated only in this table: **D** durable
acknowledged requests; **R** automatic reconnection/reconciliation; **S** honest
stale/unknown state; **I** idempotent retries/no duplicate work; **F** failures
and incomplete actions remain visible until resolution. Each row is a
required observation, not a claim about the existing implementation.

| Lifecycle boundary | D | R | S | I | F |
| --- | --- | --- | --- | --- | --- |
| Move to Solve | Intent and receipt committed before acknowledgement | Recover receipt after lost response | Requested Solve differs from observed idle/unknown | Same move key returns same operation | Pending submission/commit error visible |
| Post and queue | Delivery obligation outlives plateia | Bridge retries and replays record | Sent, queued and delivered distinguished | Resends retain logical identity even with new IRC IDs | Undelivered/dead letter retained |
| Manager acceptance | Exact acceptance/disposition recoverable | Replacement manager reconciles pending requests | Accepted differs from started | Repeated wake-ups and superseded revisions cannot redispatch | Refusal/never accepted retained |
| Claim and work | Operation remains linked to recoverable work | Reconcile claims, PR, worktree and session | Session status carries observation age | One existing execution is adopted, not cloned | Lost worker, block or partial work visible |
| Request Hold | Hold receipt and latest revision survive | Resume delivery after restart | Working until pause proven | Repeat Hold does not reset or release work | Pause pending/failure retained |
| Safe pause | Recovery checkpoint and inhibition survive | Reconcile inhibition before next dispatch | Silence is unknown, not paused | No later stage starts from replayed Solve | Missing checkpoint or merge race visible |
| Resume Solve | New intent linked to kept work | Recover same work/session where possible | Resume pending until observed | No second claim/branch/PR | Recovery failure remains actionable |
| Review and Merge status | Original request and progress links retained | Refresh pipeline/head/checks after outage | Old approval/checks labelled stale; stopped drainer shown waiting | Review retries use canonical ownership/gates; never auto-start drainer | Blocked/incomplete review or waiting merge retained |
| Merge | Shared drainer retains interrupted-merge evidence | Reconcile GitHub outcome after network loss | Unknown merge result is not Done | No second merge action from a web retry | Incident/overtaken Hold recorded |
| Cleanup | Outstanding obligations persisted | Shared cleanup retries after restart | Unavailable cleanup feed differs from complete | Retry only outstanding cleanup; never re-solve | Cleanup debt/incident retained |
| Done | Intent/outcome history and room retained | Reopen and reconciliation recover automatically | Completion tied to known evidence or explicit fallback | Replayed completion cannot restart work | Any remaining known failure stays visible |

## Scope and vision traceability

In this pass: the single-card lifecycle, required contracts, system evidence,
failure boundaries and questions needed to define the first deliverable.
No implementation, issue drafting, workflow execution or publication.

| Principle | Constraint on this pass |
| --- | --- |
| V-1 | One owner; project/machine setup must remain scriptable. |
| V-2 | Project main room and request room; preserve/search archived record. |
| V-3 | Existing IRC backbone, one replaceable boundary; no second command transport. |
| V-4 | Stable session handles and truthful presence belong to shared services. |
| V-5 | Every workflow action is an authenticated owner chat post; no web skill/terminal/tracker mutation. |
| V-6 | Shared queued/delivered/accepted semantics; direct agent work uses the same claims and gates. Direct-address UI is later scope. |
| V-7 | Owner intent versus status baskets, safe Hold, latest move wins, races and tracker-led Done. Inbox cancellation remains required later scope; do not implement it as Hold. |
| V-8 | Board-owned intent/order storage; independent real-state projection; owner/manual work counts. |
| V-9 | Failures/input needs notify; routine progress does not. Browser-push replacement remains required. |
| V-10 | Full lifecycle and recovery must be equally usable on phone and desktop. |
| V-11 | Localhost/Tailscale; runtime state/config outside Git; synthetic evidence only. |
| V-12 | Python server, mature open-source tools; all five guarantees above; framework bake-off later. |
| V-13 | Delivery, presence and execution function without plateia; shared behavior serves CLI/kanban too. |

The rest of mid-term scope remains in the vision: Approve and Inbox cancel,
PR-only cards, direct addressing, chat search/reactions/images, unread/needs-you
views, push and setup. Whether any of these is necessary in the first delivery
must be settled through Q-11 rather than silently included or excluded. The
vision's out-of-scope items remain out of scope.

## Design: contracts derived from the walk

These are required semantics, approved directions or explicitly labelled
proposals. The approved envelope direction is not yet a complete wire schema
or issue specification.

### C-1. Board persistence and acknowledgement

**Required by V-8/V-12:** plateia owns storage of requested basket, owner card
order, card identity/issue-PR association, and links to request receipts and
outcomes. Tracker labels must not store board preferences. Observed facts may
be cached but carry source, observation time and freshness; a cache is not an
independent tracker. All real state is runtime data outside the repository.

**Approved direction D-3 (formerly P-1):** use SQLite for plateia's board
state and an independent durable submission/receipt API in shared chat
tooling. The proposed database path remains
`~/.local/state/plateia/plateia.sqlite3`; the exact schema/API are unapproved.
Submission receipts must identify the logical move across retries.
The shared boundary owns chat publication/retry after accepting a submission;
it keeps running when plateia is down. Plateia must not be the sole holder of
an acknowledged, not-yet-posted request.

There are two commits to account for: board intent and shared delivery
acceptance. A crash between them must reconcile using the same operation
identity. An acknowledged move requires durable proof of both; no reliance on
an in-memory HTTP-to-IRC call. D-8 assigns revision allocation to the shared
submission boundary. D-11 fixes the order: shared submission/receipt first,
board intent plus receipt in one SQLite commit second, acknowledgement last.
If the web process dies in between, rebuild its view from the original shared
receipt/history before displaying current intent or retrying. Reconciliation
API, retention and corruption handling still need the Q-14 contract.
The chosen direction is a durable handoff between plateia-owned board storage
and shared delivery, rather than one common board/workflow transaction.
Commit direction is approved; remaining recovery details are Q-14/Q-11;
SQLite approval does not imply approval of a schema or the proposed path.

### C-2. Card-move chat format and ordering

**Required:** the record must let a human and the system identify the card,
destination, logical move, ordering and authorized endpoint. Authenticated
sender metadata supplies authority; body text never supplies it. Link queued
receipt → IRC message ID(s) → exact manager acceptance → execution → outcome.
A retry and a new move must be distinguishable. Multiple lines must not become
multiple commands. Superseded requests get an explicit disposition.

**Approved direction D-8 (formerly P-2):** a readable `card-move/v1` command
inside
the existing `[request <request-id>]` convention, with stable operation ID,
repository-qualified card key, per-card revision and destination. Preserve
normal human posts. Illustrative field layout, **not yet a final wire schema**:

```text
[request alpha-20261001-1] card-move/v1 op=move-a card=example/alpha:issue:41 rev=1 to=Solve
[request alpha-20261001-2] card-move/v1 op=move-b card=example/alpha:issue:41 rev=2 to=Hold
[request alpha-20261001-3] card-move/v1 op=move-c card=example/alpha:issue:41 rev=3 to=Solve
```

`move-a` is an illustrative ID, not an ID-generation scheme. Request IDs,
operation IDs, IRC `msgid`s and execution IDs have distinct jobs. A duplicate
operation with different content must be rejected visibly. A delayed revision
must not overwrite a later intent. Multi-browser ordering, non-web producers,
replay of a batch and manager reads at dispatch/safe boundaries use D-8's
shared submission order. The same logical move retains its original revision
on retry; a later deliberate move receives a newer revision. Client timestamps
do not settle order.

Putting the command solely in IRC tags was not selected; the readable text
envelope is approved. Existing reply/reaction tags may still carry their usual
metadata. Preserve history, readability, existing pchat callers and complete-
body validation before dispatch. Room routing, exact field encoding and
reply/event schema are unresolved (Q-14); do not implement a final parser from
this illustrative layout. Solve's authorized endpoint is D-6 and must not
encode implicit permission to start a stopped drainer.

### C-3. How the card learns real state

**Required by V-8:** reconcile tracker issue/PR relationships and closure,
canonical pipeline review/check/head state, shared work/claims, session
presence, correlated chat acceptance/progress and drainer merge/cleanup.
Acceptance is not running; session idle is not safe Hold; issue closed is not
cleanup complete; a worker's `done` is not card Done.

**Proposal P-3:** a read-only projection with requested intent/revision,
observed phase, active execution, pause/cleanup evidence, receipt disposition,
source timestamps and unresolved failures. Publish snapshots plus a resumable
change cursor; recover with a snapshot if a cursor cannot be replayed. Exact
API/transport and freshness deadlines remain open. Poll GitHub centrally per
project with caching/backoff and rate-limit awareness rather than per card or
browser. No particular polling interval is accepted.

Identity must survive linking PR #57 to issue #41 without making another card
or losing owner order. PR-only and multiple/replacement PR cases need explicit
rules before their delivery. A newer tracker fact must not be overwritten by
an old chat replay. Inconsistent evidence produces a visible unresolved state,
not a guessed success. Progress moves a card to the top of its new basket;
owner reorder remains presentation only. Exact reconciliation ordering needs
later signoff before implementation.

### C-4. Shared ownership under V-13

| Plateia owns | Shared services/skills own |
| --- | --- |
| Browser gestures and presentation; board intent/order storage; request/outcome display | Logical request submission, durable queue/receipts, publication through the IRC boundary, delivery/acceptance tracking |
| Room/card links, pending/stale/unknown display, private web access | Agent handle allocation, session-to-handle correlation and presence |
| Read-only board projection and cached presentation | Manager scheduling; claim/execution deduplication; safe pause/resume; recovery checkpoints and pipeline inhibition |
| Web push subscription/UI and phone installation experience | Workflow failure/needs-owner facts and independent delivery continuity; push integration ownership still to design |
| Python web server and eventual chosen page framework | Canonical issue/PR gates, drainer eligibility/merge and persisted cleanup |

**Approved direction D-4 (formerly P-4):** use shared durable card-level
pause control correlated to chat intent. Hold inhibits future stages and
drainer eligibility without changing approval evidence. An in-flight step
finishes safely; the service reports the actual pause or overtaken outcome.
Resume removes inhibition for the newer intent and reuses existing work.
The shared record does not replace GitHub claims/specs/checks or store board
order. It is not a plateia-only switch or a second owner-command channel.

The drainer needs an appropriate recheck near its irreversible merge boundary;
best-effort races must remain visible. No proposal promises that an HTTP Hold
acknowledgement prevents a merge already in progress. D-10 blocks new managed
actions or merges on unreadable control, without claiming a confirmed Hold.
Exact coordinator integration remains Q-11. D-13 settles readable Hold after
confirmed merge; the remaining completion-evidence questions stay in Q-7.

### C-5. Source ownership, compatibility and drainer authority

**Approved directions D-6/D-7:** all new code lives in `coghex/plateia`, with
workflow packages runnable independently of the web application. Existing
kanban code and local skills are evidence and migration sources, not the home
for new backend features. Kanban's eventual retirement is the owner's stated
direction; no immediate retirement or full migration scope is approved.

D-9 replaces the proposed drainer port with fresh shared finalization based
on the finalize step. Do not create two competing active merge authorities.
The gate/merge/cleanup contract is the starting evidence; durable delivery,
safe Hold, ambiguous-merge recovery and visible incomplete cleanup are V-12
requirements the new implementation must supply. D-12 settles the independent,
explicit-start worker model; Q-16 retains approval/gate questions. D-15 fixes
pinned package distribution; D-16/D-17 settle the Python API boundary and
focused migration scope. C-7/Q-12 records their deployment/cutover prerequisites.
No change in kanban is silently exempted from the all-new-code direction.

The manager may advance Solve through canonical review and merge/cleanup
when the drainer is already running. If it is stopped, keep the card waiting
and the request incomplete. Do not start it because of card moves, approval,
service installation, reconciliation or a resumed Solve. Only an explicit
owner start request authorizes startup. Plateia posts that request through
chat; it never controls the service itself. Migration and setup must preserve
this distinction rather than bundling implicit startup with installation.

### C-6. Approval continuity across a proven equivalent base update

**D-14/D-18 select carrying the prior reviewer judgment under the following
equivalence constraints, with a metadata-only initial base-drift boundary.
P-5 retains its stable proposal ID; the direction and boundary are now accepted.**
It belongs in the shared canonical approval
and finalization services (PLT-5), with its receipt visible through PLT-6.
The browser neither proves equivalence nor restores approval labels.

#### P-5. Eligibility and provenance

Only an open PR targeting the same resolved default branch qualifies. Before
the update, its exact head H0 must have a genuine, fresh canonical APPROVE
from the required opposite brand, bound to the currently approved issue spec
and review policy. Unknown origin follows the existing canonical review rule;
missing required reviewer evidence is not made eligible by a carry receipt.

Capture immutable baseline evidence before updating: repository/PR, H0,
base B0, approved-spec fingerprint, policy/configuration fingerprint, original
canonical review receipt and authenticated publisher/reviewer brands. Record
the managed update ID, pinned base B1, resulting head H1 and expected tree.
Nicknames, colors, message text and caller-supplied tags cannot authenticate
that evidence. A legacy approval missing these bindings requires canonical
review; reading today's spec does not reconstruct what was approved earlier.

Only a controlled, conflict-free forward merge of pinned B1 into H0 qualifies;
H0 must remain reachable and B1 must be a verified advance of the same base.
Fast-forward is eligible only if it satisfies the same proof. Manual conflict
resolution, custom merge-driver transformations, arbitrary extra commits,
retargeting, force-push, rebase and squash updates are outside this initial
automatic rule. Already-merged PRs go to reconciliation/cleanup, never
retroactive approval.

#### P-5. Exact content proof and substantive boundary

Use complete immutable Git objects, not truncated API patches. Compare the
full PR delta at H0 against its merge base with B0 to the full delta at H1
against its merge base with B1. Require identical paths, operations, old/new
blob identities, object types and modes. Handle rename endpoints, additions,
deletions, binary files, symlinks and submodules explicitly; unsupported cases
fail closed. Never normalize whitespace or omit tests, docs, configuration,
dependencies, build files or generated artifacts merely by extension.

Let P be the union of PR-owned paths before and after the update, and U the
paths changed by importing B1 into H0. Require P and U to be disjoint and H1's
tree to equal the controlled merge result. Conflict-free alone is insufficient.
The approved spec and review policy must be unchanged. Any substantive change
to the PR's reviewed contribution requires a real canonical review.

**Accepted initial boundary (D-18):** disjoint base code can still alter the
behavior of an unchanged PR. Automatic carry permits
only imported coordination metadata on an explicit, owner-approved per-repo
path allowlist; all other imported changes require canonical review. An empty
allowlist grants no exemption. Runtime code, tests, dependencies, build
inputs, approved specs, acceptance contracts and review/CI gates cannot enter
that allowlist. Do not treat all documentation as metadata. A broader rule
allowing disjoint substantive base changes needs explicit owner approval and
a concrete independence proof; disjoint paths alone do not prove it.

#### P-5. Publication, freshness and CI

No deliberate revocation, newer rejection or blocking verdict may exist. If an
approval label was stripped, positively establish that this exact managed
update caused the invalidation. Same-head removal or an unknown removal cause
is not recoverable automatically. Respect a canonical review already in flight.

Publish a distinct canonical **carry receipt**, binding original review,
H0/B0, update/B1/H1, unchanged spec/policy and equivalence proof. It says that
judgment was carried, not that a reviewer ran on H1. The authenticated
canonical validator must recognize that receipt before approval is restored;
neither a label edit nor a counterfeit fresh-review marker suffices. Fence
publication by repository/PR/H1/spec so retries reconcile the same receipt.
The exact receipt format and validator changes remain to be designed.

Carry satisfies review continuity only. All current required CI and the
review gate must independently succeed for H1 under the approved gate policy;
H0's green checks never substitute. Check selection and merge privileges
remain Q-16 choices. Re-read head, base, spec, policy, verdict and shared Hold
before publication and immediately before a head-bound merge. Any drift
invalidates eligibility and requires reconciliation. A carry never starts the
merger (D-6/D-12), bypasses Hold or grants a base-retarget race exception.

#### P-5. Fail-closed behavior and verification

Missing or shallow history, incomplete diffs, untrusted provenance, unknown
label-removal cause, overlapping paths, changed contribution/spec/policy,
unapproved imported changes, conflicts, revocation, failed/unknown CI or
concurrent drift mean **no automatic carry and no merge**. Preserve historical
review and work, expose the unmet condition, and route to canonical review or
repair. Ambiguous publication is reconciled by receipt identity before retry;
never speculate by re-adding a label. An unreadable service leaves state
unknown and the incomplete action visible (V-12), not implicitly approved.

Use synthetic fixtures for allowed metadata advances; clean overlapping and
disjoint substantive-code updates; changed specs; manual revocation; concurrent
review; rename/add/delete/type/mode/binary/submodule cases; incomplete history;
forged provenance; head/CI drift; ambiguous publication and repeated recovery.
Verify the opposite-brand origin and current-head/spec/CI gate on every
successful case. The boundary is accepted; exact receipt/validator mechanics
still need reviewed implementation. No label restoration is authorized by
recording this design, and an empty per-repository allowlist grants no carry
exemption for imported changes.

### C-7. Pinned shared releases, compatibility and upgrade recovery

**D-15/D-16/D-17 fix distribution, preservation, the versioned Python API in
existing processes and the focused initial migration. P-6 formalizes their
release contract; detailed manifests/ABI/controller mechanisms remain subject
to normal implementation review.** These are shared
workflow capabilities. They work without the plateia web process and never
give the browser a service-control or installation path.

#### P-6. One source and one supported chat API

Reviewed public source for new packages and compatibility entry points belongs
in `coghex/plateia`. Capture relevant existing code and the owner's current
fixes through a reviewed, sanitized source migration, not a copy of the entire
private skills repository. Inventory the participating script versions, their
local modifications and corresponding tracked fixes before capture, and check
again before release. A moving local edit is a conflict to preserve and report,
not something an importer may overwrite. Import no accounts, messages, runtime
configuration, session records or personal paths. Public migration evidence
uses invented fixtures; local file inventory and backup manifests stay private.

There is one supported versioned API for normalized chat reads, logical
submission, receipt/delivery/acceptance queries, acknowledgements and shared
identity/presence queries. IRC parsing, tag preservation, history/cursors,
multipart handling and account attestation live behind that boundary.
CLI/bridge/inbox consumers do not each carry a private chatlib fork, log parser
or identity allocator. Managers and workers use the same entry points;
skills describe the contract and authority, rather than embed copied transport
implementations. State ownership and serialization remain in the shared
implementation, not in a web handler.

Accepted initial deployment (D-16): a public versioned Python API used by the existing
independent bridge and thin CLI/adapter clients, without another API daemon.
An IPC service would be a different lifecycle/deployment contract, not an
automatic consequence of installing Python packages. It is not selected for
the initial release; no IPC endpoint or permissions are added.

Initial migration (D-17) covers the shared chat/inbox path, current identity,
color and recovery fixes, thin compatibility entry points and necessary
launch/reviewer identity hooks. Broader manager/worker skill-source migration
is later work, not a hidden prerequisite. Existing managers/workers remain
supported consumers; delaying their full source migration does not drop their
current fixes or change their authority. PLT-3/PLT-4 may add the focused skill
changes needed for card control, with their docs in the same reviewed PR.

Normalize only proven server-attested accounts, stable server msgids, server
order/time/target, complete tags and reply relationships. Preserve multiline
batch and logical-part evidence instead of flattening it into fabricated
messages. Legacy records lacking required attestation/envelope evidence are
unusable for the affected automatic action, not upgraded by guessing. History
replacement/truncation, unavailable acknowledgements and conflicting evidence
are explicit errors. Map old `from`/`verified` fields only where their trusted
writer and provenance meet the adapter contract; never fill an account from
nick patterns or client tags.

The inbox's specified leading-address/envelope rules and `card-move/v1` must
share a codec with distinct request semantics. The current bridge's inline
mention scan is a known overlap. Do not silently extend the inbox's
leading-only rule to all ordinary V-6 chat, or create a second competing
router; Q-14 must reconcile explicit/quoted addressing and multipart framing.

#### P-6. Preserve the owner's identity, colors and workflow fixes

Keep existing account identities, project/role bindings, session resume keys,
retired names and monotonically increasing worker counters. Resuming a session
does not allocate another identity; installing or rolling back does not
register accounts or discover-and-renumber running agents. Keep the existing
global assistant and its configured authority; do not introduce a substitute
assistant account. Registry writes retain locking, durable reservation before
provisioning and atomic file/directory persistence. Preserve the live registry;
it is not release content.

Keep the owner's current role-color mapping and local prefix decoration;
neither historical messages nor authenticated identities are recolored by
rewriting raw chat. A color never proves identity or authority. Import the
reviewed color behavior and test it with invented roles/accounts. A modified
local WeeChat integration is protected like any other installed entry point.

Capture the active delivery fixes: persisted catch-up checkpoints, acceptance
recovery before message deduplication, distinction between queued waiting and
delivery failure, safe busy/waiting-session handling, retargeting and visible
dead letters, empty-inventory protection, permanent roles and verified cited
delegation with actual sender/authority kept distinct. Do not map safe Hold or
upgrade checkpoints to the legacy interrupt path. Preserve existing claims,
review freshness and opposite-brand
publisher gates. Background reviewer identity/output/completion is separate
from a canonical verdict. Any reviewer wrapper migration preserves the
canonical validation/publication path; it does not relabel approval or change
model-class resolution. Manager/worker safe-point changes remain PLT-3/PLT-4;
fresh finalization remains PLT-5, not a drainer port.

A locally patched preview stays in place until its relevant fix is reviewed,
captured and covered by a separately authorized replacement. D-15 permits no
new private patched snapshot and no installation exception. Preserving active
work does not mean shipping an unreviewed private fork as the release.

#### P-6. Immutable release and thin entry-point contract

Each release has an immutable identity and manifest binding source commit,
package/skill versions, artifact hashes, required Python version/platform,
pinned dependencies and supported API/protocol/state read/write versions.
Build from a clean reviewed commit; do not include a mutable checkout on the
runtime import path or install editable packages. Build/test artifacts with
invented data only. Code and its operating guidance ship in the same release;
unversioned downloaded scripts cannot substitute for declared dependencies.

Stage the artifacts and a private Python environment in a new versioned
release directory outside the repository. The environment binds the selected
interpreter and exact artifacts; it neither edits system Python nor installs
private settings into a package. Keep the previous compatible release and
every version still referenced by live processes, cached skills, hooks,
entry points or service definitions. Retaining only one previous version is
insufficient when an older agent remains active. Retirement requires verified
absence of references and a reviewed cleanup policy; unresolved references
block deletion. Test a missing referenced hook with invented paths, without
publishing real local error output.
Concrete directory names/build tooling are implementation details; the manifest
and offline verification contract must be reviewed with PLT-11/PLT-12.

Thin entry points resolve an explicitly selected release, then delegate to its
API. Bind a process/action to one release for its lifetime; don't dynamically
swap modules midway through a tool action. Skill entry points declare their
compatible executable/API versions. Cached skills and already-running agents
may remain on their existing release when the compatibility matrix permits;
incompatible consumers block visibly instead of being forcibly restarted.
Status reports distinguish staged, selected and actually running versions.

Keep the existing non-web commands, bound identity behavior, request/reply
headers and output/exit semantics unless an explicit versioned change is
approved. Document and test compatibility from invented caller transcripts.
For example, `pchat` exit 3 means a queued outbox entry: an adapter cannot
translate it into proof that no message can be sent, then automatically resend.
Shared API submission proves submission only; delivery and manager acceptance
still require their own evidence. Unknown or interrupted outcomes reconcile
by stable action key. The inbox never inherits card-move retry authority by
accident; each protocol's replay policy remains explicit.

#### P-6. Staging, upgrade and modification protection

The installer separates **plan**, **stage**, **preflight**, **select/activate**
and **rollback**. Planning reports intended code/entry-point changes without
reading or printing private content. Staging creates a new release without
replacing active scripts, changing service definitions, selecting a release,
posting chat or scheduling work. Preflight uses supplied private settings only
to validate compatibility; it never provisions identities or changes access.
The existing identity rollout helper is not invoked by these phases.

For every managed target, record the prior expected content hash/type/link and
the proposed replacement. Missing ownership evidence, unknown files, changed
content or a retargeted link blocks replacement. Recheck immediately before
changing it. Do not force through, delete a modified installed file or apply
an automatic private patch. Preserve a private backup and conflict inventory;
resolve the modification by reviewing its fix into source or by a specific
owner-approved disposition, not blanket overwrite permission.

Use a locked, durable local upgrade journal with an operation identity, old/new
release manifests, proposed bindings, compatibility results and per-step
intent/outcome. Persist intent before each change. Select a complete release
binding atomically and durably; restart recovery inspects the actual binding
and journal before deciding what completed. A filesystem pointer and a journal
are not a cross-file transaction: interrupted activation must be reconciled,
not reported as succeeded or blindly applied twice. Concurrent upgraders
cannot interleave; unrelated live agents remain untouched.

Activation needs separate owner authorization naming affected entry points and
services. Use safe checkpoints for the affected writers and retain queued work;
never kill a worker mid-step to upgrade. Permit old/new processes to overlap
only under a verified read/write compatibility matrix and shared fencing;
there cannot be two active delivery/merge owners for the same obligation.
Unreadable compatibility, state or service evidence leaves activation blocked.
Installing/selecting code never starts the merger or enables a poll schedule.
A service restart or cutover requires its own authorized plan, not an
installer side effect. All such actions remain held in this conversation.

#### P-6. Versioning and rollback without rewinding work

Declare API, wire and persistent-state versions separately from package
versions. Negotiate or validate capabilities before any write; unsupported
versions are errors, not an invitation to reinterpret records. Initial upgrades
must remain readable/writable by the declared rollback release, including new
records written after selection. Additive changes are not assumed compatible
without that evidence. A breaking schema/migration requires a later explicit
owner checkpoint and a reviewed recovery plan; it is outside this first
rollback contract.

Rollback selects the previous verified compatible code/entry-point set against
the **latest** state. It never restores an old identity registry, counters,
private configuration, history, delivery cursor, outbox, request receipts,
claims, Hold state, inbox state, reviews or cleanup journal. Backups preserve
evidence; restoring a stale data snapshot is not routine code rollback.
Account/credential files stay in their existing private locations and are not
copied into artifacts. Any required configuration migration is separately
reviewed, preserves unknown local fields and requires explicit authorization;
the initial package installation requires no such migration.

Revalidate compatibility before rollback and reconcile any ambiguous activation
result first. If older code cannot consume current state, stop with visible
repair/roll-forward guidance and keep all work; do not erase newer records to
make it run. Rollback never fabricates message acceptance, repeats uncertain
sends, resets identity numbers or clears incidents/cleanup debt. Automated
selection/rollback permissions are not implied by this design; eventual
activation authorization must state the permitted recovery actions.

#### P-6. Existing inbox and eventual merger cutover

PR #1's approved `f9aef08` core merged unchanged on 2026-10-04 (`ef86f17`).
PLT-13 is a proposed separate real-adapter follow-up on that `master` core.
Reuse its
protocol and authority ceiling, manager-only replies/acceptance, intentional
owner escalation and uncertain-send behavior. Do not broaden its authority,
create duplicate collection, or make release staging enable its schedule.
No new issue for its already tracked neutral core is proposed.

Any later merger cutover inventories outstanding merges/cleanup, preserves
their disposition, and allows only one active authority per repository. The
owner must approve the cutover and explicitly start the new worker. This
release design neither migrates the old drainer state machine nor starts a
replacement. D-14/D-18's carry direction and metadata-only initial boundary
remain approved; no upgrade can silently widen them or bypass CI/spec freshness.

## Decisions

### D-1. Design in the docs worktree, beginning with the card lifecycle

Explicit owner instruction in this conversation, 2026-10-01: create
`docs/plateia_design.md` in the `docs-wip` worktree at `.worktrees/docs`; start
with the specified Solve → Hold → Solve → review → merge → cleanup → Done
walk and recovery guarantees. A later Claude session processes approved
slices with `process-design-doc`. This instruction authorizes drafting, not
tracker creation, publication or readiness signoff.

### D-2. The accepted vision governs this design

Inherited owner decisions in `docs/vision.md`, whose acceptance is recorded
there as 2026-10-02: V-1 … V-13 are authoritative. In particular Python,
chat-only actions, plateia-owned board storage, shared workflow ownership,
safe Hold, latest move wins and the five reliability guarantees are settled.
New mechanism approvals are recorded below; the vision itself is unchanged.
P-3 remains a proposal; directions formerly P-1, P-2 and P-4 are approved
only to the extent enumerated in D-3, D-8 and D-4 respectively.

### D-3. SQLite board storage with independent durable shared delivery

Explicit owner approval in this conversation, 2026-10-01: SQLite stores
plateia's board state; shared chat tooling provides durable submission
receipts and keeps delivering acknowledged requests while plateia is down.
Acknowledgement requires durable board intent and shared acceptance. The
shared service owns publication/retry; plateia owns intent/order presentation.
Exact database location/schema, API, commit order and wire syntax remain open.

The shared atomic-board-store alternative in Q-1 was not selected. Separate
ownership requires an idempotent handoff and reconciliation across both
durable records; no distributed transaction has been approved.

### D-4. Shared Hold covers workers, review and drainer eligibility

Explicit owner approval in this conversation, 2026-10-01: use shared durable
pause control across the card's pipeline, preserving work and approvals while
an active step reaches a safe point. Workers, review and drainer eligibility
must honor it. The control survives plateia downtime and serves non-web
interfaces. Resume reuses the kept work.

Stopping only a solver is rejected as insufficient because an approved PR
could still drain. Approval withdrawal is not the Hold mechanism. Exact
checkpoint boundaries, missing-state behavior and merge/cleanup races remain
open; best-effort/overtaken behavior still follows V-7/V-8.

### D-5. Shared prerequisites precede the plateia card slice

Explicit owner approval in this conversation, 2026-10-01: split the complete
lifecycle into dependency-ordered shared-service prerequisites and then the
plateia card slice. Do not treat the full walk as one mixed cross-repository
PR. A contract/recovery prototype alone was not selected as the first delivery
strategy. Exact child boundaries and owning repositories still need approval;
the delivery plan below is a proposal implementing this direction.

### D-6. Solve authorizes the pipeline but never implicitly starts the drainer

Explicit owner approval with qualification in this conversation, 2026-10-01:
Solve authorizes the normal pipeline through review, drainer merge and cleanup.
Automatic merge occurs only if the drainer is running. Start the drainer only
when the owner explicitly asks for startup. Solve, resume, Merge status and
approval do not supply that startup authorization.

A stopped drainer leaves approved work waiting visibly in Merge; a later
explicit start can drain it subject to the latest Hold and canonical gates.
The existing manager instruction to start a stopped drainer whenever a request
includes merging must be superseded. This design does not start, stop or
reconfigure the live service. An incident still follows V-9; intentional
stopped status alone is not a new notification trigger.

### D-7. All new code is plateia-owned; kanban retires gradually

Explicit owner approval and extension in this conversation, 2026-10-01:
shared code has its tracked home in plateia as independently runnable
packages; **all new code** belongs there too. The owner states that kanban
should eventually be retired.

The earlier proposal to grow canonical drainer code in kanban is superseded.
Existing tools continue as current infrastructure until an approved migration
replaces them; preserve their canonical contracts, operation without the web
application and their non-web interfaces. V-13's boundary is runtime ownership,
not a requirement to keep new workflow code in another repository.

No immediate retirement, wholesale rewrite, service switch or exception for
new compatibility code in kanban is inferred. D-15/D-16/D-17 subsequently settle
the focused source/update direction and scope; Q-12 retains deployment
prerequisites. The accepted vision is unchanged.

### D-8. Readable card-move commands with shared revision allocation

Explicit owner approval in this conversation, 2026-10-01: readable
`card-move/v1` chat commands carry immutable logical move IDs and per-card
revisions allocated by the shared service. Retrying a logical move retains
its revision; concurrent moves follow shared submission order. The approved
direction retains repository-qualified card identity and existing authenticated
owner/request-prefix conventions.

Command-only IRC-tag metadata and browser-clock ordering were not selected.
Exact field syntax, operation/request ID generation, endpoint encoding,
room routing, size validation and response-event schema remain Q-14. Handoff
commit/recovery ordering is D-11. Replayed IRC message IDs remain distinct
from logical move identity.

### D-9. Build finalization fresh from the finalize step, rather than porting the drainer

Explicit owner direction in this conversation, 2026-10-02: the current drainer
is a local script; if its functionality becomes part of plateia, start from
scratch with the finalize step. The proposal to migrate the existing drainer
into plateia before adding Hold is not selected.

Implement new shared workflow code in plateia (D-7), preserving V-13's
independence from the web process. The installed finalize gate/merge/cleanup
step is the starting contract to study; the legacy command's manual-only
invocation rule is current behavior, not implicit authorization for new
automatic invocation. D-12 separately approves the new shared worker runtime.
D-6's explicit-only startup rule remains in force; the new worker does not
silently invoke the manual fallback skill under its old authorization rules.

The existing drainer continues as current infrastructure until a separately
approved cutover. No service is started, replaced or retired by this design.
Fresh implementation must meet V-12 rather than inherit reliability the
manual finalize step does not provide.

### D-10. Pause after the current action; unreadable control blocks new managed work

Explicit owner approval in this conversation, 2026-10-02: managed work finishes
the current tool action, saves recovery context and checks control before the
next action. If control is unreadable, start no new managed action or merge
and show control unknown, not confirmed Hold. The owner remains unrestricted.

Exact safe boundaries for nested coordinators, tests and long-running
subprocesses still need implementation evidence. A confirmed held card needs
checkpoint plus whole-pipeline inhibition evidence, not merely an idle prompt.

### D-11. Commit shared receipt, then board state, then acknowledge

Explicit owner approval in this conversation, 2026-10-02: commit shared
submission/receipt first; commit board intent and receipt together in SQLite
second; acknowledge last. A restart between commits recovers the board from
the same shared receipt. The browser retains one logical move identity across
lost responses; resubmission does not invent another move or revision.

The shared service may already publish/execute a request before the web
acknowledgement. That is a recoverable incomplete web action, not proof of
manager acceptance or another authorization. SQLite-first staging was not
selected. Exact receipt lookup, reconciliation/retention and browser identity
storage remain Q-14/Q-6; all incomplete actions stay visible under V-12.

### D-12. An explicit-start shared worker runs fresh finalization

Explicit owner approval in this conversation, 2026-10-02: an independent
shared merge worker, explicitly started through chat, applies the fresh
single-PR finalization step to eligible authorized PRs in number order until
stopped. Solve/approval never starts it. It respects shared Hold and remains
independent of the plateia web process.

This selects the shared worker rather than a manager launching an agent for
each merge. It does not settle approval renewal or all merge-gate details
(Q-16), authorize cutover from the live drainer, or authorize startup now.

### D-13. Readable Hold after confirmed merge is overtaken; cleanup continues

Explicit owner approval in this conversation, 2026-10-02: after a confirmed
merge, show a subsequent Hold as overtaken and continue required cleanup.
Do not keep a half-cleaned merged worktree merely because of that late Hold.
This is a specific post-merge exception to pre-merge Hold inhibition.

The merge is never undone or re-solved. Cleanup debt stays visible until
resolved and the issue/cleanup conditions still govern Done. D-10 continues
to block new managed actions when control is unreadable; this decision does
not grant an outage exception. Reliable cleanup/claim-release coverage and
PR-only completion rules remain Q-7.

### D-14. Carry prior reviewer judgment under proven equivalence

Owner decision, **2026-10-02 16:03:41 UTC**, relayed through the project manager
from the owner's authorized assistant: let the prior reviewer judgment carry
forward under the equivalence rule. This selects Q-16's carry-forward direction
instead of requiring a new reviewer run for every eligible base update.

It does not permit approval across changed substantive code or bypass
freshness, the approved spec, opposite-brand review or CI. C-6/P-5 presents the
concrete eligibility, provenance, boundary and fail-closed contract for owner
review; this entry alone did not decide the substantive boundary, later
resolved by D-18.
Other finalize gates and merge semantics remain open in Q-16.

### D-15. Pinned plateia releases with thin entry points and one shared chat API

Owner decision, **2026-10-02 16:40:09 UTC**, relayed through the project manager
from the owner's authorized assistant: select pinned plateia packages in a
private Python environment, thin CLI/skill entry points and one shared chat
API. The editable-checkout deployment option is not selected. There is no
installation exception and no private patched snapshot.

Preserve existing identities, private configuration/state, active identity,
color and workflow fixes, and modified installed files. C-7/P-6 formalizes the
proposed release/compatibility/upgrade/rollback contract around these constraints.
It did not alone settle the API deployment/migration choices, now resolved
by D-16/D-17. It does not grant
deployment/installation/service activation/merge/scheduling authority, or
approve the proposed implementation slice boundaries.

PR #1 stays open/unmerged at its approved `f9aef08` commit. D-14 carry-forward
stays approved; the metadata-only initial boundary is subsequently accepted
in D-18. No installation, activation, merge or scheduling action was taken.

### D-16. Versioned Python chat API in existing bridge/client processes

Owner decision, **2026-10-02 17:10:18 UTC**, relayed through the project manager
from the owner's authorized assistant: accept the recommended shared, versioned
Python API inside the existing bridge/client processes. No new IPC daemon is
introduced for the initial release. This complements D-15's pinned private
environment and thin entry points; it does not authorize service activation.

### D-17. Focused shared-chat/inbox migration and necessary identity hooks first

Owner decision, **2026-10-02 17:10:18 UTC**, relayed through the project manager
from the owner's authorized assistant: migrate the focused shared chat/inbox
path, necessary compatibility and identity hooks, and current identity/color/
workflow fixes. Broader manager/worker skill migration comes later. Preserve
existing identities, private configuration/state and modified installed files
through C-7's versioned upgrade and state-preserving rollback contract.
Integration follows the normal reviewed workflow; there is no installation
exception, private patched snapshot, activation or scheduling authorization.

### D-18. Metadata-only initial base drift for approval carry-forward

Owner decision, **2026-10-02 17:10:18 UTC**, relayed through the project manager
from the owner's authorized assistant: accept the metadata-only initial
base-drift boundary in C-6/P-5, under its equivalence, provenance, freshness,
approved-spec, opposite-brand and current-head-CI constraints. D-14's prior
judgment is carried only after that proof; this is not a new reviewer verdict.

Disjoint substantive base changes are outside the initial carry rule. Changed
substantive PR contributions never qualify. Each repository needs an explicit
approved coordination-metadata allowlist; absence grants no imported-change
exemption. Exact validator/receipt implementation and other Q-16 merge-gate
choices remain review prerequisites, not permission to restore labels or merge.

### D-19. PLT-9 captures from a committed baseline, not the live working tree

Owner decision in this conversation, 2026-10-08: before PLT-9, the live
shared-chat working tree's reviewed state is committed in its own local
repository; PLT-9 then captures from that named commit and records it as
provenance. Its drift check compares the live tree against that commit.

Capturing the uncommitted live tree directly was not selected: reviewers would
check it against history on no branch, and the drift check would have no
fixed baseline. Capturing only the older committed head was rejected: it lacks
modules `pchat` imports at startup and the live-session identity fix.

The baseline commit is an external prerequisite outside `coghex/plateia`,
carried out by the owner or the owner of that work, not a plateia slice or an
action of this design. The owner also asked for PLT-9 to be split; D-20
records the split.

### D-20. Split the capture into a core slice and a bridge slice; exclude the identity rollout

Owner decision in this conversation, 2026-10-08: split the former PLT-9 by
layer into two dependency-ordered slices. PLT-9 captures the identity and
transport core (`chatlib`, `identities`, `binding`, `runstore`, `role_colors`
with names resolved at runtime, `pchat`, `agentcli`, the test-isolation guard
and their tests). PLT-15 then captures the bridge (`chat-bridge`, the
reviewer-start receipt experiment, `rotate-logs` and their tests). Both capture
from the D-19 baseline. `install-identities` is excluded from both: it is a
mutating identity rollout that edits server and plugin configuration and
restarts the bridge, which C-7 already forbids reusing as installation.

A three-way split (client and identity, then CLI, then bridge) was not
selected: `pchat` and identity resolution are too tightly bound for the middle
slice to stand alone. A sanitized verbatim copy followed by a separate
make-it-run slice was not selected: real account names must be resolved at
runtime in the first PR, and that PR must pass `build-test`.

### D-21. Capture the receipt experiment as is; PLT-15's review is its first review

Owner decision in this conversation, 2026-10-08: PLT-15 captures the
reviewer-start receipt experiment unchanged, so the captured bridge matches the
running one (D-19). No earlier review of it was located, so PLT-15's PR review
is its first review and must cover it as new code, not as already-reviewed
capture. Whether it stays switched on is the owner's operational choice,
outside the slice; capture neither enables nor disables it.

Removing it from PLT-15 was not selected: the bridge imports it, so the
captured bridge would differ from the running one. Deciding to drop it before
the baseline commit was not selected.

### D-22. PLT-9 and PLT-15 entries approved as written

Owner signoff in this conversation, 2026-10-08: the PLT-9 and PLT-15 delivery
entries (outcome, scope, dependencies, acceptance signals and out of scope),
as recorded under D-19/D-20/D-21, are approved. This settles Q-11 for these two
slices only. It is not readiness signoff for the design, and neither slice can
be processed until the D-19 baseline commit exists.

### D-23. Activate the captured code before restructuring it into the API

Owner decision in this conversation, 2026-10-08: reorder the release work ahead
of the API: PLT-9 → PLT-15 → PLT-11 (build) → PLT-12 (stage) → PLT-14
(activate) → PLT-10 (API). The first activation runs captured code that behaves
as today's live tools. From that activation on, plateia is the single source
of the shared chat code, and PLT-10 restructures it there alone.

Until that activation, the local skills repository remains what runs and may
still change. Each slice from PLT-9 to PLT-14 starts with a drift check
against the D-19 baseline and carries any newer skills commits into plateia
before its own change. D-15 is unchanged: pinned, staged and guarded releases
with no installation exception, and each real activation still needs its own
explicit owner authorization. The release manifest gains an API version only
from PLT-10 onward.

Fixing both copies by hand from PLT-9 onward was not selected (fixes get
missed), nor was keeping the skills repository authoritative until a later
activation (four slices of growing divergence, harder after the API refactor).
A shortcut activation after PLT-15, such as repointing symlinks, was not
selected because it would reopen D-15.

### D-24. PLT-15 also captures the chat skill's operating guidance

Owner decision in this conversation, 2026-10-08: amend the approved PLT-15
entry (D-22) to capture `chat/SKILL.md` from the D-19 baseline with the same
provenance and drift check, so PLT-11 can ship code with its guidance as C-7
requires. It is executed by agents, so it lands through PLT-15's PR, not
`docs-push`, and it is sanitized like the code: no real account names,
private paths or chat content.

Capturing it in PLT-9 was not selected because it describes the bridge, which
arrives in PLT-15. Capturing it in PLT-11 was not selected because that would
make the build slice copy source outside the capture slices' drift checks.

### D-25. PLT-11 approved; the first release carries shared chat only

Owner signoff in this conversation, 2026-10-08: the PLT-11 entry as presented
is approved. The first release contains only the shared chat code captured by
PLT-9 and PLT-15 with its `SKILL.md` guidance. The chat code needs only the
Python standard library (plus the `weechat` module WeeChat itself provides to
the color script), so its pinned dependencies amount to the interpreter. The
inbox core already on `master` joins a release when PLT-13 gives it a live
adapter. Packaging the inbox core in the first release was not selected: it
would ship a package nothing runs yet.

### D-26. PLT-12 approved; the chat import location is a managed target

Owner decision and signoff in this conversation, 2026-10-08: the PLT-12 entry
is approved with an explicit managed-target list: the `pchat` symlink, the
bridge and log-rotation LaunchAgent program paths, the WeeChat color-script
symlinks, the chat skill folder (read by both Claude and Codex), and the
`chat/scripts` import location itself. At activation (PLT-14) that location
becomes a thin layer handing off to the selected release, so the launch hook
and the `project-manager` scripts that import chat modules by path load the
release's modules without being edited. Staging only records and checks these
targets; it replaces nothing.

Editing each path-bound importer to find the release was not selected: it is
the broader skill migration D-17 deferred, and adds drift-protected files.
Leaving those importers on the old tree was rejected: two identity
implementations would write one registry, the split D-23 exists to prevent.

### D-27. PLT-14 approved; first rollback restores the skills tree, and the owner commits the handoff

Owner signoff in this conversation, 2026-10-08: the PLT-14 entry is approved
with two rules for the first activation. First, its rollback target is the
pre-activation `~/.codex/skills` content of the D-26 targets, recorded by hash
in PLT-12's inventory and backed up privately; rollback restores exactly those
targets, and they are kept until a later plateia-to-plateia rollback has been
demonstrated. State formats are identical because the captured code behaves
the same. Second, the controller reports the exact expected change to the
skills repository's working tree (the `chat/scripts` handoff layer); the owner
commits it there afterwards, and the drift check treats it as expected. Plateia
tooling never commits into another repository.

Fix-forward only for the first activation was not selected: it would drop
C-7's rollback guarantee at the riskiest moment. Having the controller commit
into the skills repository was not selected.

### D-28. Split the API work: behavior-preserving API first, attested evidence second

Owner decision in this conversation, 2026-10-08: PLT-10 introduces the
versioned shared chat API over today's behavior only; the CLI, bridge and
`pchat agent` entry points go through it with unchanged output and exit codes.
A new PLT-16 then adds the normalized, server-attested evidence that the
merged inbox core's adapter contract needs (read messages, read
acknowledgements, send with submitted, proven-no-send or uncertain results).
The framing shared between the inbox envelope and `card-move/v1` stays with
Q-14 and PLT-1; until then the inbox keeps its own envelope.

Keeping PLT-10 as one slice was not selected: it would mix a pure restructure
with new behavior, hiding behavior changes from review. Splitting by consumer
was not selected: every slice would touch the shared evidence model.

### D-29. PLT-10 and PLT-16 entries approved as written

Owner signoff in this conversation, 2026-10-08: the PLT-10 and PLT-16 delivery
entries (outcome, scope, dependencies, acceptance signals and out of scope),
as recorded under D-28, are approved. This settles Q-11 for these two slices.

### D-30. PLT-13 approved; the inbox keeps its own envelope

Owner signoff in this conversation, 2026-10-08: the PLT-13 entry is approved.
The adapter builds on PLT-16's evidence API and the inbox's existing envelope
(D-28), so Q-14 no longer blocks it. The inbox core and adapter join the next
release (D-25); installing or scheduling the inbox needs its own owner
authorization.

### D-31. Every card move is posted in the card's issue request channel

Owner decision in this conversation, 2026-10-08: every move for an issue
card, first and later, is posted in that issue's request channel
(`<prefix>issue-<n>`, following today's chat convention), which plateia opens
through chat if it doesn't exist yet. That room holds the card's whole record:
moves, manager acceptance, worker progress, review, merge and cleanup. Today's
bridge already wakes the manager for posts there, so no delivery change is
needed. This settles the channel-routing part of Q-14 for a card whose issue
has its own channel; grouped request channels and PR-only cards remain open.

Posting every move in the project's main channel was not selected: the card's
history would be split from its work. First move in the main channel and later
moves in the issue channel was not selected: two routing rules, and finding a
card's history would depend on knowing it.

### D-32. Grouped issues still get moves in their own card channel

Owner decision in this conversation, 2026-10-08: when the manager handles
several issues in one grouped request channel, a move for one of those cards
still goes in that card's own `<prefix>issue-<n>` channel, opened if needed.
The route never depends on history, so a retried move lands in the same room.
The manager, woken by the post, associates it with the grouped work by card
key, and posts a one-line pointer in each card's own room linking to the
grouped channel. Hold is enforced through shared control (D-4, D-10), not by
the worker reading a room. The manager pointer is focused manager guidance
for PLT-3.

Routing to whichever channel currently holds the work was not selected: the
route would be inferred from history, could change between a move and its
retry, and would block on ambiguity. Forbidding the manager to group
board-moved cards was not selected: it constrains scheduling that V-7 leaves
to the manager and doesn't cover earlier grouping.

### D-33. One request per card move; retries keep the request ID

Owner decision in this conversation, 2026-10-08: each card move is its own
request with its own request ID, allocated by the shared submission service; a
retry of the same move (same operation ID) reuses the original request ID.
Each move gets its own manager acceptance and its own terminal disposition; a
move overtaken by a later one is closed as superseded, so acceptance reminders
cannot keep obsolete work alive. The operation ID stays the client's
idempotency key for recovering a lost response (D-11). A card's whole history
is read from its room (D-31) or a trace by card key, which PLT-1 adds.

One long-lived request per card was not selected: its acceptance and
disposition would cover several different instructions, and superseded moves
would have nothing separate to close. One request per active stretch (Solve to
Done) was not selected: it needs a stretch-boundary rule and keeps the same
problem inside each stretch.

### D-34. Only the shared submission service makes card moves

Owner decision in this conversation, 2026-10-08: a card move exists only when
submitted through the shared submission service, from plateia or a terminal
command such as `pchat card move`, which assigns its revision, operation ID
and receipt. A line typed directly into IRC that looks like a `card-move/v1`
command is not a command: the bridge flags it visibly in the room as not
submitted, pointing to the board or the terminal command, and nothing is
dispatched. Plain-language requests to the manager keep working exactly as
today, as ordinary requests rather than card moves.

Accepting hand-typed moves and assigning them a revision on sight was not
selected: with no operation ID a re-typed line can't be told from a new move,
and late revision assignment competes with D-11's commit order. Ignoring such
lines silently was rejected: an intended move would vanish, against V-12.

### D-35. No endpoint field: Solve always means D-6's path

Owner decision in this conversation, 2026-10-08: `card-move/v1` carries no
endpoint field. A move's fields are its operation, card, revision and
destination; `to=Solve` always means D-6's path (solve, review, merge only if
the merge worker is already running, cleanup) and can never request starting a
stopped drainer. Any future Solve variant is a new, deliberate format version.

An endpoint field with a single legal value was not selected: parsing and
validation cost, and a way to send a bad value, for no current benefit. A
Solve variant ending at an open PR was not selected: a new owner-facing choice
the vision doesn't ask for.

### D-36. Move request states, with superseded marked by the shared service

Owner decision in this conversation, 2026-10-08: each move's request shows one
of these states in its room and on the card.

| State | Terminal | Meaning | Recorded by |
| --- | --- | --- | --- |
| accepted | no | the manager took responsibility for this move | manager, via today's exact-message reply/acknowledgement |
| blocked | no | owner input needed or a failure stopped it; carries a reason; notifies under V-9 | manager |
| refused | yes | the manager declined it, with a reason | manager |
| superseded | yes | a newer move for the same card was submitted | shared submission service, when it allocates the newer revision |
| satisfied | yes | the card verifiably reached what was asked (Solve: Done; Hold: verified safe pause and inhibition) | shared tooling, from evidence (PLT-6) |
| overtaken | yes | reality got there first, such as a Hold after a confirmed merge (D-13) | shared tooling, from evidence (PLT-6) |

Superseded is a fact of revision order, settled when the newer move is
numbered, even if the manager is down; reminders for the older move stop then.
Superseding doesn't itself stop work: what happens to work an older move
started is the manager's call, and a newer Hold acts through shared control.
Having the manager mark superseded during reconciliation was not selected:
superseded moves would stay open, and keep reminding, until the manager acts.

### D-37. Card-move mechanics fixed as constraints; spelling left to PLT-1 review

Owner decision in this conversation, 2026-10-08: these constraints bind
`card-move/v1`; exact spelling is settled in PLT-1's reviewed implementation.

1. **IDs:** clients generate random operation IDs (for example a UUID); the
   shared service allocates request IDs in today's `<project>-<yyyymmdd>-<n>`
   style; both use today's request-ID character set `[a-z0-9-]`.
2. **No free text in a move:** every field is a restricted token, so nothing
   needs escaping; anything else is a separate ordinary post.
3. **Size:** a move is one line, far under the server's 4096-byte limit; a
   move that would need splitting is refused before sending, never sent in
   parts.
4. **Duplicates:** the same operation ID with the same content returns the
   original receipt; with different content it is visibly rejected. Clients
   never send a revision; the service assigns it, so submissions cannot carry a
   conflicting revision.
5. **Lookup and retention:** receipts are queryable by operation ID, request ID
   or card key through the PLT-16 API, and kept as long as the chat record,
   which V-2 says is never deleted.

With D-31 to D-36 this resolves Q-14 for issue cards. Whether the browser
warns about near-simultaneous moves from two devices is Q-6; PR-only cards
stay with Q-7.

### D-38. Split card-move submission from its terminal and record surface

Owner decision in this conversation, 2026-10-08: PLT-1 carries the durable
submission service, receipt store and posting into the card's room; a new
PLT-17, after it and off the critical path, carries `pchat card move`, trace
by card key and the bridge's flag for hand-typed moves. Keeping both in one PR
was not selected: it would mix the durability core with user-facing commands
in one review.

### D-39. PLT-1 and PLT-17 entries approved as written

Owner signoff in this conversation, 2026-10-08: the PLT-1 and PLT-17 delivery
entries, as recorded under D-38, are approved. This settles Q-11 for these two
slices.

### D-40. Plateia ships manager and worker mechanisms; the owner applies skill changes

Owner decision in this conversation, 2026-10-08: for PLT-3 and PLT-4, plateia
ships the shared mechanism (card control, reconcile and pause/resume commands
and API) through the normal release path. Each PR is accompanied by a written,
reviewed specification of the required manager or worker skill change, with no
private content. After activation, the owner, or a session the owner chooses,
applies that change in the local skills repository, consistent with D-27:
plateia tooling never commits there.

Capturing the manager skill into plateia first was not selected: it would
reopen D-17 and delay every card slice. Capturing only the manager guidance
was rejected: one skill would come from two sources, the drift D-23 removed.

### D-41. PLT-3 entry approved as written

Owner signoff in this conversation, 2026-10-08: the PLT-3 delivery entry, as
rewritten under D-40, is approved. This settles Q-11 for PLT-3.

## Open questions

### Q-1. Where is the durable handoff, and what is its command identity?

**Resolved by D-3** for storage and independent durable delivery, and D-8
for logical identity/ordering direction. Exact protocol details are Q-14;
commit direction is D-11 and exact recovery details are Q-14. The shared atomic
store was not selected.

### Q-2. How should Hold be enforced across the entire pipeline?

**Resolved by D-4** for shared, whole-pipeline Hold and D-10 for safe-step and
unreadable-control behavior; late merge/cleanup
outcomes remain in Q-7. The solver-only alternative was rejected.

### Q-3. What is the first one-PR delivery boundary?

**Resolved by D-5** for the prerequisite-first split. The proposed child
boundaries below still require approval (Q-11). D-7 settles new code ownership;
Q-12 settles source/update and eventual cutover compatibility. No backend epic or tracker
mutation is authorized by this decision.

### Q-4. Which page framework wins the V-12 bake-off?

Deliberately open for PLT-2, after the lifecycle and recovery contracts settle.
Choose candidates later, build the same small prototype in each, and compare
with the owner. The prototype should exercise a card move, receipt recovery,
stale state, automatic reconnect, visible failures and phone/desktop use.
Keep it synthetic and disposable; do not choose a framework in PLT-1 or turn
a temporary prototype choice into the production decision.

### Q-5. What endpoint does a Solve move authorize?

**Resolved by D-6:** Solve authorizes review, merge and cleanup, but only an
already running drainer merges automatically. A separate explicit owner start
request is required if the drainer is stopped. The prior manager behavior
must change; merge authority alone does not grant startup authority.

### Q-6. What are the exact browser and freshness contracts?

Unsent moves during disconnection, concurrent phone/desktop edits, observation
age thresholds, conflict presentation, failure dismissal/resolution and
snapshot/change replay need owner choices. An acknowledged request survives;
an unsent gesture is not acknowledged. D-3 establishes the durable direction;
settle these details before a production browser slice.

### Q-7. How are completion and late controls resolved?

Define reliable cleanup coverage and claim-release evidence, when V-7's
closed-issue fallback is applicable, and Hold arriving during cleanup. Also
settle PR-only completion, replacement/multiple PR associations and reopened
issues before delivering those cases. Temporary feed outages cannot become
proof of completion. The walk's known cleanup-debt case requires visibility.

**Late-Hold part resolved by D-13:** show a Hold after confirmed merge as
overtaken and continue required cleanup. D-10's unreadable-control rule still
blocks the next managed action until control is readable. Completion coverage
and the other association/fallback cases above remain open.

### Q-8. Where should the shared code and its changes be owned?

**Resolved by D-7:** all new code is owned by `coghex/plateia`, and shared
workflow packages run independently of its web process. The proposal to keep
new drainer changes in kanban was superseded by the owner's “all new code”
extension. A separate shared-services repository was not selected.

Q-12 records the accepted source/update direction and deployment prerequisites;
D-9 supersedes porting the existing drainer. Do not use old source paths as
tracker-routing authority.

### Q-9. What identifies and orders a card move in chat?

**Resolved by D-8** for readable `card-move/v1` commands, immutable logical
move IDs and shared per-card revision sequencing. Exact wire details are
Q-14; cross-boundary commit direction is D-11. No approval of a final parser
or database schema is implied.

### Q-10. What is a safe point, and what happens if control state is unknown?

**Resolved by D-10** for finish-current-action/checkpoint/check-before-next,
and no new managed action/merge while control is unreadable. No owner action
is blocked. Concrete coordinator/subprocess boundaries still need source
evidence under Q-11; cmux inbox delivery alone is not a checkpoint. Compatibility
for existing unmanaged work and manager recovery belongs in Q-12. Late
post-merge Hold/cleanup remains Q-7; merger runtime/gate details are Q-15/Q-16.

### Q-11. Are the proposed child boundaries and handoff details accepted?

D-5 approves the split direction, not every new child below. D-7 and D-8
settle ownership and core protocol direction. Approve the proposed boundaries
with Q-12's distribution/API/migration choices now resolved and D-18's
equivalence boundary accepted. Q-16's remaining gates and Q-14's wire contract
still block their affected slices. Verify each
fits one issue, one worktree and
one PR in its own repository. If a slice crosses source homes, split it or
make an explicit external prerequisite instead of treating multiple PRs as
one child. Finalize board/shared commit recovery, projection transport and
evidence freshness before the affected implementation slices are processed.

**Partly settled, 2026-10-08:** D-19 sets PLT-9's capture baseline and D-20
splits it into PLT-9 (core) and PLT-15 (bridge), excluding `install-identities`.
D-21 keeps the receipt experiment in PLT-15 as is. **Q-11 is resolved for
PLT-9 and PLT-15 (D-22, amended by D-24), PLT-11 (D-25), PLT-12 (D-26) and
PLT-14 (D-27), PLT-10 and PLT-16 (D-28, D-29), PLT-13 (D-30), and PLT-1 and
PLT-17 (D-38, D-39), and PLT-3 (D-40, D-41)**, in D-23's order. PLT-4 to
PLT-7, PLT-2 and PLT-8 remain
unsigned, as does the first browser boundary below.

The first browser slice is proposed to cover the specified existing-approved
issue's Solve/Hold/resume path and actual status progression. Other mid-term
features remain later delivery design. The owner must confirm that first
user-visible boundary rather than having it inferred from this proposal.

### Q-12. How do existing services and local skills move to the new source home?

**Drainer-port proposal rejected by D-9:** start fresh from finalize instead
of moving the old script. Do not insert a behavior-preserving drainer port as
a prerequisite. D-12 settles the new runtime; Q-16 retains approval/gate details.

**Distribution direction resolved by D-15, 2026-10-02 16:40:09 UTC:** pinned
plateia packages in a private Python environment, thin CLI/skill entry points
and one shared chat API. Editable deployment and private patched copies are
rejected. Do not reopen source ownership or ask for an installation exception.
C-7/P-6 gives the concrete proposed preservation/upgrade/rollback contract.

**API boundary and initial scope resolved by D-16/D-17,
2026-10-02 17:10:18 UTC:** versioned Python API inside the existing bridge/client
processes, no new initial IPC daemon; focused shared-chat/inbox migration and
necessary compatibility/identity hooks, with broader manager/worker skill
migration later. The dedicated initial IPC service and immediate full skill
migration alternatives were not selected. Do not re-ask these choices.

Remaining deployment prerequisites are concrete work, not another choice of
distribution route:

- Reconcile a current source inventory with active owner fixes and local
  modifications; review the relevant source/hook changes without overwriting
  installed files or duplicating existing tracker work.
- Implement the shared API/codec and real adapter with full provenance,
  ordered complete history, multipart and acknowledgement evidence; resolve
  Q-14 where it affects framing/routing. Build on the merged inbox core on
  `master`; don't fork or duplicate it.
- Build verified pinned artifacts and a declared interpreter/API/state/skill
  compatibility matrix; test modified-file refusals, crash recovery, referenced
  release retention and code-only rollback against latest state.
- Approve the affected one-PR boundaries under Q-11 and land code with its
  required contracts/evidence through normal review. This document alone is
  not a release artifact, an installation plan approval or issue readiness.
- Before any real installation/selection/service restart/schedule, obtain the
  separate owner authorization and concrete compatible plan. Such actions
  remain held; a merger cutover additionally preserves outstanding cleanup
  and requires an explicit start request. No exception is proposed.

The implementation details (package/build names, private release-directory
spelling, manifest serialization) need review and tests, not repeated owner
choices of the already selected route. No live state migration or activation
is authorized. A breaking state change, force-replacement of a modified file,
service cutover or activation/scheduling request would be a later separate
owner checkpoint, not a pending permission request here. Preserve a single
active merge authority and outstanding cleanup obligations before any eventual
cutover. PLT-9, PLT-15 and PLT-10 through PLT-14 are proposed reviewable boundaries subject to
Q-11, not permission to implement, deploy or merge them now.

### Q-13. What is the board-to-shared commit and recovery sequence?

**Resolved by D-11:** shared submission/receipt commit first, board intent plus
receipt in one SQLite commit second, acknowledge last. A crash between commits
recovers the board from the same shared receipt/history. SQLite-first staging
was not selected. Exact lookup/retention/reconciliation API remains Q-14;
browser storage of the original move ID remains Q-6.

### Q-14. What is the exact wire and receipt schema?

**Resolved for issue cards by D-31 to D-37, 2026-10-08.** Remaining: PR-only
card routing (with Q-7) and multi-device move warnings (Q-6). The history
below is kept.

After D-8's envelope approval, settle operation-ID/request-ID generation,
escaping and size bounds, destination/endpoint fields, channel routing for
first/subsequent moves, authenticated validation, exact acceptance/outcome
events, revision-conflict and duplicate responses, snapshot/history lookup and
compatibility with human pchat posts. Solve must encode D-6's no-implicit-start
rule. Interrupted/oversized multi-line commands must not dispatch fragments.
No wire parser or schema is approved merely by the example in C-2.

**Channel routing partly resolved by D-31, 2026-10-08:** every move for an
issue card goes in that issue's request channel; D-32 keeps that rule when
the manager has grouped issues. **Request scope resolved by D-33:** one
request per move, reused on retry. **Human-typed compatibility resolved by
D-34:** only the submission service makes moves; a hand-typed envelope is
flagged, never dispatched. **Endpoint resolved by D-35:** no endpoint field;
Solve means D-6's path. **Acceptance and outcome states resolved by D-36.**
Still open: PR-only cards (with Q-7), ID formats, escaping and size bounds,
revision-conflict and duplicate responses, and snapshot/history lookup.

### Q-15. Who runs the fresh finalization step, and when?

**Resolved by D-12:** an independent shared worker, explicitly started through
chat, runs fresh single-PR finalization for eligible authorized PRs in number
order until stopped. The manager-per-merge agent alternative was not selected.
The manual skill's current invocation rule is evidence, not the new worker's
authorization; implement the approved shared primitive, not implicit calls to
the manual fallback. Gate details are still Q-16 and child boundaries Q-11.

### Q-16. Which finalize gates and merge semantics carry into the fresh implementation?

Recommendation: retain finalize's fail-closed, current opposite-brand approval
gate (with authenticated publisher and configured approval mode), require every
reported check to pass or be skipped, require a ready mergeable PR targeting
the resolved default branch, rerun mutable gates immediately before merging,
and merge the exact reviewed head with a merge commit. Unknown/unreadable
inputs refuse. The legacy primitive uses administrator merge permission and
head matching; it cannot atomically bind a concurrently retargeted base.

Approve the starting gate/merge contract separately from the operating loop.
Do not silently copy the old drainer's narrower required-check selection,
automatic CI reruns/base repair, merge-past-base exception or scheduling
policy. V-12 also requires durable ambiguous-result reconciliation, visible
failures and restartable cleanup absent from the manual step. Exact API,
residual-race handling and test families need concrete design before readiness.

**Owner clarification, 2026-10-02:** finalization should obtain fresh
opposite-brand approval only if approval was stripped during a fast-forward/
merge update, and should handle the no-conflict case automatically. The owner
did not approve the earlier bundled gate recommendation. Preserve this intent
without interpreting it as permission to manufacture an approval label.

**Verified distinction:** the current stale-approval policy preserves approval
only for updates touching none of the PR-owned files, not every conflict-free
merge. It can strip approval after a clean update with overlapping reviewed
files. Current finalize never obtains a review; it refuses stale approval.
The drainer's separate recovery may retain the previous review under exact
policy evidence or wait for a canonical re-review; it does not equate absence
of conflicts with an opposite-brand verdict.

**Direction resolved by D-14, 2026-10-02 16:03:41 UTC:** carry previous reviewer
judgment under proven equivalence. A new reviewer run for every stripped
approval was not selected. C-6/P-5 defines the concrete rule, including a
distinct canonical carry receipt rather than a fresh-review claim.

**Boundary resolved by D-18, 2026-10-02 17:10:18 UTC:** metadata-only imported
base drift on an explicit approved per-repository allowlist. Disjoint
substantive base changes do not qualify initially; changed substantive PR
contributions never qualify. Equivalence/provenance/spec/opposite-brand/current
head CI constraints are accepted. Exact receipt/validator mechanics and recovery
tests require reviewed implementation before PLT-5 is process-ready. A missing
allowlist leaves imported changes ineligible rather than broadening the rule.

Check selection, administrator merge
permission, exact approval attribution, base/head race treatment and automatic
branch-update/conflict handling remain unapproved details; the clarification
does not silently approve the rest of the prior gate bundle.

## Verification strategy

Use invented projects, accounts, messages and tracker fixtures. Required
evidence belongs in each implementation's own PR alongside code and contracts;
no real dashboard captures or data in public artifacts.

Demonstrate the normal lifecycle and its failures at the observable receipt,
chat, manager, worker, review, drainer and card boundaries. Inject process or
connection failure before and after each durable write/send/acceptance/merge
and cleanup result. A restart must recover the same logical move and existing
execution. Test separate and combined plateia, bridge and browser outages.

Exercise Solve → Hold → Solve before dispatch; replay older moves after newer
ones; duplicate wake-ups; ambiguous send success; lost HTTP acknowledgements;
manager replacement; vanished workers; owner-started work; pause during review
or just before merge; unknown tracker state; cleanup debt and reopened issues.
No duplicate claim, worktree, PR or execution may result from transport retry.
An overtaken Hold must be shown truthfully.

Prove shared queue/delivery, acceptance, safe pause/resume and execution work
with plateia stopped, using a non-web caller. Preserve existing pchat human
posts and canonical review/claim behavior. Verify GitHub reads are shared and
bounded rather than multiplied by browser/card count. Verify that no web
action writes tracker labels, starts a skill or touches a terminal directly.

Final acceptance commands and failure thresholds wait for approved mechanisms
and one-PR boundaries. This session has read evidence, not executed workflow
or outage tests.

For PLT-9, PLT-15 and PLT-10 through PLT-14, use isolated invented home/configuration/state trees
and fake transport/process/service boundaries. Verify source edits concurrent
with inventory/capture, unknown ownership, changed regular files and symlink
targets, cached incompatible consumers, missing referenced hooks, pinned
artifact tampering and interpreter mismatch. Staging must leave active files,
identities, private settings and services untouched. Exercise interruption
before/after stage, journal and binding commits and ambiguous selection.
Coexisting processes must obey the declared API/state matrix and fencing.
Rollback tests first create newer identities, requests, accepted messages,
claims and cleanup debt, then prove all survive selection of older compatible
code. Unsupported latest-state rollback blocks with visible repair guidance;
it never rewinds data. Test all retained release references before retirement.
The inbox adapter uses the merged core on `master` and synthetic complete/missing/
conflicting evidence; it never tests against real chat or schedules a poll.

## Delivery plan

The order below is dependency-valid and follows D-5. The proposed child
boundaries are deliberately narrower than the full card walk. They remain
subject to Q-11 signoff; no entry is ready merely because it has this shape.

### PLT-9. Capture the shared chat identity and transport core

- **Outcome:** reviewed public source in plateia reproduces the live chat
  client, CLI and identity behavior, including the owner's current identity,
  color and run-evidence fixes, without replacing or changing installed files.
- **Scope:** capture from the D-19 baseline commit: `chatlib`, `identities`,
  `binding`, `runstore`, `role_colors`, `pchat` and `agentcli` (the `pchat
  agent` entry points the launch hook and kanban's reviewer runner call), the
  test-isolation guard and their tests. Resolve the owner, assistant and every
  other configured identity at runtime; no real names or credentials in source,
  tests or fixtures. Record provenance (baseline commit, per-file source
  hashes) and a migration map in the same PR; a drift check compares the live
  tree against the baseline and reports differences instead of overwriting
  them. Preserve existing command names, output and exit codes. Tests run in
  plateia's `build-test` with invented home/config/state trees.
- **Owning repository:** `coghex/plateia`.
- **Phase:** source preservation prerequisite.
- **Depends on:** none in plateia. External prerequisite (D-19): the live
  shared-chat tree's reviewed state is committed in its own local repository,
  and that commit is named as the capture baseline. **Met 2026-10-08:**
  baseline `c572bad` on the skills repository's `master`. The 2026-10-08 inventory
  above identifies the fixes and their review state; coordinate any in-flight
  identity/reviewer work rather than duplicating it. Other repositories are
  evidence sources, not implicit owners of new code or a hidden second PR.
- **Ordering:** critical path; can land first.
- **Relevant decisions:** D-2, D-7, D-9, D-15, D-16, D-17, D-19, D-20.
- **Acceptance signals:** fixtures preserve permanent roles and resume
  bindings, never-reused counters, empty-inventory refusal, prefix-only role
  colors, cited-delegation provenance and silent child-run refusal; a fixture
  with a terminal-less daemon beside a live agent registers no daemon identity
  and keeps the live one (the live-session fix). The isolation guard fails
  closed if a test would touch real state. Drift between the live tree and the
  baseline is reported visibly. No private content enters the repository; no
  installed file, registry or service is changed.
- **Out of scope:** the bridge (PLT-15); `install-identities`; installation or
  repointing any symlink, LaunchAgent or hook; new accounts; manager
  scheduling and `project-manager` changes (D-17); new Hold behavior; an
  old-drainer port.
- **Open questions:** None; boundary approved by D-22 (PLT-15 amended by
  D-24). The D-19 baseline exists. **Stop before processing** until the design
  is ready.

### PLT-15. Capture the shared chat bridge

- **Outcome:** reviewed public source in plateia reproduces the live chat
  bridge's delivery, acceptance and recovery behavior on top of PLT-9's core,
  without changing the running bridge.
- **Scope:** capture `chat-bridge`, the reviewer-start receipt experiment and
  `rotate-logs` from the same D-19 baseline, with the bridge, receipt and
  log-rotation tests, provenance and drift check. The receipt experiment keeps
  its opt-in marker semantics; capture doesn't change whether it is enabled.
  It has no located prior review, so this PR's review is its first (D-21).
  Also capture the chat skill's `SKILL.md` guidance, sanitized, under the same
  provenance and drift check (D-24).
  Tests use invented state and the PLT-9 isolation guard.
- **Owning repository:** `coghex/plateia`.
- **Phase:** source preservation prerequisite.
- **Depends on:** PLT-9.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-7, D-15, D-16, D-17, D-19, D-20, D-21, D-24.
- **Acceptance signals:** fixtures preserve checkpointed and resumed catch-up,
  crash replay, message-ID deduplication after acceptance recovery, queued
  versus failed delivery, busy-session handling, manager retargeting, dead
  letters and alerts, and bounded log rotation; the receipt experiment is inert
  without its marker. The captured guidance matches the captured commands and
  contains no private content. Drift is reported visibly; the running bridge,
  its LaunchAgent and its state are untouched.
- **Out of scope:** restarting or repointing the bridge; the shared API
  refactor (PLT-10); card-move framing (Q-14); `install-identities`.
- **Open questions:** None; boundary approved by D-22 (PLT-15 amended by
  D-24). The D-19 baseline exists. **Stop before processing** until the design
  is ready.

### PLT-11. Build immutable pinned shared-package releases

- **Outcome:** a clean reviewed commit produces identifiable package/skill
  artifacts with exact dependency and compatibility metadata.
- **Scope:** source/artifact hashes, Python/platform requirements, pinned
  dependencies, wire/state versions and packaged guidance; the API version
  joins the manifest from PLT-10 onward (D-23). Test built
  artifacts outside the source checkout. The first release carries only the
  shared chat code and its `SKILL.md` guidance (D-25); the inbox core on
  `master` joins a release with PLT-13. The chat code is standard-library
  only, so pinning means the interpreter. No duplicate core or editable
  runtime imports.
- **Owning repository:** `coghex/plateia`.
- **Phase:** release prerequisite.
- **Depends on:** PLT-15 (D-23). The inbox core (PR #1/issue #2) is already
  merged.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-7, D-15, D-16, D-17, D-23, D-25.
- **Acceptance signals:** missing/tampered/unpinned inputs refuse; manifest
  identifies exact source/artifacts and supported versions; artifacts contain
  no private settings/data; declared environments run without checkout imports;
  guidance and executable/API compatibility agree. Record build evidence and
  contracts in this PR before final review.
- **Out of scope:** artifact publication, real installation, active selection
  or changing existing caches.
- **Open questions:** None; boundary approved by D-25. Build names and
  manifest spelling are reviewed implementation details. **Stop before
  processing** until the design is ready.

### PLT-12. Stage releases without changing active tools or services

- **Outcome:** a verified private environment can be staged without selecting
  it or changing active tools, configuration, services or schedules.
- **Scope:** manifest/environment validation, API/state/interpreter preflight,
  managed-target ownership/hash inventory, durable stage journal and interrupted
  stage reconciliation. Exercise isolated invented home/state fixtures; include
  operator contracts and crash evidence in this PR. Installer execution on the
  owner's machine remains separately held. Managed targets for the first
  release (D-26): the `pchat` symlink, the bridge and log-rotation LaunchAgent
  program paths, the WeeChat color-script symlinks, the chat skill folder and
  the `chat/scripts` import location used by the launch hook and
  `project-manager` scripts.
- **Owning repository:** `coghex/plateia`.
- **Phase:** release preparation prerequisite.
- **Depends on:** PLT-11.
- **Ordering:** critical path for deployment; independent of offline card work.
- **Relevant decisions:** D-2, D-6, D-7, D-15, D-16, D-17, D-23, D-26.
- **Acceptance signals:** staging preserves active/modified files, identities,
  private settings, queues and service state; unknown ownership, mismatched
  interpreter, incompatible state and tampered artifacts block. Repeated or
  interrupted staging reconciles the same operation; no partial environment
  is activated and no identity rollout helper is called. The plan lists every
  D-26 target, including path-bound importers, and a fixture with an importer
  outside the list is reported rather than silently left on the old code.
- **Out of scope:** selecting active code, service restart/start, schema or
  credential changes, account provisioning and live installation in this task.
- **Open questions:** None; boundary approved by D-26. A concrete compatible
  target plan is a later deployment prerequisite, not an installation
  exception. **Stop before processing** until the design is ready.

### PLT-14. Add guarded activation and state-preserving rollback

- **Outcome:** separately authorized release selection reconciles interruption
  and can revert compatible code while preserving latest private state/work.
- **Scope:** fenced upgrade journal, target hash/link recheck, durable atomic
  binding, staged/selected/running version evidence, safe affected-writer
  coordination, compatibility matrix and code-only rollback. Retain all versions
  referenced by live processes, cached skills/hooks, entry points or services.
  Include operator contract and synthetic interruption/rollback evidence in
  the same PR; implementing a controller never authorizes live execution.
  Its first target is the captured, behavior-identical code (D-23). The
  first activation's rollback target is the pre-activation skills-tree content
  of the D-26 targets, kept until a plateia-to-plateia rollback is shown; the
  controller reports the expected skills-repository change for the owner to
  commit, and never commits there itself (D-27).
- **Owning repository:** `coghex/plateia`.
- **Phase:** deployment/recovery preparation.
- **Depends on:** PLT-12 (D-23).
- **Ordering:** critical path for activation; independent of offline board work.
- **Relevant decisions:** D-2, D-6, D-7, D-10, D-12, D-15, D-16, D-17, D-23,
  D-26, D-27.
- **Acceptance signals:** unknown/modified installed files block replacement;
  concurrent upgraders cannot interleave; binding/journal crash outcomes
  reconcile reality. Latest identity counters, settings, requests, accepted
  messages, cursors, claims, Hold, reviews and cleanup debt survive rollback.
  Incompatible rollback exposes repair/roll-forward; referenced releases are
  not retired. No mid-action worker interruption or implicit account/config/
  service/merger/schedule action occurs. A fixture rollback from a first
  activation restores the recorded skills-tree targets exactly, and the
  controller's report names the expected skills-repository change.
- **Out of scope:** breaking migrations, real activation/deployment, automatic
  recovery authority, merger cutover/start and merge actions; committing in
  the skills repository.
- **Open questions:** None; boundary approved by D-27. Any live
  activation/recovery plan needs separate explicit authorization; none is
  requested or implied here. **Stop before processing** until the design is
  ready.

### PLT-10. Expose one shared chat API with compatible non-web entry points

- **Outcome:** the CLI, bridge and `pchat agent` entry points go through one
  versioned Python API, with today's behavior unchanged.
- **Scope:** the API module with version and capability checks over the
  existing posting, history, acknowledgement, identity and presence functions;
  `pchat`, `chat-bridge` and `agentcli` delegate to it. Every existing
  command's arguments, output and exit codes stay as they are (for example,
  `pchat` exit 3 still means a queued outbox entry). The API version joins the
  release manifest. Update the packaged `SKILL.md` guidance and add synthetic
  caller transcripts as compatibility fixtures. No new evidence semantics
  (PLT-16) and no shared message format (Q-14).
- **Owning repository:** `coghex/plateia`.
- **Phase:** shared API prerequisite, after the first activation (D-23); its
  changes reach the live tools only through the PLT-11/PLT-12/PLT-14 release
  path.
- **Depends on:** PLT-14 (D-23).
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-7, D-15, D-16, D-17, D-23, D-28.
- **Acceptance signals:** the PLT-9/PLT-15 fixtures and the caller transcripts
  pass unchanged through the API; an incompatible caller or API version fails
  visibly rather than being reinterpreted; the bridge and CLI run with the
  plateia web process stopped and no extra IPC daemon; no second identity
  allocator, router or chat-library copy exists.
- **Out of scope:** attested evidence records and uncertain-send results
  (PLT-16); shared inbox/card-move framing (Q-14, PLT-1); card revisions and
  receipts (PLT-1); release activation; new services.
- **Open questions:** None; boundary approved by D-29. **Stop before
  processing** until the design is ready.

### PLT-16. Add normalized, attested chat evidence to the shared API

- **Outcome:** a non-web consumer can read complete, server-attested chat
  evidence and send with an honest outcome, meeting the merged inbox core's
  adapter contract.
- **Scope:** read messages for a channel or prefix in server order with
  server-attested account, stable message ID, server time, target, complete
  tags, reply relationship and multipart part evidence; read server-attested
  acknowledgements; send one logical message (numbering its physical lines if
  split) with a submitted, proven-no-send or uncertain result and its proof.
  Missing, truncated, replaced or conflicting history and unavailable
  acknowledgements are explicit errors. Legacy records lacking attestation are
  unusable for automatic actions, not upgraded by guessing. Include contracts
  and synthetic fixtures in the same PR.
- **Owning repository:** `coghex/plateia`.
- **Phase:** shared evidence prerequisite.
- **Depends on:** PLT-10.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-7, D-15, D-16, D-17, D-23, D-28.
- **Acceptance signals:** no account is derived from nick patterns, colors,
  client tags or text; complete multipart evidence survives reads, and
  orphaned or interleaved parts block; a queued outbox entry never reads as
  proven no-send, and an uncertain send is never automatically resent; the
  inbox core's existing tests pass against this adapter surface with synthetic
  data; existing command behavior from PLT-10 is unchanged.
- **Out of scope:** the inbox adapter itself (PLT-13); shared framing for
  `card-move/v1` (Q-14, PLT-1); card revisions and durable receipts (PLT-1).
- **Open questions:** None; boundary approved by D-29. **Stop before
  processing** until the design is ready.

### PLT-13. Adapt the approved clarification inbox to shared chat

- **Outcome:** the merged inbox core uses one real adapter over PLT-16's
  evidence API, preserving its existing authority/evidence contract.
- **Scope:** adapter, offline integration fixtures and operating guidance;
  account/tag/part/cursor/ack evidence; exact submitted/proven no-send/uncertain
  mapping. Reuse its own existing envelope (D-28) and bounded
  claims/reconciliation; add no private script copies or substitute assistant
  identity. The inbox core and adapter join the next release (D-25).
- **Owning repository:** `coghex/plateia`.
- **Phase:** non-web consumer follow-up, separate from the already tracked core.
- **Depends on:** PLT-16 (D-28), PLT-11. The former external prerequisite, PR #1/issue
  #2's core, was met on 2026-10-04 (`ef86f17`); the adapter builds on that
  merged core without amending its contract.
- **Ordering:** independent of card-move implementation once the API exists.
- **Relevant decisions:** D-2, D-7, D-15, D-16, D-17, D-25, D-28, D-30;
  preserve issue #2's contract.
- **Acceptance signals:** synthetic questions collect durably once; only the
  configured manager's attested account introduces questions/accepts replies;
  quoted/inline mentions add no inbox recipients; replies stay with that
  manager and owner escalation is deliberate. Uncertain sends never auto-
  repost; partial/unavailable/conflicting evidence blocks visibly. Existing
  assistant identity and configuration remain intact.
- **Out of scope:** duplicate core, changing the merged core's contract, a
  shared format with `card-move/v1`, live install/schedule, new assistant
  credentials or expanded authority. Installing the inbox adds managed entry
  points and needs its own owner authorization, like any activation.
- **Open questions:** None; boundary approved by D-30. Q-14 no longer blocks
  this slice (D-28). **Stop before processing** until the design is ready.

### PLT-1. Add durable logical card-move submission to shared chat

- **Outcome:** a non-web caller can submit a logical card move through the
  shared API, recover its durable receipt and retry it without creating
  another logical request.
- **Scope:** the shared submission service and its receipt store: operation
  IDs, request allocation (one request per move, reused on retry; D-33),
  per-card revision numbering, duplicate handling, superseded marking and the
  D-36 request states, and lookup by operation, request or card key, under
  D-37's constraints. It posts each move as one `card-move/v1` line in the
  card's issue channel (D-31, D-32), opening it through chat if needed, with
  no endpoint field (D-35); only this service makes moves (D-34). Callers use
  the shared API. Include the contract and crash/replay evidence in this PR.
- **Owning repository:** `coghex/plateia` (D-7); shared runtime independent
  of the plateia web process.
- **Phase:** shared delivery prerequisite; first card-move implementation target.
- **Depends on:** PLT-16 (D-28), PLT-11.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-3, D-5, D-7, D-8, D-11, D-15, D-16, D-17, D-28,
  D-31, D-32, D-33, D-34, D-35, D-36, D-37, D-38.
- **Acceptance signals:** a synthetic move survives bridge or service restart
  and an uncertain send; a retry returns the same operation, request,
  revision and receipt; mismatched duplicates are visibly rejected and a move
  that would split is refused; a newer move marks the older one superseded
  with the manager down; queued, delivered and exact-message accepted stay
  distinguishable; everything works with the plateia web process stopped;
  rollback to a pre-PLT-1 release leaves the receipt store intact, never
  deleted or rewound.
- **Out of scope:** the terminal command, trace by card and hand-typed flag
  (PLT-17); manager execution changes; safe worker pause; drainer inhibition;
  plateia database and browser framework.
- **Open questions:** None; boundary approved by D-39. **Stop before
  processing** until the design is ready.

### PLT-17. Add card moves to the terminal and the record

- **Outcome:** the owner can move a card and follow its history from a
  terminal, and a hand-typed move can never be mistaken for a submitted one.
- **Scope:** the `pchat card move` command over PLT-1's API; `pchat trace` by
  card key; the bridge flags a hand-typed `card-move/v1` line in its room as
  not submitted, pointing to the board or the command, and dispatches nothing
  (D-34). Update the packaged `SKILL.md` guidance.
- **Owning repository:** `coghex/plateia`.
- **Phase:** shared delivery follow-up.
- **Depends on:** PLT-1.
- **Ordering:** not on the critical path; later card slices need only PLT-1.
- **Relevant decisions:** D-2, D-7, D-33, D-34, D-36, D-37, D-38.
- **Acceptance signals:** the command produces the same receipts as the API
  and its retries reuse them; the card trace shows every move and its state in
  order; a hand-typed move is flagged visibly and wakes no worker; existing
  `pchat` commands are unchanged.
- **Out of scope:** manager behavior, Hold control, the browser.
- **Open questions:** None; boundary approved by D-39. **Stop before
  processing** until the design is ready.

### PLT-3. Reconcile ordered card intent before manager dispatch

- **Outcome:** shared card control and a reconcile step let a manager adopt a
  card's latest intent without starting duplicate work, and Hold persists as
  control for later stages.
- **Scope:** a durable card-control store shared with non-web callers: each
  card's latest requested state and revision, Hold inhibition, and the link
  from a card to its claim, worktree, PR and session. A `pchat card reconcile`
  command and API call returns the latest intent, superseded moves (marked by
  PLT-1), existing work to adopt, and whether a new dispatch is allowed; the
  manager records accepted, blocked and refused through the API (D-36), and a
  helper posts the D-32 pointer for grouped work. Unreadable control reports
  unknown and allows no new dispatch (D-10). Manager scheduling and canonical
  claims and gates are unchanged. Per D-40, the PR is accompanied by a written
  specification of the manager-side change (when to reconcile, which states
  to record, the pointer rule), with no private content, landed with
  `docs-push` and linked from the PR before its final review; the owner
  applies the manager change in the skills repository after activation.
- **Owning repository:** `coghex/plateia` (D-7) for the mechanism; the
  manager skill stays in the local skills repository (D-17, D-40).
- **Phase:** shared orchestration prerequisite.
- **Depends on:** PLT-1.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-3, D-4, D-5, D-6, D-7, D-8, D-10, D-31, D-32,
  D-36 (manager records accepted, blocked and refused), D-40.
- **Acceptance signals:** with synthetic cards and invented work, repeated
  deliveries and a replaced manager adopt one execution; Solve, Hold, Solve
  accumulated before dispatch acts only on the latest intent; a Hold blocks
  new dispatch without releasing work or approvals; refused and never-accepted
  requests stay visible; unreadable control blocks dispatch and reads as
  unknown, not held; no move, resume or approval starts a stopped drainer; the
  specification names every manager step it changes.
- **Out of scope:** worker and review-loop safe pause (PLT-4), merge checks
  (PLT-5), committing in the skills repository, board order and the browser.
- **Open questions:** None; boundary approved by D-41; deployment follows
  C-7/Q-12. **Stop before processing** until the design is ready.

### PLT-4. Pause and resume workers and review loops at safe boundaries

- **Outcome:** the existing worker/review execution can report a verified
  safe pause and resume its kept work through shared control.
- **Scope:** integrate the approved checkpoint rule with solver and review
  loop boundaries, persist recovery context and correlated pause/resume
  evidence, and prevent the next stage while held. Retain branch, worktree,
  uncommitted work, PR, claim and review evidence; keep freshness gates.
- **Owning repository:** `coghex/plateia` (D-7) for the shared mechanism;
  worker and review-loop skill changes follow D-40: a written specification
  accompanies the PR, and the owner applies it in the skills repository.
- **Phase:** shared execution prerequisite.
- **Depends on:** PLT-3.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-4, D-5, D-7, D-10, D-40.
- **Acceptance signals:** Hold during an in-flight step yields pause pending
  until a safe checkpoint; no forced terminal edit/kill; replayed Hold does
  not lose work; newer Solve resumes the same execution/artifacts; a missing
  worker/checkpoint is unknown, not safely held; works without plateia.
- **Out of scope:** drainer eligibility, plateia projection and UI; general
  mid-step interruption remains outside the vision's scope.
- **Open questions:** Q-11; deployment follows C-7/Q-12. **Stop before processing.**

### PLT-5. Build shared finalization from the finalize contract

- **Outcome:** a fresh shared single-PR finalization path enforces the approved
  gate, respects Hold, and exposes verified merge and incomplete cleanup.
- **Scope:** start from finalize's repository/target/gate/merge-confirmation/
  identity-safe cleanup contract (D-9), with V-12 durability and read-only
  outcomes. Consume shared control before new actions and irreversible merge.
  The shared worker runtime is D-12; approval continuity and the metadata-only
  initial boundary are D-14/D-18. Exact validator and other gate details remain
  C-6/Q-16 prerequisites.
- **Owning repository:** `coghex/plateia` (D-7). No drainer-port prerequisite;
  existing drainer is evidence/current infrastructure only.
- **Phase:** shared finalization prerequisite after the manager's shared
  control contract. A primitive, restartable cleanup and running-worker layer
  may need separate approved slices; do not pack them into this child by default.
- **Depends on:** PLT-3; refine consumer dependencies if Q-11 splits this
  responsibility into separately reviewable outcomes.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-4, D-5, D-6, D-7, D-9, D-10, D-12, D-13, D-14,
  D-15, D-16, D-18.
- **Acceptance signals:** gate refusal mutates nothing; held/unknown-control
  PR never begins a new merge; merge is head-bound and verified before cleanup;
  ambiguous network results reconcile against actual tracker facts; outstanding
  cleanup survives restart and remains visible; an already committed merge
  reports overtaken Hold; a stopped merge worker stays stopped without an
  explicit owner start request; a readable Hold after confirmed merge is
  overtaken while required cleanup continues. Approval-recovery acceptance
  signals enforce C-6's accepted metadata-only provenance/content/freshness/CI
  constraints and fail-closed cases; exact validator and other merge gates
  await Q-16. Runtime tests follow D-12.
- **Out of scope:** porting the old drainer; web merging; implicit/manual-skill
  invocation authority; unrelated repair/rerun features; board order/UI.
- **Open questions:** Q-7, Q-11, Q-16; deployment follows C-7/Q-12.
  **Stop before processing;
  this is not yet a settled one-PR boundary.**

### PLT-6. Project the card's real lifecycle from shared evidence

- **Outcome:** a read-only consumer can distinguish accepted, working,
  pause pending/held, review, merge, cleanup and Done for one linked issue/PR.
- **Scope:** reconcile tracker, existing claims/session correlation, chat
  receipts, execution checkpoints and drainer observations; expose provenance,
  observation age, uncertainty, incomplete actions and overtaken intent.
  Centralize bounded tracker reads for consumers; do not build a second
  workflow engine or infer state from terminal prose.
- **Owning repository:** `coghex/plateia` (D-7). The workflow facts remain
  shared, independent of the web process; presentation consumes them.
  Projection API details remain P-3.
- **Phase:** lifecycle observation prerequisite.
- **Depends on:** PLT-1, PLT-3, PLT-4, PLT-5.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-4, D-5, D-6, D-7, D-9, D-12, D-13, D-36
  (satisfied and overtaken come from this projection's evidence).
- **Acceptance signals:** owner-started work is recognized; same issue/PR
  retains one identity; stale replay cannot overwrite newer evidence; an
  outage is unknown/stale rather than success; closed issue with known
  cleanup debt is not Done; issue reopening becomes active again; tracker
  traffic does not multiply with browser/card count; approved work with a
  intentionally stopped, incident-free drainer reports waiting rather than
  merging or failed; an actual incident remains visible alongside that wait.
- **Out of scope:** full agent-directory UI, unrelated PR association cases
  unless included by Q-7, browser push and page framework choice.
- **Open questions:** Q-6, Q-7, Q-11; deployment follows C-7/Q-12.
  **Stop before processing.**

### PLT-7. Persist board intent and reconcile shared receipts in plateia

- **Outcome:** the Python application persists board preferences in SQLite
  and submits/reconciles moves through shared chat without owning execution.
- **Scope:** board identity, requested basket/revision, owner order, receipt
  links and cached observed facts with age; durable cross-boundary recovery
  reconciling intent and shared receipt; read-only snapshot/change interface
  for the eventual page. Use synthetic API clients before framework choice.
- **Owning repository:** `coghex/plateia`.
- **Phase:** plateia backend prerequisite for comparative prototypes.
- **Depends on:** PLT-1, PLT-6.
- **Ordering:** critical path.
- **Relevant decisions:** D-2, D-3, D-5, D-7, D-8, D-11.
- **Acceptance signals:** acknowledge only with durable board intent and
  shared receipt, committed before the board's atomic intent/receipt commit;
  restart at each commit/handoff boundary recovers the same
  logical move; lost response can be queried/retried without new work; owner
  order persists; labels contain no board preferences; cached state is aged;
  runtime data stays outside Git; localhost binding; no skill/terminal/tracker
  mutation by the application.
- **Out of scope:** page framework, production browser controls, image
  storage and new workflow logic.
- **Open questions:** Q-6, Q-7, Q-11 (Q-14 resolved for issue cards by D-31
  to D-37). **Stop before processing.**

### PLT-2. Compare page frameworks with the same recovery prototype

- **Outcome:** one reviewable comparison artifact with reproducible synthetic
  prototypes, evidence and an explicit owner framework decision; candidate
  count and work boundary need confirmation before processing.
- **Scope:** identical lifecycle/recovery interactions on phone and desktop;
  compare mature open-source candidates against V-12, maintainability and the
  Python server boundary.
- **Owning repository:** `coghex/plateia`.
- **Phase:** later, after the shared lifecycle and plateia backend contracts.
- **Depends on:** PLT-7.
- **Ordering:** critical path for choosing the production page framework.
- **Relevant decisions:** D-2, D-5.
- **Acceptance signals:** comparable prototype results, restart/reconnect and
  stale/failure evidence, phone/desktop parity, owner signoff on the choice.
- **Out of scope:** a production framework decision before the comparison;
  unrelated workflow execution changes.
- **Open questions:** Q-4 deliberately left open until this later slice;
  Q-6 for the exact interactions used in the comparison; Q-11 for its
  one-PR scope. **Stop before processing until these are approved.**

### PLT-8. Deliver the first browser card lifecycle on phone and desktop

- **Outcome:** the owner can move the first issue card Solve → Hold → Solve
  and see real review, merge, cleanup and Done through the chosen page stack.
- **Scope:** display requested versus observed state, queued/delivered/accepted
  and unresolved failures, safe Hold/resume progress, issue/PR links, owner
  order, automatic connection recovery and receipt reconciliation. Minimal
  main/request-room context keeps every move traceable to chat.
- **Owning repository:** `coghex/plateia`.
- **Phase:** first production browser card slice.
- **Depends on:** PLT-2, PLT-7, PLT-12, PLT-14; these transitively require the
  shared lifecycle and release contracts. Eventual real installation/activation
  remains separately held.
- **Ordering:** critical path.
- **Relevant decisions:** D-1 through D-18; add the framework decision after
  the bake-off, before processing this child.
- **Acceptance signals:** the entire invented #41/#57 walk is demonstrated
  on phone and desktop, including separate/combined plateia, bridge and browser
  outages; acknowledged moves survive and retries start no duplicate work;
  pending/stale/unknown and overtaken states are explicit; known cleanup debt
  prevents Done; shared work continues while the web application is stopped;
  Solve/Hold/resume/approval with a stopped drainer leaves it stopped and the
  card waiting for merge until the owner explicitly asks for startup.
- **Out of scope:** proposed later deliveries for Approve/Inbox cancel,
  PR-only and complex linking, direct-address UI, full chat extras, push and
  setup; these remain required mid-term scope, not removed from the vision.
- **Open questions:** Q-6, Q-7, Q-11 and the framework decision from
  Q-4. **Stop before processing.**

## Handoff

Resume here after reading AGENTS.md and the accepted vision. D-3 through
D-18 record the accepted storage, Hold, split, endpoint/startup, source-home,
command-ordering, fresh-finalize, checkpoint, shared-first commit, running
shared worker, late-Hold cleanup, approval continuity/metadata-only boundary,
pinned private releases, existing-process Python API and focused migration.
Do not reopen them without new evidence or owner direction. The drainer-port
proposal is rejected; do not carry it forward as a prerequisite. D-14/D-18
accept carry-forward with the metadata-only initial boundary; D-15/D-16/D-17
settle Q-12's initial direction and scope. Do not re-ask them or infer approval
of unrelated merge-gate choices. Continue with the deployment prerequisites
listed under Q-12, plus Q-16's other gate details, Q-7 completion coverage,
Q-14 wire/routing details, Q-6 freshness/browser behavior and Q-11 concrete
one-PR boundaries in focused checkpoints. Only P-3 remains a proposal among
the original P-1 through P-4. P-5's direction/boundary is accepted; its exact
validator mechanics and P-6's build/controller details need implementation
review under the accepted constraints.
Preserve ongoing shared identity/color/workflow fixes and refresh the inbox
overlap before implementing adapters. PLT-9, PLT-15 and PLT-10 through PLT-14 have complete
candidate delivery contracts; none authorizes live use or is globally ready
for processing by itself. Keep ledger and delivery plan in identical dependency
order when boundaries change.

**2026-10-08 session:** D-19 to D-22 set PLT-9's committed capture baseline,
split it into PLT-9 (core) and PLT-15 (bridge) without `install-identities`,
keep the receipt experiment in PLT-15 for its first review, and approve both
entries. **The D-19 baseline exists:** at the owner's request on 2026-10-08,
the local skills repository's `master` was pointed at reviewed commit `c572bad`
(26 commits past `f010533`, history fetched from its task clone), with no file
on disk changed. The live tree then matched it except one uncommitted
`cmux-supervisor` SKILL.md paragraph, outside PLT-9/PLT-15 and left for the
owner. Re-run the drift comparison before processing PLT-9. D-23 then
reorders release work ahead of the API (PLT-15 → PLT-11 → PLT-12 → PLT-14 →
PLT-10). D-24 to D-27 then add the chat guidance to PLT-15 and approve
PLT-11, PLT-12 (with its managed-target list) and PLT-14 (with the first
activation's rollback and skills-repository commit rules). D-28/D-29 split
the API work into PLT-10 (behavior-preserving API) and PLT-16 (attested
evidence) and approve both; D-30 approves PLT-13. D-31 to D-37 resolve Q-14
for issue cards; D-38/D-39 split PLT-1 (adding PLT-17) and approve both. Next
for Q-11: PLT-3 onward and the first browser boundary.

The owner must explicitly approve readiness after material choices and slice
boundaries are settled. `process-design-doc` then processes the epic first
and exactly one child per invocation, with approval for each tracker artifact.
All new-code slices are plateia-owned under D-7. Existing kanban/local source
is migration evidence; no unapproved compatibility edit or service switch is
authorized. Do not file a new-code child in kanban merely because its current
implementation lives there. Keep a stopped drainer stopped absent an explicit
owner start request.
This document was first published at `e716052`. Edit it in the `docs-wip`
worktree and publish updates with `docs-push` only when the owner asks.
