# Collaboration foundations design

This design states how plateia could make requests between the owner,
assistants, managers and workers safe to repeat and accountable. It covers
what a logical request is, who may ask, when a request counts as accepted and
executed, how it comes back, how lost or repeated messages are handled, and
how every message a request costs is counted and capped. It is the design
half of #26, written from that issue's fixture and live-trial evidence. It
implements nothing.

**Owner decision, 2026-10-13: design only.** The owner authorized writing
this design from #26's findings. That decision approves the design work, not
the choices below: every `D-N` here except D-7 is a **proposal, not yet
ratified**, and
`Q-1` to `Q-7` are open. Implementation, tests, trials, and skill,
configuration or security changes are not authorized by it.

**Owner direction, 2026-10-13 (later the same day).**
- **Device-neutral authority.** The owner's instructions to an assistant
  carry the same authority from any client, phone or desktop. Use from a
  phone must not be treated any differently, and the owner is not asked to
  repeat routine approvals in IRC or to set up an IRC client.
- **Format and validation.** The owner agrees with having a request format
  and validation.

These two points are confirmed. The technical details below that implement
them are still proposals.

**Owner approval, 2026-10-13: COLLAB-1 only (#27).** One draft pull request,
never merged, for a library-only `request/v1` with a readable one-line form in
the `[request <id>]` convention. That settles Q-5 for this slice, without
settling `card-move/v1`'s final schema. Every other slice and question stays
open.

Design state: `exploring`

Status legend: `[ ]` unprocessed · `[#N]` linked to issue N · `[no-issue]`
reviewed and deliberately not tracked separately · `[deferred]` blocked on a
concrete precondition

## Processing status

- [#26] EPIC. Collaboration foundations (umbrella: #26; no new epic)
- [#27] COLLAB-1. Reconcile the generic request form with `card-move/v1`
- [ ] COLLAB-2. Record requests and claims durably in the bridge's authority
- [ ] COLLAB-3. Correlate results, stops and blockers, and bound the return
- [ ] COLLAB-4. Enforce message budgets with reserved slots
- [ ] COLLAB-5. Verify the foundations in one bounded, isolated live trial

## Epic contract

- **Goal:** a logical request between plateia's participants has one stable
  identity. It is verified against a scoped authority, accepted explicitly,
  claimed once by a component that enforces the claim, and answered within a
  bound. Every message it costs is reserved before it is sent.
- **Done when:**
  - a reposted or relayed request never causes a second claim or execution
    in any component that enforces the claim;
  - every accepted request ends in a durable result, stop or blocker, or a
    visible expiry;
  - no participant can send past its reserved budget.
- **Users and operators:** the owner and the owner's assistants, who ask;
  project managers and workers, who accept and execute; the operator who
  reads the record.
- **Arc label:** `None proposed`.

## Current state and evidence

Evidence: #26's fixture run
([comment 6152971887](https://github.com/coghex/plateia/issues/26#issuecomment-6152971887))
and two live trials
([6153643792](https://github.com/coghex/plateia/issues/26#issuecomment-6153643792),
[6153755894](https://github.com/coghex/plateia/issues/26#issuecomment-6153755894)).
The raw evidence is kept privately outside the repository.

Three kinds of behavior are kept apart here.

**Mechanisms that exist today** (shipped source on `master`):
- `chatlib.cited_authority` refuses an unknown, unverified, or
  non-owner-or-assistant citation.
- The bridge closes a wake only on the project manager's reply naming the
  message (`accepted` in `chat-bridge`); replays are idempotent.
- An unaccepted wake gets two reminders (`MAX_REMINDERS`, `ACCEPT_WAIT`
  900 s), then a dead letter and an owner alert. The bridge resends, never
  the sender.
- The bridge delivers a wake with `cmux agent message`. Live, an idle
  Claude Code session woke on it within 6 s.
- The clarification inbox (`packages/routine_inbox/`) routes with IRCv3
  client tags (`+plateia/to`, `+plateia/key`, `+plateia/part`,
  `+plateia/parts`).

**Approved designs that are not yet built** (`plateia_design.md`):
- **`card-move/v1`** (D-8): a readable one-line command after
  `[request <id>]`.
- **D-33:** one request per card move; a retry keeps the request ID; a
  superseded move is closed as superseded.
- **D-37:**
  - client operation IDs, and service-allocated request IDs in the
    `<project>-<yyyymmdd>-<n>` style;
  - no free text in a move, and one line only;
  - the same operation ID with the same content returns the original
    receipt, and with different content is rejected.
- **D-60:** one live execution per card, fenced by generation.

**Gaps in today's mechanisms:**
- **Dedup is by message ID only.** A repost or relay of the same logical
  request with a new ID queued a new wake: 3 wakes for 1 request in the
  fixture, and a fresh wake live.
- **A missing citation is neither annotated nor refused.** The citation
  check applies only to manager senders, and the cited post's room is not
  checked.
- **Routing is by channel prefix to the project's manager.** No room is
  bound to a request.
- **Nothing bounds the return.** After acceptance the wake is removed, and
  nothing tracks a result.
- **The identity registry can disagree with live sessions.** A worker was
  listed as retired, with no session, while its session was live and woke.

**Agent judgment, not mechanism:**
- Unprepared, the woken worker declined to act on a relayed request.
- Prepared, it accepted with an exact reply and executed once.
- On a repost it refused, saying the request was a duplicate. That refusal
  was its own reasoning, confounded by the trial's one-marker limit. No
  component enforced it.

**Accounting.** The corrected trial counted 3 subject activations (one typed
preparation and two requests). The recipient's two replies brought the
conservative total of submissions to 5, over a cap of 4. The replies were
neither reserved nor capped.

## Desired experience

A manager asked twice for the same thing does it once and says so. An
assistant relaying an owner's request carries the owner's original, scoped
authority, not its own. Anyone reading the record can see, for each request:
- who may decide it;
- whether it was accepted, claimed, started and finished, or when it expired;
- the exact result.

A participant at its budget cap sends nothing more, and the record says why.

## Scope

### In scope

- A generic request identity, reconciled with `card-move/v1`.
- An authority envelope.
- Acceptance, claim, start, result and closure as distinct transitions.
- Restart and lost-reply recovery.
- Bounded return, expiry and tombstones.
- Enforced message budgets.
- A mapping to PLT-1, PLT-3 and the clarification inbox.

### Out of scope

- Implementation.
- Changing installed skills, runtime configuration, security, accounts or
  identities.
- Another live trial before COLLAB-5 is separately approved.
- Exactly-once guarantees for arbitrary external side effects.
- Deleting or compacting the chat archive.
- The card-move rules of PLT-1 and PLT-3 themselves.

## Design

Every rule in this section is **proposed** (see Decisions).

### 1. Request identity

**The execution key.**
- The request ID follows D-33 and D-37: it is allocated by the shared
  service, in the `<project>-<yyyymmdd>-<n>` style.
- The client's operation ID stays the idempotency key for recovering a lost
  response.
- The **execution key** is the request ID plus its revision number. It is
  immutable: one key, one exact content digest, one execution.

**Repeats and conflicts.**
- A message whose key and digest match a known key is a **repeat**, whether
  it is a repost, a relay or a replay. It is answered from the record and
  never re-executed.
- The same key with a different digest is a **conflicting reuse**. It is
  refused visibly, and the existing record is unchanged.

**Supersession.** Changing a request creates a new key: the same request ID
at revision n+1, with its own digest. It is accepted only from the request's
original authority, or from one that authority names. A superseding
revision:
- closes the earlier key as `superseded`, but only if that key has not
  started;
- if the earlier key has started, or its outcome is unknown, waits until
  that key reaches a terminal or reconciled state. It never re-executes the
  earlier work and never erases its claim or result;
- is refused while a newer revision already exists.

**Registration.** Registering a key is one atomic insert-if-absent in the
request store. Two concurrent registrations of the same key converge: one
wins, and the other gets the winner's record. The authoritative room is
bound in that same insert, before any dispatch or acceptance. Copies of the
request elsewhere are pointers to it, not new requests.

### 2. Authority envelope

- **The chain.** The envelope carries the original scoped grant, the chain
  of provenance (owner, then assistant, then manager), and exactly one
  intended decision-maker.
- **Proof.** The verification anchors are the owner's existing written
  policy grant and the server-verified assistant's relay post. That relay
  is checked as `cited_authority` checks today.
  - Later hops, such as a manager handing work to a worker, cite that relay
    and keep its exact scope.
  - The owner's original instruction may be in a client outside chat. Its
    reference is audit context, not an IRC-verifiable link.
  - A sender's nick or label is never proof.
- **Revocation and supersession.** A later verified post from the same
  authority can revoke or supersede the grant. Gates attached to the
  original grant, such as a merge or live-action gate, travel with it.
- **No widening.** A relay never widens scope, and no envelope carries a
  blanket delegation.
- **Device-neutral owner authority (owner-confirmed).**
  - The owner gives an instruction to an assistant in whatever client the
    owner uses. The assistant conveys the bounded decision into chat,
    posting as its own server-verified account. That chat post is the
    request's authority.
  - Plateia validates three things: the request's correlation, the
    delegation's scope, and the relay's provenance. None of them depends on
    the owner's device.
  - Plateia does not try to authenticate copied owner text as if the owner
    had posted it in IRC. Trace references from the owner's conversation,
    such as a thread or message reference, are recorded for audit. They are
    never self-authenticating proof.
- **What existing anchors support today.**
  - **Verified authorship.** `cited_authority` resolves a chat post to its
    server-verified account, and refuses unverified or non-owner-or-assistant
    accounts.
  - **The operational grant.** The owner's own manager policy already grants
    authority to registered, server-verified assistant accounts, and asks
    that such work be logged as "owner via assistant". Its exception for
    managers is scoped to the cited post and its endpoint. This design
    relies on that existing grant. It does not create, widen or reinterpret
    it.
  - **Precedence.** A request's own restrictions, and owner-only or security
    gates, still win over the grant.
- **What those anchors do not support yet.** These are engineering
  prerequisites for *every* client, not phone restrictions:
  - **Scope, expiry and revocation.** `cited_authority` proves who posted.
    It does not prove the delegation's full scope, its expiry or its
    revocation, and it cannot validate the original conversation.
  - **A request-scoped delegation record.** Proposed: a structured record
    posted by the verified assistant, carrying:
    - the request ID;
    - a scope that is narrower than, or equal to, the policy grant;
    - the gates that travel with it;
    - a policy reference;
    - optional expiry;
    - supersession or revocation by a later verified post.

    Plateia would check each request against that record.
  - **No widening.** The record is derived from the existing grant with
    narrower limits. It never turns the configured list of assistant
    accounts into blanket scope, and it adds no new security policy.
- **Nothing beyond the existing grant is needed** to start, and no new trust
  bridge is proposed. Verifying the owner's original conversation directly,
  as a cryptographic or importer bridge, is explicitly deferred and not
  required (Q-6).

### 3. Transitions and their actors

Each transition is a durable record, correlated by execution key. Each names
the only actor allowed to make it. A transition from the wrong actor, for
the wrong key, or with a stale generation is refused and recorded.

| Transition | Actor | Meaning |
|---|---|---|
| Receipt | Transport | Delivered or read. Not acceptance. |
| Acceptance | The envelope's decision-maker | An explicit reply naming the request ID. Not execution. |
| Claim | The executing component | An atomic, fenced claim of execution: a compare and set on the key, holding an owner, a generation and a fence token (D-60's shape). A second claim fails and adopts the existing one. |
| Start | The claimant | Recorded before any external effect. |
| Result, stop or blocker | The claimant, at its generation | Durable as soon as it is written. |
| Closure | The requester, or the decision-maker for it | One receipt or acknowledgement naming the result. |

**The proposed closure rule:**
- A result is durable when written.
- The request is closed by one correlated receipt or acknowledgement of that
  result.
- A missing acknowledgement never reruns the work. It leaves the request
  "result delivered, unclosed", and may be reminded within the budget.
- Acknowledgements are never themselves acknowledged.
- A late or stale-generation result is kept in the record, but cannot
  overwrite a newer terminal outcome.

**The decision-maker and the claimant are different roles.** A manager may
accept a request and a worker may claim it. The record keeps both.

### 4. Restart, replay, fencing and its limits

- **After a restart,** the claimant recovers its persisted claim and any
  persisted result. A lost reply is answered again from the record.
- **Unknown outcomes.** If a start is recorded but its external effect's
  outcome is unknown, the key stays `unknown` until it is reconciled against
  that effect. An expired lease or a restart never permits a takeover
  without positive evidence of the earlier attempt's outcome.
- **A fence protects only components that check it.** The fence guarantees
  single execution only for components and adapters that check it *before*
  their side effect. A shared ledger alone cannot stop an agent or tool that
  bypasses it. Covering a participant therefore means building the check
  into that participant's adapter, which is a rollout gate (Q-4).
- **Adapters.** No exactly-once promise is made for arbitrary external
  effects. Each adapter that acts on the world (a GitHub write, a merge, a
  file publication) must name its idempotency key or its verification query.
  An adapter without one is refused automatic recovery.

### 5. Bounded return, expiry and tombstones

- **The bound.** Every accepted key has a return bound. Past it, the key is
  visibly `expired` and the requester is told. Expiry is not closure: a
  later result is still recorded under the rules in section 3.
- **Tombstones.** A closed or superseded key leaves a permanent minimal
  tombstone: its key, digest, terminal state and time. An old message can
  never resurrect it.
- **Fail closed.** If a tombstone store is unreadable, or of an
  incompatible version, or a rollback would lose tombstones, the store
  refuses new registrations for the affected keys. It never silently
  forgets them.
- **Retention.** The chat itself is never touched: V-2 keeps every room's
  archive forever and searchable. Only the request store's execution
  payloads could be compacted, under a policy the owner decides (Q-3).
  Tombstones are never compacted.
- **Steps.** Multi-step work uses child keys, each with its own lifecycle
  and its own reserved budget (section 6).

### 6. Enforced message budgets

Budgets are enforced, not just reported.

- **One shared cap per scope.** A request, or a trial, has a single cap.
  Every boundary that sends on its behalf draws from that same cap: the
  requester, the bridge, the decision-maker, the claimant, child work, and
  replies and acknowledgements.
- **Reserve before every send.** Each submission atomically reserves a slot
  first. That covers preparations, requests, reposts, replies,
  acknowledgements, reminders, and sends that wake no one. Concurrent
  senders cannot overspend.
- **Reserve the whole exchange up front.** Before work starts, the requester
  reserves a finite allowance for the whole exchange, including the terminal
  report, so a result is never stranded for lack of budget.
- **Refusal at the cap sends nothing.** A send refused at the cap produces
  no submission and wakes no one. It is recorded locally, and no error
  message is sent about it, so there is no unbudgeted error loop.
- **Crashes keep the slot consumed.** A crash after reserving, whether
  before or after the send, leaves the slot consumed, with the outcome
  unknown unless it is proven unused. There are no refunds for unknowns.
- **Coordination overhead.** Overhead has its own bounded cap. Callers
  cannot move a request's or trial's own messages, replies included, into
  overhead.
- **Three counts.** The ledger counts submissions, deliveries and
  activations separately. A delivery that wakes no one is not an
  activation.

COLLAB-4 builds this enforcement only at plateia's own sending boundaries.
Enforcement inside agents does not exist, and this design does not claim it.

### 7. Who enforces what

| Behavior | Today | Proposed |
|---|---|---|
| Message-ID dedup | Bridge (mechanism) | Kept |
| Logical request dedup | Recipient judgment only | Request store, by execution key and digest, at components that check it (COLLAB-2) |
| Citation check | Annotation; a missing citation is ignored | The envelope is required and verified (COLLAB-1, COLLAB-2) |
| Exact acceptance | Bridge, by a reply naming the message ID | By a reply naming the request ID (COLLAB-1, COLLAB-3) |
| Execution once | Agent judgment | Fenced claim, at participants whose adapter checks it (COLLAB-2, Q-4) |
| Bounded return | None | Return bound, expiry and tombstone (COLLAB-3) |
| Message budget | Manual, under-counted | Reserved slots under one cap at plateia's boundaries (COLLAB-4) |

### 8. Overlap with PLT-1, PLT-3 and the clarification inbox

- **PLT-1** (durable logical card-move submission, `card-move/v1`):
  - this design reuses its request-ID allocation, its operation-ID
    idempotency and its duplicate rule (D-33, D-37), and generalizes them to
    other requests;
  - *serves* its need for authority and closure;
  - *leaves open* card-move semantics.
  - **Owner decision before processing (Q-5):** whether a generic request
    uses a readable one-line command like `card-move/v1`, IRCv3 client tags,
    or both. Any difference from approved `card-move/v1` is an owner
    decision, never a silent change.
- **PLT-3** (reconcile ordered card intent before dispatch):
  - *serves* claim, fencing and terminal records, in D-60's generation-fenced
    shape;
  - *leaves open* intent ordering across cards.
- **Clarification inbox:**
  - its `+plateia/` tags and per-message `key` stay as they are;
  - *conflicts* only if the codec choice in Q-5 differed from both forms
    without a mapping.

## Decisions

D-1 to D-6 are **proposals, not ratified owner decisions**. The owner's
2026-10-13 decision authorized the design only. D-7 records what the owner
confirmed later that day. D-8 to D-14 record the owner's answers to the open
questions on 2026-10-14.

### D-1. The request ID and the message ID are different things

Proposed. A transport message ID identifies one delivery. The execution key
and its digest decide repeats. This is grounded in the fixture: one request
produced three wakes.

### D-2. Repeats are answered from the record, never re-executed

Proposed. Components that check the record answer a repeat from it. A
recipient's own judgment remains a defense, but is never the guarantee.

### D-3. Acceptance, claim, execution and closure are separate transitions with named actors

Proposed. Both live trials showed that waking a recipient is not acceptance,
and accepting is not executing.

### D-4. Unknown external outcomes are reconciled, not retried

Proposed. No lease expiry or restart turns an unknown into a takeover
without positive evidence.

### D-5. Every send reserves a slot under one shared cap, replies included

Proposed. The corrected trial's overrun came from unreserved replies.

### D-6. Reuse `card-move/v1`'s identity rules; the codec is an owner decision

Proposed. This replaces the first draft's choice of `+plateia/` client tags,
which would have competed with approved `card-move/v1`. See Q-5.

### D-7. Owner authority is device-neutral; requests have a format and validation

**Owner-confirmed** (2026-10-13). An owner instruction relayed by a verified
assistant has the same authority from any client. The owner agrees with a
request format and validation. How the delegation is represented and checked
(section 2) is still a proposal.

### D-8. Requests and claims live in the outbox authority's database

**Owner decision** (2026-10-14; it answers Q-1). The request and claim records
live in the bridge's outbox authority database (#19, #24), which already holds
fenced state. There is no separate store.

### D-9. A request's return bound

**Owner decision** (2026-10-14; it answers Q-2's structure). The requester sets
a request's return bound, up to a project maximum. A project default applies
when the requester sets none. The owner set the amounts the same day: the
project default is **4 hours** and the project maximum is **24 hours**.

### D-10. Nothing in the request store is compacted for now

**Owner decision** (2026-10-14; it answers Q-3). Like V-2's chat archive,
request records, results and tombstones are kept and not compacted. Revisit
this only if size becomes a real problem.

### D-11. Claim checks: plateia's own components first

**Owner decision** (2026-10-14; it answers Q-4).
- **Now:** plateia's own components check the claim before their side
  effects.
- **Later:** agents and their tools adopt the check through kanban's skills.
- **Until then:** each participant that does not check the claim is listed
  as not covered by the single-execution guarantee. COLLAB-2's done condition
  covers plateia's own components.

### D-12. Generic requests use the readable one-line form

**Owner decision** (2026-10-13, through #27; it answers Q-5). Generic requests
use one readable line in the existing `[request <request-id>]` convention, like
`card-move/v1`. COLLAB-1 implements it (#27, PR #28).

### D-13. The delegation record names the existing manager policy as it stands

**Owner decision** (2026-10-14; it answers Q-6's one owner choice). The
request-scoped delegation record refers to the existing manager policy as it
stands, not to a versioned copy. No new confirmation is needed.

### D-14. The default message cap is 4 per request, replies included

**Owner decision** (2026-10-14; it answers Q-7). Each request's default cap is
4 messages, counting replies (the corrected trial's cap). Only the owner may
raise it, per request.

## Open questions

### Q-1. Where does the request record live?

**Decided** (2026-10-14): see D-8.

The outbox authority's database (#19, #24) is the natural home: it already
holds fenced state. The alternative is a separate store. This decides
COLLAB-2's scope; it is the owner's decision.

### Q-2. What is the default return bound, and who may extend it?

**Decided** (2026-10-14): see D-9. The default is 4 hours and the maximum is 24 hours.

It affects COLLAB-3. The known options are a per-request bound set by the
requester, or a project default.

### Q-3. What may be compacted in the request store?

**Decided** (2026-10-14): see D-10.

V-2's chat archive is never deleted or compacted, and tombstones are kept
forever. Whether and when the request store's execution payloads may be
compacted is a privacy and size trade-off for the owner. It affects
COLLAB-3.

### Q-4. Which participants must check the claim before acting, and when?

**Decided** (2026-10-14): see D-11.

This is a **rollout gate**, not a note. Until a participant's adapter checks
the claim before its side effects, the single-execution guarantee does not
cover that participant. Skills are out of scope here, so which agents and
tools adopt the check, and when, is the owner's decision. It gates COLLAB-2's
done condition.

### Q-5. Which form do generic requests use?

**Decided** (2026-10-13): see D-12.

The options are a readable one-line command like `card-move/v1`, IRCv3
client tags like the clarification inbox, or both with a mapping. Any
incompatibility with approved `card-move/v1` (D-8, D-33, D-37) is an owner
decision. It gates COLLAB-1.

### Q-6. How is relayed owner authority checked by machine?

**Decided** (2026-10-14): see D-13 for the owner's one choice. The engineering below is the proposal it builds on.

The owner's intent is settled: authority is device-neutral, and relayed
through a verified assistant under the existing grant (D-7). What is open
is engineering, and it applies to every client.

- **Today:** verified authorship, plus the existing policy grant.
- **Proposed:** the request-scoped delegation record in section 2. Plateia
  checks each request against it for correlation, a scope no wider than the
  grant, and relay provenance.
- **Owner action:** none is required beyond the authority already given. One
  choice is the owner's, as the author of that policy: whether the record's
  policy reference names the existing manager policy as it stands, or a
  versioned copy of it. The default is the policy as it stands, with no new
  confirmation needed.
- **Deferred:** verifying the original conversation itself, which would be a
  new trust bridge. It is not needed for this contract.

It affects COLLAB-1.

### Q-7. What are the default caps, and who sets them?

**Decided** (2026-10-14): see D-14.

The design fixes enforcement, not amounts. It affects COLLAB-4.

## Verification strategy

- Invented-fixture tests per slice:
  - identity: repeat, relay, conflicting reuse, concurrent registration and
    supersession, both before and after start;
  - recovery: a lost reply after a restart, an unknown outcome, wrong-actor
    and stale-generation refusals;
  - closure: expiry and a late result, a tombstone against an old message,
    a fail-closed tombstone store;
  - budgets: refusal at the cap, concurrent reservations, and a crash after
    reservation.
- One bounded live trial at the end (COLLAB-5), only with separate owner
  approval. It runs under a reserved cap covering every submission in both
  directions, and the report must show the cap was never exceeded.

## Delivery plan

### COLLAB-1. Reconcile the generic request form with `card-move/v1`

- **Outcome:** a generic request form and authority envelope, reusing
  `card-move/v1`'s identity rules, in the form the owner chooses in Q-5. It
  includes validation of the request-scoped delegation record: correlation,
  a scope no wider than the existing grant, and relay provenance. It refuses
  malformed, conflicting, unverified or out-of-scope requests. Validation is
  the same for every client.
- **Scope:** the codec and validation only. No storage.
- **Phase:** 1
- **Depends on:** `none`
- **Ordering:** `can land first`, once Q-5 is decided
- **Relevant decisions:** D-1, D-6
- **Acceptance signals:**
  - fixture round-trips, and a refusal for each invalid form;
  - existing `card-move/v1` examples parse unchanged;
  - an out-of-scope request is refused;
  - a request relayed by a verified assistant gets the same validation
    outcome regardless of the owner's device, within the existing grant,
    the request's bounds and the owner-only gates. This is device
    neutrality, not identity substitution: the relay remains the
    assistant's post;
  - a trace reference alone never authorizes anything.
- **Out of scope:** persistence, routing, skills.
- **Open questions:** Q-5, Q-6

### COLLAB-2. Record requests and claims durably in the bridge's authority

- **Outcome:**
  - atomic registration with the room bound;
  - repeats answered from the record, and conflicting reuse refused;
  - supersession rules enforced;
  - fenced claims with actor and generation checks.
- **Scope:** the request and claim records, repeat handling, and recovery
  after a restart.
- **Phase:** 2
- **Depends on:** COLLAB-1
- **Ordering:** `critical path`
- **Relevant decisions:** D-2, D-3, D-4
- **Acceptance signals:**
  - a repost or relay produces no second claim;
  - concurrent registrations converge;
  - a stale claimant is fenced;
  - an unknown outcome stays unknown.
- **Out of scope:** return bounds, budgets, adapter rollout to agents.
- **Open questions:** Q-1, Q-4

### COLLAB-3. Correlate results, stops and blockers, and bound the return

- **Outcome:**
  - durable results;
  - closure by one correlated acknowledgement;
  - visible expiry;
  - tombstones that fail closed.
- **Scope:** terminal records, the closure rule, the return bound, and
  compaction of the request store only.
- **Phase:** 2
- **Depends on:** COLLAB-2
- **Ordering:** `critical path`
- **Relevant decisions:** D-3
- **Acceptance signals:**
  - expiry and late-result fixtures;
  - a stale result never overwrites a newer one;
  - an old message cannot reopen a closed key.
- **Out of scope:** budgets, and the chat archive.
- **Open questions:** Q-2, Q-3

### COLLAB-4. Enforce message budgets with reserved slots

- **Outcome:** one shared cap per scope, with:
  - an atomic slot reserved before every send at plateia's own boundaries;
  - a full-exchange allowance reserved up front;
  - refusal at the cap with no send and no error loop;
  - crash-consumed slots;
  - a bounded, unrelabelable overhead cap;
  - separate counts of submissions, deliveries and activations.
- **Scope:** the reservation store and the checks at plateia's sending
  boundaries. Not enforcement inside agents.
- **Phase:** 2
- **Depends on:** COLLAB-1
- **Ordering:** `independent`
- **Relevant decisions:** D-5
- **Acceptance signals:**
  - concurrent reservations never overspend;
  - a send refused at the cap emits nothing;
  - the corrected trial's shape (1 preparation, 2 requests, 2 replies)
    under a cap of 4 is stopped at the fifth reservation.
- **Out of scope:** agent-internal enforcement.
- **Open questions:** Q-7

### COLLAB-5. Verify the foundations in one bounded, isolated live trial

- **Outcome:** live evidence of:
  - idle wake;
  - exact acceptance by request ID;
  - a single claim;
  - a repeat refused by mechanism;
  - a cap that holds.
- **Scope:** one trial under an enforced cap covering every message, with
  disposable participants provisioned by an approved route.
- **Phase:** 3
- **Depends on:** COLLAB-2, COLLAB-3, COLLAB-4
- **Ordering:** `not on the critical path`
- **Relevant decisions:** D-2, D-5
- **Acceptance signals:**
  - the repeat is refused by the request store, not by the recipient's
    judgment;
  - no send exceeds the cap.
- **Out of scope:** production sessions, routing changes.
- **Open questions:** it needs separate owner approval of its budget and of
  how participants are provisioned.
