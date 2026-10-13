# Collaboration foundations design

This design states how plateia makes requests between the owner, assistants,
managers and workers safe to repeat and accountable. It covers what a
logical request is, who may ask, when a request counts as accepted and
executed, how it comes back, how lost or repeated messages are handled, and
how every message a request costs is counted. It is the design half of
#26, written from that issue's fixture and live-trial evidence. It
implements nothing.

Design state: `exploring`

Status legend: `[ ]` unprocessed · `[#N]` linked to issue N · `[no-issue]`
reviewed and deliberately not tracked separately · `[deferred]` blocked on a
concrete precondition

## Processing status

- [#26] EPIC. Collaboration foundations (umbrella: #26; no new epic)
- [ ] COLLAB-1. Define the request envelope and its codec in the shared chat package
- [ ] COLLAB-2. Record requests and claims durably in the bridge's authority
- [ ] COLLAB-3. Correlate results, stops and blockers, and bound the return
- [ ] COLLAB-4. Account every request message against a reserved budget
- [ ] COLLAB-5. Verify the foundations in one bounded, isolated live trial

## Epic contract

- **Goal:** a logical request between plateia's participants has one stable
  identity. It is verified against a scoped authority, accepted explicitly,
  claimed once, executed at most once by plateia's own mechanism, and
  answered within a bound. Every message it costs is counted.
- **Done when:**
  - a reposted or relayed request never causes a second claim or execution
    in any shipped component;
  - every accepted request ends in a result, stop or blocker that names it,
    or is visibly expired;
  - a trial's message count matches its reservation exactly.
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
- The clarification inbox (`packages/routine_inbox/`) carries a routing
  envelope in IRCv3 client tags (`+plateia/to`, `+plateia/key`,
  `+plateia/part`, `+plateia/parts`), keyed per message.

**Gaps in those mechanisms:**
- **Dedup is by message ID only.** A repost or relay of the same logical
  request with a new ID queued a new wake: 3 wakes for 1 request in the
  fixture, and a fresh wake live.
- **A missing citation is neither annotated nor refused.** The citation
  check applies only to manager senders, and the cited post's room is not
  checked.
- **Routing is by channel prefix to the project's manager.** No room is
  authoritative for a request.
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

**Accounting.** The corrected trial reported 3 subject activations (one typed
preparation and two requests). The recipient's two replies brought the
conservative total to 5 messages, over a cap of 4. The reply cost was not
reserved in advance.

## Desired experience

A manager asked twice for the same thing does it once and says so. An
assistant relaying an owner's request carries the owner's original, scoped
authority, not its own. Anyone reading the record can see, for each request:
- who may decide it;
- whether it was accepted, claimed, started and finished, or when it expired;
- the exact result.

An operator running a trial reserves its whole message budget before sending
anything, and the count afterwards matches.

## Scope

### In scope

- A logical request identity, separate from transport message IDs.
- An authority envelope.
- Acceptance, claim, start, result and acknowledgement as distinct records.
- Restart and lost-reply recovery.
- Bounded return, expiry and tombstones.
- Full message accounting.
- A mapping to PLT-1, PLT-3 and the clarification inbox.

### Out of scope

- Implementation.
- Changing installed skills, runtime configuration, security, accounts or
  identities.
- Another live trial before COLLAB-5 is separately approved.
- Exactly-once guarantees for arbitrary external side effects.
- The card-move rules of PLT-1 and PLT-3 themselves.

## Design

### 1. Request identity

- **The request ID.** Every logical request carries a stable, namespaced
  ID: `<origin-project>/<origin-request>/<action>`, such as
  `alpha/alpha-20261013-1/solve-12`. The transport message ID stays
  separate.
- **Content binding.** Each ID binds the request's exact content: the
  action, target and parameters, as a canonical digest, plus a revision
  number.
- **Repeats.** A message whose ID and digest match a known request is a
  *repeat*, whether it is a repost, a relay or a replay. It is answered
  from the record and never re-executed.
- **Conflicts.** The same ID with a different digest is a *conflicting
  reuse*. It is refused visibly, never merged.
- **Revisions.** An intentional change is a new revision of the same ID,
  allowed only by the original authority.
- **New actions.** A deliberately new action gets a new ID.
- **One authoritative room.** The first accepted message names the room.
  Copies elsewhere are pointers, not new requests.

### 2. Authority envelope

- **The chain.** The envelope carries the original scoped grant, the chain
  of provenance (owner, then assistant, then manager), and exactly one
  intended decision-maker.
- **Proof.** Each link is a verified post, checked as `cited_authority`
  checks today. A sender's nick or label is never proof.
- **Revocation and supersession.** A later verified post from the same
  authority can revoke or supersede the grant. Gates attached to the
  original grant, such as a merge or live-action gate, travel with it.
- **No widening.** A relay never widens scope, and no envelope carries a
  blanket delegation.

### 3. Lifecycle records

Each step is its own durable record, correlated by request ID:

- **Receipt.** Delivered or read. It is not acceptance.
- **Acceptance.** The decision-maker's explicit reply naming the request
  ID. It is not execution.
- **Claim.** An atomic, fenced record of who owns execution: a compare
  and set on the request ID, holding an owner, a generation and a fence
  token. A second claim fails. A stale owner's writes are refused by the
  fence.
- **Start.** Recorded before any external effect.
- **Result, stop or blocker.** Exactly one terminal record, naming the
  request ID and claim generation.
- **Acknowledgement of the result.** One acknowledgement closes the
  request. It is never itself acknowledged, so there are no ack-of-ack
  loops.

### 4. Restart, replay and lost replies

- **After a restart,** the claimant recovers its persisted claim and any
  persisted result. A lost reply is answered again from the record.
- **Unknown outcomes.** If a start is recorded but its external effect's
  outcome is unknown, the request stays `unknown` until it is reconciled
  against that effect. It is never retried blindly, and an expired lease
  never redispatches it.
- **Adapters.** No exactly-once promise is made for arbitrary external
  effects. Each adapter that acts on the world (a GitHub write, a merge, a
  file publication) must name its idempotency key or its verification
  query. An adapter without one is refused automatic recovery.

### 5. Bounded return and closure

- **The bound.** Every accepted request has a return bound. Past it, the
  request is visibly expired and the requester is told. Expiry is not
  logical closure: a late result is still recorded, and closes the request.
- **Tombstones.** A closed request leaves a permanent minimal record: its
  ID, digest, terminal state and time. An old message can never resurrect
  it.
- **Retention.** Full records follow a stated retention policy (Q-3).
- **Steps.** Multi-step work uses child IDs, such as
  `…/solve-12/review-1`, each with its own lifecycle.

### 6. Full message accounting

- **Reservation.** Before every submission, the sender reserves budget
  for it. That includes preparations, first and repeated wakes, replies,
  acknowledgements, and sends that end queued, failed or unknown.
- **Two counts.** The ledger counts *submissions* (what was attempted)
  separately from *deliveries and activations* (what woke someone).
- **No refunds.** An unknown result is never refunded.
- **Coordination overhead.** Messages that coordinate a request are
  counted separately from the request's own budget, and the report says
  which is which.
- **Waking.** A reply or acknowledgement that needs no action is sent
  without waking an idle model where the transport allows. Otherwise its
  cost is reserved.

### 7. Who enforces what

| Behavior | Today | Proposed |
|---|---|---|
| Message-ID dedup | Bridge (mechanism) | Kept |
| Logical request dedup | Recipient judgment only | Authority enforces by request ID and digest (COLLAB-2) |
| Citation check | Annotation; a missing citation is ignored | The envelope is required and verified (COLLAB-1, COLLAB-2) |
| Exact acceptance | Bridge, by reply naming the message ID | By reply naming the request ID (COLLAB-1, COLLAB-3) |
| Execution once | Agent judgment | Fenced claim (COLLAB-2) |
| Bounded return | None | Return bound, expiry and tombstone (COLLAB-3) |
| Message budget | Manual, under-counted | Reservation ledger (COLLAB-4) |

### 8. Overlap with PLT-1, PLT-3 and the clarification inbox

- **PLT-1** (durable logical card-move submission):
  - *serves* its need for a stable logical ID, dedup and authority;
  - *leaves open* card-move semantics and ordering;
  - *conflicts* only if PLT-1 defines its own envelope. It should reuse
    COLLAB-1's.
- **PLT-3** (reconcile ordered card intent before dispatch):
  - *serves* claim, fencing and terminal records;
  - *leaves open* intent ordering across cards.
- **Clarification inbox:** COLLAB-1 extends the existing `+plateia/` tag
  namespace rather than adding a second codec. The inbox's per-message
  `key` stays as is. *Conflicts* only if the two keys were confused; the
  request ID is a separate tag.

## Decisions

### D-1. The request ID and the message ID are different things

A transport message ID identifies one delivery. Only the request ID and its
digest decide repeats. This is grounded in the fixture: one request
produced three wakes.

### D-2. Repeats are answered from the record, never re-executed

Every component answers a repeat from its record. A recipient's own
judgment remains a defense, but is never the guarantee.

### D-3. Acceptance, claim and execution are separate records

Both live trials showed that waking a recipient is not acceptance, and
accepting is not executing.

### D-4. Unknown external outcomes are reconciled, not retried

No lease expiry or restart turns an unknown into a redispatch.

### D-5. Every message is reserved before it is sent, replies included

The corrected trial's overrun came from unreserved replies.

### D-6. The envelope extends `+plateia/` client tags

There is one codec, shared with the clarification inbox.

## Open questions

### Q-1. Where does the request record live?

The outbox authority's database (#19, #24) is the natural home: it already
holds fenced state. The alternative is a separate store. This decides
COLLAB-2's scope; it is the owner's decision.

### Q-2. What is the default return bound, and who may extend it?

It affects COLLAB-3. The known options are a per-request bound set by the
requester, or a project default.

### Q-3. What is the retention policy for full records?

Tombstones are permanent and minimal. How long full records are kept is a
privacy and size trade-off for the owner. It affects COLLAB-3.

### Q-4. Must existing agents and skills adopt the envelope, and when?

Skills are out of scope here. Until they adopt it, dedup is enforced only
where plateia's own components act. Adoption timing is the owner's
decision. It affects COLLAB-2's done condition.

## Verification strategy

- Invented-fixture tests per slice: repeat, relay, conflicting reuse,
  revision, a lost reply after a restart, an unknown outcome, expiry and a
  late result, a tombstone against an old message, and reservation
  arithmetic.
- One bounded live trial at the end (COLLAB-5), only with separate owner
  approval. It runs under a reserved budget that covers every submission in
  both directions, and the report must match the reservation exactly.

## Delivery plan

### COLLAB-1. Define the request envelope and its codec in the shared chat package

- **Outcome:** the request ID, digest, revision and authority chain encode
  and decode as `+plateia/` client tags, with refusal of malformed,
  conflicting or unverified envelopes.
- **Scope:** the codec and validation only. No storage.
- **Phase:** 1
- **Depends on:** `none`
- **Ordering:** `can land first`
- **Relevant decisions:** D-1, D-6
- **Acceptance signals:** fixture round-trips, and refusals for each
  invalid form.
- **Out of scope:** persistence, routing, skills.
- **Open questions:** `None`

### COLLAB-2. Record requests and claims durably in the bridge's authority

- **Outcome:** repeats are answered from the record, conflicting reuse is
  refused, and claims are atomic and fenced.
- **Scope:** request and claim records; repeat handling; recovery after a
  restart.
- **Phase:** 2
- **Depends on:** COLLAB-1
- **Ordering:** `critical path`
- **Relevant decisions:** D-2, D-3, D-4
- **Acceptance signals:**
  - a repost or relay produces no second claim;
  - a stale claimant is fenced;
  - an unknown outcome stays unknown.
- **Out of scope:** return bounds, accounting.
- **Open questions:** Q-1, Q-4

### COLLAB-3. Correlate results, stops and blockers, and bound the return

- **Outcome:** each accepted request ends in one terminal record, or a
  visible expiry. Tombstones block resurrection.
- **Scope:** terminal records, acknowledgement without loops, the return
  bound, retention.
- **Phase:** 2
- **Depends on:** COLLAB-2
- **Ordering:** `critical path`
- **Relevant decisions:** D-3
- **Acceptance signals:** expiry and late-result fixtures; an old message
  cannot reopen a closed request.
- **Out of scope:** accounting.
- **Open questions:** Q-2, Q-3

### COLLAB-4. Account every request message against a reserved budget

- **Outcome:** a reservation ledger that counts submissions and
  activations separately, includes replies and acknowledgements, and never
  refunds an unknown.
- **Scope:** the ledger and its report.
- **Phase:** 2
- **Depends on:** COLLAB-1
- **Ordering:** `independent`
- **Relevant decisions:** D-5
- **Acceptance signals:** the arithmetic fixtures, including the corrected
  trial's shape (1 preparation, 2 requests, 2 replies gives 5).
- **Out of scope:** enforcement inside agents.
- **Open questions:** `None`

### COLLAB-5. Verify the foundations in one bounded, isolated live trial

- **Outcome:** live evidence of idle wake, exact acceptance by request ID,
  claim once, refusal of a repeat by mechanism, and an exact message count.
- **Scope:** one trial under a reservation covering every message, with
  disposable participants provisioned by an approved route.
- **Phase:** 3
- **Depends on:** COLLAB-2, COLLAB-3, COLLAB-4
- **Ordering:** `not on the critical path`
- **Relevant decisions:** D-2, D-5
- **Acceptance signals:** the report's counts equal the reservation, and
  the repeat is refused by the authority, not by the recipient's judgment.
- **Out of scope:** production sessions, routing changes.
- **Open questions:** needs separate owner approval of its budget and of
  how participants are provisioned.
