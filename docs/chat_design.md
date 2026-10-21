# Milestone 1: working chat

This design turns [the accepted vision](vision.md) into a usable chatroom on
phone and desktop. It records the milestone's existing owner decisions and
keeps unresolved choices explicit. It does not authorize implementation,
installation, live configuration or service changes.

Design state: `ready for issue processing`

Vision-edit rule (owner decision 2026-10-20, D-50): present the exact proposed
wording and obtain owner approval before any future edit to `docs/vision.md`.
Approval to record a design answer is not approval to edit the vision.

Status legend: `[ ]` unprocessed · `[#N]` linked to issue N · `[no-issue]`
reviewed and deliberately not tracked separately · `[deferred]` blocked on a
concrete precondition

## Processing status

- [x] EPIC. Deliver milestone 1: working chat on phone and desktop — [#36]
- [x] CHAT-1. Define the shared chat API records and compatibility contract — [#37]
- [ ] CHAT-2. Preserve one logical message across IRC multiline fragments
- [ ] CHAT-3. Expose authoritative history with a gap-free live handoff
- [ ] CHAT-4. Share owner IRC sessions and verified room membership
- [ ] CHAT-5. Persist and fence logical send attempts before IRC writes
- [ ] CHAT-6. Connect the shared sender to Ergo with conservative recovery
- [ ] CHAT-7. Publish role and agent-state facts from real sessions
- [ ] CHAT-8. Expose durable per-recipient bridge delivery outcomes
- [ ] CHAT-9. Apply mention routing to complete logical messages
- [ ] CHAT-10. Serve a protected Python web adapter with browser owner binding
- [ ] CHAT-11. Build the first text chat page with live history and composition
- [ ] CHAT-12. Complete browser send recovery and remembered dismissal
- [ ] CHAT-13. Show room users and owner join/leave controls
- [ ] CHAT-14. Display bridge-backed agent-delivery errors on messages
- [ ] CHAT-15. Store capped images with durable references and safe cleanup
- [ ] CHAT-16. Compose image links through paste and the image button
- [ ] CHAT-17. Render managed images inline and verify phone formats
- [ ] CHAT-18. Implement shared room identities and issue/PR lifecycle mapping
- [ ] CHAT-19. Verify structured room archives with their images and membership cleanup
- [ ] CHAT-20. Close dismissed send gaps and reconcile bounded late history
- [ ] CHAT-21. Present every room type and its lifecycle in plateia
- [ ] CHAT-22. Provide guarded per-project cutover and owner-specific guidance
- [ ] CHAT-23. Qualify the complete milestone across service and browser boundaries

These 23 slices follow D-74's approved stage order. D-76 approves their
boundaries at `e6107c0663`, boundaries only. D-81 settles K-1's design
contract; D-82 settles K-2's state mapping; D-86 settles K-3's image contract.
Their integration checks remain required. D-87–D-94 settle K-4's design
contract, as mapped in the coverage review below. Epic [#36](https://github.com/coghex/plateia/issues/36)
is filed; all 23 children remain unprocessed. D-95 records readiness and
D-96 delegates issue/epic signoff to the project manager within the approved
design. Each artifact still needs separate signoff before filing.

## Epic contract

- **Goal:** the owner can read live conversations, read earlier messages,
  post as the owner, address agents, see room users and share pasted images
  from both a phone and a desktop browser.
- **Done when:** all milestone 1 room types support that experience;
  closed issue/PR conversations and their images are preserved together;
  PNG sharing works; and the
  five V-12 guarantees hold through connection loss and process restarts.
  Apply the owner's explicit unconfirmed-send/dismissal and archive-gap
  rules in D-47/D-48/D-65/D-66 rather than claiming every uncertain send
  can be proved posted or cancelled.
  The concrete acceptance scenarios follow the decisions below.
- **Users and operators:** one owner; the existing shared chat services and
  agents continue operating independently of the web application.
- **Arc label:** None proposed.
- **Publication:** this document belongs to `coghex/plateia`, on `master`.
  All milestone-1 issues use that tracker. Shared workflow implementation
  stays in the skills repository under `plateia_design.md` D-78, using D-75's local review and
  delivery route; plateia code uses its own pull requests.

## Current state and evidence

Read-only checks on 2026-10-20:

| Evidence | Established fact and limit |
| --- | --- |
| `docs/vision.md` at `228159c` | The owner accepted working chat as milestone 1, including posting, history, room users and pasted PNG images. |
| `README.md`, `packages/routine_inbox/`, repository file inventory | There is no web application. The routine inbox is the only package; it is not an existing browser chat backend. |
| `docs/plateia_design.md`, D-78; retirement PR #35 | The skills repository owns chat. Plateia's captured chat and release packages were removed. Retired outbox-authority and request-store designs are not live capabilities. |
| Skills repository, `chat/SKILL.md` | Current guidance still describes request channels. D-8 settles reconciliation in the owner's global skills when room implementation is reached; the live guidance has not changed. |
| Skills repository, `chat/scripts/chatlib.py`, `post` | Posting authenticates an account and handles IRC message formatting. Its result is a line count; its public signature has no caller-supplied logical operation ID. This alone does not establish browser retry safety. |
| Skills repository, `chat/scripts/chatlib.py`, `_round`, `already_posted`, refreshed 2026-10-20 | The sender waits for a PONG after writing IRC messages. Retry reconciliation matches account/text/time in local logs; partially landed posts may be retried whole. There is no message-ID check-and-cancel API. This does not meet D-34's at-most-once contract. |
| Read-only Ergo capability probe, shared sender/bridge source and isolated multiline tests, 2026-10-20 | Ergo advertises `batch`, `message-tags` and `draft/multiline`. Shared chat negotiates multiline and joins each batch in live/history delivery. Ten selected existing tests pass. Oversized posts still become separate batch records without a common logical message ID; leading/trailing blank lines are stripped. See D-37 for the required gaps. No test messages were sent to live rooms. |
| Upstream Ergo at `678d29d5442b00aa4ed79e86f9139443a3e1994a`, publication path | No caller-ID deduplication or per-message expiry check was found. P-36's server-patch proposal was rejected; D-45 changes the deadline to the single writer's decision cutoff. |
| Installed Ergo 2.19.1, read-only capability/history checks for D-45 | Negotiated message tags, batches, multiline, echo-message, labeled-response, CHATHISTORY, server-time and account tags. History replay retained an existing client-only reply tag; repeated reads retained the same value. A mid-window server-msgid lookup succeeded. No tagged test message was posted, and no config/service change or restart was made. |
| Ergo 2.19.1 source, follow-up recovery analysis | Channel delivery precedes history insertion; a history insertion error is logged without undoing delivery. Session logout does not wait for the old command loop to finish. P-39 records why these are insufficient proofs of safe cancellation after an uncertain write. No live fault was induced. |
| Skills repository, `chat/scripts/chat-bridge`, `project_for`, `handle` | Routing uses project channel names/prefixes. Ordinary direct agent delivery is restricted by sender category, unlike V-6's any-agent communication. Queuing precedes logging, with crash replay permitted. These are integration gaps, not evidence that the new contract is implemented. |
| Skills repository, `chat/SKILL.md`, identity and delivery sections | Stable identities, session discovery, history, outbox, delivery and acceptance machinery exist. Their exact guarantees and usable interface require focused verification before designing the web adapter. IRC login alone is not presence evidence. |
| Shared `chat-bridge`, `Deliveries._record`, `_dead`, `accept`, `accepted`, and `pchat trace`, checked for D-41 | The bridge records attempts, dead letters and authenticated replies/acks. Delivery success and recipient acceptance are separate. Attempt records omit a structured recipient field; acceptance closes matching pending items, while late acknowledgements also have a separate record. `trace` renders request-oriented text, not a typed per-message/per-recipient outcome. A shared read contract is required for plateia. |
| GitHub issue/PR inventory, `coghex/plateia`, refreshed 2026-10-21 | No open issues or PRs. Earlier foundation epic #10 and collaboration epic #26 are closed; neither is an open milestone 1 tracker. Recheck overlap before readiness. |
| `.github/workflows/ci.yml`, refreshed 2026-10-20 | Python 3.10/3.14 tests feed an always-running `build-test` aggregate that rejects any non-success dependency. There is no frontend package or Node build yet. D-27 extends this existing gate. |

The brief identifies general and project testing rooms as not yet configured.
This design does not publish private configuration. Creating those
rooms in live configuration remains the owner's action.

Presence follow-up, 2026-10-21: shared `chat/scripts/identities.py` supports
owner and assistant roles, named global assistants, `status`/`last_seen`,
and process/session evidence. Its `sync` preserves identities on an empty
inventory and retires ended sessions only after a successful inventory.
These are inputs for the shared presence contract, not proof that a registered
account is currently online or that browser-owner presence already exists.
No identity sync or live configuration change was run for this inspection.

Owner-account follow-up, 2026-10-21: the owner's update supersedes the earlier
single-owner finding. The manager reports that the phone account is now a
shared-chat owner, with support delivered in skills commit `7ce2401`.
Read-only source inspection confirms that `chatlib.owners(cfg)` combines
`owner` and `owners`, and that authority, delegation citations, owner-led
enrollment, direct dispatch and owner notifications consume that set.
The commit includes second-owner regression tests; those tests were inspected,
not run for this documentation update. The applied private configuration is
manager-reported, not inspected or reproduced here. D-61/D-62 consume this
completed support; plateia must recognize every account in the shared owner
set, not maintain its own two-account authority list. That authority follow-up
required no private configuration inspection or changes. The manager also
reports the bridge restarted on `7ce2401`, with this authority now live.

Membership follow-up, 2026-10-21: read-only inspection identifies the running
installation as Ergo 2.19.1. Its configuration enables multiclient attachment,
allows it by default and forces nicknames to equal account names. The matching
release's `ClientManager.SetNick` attaches an authenticated connection to the
existing account's client; channel membership belongs to that shared client,
and `Channel.Part` removes it and reports the PART to its other sessions.
This supports D-63's shared membership across interfaces, conditional on
authenticating during connection registration. No test JOIN/PART, owner login,
configuration change or service restart was performed.
[Ergo 2.19.1 attachment implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/client_lookup_set.go),
[channel membership implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/channel.go).

Retention after **all** IRC connections disappear is a separate setting:
the installation uses opt-in always-on accounts. A read-only examination of
the running process's datastore found that always-on is not enabled for every
owner profile. D-64 requires completing that setup later; multiclient support
alone is not proof of zero-connection retention. This check does not establish
runtime join/part behavior through a live experiment or successful recovery
after a server restart; those remain integration verification.
[Ergo always-on behavior](https://github.com/ergochat/ergo/blob/v2.19.1/docs/USERGUIDE.md#always-on),
[persistence setting evaluation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/config.go),
[startup restoration](https://github.com/ergochat/ergo/blob/v2.19.1/irc/accounts.go).

### Relationship to the earlier design

`plateia_design.md` remains historical design evidence; this document does
not silently inherit its former epic or delivery order.

- `plateia_design.md` D-78 governs chat source ownership and retires the capture/release work.
- The vision's 2026-10-20 milestones replace the old board-first path for
  this work. Board prerequisites are not prerequisites for working chat.
- The current vision expressly leaves the framework and evaluation method
  for discussion. The earlier D-54 prototype comparison is not imposed here.
- Earlier D-66 closed rooms at card Done. Current V-2 closes them when the
  PR closes; the detailed lifecycle needs settlement here.
- Earlier D-51's offline policy was for card moves. It does not settle
  free-text posting while disconnected.
- Earlier D-52 freshness thresholds and D-64 browser protections are relevant
  prior decisions to reconcile with this chat interface, not proof of a
  running implementation.

## Desired experience and scope

The owner chooses a project and room, sees the conversation and its users,
reads earlier messages and posts without changing devices or opening a
terminal. For milestone 1, selecting a user inserts their `@handle` into the
current draft and focuses the composer, preserving text and images (D-40).
The same features
are available on phone and desktop. Desktop has a narrow rooms column on
the left, a wider users column on the right, and the conversation in the
middle taking most of the screen. On phone, room and user lists open through
buttons (D-28).

Scrolling up loads older messages without moving the owner's reading
position. New arrivals leave that position alone; “Jump to latest” returns
to the bottom. When already at the bottom, follow new messages (D-39).

Pasting an image inserts it directly into the message being written. The
owner sees it inline while finishing the message, before pressing Send.
There is no separate image-preview dialog or caption step. An early slice
may show a placeholder link in the message; a later milestone 1 slice
replaces that display with the inline image (D-17).

In scope:

- Project main rooms.
- Work rooms for issues with their PRs, and standalone PRs.
- Project testing rooms, without designing the testing workflow.
- A system-wide general room, including design-agent conversations (D-16),
  and the existing `#chat-test` room.
- Live reading, posting and history as one milestone.
- File preservation of closed work-room logs with their images (D-20).
- A clean cutover per project: warn the owner, close non-exempt issues/PRs,
  archive legacy request rooms and restart work in issue/PR rooms. Only
  owner-named parked work keeps a marked legacy room (D-44). Permanent rooms
  stay; existing design rooms are discarded without saved logs (D-16).
- Each room's user list, role colors, and agent idle/working/waiting pips.
- Pasted image sharing with PNG support at minimum.
- V-12 reliability, a Python server, localhost listening and access through
  `tailscale serve`.

Out of scope:

- Milestone 2's archive browser/search, browser push, unread indicators,
  highlighted owner mentions and queued/delivered/accepted indicators.
- A current-task line under agent names: optional milestone-2 setting,
  with its default still undecided (D-30).
- The board and its workflow, reactions, and tracker mini-cards.
- Markdown rendering: deferred to a later milestone, with no milestone
  number selected (D-72).
- Automatic previews for outside image or website links (D-73).
- Dedicated design rooms (rejected by D-16).
- Custom rooms with their own rules: an owner-requested future requirement,
  deferred beyond milestone 1 with no target milestone or design selected
  (D-59). The milestone-1 creation restriction is a shortcut, not a permanent
  product limit.
- Designing testing-agent workflows, replacing IRC, or controlling sessions
  directly from the web application.
- Any live configuration, accounts, installation, schedules or service changes
  during design work.

Deferring delivery indicators does not defer durable delivery, duplicate-work
prevention or visible unresolved failures. Browser push belongs to milestone
2. Milestone 1 displays bridge-backed delivery errors (D-41/D-42) and keeps
the bridge's existing phone alerts unchanged (D-43).

## Decisions

D-1 through D-7 restate accepted source decisions. D-8 onward record the
owner's answers in this design conversation. None approves unspecified mechanisms or
delivery slices.
Earlier entries retain the questions that were open when they were recorded;
the current open-question resolutions and contract-gate review below identify
the later decisions that settle them.

### D-1. Deliver working chat together

Owner decision 2026-10-20, `vision.md`, Milestones: reading, posting and
history ship together on phone and desktop, including the V-2 room types,
file preservation of closed-room logs, room user lists and pasted images.
There is no read-only first milestone.

### D-2. Keep chat canonical in the skills repository

Owner decision 2026-10-19, `plateia_design.md`, D-78: chat code stays in the
skills repository. Plateia carries no copy. V-13 places handles, presence,
delivery, queuing, acceptance and execution in shared skills/services that
continue functioning when plateia is down.

### D-3. Use the existing IRC backbone behind one boundary

Owner decision 2026-10-02, V-3: IRC remains transport and record. IRC-specific
behavior is isolated so that replacing the transport does not change agents
or the interface. Images are stored by plateia, posted as links and displayed
inline. The exact interface and image lifecycle are unresolved.

### D-4. Post as the owner and separate delivery from authority

Owner decisions 2026-10-02 and 2026-10-20, V-5/V-6: plain project-room posts
go to the manager; mentions address sessions. General-room posts dispatch
only to mentioned users. Agents can address other users across projects.
Delivery grants no authority, and the web application does not execute work.

### D-5. Show real session presence and room users

Owner decisions 2026-10-02 and 2026-10-20, V-4: active project sessions,
including manually opened sessions, have stable handles. General lists all
active agents across projects alongside humans and assistants. Names have
role colors; agents have idle/working/waiting pips; humans and assistants
have no pip. A session that ends leaves the room.

### D-6. Trustworthy operation and private access are mandatory

Owner decision 2026-10-02, V-11/V-12: use a Python server and mature,
fully featured open-source tools, listen only on localhost and serve through
the private Tailscale network. Acknowledged requests survive restarts;
connections recover; stale/unknown state is explicit; retries start no
duplicate work; unresolved failures and incomplete actions remain visible.
D-47/D-48 add the owner's explicit exception for dismissing unconfirmed-send
notices while retaining the unresolved outcome in shared state.

### D-7. Leave framework selection and image details for discussion

Owner decision 2026-10-20, V-3/V-12: the owner and designer settle the page
framework and how to evaluate it, and discuss image upload/display. PNG is
required. At that point no candidate or upload mechanism was selected.
D-17/D-19 subsequently settle image composition/upload timing, and D-25
settles Svelte and validation in real chat slices.

### D-8. Standardize issue/PR rooms through the owner's global skills, at the room implementation step

Owner decision 2026-10-20, direct answer in this design conversation resolving
Q-1: issue/PR rooms become the standard room model. The design includes an
owner-specific global skill change in the skills repository, so the owner's
agents and interfaces use the same convention across projects. Plateia
displays that shared model rather than maintaining its own request-room
mapping.

Make the skill change when implementation reaches the room-model work, not
now and not as an advance prerequisite for unrelated milestone work.
The eventual delivery plan must include this change explicitly alongside the
affected room work and identify any necessary shared-service changes under
V-13. Exact skill files, migration and service mechanisms remain to be
designed. This decision does not authorize live changes during design.

### D-9. Create work rooms on first use

Owner decision 2026-10-20, direct answer in this design conversation:
create an issue or standalone-PR work room when discussion or work first
needs it, including issue review. Filing a tracker item alone does not
create a room. This resolves Q-2's opening rule and accepts P-2.

The opening rule belongs in D-8's shared room-model work and global skill
change. Closure, reopening and associations still need their own contracts.

### D-10. Close and preserve rooms for issues that close without a PR

Owner decision 2026-10-20, direct answer in this design conversation:
when an issue closes without ever having a PR, close its work room and
preserve its log in a file. This accepts P-3 and resolves that part of Q-2.
The rule applies to existing work rooms; it does not create a room merely
to close it for an unused tracker item.

This complements V-2's PR-close rule. Shared lifecycle detection and file
preservation must implement it with D-8's room-model work. Archive completion,
reopening and multiple-PR cases remain separate design questions.

### D-11. Associated PRs share the issue room; close it after the last open PR closes

Owner decision 2026-10-20, direct answer in this design conversation:
an issue's associated PRs share its room. Keep that room open while any of
those PRs remains open; close it and preserve its log when the last open PR
closes. This accepts P-4 and clarifies V-2's PR-close rule for multiple PRs.

For invented issue #41 with PRs #57 and #58, closing #57 does not close the
room while #58 is open. Reliable association detection and stale tracker
state handling remain to be specified.

### D-12. Include archiving existing rooms before adopting the new room model

Owner decision 2026-10-20, direct request in this design conversation:
the plan must include an initial archive/migration step for existing rooms,
before adopting the new room model. Previously this document mentioned
migration only as an open question; that was not a concrete rollout step.

The delivery plan must explicitly account for this work alongside D-8's
global skill change. This is a design requirement, not an instruction to
archive live rooms now. D-44 settles the clean per-project cutover and parked
exceptions, superseding general retention of active implementation rooms.
D-16's design-room discard still applies. It does not require milestone 2's
archive browser.

### D-13. Archive completed work rooms; discard existing design rooms

Owner decision 2026-10-20, amended by the owner's later answer:

- Archive completed implementation rooms after saving and verifying their
  logs. Finished means the PR is closed out; a room covering several PRs
  remains active while any associated PR is open.
- Keep permanent main/general/testing rooms. D-44 supersedes the earlier
  general retention of active implementation rooms: only owner-named parked
  exceptions retain their marked legacy room through cutover.
- Discard all existing design rooms at rollout, whether their work is
  finished or not. Do not migrate their conversations or save or verify
  their logs. This replaces the earlier all-issues-approved cleanup rule.

The design-room exception does not change issue/PR log preservation or
D-10's issue-without-PR rule. Inventory and archive files stay private.
Nothing live changes now.

### D-14. Archived rooms leave active lists and reject new posts until reopened

Owner decision 2026-10-20, direct answer in this design conversation:
accept P-6. A successfully archived room disappears from active room lists
and stops accepting new posts until reopened. Its verified log remains
preserved in files. Archive browsing remains milestone 2.

This is shared room behavior, not merely a hidden row in plateia. The
implementation must account for other chat clients. The transition into
that state, already-queued messages and reopening still need contracts;
this decision does not claim existing IRC tools implement them.
D-13's discarded design rooms are an explicit exception to log preservation.

### D-15. Show a grey room title while closure is being verified

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply to the closing-state question and confirmed in this design conversation:
accept P-7, with a grey room title while closing.

Stop accepting new submissions, settle messages already durably accepted
for posting, then preserve and verify the final log. Until that succeeds,
keep the room visible with its title grey. Show unresolved delivery or
archive problems rather than claiming closure succeeded. Once closure is
verified, mark it archived and remove it from active lists under D-14.

Owner amendment 2026-10-21, D-65: a dismissed unconfirmed send does not keep
closure pending indefinitely. Finalize from IRC history as it stands, with
an explicit archive note identifying each unresolved logical send ID and a
small gap marker on the closed room. Until dismissal, the grey closing rule
still applies. Other archive failures and missing referenced images remain
subject to verification; this exception does not turn an unavailable history
read into a complete archive.

This applies to normal closure and rooms being archived in the initial
cleanup; discarded design rooms require no saved or verified log (D-13).
An outage can keep
a finished-work room visibly closing for longer; already accepted messages
must not be lost. The exact posting barrier, history completeness evidence
and recovery protocol still need a technical contract.
D-20 requires the archive to include the conversation's images; verification
must cover those images as well as the log before closure succeeds.

### D-16. Design agents use general chat; no dedicated design rooms

Owner decision 2026-10-20, including the later clarification relayed by the
project manager: design agents use the system-wide general room. No design
rooms. Discard existing design rooms at rollout without migrating their
conversations or saving their logs (D-13). P-8 and P-9 are rejected.

General remains a permanent room. Completion of an individual design does
not close it. Its existing mention-only routing still applies: unmentioned
posts remain in the room without dispatch. D-8's owner-specific global skill
change must include this convention.

The general room does not exist yet; this design session continues in its
assigned existing channel for now. Provisioning general remains the owner's
live configuration action. Nothing live changes now.

### D-17. Paste images into the message; deliver the link before inline rendering

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply to the image question: pasting puts the image directly into the message
being written. Show it inline while the owner finishes typing, before Send.
There is no separate preview with an added-text step. Pasting does not send
the message.

Delivery may be split within milestone 1: an early slice inserts a
placeholder link for the pasted image; a later slice turns that into the
inline image. The placeholder is an intermediate presentation, not the
completed milestone's image experience. Plateia still stores images and
posts links through chat (D-3); inline rendering does not replace that
transport contract.

This settles composition behavior and the allowed delivery sequence, not
the complete issue boundaries. D-18 settles upload failure and D-19 settles
upload timing; D-20 settles sent-image retention and D-22 sets stored-image
limits. Input/processing limits and unused-upload cleanup remain open.

### D-18. Keep the whole message unsent when an image upload fails

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: accept P-11. If an image upload fails, keep the whole message unsent
in the composer so the owner can retry or remove the image. Show the failure;
do not silently post only the text or a broken image link.

This applies to both D-17's placeholder-link and inline-image stages.

### D-19. Upload on paste; post only on Send

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: accept P-12. Start uploading an image as soon as it is pasted, while
the owner finishes typing. The message is posted to chat only on Send.
An uploaded image is not itself a sent message.

This applies to both image delivery stages. Cleanup of uploads from
abandoned drafts remains to be specified.

### D-20. Retain and archive images alongside their conversations

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: accept P-13, and archive a room's images alongside its conversation.
Keep sent images for as long as their message logs. A room archive must
preserve the image files with the conversation, not just links to disposable
uploads. Image references must remain usable after archiving.

This is part of milestone 1 file preservation, not deferred to the milestone
2 archive browser. D-15's verification includes the images. The archive
format and how retained links resolve still need a technical contract.
Unused draft uploads are a separate cleanup question.

### D-21. Cap stored images on upload; fit their display to the column

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: shrink large images on upload to a fixed size or memory limit, not
the current chat-column width. Do not retain high-resolution originals.
D-22 records the approved limits.

Display the stored image proportionally within the chat column. A tap or
click opens that stored, capped image at full size. Resizing the window
changes display size, not the stored image. D-20's retention and archive
rules apply to this capped version.

### D-22. Cap each stored image at 1280 pixels and 1 MiB

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: accept P-15's caps. The longest side is at most 1280 pixels, with
proportions preserved and no upscaling. The encoded file is at most 1 MiB
(1,048,576 bytes); shrink further if necessary to meet both limits.

Store and archive only the capped image. These limits are independent of
window size and apply from the first image slice. Fine text in large
screenshots may require cropping before paste. D-69 bounds source bytes and
pixels; decoding and concurrency still need a bounded implementation because
the stored-file cap is not a total memory budget.

### D-23. Keep each room's unfinished message on the same device

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: accept P-16. Each room retains its unfinished message, including
images, across room switches and page reloads on the same device.

As clarified by D-33, Send clears the saved draft immediately; explicitly
discarding a draft also clears it. Draft
storage must preserve its attachments; unused-upload cleanup must not remove
images still belonging to a saved draft. The storage mechanism and recovery
of interrupted uploads are qualified by D-83: a reload before a capped copy
exists may require re-adding that image. Cross-device draft
synchronization is not part of this decision.

D-32/D-33 exclude failed-send entries from persistence and retain only one
temporary failure for retry. D-36 also excludes unconfirmed submissions
from browser persistence. Neither is another saved draft.

### D-24. Keep disconnected drafting minimal

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: allow typing while disconnected, with Send disabled until
reconnection. Treat this as a low-priority edge case.

Handle disconnected typing within normal composer and connection-state
behavior, without an offline send queue. D-34 supersedes the low-priority
framing for messages already submitted: send deadlines, authoritative
check/cancel and safe retry require proper design and network-fault coverage.
Local deployment is not a reason to weaken those guarantees.

### D-25. Use Svelte with Python, validated in real chat slices

Owner decision 2026-10-20, relayed by the project manager as an authenticated
answer to Q-7: use Svelte for the page. The owner rejected React as overkill.
Python remains the server. Validate the choice in the real phone/desktop
chat slices; no separate comparative prototype exercise is required.

The owner instructed the designer to settle the Svelte build setup and
editor integration and record the Node/npm toolchain, dependency pinning
and CI build step. D-26/D-27 record those technical selections under that
instruction. No installation or implementation is authorized in this session.

### D-26. Build a client-only Svelte/Vite page with a Tiptap composer

Design selection 2026-10-20 under D-25's explicit instruction:

- Use plain Svelte 5 with Vite and TypeScript in `web/`. The build produces
  `web/dist/` HTML, JavaScript and CSS, served by the Python application on
  the same origin as its API. Node is a development/build dependency, not
  a second production server. Development servers bind to localhost.
- Use the official `@sveltejs/vite-plugin-svelte`. SvelteKit's client-only
  static mode is viable, but its routing/loading conventions are unnecessary
  for the current chat page. Room URL and browser-history handling still
  need a small explicit contract.
- Use Tiptap 3's open-source core and ProseMirror packages through a Svelte
  component: create the editor on mount, track editor transactions, and
  destroy it on unmount. Enable only the message schema needed for text,
  paragraphs/line breaks, undo and image/link nodes; this does not add a
  document-formatting feature set.
- Handle pasted image files through plateia's upload API. Insert an
  attachment at the paste position and update that same attachment when
  upload completes; subsequent typing must not move or duplicate it. Use
  the same attachment identity in D-17's link-first and inline stages.
  The later image node uses inline rendering.
- Store a versioned draft representation rather than arbitrary editor HTML.
  Convert messages to text and image links at the chat boundary. The exact
  serialization and input validation are still Q-3 contracts.

These are design choices, not tested implementation claims. Svelte documents
[direct Vite builds](https://svelte.dev/docs/svelte/getting-started)
and [SvelteKit static SPA builds](https://svelte.dev/docs/kit/single-page-apps).
Tiptap documents [Svelte lifecycle integration](https://tiptap.dev/docs/editor/getting-started/install/svelte)
and an [inline image node](https://tiptap.dev/docs/editor/extensions/nodes/image);
the image extension displays images but does not implement uploading.
No paid editor service or cloud collaboration service is needed.

### D-27. Pin the frontend toolchain and include its build in `build-test`

Design selection 2026-10-20 under D-25's explicit instruction. Starting
versions, checked read-only against publisher release/registry metadata:

| Tool or dependency | Selected version |
| --- | --- |
| Node.js, LTS line | 24.22.0 |
| npm, bundled with that Node release | 11.19.1 |
| Svelte | 5.57.2 |
| Vite | 8.3.4 |
| `@sveltejs/vite-plugin-svelte` | 7.3.1 |
| TypeScript | 6.0.3 |
| `svelte-check` | 4.7.7 |
| Tiptap core, ProseMirror package and image extension | 3.32.0 |

The Svelte plugin's declared peers accept the selected Svelte/Vite versions.
`svelte-check` 4.7.7 declares TypeScript 5/6 support, so TypeScript 7 is not
selected. This is a manifest compatibility check; a real locked install and
build must pass in the first frontend PR.

Record Node's exact version in `web/.node-version`, npm's exact version in
`web/package.json` (`packageManager`), and verify both versions in CI.
Pin direct dependencies to exact versions without `^` or `~`; pin any
additional Tiptap schema packages to the same release. Commit
`web/package-lock.json` with transitive versions and integrity hashes.
Use `npm ci` for clean installs. Do not use floating `latest`, implicit
global tools or peer-dependency bypass flags in the build.

The first frontend code slice adds a `web` job to the existing CI workflow:

1. Check out the code; set up the exact Node version from the file and
   verify the bundled npm version.
2. In `web/`, run `npm ci`, then `npm run check` (Svelte and TypeScript
   checking), then `npm run build` (`vite build`).
3. Include `web` in `build-test.needs`, alongside the existing Python
   tests. The aggregate must fail for a failed, cancelled or skipped web
   job. Preserve the PR/master triggers and read-only permissions.

Use the same commands locally. Keep `node_modules/` and generated `web/dist/`
out of git; serve the output of the exact checked source build. Add focused
browser/editor tests with the interaction slices they verify, rather than
claiming a successful bundle proves mobile paste, draft recovery or history
behavior. Dependency updates are explicit code changes with regenerated
lockfiles and the same checks. Recheck releases at implementation if a
compatibility or security issue requires changing these starting pins.

Evidence: [Node release index](https://nodejs.org/dist/index.json),
[Svelte metadata](https://registry.npmjs.org/svelte/5.57.2),
[Vite metadata](https://registry.npmjs.org/vite/8.3.4),
[plugin metadata](https://registry.npmjs.org/@sveltejs/vite-plugin-svelte/7.3.1),
[TypeScript metadata](https://registry.npmjs.org/typescript/6.0.3),
[`svelte-check` metadata](https://registry.npmjs.org/svelte-check/4.7.7),
[Tiptap core metadata](https://registry.npmjs.org/@tiptap/core/3.32.0),
and [image extension metadata](https://registry.npmjs.org/@tiptap/extension-image/3.32.0).
The [npm CI contract](https://docs.npmjs.com/cli/v11/commands/npm-ci/)
requires a matching lockfile and does not rewrite dependency manifests.

### D-28. Give the conversation most of the screen

Owner decision 2026-10-20. Use a Discord-like desktop arrangement: a narrow
rooms column on the left, a wider users column on the right, and the chat
in the middle taking most of the screen. The relative widths are deliberate:
rooms are narrower than users, and chat remains the largest column.

On phone, show the conversation with buttons to open the room and user
lists. Preserve the same features on both layouts. Exact pixel widths and
the responsive breakpoint are implementation details to validate with real
chat content and resized windows, within these proportions.

### D-29. Keep role colors and use grey, green and amber status dots

Owner decision 2026-10-20. Agent status dots are grey for idle, green for
working and amber for waiting on the owner, alongside the existing role
colors. An amber waiting dot beside a yellow designer name is acceptable;
the role and state do not need different hues.

The role colors remain manager purple, solver orange, reviewer red,
designer yellow, guide blue, assistant green and owner cyan. Human users
and assistants have no status dot (V-4). Provide a readable status label
as well as color on phone and desktop. Missing or stale evidence is not
idle and must not acquire a grey idle dot by default.

### D-30. Defer current-task lines to an optional milestone-2 setting

Owner decision 2026-10-20. Do not show a current-task line beneath agent
names in milestone 1. Note it only as an optional setting for milestone 2;
its default is not decided. This narrows the initial presentation of V-4
without removing milestone 1's real session presence, role colors or
idle/working/waiting dots. No milestone-1 slice includes the task-line
setting or its presentation.

### D-31. Enter sends on desktop; phone uses the Send button

Owner decision 2026-10-20. On desktop, Enter sends the message and
Shift+Enter adds a new line. On phone, Return adds a new line and tapping
Send posts the message. Keep the Send button available on desktop too.
Keyboard submission uses the same upload-readiness and connection rules
as the button. Confirming a character through an input method must not
accidentally send the message.

### D-32. Use quiet sending text and a temporary retry icon on failure

Owner decision 2026-10-20. While sending, put small grey “sending...” text
below the message, not in a bubble. On failure, keep the message in this
app's chat window with a red exclamation mark. Clicking or tapping that
mark retries the send.

Failed sends are temporary local entries for retry, not saved or persisted
conversation records. Their contents must not be restored as failed entries
after a reload. This decision does not remove V-12's durability requirement
for requests the shared service has already acknowledged. D-33 settles the
saved-draft transition and when the temporary failed entry is removed.

The owner did not approve the proposed “Checking” state. D-34 instead
requires a countdown below an unconfirmed message and an authoritative
server check at its deadline.

### D-33. Clear the draft on Send and retain only one temporary failure

Owner decision 2026-10-20. Send clears the saved draft immediately. At most
one failed send is shown in the app's chat window. Any subsequent message
clears the previous failure, even when the newer message also fails: the
newer failure replaces it. There is no saved failure list or backlog of
retry entries.

This refines D-23 and D-32. Unsubmitted drafts still survive room switches
and reloads; failed sends do not. A disabled Send action does not submit or
clear a draft. The rule concerns temporary failed-send feedback, not the
durable record of requests the shared service has already acknowledged.

### D-34. Resolve send deadlines by message ID at the server

Owner decision 2026-10-20. Each message has a browser-created ID. When
confirmation is missing, show small grey “sending... (N)” text below the
message, counting down to a fixed deadline. At zero, ask the server about
that ID:

- If posted, display the message normally. A late confirmation does not
  justify hiding a message already in the conversation record.
- If not posted, the server cancels the pending submission before returning
  that result. Late arrival of the original request cannot then post it.
  Only after that authoritative result does the browser show the red retry
  icon, subject to D-33's single-failure rule.
- Retry uses the same logical message ID. It must post at most once,
  including when requests, responses or retries are delayed or repeated.

Design for real latency, lost replies, reconnects and late requests even
though deployment is local. This supersedes D-24's minimal treatment of
send recovery and P-25's indefinite plain sending text. Disconnected typing
remains minimal; send recovery is a required, fully tested contract.

The owner delegated the countdown duration. Design selection: **10 seconds
per send attempt**, measured from dispatch with a monotonic clock; display
the rounded-up remaining seconds, clamped at zero. Reconnects and background
tab suspension do not restart that attempt's deadline. D-45 supersedes the
earlier publication-expiry interpretation: this is the single sender's
decision cutoff, after which it starts no IRC write for that attempt. A
write started before cutoff can remain in flight; D-35 covers the unknown
outcome; D-47 allows it to remain unconfirmed when proof cannot be recovered.
D-79/P-68 supplies the host/browser clock mapping and permits conservative
earlier expiry; no network delay can extend the ten-second budget.

The shared sender contract requires a serialized check-and-cancel operation,
not a separate unsynchronized read followed by cancellation. It covers an ID
whose original request has not arrived yet. Durable cancellation and
deduplication metadata are distinct from D-32's unsaved failed-message UI;
they must survive service restarts to reject late requests. They are not a
saved failure conversation or an automatic retry queue.

An explicit retry must be distinguishable from delayed delivery of the
cancelled attempt while retaining the same logical ID. The single sender
must fence old attempts and serialize retry, cancellation and IRC writes.
A local timeout, missing bridge log entry, or successful socket write is
not authoritative proof of the IRC publication outcome. Q-3 must establish
that proof at the actual publication boundary; do not claim the existing
sender already provides it or substitute text matching for message IDs.

### D-35. Mark an unanswered deadline check as unconfirmed

Owner decision 2026-10-20. At the send deadline, keep small grey
“sending... (0)” text while awaiting the server's authoritative result.
There is no retry action. After a few more unanswered seconds, replace
that text with an explicit unknown state, still without retry. Once the
server confirms posting, show the message normally; once it confirms
cancellation, show the red exclamation retry mark under D-32/D-33.

The owner delegated wording and delay. Design selection: **5 seconds after
the 10-second deadline**, replace the text with **“unconfirmed: awaiting
server”**. This is 15 seconds after dispatch, not five seconds after each
reconnect or status request. Keep the same small grey placement below the
message. The wording reports missing confirmation without claiming a slow
server is necessarily unreachable. It also applies while the server is
resolving an uncertain IRC publication outcome.

Continue recovery when connectivity returns. Elapsed time alone never
turns this state into failure or enables retry. A later authoritative
answer settles it immediately. This is the V-12 unknown state, not a
milestone-2 delivery-progress indicator.

### D-36. Do not restore unconfirmed sends; enforce expiry on the server

Owner decision 2026-10-20. Unconfirmed sends do not reappear after a reload.
Persist nothing about them in the browser: no pending-message content,
ID, deadline or recovery entry. Send still clears the saved draft at once
(D-33). Unsubmitted drafts remain covered by D-23.

Each send carries its deadline. D-45 clarifies enforcement: the shared
single sender refuses to start an IRC write after the cutoff, but a write
already started can still be in flight. After reload, **once the prior
attempt has settled**, the message is in authoritative IRC history or will
never be posted. If missing then, the owner retypes it. Expiry alone does
not prove absence during the in-flight window. There is no automatic
reconstruction or resubmission of the discarded local entry. D-35's
message-level feedback applies while that entry remains in the current page;
D-46 addresses the reloaded room's history state.

This supersedes P-27. It does not remove server-side cancellation and
deduplication records required by D-34. The deadline belongs to a send
attempt; an explicit retry in the still-open page retains the logical ID
and has its own deadline. An expired original attempt that has not started
writing must remain unable to post, even after a retry is authorized.

Q-3 must cover requests queued before expiry, writes in flight at cutoff,
sender restart, clock disagreement and history lag. The sender's expiry
rule must work without a surviving browser to request cancellation. A stale
or incomplete history view cannot prove a message missing: retyping safely
depends on settled authoritative history, as clarified by D-45. D-47 accepts
that some outcomes cannot be proven: keep them unconfirmed without retry;
the owner may dismiss the notice and independently choose to write a new
message. Never claim that dismissal proves the original was not delivered.

### D-37. Preserve long posts using explicit transport grouping

Owner decision 2026-10-20. Long pasted text stays one message in the web
chat, with its line breaks preserved. The transport must explicitly mark
which IRC lines belong to that message, using IRCv3 multiline batches or
an equivalent. Reassembly must never merge separate posts just because
they share a sender, arrive close together or look like continuation text.
Agent and terminal views can retain their line-oriented presentation;
those displayed lines are not independent message identities.

Verified support and limits on 2026-10-20:

- Ergo advertises `batch`, `message-tags` and `draft/multiline` in a read-only
  capability query. This establishes advertised support, not a live posting
  or deadline test. No account login, room join or test post was needed.
- Shared `chatlib.post` negotiates those capabilities, emits `BATCH` framing
  and uses `draft/multiline-concat` for wrapped pieces of one text line.
  The bridge's `join_multiline` and receive loop collect by batch reference
  and process on the closing marker, including batches nested in history.
- The existing `SendMultilineTests` and `MultilineTests` pass: 10 isolated
  tests covering sending, live/history reassembly and related recovery.
  They establish current behavior, not completion of the new contract.
- `multiline_batches` splits text exceeding negotiated byte/line limits
  into separate batches with added continuation text. Each is a separate
  record; no logical message ID connects all parts. It also strips outer
  blank lines and can alter whitespace at batch boundaries. Synthetic
  read-only checks confirm ordinary paragraphs round-trip but outer blank
  lines and oversized posts do not round-trip exactly.
- If multiline is unavailable, the sender falls back to independent
  `PRIVMSG` lines. That fallback cannot satisfy this web contract without
  equivalent explicit grouping. The `post` docstring's unlimited single
  batch claim is broader than the implementation.

Required shared-contract changes: preserve the exact message text and
line breaks; carry an authenticated logical identity through live delivery
and history; distinguish ordered parts and completion if multiple batches
are needed; and reconcile that identity with D-34's browser-created ID.
Batch references alone are connection-scoped framing, not durable logical
IDs. Never infer cross-batch membership from timing or continuation text.
Only a complete logical message may be presented or routed as the submitted
instruction. Missing, repeated, interleaved or replayed parts must not
create partial instructions, duplicates or merged independent posts.

Capability negotiation, support for D-38's approved size limit, any equivalent
fallback and multi-batch completion must be settled together with D-36's publication
deadline. Plain line fallback cannot silently weaken the guarantee.
These changes belong in shared chat; plateia consumes the grouped contract
behind its IRC boundary rather than inventing its own grouping heuristic.

Protocol evidence: the [IRCv3 multiline specification](https://ircv3.github.io/specs/extensions/multiline.html)
defines batch framing, concat tags, completion and negotiated limits;
[Ergo's changelog](https://github.com/ergochat/ergo/blob/master/CHANGELOG.md)
records support for `draft/multiline`. The extension still uses draft names;
implementation must negotiate the actual supported capabilities.

### D-38. Limit each message to 64 KiB of text

Owner decision 2026-10-20. Approve a 64 KiB per-message text limit, with a
warning before Send if exceeded. The limit is 65,536 bytes of UTF-8 message
content, including serialized attachment links as specified in P-29. Image
files themselves retain D-22's separate limits.

Check the complete serialized text before submission. If over the limit,
show a clear warning and retain the draft for editing; do not submit or
silently truncate it. An over-limit attempt does not clear the saved draft
or start a send deadline. The server enforces the same limit.

This is the logical message limit, independent of individual IRC batch
limits. It does not establish that the existing sender supports the full
size under D-34/D-36's deadline and at-most-once requirements. D-37's shared
contract must provide that support.

### D-39. Keep the reader's place while loading history

Owner decision 2026-10-20. Scrolling up loads older messages and preserves
the visible reading position. While the owner is reading earlier messages,
new arrivals leave that position alone. “Jump to latest” returns to the
bottom; when already at the bottom, follow new messages automatically.
Use the same behavior on phone and desktop.

This is milestone-1 history navigation. It does not add unread counts,
mention badges or milestone-2 notification features.

### D-40. Selecting a user inserts a mention into the current draft

Owner decision 2026-10-20, scoped to milestone 1. Selecting a user inserts
their `@handle` at the cursor in the current draft and focuses the message
box, keeping existing text and images. It does not replace the draft,
change rooms or send anything. The owner finishes editing and uses the
normal Send action. Apply this from the desktop user list and the phone
user panel alike.

This settles the milestone-1 interaction only. Later milestones may revisit
it; this decision does not introduce a separate conversation or user-detail
workflow.

### D-41. Show posted-message delivery failures from the bridge's record

Owner decision 2026-10-20. A message that was posted but cannot reach its
addressed agent keeps a delivery error beside it until resolved, including
across room switches and reloads. Later messages do not clear it.

The error must come from the chat bridge's own delivery/acceptance record.
Plateia must not guess from an idle session, elapsed time, lack of a reply
or a missing record. If the required record cannot yet be read through the
shared contract, add that capability in the skills repository under V-13.
The owner also requires an explicit definition of resolution; D-42 settles
it as acceptance by the intended agent, with no dismissal.

The shared code already records delivery attempts, terminal dead letters
and authenticated replies/acknowledgements. A successful transport attempt
is not necessarily recipient acceptance. The current attempt record lacks
a structured recipient field, and the acceptance method removes matching
pending items; later acknowledgements after dead-lettering must be joined
with retained recipient evidence. The existing textual `pchat trace` output
is not the structured interface plateia needs.

Required skills-repository work: expose a typed outcome keyed by logical
message and intended recipient, with failure reason, source evidence,
freshness and resolution evidence. Preserve enough routing identity to
reconcile late acceptance after a delivery leaves the pending queue and to
distinguish several recipients of the same message. Do not infer success
from removal from that queue. A missing or stale outcome is unknown/stale,
not proof of delivery or resolution.

This is distinct from D-32/D-33's temporary failed-send entry: the message
is already in the conversation record. Its delivery error does not invite
posting a duplicate instruction. Milestone-2 progress indicators and browser
push remain out of scope.

### D-42. Only the intended agent's acceptance resolves a delivery error

Owner decision 2026-10-20. Clear a recipient's delivery error only when the
bridge records authenticated acceptance by that intended agent for the
same message. The owner cannot dismiss it. For several recipients,
acceptance resolves only the accepting recipient's error; unresolved
recipients remain visible. Keep the underlying failure and acceptance
evidence in the shared record.

If an agent is gone, the owner can resend the message or redirect it to
another agent. That is an explicit new post with its own message ID and
delivery tracking. The original keeps its error, even if the new copy is
accepted. Plateia must not silently retarget the original, automatically
resend it or treat another agent's acceptance of a new copy as resolution.

This is distinct from D-34's same-ID retry of a send confirmed cancelled
before posting. Here the original was already posted and the owner chooses
to make a new copy. A later unrelated reply, successful transport attempt,
agent appearing online or disappearance from the pending queue does not
resolve the original delivery error.

### D-43. Keep existing bridge phone alerts unchanged for milestone 1

Owner decision 2026-10-20, scoped to milestone 1. Keep the chat bridge's
existing phone alerts for failed or stalled delivery alongside the delivery
error in the chat window. Make no change to bridge notification behavior:
retain its triggers, timing, routing and content policy. Plateia does not
send a second alert or add alerts for individual transport retries.

This decision creates no bridge notification change or live configuration
work. D-41's required shared read interface remains part of the delivery
contract. Browser push stays in milestone 2. Dismissing an existing phone
notification does not resolve the chat error; D-42 still requires acceptance
by the intended agent.

### D-44. Cut over each project cleanly, with named parked exceptions

Owner decision 2026-10-20, replacing P-35's gradual migration. Before the
new issue/PR room model goes live for a project:

1. Its manager warns the owner about that project's cutover.
2. The owner closes every open issue and PR except parked items the owner
   explicitly names as exceptions.
3. Archive that project's legacy request rooms through the normal archive
   path. The existing design-room exception remains D-16: discard those
   rooms without migration or saved logs. Permanent rooms remain.
4. Switch the project to the new model and restart work in issue/PR rooms.

A named parked issue/PR keeps its existing room in place, visibly marked
as a legacy room, until its work finishes. This is the explicit exception;
ordinary unfinished work is not grandfathered into old rooms. Preserve the
exception's tracker association so it does not acquire a duplicate room.

The owner accepted some historical loss during cutover and delegated rollout
details to the project manager. Preserve the available record through the
normal archive path and describe retained coverage honestly; do not require
perfect historical reconstruction as a migration project. This tolerance
is for legacy cutover, not permission to silently drop new messages or
images under the new model.

The manager's delegated rollout design is a **per-project switch in shared
chat configuration**. Each project has its own warning and owner cutover;
other projects remain on their existing convention until theirs occurs.
D-8's owner-specific global skill change must honor this switch, including
parked exceptions, rather than forcing every project to change at once.
The switch and room lifecycle belong in shared chat, not plateia mapping.

No live rooms, tracker items or configuration change during this design.
The project's rollout warning and owner closure step happen when the
implemented room-model change is ready to activate.

### D-45. Use one sender and IRC history; never patch the IRC server

Owner decision 2026-10-20. Reject P-36. The owner will not maintain an IRC
server patch. Design the no-patch approach first:

1. Use a single sender for owner posts. It never starts an IRC write for an
   attempt after that attempt's deadline. The deadline is the writer's
   decision cutoff, not a guarantee of IRC publication by that instant.
2. Carry every message's logical ID into IRC, for example in a client-only
   message tag. IRC history is authoritative. If an outcome is unclear,
   look for that ID: found means posted; absent **once settled** means
   cancelled. A missing bridge-log entry is insufficient.
3. A retry keeps the same logical ID and checks IRC history before writing.
   Serialize retries through the same sender so they cannot duplicate work.
4. Cover the in-flight window with D-35's unconfirmed state. Do not claim
   cancellation or enable retry before settlement.

This supersedes D-34/D-36's strict publication-deadline interpretation while
preserving their ID, retry and no-browser-persistence decisions. Verify
installed-server support read-only. If the no-patch approach cannot meet
the contract, bring the evidence and a different IRC server back to the
owner as a decision. A maintained patch is never the fallback. No server
replacement or live configuration/service change is authorized now.

Read-only verification:

- The installed binary reports **Ergo 2.19.1**. A client using the designer's
  own identity negotiated `message-tags`, `batch`, `draft/multiline`,
  `echo-message`, `labeled-response`, `draft/chathistory`, `server-time` and
  `account-tag`.
- A history query without room membership was refused. A temporary reader
  in the existing design room obtained labeled history responses containing
  account, server message ID, time and an existing client-only reply tag.
  Repeating the read retained that tag's value; no private values or message
  contents are reproduced here. The reader left after the queries.
- A mid-window native `msgid` history query found the selected message.
  An initial edge query did not; its cause was not established. The release's
  `GenericAround` also has a documented first-item edge case. Recovery must
  use complete pagination, not an `AROUND` result as proof of absence.
- In the matching release, history items contain a generic tag map and
  serialize it into the history store. CHATHISTORY selectors accept server
  `msgid` or timestamp, not arbitrary client-tag values. Logical-ID lookup
  therefore requires scanning authoritative, fully paged history and
  matching the tag, or an equivalent shared index verified against history.
- These checks establish the capability path, not a tested implementation
  of the new sender. No test post with the proposed ID tag, process crash,
  server restart or config change was performed. Durable retention, the
  exact new tag round-trip and the old-write settlement barrier remain
  required implementation validation.

Sources for the installed release: [history selectors](https://github.com/ergochat/ergo/blob/v2.19.1/irc/handlers.go#L811),
[history item tags](https://github.com/ergochat/ergo/blob/v2.19.1/irc/history/history.go),
[serialization](https://github.com/ergochat/ergo/blob/v2.19.1/irc/history/serialization.go),
[SQLite history storage](https://github.com/ergochat/ergo/blob/v2.19.1/irc/sqlite/history.go),
and [AROUND implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/history/queries.go).

### D-46. Bound history syncing after reload, then show unconfirmed

Owner decision 2026-10-20. Accept P-38 with a fixed time limit. After reload,
show the room-level **“Syncing history...”** notice while a prior write is
unsettled. Never restore its pending message, content or retry entry. If
recovery cannot finish within the bound, replace the syncing notice with an
explicit unconfirmed notice rather than leaving it apparently progressing
forever.

The owner delegated the bound. Design selection: **15 seconds from the
room's first history request in the loaded page**, then small grey
**“unconfirmed: awaiting history”**. This uses D-35's total waiting duration
and unknown-state wording, with room-level placement. Repeated requests,
reconnects and room switches do not restart the timer within that page.
Account for elapsed time when a suspended page resumes. No per-send browser
storage is introduced; a new page starts a new bounded history request.

The timer changes the notice only. It does not prove cancellation, enable
retry or declare an absent message safe to retype. Continue recovery; remove
either notice once the prior write is settled and authoritative history has
caught up. A server that remains unreachable leaves an honest unconfirmed
notice, not an endless syncing state. These are history-recovery notices,
not milestone-2 unread or delivery-progress features.
D-47 additionally permits the owner to dismiss an unconfirmed notice.

### D-47. Keep Ergo; permit dismissal of an unprovable-send notice

Owner decision 2026-10-20. Accept P-39. Keep Ergo, with no server comparison
or patch. When recording or settlement cannot be proven, the send remains
**unconfirmed**, potentially indefinitely. There is no automatic resend or
same-ID retry, and no claim that retyping is safe.

The owner may **dismiss** an unconfirmed notice that cannot settle. Dismissal
only removes that notice. It never sends anything, changes the original
outcome to posted/cancelled, or authorizes retry. Preserve the shared sender's
unresolved record so a delayed request cannot become a new send merely
because its notice was dismissed. A later authoritative posted result remains
part of normal history.
For room closure, D-65 additionally permits finalizing the current-history
archive after dismissal, with a named-ID gap note and closed-room marker;
the send's outcome itself remains unconfirmed.

If the owner chooses to retype and Send, that is a **new message with a new
logical ID**. It is independent of the original; its ID cannot deduplicate
against that original. Do not imply this choice is safe from repeating work
the original might already have caused. Do not provide a hidden resend in
the dismissal action or restore the original into the composer.

This applies to the unconfirmed-send/history notices in D-35/D-46. D-42's
posted-message agent-delivery error still clears only on agent acceptance;
that error remains non-dismissible. D-36's prohibition on browser persistence
of pending sends remains in force. D-48 settles how dismissal survives reload.

### D-48. Remember dismissed notices across reloads and devices

Owner decision 2026-10-20. Accept P-40. The shared sender remembers dismissal
with the unresolved send record, so that notice stays dismissed across
reloads and on the owner's other devices. No pending-send or dismissal
recovery data is persisted in the browser.

Dismissal applies only to the particular unresolved send. New unconfirmed
sends still show their own notices, including a fresh room-level notice
where appropriate. Do not implement dismissal as a permanent mute for a
room. The attempt record and its protection against duplicate writes remain;
a later confirmed message appears normally in history. D-47's separation
between hiding a notice and resolving the outcome continues to apply.

### D-49. Delete provably unused image uploads after 24 hours

Owner decision 2026-10-20. Accept P-41. Delete unused uploads **24 hours after
they become unreferenced**. Keep anything still needed by a saved draft, send
or retry, unresolved send, posted message or archive. Keep an image whenever
it cannot be proven unused. Inactivity or an offline browser is not proof.

Removing an image from one draft or discarding one retry entry releases only
that reference. Other references continue to protect the stored image. Saved
drafts do not expire just because they have not been opened. Interrupted
uploads and uncertain draft reference state must be reconciled before cleanup
can classify their images as unused. Recheck references before deletion;
new use during the grace period prevents cleanup.

Include reference tracking and cleanup in the first image-storage slice,
alongside D-20/D-22's retention and storage caps. This is the future behavior;
no live uploads or files are deleted during design.

### D-50. Obtain exact-wording approval before future vision edits

Owner decision 2026-10-20. The owner approved after the fact the V-12 edit
published in `9d75f41098`, which records the D-47/D-48 exception for remembered
dismissal of unconfirmed-send notices. Before **any future vision edit**,
present the exact proposed wording to the owner and obtain approval first.
Do not infer that approval from a design answer or approval to record it here.
This turn records the rule in this document; it does not change the vision.

### D-51. Reopen the existing room when PR work resumes

Owner decision 2026-10-21. Accept P-42. A reopened PR or a replacement PR
for the same issue reopens that issue's existing room, preserving its
conversation and images. A reopened standalone PR likewise reuses its
original room. This matches the earlier reopened-issue rule and D-11's
shared room for associated PRs; a new attempt does not create another room.

If no room has existed, first-use creation still follows D-9. If closure is
in progress, reconcile resumed work before completing the archive transition.
Preserve history already archived; a stale closure operation must not hide
the room after it has become active again. Subsequent closure follows the
same D-10/D-11/D-15 lifecycle and preserves the continued conversation.

### D-52. Use the main room for project work without an issue or PR

Owner decision 2026-10-21. Accept P-43. Project work with no issue or PR
uses the project's permanent main room. Do not open a temporary work room
for it. Design conversations continue to use general under D-16.

If an issue or PR later follows, use its work room on first use under D-9
and link back to the earlier discussion. Keep the original messages in their
original room, without copying or migrating the conversation. Include this
convention in D-8's shared room-model and owner-specific global skill change
when that work is implemented. It does not change live room use now.

### D-53. List all active agents in the shared chat-testing room

Owner decision 2026-10-21. Accept P-44. `#chat-test` lists all active agents
across projects, as general does, using the same shared session evidence,
stable handles, role colors and agent status pips. A session that ends leaves
the list; uncertain evidence follows the established freshness rules.

This changes the **list only**. It does not change routing or authority,
add a testing workflow, or require each listed agent to hold an IRC connection.
Owner presence and room opening follow D-54/D-55/D-56; assistant presence
follows D-57.

### D-54. Show the owner only from real logged-in room membership

Owner decision 2026-10-21, correcting P-45. The owner appears in a room's
user list only when logged in and joined to that room, based on real IRC
membership of the configured owner accounts. Do not insert a self entry
in every room merely because the owner opened the application.

Different owner accounts can have different joined rooms. Read each account's
actual membership; do not infer the phone's rooms from the desktop's rooms
or treat account registration as a live login. Account mappings and live
membership stay in private configuration/state; public examples use invented
accounts. The owner retains the owner role color and has no status pip.

The shared IRC boundary supplies membership and its freshness to plateia.
Missing or stale evidence must not be shown as a current join. D-55 keeps
owner accounts as separate entries; D-56 makes opening a room join it as
the owner. D-57 lists assistants by their live sessions without requiring
an IRC join.

### D-55. Keep owner accounts as separate user-list entries

Owner decision 2026-10-21, answering P-46. Keep the two owner accounts
separate, as they are now. Show each account as its own entry when that
account is logged in and joined to the room under D-54. If both are present,
show two entries; one leaving does not remove the other's entry. Preserve
the real account attribution of messages. Both retain the owner role color
and no status pip. Do not aggregate them into one displayed identity.

### D-56. Opening a room joins it and creates it on first use if missing

Owner decision 2026-10-21, answering P-47. Opening a room in plateia joins
it as the owner, so the corresponding owner account appears after its real
membership is established. If the room does not yet exist, opening creates
it, fitted to D-9's create-on-first-use rule and the shared room model.
Opening is not a read-only operation. A failed or unconfirmed join must not
be presented as successful membership.

Creation must use the shared room identity/naming and lifecycle rules;
an absent live IRC channel is not proof that no logical room exists. Preserve
archived history and images, and do not bypass D-14's rejection of posts until
reopening or D-51's reuse of an existing room. This does not authorize arbitrary
room names, new room types or live configuration changes during design.
Creation eligibility for milestone 1 follows D-59. D-58 settles opening
an archived room for milestone 1. D-60 settles switching and explicit
join/leave controls. Define connection ownership and reconnect lifetime in
the shared contract.

### D-57. Include live assistants in every room without an IRC join

Owner decision 2026-10-21. Accept P-48. Assistants appear in every room's
user list while their session is live, using shared session records. No IRC
join is required. Keep their assistant role color and show no status dot.

An ended session leaves the list. Registration alone is not evidence that
a session is live; stale or unknown evidence follows the existing freshness
rules. The shared service supplies this information. Plateia does not inspect
cmux or run session discovery itself. This presence rule does not change
assistant authority, message routing or connection ownership.

### D-58. Show an archive notice without joining, in milestone 1

Owner decision 2026-10-21, explicitly scoped to **milestone 1**. Accept P-49.
Opening an archived room through an old link shows **“This room is archived.”**
without joining or reopening it. Do not offer Send or recreate a missing IRC
channel for that archived room. Its log and images remain preserved; archive
browsing remains milestone 2.

This is the milestone-1 exception to D-56's normal open/join behavior. A room
reactivated by reopened issue/PR work follows D-51 and can then be opened and
joined normally. A room still closing follows D-15 until archive verification
finishes. This decision does not settle milestone 2's archive-opening UI.

### D-59. Limit creation for milestone 1; defer custom rooms and rules

Owner decision 2026-10-21. Accept P-50 as a **milestone-1 shortcut only**.
Plateia creates configured permanent rooms and work rooms for real issues/PRs
using the shared room identity and lifecycle. It does not create freely named
rooms in milestone 1. Associated PRs use their issue's room; standalone PRs
have their own room. Existing parked legacy rooms retain D-44's exception.

Resolve eligibility through shared configuration/tracker/lifecycle records
before creation. Unknown evidence is not permission to invent a room, and
an absent live channel does not erase an archived room identity. Keep D-58's
archived-link behavior. Adding permanent rooms to live configuration remains
the owner's rollout action, not an action performed during this design.

**Deferred future requirement:** the owner wants custom rooms with their
own rules. Record that intent without designing the rules, editor, storage,
routing or implementation now. It is outside milestone 1, with no future
milestone assigned. Do not turn this shortcut into a permanent restriction
or add custom-room machinery to the milestone-1 slices.

### D-60. Preserve joins on room switches and provide Join/Leave controls

Owner decision 2026-10-21. Accept P-51 with the owner's addition. Switching
rooms changes the visible conversation only; it does not leave rooms already
joined. Every room page has a **Join/Leave** control. **Leave sends IRC PART**
and removes that owner account from the room's user list when confirmed.
Join establishes real membership. Preserve the other owner account's separate
entry if that account is still joined.

The guiding principle is that room functionality mirrors IRC for the most
part. Apply the explicitly approved exceptions, including D-58's milestone-1
archive rule: an archived page must not enable Join, reopen the room or create
a replacement channel. Show its archive notice and an unavailable Join
control. This principle does not add other IRC commands or features to scope.

After an explicit Leave, do not immediately rejoin merely because the same
page rerenders. Keep the button consistent with authoritative membership;
failed or unknown join/part outcomes must not be shown as success. Leaving
does not delete the saved room draft, conversation or images. These controls
must work on phone and desktop. No live joins, parts or connection changes
are performed during design.

### D-61. Use separate phone and desktop owner accounts with equal authority

Owner decision 2026-10-21. Reject P-52. From the owner's phone, plateia joins,
leaves and posts as the phone's existing IRC account; from the desktop, it
acts as the desktop's existing account. Both have the same owner authority
and permissions. Keep their separate membership entries and real message
attribution under D-54/D-55. Real account mappings remain private.

The owner delegated the browser-to-account mechanism for design to settle;
bring back only a material owner choice. D-62 records that selection.
**Owner update 2026-10-21:** the phone account is now an owner in shared chat;
the owner-authorized configuration change and shared support in skills commit
`7ce2401` are complete. They are no longer pending rollout actions. Consume
the canonical `chatlib.owners(cfg)` set (`owner` plus `owners`) for every
owner account's authority, rather than duplicating a two-account permission
list in plateia. Browser profiles select attribution and cannot grant authority.

Browser binding and sender/membership integration remain implementation work.
At runtime, if a profile's account is absent from the shared owner set, block
its account actions rather than posting as the other account or silently
giving it lesser authority. This is a configuration validity check, not an
outstanding request to add the phone account. No further live configuration
change is performed by this design session.

D-45 still requires one shared sender coordinating writes; it can authenticate
separate IRC connections for the two accounts. Bind every logical message ID
and attempt to its actual source account. History lookup, retry and membership
actions must retain that identity; a reconnect or browser-profile change must
not move an existing operation to the other account.

### D-62. Bind each browser to a remembered Phone or Desktop profile

Design selection under the owner's 2026-10-21 delegation in D-61; this is
the chosen mechanism, not a claim of deployed behavior:

- On first use, after the existing owner-access check, choose **Phone** or
  **Desktop** once for that browser. The server maps that profile to the
  privately configured owner account, validated against the canonical shared
  owner set already supported by `7ce2401`. Setup does not add accounts or
  alter their authority. Show the current account alongside the composer so
  attribution is apparent before Send.
- Keep an opaque browser-binding token in an HttpOnly, SameSite cookie,
  Secure on the deployed HTTPS origin. Store its account/profile mapping
  server-side. Reloads retain the binding; a new browser or cleared cookie
  requires setup again. Do not guess from screen width, user-agent strings
  or responsive layout, and do not silently fall back to the other account.
- Continue enforcing the existing localhost/Tailscale identity, Host and
  Origin requirements. Profile selection is a protected same-origin mutation
  and can select only the configured owner profiles. The binding selects
  attribution; shared chat remains the authority on permissions. No IRC
  password enters the browser, and no new user-registration system is added.
- Resolve joins, parts and sends from the server's binding, not an account
  name supplied with each request. Bind an accepted operation immutably to
  that account; later binding changes cannot alter its sender or retry it
  under another identity. Membership is independent between the two accounts.

This cookie is browser setup state, not storage of pending messages; D-36's
ban on persisting unconfirmed content, IDs and deadlines remains intact.
Persisting drafts remains governed by D-23.

Evidence for avoiding inference from Tailscale user identity: Serve provides
the authenticated user's identity, and Tailscale's example states that the
same user's other devices receive the same identity headers. It therefore
does not by itself distinguish Phone from Desktop.
[Serve identity headers](https://tailscale.com/docs/features/tailscale-serve#identity-headers),
[official identity-header example](https://github.com/tailscale-dev/id-headers-demo).

### D-63. Keep account sessions and membership behind every interface

Owner decision 2026-10-21. Accept P-53. Each owner account stays logged in
and joined behind plateia whether or not its browser is open. Closing or
suspending the interface is not PART or logout; reopening resumes the
account's existing membership. Its room-list presence means joined, not
currently viewing. A real loss of authoritative IRC state remains stale or
unknown under the existing freshness rules.

Membership is shared with the owner's other IRC clients on the same account.
Join or Leave in plateia changes that account's membership everywhere;
actions from another client must likewise update plateia. The two different
owner accounts remain independent. Do not maintain a competing browser-only
join list or automatically undo a PART received from another client. D-56's
explicit room opening and D-60's Join control remain ways to join again;
a rerender or background refresh is not a new request to join.

Use the installed Ergo's multiclient attachment, verified read-only above:
authenticate the shared service's connection as the selected account during
registration so it attaches to the same identity. Keep this service lifetime
independent of browser connections and the web application. No Ergo patch or
replacement membership service is needed. Retention with no connected IRC
clients is a separate always-on setting, settled by D-64; do not silently
assume its current configuration satisfies that stronger recovery case.

### D-64. Enable always-on for every owner account at setup

Owner decision 2026-10-21. Accept P-54, applying it to every account in the
canonical shared `owner` plus `owners` set. At plateia setup, enable Ergo
always-on for any owner account that lacks it, including the phone profile.
This retains only rooms the account has joined, even when every IRC client
disconnects. It does not join additional rooms. Explicit PART still removes
that account from the room across its clients, and old membership snapshots
must not restore a completed leave.

Apply the setting to owner accounts individually, not as a server-wide
mandatory default. This approval does not extend always-on to agent or
assistant accounts; any proposed extension needs a separate owner decision.
Their presence continues to use real session records under D-5/D-57.

This is an approved setup action, not an action performed during design.
Verify both zero-client retention and restoration of persisted membership
after an Ergo restart. Keep this membership setting separate from D-61's
already completed owner-authority configuration.

### D-65. Finalize closure after dismissal, recording the unconfirmed gap

Owner decision 2026-10-21. Reject P-55 in favor of finalizing from IRC history
as it stands once the owner dismisses the unconfirmed-send notice under
D-47. Before dismissal, keep D-15's grey closing state. After dismissal,
that unresolved outcome no longer prevents closure; the room can leave the
active list once the history-based archive and its images are verified.

IRC history remains the conversation record. Do not insert an unconfirmed
send into the archived conversation as though it posted, or treat dismissal
as cancellation or permission to retry. Instead, include an explicit archive
note naming each unconfirmed send's logical ID and stating that it was never
confirmed at closure. Keep the shared dismissal and uncertainty records;
D-49 still protects any images whose use cannot be disproved.

The closed room carries a small marker identifying this gap. Include it on
the milestone-1 archived-room notice under D-58; this does not add an archive
browser or a way to join or reopen the room. The marker distinguishes an
archive closed with a known uncertainty from one with no such recorded gap.
Dismissal does not waive other archive verification or resolve agent-delivery
errors under D-42. D-48's dismissal is per send: another undismissed
unconfirmed send still keeps closure pending.

D-66 governs later confirmation, with checks limited to 24 hours after
closure and again on legitimate reopening.

### D-66. Reconcile late sends for 24 hours after closure and on reopen

Owner decision 2026-10-21. Accept P-56 with a fixed limit: check for late
appearance in authoritative IRC history only during the **24 hours after
closure**, and again if the room reopens under D-51. Do not keep an indefinite
watcher on closed rooms. Persist the closure time so a restart does not
restart or extend the window. Opening an archived-room link under D-58 is
not reopening the room and does not start another checking window.

If a named send is found and verified, update the archive with the message
once, in its IRC history order, and include its referenced images. Preserve
the original gap note with an annotation that the send was later confirmed.
Clear that send's gap marker only after the archive update is verified;
remaining unresolved sends keep their markers. The correction leaves the
room closed and never resends anything. A failed archive update must remain
recoverable without losing the existing archive or duplicating the message.

After the 24-hour checking window ends, unresolved notes and markers stand;
check again on a genuine room reopen. The end of checking is not evidence
that a send was cancelled, never posted or is safe to retry. It also does
not prove an image unused: D-49 continues to protect uncertain references.
The two 24-hour periods have different starting events: this one starts at
room closure; unused-upload cleanup starts only when an image is provably
unreferenced.

### D-67. Add an image button alongside paste on phone and desktop

Owner decision 2026-10-21. Accept P-57. Provide a small image button in the
milestone-1 composer on both phone and desktop, opening the device's
photo/file chooser. Insert the selected image into the current draft and
begin uploading immediately, while the owner can continue typing. Post the
message and image only on Send.

The button and paste share one composition/upload path, preserving the
current text and images. D-17's placeholder-link stage and later inline
stage apply to both entry methods, as do D-18's whole-message upload-failure
behavior, D-22's stored-image caps, D-23's draft persistence and D-49's
reference tracking. There is no separate preview/caption screen or general
file-sharing feature. D-68 settles the supported input formats.

### D-68. Support PNG, JPEG and WebP; verify whether HEIC is needed

Owner decision 2026-10-21. Accept P-58 with a narrower initial scope:
milestone 1 supports PNG, JPEG and WebP still images. Animation and video
remain out of scope. All accepted formats use the existing immediate-upload
flow and D-22's stored-image cap: 1280 pixels on the longest side and 1 MiB.

Do not add HEIC/HEIF support or its decoder dependency now. During real
phone verification, inspect what the page actually receives from the photo
chooser and paste, including photos originally stored as HEIC. The expected
conversion to a supported format is a hypothesis to verify on the owner's
phone, not a guarantee about every browser, picker source or file extension.

Only if that testing finds HEIC arriving unconverted should HEIC support be
brought forward. Record the reproduction and detected format without
publishing the owner's photo or metadata, then obtain a separate owner
decision on the decoder dependency before adding it. Do not silently install
a decoder or claim that untested phone selection works. This is a
verification-triggered follow-up, not an unconditional milestone-1 dependency
or slice.

Unsupported inputs show an error and leave the existing draft intact under
D-18; do not silently flatten animation or send text without a failed image.

### D-69. Limit source images to 20 MiB and 50 megapixels

Owner decision 2026-10-21. Accept P-59. Each source image must be at most
**20 MiB encoded (20,971,520 bytes)** and **50 million pixels** before
shrinking. Both limits apply to paste and the image button. Exceeding either
limit follows D-18's failed-upload rule: show the error, keep the whole draft
unsent, and let the owner retry or remove the image. Do not silently send
only its text or discard other draft content.

The saved and archived result still obeys D-22: at most 1280 pixels on the
longest side and 1 MiB, preserving proportions, without upscaling, and
shrinking further if necessary. Do not retain the high-resolution source as
an attachment or archive asset.

Enforce source limits before full decoding and storage as appropriate:
bound the bytes received and check decoded dimensions before allocating
the full pixel buffer. These limits alone are not a total processing-memory
guarantee; bound concurrent image processing as part of the implementation.
Verify the actual content instead of trusting client-supplied sizes.

### D-70. Keep one self-describing structured archive log

Owner decision 2026-10-21. Reject P-60. The owner expects agents to read
archives, so keep the structured conversation log as the only conversation
format, alongside the preserved capped images. Do not generate a separate
plaintext transcript or a second rendering of the conversation.

Make the structured archive self-describing and readable by an agent without
extra tooling. Include enough context to interpret it independently of the
live service: schema/version information, room identity, sender and timestamp
fields, stable message IDs, preserved message text and line breaks, image
references, and explicit gap notes with later-confirmation annotations under
D-65/D-66. Any archive verification metadata supports that one record rather
than becoming a second conversation log. Exact schema and filenames remain
implementation details to document and verify.

The existing image preservation, verification, closure and bounded late-send
correction requirements still apply. A later correction updates the structured
record and referenced images; there is no transcript to synchronize. This
adds no milestone-1 archive browser or search interface.

### D-71. Archive parts every owner; opening the reopened room joins again

Owner decision 2026-10-21. Accept P-61. Archiving a work room makes every
owner account in the canonical `owner` plus `owners` set leave it through
IRC PART. Under D-63 this removes membership in the owner's other IRC
clients too. Keep the structured conversation log and images; leaving the
room does not log the account out or disable its always-on setting.

If the work room is legitimately reopened under D-51, opening it joins
again under D-56 as the browser's bound owner account. This does not make an
archived-link visit reopen or join a room under D-58, or automatically join
every owner account merely because work resumes. Preserve the room identity,
history and images through the cycle.

The shared room lifecycle owns this cleanup, including when plateia is
closed. Verify actual per-account membership instead of presenting a failed
or unknown PART as successful. Guard closure/reopen races so an old archive
operation cannot part an account after it joins the reopened room. Apply
the owner cleanup without altering agent or assistant session records.

### D-72. Render literal text, links and images; defer Markdown

Owner decision 2026-10-21. Accept P-62. Milestone 1 displays message text
exactly as typed, keeping pasted line breaks and spacing, with clickable
links and the approved inline images. Do not interpret Markdown headings,
emphasis, lists or code fences; their punctuation remains literal text.
Markdown rendering is deferred to a later milestone, not assigned to
milestone 2 by this decision.

Keep D-26's minimal composer schema and D-37's exact logical message in IRC.
Wrapping to fit phone or desktop is presentation only and must not rewrite
stored text. The existing safe-link and untrusted-content rules still apply:
render text as text, and make only http/https destinations clickable.

### D-73. Inline only plateia-stored images; leave outside URLs as links

Owner decision 2026-10-21. Accept P-63. Milestone 1 renders inline only
images stored by plateia. Outside image URLs remain clickable links, with
no automatic image or website previews. Merely displaying a message must
not fetch an outside URL to produce a preview.

Recognize managed image references using the configured origin and stored
object identity, not a filename extension or an arbitrary URL claiming to
be an image. Keep the stored-image caps and retention/archive requirements.
The early placeholder-link slice still shows links until inline rendering
ships; the completed milestone displays managed images inline under D-17.

### D-74. Deliver text chat, then images, then rooms and archives

Owner decision 2026-10-21. Accept P-64. Milestone 1 delivery order is:

1. Shared sender/API contracts and usable text chat in existing rooms,
   with reading, posting and history delivered together.
2. Image sharing: managed links first, then inline images.
3. The issue/PR room model, archives and per-project clean cutovers,
   including the shared/global skill changes.

All three stages are required for milestone 1. Initial shared contracts
must account for later room lifecycle and image references without creating
parallel records. Shared prerequisite work may precede its visible stage.
Before any project cutover goes live, the project manager gives the owner
the D-44 warning; no live activation is authorized by this design decision.

Next define dependency-ordered, reviewable slices. This approves their stage
order, not every implementation mechanism, final issue boundary or readiness.
The owner still approves design readiness and each issue before filing;
the manager runs filed-issue reviews.

### D-75. Track shared work in plateia and deliver skills changes locally

Owner decision 2026-10-21, relayed by the project manager after the tracking
question. Every milestone-1 issue is tracked in `coghex/plateia`, including
issues whose implementation belongs in the canonical skills repository.
Keep that repository local only for now; do not add a remote or copy chat
implementation into plateia.

For each shared-chat issue, a worker changes a branch in a separate checkout
of the local skills repository. An opposite-brand reviewer approves the
exact commit, the relevant local test suites run with their results recorded,
and the owner or a level-2 agent authorizes merging into the canonical
checkout. The plateia issue names the skills change and links the reviewed
commit and test evidence as completion. There is no invented GitHub PR or CI
gate for the local repository. A changed commit needs review of that commit.

Plateia implementation remains in its own isolated worktree and PR with the
normal review and CI gates. Keep cross-repository changes in separate slices
with explicit dependencies and completion evidence. Shared-chat issues may
be drafted for owner signoff on this route; this does not waive the design
readiness checkpoint, individual issue approval or the brief's prohibition
on live changes during design.

### D-76. Approve the 23 slice boundaries, with the contract gates still open

Owner decision 2026-10-21. Accept P-65's boundaries at `e6107c0663`:
CHAT-1–CHAT-14 for shared foundations and usable text chat, CHAT-15–CHAT-17
for images, and CHAT-18–CHAT-23 for rooms, archives, cutovers and final
testing. Preserve the dependency order and separate implementation owners.

This approves boundaries only, not implementation or design readiness.
Resolve K-1–K-4 before drafting affected issues. Each issue still requires
owner signoff before filing, and the project manager runs its review.

### D-77. The existing bridge owns the single sender behind a durable queue

Owner decision 2026-10-21. Accept P-66 as written at `380b967943`, including
the deadline precision note. The existing chat bridge owns the single
covered sender, its persistent owner-account connections, durable command
queue and transactional attempt journal. Clients enqueue through the shared
Python API and read results; they do not take over IRC publication. There
is no new service, and a web-process restart does not own the sender's
lifetime. An exclusive process-lifetime writer lock fences bridge instances.

If the bridge is down, covered sends cannot start. Queued writes whose
deadline has passed never start when it returns. A write already started
may still post or remain unconfirmed under D-45/D-47; restart never retries
or resumes it merely because the new process obtained the writer lock.
Persist write-started before touching the socket. Explicit retry remains
limited to conclusively cancelled, never-started attempts with a fresh
attempt generation and the same logical message ID.

Service the send queue independently of slow agent delivery. Bridge-originated
commands must not wait synchronously for their own consumer. Replace the
minute-long legacy outbox schedule for covered sends and prevent covered
IDs from also entering the legacy replay path. Historical outbox entries
are not implicitly migrated. Other terminal IRC clients remain independent
and do not gain this API's retry guarantee.

This resolves execution ownership within K-1, not the whole gate. Complete
the deadline clock, operation/state, fragment and history/live contracts
before drafting the affected issues. No implementation or live change is
authorized by this design approval.

### D-78. Plan one native multiline batch per message, with setup verification

Owner decision 2026-10-21. Accept P-67 as a plan. At setup, raise Ergo's
native multiline limits to 65,536 bytes and 65,536 lines so each supported
64 KiB message uses one native batch. Do not split one logical message into
independently published batches, and do not patch Ergo. Preserve the
logical ID and exact text across the batch's wire lines and native history
identity. The existing unconfirmed/no-retry rules still apply.

Verify buffer sizing and fault behavior before activation. The change is
server-wide live configuration: the project manager confirms it with the
owner at setup before applying it. This design approval is not permission
to change the running server now. If verification exposes a needed buffer
change, bring its size and evidence back rather than silently changing
unrelated limits. The accepted slice boundaries stay unchanged.

### D-79. Prefer heartbeat clock samples when they preserve the conservative cutoff

Owner decision 2026-10-21. Accept P-68's goal and conservative-cutoff rule,
but do not adopt its per-send reservation handshake. First evaluate the
project manager's heartbeat variant: samples from the bridge's continuous
clock and boot epoch arrive ahead of time on the live stream, and Send uses
one content-bearing request. Adopt that variant if equally safe; otherwise
return the specific failure and evidence to the owner.

The browser uses the sample and its receipt time to calculate a conservative
deadline, requires a current-document sample newer than the last resume or
freeze and recent enough to trust, and otherwise waits briefly for a fresh
heartbeat. The server refuses deadlines beyond its own current time plus ten
seconds and never extends one. Preserve epoch handling, cancellation
tombstones, retry generations, D-45/D-47 and actual-browser verification.
Backgrounding after content submission does not cancel that submitted attempt.

Evaluation outcome 2026-10-21: adopt the heartbeat variant under this
delegation. P-68 now records the selected protocol, equivalence argument,
90 synthetic arithmetic cases and required lifecycle safeguards. This is
design-level equivalence under the same bounded-clock-error assumption,
not a claim of completed browser/sleep/network integration tests. Those
activation checks remain mandatory; a failure returns to the owner rather
than silently weakening the contract or switching protocols.

### D-80. Keep history on IRC, overlap boundary milliseconds and report incomplete ranges

Owner decision 2026-10-21. Reject P-69. Do not read Ergo's database or add
a dependency on its storage format. Use standard IRC CHATHISTORY with page
bounds that include the boundary millisecond, deduplicating native/logical
message IDs across the overlap.

If more messages share a timestamp than a page can hold, return explicitly
incomplete history rather than silently skipping them. Do not advance a
coverage claim past a boundary that has not been exhausted. If this approach
cannot meet K-1 within that accepted limitation, return the evidence to the
owner as a new question; the rejected database reader is not a fallback.
No change to Ergo, its configuration or its database is authorized here.

### D-81. Accept the shared snapshot/feed and version-one API contract

Owner decision 2026-10-21. Accept P-70 as written at `d1b12a93a1`.
Shared chat provides a snapshot at an epoch/revision, followed by ordered,
idempotent changes. Reconnect replays retained updates; an unusable cursor
returns an explicit reset and fresh snapshot. Use D-80's overlapping IRC
history, keep incomplete ranges explicit and preserve the distinction
between live observation and authoritative history evidence. Do not restore
pending sends after reload.

Accept P-70's version-one records and commands. This settles the K-1 design
contract alongside D-77–D-80 and the identity/cancellation invariants below.
Integration and activation checks remain required; approval is not evidence
that the implementation exists or passes them. K-2–K-4 remain open, the
design stays exploring and no issue or live change is approved by this step.

### D-82. Automatically arm owner waits from typed chat questions

Owner decision 2026-10-21. Accept P-71 with automatic setting of the wait
mark. When an agent posts through `pchat post --type question` mentioning
an owner account, shared code records a wait bound to that agent's session
and question. It becomes visible only when runtime evidence shows the turn
has ended, and clears when the next turn actually starts. No extra manual
status-setting step is required. An informal question without that type
leaves the agent showing its runtime state.

Verified owner-input prompts also count as waiting. Working/idle continue
to require runtime evidence; missing or conflicting evidence uses a hollow
grey unknown dot, distinct from solid grey idle. The rule belongs in shared
skills, not the web page. P-71 below incorporates the correction. This
settles K-2's design mapping; source-adapter verification remains required.
K-3/K-4 and whole-design readiness remain open.

### D-83. Keep unfinished-image failures after reload without retaining HD originals

Owner decision 2026-10-21. Accept P-72. If reload interrupts an image before
a recoverable capped copy is finished, keep its draft slot and ask the owner
to paste or choose it again. Keep text and other images; recover a completed
capped image even when its upload response was lost. Send remains blocked
until the missing image is replaced or removed. No HD original is retained.

This qualifies D-23's draft recovery for that preparation/upload interval.
Normal saved images recover without reselection; D-18 still governs upload
failure and retry/removal. K-3's encoding, processor/pins, resource bounds and
archive/export contract remain to be settled. K-4 remains open separately.

### D-84. Use pinned Pillow 13 and scope the new app to Python 3.11+

Owner decision 2026-10-21. Accept P-73: use pinned Pillow 13 for image
processing, initially 13.0.0, with a Python 3.11+ floor for the new web
application only. Give that application separate 3.11/3.14 checks and keep
the existing inbox's Python 3.10 support and 3.10/3.14 checks. Follow P-73's
dependency locking and CI aggregation contract. Installation belongs to
the implementing slice; nothing is installed or changed live now.

This settles K-3's processor and scoped Python compatibility choice, not its
remaining encoding, processing budgets or asset-reference/export contract.

### D-85. Accept bounded image processing and normalized output

Owner decision 2026-10-21. Accept P-74 as written at `ba12576225`: one
admitted image job at a time, in a separate worker, with a 20-second
processing deadline and monitored 1-GiB worker-memory stop threshold.
Exceeding a budget is an upload failure under D-18/D-83. The monitored
threshold can overshoot; deployment checks must verify its behavior.

Accept the protected JSON/base64 upload boundary and P-74's normalization:
correct orientation/color, strip source metadata including location,
preserve transparency and re-encode within the 1280-pixel/1-MiB caps.
Source limits remain 20 MiB and 50 million pixels. No HD original is kept.
The implementation and deployment checks remain required; nothing live
changes now. K-3's remaining design work is the asset-reference/pinning/export
contract, including archive operation while the web process is down.

### D-86. Accept one image catalog and a local asset command

Owner decision 2026-10-21. Accept P-75 as written at `54b0ba2a70`:
plateia owns one durable image catalog outside the repository, with a
versioned local asset command that operates while the web process is down.
Use individual references, conservative 24-hour unused-image cleanup and
verified archive copies. Shared chat owns send and archive lifecycle;
ordinary text chat continues if the helper is unavailable, while affected
image operations remain unresolved and retain their references.

Together with D-83–D-85, this settles K-3's design contract. Its crash,
concurrency, processing and deployment checks remain required. K-4 and
whole-design readiness remain open; no implementation or live change is
authorized by this decision.

### D-87. Give shared chat a moderator account for work-room control

Owner decision 2026-10-21. Accept P-76 as written at `9729415571`:
a dedicated shared-chat moderator account registers and controls work-room
IRC permissions. Conversation accounts, including owners, assistants and
agents, receive no voice, operator or founder grants in those rooms. The
moderator never posts conversation; owner authority remains unchanged.
Keep `+n` while active and add `+m` when frozen or archived, with the
verification and conflict rules in P-76. Permanent rooms keep their setup.

Account creation is a live setup action: the manager confirms it with the
owner at setup. No account or service is changed now. This settles K-4's
permission ownership, not its in-flight post/archive race, tracker lifecycle,
archive completion or reopen recovery.

### D-88. Accept snapshot archives and bounded late checks from every client

Owner decision 2026-10-21. Accept P-77 as written at `7119fa6f8c`:
after freezing, archive the verified saved IRC history as a snapshot. Check
for late posts from every client during the 24 hours after closure and again
on legitimate reopen, incorporating verified late messages and their images.
Each archive records its coverage and snapshot limitation.

Accept that an unseen post saved after the checking window can remain
missing until reopen, and that a post never saved to IRC history cannot be
recovered by these scans. Known missing history or images still block
closure; known unconfirmed shared sends still require dismissal under D-65.
No indefinite watcher or server patch is introduced. This settles K-4's
archive semantics, not its remaining lifecycle and recovery protocol.

### D-89. Keep reopening fenced until old cleanup is settled

Owner decision 2026-10-21. Accept P-78 as written at `d1da5c1c59`:
shared chat durably records close/reopen generations and operation receipts.
Posting and joining remain blocked until conflicting older cleanup commands
are settled. After 15 seconds of uncertainty, show the specified blocked
message instead of an indefinite reopening indication. Never restart Ergo
or forcibly disconnect a client automatically to recover.

Recovery requests go to the project manager first. The manager escalates to
the owner only when hands-on action is needed; this does not grant new live
service authority. This settles the recovery behavior. K-4 still needs
tracker associations/evidence, the persistent/archive-publication contract
and concrete IRC completion evidence before affected issue drafting.

### D-90. Accept durable room associations and manager-first conflicts

Owner decision 2026-10-21. Accept P-79 as written at `2d2346823e`:
shared chat keeps a durable work-item-to-room registry. Establish links from
explicit workflow registration or a complete, unambiguous GitHub link
result. Preserve established bindings and historical associations; ambiguous,
conflicting or later-changed links go to the project manager first.

A PR linked to several issues waits for resolution. Moving an already-used
binding requires an explicit owner-approved reconciliation plan; do not
automatically move conversations or close rooms because a link changed.
This settles association ownership and conflict behavior. K-4 still needs
the tracker observation/transition contract, persistent archive publication
and concrete IRC completion evidence. No live registration is authorized.

### D-91. Accept fresh tracker evidence and separate rounds of work

Owner decision 2026-10-21. Accept P-80 as written at `e1d02e4d3a`:
use fresh GitHub state and close/reopen events to drive lifecycle. A reopen
starts a new round, so PRs closed in an earlier round cannot immediately
close it again. Check every relevant PR/link before closing and recheck
before finalizing; incomplete or unavailable evidence waits.

Accept P-80's first-use and closure/replacement table. Create a room on first
use only for active work; an archived link still cannot reopen it. This
settles K-4's tracker observation and transition semantics. The durable
registry/archive publication contract and concrete IRC completion evidence
remain to be settled. No live polling setup or tracker mutation is authorized.

### D-92. Accept the shared control store and verified immutable archives

Owner decision 2026-10-21. Accept P-81 as written at `b26849ae31`:
keep shared send/control and room lifecycle records in one local SQLite
store, and publish immutable archive folders only after their log and images
are verified. Switch the current archive pointer and lifecycle/feed state
in the same control-store transaction after the required checks and cleanup.

Retain published revisions without automatic pruning in milestone 1.
The separate image catalog remains behind its asset command; this is an
ordered recovery protocol, not a cross-store transaction. This settles
K-4's durable registry/archive publication contract. Concrete IRC completion
and restart evidence remain open. No live storage or service change is
authorized.

### D-93. Accept IRC recovery and reaffirm operation with the web stopped

Owner decision 2026-10-21. Accept P-82 as written at `07017ff3d4`:
confirm current IRC effects, recheck and repair after reconnects, and keep
shared posting blocked while relevant state is unverified. Unconfirmed old
commands still fence reopening and go to the manager first. Accept the
specified crash-persistence gap and existing 24-hour/on-reopen late checks;
never restart the IRC server automatically.

Reaffirm that the plateia web process may be stopped while IRC runs. IRC,
the bridge, managers, other IRC clients and shared room freeze/archive/reopen
must continue. Image archiving uses the installed local asset command,
invoked as needed, without a running web service. The web page is unavailable;
stopping it must not stop or take ownership of these shared functions.
CHAT-23 must explicitly exercise this boundary. Flag any design or
implementation dependency that violates it rather than requiring the web
process to stay up. This is a design requirement, not authorization to stop
any live service.

P-82's IRC completion/restart choice is settled. Audit the combined K-4
contract and this web-off boundary before declaring the gate resolved.

### D-94. Accept web-served unavailability while the web service is off

Owner decision 2026-10-21. Accept P-83 at `68eebb0f8c`: nothing served
by the web service is guaranteed while it is stopped, including the page,
uploads and managed image URLs opened from other IRC clients. Restarting
the service restores the same image links; no independent image server is
required in milestone 1.

D-93 still applies to everything not web-served: IRC, bridge, managers,
independent clients, shared room lifecycle and local image archives continue
working. This resolves the web-off audit's image-URL availability question.
CHAT-23 verifies both sides of that boundary without stopping live services.

### D-95. Approve the design for issue processing

Owner decision 2026-10-21. The design at `872b7eb262` is ready for issue
processing. Continue in the same design session with `process-design-doc`:
draft the epic first, then one dependency-ready child at a time, obtaining
the owner's separate approval of each exact artifact before filing it.
Report each filed issue through the assigned design channel; the project
manager runs its issue review.

This approves the ready design and drafting workflow, not any unpresented
issue, implementation, installation or live setup. Existing confirmation
gates for IRC limits, owner persistence, moderator-account creation and
per-project cutover remain in force.

### D-96. Delegate issue and epic signoff to the project manager

Owner decision 2026-10-21. Approve the epic draft for filing with only the
specified requirement-1 wording change: each child receives separate signoff
before filing, by the owner or the project manager under this delegation.
The delegation covers issue and epic approval when the proposed text matches
the recorded decisions. It does not permit new design choices, scope changes
or deviations from an owner decision; those return to the owner.

Bring future child drafts from this design to the project manager for signoff
rather than directly to the owner. Keep one artifact at a time, present the
complete exact draft before approval, and report every filed issue in the
assigned design channel. The manager continues to coordinate canonical issue
reviews. This extends D-95's signoff authority without changing the approved
design, child boundaries, review gates or live-action authority.

Owner clarification 2026-10-21: epics are tracking issues and do not require
the solvable-issue readiness review gate. Epic #36 is verified and its owner
filing approval stands. Each filed child still receives the opposite-brand
issue review launched by the project manager.

## Proposals

### P-1. Make issue/PR room semantics shared

Resolved by D-8, with the owner's timing correction: include the global skill
change when room implementation is reached. The separate plateia mapping
alternative was not selected. Request records within a room and migration
details still need their own contracts.

### P-2. Open a work room when conversation or work first needs it

Resolved by D-9. Automatic room creation for every filed tracker item was
not selected; untouched backlog items need not fill the room list.

### P-3. Close an issue-only room when its issue closes

Resolved by D-10. Issues that close without ever having a PR do not leave
their work rooms permanently active; their logs are preserved.

### P-4. Keep a shared issue room open until its last open PR closes

Resolved by D-11. Associated PRs share the issue room, which remains open
until the last open PR closes. The board is not a prerequisite.

### P-5. Archive completed legacy work rooms and retain active or permanent rooms

Resolved by D-13 as amended: archive completed implementation rooms, retain
active/permanent rooms, and ask about uncertain ones. Existing design rooms
are discarded at rollout without saved logs.

### P-6. Archived rooms leave the active list and stop accepting posts

Resolved by D-14. Archived rooms leave active lists and reject new posts
until reopened; their verified file logs remain preserved.

### P-7. Finish archiving before removing a room from the active list

Resolved by D-15: settle already-accepted posts and verify the archive before
removing the room. The owner added a grey room title during closing.

### P-8. Keep a topic room for design work before its issues exist

Rejected by D-16. The owner chose the system-wide general room for design
agents. No delivery slice or global skill change may introduce dedicated
design rooms under this proposal.

### P-9. Move active legacy design conversations to general at rollout

Rejected by D-13/D-16: discard existing design rooms at rollout. No
conversation migration, completion check or saved log is needed.

### P-10. Preview pasted images before sending

Replaced by D-17's clarification: paste directly into the message and see
the image inline before Send, with no separate preview or added-text step.
The owner permits a placeholder-link slice before the inline-image slice.

### P-11. Keep the message unsent if its image upload fails

Resolved by D-18. Upload failure keeps the whole message unsent, with retry
or removal available.

### P-12. Upload pasted images while the owner finishes typing

Resolved by D-19. Upload starts on paste while typing continues; posting
still requires Send.

### P-13. Keep sent images for as long as their message logs

Resolved by D-20. Keep sent images as long as their logs and archive the
image files alongside the conversation.

### P-14. Fit inline images to the conversation; open full size on selection

Resolved by D-21 with the owner's correction: fit display to the column,
but cap the stored image independently on upload. Tap/click opens the capped
version. Retaining the high-resolution original was rejected.

### P-15. Store images up to 1280 pixels on the longest side and 1 MiB

Resolved by D-22. The owner approved both limits, shrinking further if needed.
A square 1280-pixel image uses 6.25 MiB for one four-byte-per-pixel buffer;
that arithmetic is not a bound on total upload-processing memory.

D-69 now settles incoming file-byte/pixel limits; the bounded processing
implementation still needs verification because shrinking the output does
not bound decoding the input. [Pillow's image documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html)
documents proportional thumbnail sizing and separate input pixel-count
checks. It is feasibility evidence, not a library selection or a source for
the owner-approved numeric limits.

### P-16. Keep a draft for each room on the same device

Resolved by D-23. Each room keeps its unfinished message and images across
room switches and reloads on the same device.

### P-17. Allow drafting offline, with Send disabled until reconnection

Resolved by D-24, with the owner's scope limit: minimal behavior inside the
composer, no separate slice or extra offline machinery.

### P-18. Choose React with Vite from a short comparison, then validate real chat

Framework recommendation rejected by D-25: the owner chose Svelte because
React was overkill. The real-chat evaluation method was accepted.
The comparison below is retained as the basis of that discussion, not the
current recommendation. D-26/D-27 specify the selected setup.

This is a design recommendation based on official documentation reviewed on
2026-10-20, not a measured prototype result:

| Candidate | Fit for this chat design | Tradeoff |
| --- | --- | --- |
| React with Vite | React documents a client-only Vite setup; Tiptap documents a React editor integration. My assessment: a practical fit for the composer, draft state and live conversation. | Vite is a build tool, not a complete application framework; routing and data-fetching conventions still need explicit choices. |
| Svelte | Compiled UI components suit an interactive page. It remains a viable alternative; Tiptap also lists Svelte integration. | It still needs editor integration, persistence and the same Python/shared-chat contracts. No evidence here establishes a reliability advantage over React. |
| Python templates with htmx | HTML response swaps fit server-rendered views and ordinary forms. | My assessment: the inline image editor and persistent drafts would still require a substantial browser component, making this a less direct fit for the accepted composer. |

Sources: [React's build-tool guidance](https://react.dev/learn/build-a-react-app-from-scratch),
[Tiptap's React integration](https://tiptap.dev/docs/editor/getting-started/install/react),
[Svelte overview](https://svelte.dev/docs/svelte/overview), and
[htmx documentation](https://htmx.org/docs/).
React's guidance cautions that a build-tool-only setup leaves application
conventions to the project. The selected Svelte setup is recorded above;
framework selection is no longer open.

### P-19. Use three columns on desktop and panels on phone

Resolved by D-28, including the owner's width clarification: narrow rooms,
wider users, and chat taking most of the desktop screen. Phone lists open
through buttons.

### P-20. Use simple status dots beside the existing role colors

Resolved by D-29, including acceptance of an amber waiting dot alongside a
yellow designer name. Source and freshness contracts remain under Q-5.

### P-21. Show an agent's current task beneath its name

Deferred by D-30 to an optional milestone-2 setting. It is outside
milestone-1 scope and slices; the default remains open for milestone 2.

### P-22. Use Enter to send on desktop and the Send button on phone

Resolved by D-31: Enter sends and Shift+Enter adds a line on desktop;
phone Return adds a line and the Send button posts.

### P-23. Show sending and failed messages in the conversation

Partly resolved by D-32 with revised presentation: small grey “sending...”
below the message, then a red exclamation mark on failure that retries when
clicked. Failed entries are not persisted. The proposed “Not sent” text is
superseded by the icon; “Checking” was not approved.

D-34 now settles normal display on confirmed posting, authoritative
lookup/cancellation and reuse of the message ID on retry. Posting does not
claim recipient acceptance. Visible delivery errors discovered after posting
still need a contract. These requirements do not introduce milestone 2's
full receipt display. Exact shared recovery contracts belong in Q-3.

### P-24. Clear the saved draft when Send submits it

Resolved by D-33, with the owner's additional rule: only one temporary
failure is shown, and any subsequent message clears it, including a newer
failure. Unsubmitted drafts remain saved; failed sends do not.

### P-25. Keep “sending...” while an uncertain submission is checked

Superseded by D-34's countdown, authoritative check/cancel and same-ID
retry. A plain sending label without a deadline was not selected.

### P-26. Keep the countdown at zero until the server can confirm the outcome

Resolved by D-35 with the owner's refinement: keep the zero countdown
briefly, then show an explicit unconfirmed state until the server answers.
No retry before confirmed cancellation. The selected extra delay is five
seconds and the wording is “unconfirmed: awaiting server”.

### P-27. Recover unconfirmed submissions after a same-device reload

Rejected by D-36. No browser persistence or restoration of unconfirmed
sends. D-45 clarifies the sender's deadline and requires settled history,
not just elapsed time, before a missing message can safely be retyped.

### P-28. Keep long text as one visible message

Resolved by D-37 with the explicit transport-grouping requirement.
Verified existing multiline support does not yet cover oversized logical
posts or exact preservation at their boundaries.

### P-29. Limit a logical message to 64 KiB of text

Resolved by D-38: 65,536 bytes of UTF-8 content per logical message,
with a warning before submission when exceeded and no silent truncation.

### P-30. Preserve the reading position while loading older messages

Resolved by D-39: scroll up loads older messages, new arrivals preserve the
reader's place, and “Jump to latest” returns to the bottom on both layouts.

### P-31. Selecting a user adds a mention to the current draft

Resolved by D-40 for milestone 1: insert the selected user's `@handle` into
the current draft and focus the composer without losing text or images.

### P-32. Keep an agent-delivery error attached to a posted message

Approved with conditions in D-41: derive it from the bridge's own record,
add any missing read capability in shared skills, and define what resolves
the error explicitly. The resolution choice is P-33.

### P-33. Resolve a delivery error when the intended agent accepts the message

Resolved by D-42: only acceptance by the intended agent clears its error;
there is no dismissal. Resending or redirecting creates a separately
tracked copy and leaves the original error in place.

### P-34. Use the bridge's existing delivery-failure alerts in milestone 1

Resolved by D-43 for milestone 1: retain the existing bridge phone alerts
alongside the chat error, with no bridge notification change.

### P-35. Finish active legacy implementation work in its existing rooms

Rejected by D-44 in favor of a clean cutover per project. Only parked
issues/PRs explicitly named by the owner retain marked legacy rooms until
completion; there is no general gradual migration of active work.

### P-36. Add message-ID and deadline enforcement at the IRC publication boundary

Rejected by D-45. No Ergo patch or owner-maintained IRC server. Design the
single-sender/history approach first; if it cannot work, bring evidence for
choosing a different server to the owner. Do not revive this patch proposal.

### P-37. Shared single-sender protocol for the no-patch approach

Proposed implementation contract under D-45, not a claim of existing code:

1. All submissions and retries covered by this contract go through one
   shared sender. Enforce exclusive writer ownership across restarts. Keep
   an attempt journal on the server before touching the socket; browser
   pending-message persistence remains forbidden. Bind the logical ID to
   authenticated sender, room and immutable content so ID reuse cannot
   silently change the instruction.
2. Give an explicit retry a distinct attempt generation and deadline, while
   keeping the logical ID. Reject expired or cancelled old generations,
   including HTTP requests arriving after a newer retry. Under the same
   writer serialization, check the deadline immediately before starting the
   write. A queued, not-yet-written attempt can be cancelled conclusively.
3. Carry a client-only logical-ID tag through the multiline representation;
   retain it in the shared record and expose it with the server's `msgid`.
   Labeled responses correlate queries; they are not durable logical IDs.
   D-37's complete-message grouping still applies to every supported size.
4. After an uncertain write, recover from IRC history, not text matching or
   the bridge's last logged message. Save the pre-send history cursor and
   inspect the complete relevant range through an authoritative settled
   endpoint. Match the logical tag, account and room. A cached shared index
   may accelerate a positive lookup but cannot prove absence unless its
   coverage is known complete. Missing access, pruned history or an
   interrupted page means unknown, not cancelled.
5. An ordered round-trip after the write does not establish durable recording:
   P-39's verified publication-before-history gap rules it out as sufficient
   proof for retry. A reconnect likewise does not settle an old session's
   buffered bytes. Recovery may report posted when the complete matching
   message is found; it must not turn a missing record after a write began
   into cancellation merely because a PONG arrived or a session disconnected.
   Conclusive cancellation before any write began remains available under
   the exclusive writer's journal and attempt fencing. Other cancellation
   evidence must establish both no possible later publication and no prior
   unrecorded delivery; otherwise D-47 requires unconfirmed with no retry.
6. If the matching complete message is found, record posted and return it
   without writing again. If absent after proven settlement and proven
   completeness of recording, record the cancelled attempt. Before an explicit
   same-ID retry, check history again
   and allow one new attempt under the exclusive writer. An unproven outcome
   stays unconfirmed without retry, as D-35 requires.

The installed capabilities support tagged history recovery. D-47 accepts
unconfirmed outcomes when disconnect/restart settlement or complete recording
cannot be proven, so the protocol need not always return posted or cancelled.
Verify its refusal to retry those cases, exclusive writer ownership and
64 KiB multiline transport before claiming the implementation works.
The shared skills/service owns this protocol; plateia presents its results.
P-39 records a further source-level gap: even a settled write can have reached
recipients without a durable history entry. Absence needs completeness of
recording as well as completion of the old writer.

### P-38. Mark reloaded history as syncing while a prior send is unsettled

Resolved by D-46 with the owner's time-limit condition: “Syncing history...”
lasts at most 15 seconds from the room's first history request in a loaded
page, then becomes “unconfirmed: awaiting history” until recovery completes
or the owner dismisses it under D-47.
No pending message or retry entry is restored.

### P-39. Keep irrecoverable send outcomes unconfirmed, or evaluate another server

Resolved by D-47: keep Ergo and leave unprovable sends unconfirmed, with
owner dismissal of the notice. No server comparison or patch. Evidence:

Read-only follow-up on Ergo 2.19.1 found an additional limit:

- `Channel.SendSplitMessage` sends to member sessions before calling
  `AddHistoryItem`. History-write errors are logged, without reversing
  delivery. **Inference:** a crash between delivery and history insertion,
  or an insertion failure, can leave a recipient with a message that history
  does not contain. A later complete scan cannot prove it was never delivered.
  [Delivery and history ordering](https://github.com/ergochat/ergo/blob/v2.19.1/irc/channel.go#L1343),
  [history-write error handling](https://github.com/ergochat/ergo/blob/v2.19.1/irc/channel.go#L743).
- A session's command loop executes handlers sequentially. An ordered reply
  on the original connection can establish that its earlier handler returned,
  but does not establish successful history persistence.
  [Command loop](https://github.com/ergochat/ergo/blob/v2.19.1/irc/client.go#L681).
- Native session logout calls `destroy`, which detaches/closes a session
  without waiting for its command loop to finish. **Inference:** its success
  reply alone is not a barrier against an already executing handler.
  [Logout](https://github.com/ergochat/ergo/blob/v2.19.1/irc/nickserv.go#L1350),
  [session destruction](https://github.com/ergochat/ergo/blob/v2.19.1/irc/client.go#L1310).

No crash, logout or service change was performed. This is source evidence,
not a reproduced live failure. It prevents claiming that an absent ID after
reconnection or IRC restart always means cancelled. Single-writer ownership
does not close a delivery-before-history gap.

The accepted no-patch approach has an explicit limitation:
when recording or settlement cannot be proven, the outcome may remain
unconfirmed indefinitely, without retry or a claim that retyping is safe.
D-46 still bounds the syncing presentation to 15 seconds; it does not impose
a deadline for obtaining truth. Found complete IDs remain posted; an attempt
known never to have been written can be cancelled. Lost confirmation plus
missing history alone must never authorize a repeat write.

The owner accepted this limitation in D-47. It no longer blocks design on
choosing a server; implementation still needs to verify that uncertain
outcomes cannot authorize duplicate writes.

### P-40. Remember dismissal in the shared sender

Resolved by D-48: the shared sender remembers dismissal across reloads and
devices. New uncertain sends retain their own notices; the unresolved
record remains and a late positive result still appears in history.

### P-41. Remove unused image uploads after a grace period

Resolved by D-49: delete provably unused uploads after a 24-hour grace period;
retain anything referenced or whose unused status is uncertain. Exact
reference registration and interrupted-upload recovery belong in the
attachment contract.

### P-42. Reuse the room when PR work resumes

Resolved by D-51: reopened and replacement PR work reuses the existing
issue/PR room with its history and images. Resumed work must not be hidden
by a stale closure operation.

### P-43. Use the project main room for work without a tracker item

Resolved by D-52: use the permanent project main room for work without an
issue or PR; design remains in general. A later issue/PR room can link to
the earlier discussion without moving or copying it.

### P-44. Show all active agents in the shared chat-testing room

Resolved by D-53: `#chat-test` lists all active agents across projects, as
general does. Only the list changes; routing, authority and IRC connections
do not change.

### P-45. Show the owner and live assistants in room user lists

Corrected by D-54: reject the automatic owner entry in every room. Use real
logged-in membership of the owner accounts. The owner-account display,
opening/joining behavior and assistant membership rule are separated below
for one-at-a-time decisions. No status dots for owner or assistants remains
approved under D-29.

### P-46. Combine joined owner accounts into one owner entry

Rejected in favor of D-55: retain separate entries for the two owner accounts,
each based on its own logged-in membership. Do not ask this choice again.

### P-47. Settle whether opening a room joins it as the owner

Resolved by D-56: opening joins as the owner and creates a missing room on
first use within the shared naming/lifecycle rules. Archive behavior and
creation eligibility must respect D-9/D-14/D-51; no implicit permission to
replace an archived room with a fresh one.

### P-48. Keep assistant presence based on live sessions

Resolved by D-57: assistants with live sessions appear in every room using
shared session evidence, without an IRC join or status dot.

### P-49. Opening an archived room does not reopen it

Resolved by D-58 for milestone 1 only: show “This room is archived.” without
joining, reopening or recreating the room. Preserve its log and images;
archive browsing remains milestone 2.

### P-50. Create only rooms recognized by the shared room model

Resolved by D-59 as a milestone-1 shortcut only: configured permanent rooms
and real issue/PR rooms, with shared names and lifecycle rules. Custom rooms
with their own rules are a deferred future requirement, not designed now.

### P-51. Switching rooms preserves existing joins

Resolved by D-60: switching preserves joins. Every room page also has a
Join/Leave control; Leave is IRC PART. Room functionality should mostly
mirror IRC, within the approved milestone scope and archive exceptions.

### P-52. Use one configured owner account for plateia joins and posts

Rejected by D-61. Phone and desktop use their respective owner accounts with
equal authority. D-62 settles browser binding under the owner's delegation;
the shared owner-configuration change is complete as of the owner's
2026-10-21 update, with support in skills commit `7ce2401`.

### P-53. Keep IRC connections and joins when the browser closes

Resolved by D-63, owner decision 2026-10-21. Accounts remain logged in and
joined behind the interface, sharing membership with their other IRC clients.
The installed Ergo configuration and matching source support multiclient
attachment; D-64 settles zero-connection retention for all owner accounts.

### P-54. Enable Ergo always-on for both owner profiles at setup

Resolved by D-64, owner decision 2026-10-21. At setup, enable always-on for
every owner account in the shared owner set, retaining only joined rooms.
Agent and assistant accounts are excluded from this approval. No live change
was made during design; the separate owner-authority setup is already complete.

### P-55. Keep closure pending while a send remains unconfirmed

Rejected by D-65, owner decision 2026-10-21. Until dismissal, keep the grey
closing state. After dismissal, finalize from current IRC history, name each
unconfirmed logical send ID in an archive note and mark the closed room's gap.
An unresolved send must not keep a room closing indefinitely after dismissal.

### P-56. Update a closed archive when an unconfirmed send is found later

Resolved by D-66, owner decision 2026-10-21, with the owner's limit: check
only during the 24 hours after closure and again on reopening. A verified
late send updates the archive and images, annotates the original note and
clears its gap marker without reopening or resending. No indefinite watcher.

### P-57. Offer an image button alongside pasting

Resolved by D-67, owner decision 2026-10-21. Phone and desktop both get an
image button alongside paste, using the same draft and immediate upload;
posting still happens only on Send.

### P-58. Support common still-image formats in milestone 1

Resolved by D-68, owner decision 2026-10-21, with a narrower initial scope:
PNG, JPEG and WebP stills. HEIC/HEIF is conditional on real phone testing
showing unconverted input, followed by a separate owner decision on any
decoder dependency. Animation and video remain out.

Technical feasibility evidence, 2026-10-21: Pillow documents PNG, JPEG and
WebP support; Pillow-Heif supplies HEIF decoding through libheif. HEIC support
would therefore add a decoder dependency, with exact package pins and wheel
compatibility for the project's Python/macOS/CI environments to verify before
implementation if D-68's trigger is met and the owner approves the dependency.
This is retained feasibility evidence, not an approved HEIC implementation,
package selection, installation or claim of tested browser/device behavior.
[Pillow formats](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html),
[Pillow-Heif](https://pillow-heif.readthedocs.io/en/stable/index.html),
[installation and wheels](https://pillow-heif.readthedocs.io/en/latest/installation.html).

### P-59. Bound source images before shrinking them

Resolved by D-69, owner decision 2026-10-21: at most 20 MiB encoded and
50 million pixels per source image. Exceeding either limit follows the
failed-upload rule; saved images still obey the 1280-pixel/1-MiB cap.

### P-60. Make milestone-1 room archives readable as local files

Rejected by D-70, owner decision 2026-10-21. No plaintext transcript:
agents will read a self-describing structured log, with preserved images
and gap notes, without extra tooling. Do not maintain two conversation formats.

### P-61. Leave an archived room on every owner account

Resolved by D-71, owner decision 2026-10-21. Archive parts every owner
account while preserving the log and images. When the room legitimately
reopens, opening it joins again under D-51/D-56. An archived-link visit still
does not reopen or join it.

### P-62. Keep message text literal in milestone 1

Resolved by D-72, owner decision 2026-10-21: literal message text with
clickable links and inline images, preserving spacing and line breaks.
Markdown is deferred to a later milestone, without a selected milestone number.

### P-63. Inline only images stored by plateia

Resolved by D-73, owner decision 2026-10-21. Only plateia-stored images render
inline; outside image URLs stay clickable links, without automatic image or
website previews.

### P-64. Deliver text chat, then images, then the new room lifecycle

Resolved by D-74, owner decision 2026-10-21. Deliver shared contracts and
usable text chat first, images second, and the new room model, archives and
per-project cutovers third, all in milestone 1. The manager gives the owner
the D-44 warning before any live cutover. Reviewable slice boundaries and
issue signoffs follow; no live change is authorized here.

### P-65. Use the proposed 23 dependency-ordered slice boundaries

Resolved by D-76, boundaries only, at `e6107c0663`. The 13 shared-skills
changes and 10 plateia changes use D-75's applicable delivery route.
K-1–K-4 remain open; this is not approval to draft issues or implement.

### P-66. Put the covered sender in the existing bridge, behind a durable queue

Resolved by D-77, owner decision 2026-10-21, accepting this proposal at
`380b967943` with the deadline precision note. The accepted proposal follows.

Use one sender inside the existing shared chat bridge process. Clients
call the shared Python API to record commands in a durable local queue and
read results; they never take over its IRC writes. This implements the
already-selected Python API/no-new-daemon boundary from earlier D-16 and
this design's D-2/D-45. The queue, attempt journal and sender implementation
belong to shared skills, not the web server.

The bridge owns persistent owner-account connections and serializes the
covered send/check/cancel/retry operations for both owner accounts. The
web server can restart without taking these connections or commands with
it. If the bridge is unavailable, clients do not bypass it: commands wait
only until their existing attempt deadline, and expired commands must never
start a write when it returns. This does not extend the ten-second deadline
or automatically retry a failed message. Existing terminal IRC clients can
still post independently; their ordinary posts do not gain the covered
sender's logical-ID retry guarantee.

Use a local transactional journal behind the API, rather than the legacy
text-match outbox. An exclusive process-lifetime writer lock prevents two
bridge instances from publishing covered sends. Record a write-started state
durably before the first socket write; after a crash that state remains
potentially published, even if the socket may never have been touched.
The new writer may look for the ID in history but must never resume or
resend that attempt merely because it acquired the lock. D-47's unconfirmed
outcome remains available. A separate cancelled-before-write state permits
the already-approved explicit retry with a fresh attempt generation.

The queue must be serviced promptly within the existing bridge, independently
of potentially slow agent-delivery work. Bridge-generated commands enqueue
without synchronously waiting for the same consumer. Its current one-minute
outbox flush is not the new scheduler. No covered logical ID may also enter
the legacy replay path; adoption by a shared caller replaces that caller's
direct-write/replay path for covered sends. This proposal does not silently
migrate historical outbox entries or change unrelated terminal clients.

Read-only evidence, 2026-10-21: canonical `chat/scripts/chat-bridge` already
owns a service loop, history catch-up and delivery processing, and calls
`flush_outbox` every minute. `chatlib.post` opens a caller-owned connection;
`outbox_claim` locks only the file claim, not publication ownership, and
replays leftover claimed files after a crash. These are reusable boundaries,
not proof that the required exclusive sender or journal exists. No code,
live configuration or service change was made.

The alternative is letting individual clients acquire sender ownership and
open their own connections. That retains the current caller-driven shape
but spreads persistent connection lifetime and restart recovery across web
and command-line processes. Recommend the existing bridge to keep one
owner of those responsibilities without adding a service.

This fixes execution ownership and makes covered sends depend on bridge
availability. It affects K-1, particularly CHAT-1 and CHAT-4–CHAT-6, and
their consumers. D-77 does not resolve the rest
of K-1: exact deadline clock semantics, operation records, fragment identity,
history/live cursors and evidence validation still need the detailed contract.

### P-67. Fit one supported message into one native Ergo multiline batch

Resolved by D-78 as a plan, owner decision 2026-10-21, with buffer/fault
verification before activation and separate owner confirmation of the live
setup change. The accepted proposal and evidence follow.

Use one native multiline batch for every covered logical message,
including the full 65,536-byte text limit, instead of publishing several
independent batches and adding a cross-batch commit protocol. Retain the
browser-created logical ID as a client-only tag on the batch opener, bind it
to the authenticated account and room, and retain the native history msgid
as its alias. Individual IRC lines remain explicitly grouped by the batch.
This preserves CHAT-2's approved outcome and boundary.

At setup, propose Ergo's native `limits.multiline.max-bytes: 65536` and
`max-lines: 65536`. The line ceiling accommodates even a valid nonempty
64 KiB message made mostly of line breaks without silently adding a smaller
text limit. Preserve every supported leading/trailing blank line and space;
do not send an empty/all-line-break batch, which the protocol rejects.
Require negotiated multiline support and enough capacity before any write.
If those preconditions fail, report the configuration problem without
publishing part of the message or falling back to independent posts.

This is a server configuration proposal for later owner-controlled setup,
not an Ergo patch or authorization to change anything live now. The native
limits are server-wide, not a plateia-only setting. The current server
configuration and the complete wire/output-buffer budget must be checked
before activation; a 64 KiB body is not a 64 KiB IRC exchange. In particular,
the installed sample configuration has a 96k send queue. Do not assume that
queue can hold the worst-case multiline output or enlarge unrelated limits
silently. Any needed buffer change must be sized, tested and brought back
with evidence before activation.

Evidence reviewed 2026-10-21:

- Ergo 2.19.1 exposes the byte and line limits as configuration and advertises
  their values. The installed sample uses 4096 bytes and 100 lines.
  [Configuration and capability construction](https://github.com/ergochat/ergo/blob/v2.19.1/irc/config.go).
- Its batch handler accumulates the incoming lines and dispatches the message
  after a valid closing batch marker; its receive handler does not dispatch
  each batched line as a separate post. The session retains batch state.
  **Inference:** using one batch removes publication between independent
  chunks of the same logical message. This is source evidence, not a fault
  test or proof of durable history insertion.
  [Batch and message handlers](https://github.com/ergochat/ergo/blob/v2.19.1/irc/handlers.go),
  [session batch state](https://github.com/ergochat/ergo/blob/v2.19.1/irc/client.go).
- IRCv3 defines newline/concat reconstruction and requires client-only tags
  on the opening batch. Match the installed draft capability names and verify
  exact tagged live/history round trips rather than assuming all versions
  implement the latest draft identically.
  [Multiline specification](https://ircv3.net/specs/extensions/multiline).

The sender still records write-started before sending the opener. Once that
attempt starts, loss of confirmation remains potentially published: receiving
a valid closing marker may have committed the whole message. D-45/D-47's
history authority and unconfirmed/no-retry rule remain unchanged, including
the known delivery-before-history gap. This proposal does not claim an
atomic transaction between delivery and durable history or change the deadline.

Before activation, CHAT-2/CHAT-6 must verify one complete history identity and
one agent dispatch at the size/line limits, exact text and logical-tag replay,
interleaved independent messages, interrupted batches, lost final replies,
capability changes, and realistic output buffering/fakelag on isolated Ergo.
Do not add a second multi-batch fallback if verification fails: bring the
evidence back before changing the transport design or approved text limit.

D-78 approves the single-batch plan; actual server-wide activation remains
subject to its verification and owner-confirmation conditions. This is part
of K-1 and affects CHAT-2, CHAT-3, CHAT-6, CHAT-8 and their consumers.
Deadline clock semantics and history/live cursor proof remain separate
unfinished parts of K-1.

### P-68. Use heartbeat clock samples for a single-request send

Resolved by D-79: the goal and conservative cutoff are accepted; the
heartbeat variant is selected after the comparison below. The original
per-send reservation proposal remains available at `8472a78bef`; it is not
an alternate implementation path or an automatic fallback.

#### Selected clock and submission contract

The shared clock provider uses the host's continuous monotonic clock and
boot epoch. On macOS, `mach_continuous_time` advances during sleep, as the
installed SDK declarations specify. Convert through the platform timebase;
bridge and web processes use the same shared provider and epoch. UTC is
display/audit data, never write eligibility. Keep the epoch through process
restarts; a host reboot invalidates old, never-started attempts. Write-started
attempts still require history reconciliation and never restart automatically.

Piggyback a fresh sample on live stream frames and on a heartbeat at least
once per second while the page is active. This is part of the existing chat
stream, not a new service or per-message preparation exchange. Sample the
shared host clock freshly when constructing the frame; do not relabel an old
cached heartbeat as new. Carry the clock epoch, stream generation, increasing
sample sequence and sampled time. The browser records `b_r = performance.now()`
when it receives the sample, and keeps it only in memory.

Use a sample only in its current document/stream generation, with no
intervening hide, freeze, resume or reconnect, and at most **five seconds**
of browser elapsed time since receipt. Out-of-order samples cannot replace
a newer one. Invalidate on lifecycle changes; after resume/reconnect create
a new stream generation and require a freshly sampled frame for it. Old
queued callbacks/frames are ignored even if delivered after resume. A new
connection must produce a fresh clock sample, not replay one from its history
cursor. This establishes that a resumed page does not mistake delivery of an
old buffered frame for a sample taken after resume.

At Send, create the logical/command IDs, record browser time `b0`, start the
ten-second countdown and clear the saved draft under D-33. With a valid
sample, submit the content once, carrying an immutable deadline:

`deadline = s + (b0 - b_r) + 10 seconds - guard`

Use the existing **100 ms guard**. Encode server clock values without
floating-point precision loss, round the signed browser duration downward,
and never round the final deadline later. The sample value, signed elapsed
duration, boot epoch and attempt generation accompany the request; the shared API validates their
shape and the deadline calculation. It rejects a wrong host epoch, an expired
deadline or a deadline greater than its own current clock plus ten seconds.
All queueing, validation and connection delays consume the same budget.
The writer rechecks immediately before its write-start decision. No API
receipt, reconnect, duplicate command or web restart refreshes the deadline.

If no valid sample is available at the click, wait in the existing sending
state for a fresh frame, bounded by the **original ten-second countdown**.
Keep `b0`; do not start a new countdown when the frame arrives. The new
receipt time can be later than `b0`, so `b0 - b_r` is negative and must remain
negative. Recompute using that fixed click time; if the resulting budget is
already exhausted, do not submit content and request cancellation. There is
no per-send reservation request. If the page hides/freezes before content
submission, invalidate that unsent attempt and request cancellation; do not
resume it later. The normal path has already submitted content at Send, so
backgrounding afterward does not cancel it or alter its deadline.

At zero, use the existing serialized check/cancel and its durable tombstone,
including when no content request ever reached the bridge. Show retry only
after confirmed cancellation. Repeated API commands remain idempotent;
explicit retry keeps the logical ID, creates one fresh attempt generation,
and gets its own click time/deadline through the same rule. Once submitted,
the fixed deadline belongs to the attempt, independently of the browser's
current stream or lifetime. Do not invalidate an already-registered attempt
merely because the page later changes stream generation. Reload stores or
restores neither submitted content nor retry state. D-35/D-47 apply to
unanswered checks and possibly written attempts unchanged.

#### Equivalence and verification

Let `s` be the server sample time, `d >= 0` its transport/queueing delay to
the browser callback, and `a` the signed interval from that callback to the
click. In ideal shared units, true server time at the click is `s + d + a`.
If the measured signed interval has error `e`, the selected deadline is
`s + a + e + 10s - guard`. It is no later than the true click plus ten
seconds whenever `e <= guard`, because the difference is
`e - guard - d <= 0`. Delayed or buffered delivery only makes expiry earlier.
The proof also holds when the frame arrives after the click (`a < 0`).
Clamping that interval to zero would invalidate the proof and is forbidden.

The reservation alternative also depends on a bounded browser elapsed-time
error; it does not remove that assumption. Heartbeat reuse adds elapsed time
since a previous sample, so the five-second freshness cap bounds that interval.
A post-click wait is bounded by the original ten seconds. Verify the error
bound in both directions over those intervals, including timer coarsening
and rate differences. The unchanged 100 ms guard is an activation condition,
not an assumption inferred merely from the existence of `performance.now()`.
The [High Resolution Time specification](https://www.w3.org/TR/hr-time-3/)
distinguishes elapsed monotonic time from wall time but does not establish
the behavior of the owner's actual browser under sleep.

| Case | Required result |
| --- | --- |
| Fresh frame available at Send | One content request; fixed conservative deadline. |
| Asymmetric network delay or buffered frame | Cutoff can be earlier, never later; no latency compensation that adds time. |
| Frame arrives after Send | Keep the original click time and signed negative interval; no renewed budget. |
| Stale sample, resume or reconnect | Drop the old generation and await a freshly sampled frame; no old callback can authorize submission. |
| No fresh frame before zero | No content submission; serialized cancellation/tombstone before retry. |
| Hide/freeze before content submission | Cancel the unsent attempt; no submission resumed later. |
| Background/reconnect after submission | Keep the already-fixed deadline and sender state; no automatic cancellation or retry. |
| Host sleep or wall-clock correction | Continuous host clock still expires the attempt; wall time cannot extend it. |
| Host reboot or unknown clock epoch | Refuse old never-started attempts; possibly written attempts remain unconfirmed until proved posted. |
| Browser elapsed-time behavior exceeds the guard | Stop activation and return evidence; do not silently choose a weaker clock or another protocol. |

On 2026-10-21, 90 exact-rational arithmetic cases covered signed sample ages
from -9.9 to +5 seconds, delivery delays from zero to 60 seconds and elapsed
errors of -100, 0 and +100 ms. Every resulting cutoff was no later than click
plus ten seconds. A counterexample confirmed that clamping a post-click
sample's negative age to zero extends the cutoff. These are design arithmetic
checks, not a running browser or network test.

Compared with the reservation handshake, this removes its extra send-time
round trip and gives the same conservative publication-start bound under the
same clock-error/epoch conditions. Lifecycle invalidation and fresh stream
generations supply the required resume safety. Therefore select it under
D-79's conditional authorization. CHAT-5/CHAT-6/CHAT-12 must verify actual
phone/desktop timing, sleep/background behavior, delayed/replayed frames,
clock epochs, cancellation races, and crashes before activation. No live
change or implementation was made during this evaluation.

### P-69. Read Ergo's persisted history through a versioned, read-only shared adapter

Rejected by D-80, owner decision 2026-10-21. Keep history on standard IRC
CHATHISTORY, include the boundary millisecond in adjacent pages, and
deduplicate message IDs. Explicitly report an unexhausted boundary as
incomplete history. Do not read Ergo's database or depend on its storage
format. The original rejected proposal is retained in commit `dbf5a588f1`;
it is not an alternative implementation path.

The K-1 overlap contract below applies the owner's selected approach and
records source/model evidence. Source inspection of Ergo's query code is
evidence about its IRC behavior, not permission to query its live storage.

### P-70. Give chat readers a shared snapshot and resumable change cursor

Resolved by D-81, owner decision 2026-10-21, accepting this proposal as
written at `d1b12a93a1`. The accepted contract follows. Keep the
existing Python API and bridge-owned durable queue from D-77. Add a shared
read view: a snapshot and a change cursor, with ordered updates after that
cursor. The same boundary serves plateia and other shared-chat clients;
plateia does not parse IRC, reconstruct history or run another sender.
No new daemon, Ergo database reader or second history authority is proposed.

The reason for this choice is the handoff: independently fetching history
and then starting live delivery can miss messages between those operations.
A shared view makes that handoff one contract and lets a reconnect replay
updates. If a cursor is no longer available, return an explicit reset and a
new snapshot instead of pretending that no updates occurred. This follows
the snapshot/cursor pattern already selected for the earlier board read model
in `plateia_design.md` D-49, but applies it explicitly to milestone-1 chat.

#### History and live handoff

- The bridge establishes and verifies its live room subscription before
  starting the IRC history query. Its parser keeps accepting live arrivals
  while labeled history batches are read; complete messages from either
  source merge by native IDs and verified logical aliases. Preserve source
  provenance: a live echo alone never becomes durable history evidence.
- Catch-up scans backward using D-80. Where a previous checkpoint exists,
  it must find that checkpoint's ID and exhaust its entire boundary
  millisecond, by crossing into an older millisecond or obtaining the positive
  end indication. Seeing the checkpoint ID alone is insufficient. A missing
  checkpoint, retention gap or saturated boundary remains explicitly
  incomplete; it cannot be hidden by attaching live updates to the result.
- Commit each merged record and its change revision together in shared
  durable state. A snapshot includes everything through revision R; clients
  then receive updates strictly after R. Replayed updates are idempotent.
  Record late history confirmation as an update to the existing message,
  not another visible copy. Conflicting ID/content/account evidence is an
  explicit conflict, never a text-based merge.
- Keep a stream epoch as well as a monotonic revision so replaced or rebuilt
  state cannot reuse an old cursor. If a disconnect interrupts the scan,
  invalidate that scan's coverage and repeat from a known boundary. If a
  consumer falls behind retained changes, require a new snapshot. A web
  restart cannot remove the bridge's already-recorded messages or evidence.
- This prevents a gap introduced by the shared read-view handoff. It cannot
  make Ergo delivery and persistence atomic. A message delivered before the
  live subscription but not yet visible in an IRC query may evade that query;
  an insertion failure may never produce a history record. D-47's existing
  limitation remains: no scan, cursor, checkpoint or live echo proves a
  write-started send absent or cancelled. Known unconfirmed IDs continue
  reconciliation; unknown server omissions cannot be inferred from page size.
- Return available messages with their coverage state. Respect D-46's
  15-second syncing bound rather than blocking the page indefinitely on a
  scan. A cursor reset does not renew that waiting period. This is not a new
  browser copy of the failed/unconfirmed send queue.

#### Version-one records and operations

These are semantic fields, not final Python symbol or JSON field names.

| Record or operation | Required meaning |
| --- | --- |
| Envelope | API version, shared-state epoch/revision, observation time, source and freshness; unsupported major versions fail explicitly. |
| Message | Stable room identity, authenticated sender/account evidence, native msgid aliases, optional covered logical ID, exact text, timestamp, complete-batch status, live/history provenance and managed asset references. Untagged external IRC posts keep their native identity. |
| History page | Messages, opaque next cursor, queried bounds, effective limit and coverage (`complete for queried available range`, `more`, `incomplete` or `unavailable`) with a reason. Separate evidence of a positive match from evidence about the range. |
| Snapshot/change feed | Room message projection, coverage and permitted notice/delivery/membership metadata at one revision; ordered idempotent upserts and explicit reset on an unusable cursor. Clock frames use D-79's fresh samples, never replayed event timestamps. |
| Submit | Command ID, logical message ID, attempt generation, immutable actor/room/content, managed asset references and D-79's epoch/deadline. Durable registration is not posting. |
| Check/cancel | Command ID and actor/room/logical ID/generation; serializes with the writer and creates a tombstone if the attempt has not arrived. Returns posted evidence, confirmed cancellation or unconfirmed, never a guessed success. |
| Retry | New command ID and deadline, same logical ID and immutable content, expected cancelled generation. Recheck IRC history and atomically allocate at most one next generation; stale, contradictory or unavailable evidence cannot publish. |
| Dismiss notice | Idempotent command scoped to the unconfirmed logical send. Persist suppression across devices without changing its send outcome or touching a bridge-delivery error. |
| Membership | Idempotent join/leave command with immutable acting owner account and room/lifecycle generation; result contains observed IRC membership or an unresolved failure, not a success inferred from enqueue. |

All mutating commands enter the shared queue through the Python API;
execution remains in the existing bridge. Reads use that API's shared view,
not private tables from the web application. Persist the command identity,
immutable payload binding and result together so repeated requests return
the same result. A crashed client's replay cannot refresh a deadline or
allocate another retry. Keep D-77's process-lifetime writer lock and the
identity/cancellation table below as the send-state contract. No covered
command may fall through to the old outbox or direct caller-owned IRC writes.

The browser receives actual history and the durable dismissal/notice facts
approved in D-48. It does not receive a restored pending message body or a
retry action after reload; D-36's no-browser-recovery rule still applies.
Snapshots must not restore a covered send's live-only body as though it were
confirmed history. A body already present in the current page may continue
its existing send interaction; rebuilding the page does not recover it.
Agent-delivery outcomes retain the separate per-recipient identity from
D-41/D-42; posted-to-IRC and accepted-by-agent never share one success flag.

Required verification includes arrivals during every page and snapshot
boundary, duplicate/reordered API delivery, interrupted nested batches,
missing checkpoints, saturated timestamps, epoch changes, stale cursors,
late history confirmation and a crash between record/revision publication.
Exact tagged history round trips, buffer sizing, clock behavior and owner
session sharing still require the isolated integration checks already
attached to CHAT-2–CHAT-6. Source and query models do not satisfy them.

This affects CHAT-1, CHAT-3–CHAT-6, CHAT-8 and CHAT-10–CHAT-12 without
changing the 23 approved boundaries. D-81 settles K-1's remaining
design contract, subject to those implementation/activation checks and the
independent later gates (K-2 since resolved by D-82; K-3/K-4 still open).
It does not call the whole design ready, approve
issues or authorize a live change.

### P-71. Make waiting on the owner explicit in shared agent status

Resolved by D-82, owner decision 2026-10-21, with the automatic-setting
correction incorporated below. The superseded manual-setting proposal is
retained at `9ac091ac34`. Keep presence tied to real sessions and
keep D-29's three colors. Use runtime evidence for working/idle, and add an
automatically armed shared waiting record when an agent posts a typed
question mentioning an owner. Posting the question does not immediately
mean the agent has stopped working; runtime end-of-turn evidence activates
the amber display. A terminal accepting input does not establish that either.

| State | Required evidence | Presentation |
| --- | --- | --- |
| Working | A current, correctly bound runtime turn/activity observation, without a later stop or blocking input event. Registration and the fallback string `running` do not qualify. | Solid green dot. |
| Idle | A confirmed end-of-turn/idle observation for the live session, with no active owner wait. Mere silence does not qualify. | Solid grey dot. |
| Waiting on owner | A wait automatically armed by an agent's `pchat post --type question` mentioning an owner, followed by confirmed end of that turn; or a verified active runtime prompt specifically requiring the owner's input. Generic `needsInput` without a verified reason does not qualify. | Solid amber dot. |
| Unknown | Missing, conflicting, unsupported or too-old evidence; not a fourth guessed work state. | Hollow grey dot with an accessible “Status unknown” explanation. |

The wait record belongs in shared chat, alongside its session-backed status
projection. The shared posting path records it automatically through the
agent's authenticated identity; bind it to the logical session, live process
generation, turn and question ID. Resolve mentions against the canonical
owner set, not one hardcoded account or a display-name match. Untyped
questions and questions addressed only to another agent do not arm a wait.
Arm it for the successfully posted question, not a failed local posting
attempt. Correlate turn generations so late confirmation or replay of an old
question cannot restore a wait cleared by a newer turn. No manual status
command is needed. Arming it while finishing the current turn leaves the
dot green until the turn actually stops. At that point the active wait
produces amber instead of idle. A verified blocking native prompt can produce
amber within a turn because execution is blocked there.

Clear the chat wait when that same session actually starts its next turn.
An owner reply arriving in IRC or a bridge
delivery acknowledgement alone does not clear it: the agent may not yet have
resumed. A new typed question to an owner in the new turn arms another wait;
an informal question does not. Native prompt resolution follows its matching runtime event,
not an unrelated chat reply. Old-generation waits cannot attach to a new
process merely because it occupies the same tab or uses the same role name.
This is state reporting, not a change to delivery, authority or acceptance.

Keep the last state transition time separate from the time it was observed
and from successful inventory time. Preserve the already-approved freshness
defaults: stale after two minutes without a trustworthy refresh, unknown
after ten. Stale known status keeps its last color with an explicit stale/age
annotation; unknown uses the hollow dot. A fresh read of a defaulted or known
unreliable lifecycle value cannot manufacture fresh working evidence. A
successfully verified unchanged current state may refresh its observation;
reading a historical hook event again may not. Assistants and owner accounts
still have no status dot. No current-task line is added.

Presence and activity remain independent. A successfully completed real
inventory can remove an ended session. An inventory outage retains the last
presence with stale/unknown evidence instead of treating every agent as gone.
Conflicting placement, reused PIDs and missing hooks cannot transfer a state
or wait to another session. Scope project agents, global lists, assistants and
real owner membership exactly as already decided in D-5/D-53–D-57.

CHAT-7 implements the normalization, automatic wait record and shared guidance;
CHAT-13 only renders the resulting facts. Verification must cover both agent
brands, a question asked while still working, a stopped question waiting for
an answer, an answer delivered before resumption, native prompt resolution,
interrupts, missed hooks, session replacement and stale-source recovery.
Also cover both owner accounts, informal questions, peer-only questions,
failed posts, duplicate confirmations and an old question replayed after a
new turn has cleared its wait.
The source gaps below are required adapter checks, not permission to guess
from terminal text in the web page or to patch cmux. If the installed runtime
cannot supply a required trustworthy observation, expose unknown and return
the gap before claiming that state works. D-82 settles K-2's design mapping;
these adapter checks remain implementation obligations. K-3/K-4 remain open.

### P-72. Recover unfinished image uploads without retaining originals

Resolved by D-83, owner decision 2026-10-21. Persist the room's text and attachment
slots immediately, but retain image bytes only after they meet the approved
1280-pixel/1-MiB cap. Never put an HD original into browser draft storage,
an upload spool, the server's asset store or a backup. Original input may
exist transiently in memory while being uploaded and processed.

Use server-side normalization in the Python application, beginning
as soon as paste/selection starts the upload. The server validates source
limits and format, produces the capped image, and commits it with its draft
reference before acknowledging completion. The browser saves the resulting
asset ID in the existing draft slot. Sending still waits for every image;
finishing an upload alone never posts a message. This does not choose the
processor dependency or resource budgets, which remain separate K-3 work.

Use a stable upload ID persisted with the draft slot before the request.
After reload, query that ID: a completed capped asset recovers even if its
response was lost. A known in-progress job remains pending; a failed or
interrupted upload with no recoverable capped image becomes a failed slot
reading “Image unavailable — paste or choose it again.” Keep the text, image
position and other attachments. Send remains disabled until that slot is
replaced or removed. Do not falsely display the missing image as recovered
or silently send without it.

This is D-83's narrow qualification to D-23: if the page reloads before
any recoverable capped copy exists, the owner may need to select/paste that
image again. Normal saved images recover without reselection. While the page
remains open and still has the original in memory, a transient upload failure
can be retried directly under D-18. After memory is lost, Retry uses an
existing capped copy if available; otherwise it asks for replacement input.
Removing or replacing the failed slot never discards the rest of the draft.

Keep uploaded draft assets pinned under D-49 even when the page is closed;
recovering by upload ID must not race unused-upload deletion. A server crash
may lose in-memory processing but cannot expose a half-written asset as
complete. Cross-device draft synchronization remains out of scope, and no
browser persistence of submitted/failed sends is introduced.

The tradeoff is unavoidable before a capped copy exists: transparent recovery
of arbitrary original bytes requires retaining them somewhere. Browser-side
shrinking could move this interval earlier, but cannot remove failure or
reload during preparation. Retaining a temporary original would change the
no-HD-storage rule; it is not an approved fallback. Use the explicit
failed slot and reselection for this short interruption window.

This affects CHAT-15/CHAT-16 and their reload/failure checks, without changing
slice boundaries. Verify interruption before upload, during normalization,
after the capped asset commits but before its response, and while another
room is selected. Distinguish recoverable completion, known pending work and
lost source bytes; preserve the draft throughout. K-3 remains open for the
upload encoding, processor/pins, resource bounds and archive/export contract.
K-4 remains open separately.

### P-73. Use current Pillow with a Python 3.11 floor for the new web application

Resolved by D-84, owner decision 2026-10-21. Use **Pillow 13.0.0** for server-side
PNG/JPEG/WebP validation and shrinking, and require **Python 3.11 or newer**
for the new web application. Qualify that application on Python 3.11 and
3.14; use the existing local 3.14 runtime for deployment. The existing
routine-inbox package retains its Python 3.10 floor and 3.10/3.14 CI matrix.
This is a component-specific floor, not a repository-wide compatibility
change or an instruction to upgrade the shared bridge.

Evidence checked 2026-10-21: the repository has no existing web application
or image dependency; `.github/workflows/ci.yml` currently tests only the
inbox suite on 3.10/3.14. PyPI reports Pillow 13.0.0 as the current release
with `Requires-Python >=3.11`; 12.3.0 requires >=3.10. Upstream's compatibility
table confirms this split. The 13.0.0 release also documents decoder fixes,
including JPEG decoding. Recommend the current release for the new image
path instead of choosing an older major solely to reuse the inbox matrix.
[Pillow compatibility](https://pillow.readthedocs.io/en/stable/installation/python-support.html),
[Pillow 13 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html),
[release metadata](https://pypi.org/pypi/Pillow/13.0.0/json).

Pin the new web application's Pillow dependency exactly and commit the
installation lock/requirements with hashes for the supported CI/deployment
artifacts. Use a separate environment; neither shared chat nor the inbox
imports Pillow merely to read a chat record or archive a capped image.
Pillow's built-in format allowlist supplies PNG/JPEG/WebP; add no HEIC plugin
unless D-68's actual phone verification triggers that separate decision.
Do not silently downgrade Pillow to make a 3.10 install succeed. An unsupported
interpreter fails with a clear version requirement before starting the app.

CHAT-10 establishes the new backend's 3.11/3.14 checks. CHAT-15 adds its
locked image dependency and image tests to those checks. Keep the existing
inbox job and add the new backend job to `build-test.needs`; a failed,
cancelled or skipped required job must fail the aggregate. Use the same
dependency lock for local qualification and CI. Recheck the selected release
at implementation for required fixes, documenting any pin update and running
the same checks rather than using a floating version.

This settles the processor choice and the scoped Python compatibility
contract under D-84. It does not claim a dependency was installed or tested,
or settle output encoding, processing budgets, storage/reference ownership or
archive export. Those remain K-3 work; K-4 remains open. The 23 approved
slice boundaries do not change.

### P-74. Bound each image job and retain only a normalized capped result

Resolved by D-85, owner decision 2026-10-21, accepting this proposal as
written at `ba12576225`. Process one image at a time in a separate,
short-lived Pillow worker, with a **20-second processing deadline** and a
**1-GiB worker resident-memory stop threshold**. Start the processing timer
before opening the received image, not while the network upload is arriving.
If the worker fails, exceeds the deadline or crosses the memory threshold,
terminate that owned worker and report an upload failure under D-18/D-83.
Do not weaken the cap, silently drop the attachment or post the remaining text.

Admission covers body receipt and processing, so simultaneous requests cannot
each allocate a maximum-sized body behind a single decoder queue. The page
uploads its selected images in sequence while typing remains available; other
concurrent requests receive an explicit busy result before their image body
is admitted and keep their draft for retry. This is not an offline send queue.
One admitted job is an application-wide limit across web processes, not one
worker per HTTP request or browser. A slow image must not block chat reads,
send commands or the bridge.

The parent observes worker memory at least every 100 ms and stops it on a
threshold crossing. This is a monitored stop threshold, **not a hard 1-GiB
allocation ceiling**: sampling and termination can allow overshoot, and the
web process's bounded request buffers are additional memory. Use enforceable
OS limits where available, but do not assume that a Unix resource constant
has identical enforcement on macOS and Linux. If monitoring cannot operate,
fail the job instead of running without its budget. Qualification on the
deployment Mac must measure peak worker/parent memory and termination delay;
excessive overshoot returns for a revised budget/mechanism before activation.
No resource-limit behavior has been experimentally verified in this design.

#### Upload encoding within the accepted browser boundary

Keep the existing JSON/custom-header mutation contract from
`plateia_design.md` D-64: one image per same-origin authenticated JSON request,
with a strict base64 byte string and the persisted upload/draft-slot IDs.
Apply Host, Origin, account binding and the custom header before accepting
the body. Do not add a multipart/form exception or an unauthenticated upload
URL. Refuse compressed request bodies rather than introducing another
unbounded expansion stage.

The wire-body maximum is **28 MiB**, including JSON; the decoded source still
must be at most **20 MiB**. A maximum source uses 27,962,028 base64 characters,
leaving bounded room for the small envelope. Reject excessive body length
while receiving, not after loading an unbounded request. Strictly decode and
validate the expected fields; never trust MIME, extension or declared pixel
count. Keep received originals in memory only, with no temporary-file spool
or request-body logging. A replacement attachment gets a new upload ID;
an old upload's delayed completion cannot replace the new draft slot.

#### Normalization and saved representation

Allow only PNG, JPEG and WebP stills. Check dimensions and the 50-million-pixel
source cap before full decode, reject animation/multiple frames, and fully
decode the selected image before accepting it. Apply EXIF orientation to the
pixels, convert a valid embedded color profile to sRGB (otherwise use the
format's supported default interpretation), and preserve transparency where
the input supports it. Malformed required decoding/profile data is an upload
failure, not a reason to retain the original. Re-encode a fresh 8-bit image,
without source EXIF/GPS, comments or other ancillary metadata; any retained
color declaration describes the normalized output only.

Keep PNG as PNG, JPEG as JPEG and WebP as WebP. Use lossless PNG and a starting
quality of 85 for JPEG/WebP. Fit within 1280 pixels without upscaling, then
reduce dimensions further and re-encode until the encoded file is at most
1 MiB. Do not crop, flatten alpha, retain an HD alternate or copy input bytes
through unchanged. Bound the encode loop by strictly decreasing dimensions
and the same worker deadline. Measure the final bytes/dimensions and decode
the result successfully before atomically publishing it as a complete asset.
Exact filter/encoder options are implementation details to verify on invented
screenshots, photos, transparency and phone orientation cases.

Pillow documents lazy identification before full loading, its configurable
pixel warnings/errors, explicit format allowlists, supported encoders and
EXIF transposition. These support the proposed sequence; they do not prove
our time or memory budget. Python documents that resource-limit support is
OS dependent. No new dependency beyond D-84 is selected by this proposal.
[Pillow image API](https://pillow.readthedocs.io/en/stable/reference/Image.html),
[format handling](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html),
[orientation](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose),
[Python resource limits](https://docs.python.org/3/library/resource.html).

CHAT-15 owns the bounded upload/worker/result contract; CHAT-16 uses its
pending/busy/failure outcomes without losing the draft; CHAT-17 qualifies
actual phone inputs, with HEIC still conditional under D-68. Verify boundary
bytes/pixels, corrupted headers and compressed metadata, concurrent uploads,
worker timeout/memory failure, parent exit and orphan-worker cleanup, lost
responses, exact output caps and no original-file persistence. These checks
use isolated invented inputs; no live service change is authorized here.

D-85 settles K-3's upload encoding and processing behavior. The
asset-reference/pinning/export contract and its web-down operation remain
open, as does K-4. The 23 slice boundaries stay unchanged.

### P-75. Keep one image catalog and expose local pin/export operations without the web process

Accepted by D-86, owner decision 2026-10-21, as written at `54b0ba2a70`.
Plateia owns the capped image
files and one durable SQLite asset catalog outside the repository. Shared
chat owns send, message and room/archive lifecycle decisions. Both use a
versioned local asset API; the browser never edits the catalog, and shared
chat does not depend on the HTTP server to preserve or export an image.

Implement that API in the installed plateia asset package, callable through
a short-lived JSON command using its configured Python 3.11+ environment.
It opens the same catalog and files whether the web process is running or
stopped. It does not import the web application or Pillow to pin, verify or
copy an already-capped asset. This adds no persistent service and copies no
chat code into plateia. Shared callers depend on the versioned command
contract, not the catalog schema or the checkout's internal module paths.
Ordinary text chat continues if the asset interface is unavailable; an image
send or archive that needs it reports an unresolved failure and retains its
references instead of claiming success.

#### Asset identity, storage and access

Each completed upload has an opaque asset ID, immutable capped bytes,
SHA-256 digest, byte length, dimensions, media type and storage/schema
version. Retrying the same upload ID returns the same asset; a replacement
uses a new upload ID. Do not overwrite an existing asset ID with different
bytes. Cross-upload content deduplication is unnecessary for milestone 1.
Write/flush the capped file before committing its complete catalog record
and initial draft reference. Recover interrupted staging explicitly; neither
an orphan file nor a catalog row without verified bytes is a complete image.

Messages carry stable managed image URLs and asset IDs in the shared logical
record. Recognize a managed link only through configured canonical origins,
the exact asset route and a valid catalog identity, never by filename suffix
or a resemblance to the hostname. The browser reads capped images through
the existing private access boundary; serve only complete catalog objects
with the recorded safe image media type and `nosniff`. Opening the image
returns the same capped bytes, not another resolution or an original. Outside
URLs remain ordinary links under D-73. Archive entries also name the asset
ID, digest and a relative local image path, so reading an archive requires
neither a live URL nor the web process.

#### References and the draft-to-send handoff

The catalog stores individual durable references, not a caller-maintained
integer count. A reference identifies its kind, owner/producer, stable use ID
and generation: draft slot, send/retry, unresolved send, observed message or
archive generation. Acquire/release is idempotent and scoped to that exact
reference. A late release cannot remove a newer generation or another use.
Only the corresponding shared lifecycle can release send/message/archive
references; a browser operation can release only its bound draft/slot use.

| Transition | Required ordering |
| --- | --- |
| Upload completes | Commit the capped asset with its draft reference before returning success. A removed/replaced slot has a generation tombstone; late upload completion cannot recreate that draft attachment. |
| Send clears the browser draft | D-33 still clears local draft storage immediately. Before any IRC write, durably acquire a send/retry reference for every managed asset and record its receipt in the shared send journal. Only then release the submitted draft revision's references. |
| Send fails or is cancelled before writing | Keep references while the current page may offer retry. A confirmed cancellation alone does not mean the image is unused. Release that retry use only on explicit disposal/replacement evidence; loss of the page without such evidence remains uncertain under D-49. |
| Send becomes posted | Acquire the message reference before releasing its send-only reference. Unconfirmed outcomes retain protection even after notice dismissal or the 24-hour late-check window. |
| Managed link arrives through another IRC client | Observing the reference in live delivery or history acquires a message reference. Do not require that the message originated in plateia. A live reference protects its image even before durable history confirmation. |
| Draft/image is removed | Release only the exact acknowledged draft-slot revision. Other drafts, messages, retries and archives retain their independent references. Browser inactivity, reload and missing local storage are not releases. |

Pin acquisition and shared send registration are separate durable operations,
not a claimed transaction across two stores. On a crash between them, retain
the extra pin and reconcile by stable operation IDs. Favor a retained asset
over a dangling published link. If an asset is missing, corrupt, being
deleted or cannot be pinned, refuse the image-bearing attempt before writing;
do not strip its links and send the remaining text. Existing deadlines still
apply while obtaining asset evidence. Catalog operations cannot extend them.

#### Offline archive export and cleanup

The local API supplies versioned operations to inspect/verify an asset,
acquire/release a reference, look up an upload, pin an archive's asset set and
export that pinned set. Requests include command/use IDs and generations;
responses include catalog version/epoch, exact IDs/digests and explicit
complete, missing, conflicting or unavailable results. Unknown versions fail
explicitly. Shared callers never infer successful preservation from an exit
code without the matching result or from a file path alone.

Shared archiving first pins the complete referenced set, then asks the asset
API to copy each immutable capped file into its private staging directory.
The helper accepts destinations only within the configured archive root;
the browser cannot supply filesystem paths. Re-read and verify each copied
file's digest and length, returning a manifest of IDs, relative paths, media
types, dimensions and checksums. Include that information in the one
self-describing structured archive under D-70. A manifest is preservation
metadata, not a second conversation transcript. Long copy work holds neither
the sender execution path nor a long catalog transaction.

Shared lifecycle code verifies the conversation and image set before
publishing the archive generation and reporting closure. Failure leaves
the previous complete generation intact and the new pins retained for
recovery. Preserve stable asset IDs on reopen; corrections add their required
pins/files before publishing a new verified generation. K-4 still determines
the posting barrier, exact close/reopen generations and final publication.
The image export contract does not waive unavailable history or missing files.

Use D-49's 24-hour grace only after the catalog can establish no reference
and no unresolved producer/staging operation. Record `unreferenced_since`;
new use cancels that grace. Under catalog serialization, recheck immediately
before marking an asset deleting. New acquisitions either protect it before
that transition or fail before a caller may publish a link. Complete deletion
recoverably; do not leave a missing blob labeled available. Reconciliation
gaps, interrupted reference delivery, catalog corruption or uncertain browser
disposal prevent a claim of unused and therefore prevent cleanup. An old
timestamp, empty query result or absent browser heartbeat is insufficient.
Shared positive references must be reconciled before cleanup can advance;
never use a negative IRC history scan to release a known uncertain send pin.

This implements existing retention choices, including their conservative
cost: an abandoned draft/retry with no provable release can retain its capped
images. Do not introduce age-based draft expiry or a new owner cleanup UI.
Keep the canonical image while any log/message/archive reference needs it;
the archive's verified local copy also remains self-contained. No files are
removed as part of accepting this design.

#### Verification and ownership

The repository currently has only the routine-inbox package and no asset
catalog/export implementation. This is a new contract, not an existing
capability verified by source inspection. CHAT-15 provides the catalog, local
API, reference transitions and cleanup; CHAT-16 supplies browser draft and
retry-disposal evidence; CHAT-19/CHAT-20 consume archive operations from shared
lifecycle code under the later K-4 contract. Keep those approved boundaries.

Verify crash points around file/catalog commit, pin/enqueue/draft release,
retry disposal, deletion versus new acquisition and archive copying. Stop the
web process and prove that the local API can pin/export/verify invented
assets, including missing or corrupted files and stale generations. Exercise
two drafts sharing one asset, a lost upload reply, a direct IRC managed link,
an unconfirmed send and a late archive correction. Missing evidence must keep
the image and expose the failure, not silently complete cleanup or closure.

D-86 settles the remaining K-3 design contract together with D-83–D-85,
subject to its implementation and deployment checks. K-4 and whole-design
readiness remain open; no tracker artifact or live configuration change is
authorized by this decision.

### P-76. Let shared chat control work-room IRC permissions

Accepted by D-87, owner decision 2026-10-21. Give the shared bridge a
configured service account for work-room channel registration and mode
changes. It is the channel founder; ordinary conversation accounts,
including every owner, assistant and agent account, have no channel voice,
operator or founder grants in these rooms. This is IRC channel administration,
not a change to the owner's authority or to authenticated message senders.
The service account does not post conversation on anyone's behalf; D-77's
sender still posts as the actual authenticated account.

Create/register a new work channel through shared lifecycle code before
joining its first conversation participant. Verify registration, persistent
account grants and current member modes before declaring provisioning
complete. If the name already exists with unexpected ownership or grants,
report a conflict; do not silently take it over or remove permissions.
The rollout plan must explicitly reconcile legacy work rooms that will be
archived. Permanent rooms and owner-named parked exceptions retain their
existing setup until their applicable cutover work.

For an active work room, use normal unmoderated conversation with no
outside-channel posting (`+n`). For a frozen or archived work room, shared
chat sets moderation (`+m`) as well. With no participant voice or higher
channel mode, this rejects new conversation from terminal and phone IRC
clients as well as from plateia. Keep the registered channel and its
identity; do not unregister or purge it as an archive shortcut. Only the
shared lifecycle restores ordinary posting on a legitimate reopen. D-71's
owner PART actions still occur at the archive stage, not at this permission
change.

The service account is dedicated to channel control, has no unrelated
server-operator capability, and never supplies a conversational bypass.
Ordinary participants do not receive automatic grants on reconnect. A
deliberate administrator override or unexpected permission change is a
visible lifecycle conflict requiring reconciliation, not evidence that the
room remains safely frozen. It does not change owner authority to direct
that reconciliation. Shared chat owns these operations and their durable
intent; plateia only displays the resulting state. No new persistent service
or server patch is proposed.

#### Evidence and remaining barrier work

Read-only inspection of the installed-version upstream source, Ergo 2.19.1,
shows that `CanSpeak` blocks outsiders with `+n` and unprivileged members
with `+m`; any channel-user mode bypasses the moderation check. Registration
keeps an empty channel alive. Permission checking precedes delivery and
history insertion, so a successful mode change cannot prove that earlier
handlers have finished. Mode writeback is asynchronous. These are source
facts, not a live qualification result.
[Ergo channel implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/channel.go).
ChanServ exposes registration and persistent account grants; its registration
policy must permit the configured control account at setup.
[Ergo ChanServ implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/chanserv.go).
The inspected shared sender/bridge has no existing work-room registration
or mode-control lifecycle to reuse as a completed capability.

This proposal selects control ownership and the prospective posting gate
only. **It does not settle archive completeness or the full K-4 barrier.**
Still resolve how admitted shared sends drain before freezing, how posts
already executing in other IRC clients are accounted for, and how server
restart/persistence gaps affect verification. Do not treat mode replies,
PONG, a quiet interval or repeated matching history reads as proof that no
earlier post can appear. D-65's exception remains limited to its approved
dismissed send IDs; this proposal does not silently extend it to unidentified
other-client posts. Keep Ergo and bring any needed guarantee change to the
owner, with evidence; never patch it.

CHAT-18 owns provisioning and permission ownership; CHAT-19 owns freezing
and the still-open archive barrier; CHAT-20 owns later correction/reopen;
CHAT-22 owns the explicit per-project setup. Keep the 23 approved boundaries.
Qualification must cover direct owner/agent IRC clients, automatic grants,
always-on membership restoration, conflicting existing channels, bridge and
Ergo restarts, and permission changes during close/reopen. Missing evidence
keeps closure unresolved. No account, mode, configuration or service changes
are authorized now. The manager confirms moderator-account creation with
the owner at setup.

### P-77. Close from a verified history snapshot and check all clients for late posts

Accepted by D-88, owner decision 2026-10-21. After draining admitted
shared sends and verifying the moderator's posting block, archive the IRC
history available at closure. Extend D-66's bounded reconciliation from
named unconfirmed sends to **all ordinary conversation messages**, including
ones sent directly by other IRC clients. Check during the 24 hours after
closure and again on legitimate reopen; do not add an indefinite watcher.

This qualifies D-15's completeness requirement: a verified archive preserves
the history successfully enumerated at closure and its images, but is not
proof that every earlier server handler has finished or that every delivered
message reached durable history. Every structured archive records that
snapshot boundary, its coverage and this limitation in self-describing
metadata. It does not claim an absolute final-message watermark.

#### Why the moderator alone cannot prove final history

In Ergo 2.19.1, `SendSplitMessage` checks permission, then delivers, then
inserts history. No lock spans those steps. An allowed ordering is: a direct
client passes the permission check; the moderator freezes the room; an
archive read finishes; the earlier handler resumes and inserts its message.
History insertion errors are logged without reversing delivery.
[Ergo channel source](https://github.com/ergochat/ergo/blob/v2.19.1/irc/channel.go).
The persistent backend associates a history entry with its channel after
inserting its base record, using the original message timestamp. A late
entry can therefore appear behind an already-read timestamp boundary.
[Ergo history source](https://github.com/ergochat/ergo/blob/v2.19.1/irc/sqlite/history.go).

This is a source-supported possible ordering, not a reproduced live failure
or an estimate of its frequency. PONG, a quiet period and repeated identical
reads do not establish a bound on it. An extra fixed wait would reduce the
chance but would not supply the missing proof. Retain the owner's choice of
Ergo without a patch; this proposal makes the resulting archive limit
explicit instead of leaving every closure waiting for an unavailable proof.

#### What may close, and what still blocks closure

- Keep D-65 unchanged for known shared sends: an undismissed unconfirmed
  send prevents closure; dismissal permits its named gap note and marker.
  The broader late check does not dismiss or retry any send.
- Still require successful history enumeration under D-80, the expected
  available-history coverage, complete logical messages, verified images
  under D-86 and the remaining K-4 publication/cleanup checks. Unavailable
  history, a saturated page boundary, a missing required history range,
  corrupt files or missing images cannot become a successful snapshot.
- Reconcile messages already observed live against authoritative history.
  A known missing direct-client message is an archive problem and keeps
  closure unresolved; this proposal gives it no automatic dismissal. The
  exception here is the **unobserved possibility** of a handler appearing
  later, not permission to discard a known discrepancy.
- Ordinary successful snapshots need no new room warning solely because
  that theoretical race exists. The structured log states the limitation;
  D-65's marker continues to identify actual named gaps, and detected archive
  failures remain visible. Absence of a marker does not certify that an
  unseen server-side write was impossible.

#### Bounded correction and its limit

Reconciliation must search for newly visible records across the archived
room's retained history range, not only timestamps after its newest message
or the list of known send IDs. Use D-80's overlapping IRC pages and deduplicate
by native/logical identity; never read the Ergo database. Keep previously
verified archive records when current retention no longer returns them.
Maintain live observation during the permitted checking window as an
additional source of candidates, not a replacement for history evidence.

A newly confirmed message is incorporated once, in history order, together
with verified image copies. Publish a recoverable new archive revision;
preserve the previous verified revision and annotate the correction. Follow
D-66's existing rules for named gap notes and markers. Failed reconciliation
or correction records its error rather than claiming a successful check.
Neither an outage nor a restart extends the persisted 24-hour window; record
missed checks and resume only while time remains. Genuine reopen performs
another history reconciliation. An archived-link visit does not.

The owner accepts this limit: an unseen message saved only after
the checking window can remain absent until reopen; a message never saved
to IRC history cannot be recovered by these scans. This is why the archive
describes its actual evidence rather than claiming all server-side work was
proved complete. Known evidence and uncertain image references are retained
under D-49/D-86; ending checks is not proof that anything is unused.

This changes CHAT-19/CHAT-20's archive and correction semantics, without
changing the approved slice boundaries or editing the vision. K-4 still
needs tracker evidence, the precise drain/freeze sequence, restart recovery,
atomic archive publication and membership/reopen fencing. Its remaining
contract must include isolated delayed-handler, late old-timestamp, failed
history-write and interrupted-correction cases. No live change is authorized.

### P-78. Fence close/reopen work and keep uncertain cleanup visibly pending

Accepted by D-89, owner decision 2026-10-21. Keep a durable lifecycle record
for each stable room in shared chat, with a monotonically increasing
generation, desired open/closed state, observed phase and outstanding
operation receipts. Serialize lifecycle actions for that room. A reopen
supersedes the old close intent immediately, but does not expose posting or
issue a new owner JOIN until older conflicting IRC commands are settled.

The practical rule is: **an old close must never freeze or part an owner from
the newly active generation**. A local generation check alone cannot enforce
this after a command has already reached IRC. Persist the operation's intent
before writing, including room, generation, acting account, exact effect and
command/connection identity. Check the current generation before dispatch;
cancel undispatched obsolete work. Do not replay an uncertain MODE or PART
just because a worker restarted or its reply was lost.

#### Close and supersession ordering

1. Record closing and stop admitting new shared sends/joins for that room.
   Drain already admitted sends until no further shared write will be issued
   for them, under their existing deadlines. Record any indeterminate outcome;
   do not wait for its dismissal before freezing. Do not extend deadlines or
   give conversation accounts a moderation bypass.
   Until the moderator's block is verified, direct IRC clients can still
   post; include their history under D-88. Do not claim the IRC freeze has
   happened merely because plateia shows a grey title.
2. Set and verify the moderator's posting block. Build and verify the D-88
   history snapshot and D-86 image copies. An undismissed unconfirmed send
   or any other required unresolved evidence still prevents completion.
3. Before each owner PART, check that the close generation is still current
   and that its archive is verified. Record and settle each account's
   operation separately. A reopen stops remaining undispatched PARTs; it
   does not undo or pretend away one already sent.
4. Mark the room closed only after the current generation's required archive
   and membership steps are verified. Persist that closure time once for
   D-66/D-88's window. If a reopen has superseded it, retain any verified
   snapshot but never publish the obsolete operation as the room's current
   closed state or start a false closure window.

All slow exports and copies carry their room generation. Recheck it at
publication, rather than holding a network operation or the global sender
queue hostage to archive I/O. A stale export cannot overwrite the current
archive pointer, clear newer errors or mark a reopened room closed. Keep
previously verified archive generations until their replacements are fully
verified; failed work and uncertain references remain recoverable.

#### Reopen and the uncertainty case

On a verified reopen trigger, record the newer desired generation and stop
dispatching old close effects. Finish accounting for effects already sent.
Perform the required on-reopen history reconciliation, preserve history and
images, then remove the posting block and verify the resulting room state.
Only after this is safe does the room become active. Opening/joining it then
acts as the bound owner under D-71; do not automatically join every owner.

If a connection fails with an old MODE or PART still potentially executing,
keep the room unavailable for new shared posts and joins until completion or
non-execution is proved. Show a grey title with `Reopening…` while checking;
after 15 seconds without resolution show `Reopening blocked: a previous room
change is unconfirmed.` A pending close uses the corresponding
`Closing blocked: a room change is unconfirmed.` The bound changes the
display, not the outcome. Recovery continues when evidence arrives; there
is no dismissal that allows a conflicting reopen, and no automatic server
restart or forced client disconnect. If existing interfaces cannot supply
the proof, report the blocker to the project manager first. The manager
escalates to the owner only when hands-on action is needed, under existing
live-action authority rules.

This can delay a reopened issue's room after an outage. That is the explicit
tradeoff accepted by D-89, preventing old cleanup from affecting new work.
It does not promise that all direct IRC clients obey plateia's pending UI:
shared chat reports real membership/modes and any outside change honestly.

#### Evidence boundary and implementation checks

Ergo 2.19.1 processes commands in order within one session's command loop.
That supports checking a correlated mutation result and a subsequent
same-session read while the session remains intact; it does not establish
the fate of another session's pending handler after disconnection.
[Ergo client loop](https://github.com/ergochat/ergo/blob/v2.19.1/irc/client.go).
MODE applies changes before announcing them, with separate history work.
[Ergo mode handler](https://github.com/ergochat/ergo/blob/v2.19.1/irc/handlers.go).
Neither a fresh connection's state query nor a local generation number alone
proves an older command cannot run later. The adapter must distinguish
confirmed effect, confirmed rejection/non-dispatch and unresolved execution;
do not infer the latter two from a timeout. A PONG is not archive durability
evidence under D-88.

CHAT-18 owns the lifecycle record/generations; CHAT-19 owns close effects
and recoverable archive publication; CHAT-20 owns reopen and correction;
CHAT-21 displays these states. Keep the approved slice boundaries. Verify
reopen before dispatch, during a partial owner cleanup, after a lost command
reply, during archive copy/publication and after process restart. Verify
that delayed old jobs cannot change the newly active generation.

D-89 settles this recovery behavior, not all of K-4. Tracker
association/evidence, exact persistent record and archive-publication
contracts, and the adapter's concrete completion evidence remain to be
resolved before affected issue drafting. No live operation is authorized.

### P-79. Keep durable room associations and reconcile ambiguous tracker links

Accepted by D-90, owner decision 2026-10-21. Shared chat owns one
durable registry of work-item-to-room associations outside the repository.
GitHub supplies item identity, current state and declared issue links; an
editable PR description does not itself rename a room or move its history.
Keep stable repository/item IDs, item kind, display number/URL, room ID,
association revision and the evidence/authority that established each link.
Never identify an item by its number without its repository and kind.

An issue has one work room; all its associated PRs use that room under D-11.
A standalone PR has its own room under D-59. Reopening or replacing work
uses the existing association and room under D-51, with D-89's generation
checks. Creating an association record does not create an IRC room for
untouched backlog items; D-9's first-use rule still applies.

#### Establishing an association

- The shared workflow can explicitly register the issue/PR link while the
  manager opens or takes up a PR for that issue. Validate both tracker IDs
  and project eligibility; record the authenticated authority and command
  ID. Include this registration in D-8's owner-specific skill changes. It
  covers cases such as a PR targeting a non-default branch. It changes no
  GitHub issue, PR, description or sidebar link.
- Without an existing binding or explicit registration, use the complete
  GitHub `closingIssuesReferences` result, including manual links. Exactly
  one eligible issue establishes the issue-room association. A confirmed
  empty result establishes a standalone-PR room on first use. An error,
  truncated page set or inaccessible referenced item is not an empty result.
- More than one linked issue, an out-of-project link or conflicting explicit
  and GitHub evidence produces an association conflict. Do not pick the
  lowest number, parse arbitrary mentions as work ownership, create a
  grouped room or duplicate the PR's conversation into several rooms.
  Return the conflict to the project manager first. New ambiguous room
  creation/routing waits for resolution; existing rooms remain usable
  according to their already established lifecycle state.

GitHub's GraphQL API exposes `closingIssuesReferences` with pagination and
manual-link controls.
[GitHub PR schema](https://docs.github.com/en/graphql/reference/pulls#pullrequest).
GitHub documents that closing keywords only establish links for PRs targeting
the default branch, that multiple issues may be linked, and that editing a
description can remove a keyword-based link. This is why a missing link is
not proof against an explicit workflow association and a changed link cannot
safely move a conversation by itself.
[GitHub issue-linking rules](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).

#### Keeping the association stable

Retain historical associations after PR closure; do not rebuild them from
only currently open PRs. A replacement PR explicitly registered or uniquely
linked to the same issue joins its existing association set. Record the
source and revision of each observation so an older response cannot erase
a newer registration or silently undo a resolved conflict.

For a GitHub-derived binding, removing or changing the declared issue link
raises a reconciliation conflict rather than silently changing rooms. An
explicit workflow binding may legitimately have no GitHub closing link;
a contradictory nonempty GitHub link still needs reconciliation. A PR
first used as standalone and later linked to an issue is also a conflict,
even if that issue's room has not yet been created. Do not silently reparent
it or erase the standalone conversation.

While a relevant conflict is unresolved, stop automatic closure/reopen
decisions that depend on that association set and expose the reason to the
manager and shared read view. Preserve the last confirmed binding and its
evidence. Do not reroute a submitted message or change its immutable room.
The manager may repair routine registration errors within established
authority; moving an already-used binding between rooms requires an explicit
owner-approved reconciliation plan. No automatic room merge, transcript
copy, history rewrite or tracker edit is part of this proposal.

This registry is the canonical room association record, not a second tracker
for issue/PR state. Verify live open/closed/reopened evidence through shared
GitHub reads before lifecycle actions. An unavailable or inconsistent read
cannot manufacture closure. Carry forward the approved central polling,
caching, rate-limit handling and GitHub freshness defaults from
`plateia_design.md` D-49/D-52; do not add polling per browser or per card.
The exact completeness and close/reopen-event contract remains K-4 work.
The inspected shared chat sender/bridge has no implemented tracker-to-room
registry. This is a contract to add, not a claim that the earlier board
design already provides a working room adapter.

CHAT-18 owns registry/discovery and workflow registration; CHAT-21 consumes
the stable room identity and conflict result; CHAT-22 reconciles legacy and
parked associations during the approved per-project cutover. Keep the 23
slice boundaries. Verify unique/manual/absent/multiple links, non-default
branches, partial reads, stale responses, changed links after first use,
replacement PRs, concurrent first use and restart recovery with invented
items. D-90 settles association ownership and conflict behavior;
it does not settle all of K-4 or authorize any live registration or cutover.

### P-80. Drive lifecycle from fresh tracker evidence and distinct rounds of work

Accepted by D-91, owner decision 2026-10-21. Shared chat observes GitHub
centrally per project and updates D-90's registry and D-89's desired lifecycle
state. It does not change the tracker. Manager completion posts, labels,
missing list entries and elapsed time are not substitutes for verified
issue/PR state. Manager/workflow activity may request an immediate coalesced
refresh; no webhook endpoint or polling in the browser is required.

Keep the approved defaults from `plateia_design.md` D-52: poll every 60
seconds, use conditional reads where supported, back off to 5 minutes on
errors/rate limits, mark GitHub evidence stale after 5 minutes and unknown
after 30 minutes or immediately on authentication failure. An unavailable
refresh keeps the last confirmed lifecycle and its evidence age; it does
not close, reopen or create a room by inference.

#### What a complete lifecycle observation contains

For an issue room, read the issue, all declared PR links including closed
and manually linked PRs, and every additional explicit/historical PR binding
in D-90's registry. Resolve each relevant PR's current state and issue links.
Follow every page needed for that set; a missing, inaccessible or failed
page is unresolved evidence. Validate links through P-79's conflict rules
before using the set for closure. Never conclude that the last PR closed
from a query that returns only currently open PRs.

Read the relevant issue/PR close, merge and reopen timeline events, with
stable event IDs, object identity and timestamps. Keep a durable event
checkpoint and enough retained evidence to reconstruct the current work
round after restart. Deduplicate by event ID, not timestamp. Traverse back
to the known checkpoint or exhaust the filtered history on bootstrap;
an absent checkpoint requires a full relevant-history reconciliation, not
a guessed gap. Use timeline order within an item. If cross-item event order
matters and timestamps cannot resolve it, retain an explicit ambiguity for
the manager rather than inventing an order from opaque IDs.

GitHub exposes `closedByPullRequestsReferences` with `includeClosedPrs`
and manual-link controls, and `timelineItems` with typed close/reopen events.
Reopen events have stable IDs and creation times.
[GitHub issue schema](https://docs.github.com/en/graphql/reference/issues).
Each connection must be paged through its `pageInfo` cursors.
[GitHub pagination](https://docs.github.com/en/graphql/guides/using-pagination-in-the-graphql-api).
A read-only query against an existing repository item verified the closed-PR
inclusion and filtered event query shape; it did not exercise concurrent
tracker edits, missing pages or recovery. No tracker item was modified.

#### First use and a later round of work

Persist which tracker events and already-closed PRs precede the current
round, separately from the room's IRC/archive operation generation. This
prevents an old closed PR from immediately undoing a newly reopened issue.
Historical associations and logs remain; a new round is not a new room.
Use verified creation/close/reopen events to include a PR that opened and
closed between polls; observing it open in a prior poll is not required.

| Verified situation | Desired room state |
| --- | --- |
| First use of an open issue with no existing room | Open for this round. Record prior closed PRs as history; they alone do not immediately close the newly used room. Any currently open associated PR belongs to the current round. |
| Any confirmed associated PR remains open, including a draft PR | Keep the issue room open, even if the issue itself is closed. |
| The last open PR from the current round closes, merged or unmerged, with no associated PR still open | Begin closure under D-11/D-89. The issue may remain open; that unchanged open state does not immediately reopen the room. |
| The issue closes and no associated PR is open | Begin closure, including a reopened round that has not yet produced a new PR. |
| A verified issue reopen starts a newer round, with no later event ending that round | Reopen the existing room under D-89. Previously closed PRs do not close this new round by themselves. |
| A bound PR reopens, or an open replacement PR is associated with the issue | Reopen/use the same issue room; any other open associated PR keeps it open. |
| A standalone PR closes or reopens | Its own room follows that PR's verified state, preserving identity/history. |
| First use of a closed issue with no open associated PR, or a closed standalone PR, and no existing room | Do not create an IRC room just to archive it. Return `This work has no active room.` Existing archived identities still use D-58's archive notice. |

An old archived-room link is never a new first use. Neither a comment nor a
change to `updatedAt` is a reopen event. A closed-then-reopened issue between
two polls is detected through its timeline even if both observed snapshots
say open. After an outage, reconcile the latest intended state before
dispatching effects; do not replay obsolete open/close transitions as live
room operations. If state and timeline evidence disagree, keep reconciliation
pending and expose it through the manager-first recovery path.

#### Transition validation and concurrent changes

Store each observation's source IDs, query/page coverage, read start/end,
source versions/timestamps, association revision and local observation
revision. Serialize application per project/room; an older response cannot
overwrite newer evidence or a later explicit registration. A changed binding
revision invalidates an in-progress closure decision.

Before entering closing, require a successful revalidation of the complete
relevant issue/PR/link/event set. Revalidate again before final closed-state
publication after archive and membership work. Evidence used for these
decisions must still be fresh under the approved thresholds; a failed latest
read is not overridden by a formerly fresh cache. If the second check cannot
complete, retain the verified archive and remain visibly closing. A confirmed
newer reopen supersedes the close and invokes D-89's cleanup fence.

A positive, verified reopen can stop obsolete cleanup promptly; it does not
skip the remaining checks before the room becomes active. Check the local
generation before every irreversible lifecycle effect and at final commit.
GitHub reads and local IRC effects are not one transaction: a remote change
after the latest successful read may be discovered on the next refresh.
Reconcile it to the same room under D-89; never claim instantaneous agreement
with GitHub or a cross-system lock.

This contract belongs in CHAT-18; CHAT-19/CHAT-20 consume its observation and
generation receipts, and CHAT-21 displays stale/unknown/conflict states.
No board, additional room type or new slice is introduced. Verify a last-PR
close with another PR on a later page, draft PRs, explicit non-default-branch
links, issue close/reopen between polls, replacement PRs, historical closed
PRs at first use, equal-time ambiguous events, partial/rate-limited reads,
new registration during pagination and reopen during archive cleanup.

D-91 settles tracker observation and transition semantics. K-4
still needs the exact durable registry/archive publication contract and
concrete IRC completion evidence. No live polling setup or tracker mutation
is authorized by this proposal.

### P-81. Publish immutable archive bundles through one durable shared control store

Accepted by D-92, owner decision 2026-10-21. Put the shared bridge's
durable send/control records, room registry, lifecycle operations and change
feed in one local SQLite database outside the repository. CHAT-5 establishes
the durable send store; CHAT-18 adds room records and migrations to that
store. This selects its storage backend without changing K-1's accepted
external contract or implementing rooms in the early text slices.

The bridge remains the sole mutation owner under D-77's process lock.
Plateia and command-line consumers use the shared API; they do not edit
tables. D-86's image catalog remains separately owned by plateia behind its
local asset command. Neither the Ergo database nor the image catalog is
attached as part of a claimed cross-store transaction.

#### Durable records and transaction boundaries

The shared store records stable room/item identities and associations;
tracker observations and work-round checkpoints; desired/observed lifecycle
generations; send admission and drain state; each IRC operation and its
receipts; archive candidates and published revisions; unresolved gaps;
owner-set/membership evidence; closure time and late-check deadline; and
versioned change-feed entries. Keep record/schema versions and idempotent
command IDs. Unknown versions, corrupt state and failed writes produce an
explicit unavailable state, never an empty replacement registry.

Use short transactions to change room admission and register sends against
the same gate. Closing commits the nonaccepting gate before collecting the
previously admitted attempts. A send racing that transition is either
durably admitted to the drain set or rejected before any IRC write. Check
the room generation and explicit drain authorization again at dispatch;
already admitted attempts may finish under D-89, without admitting new ones.
Keep slow tracker/history reads,
image exports and IRC waits outside database transactions.

Use SQLite rollback-journal mode (`DELETE`), `synchronous=EXTRA`, and
`fullfsync=ON` on macOS; read back effective settings and verify filesystem
support at deployment. Keep the database on local storage. Use the supported
Python SQLite binding, with no additional database service. The transaction
and file-flush guarantees rely on the operating system and storage honoring
their contracts; qualification must not substitute a checksum or a successful
rename for durable flushing.
[SQLite atomic commit](https://www.sqlite.org/atomiccommit.html),
[SQLite durability settings](https://www.sqlite.org/pragma.html#pragma_synchronous).

#### One self-describing log per archive revision

Each immutable archive directory contains one UTF-8 JSONL conversation log
and the verified capped image copies it references. Its version-one records
include a header with room/item identity, work round, lifecycle generation,
snapshot time and D-88 coverage/limits; ordered logical messages with native
aliases, authenticated sender facts, timestamps and exact text/line breaks;
asset records with relative path, media type, dimensions, size and digest;
and gap notes with later-confirmation annotations. A final record gives
record counts and a digest of the preceding log bytes. The control store
also records the complete file digest and verified image manifest.

This is D-70's one structured format, without a plaintext transcript or an
archive viewer. An agent can interpret the log and local images without
the web process, SQLite access or a project-specific reader. The log states
when its history snapshot was captured; it does not pretend that owner PART
and final closure had already completed at that earlier instant. Actual
closure time and its checking deadline belong to the lifecycle receipt.

Paths are generated from opaque room/revision IDs beneath the configured
private archive root. The browser never supplies paths. Reject traversal,
unexpected symlinks and identity/digest conflicts. A new revision never
overwrites a published file in place.

#### Prepare, seal and publish

1. Under the current close/correction generation, persist a candidate ID,
   expected base archive revision and the room conversation/gap revision
   being captured. Pin its full managed-image set through D-86 before export.
2. Write the structured log and copied images into a private staging
   directory on the same filesystem as the final archive location. Re-read
   them, validate complete message/alias identities and counts, and verify
   every referenced image's bytes against its manifest. Include D-65's
   dismissed gaps; any other known missing evidence blocks completion.
3. Flush the files and required directory metadata, then rename the sealed
   directory to a unique immutable revision path and durably flush the
   containing directory. Check failures explicitly. Same-filesystem rename
   supplies atomic visibility, not an atomic transaction with SQLite.
   [Python filesystem operations](https://docs.python.org/3.11/library/os.html#os.rename).
4. Record the verified candidate and its hashes in the control database.
   Only then may D-89's owner cleanup proceed. A prepared candidate is not
   yet the room's authoritative closed state or current archive pointer.
5. After required membership cleanup and D-91's fresh final tracker check,
   use one short transaction to compare the current lifecycle generation,
   association/observation and owner-set revisions, candidate/base revision,
   and known conversation/gap coverage. If still valid, publish the archive
   pointer, closed state, closure time, 24-hour deadline, gap markers and
   change-feed event together. A mismatch retains the candidate and resumes
   reconciliation; it cannot publish stale closure.

A newly observed message or changed gap evidence before the final commit
requires reconciliation of the candidate's coverage. Do not silently omit
known evidence just because copying already finished. D-88 still governs
previously unseen posts appearing after closure.

For a correction, follow the same prepare/seal/verify sequence and compare
the expected base revision before atomically replacing the current pointer
and updating the relevant markers. Keep the original closure time/deadline;
a correction is not another closure or a reopen. A superseded generation
cannot overwrite a newer archive or remove newer unresolved gaps.

#### Restart and retention

On startup, reconcile durable candidate/operation records and filesystem
artifacts before resuming lifecycle work. An unfinished staging directory
is not an archive. A sealed directory without a publication receipt is a
candidate that may be reverified, not proof that cleanup/closure completed.
A committed pointer must name a complete verifiable bundle; missing or
corrupt bytes become a visible recovery incident. Preserve any older verified
revision without falsely declaring it a complete substitute for the lost
newer one. Route recovery through D-89's manager-first path.

Publish the associated feed entry in the same database transaction as its
state change, so restart can replay it without inventing closure twice.
Retain published archive revisions in milestone 1; there is no automatic
archive-pruning policy. They are previous versions of the same structured
record, with one current pointer, not competing conversation authorities.
An unchanged late check needs only a control receipt, not another full copy.
Keep uncertain staging/export pins until their lack of use is proved;
D-49's 24-hour asset cleanup does not delete retained conversation archives.

This makes cross-store completion an ordered, recoverable protocol rather
than a distributed transaction. A crash may leave extra files or pins, but
must not expose a half archive as complete. CHAT-18 owns the registry schema,
CHAT-19 publication, CHAT-20 correction, and CHAT-15 the existing asset API;
the approved slice boundaries remain. Verify interruption at every numbered
boundary, disk-full/flush errors, missing/corrupt files, web-down export,
simultaneous correction/reopen and stale candidates with invented data on
the supported platforms. No durability fault test has been run for this
proposal yet.

D-92 settles the durable registry/archive publication contract.
K-4 still needs the concrete IRC completion and restart-evidence
contract before affected issue drafting. No database, service, archive or
configuration is changed live by this proposal.

### P-82. Confirm current IRC effects and recheck room state after reconnects

Accepted by D-93, owner decision 2026-10-21. A confirmed IRC
operation proves its effect in the running server, not that the effect is
durable across an Ergo crash. Use the receipts below, retain D-89's blocking
rule for uncertain old commands, and reconcile room permissions and owner
membership after reconnecting. This explicitly accepts the restart gap below;
it does not introduce a server patch or automatic server restart.

#### Command receipts and sequencing

Before writing a lifecycle command, durably record its room, generation,
acting account, connection identity, exact requested effect and write-start
state in D-92's control store. Use a single room target and narrowly scoped
effect per operation, rather than a multi-room command whose partial success
is ambiguous. Serialize conflicting commands for that room across the
moderator and owner connections. Keep unrelated rooms and archive I/O out of
that serialization boundary.

| Evidence | Meaning and permitted next step |
| --- | --- |
| Intent cancelled before any write could start | Confirmed non-dispatch; the obsolete operation cannot affect a new generation. |
| Correlated result plus a completed, ordered read on the same uninterrupted command session | Record the actual applied or rejected effect and current state; later conflicting work may proceed after its own checks. |
| Partial write, lost reply, connection break before completion proof, or crash before the receipt is durable | Unconfirmed execution; retain the operation and D-89's fence. No blind replay and no conflicting reopen. |
| Matching state read on a new connection | Current observation only; it cannot settle an older possibly executing command. |

For MODE, inspect the actual applied changes and then query channel modes;
verify D-87's registration, account grants and current participant privileges
before treating `+nm` as a posting block. A no-op mode request may have no
change announcement; its ordered query can prove the requested current state.
For PART, use the authenticated owner's own session, its correlated response
and a complete subsequent self-membership read. An already-absent response
can satisfy the desired absence after verification. Do not substitute another
client's nick, an incomplete NAMES list or silence for account membership.
JOIN likewise needs the bound account's response and verified membership.
Correlate replies using negotiated labels where available, otherwise a
strictly serialized command/read exchange; qualify either path against Ergo.
Errors are interpreted per operation: a permission failure settles execution
but does not satisfy the requested room state.

An ordered read establishes that the earlier synchronous handler has
returned. It is not proof that history or asynchronous persistence finished.
Keep source connection/command IDs, parsed results and verification revisions
in the durable receipt; the final D-92 publication checks those revisions.
Invalidate the relevant verification on an observed conflicting event or
connection loss. Concurrent outside actions can occur after any observation;
report them as conflicts rather than claiming an atomic transaction with IRC.

#### Evidence for the durability limit

Ergo 2.19.1's session loop executes each command handler before reading the
next command. Owner PART removes membership before returning; always-on
membership changes schedule a separate writer. A completed ordered exchange
therefore supports a current-state receipt, but not a durable membership
receipt. [Ergo client implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/client.go).

Channel modes also use asynchronous writeback. In `Channel.performWrite`,
serialization/storage failures are logged and dirty bits restored; locally
shadowed error variables do not populate the named return value. Even a
successful caller return must not be promoted to a disk-persistence proof.
This is read-only source evidence, not a reproduced live failure.
[Ergo channel implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/channel.go).
MODE applies changes before announcing them; the later ordered query concerns
the running state, not a storage flush.
[Ergo command handlers](https://github.com/ergochat/ergo/blob/v2.19.1/irc/handlers.go).

#### Reconnect recovery and the accepted gap

On bridge startup or loss of a relevant IRC connection, mark affected current
observations unverified. Before resuming shared sends, joins or lifecycle
completion, reconcile the durable registry with live ownership, grants,
modes and account membership. Include archived registered work rooms in this
event-driven state reconciliation; this is not an indefinite history watcher.
Preserve their current verified archives and original closure/check deadlines.

Where prior operations are settled and ownership/grants still match, issue
new generation-checked operations to restore the registry's required posting
mode and, for a still-closed room, repeat its owner cleanup when needed.
Never part an owner from a newer open generation. Unexpected ownership,
grants or evidence of a deliberate outside override goes to the manager;
do not silently take over a channel or strip permissions. Missing channels
or registration are also conflicts, not permission to recreate an archive's
identity without reconciliation. New live setup still requires its existing
owner confirmation.

An old unconfirmed command remains blocked under D-89 even if a new query
looks right. Recovery must obtain its completion/non-execution evidence, or
verified evidence that the old server process and its handlers ended, then
reconcile the current state. A local socket close, changed connection ID,
matching state or elapsed time is not that proof. Evidence unavailable through
the qualified adapter goes to the manager first; hands-on action remains the
owner's call. Never restart Ergo or disconnect other clients automatically.

**Accepted explicit limit:** after an Ergo crash, saved permissions or owner
memberships may be older than their last confirmed running state. Until
shared chat reconnects and repairs them, an archived room may temporarily
allow direct IRC posts or show restored owner joins. Shared posting stays
blocked while its state is unverified, but it cannot fence independent IRC
clients during that gap. There is no promised time bound while the bridge or
server is unavailable. Ordinary deliberate direct-client changes likewise
remain observable external actions, not something plateia can transact away.

Newly discovered posts follow D-88: reconcile history within the original
24-hour window and on legitimate reopen, without extending the window on a
restart. Posts arriving outside that window can remain absent from the
archive until reopen. Report detected permission/membership conflicts and
failed checks; preserve the archive rather than pretending it is invalid or
that the room has legitimately reopened. During an unresolved close/reopen,
use D-89's existing grey/blocked behavior and 15-second display bound.

This qualifies D-87's posting block and D-71's completed membership cleanup
as verified running-state guarantees, not crash-proof server persistence.
The alternative, if this limit is unacceptable, is evaluating a different
IRC server; never patch Ergo or silently claim a stronger guarantee.

CHAT-18 implements operation receipts and reconnect reconciliation;
CHAT-19/CHAT-20 use them for close/reopen, CHAT-21 displays the existing
unverified/blocked states, and CHAT-23 qualifies the combined behavior.
Keep the approved slice boundaries. Test lost/partial replies, no-op MODE,
already-absent PART, concurrent outside joins/mode changes, stale generations,
bridge restart before receipt commit, and isolated Ergo crashes before mode
or always-on membership writeback. Verify no old command can affect a newly
active generation and no outage restarts the history window. These fault
tests have not run; activation requires their sanitized evidence.

D-93 settles the IRC completion and restart-limit choice. Review
the combined K-4 contract and cutover obligations before declaring that gate
resolved; neither this proposal nor its approval calls the whole design ready.
No live commands, configuration, database reads or server changes are
authorized by this proposal.

### P-83. Allow image URLs to be unavailable while the web process is stopped

Accepted by D-94, owner decision 2026-10-21. Keep image HTTP serving
with the plateia web process in milestone 1. When that process is stopped,
the page, uploads and opening managed image URLs are unavailable, including
opening those URLs from an independent IRC client. IRC still carries the
links; the capped files, asset references and local archive copies remain
intact. Starting the web process restores the same URLs without reposting.

This limit preserves the selected single web server and
short-lived asset command, without introducing a separate image-serving
service. Shared send/pin/export operations continue through the local asset
command, never through an HTTP image fetch. Archive readers use the recorded
relative file paths. This does not relax D-93's requirement that IRC, bridge,
managers, clients and room lifecycle work while the web process is stopped.

D-94 resolves the earlier ambiguity: D-93 does not guarantee remote image
viewing while the web service is stopped. Do not add an independent image
server or require the web process to stay up for shared lifecycle work.

CHAT-15 owns serving and the local asset command; CHAT-23 must check the
approved availability boundary with the web stopped and after restart.
No slice boundary change or live action is proposed.

## Open questions

### Q-1. Where does the new room model live?

**Resolved by D-8.** Issue/PR rooms are the standard shared model; include an
owner-specific global skill change when the room-model work is implemented.

### Q-2. What is a work room's exact lifecycle?

**Design contract resolved by D-87–D-94**, together with the earlier lifecycle
decisions below. Isolated implementation checks and owner-confirmed setup
remain required; accepted evidence limits must remain explicit.

**Opening resolved by D-9:** create on first use, including issue review.
**Opening from plateia resolved by D-56:** join as the owner and create if
missing, within the shared room model.
**Creation eligibility resolved by D-59 for milestone 1:** configured
permanent rooms and real issue/PR rooms only. Custom rooms with their own
rules are deferred future work, not designed here.
**Archived links resolved by D-58 for milestone 1:** show “This room is
archived.” without joining, reopening or creating a replacement room.
**Switching and membership controls resolved by D-60:** switching keeps
existing joins; every room page has Join/Leave, with Leave sending IRC PART.
Archived pages keep Join unavailable under D-58.
**Connection lifetime resolved by D-63:** closing the browser keeps the
account joined; membership is shared with its other IRC clients. D-64 requires
Ergo always-on for every owner account, retaining only joined rooms if every
IRC client disconnects.
**Archive membership resolved by D-71:** archive parts every owner account;
opening the legitimately reopened room joins again as the bound account.
**Issue closure without any PR resolved by D-10:** close and preserve the log.
**Multiple PRs resolved by D-11:** share the issue room and close after the
last open PR closes.
**Archived behavior resolved by D-14:** leave active lists, retain the file
log and reject new posts until reopened.
**Closing resolved by D-15:** keep the title grey until accepted posts are
settled and the file archive is verified; keep unresolved problems visible.
**Design conversations resolved by D-16:** use permanent general chat;
no dedicated design rooms. Existing design rooms are discarded at rollout
without conversation migration or saved logs.
**Reopened issues already resolved:** prior `plateia_design.md` D-66 (owner
decision 2026-10-08) reuses and unarchives the same room. Preserve that rule;
this milestone's D-10/D-11 replace the earlier board-dependent closure trigger.
D-11 and V-2 say PR closure, not only merge, so closed-unmerged PRs use the
same closure rule. **PR resumption resolved by D-51:** reopened and
replacement PRs reuse their existing issue/PR room with history and images.
**Work without a tracker item resolved by D-52:** use the project's main
room; design remains in general. Existing rooms, including grouped request
rooms, are addressed under Q-8. D-88/D-92/D-93 settle snapshot coverage,
verified preservation and recovery after outages or late posts. No archive
browser is included in this milestone.
D-65 permits closure after dismissal using current IRC history, with an
explicit unconfirmed-ID note and closed-room gap marker. D-66 reconciles a
later-confirmed send within 24 hours after closure and on legitimate reopen.
D-87 accepts P-76's shared moderator ownership of work-room IRC permissions;
account creation is confirmed with the owner at setup.
D-88 accepts P-77's explicit history-snapshot limit with bounded late checks
covering every client.
D-89 accepts P-78's serialized durable generations and visibly blocked
recovery when an old IRC mutation is unconfirmed, with manager-first recovery.
D-90 accepts P-79's durable shared association registry, initial GitHub/workflow
link evidence and manager-first conflict handling.
D-91 accepts P-80's fresh tracker observation and work-round transition
contract, including first use and reopen history.
D-92 accepts P-81's shared control store and immutable archive publication
protocol.
D-93 accepts P-82's current-state IRC receipts, reconnect reconciliation and
explicit crash-persistence gap, and requires shared lifecycle work to continue
with the web process stopped. The combined K-4 coverage review below maps
these accepted contracts to the affected slices.
D-94 resolves P-83's web-off image-URL availability question without changing
the accepted local image-archive contract.

### Q-3. What interface separates plateia from shared chat?

**K-1 design contract resolved by D-77–D-81:** the accepted P-70 API and
feed, D-80 overlap, D-79 clock, D-78 native batch and D-77 sender ownership
apply together. The evidence and implementation obligations below remain;
they are not unanswered design choices or claims of completed verification.

Implement the accepted discovery, history/live handoff, owner posting,
operation identity, routing, delivery and presence contracts. Recheck the
canonical source during issue drafting. New workflow behavior belongs in
the skills repository. Do not revive retired packages by inference.

Earlier `plateia_design.md` D-16 selected a shared, versioned Python API
inside the existing bridge/client processes, without a new initial IPC
daemon. Carry that interface direction forward alongside `plateia_design.md`
D-78's canonical skills ownership. Existing connection/post helpers are not yet the typed
history, membership, delivery and serialized-sender contract required here;
define those extensions without assuming the retired capture is available.

D-34/D-45 require browser-created IDs, one shared sender serializing
submission/check/cancel/retry, and a history check before any same-ID retry.
The writer must not start an IRC write after its attempt's deadline; a write
already in flight remains unconfirmed until settled. D-77 establishes writer
ownership; D-79/P-68 fixes the clock mapping and D-80 selects overlapping
IRC history. D-81 accepts P-70's API and history/live handoff,
keeping retry generations
distinct from delayed originals. The current PONG/text-match sender
does not satisfy this. D-36 rejects browser recovery storage; enforcement
must work after the browser disappears. P-37 records the proposed protocol.
Verify its evidence rules and refusal to retry uncertain outcomes. A missing
bridge entry, elapsed timer or newly connected reader is not cancellation.
P-36 is rejected: no IRC patch. D-47 resolves P-39: keep Ergo, allowing an
unprovable send to remain unconfirmed without retry. The owner may dismiss
the notice and choose to send a newly typed message with a new ID. No server
comparison is needed. Do not hide this limitation behind the syncing notice.

D-37 requires explicit logical grouping across any transport fragments,
exact content preservation and the same identity in live delivery/history.
Existing per-batch reassembly is insufficient for oversized posts. Define
the relationship between browser IDs, durable message IDs, batch references
and ordered parts; verify complete logical messages and sender cutoff/recovery
before dispatching an agent instruction.

D-41 requires a shared, typed per-message/per-recipient delivery outcome
from the bridge's records. Include late acceptance after dead-lettering,
structured recipient identity and freshness; plateia must not implement a
parallel delivery-state inference engine over textual trace output.

D-61 uses two real owner accounts with equivalent authority. The shared
sender coordinates both while binding each operation to its original account.
The shared ownership contract is already delivered in skills commit `7ce2401`:
consume its canonical owner set for permissions, including cited authority
and owner-only actions. Do not treat the phone account as an assistant or
replace its attribution with the desktop account. D-62's browser binding
selects an existing account; it cannot grant shared-chat authority.

### Q-4. What are the posting and recovery interactions?

**Draft recovery resolved by D-23:** keep each room's unfinished message and
images across switches and reloads on the same device.
**Disconnected drafting resolved by D-24:** keep typing and disable Send.
D-34 supersedes the minimal framing for submitted messages.
**Keyboard submission resolved by D-31:** desktop Enter sends,
Shift+Enter adds a line; phone Return adds a line and Send posts.
**Text rendering resolved by D-72:** literal text, spacing and line breaks,
with clickable links and inline images. Markdown is deferred to a later
milestone. D-73 limits inline rendering to plateia-stored images.
**Sending and failure presentation resolved by D-32:** small grey
“sending...” below the message; failed messages remain temporarily in the
app with a red exclamation mark that retries when clicked. Failed entries
are not persisted.
**Saved-draft transition and failure lifetime resolved by D-33:** Send
clears the draft immediately; only one failed send is shown, cleared by any
later message, including a newer failure.
**Deadline and retry resolved by D-34:** countdown, authoritative server
check/cancel, normal display if posted, retry icon if cancelled, same-ID
retry with at-most-once posting. The delegated duration is 10 seconds;
D-45 makes this the writer's decision cutoff, not an IRC commit deadline.
**Unanswered deadline check resolved by D-35:** keep “sending... (0)” for
five more seconds, then small grey “unconfirmed: awaiting server”; no retry
until confirmed cancellation, and normal display on confirmed posting.
**Reload behavior resolved by D-36/D-45:** no persistence or reappearance of
unconfirmed sends. The shared sender starts no write after the attempt's
deadline. Once an earlier write has settled and history is complete, the
message is in history or will never post; the owner retypes if missing.
Q-3 must establish that settlement and history coverage.
**Reload history notice resolved by D-46:** “Syncing history...” for at most
15 seconds, then “unconfirmed: awaiting history” until settlement and
history catch-up. Neither notice restores a pending send or authorizes retry.
**Unprovable outcome resolved by D-47:** keep Ergo; unconfirmed can persist
indefinitely without retry. The owner may dismiss its notice; retyping and
Send creates a new ID. **Dismissal persistence resolved by D-48:** the shared
sender remembers it across reloads and devices; new uncertain sends get new
notices. Agent-delivery errors under D-42 remain non-dismissible.
**Long-message presentation resolved by D-37:** one web message with line
breaks preserved and explicit transport grouping, never time-based merging.
**Message size resolved by D-38:** 64 KiB of text, with a warning before
Send when exceeded; retain the draft rather than submitting or truncating.
**User selection resolved by D-40 for milestone 1:** insert the user's
`@handle` and focus the current draft, retaining text and images.
Distinguish server acceptance from
recipient acceptance without importing milestone 2's full receipt display.
**Posted-message delivery errors approved by D-41:** keep the error beside
the message, based on the bridge's own delivery/acceptance record. Any
missing shared read capability belongs in the skills repository.
**Resolution settled by D-42:** only the intended agent's acceptance clears
its delivery error; no dismissal. A resent or redirected copy has its own
tracking and does not clear the original.
**Milestone-1 notification settled by D-43:** existing bridge phone alerts
stay unchanged alongside the chat error; no new bridge notification work.

### Q-5. How are room membership and session status presented?

**K-2 design mapping resolved by D-82:** P-71 incorporates automatic
owner waits from typed questions and the accepted evidence/freshness rules.
Its source-adapter checks remain required under the delivery-plan gates.

**Role and status colors resolved by D-29:** retain role colors, with grey
idle, green working and amber waiting dots for agents. Human users and
assistants have no dot. Similar role/dot hues are acceptable.
**Project membership already resolved by V-4/D-5:** every active session
working on a project appears in that project's rooms, including manually
opened sessions. General lists active agents across projects.
**Shared chat-testing list resolved by D-53:** `#chat-test` also lists all
active agents across projects; this changes the list only.
**Owner presence resolved by D-54:** actual logged-in IRC membership of
the owner accounts, not an automatic entry in every room.
**Account display resolved by D-55:** separate entries for the two accounts.
**Opening/joining resolved by D-56:** opening joins as the owner and creates
a missing room within the shared lifecycle.
**Assistants resolved by D-57:** list live assistants in every room using
shared session records, without an IRC join or status dot. D-82 supplies
the status meanings and evidence rules. D-63 settles connection lifetime and shared membership;
D-64 settles owner-only retention with no connected IRC clients.
**Switching/join/leave resolved by D-60:** keep joined rooms while switching;
Join/Leave sends the corresponding IRC action.
**Acting accounts resolved by D-61:** phone and desktop use their respective
owner accounts with equal authority. D-62 selects a remembered browser profile
under the owner's delegation. The owner reports the shared-chat authorization
change complete, backed by skills commit `7ce2401`; no rollout action remains
for adding the phone account. D-63 keeps sessions and joined rooms behind
the interface, shared across that account's IRC clients.
**Current-task line deferred by D-30:** optional milestone-2 setting,
excluded from milestone 1; no default selected.
**Freshness already resolved:** prior `plateia_design.md` D-52 (owner
decision 2026-10-08) sets agent presence stale after 2 minutes and unknown
after 10; chat reconnecting appears immediately, with stale after 1 minute
without a bridge heartbeat and unknown after 5. Carry these defaults into
the shared read contract. Stale values show their age; unknown values are
not shown as current. Neither becomes idle or success merely as time passes.
These thresholds describe evidence age, not D-35/D-46's shorter UI wait bounds.

### Q-6. How should images be uploaded, displayed and retained?

D-83 accepts P-72's capped-only image retention and the explicit reselection case
when reload interrupts an image before a recoverable capped copy exists.
This qualifies D-23's draft recovery without retaining HD originals.
D-84 accepts P-73's Pillow 13.0.0 and Python 3.11 minimum for the new web
application, with the existing inbox compatibility preserved.
D-85 accepts P-74's bounded JSON uploads, separate image worker and normalized
output behavior. D-86 accepts P-75's local catalog/pin/export contract and
settles K-3. Image implementation and deployment checks remain required;
K-4 still owns the room barrier and final archive lifecycle.

**Composition resolved by D-17:** paste into the message, visible inline
while typing before Send; no separate preview step. A placeholder link may
ship first, followed by inline rendering within milestone 1.

**Upload failure resolved by D-18:** keep the whole message unsent for retry
or image removal.
**Upload timing resolved by D-19:** upload on paste, post only on Send.
**Retention resolved by D-20:** keep sent images as long as their logs and
archive them alongside the conversation.
**Display and storage policy resolved by D-21:** fit display to the column,
cap stored images independently on upload, and open the capped image at full
size on tap/click. No retained high-resolution originals.
**Stored-image limits resolved by D-22:** longest side at most 1280 pixels
and file at most 1 MiB, shrinking further as needed.
**Unused-upload cleanup resolved by D-49:** delete provably unreferenced
uploads after 24 hours; keep needed images and uncertain cases.
**Image selection resolved by D-67:** image button alongside paste on phone
and desktop, using the same draft and immediate upload, posting only on Send.
**Input formats resolved by D-68:** PNG, JPEG and WebP stills; HEIC only if
real phone testing exposes unconverted input and the owner separately approves
the decoder dependency.
**Source limits resolved by D-69:** 20 MiB encoded and 50 million pixels;
larger inputs follow the failed-upload rule. D-85/D-86 settle processing and
asset access; D-88/D-92 settle archive mechanics. Actual device paste/chooser
and processing qualification remain required. D-94 permits web-served images
to be unavailable with the web stopped; local archives remain readable.
Plateia stores images; shared chat records their links.
**Inline scope resolved by D-73:** managed image references render inline;
external image URLs stay clickable links with no image or website previews.

### Q-7. Which page framework, evaluation method and browser layout?

**Framework and evaluation resolved by D-25:** Svelte with Python, validated
in real chat slices. D-26/D-27 settle the delegated build/editor/toolchain/CI
choices. **Layout resolved by D-28:** narrow rooms left, wider users right,
chat taking most of the desktop screen; room/user lists behind buttons on
phone. **History scrolling resolved by D-39:** load older on scroll-up,
preserve the reader's place on new arrivals, and provide “Jump to latest”.
Carry forward prior `plateia_design.md` D-64's browser trust boundary:
localhost/Tailscale access, allowed Host and same-origin requests, the owner's
configured identity, and protected mutating/streaming endpoints. Render chat
as untrusted content; restrict links to http/https and safe new-tab behavior.
Its JSON/custom-header mutation rule still applies. D-85's bounded JSON
upload encoding stays within that boundary.

### Q-8. How should existing rooms be archived and migrated?

D-12 requires an explicit initial archive/migration step.
**Cutover resolved by D-44:** warn the owner per project; the owner closes
non-exempt issues/PRs; archive legacy request rooms; switch that project
and restart work in issue/PR rooms. Only owner-named parked items keep marked
legacy rooms until they finish. Some historical loss at cutover is accepted;
rollout details belong to the project manager. Other projects remain on
their existing model until their own cutover. Permanent rooms stay.
Discard design rooms without migration or saved logs (D-16); new design
conversations use general. The current reporting channel stays in use now.

**Archived behavior resolved by D-14:** leave active lists and reject new
posts until reopened, retaining the file log.
**Closing transition resolved by D-15:** block new submissions, settle
already-accepted posts and verify the archive; keep the title grey and
problems visible until verified closure.
D-88/D-89/D-92/D-93 settle normal archive verification and interruption
recovery, including the explicit snapshot and IRC crash-persistence limits.
D-65 resolves permanent send uncertainty: after dismissal, archive current
IRC history and record the gap by logical ID; keep the closed-room marker.
D-66 limits late-send checks to 24 hours after closure and again on reopen;
verified results update the archive and images and annotate the original note.
The manager owns D-44's concrete activation procedure and parked-room
mapping. Do not reopen the rejected gradual-migration plan.
Include the conversation's images in the archive and its verification
(D-20).
**Archive format resolved by D-70:** one self-describing structured
conversation log with images and gap notes, without a plaintext transcript
or extra reading tools. D-71 parts every owner on archive and rejoins the
bound account when it opens the legitimately reopened room.
For rooms being archived, preserve conversation history and references.
Discarded design rooms are exempt. Do not assume existing JSONL
files alone prove a complete archive, or that removing a room from a client
is archiving it.

## Verification strategy

Use invented projects, accounts, messages, tracker items and images. Never
publish live configuration, private chat, personal paths or live screenshots.

### Web-off dependency audit (D-93)

Design inspection 2026-10-21, not runtime qualification:

| Function | Accepted execution boundary and required check |
| --- | --- |
| IRC and independent clients | External to plateia; stopping the web process must not stop IRC or disconnect these clients. |
| Covered sender and owner sessions | D-77 puts the durable queue, sender and connections in the bridge. No browser request or web lifetime owns their continued operation. |
| Agent delivery, acceptance and manager work | Shared bridge and agent sessions continue independently; verify delivery and acceptance with the web stopped in CHAT-23. |
| Tracker polling, room freeze/archive/reopen and reconnect repair | Shared lifecycle owns these under D-87–D-93. Timers and recovery cannot be started only by a web request or hosted in the web process. |
| Image pinning, verification and archive export | D-86's installed local asset command opens the catalog/files on demand. It requires neither the web app nor a resident asset process, and must not fetch its own HTTP URLs. |
| Reading archived images | D-92's immutable bundle includes verified local images and relative references; neither a web URL nor a running web process is needed. |
| Opening a managed image URL from another IRC client | D-94 accepts that the HTTP route is unavailable with the web process stopped. Verify that restarting it serves the same retained image URLs. |

The first six rows expose no required running-web dependency in the accepted
design. They are implementation acceptance obligations, not evidence of
implemented behavior. A worker finding such a dependency must report it as
a D-93 violation and fix or escalate it before claiming the slice complete.
D-94 resolves the final row, closing this design dependency audit. Runtime
qualification remains required. No live process was stopped for this inspection.

The eventual design must make these observable: phone/desktop parity;
history/live continuity; correct project and general-room routing; no
duplicate work after retry; acknowledgement durability; automatic recovery;
honest presence freshness; durable unresolved errors; preserved room logs;
and image failures that cannot masquerade as a sent message.

Check D-28 with realistic synthetic room/user names and conversation content
at desktop and phone sizes and while resizing: rooms narrower than users,
chat taking most of the desktop screen, and both lists reachable through
buttons on phone without losing the current conversation or its draft.

Check D-29 with each role and status, including an amber waiting dot beside
a yellow designer name. Status must also be readable without relying on
color; humans and assistants have no dot, and unknown evidence never
appears as idle.
Check D-53 with synthetic active sessions from several projects: general and
`#chat-test` expose the same active-agent population, while project rooms keep
their project population. End a session and age its evidence to check both
removal and stale/unknown handling. List membership must not create IRC
connections, route messages or grant authority.
Check D-54 with invented desktop and phone owner accounts joined to different
rooms, an authenticated account not joined to the viewed room, and a registered
but disconnected account. The owner must not appear from viewing a room
without joining it or from another account's unrelated membership. Include PART/QUIT,
reconnect and stale membership evidence; unknown must not look like a current
join. Check D-55 with both owner accounts present and with either leaving:
two independent entries, with no aggregate owner entry.
Check D-56 by opening an existing eligible room and a never-created eligible
room: join as the configured owner account, create the latter on first use,
and show presence only after membership is established. Concurrent opens
must converge on one shared room identity. Include failed joins and archived
room records with no live channel; do not silently create a replacement
conversation or enable posting to an archived room.
Check D-58 with an old link to a verified archived room, both with and without
a remaining IRC channel: display the exact archive notice, issue no join or
creation request, and expose no Send action. Preserve the log and images.
After tracker work legitimately reactivates the same room under D-51, opening
it must follow D-56 normally. Do not treat a still-closing room as verified
archived or add an archive browser to milestone 1.
Check D-59 with configured permanent rooms, eligible issue/PR rooms, an
arbitrary name, an unknown tracker result and an archived identity whose
channel is absent. Only eligible milestone-1 rooms can be created; linked
PRs resolve to the issue room and concurrent first opens cannot duplicate it.
Retain the named parked-room exceptions. Do not add or test custom-room rules
as a milestone-1 feature.
Check D-60 on phone and desktop: room/project switches issue no PART;
Leave parts the current owner account and changes its list entry only on
confirmation; Join restores confirmed membership. Keep the other owner
account's entry independent. A rerender after Leave must not undo the action.
Preserve the saved draft, conversation and images. Exercise delayed/failed
membership responses and verify the archive-page control cannot bypass D-58.
Check D-57 with a live assistant that has no IRC connection: it appears in
every room, including project rooms, general and `#chat-test`, with the
assistant role color and no dot. End its session and verify removal; a
registered account without live-session evidence must not appear as current.
Source outages use stale/unknown handling without turning it into a live
assistant. Listing it must not join IRC or alter message routing.
Check D-61/D-62 with synthetic Phone and Desktop browser profiles: their
joins, parts and posts use distinct configured accounts with equivalent owner
authority and unchanged attribution. Multiple browsers on one profile share
that account's membership; a leave must not part the other account.
Resize the page and change the user-agent without changing the binding.
Reload retains setup; a missing/invalid binding requires setup before an
account action, with no fallback identity. Verify protected setup, rejection
of arbitrary request-supplied accounts and no browser IRC credentials.
Reject a profile whose mapped account is absent from the shared owner set.
Delayed sends, check/cancel and same-ID retries must retain their original
account across reconnects and browser-binding changes. Include parity checks
for shared owner/delegation permissions, including an additional synthetic
owner to catch a hard-coded two-account permission list, and reject
unconfigured identities.

Check D-63 with two IRC clients on one synthetic owner account: join and
part from either interface, observe the same membership in both, and verify
that a part received from elsewhere is not undone by a browser rerender.
Keep the other owner account's membership independent. Close every browser
and stop the web application while the shared service continues: neither
action should PART or log out the account. Reopening must reconcile with
current IRC membership. Verify attachment during authenticated registration,
including existing sessions; do not silently connect under a fallback nick.
For D-64, also verify zero-client retention, server-restart restoration and
that a completed PART is not restored as a join. Cover every configured owner,
including an additional synthetic owner beyond the two browser profiles;
setup must not change agent or assistant account settings or the server-wide
default. Retention must not join rooms the account never joined. These are
future isolated integration checks, not tests performed on the owner's rooms.

Verify shared delivery and room lifecycle with the plateia process stopped.
Verify localhost binding and the browser boundary with synthetic requests.
Exact tests and fixtures follow the approved contracts rather than inventing
behavior here.

Check D-65 with an unconfirmed send in a closing room. Before dismissal it
keeps the room grey; after dismissal the shared lifecycle can finish from
available, fully read IRC history and verified images. The archive includes
an explicit note with the unresolved logical ID, not a fabricated posted
message, and the milestone-1 closed-room notice displays the gap marker.
Multiple gaps require independent dismissal; one dismissal must not hide or
waive another. An unavailable history read, failed archive write or missing
referenced image still prevents successful verification. Confirm that no
resend, same-ID retry, cancellation claim or deletion of uncertain images
occurs, and that the note and marker survive a service restart.
Check D-66 with a send found within the 24-hour window: include it once with
its images, annotate the gap note and clear only its resolved marker after
verification. Repeated results and interrupted archive writes must not
duplicate messages or lose the existing archive. Verify that checks stop
at the persisted closure deadline, including across restarts and outages;
afterward the unresolved note stands without a watcher. A genuine reopen
checks again, while an archived-link visit does not. Expiry must neither
enable retry nor release uncertain image references for cleanup.

The initial cleanup needs synthetic examples of closed-out PR work,
owner-named parked work, non-exempt open work, existing design rooms and
permanent rooms. Verify D-44's warning/owner-closure/archive/switch order:
non-exempt open work prevents activation; named parked work remains reachable
in marked legacy rooms without duplicates; permanent rooms remain; and
design rooms are discarded without migration or saved logs. Verify that
switching one project does not change another project's room behavior.
Historical gaps accepted at cutover must not be represented as complete
records or weaken preservation for new work.
Verify that new design conversations use general, that completing a design
never closes general, and that discarding a design room does not mark
unfinished tracker work complete.

Closing-state scenarios must show the grey room title until verification
succeeds, preserve already-accepted posts, and leave failures visible rather
than prematurely removing the room.
Check D-51 for reopening an associated PR, reopening a standalone PR, and
starting a replacement PR for an issue whose room was archived. Preserve room
identity, messages and image references, including through another close.
Race reopening with an in-progress archive: an old closure result must not
hide the active room or discard its continued conversation. A tracker item
that never had a room still creates one only on first use under D-9.
Check D-52 with project work that has no tracker item: conversation stays in
the main room without creating a temporary work room. If an issue/PR follows,
its room links to the earlier discussion without duplicating messages.
Design conversations still use general and do not create design rooms.

Image composition checks must cover PNG paste while writing a message,
continuing to type with the image visible, and posting only on Send, on
phone and desktop. Verify the early placeholder-link stage and the final
inline-image stage separately; milestone 1 is not complete at the placeholder.
In both stages, an upload failure must retain the whole unsent message and
offer retry or image removal, without posting text alone or a broken link.
Verify that pasting begins the upload while typing can continue, and that a
completed upload alone never posts a message.
Check D-67's image button on phone and desktop with an existing text/image
draft: choosing an image inserts into that draft and begins upload without
sending; cancelling the chooser leaves the draft unchanged. Exercise the
same upload failure, retry/removal, caps and draft persistence through paste
and selection. Both entry methods must work in the placeholder-link slice
and the completed inline-image experience.
For D-68, cover valid PNG, JPEG and WebP stills plus animated, video,
unsupported and malformed inputs. Verify the stored caps for each accepted
format and retain the draft when an image fails. On the owner's phone,
exercise photo-library selection, file selection and image paste, including
a photo whose source format is HEIC; inspect the received content's actual
format rather than relying only on its extension or declared MIME type.
Keep private images and metadata out of public evidence. If HEIC reaches the
page unconverted, record that result and bring the decoder dependency to the
owner before adding support. If all tested paths yield supported formats,
record the tested device/browser and omit the extra decoder. This test is
still required; it has not been performed during design.
For D-69, test source byte size and pixel count independently, at each limit
and just above it. An otherwise valid image at both limits may proceed to
shrinking; an over-limit source fails before full decoding and leaves the
draft unsent. Exercise paste and chooser paths, inaccurate declared sizes,
interrupted uploads and concurrent processing. For each accepted format,
verify that saved and archived files satisfy both output caps and that no
high-resolution source becomes a retained attachment.
For D-72, round-trip text with multiple spaces, indentation, tabs, blank
lines, Markdown punctuation and literal HTML through compose, IRC, live
display, history and structured archive. Preserve the underlying text and
line breaks; responsive wrapping must not alter them. Links stay clickable
only for allowed destinations, and inline images retain their composition
position. Do not add Markdown parsing or a rich-formatting toolbar.
For D-73, display managed image references, outside image URLs and ordinary
website links in both live messages and loaded history. Only verified
managed objects may render inline. Observe network requests to confirm that
outside links are not fetched merely to display messages, including links
whose path looks like an image or whose hostname resembles the managed
origin. Explicitly opening a link remains a normal owner action.
Check D-31's desktop Enter/Shift+Enter and phone Return/Send behavior,
including input-method composition and attempts to submit while an image
upload is unfinished or the connection is unavailable.
Check D-32's small grey sending text below the message, red failure icon,
and retry through that icon on both phone and desktop. Failed entries must
not be persisted or restored on reload. Check this separately from saved
unfinished drafts and acknowledged requests; their different lifecycles
must not silently erase either durability guarantee.
Check D-33: Send clears the saved draft immediately, a later message removes
the previous failed entry, and a newer failure replaces it rather than
creating a second retry entry. A removed failure must not reappear as a
saved draft or a persisted failure after reload.

Deadline tests must use delayed, lost, duplicated and reordered requests
and responses, not just localhost success. Cover cancellation reaching the
server before the original send; publication racing cancellation; a posted
message whose reply is lost; repeated cancellation calls; a retry arriving
before a delayed original attempt; duplicate retry calls; and service
restart after posting or cancellation. Assert at most one logical IRC post and
at most one resulting agent action, with no publication by cancelled old
attempts. Test countdown expiry during tab suspension and reconnect without
resetting its deadline. No red failure icon may falsely describe a posted
or unresolved message. Include delayed IRC confirmation and bridge-log lag:
absence from a local log is not proof of cancellation.

Check D-35's transition at 15 seconds after dispatch, including a suspended
tab and repeated unsuccessful reconnects. The zero countdown must become
the unconfirmed text without enabling retry. A late posted result removes
the text; a confirmed cancellation permits the red retry icon, subject to
the single-failure rule. Waiting longer alone must never manufacture either
outcome.

D-36/D-45 checks must reload or close the browser before confirmation and prove
there is no persisted pending content, ID, deadline or restored entry.
Unsubmitted drafts must still survive. Delay the original request past
expiry with no browser left to cancel it, and verify no IRC write starts.
Also cover requests received before expiry but queued when it passes. A
write already in flight may post later and must remain unconfirmed until
settled. Cover clock skew, competing writers during restart, lost old
connections and delayed history ingestion. Prove the previous write has
settled, then page the complete authoritative history range before treating
absence as cancellation. Missing capabilities, pruned history or incomplete
coverage must remain unknown. A same-ID retry with a fresh attempt deadline
must recheck history and must not reactivate an expired original attempt.
These fault and restart checks remain required future validation; the
read-only D-45 probes do not establish their results.

Check D-46 with a prior unsettled write after reload: the room notice changes
from syncing to unconfirmed at 15 seconds even if requests never return.
Repeated requests, reconnects, room switches and page suspension must not
extend that bound within the loaded page. Verify settlement both before and
after the bound, with history lagging separately: clear the notice only when
both are resolved. A fresh reload gets a bounded request without recovering
any pending send from browser storage. Neither timeout nor notice removal
may manufacture a cancellation result or a retry entry.

Check D-47 using synthetic lost-confirmation and history-write-failure cases:
the sender must retain unknown rather than infer cancellation from absence.
Dismiss both message-level and room-level unconfirmed notices and assert
that no send, retry or outcome change occurs. Delayed original requests must
remain fenced after dismissal. Retyping and Send must allocate a new ID;
late confirmation of the original must remain independently visible in
history. Dismissing this notice must not clear an agent-delivery error.
Check D-48 with two browser sessions and a sender restart: dismissal remains
remembered after reload and across devices without browser persistence.
Create a new unresolved send in the same room and verify that its notice
still appears. A stale dismissal request for the original must not hide the
new notice or erase either send's recovery record.

Draft recovery checks must retain text and images across room switches and
reloads on the same device, including drafts whose uploads are interrupted
or have failed. Cleanup must not remove saved-draft attachments.
Check D-49 on both sides of the 24-hour grace boundary, with reference reuse
during cleanup, a draft left offline longer than a day, an interrupted
upload, a retry entry and an unresolved send. Delete only proven unused
objects; uncertainty must retain them. Posted and archived image references
remain protected independently of draft removal.
Archive checks must show that the conversation and its image files are
preserved together, that references still resolve, and that a missing image
cannot be silently treated as a fully verified archive.
For D-70, inspect a synthetic archive without the running service or a
project-specific reader: its schema/context, accounts, times, message IDs,
text and image references must be understandable from the structured files.
Verify that gap notes and later-confirmation annotations identify their sends
unambiguously, and that no separate plaintext transcript is produced. Test
reading after a late correction and a close/reopen/close cycle.
For D-71, archive a synthetic work room joined by multiple owner accounts
and multiple clients of one account: each owner's actual membership must
be removed across its clients while its login, other rooms, log and images
remain intact. Include an additional owner from the shared owner set and
an owner already absent from the room. Verify failed/unknown PART handling
and restart recovery without claiming success. Reopen the same work room,
open it as one owner and verify that account rejoins with the preserved
conversation. Race an older closure against that join; it must not remove
the new membership. Agent/assistant session records remain unchanged.
Check stored dimensions and file size against the approved caps independently
of viewport width. Resizing the window changes only display; opening and
archiving use the capped version, with no retained high-resolution original.

Long-message verification must cover exact leading, trailing and internal
line breaks, Unicode split boundaries, negotiated byte/line limits, multiple
independent messages in separate batches, interleaved same-sender posts,
repeated identical posts, truncated
batches, reconnect and history replay. Assert one web message per logical
post and one instruction dispatch, with no merging of separate posts or
partial dispatch when a fragment is absent. Under D-78, one covered message
must fit one batch; missing capability or insufficient limits cause an
explicit refusal before writing, never a multi-batch or plain-line fallback.
Verify output buffers and fault behavior before the separately confirmed
live setup change. Existing 10-test evidence is a baseline only.

Verify D-38 at 65,535, 65,536 and 65,537 serialized UTF-8 bytes, including
multibyte characters and attachment links. Over-limit content must remain
in the draft with a warning and no send attempt; client and server must
agree on the byte count. Supported-size messages still need complete,
at-most-once publication under the send-deadline contract.

Check D-39 on phone and desktop: loading older pages and receiving new
messages must preserve the visible message and reading position. Include
images finishing their load above the viewport. “Jump to latest” returns
to the bottom, and automatic following resumes there without adding unread
counts or mention badges.

Check D-40 with empty and nonempty drafts, including images: selecting a
user inserts the correct stable handle, preserves existing content and
focuses the composer without sending or switching rooms. Cover both the
desktop list and phone panel. This is milestone-1 behavior only.

Verify D-41 from synthetic bridge records: failed transport, delivery
without acceptance, terminal dead letter, late authenticated acceptance,
wrong-recipient reply and several intended recipients. Keep unresolved
errors across room switches, reloads and later messages. Missing, stale or
unreadable records cannot clear an error or manufacture a failure.
Verify D-42 with late acceptance by the correct agent and replies from the
wrong agent. A resend or redirect must have a new message ID and independent
tracking; acceptance of that copy must leave the original error intact.
No dismissal action clears the error. Several recipient failures resolve
independently, and a permanently gone agent leaves the original unresolved.

Verify D-43 with synthetic bridge records and notification fakes: viewing
or refreshing the chat error must not send another phone alert, change the
bridge's existing alert behavior or clear the error when a phone notice is
dismissed. No new browser-push path belongs in milestone 1.

## Delivery plan

D-74 approves three stages: text chat, images, then the new room lifecycle,
archives and per-project cutovers. D-76 approves the following **23 slice
boundaries** at `e6107c0663`. The contract gates are resolved and D-95 records
readiness approval at `872b7eb262`. Each epic/child draft still needs its
own signoff before filing; no pull request is created by design processing.

All issues use `coghex/plateia`. “Shared skills” means implementation in the
canonical local skills repository through D-75: separate checkout/branch,
exact-commit opposite-brand review, recorded local tests and an owner/level-2
authorized merge. “Plateia” means an isolated worktree and one focused PR
with the normal opposite-brand review and CI. No slice combines code from
both repositories in one change; shared completion evidence gates consumers.

Dependencies are required completion evidence, not just an issue being closed
or a branch existing. Documentation lands through `docs-push` as it becomes
ready; executable skills and test-consumed contracts travel with their code.
Each slice owns meaningful local verification; CHAT-23 covers remaining
cross-component fault cases rather than postponing testing until the end.

Public plateia CI uses invented contract fixtures; it must not vendor the
private shared source or depend on a remote that does not exist. Real shared
integration runs in isolated local checkouts against the reviewed skills
commit and records sanitized completion evidence under D-75. Record which
checks actually ran in CI versus locally; neither substitutes for the other.

### Contract gates before affected issue drafting

These gates separate settled contracts from unfinished design work; they
are not permission to let implementers invent behavior. D-76 approves the
dependency decomposition, while D-81/D-82/D-86 settle K-1/K-2/K-3's design contracts.
The K-4 coverage review below maps its later approvals. Approved boundaries
alone never settle a contract. Carry each accepted contract into its affected
issue. Bring any new user-visible behavior or serious ownership choice to the
owner; exact field spelling and test mechanics are implementation details.

| Gate | Required resolution | Affected slices |
| --- | --- | --- |
| K-1 / Q-3 | Versioned Python API execution in existing shared processes; single-writer ownership and restart fencing; durable journal states; browser/server deadline clock basis; logical fragment identity/completeness; authoritative history/live cursor and retry evidence. Retain D-47's unknown outcome whenever proof is absent. | CHAT-1–CHAT-6, CHAT-8–CHAT-12 |
| K-2 / Q-5 | Verify sources that distinguish idle, working and waiting on the owner from mere live-session registration; specify freshness and unknown results without guessing. | CHAT-7, CHAT-13 |
| K-3 / Q-6 | Upload encoding within the approved browser boundary, processor/pins and resource bounds; persisted image drafts before/after failed upload without retained HD attachments; one asset-reference/export contract usable by shared archiving while the web process is down. | CHAT-15–CHAT-17, CHAT-19 |
| K-4 / Q-2, Q-8 | Tracker-to-room lifecycle evidence/generations; posting barrier covering other IRC clients; authoritative archive coverage, image verification and atomic completion/correction; membership cleanup and interruption/reopen recovery. | CHAT-18–CHAT-22 |

D-75 resolves tracking and local skills delivery. D-68's HEIC question is a
deliberately conditional verification gate, not a mandatory decoder dependency:
stop and return evidence to the owner only if actual phone input remains HEIC.
K-1's execution ownership is resolved by D-77, its native single-batch
transport plan by D-78, its clock protocol by D-79/P-68 and its IRC-only
history read boundary by D-80. The overlap contract below makes D-80
concrete; D-81 accepts P-70's detailed API records and history/live handoff
and settles K-1's design contract.
Transport activation still needs D-78's verification and
separately confirmed setup change; clock activation still needs actual-browser
verification under P-68.
D-82 settles K-2's design mapping, including automatic owner waits; its
runtime-source checks remain. D-86 settles K-3 through P-75 and D-83–D-85;
its implementation and deployment checks remain. D-87–D-94 settle K-4 as
mapped below. All four gates are resolved by explicit contract approvals,
not boundary approval or source inspection alone. This does not approve
design readiness, issue creation, implementation acceptance or live setup.

### K-4 coverage and readiness review, 2026-10-21

This is a consolidation of accepted decisions, not a new behavior proposal.

| Required contract | Accepted resolution | Implementation slices |
| --- | --- | --- |
| Room identity, links and tracker evidence | D-90/P-79: durable bindings, manager-first ambiguity and owner approval for moving used bindings. D-91/P-80: complete fresh observations, work rounds, close/reopen events and final recheck. | CHAT-18 |
| Admission, drain and freeze | D-87/P-76: dedicated moderator and participant permissions. D-89/P-78: stop new admission, drain existing attempts, freeze before snapshot. D-92/P-81: gate and admission in the same control store. | CHAT-18, CHAT-19 |
| History coverage and image preservation | D-88/P-77: available-history snapshot, explicit limits and no silent known omissions. D-80: overlapping IRC pages. D-86/P-75: local pins and verified capped-image export. | CHAT-19 |
| Publication and correction | D-92/P-81: durable SQLite control records, sealed immutable bundles, verified publication and retained revisions; correction preserves the original closure deadline. | CHAT-18–CHAT-20 |
| Owner cleanup and reopen fencing | D-71: part all owners at archive, join the bound owner on legitimate opening. D-89/P-78 and D-93/P-82: generations, operation receipts, blocked unknown old commands, current-state verification and reconnect repair. | CHAT-18–CHAT-21 |
| Unconfirmed sends and all-client late posts | D-65/D-66 and D-88/P-77: named dismissed gaps, correction across all clients for 24 hours and on legitimate reopen only; no indefinite history watcher. D-93 accepts the IRC restart gap. | CHAT-19, CHAT-20 |
| Legacy rollout | D-44: per-project clean cutover, warning and owner closure prerequisites, explicit parked exceptions. D-16: discard old design rooms without logs. The manager owns concrete rollout mapping; live actions retain their authorization gates. | CHAT-22 |
| Web independence | D-93/D-94: all shared operations and local archives work with the web stopped; web-served content is unavailable. The dependency audit is closed, with explicit runtime qualification still required. | CHAT-15, CHAT-18–CHAT-23 |

K-4's design contract is resolved. Preserve its limits when drafting: a
snapshot is not proof that all server handlers finished, confirmed IRC state
is not crash-proof persistence, and an unconfirmed old mutation can block
reopening until manager-first recovery obtains evidence. Never convert those
limits into a success inferred from elapsed time.

Readiness checks: the epic goal/done condition and non-goals are explicit;
the 23 approved boundaries remain in dependency order with matching ledger
entries; K-1–K-4 have accepted contracts; compatibility, retention, recovery,
privacy, local shared-skills delivery and live setup gates are recorded.
HEIC remains deliberately conditional under D-68, and concrete per-project
activation belongs to the manager under D-44. These are named stop conditions,
not undecided milestone-1 behavior delegated to implementers.

Tracker recheck on 2026-10-21 found no open issues or pull requests in
`coghex/plateia`. The closed [previous epic #10](https://github.com/coghex/plateia/issues/10)
covered the earlier captured-chat/board arc, not this milestone's working-chat
scope and current canonical-skills ownership. No existing open umbrella
duplicates this design. Repeat per-artifact deduplication during processing.

D-95 records the owner's readiness approval at `872b7eb262`. Process the epic
first, then one child at a time with separate owner or delegated project-manager
signoff under D-96; the manager runs each filed issue's review. No implementation or activation is approved by
this readiness review.

### K-1 elaboration: identity and cancellation invariants

The following makes D-34/D-45/D-47/D-48/D-77 concrete within the design
contract accepted by D-81. It is a contract to implement and verify, not a claim
about the current `chatlib` or bridge. D-78 selects the native single-batch
wire representation and D-79/P-68 fixes the clock mapping. D-81 accepts
P-70's remaining API records and history/live cursor.

Keep four identities distinct: the browser's logical message ID; the
attempt generation for each explicit retry; a command ID for idempotent
API requests; and native IRC msgids identifying history evidence. A retry
keeps the logical ID and immutable account, room and content, but gets a new
attempt generation and deadline. A repeated HTTP request keeps its command
ID and cannot create another generation. Batch references and response
labels are transient framing/correlation values, never durable message IDs.
The browser keeps submitted content and its retry information only in memory.

| Journal fact | Permitted transition/result | Insufficient evidence |
| --- | --- | --- |
| No attempt has been registered | A check/cancel records an account/room-bound cancellation tombstone for that ID and generation before confirming cancellation. A later original request cannot bypass it. | A missing row alone cannot return a retryable cancellation. |
| Registered, no write started | Under writer serialization, either cancel/expire durably or record write-started durably before socket access. The competing operation observes the committed result. | An HTTP timeout or an uncommitted cancellation request. |
| Write-started | Match the complete message in authoritative IRC history, or retain unconfirmed. Restart does not resume the remaining bytes or retry the attempt. | Missing history, a PONG, disconnected socket, new writer lock or failed local log append. |
| Posted with matching history evidence | Return the same posted message and native aliases for repeated submit/check/retry requests; do not write again. | Matching text without matching logical ID, account, room and exact content. |
| Confirmed cancelled before any write | An explicit retry checks history and atomically creates at most one new attempt generation. If required evidence is unavailable or contradictory, do not publish. | A request to retry an unknown, expired-old-generation or write-started attempt. |
| Unconfirmed notice dismissed | Persist dismissal independently of the send outcome; suppress that notice across devices. Later history evidence can still settle the underlying send. | Dismissal never authorizes retry, erases a delivery error or cancels a possible IRC publication. |

Cancellation of an older generation cannot cancel a newer retry, and a
delayed submission cannot replace immutable content or refresh its deadline.
Reject identity/content conflicts explicitly. A cancellation tombstone need
not hold message text, but must identify the account, room, logical ID and
attempt it fences. Persist commands and state changes transactionally;
enqueue acknowledgement means durable registration, not IRC posting.

The single writer validates account authority, room eligibility, capability,
size and deadline before marking write-started. That mark is deliberately
conservative: a crash immediately afterward may leave an unconfirmed send
even if no byte was actually written. It buys the accepted at-most-once
behavior by refusing to guess. Journal corruption, unsupported state/API
versions and failure to acquire the writer lock cannot fall back to direct
posting. Reconciliation can read history without becoming a second writer.

Required fault cases include cancel arriving before submit, two identical
retry requests, old submit/cancel arriving after a new generation, a crash
on either side of every durable transition, and a web restart while the
bridge continues. Preserve the accepted ten-second cutoff and five-second
unknown-notice delay; tests must use D-79/P-68's selected heartbeat clock
contract, not reset the timeout at HTTP receipt. No issue is ready from this
table alone.

### K-1 evidence: strict cursors skip ties; overlapping milliseconds retain them

Source inspection on 2026-10-21 found that Ergo 2.19.1's SQLite-backed
`Between` resolves a native msgid to its timestamp, then queries with strict
`nanotime > after` / `nanotime < before` bounds and orders only by nanotime.
Native IDs are therefore not a second ordering key in this implementation.
If a page ends partway through messages with an equal timestamp, continuing
from its last msgid can skip the remainder of that timestamp.
[SQLite history queries](https://github.com/ergochat/ergo/blob/v2.19.1/irc/sqlite/history.go).

A read-only design experiment used an in-memory SQLite table with invented
IDs 1–4, timestamps 100, 100, 100, 101 and page size two. The inspected
predicate returned `[1, 2]`, then `[4]`, omitting ID 3. This reproduces the
query boundary, not an observed loss in the live IRC service. It prevents
claiming gap-free coverage solely from a completed page and its last msgid.
The previously observed first-item AROUND gap also has a source explanation:
`GenericAround` returns when there is no earlier half-page, before retrieving
the requested first item.
[AROUND implementation](https://github.com/ergochat/ergo/blob/v2.19.1/irc/history/queries.go).

The shared bridge currently advances through its `HistoryPager` using the
last native msgid (or timestamp) of a full page. A boundary/count heuristic
is insufficient for the new coverage claim. Missing reference IDs, retention,
query errors and reconnects must likewise remain distinguishable from an
authoritatively completed available range.

D-80 resolves the read boundary: use overlapping IRC queries and report an
unexhausted boundary as incomplete. The rejected database reader in P-69 is
not an alternative available to an implementer. Source inspection of server
code does not authorize reading the live database.

Further source inspection on 2026-10-21 found that the CHATHISTORY handler
rounds an AFTER timestamp up to the next millisecond, while leaving a BEFORE
timestamp unchanged. The backend then applies its strict comparison. Thus
AFTER at the preceding millisecond still excludes a message exactly at the
desired millisecond. BEFORE at the following millisecond includes the entire
desired boundary, including its submillisecond records. LATEST returns the
newest limited set, presented chronologically; the handler clamps requests
to the configured maximum and supplies an end-of-pagination indication when
a successful query returns fewer items than its effective limit.
[CHATHISTORY handler](https://github.com/ergochat/ergo/blob/v2.19.1/irc/handlers.go).

### K-1 overlap contract under D-80

Use backward traversal for both older-history reads and reconnect catch-up:

1. Start with `CHATHISTORY LATEST <room> * <limit>`, or resume the stored
   opaque boundary for an older-page request. Establish the effective limit
   from the server's advertised/configured maximum; a silently clamped limit
   cannot be mistaken for exhaustion.
2. Let `m` be the millisecond containing the oldest returned timestamp. The
   next request is `CHATHISTORY BEFORE <room> timestamp=<m + 1 ms> <limit>`.
   This deliberately repeats the whole boundary millisecond. Do not use
   AFTER, the last native msgid, or AROUND to skip that overlap.
3. Deduplicate native msgids and verified logical-ID aliases. Equal text,
   equal timestamps and batch references never identify duplicate messages.
   Count raw top-level history items for paging, including events and each
   reconstructed multiline message once; count before deduplication.
4. A full page that cannot move the oldest boundary strictly earlier may
   be retried with a larger limit, up to the server maximum. If that maximum
   still gives no progress or positive exhaustion evidence, return incomplete
   history with the blocked boundary. Do not loop forever or advance past it.
   This also covers distinct submillisecond timestamps within one wire
   millisecond, and the ambiguous case of exactly one full page of ties.
5. Only a successfully completed response batch with the server's positive
   end indication establishes exhaustion. Crossing into an older millisecond
   establishes that the preceding boundary was traversed for that query.
   An empty set of *new* IDs after deduplication establishes neither fact.
   Query errors, lost batches, changed capabilities or missing access leave
   coverage incomplete. Never convert them into an empty successful history.

Keep coverage separate from message evidence. A complete matching history
record can establish that a send posted even if a different boundary is
incomplete. A page cursor describes a room, query direction, boundary and
coverage generation; it is not a promise of an immutable server snapshot or
a globally unique timestamp order. Preserve returned order among ties without
inventing an ordering authority from the lexical value of IDs.

The end indication describes the history available to that IRC query, not
proof that all previously delivered messages were durably recorded. Retention
and the known delivery-before-history gap remain relevant. No negative
history result after write-started authorizes retry under D-47. A history/live
handoff must additionally cover live arrivals during the scan; merely reaching
the end of an overlapping traversal does not establish that handoff.

Design experiment, 2026-10-21: an in-memory SQLite model of the inspected
strict BEFORE query used invented IDs, base limit three and maximum eight.
Exact/submillisecond boundaries and repeated overlap returned every ID once;
seven tied records completed, while eight or nine tied records stopped with
explicit incompleteness at the full maximum rather than guessing. Five
hundred generated histories with bounded timestamp density all completed
with every ID exactly once. A separate predicate check reproduced AFTER's
rounding exclusion and BEFORE's inclusion of the boundary. These are query
model checks, not live Ergo, retention, concurrent-write or reconnect tests.
They support selecting the overlap traversal within D-80's accepted limit;
CHAT-3 still requires isolated-server integration evidence before activation.

### K-2 evidence: live identity is not current activity or an owner wait

Read-only source checks, 2026-10-21:

- Shared `chat/scripts/identities.py` derives live sessions from the process
  inventory and cmux hook records. `hook_record` checks PID/start-time
  consistency; `inventory` excludes known nonterminal processes and avoids
  ambiguous placements. These are useful presence boundaries, not proof of
  working or waiting. Its `agent_lifecycle` fallback is `running`, so a
  missing hook must not become a green dot in the new projection.
- The installed Codex wrapper injects session, prompt, stop and tool/permission
  hooks, and explicitly allows launches to proceed when hooks are disabled
  or injection fails. The installed Claude wrapper also injects hooks.
  Presence therefore cannot imply that lifecycle instrumentation is working.
- Shared `chat/scripts/chat-bridge` reads `surface.input_state`, including
  `lifecycle` and `waiting_on_human`, to decide how to deliver messages.
  Its interrupt path documents that lifecycle can remain running after Esc,
  and uses terminal-specific evidence for that action. Existing bridge tests
  model waiting and interruption for delivery; they do not establish the
  accuracy of a user-list status projection. Those tests were inspected,
  not rerun for this design step.
- Upstream cmux documents stored session identity and the four lifecycle
  values, but that does not establish that an arbitrary `needsInput` value
  means an outstanding owner chat question. This is supporting documentation,
  not a verification of every installed runtime transition.
  [cmux agent hooks](https://github.com/manaflow-ai/cmux/blob/main/docs/agent-hooks.md).

No existing explicit, question-linked owner-wait field was found in shared
identity discovery. D-82/P-71 specify that missing shared contract. Preserve
source, session/process generation, reason, event time and observation time
in the projection; preserve unknown instead of normalizing missing data to
one of the three reassuring colors. Actual lifecycle integration remains a
CHAT-7 verification obligation. No session was interrupted or reconfigured,
and no hook, live config or service was changed during these checks.

### Stage checkpoints

- **Stage 1, CHAT-1–CHAT-14:** usable text chat in existing rooms on phone
  and desktop, including history, drafts, correct identity/membership, user
  lists, send recovery and bridge-backed delivery errors. Intermediate PRs
  are not represented as a finished reliable chat experience.
- **Stage 2, CHAT-15–CHAT-17:** managed image links, then inline images,
  with reference retention/cleanup and both input methods from the first
  image feature. Stored caps apply from the start.
- **Stage 3, CHAT-18–CHAT-23:** new room types and issue/PR conventions,
  verified archives, bounded gap correction, per-project cutover readiness
  and full integration evidence. No live cutover is bundled into a code
  merge; the manager supplies the D-44 warning and concrete rollout plan.

### CHAT-1. Define the shared chat API records and compatibility contract

- **Outcome:** Consumers exchange versioned chat facts without reinterpreting unknown data.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Add versioned Python records/codecs for rooms, complete logical messages, history cursors, freshness, membership, sender attempts and recipient delivery facts. Define stable IDs and explicit unknown/error results; anticipate image references and lifecycle generations without implementing those features.
- **Phase:** 1. **Depends on:** none. **Ordering:** critical path.
- **Relevant decisions:** D-2–D-6, D-37, D-41, D-61, D-75.
- **Acceptance signals:** Synthetic producer/consumer fixtures round-trip exact text and identities; unsupported versions fail explicitly. The codecs match the reviewed K-1 operation/state contract; unknown versions cannot silently reinterpret data.
- **Out of scope:** IRC connections, UI, room activation and a new IPC daemon.
- **Open questions / stop conditions:** K-1–K-4 are settled by the mapped approvals; implement their accepted records and semantics without inventing behavior in the codecs. Exact field spelling remains an implementation detail.

### CHAT-2. Preserve one logical message across IRC multiline fragments

- **Outcome:** A supported long post is one exact logical message in live delivery and history.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Extend the shared encoder and bridge reassembly with an explicit logical ID and complete native multiline batch up to 65,536 UTF-8 bytes under D-78. Preserve leading/trailing blank lines, spacing and attachment-link text across the ordered wire lines. Retain native message-ID aliases needed for history and acceptance. Refuse before writing if negotiated capacity is insufficient; no cross-batch fallback.
- **Phase:** 1. **Depends on:** CHAT-1. **Ordering:** critical path; may run in parallel once its listed dependencies and contract gates are satisfied.
- **Relevant decisions:** D-37, D-38, D-45, D-72, D-78.
- **Acceptance signals:** Interleaved posts never merge; duplicate, missing and reordered fragments do not dispatch partial instructions. Complete live and replayed records match byte-for-byte, including boundary-size Unicode input.
- **Out of scope:** Browser rendering, retry policy and heuristic merging of legacy posts.
- **Open questions / stop conditions:** K-1 / Q-3; prove the chosen tag/grouping round-trip, output-buffer sizing and interrupted-batch behavior against isolated Ergo before activation. The manager separately confirms the live setup change with the owner under D-78.

### CHAT-3. Expose authoritative history with a gap-free live handoff

- **Outcome:** Readers move between older history and live arrivals without gaps or duplicate messages.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Add bounded page reads, logical-ID lookup and a cursor-based history/live handoff behind the Python API. Return coverage and freshness explicitly. Deduplicate native and logical aliases; expose incomplete history without claiming a missing send was cancelled.
- **Phase:** 1. **Depends on:** CHAT-1, CHAT-2. **Ordering:** critical path; may run in parallel once its listed dependencies and contract gates are satisfied.
- **Relevant decisions:** D-37–D-39, D-45–D-48.
- **Acceptance signals:** Messages arriving during paging appear once; reconnect and equal-timestamp boundaries preserve order. Incomplete/denied history stays unknown. Legacy ungrouped posts remain separate.
- **Out of scope:** A browser disk history cache, archive browsing and delivery-state inference.
- **Open questions / stop conditions:** K-1 design resolved by D-81. D-80 selects overlapping IRC bounds and explicit incompleteness; P-70 supplies the accepted handoff/read contract. Isolated-server verification of coverage, ID evidence and cursor recovery remains required.

### CHAT-4. Share owner IRC sessions and verified room membership

- **Outcome:** Owner accounts retain real, shared membership independently of browser lifetime.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Expose join, part and membership observations for every configured owner through persistent account sessions in existing shared processes. Attach to Ergo's same-account multiclient identity; preserve account-specific membership and emit confirmed/stale/unknown results.
- **Phase:** 1. **Depends on:** CHAT-1. **Ordering:** critical path; may run in parallel once its listed dependencies and contract gates are satisfied.
- **Relevant decisions:** D-54–D-56, D-60–D-64, D-75.
- **Acceptance signals:** Two clients share a synthetic account's joins/parts; another account stays independent. Browser/web-process closure does not part it. Setup verification covers owner-only always-on without applying live settings.
- **Out of scope:** Issue/PR room creation policy, browser profile UI, changing owner authority or agent session records.
- **Open questions / stop conditions:** K-1 design resolved by D-81; verify the accepted session ownership and restart reconciliation contract. Live always-on setup remains a separate authorized operation.

### CHAT-5. Persist and fence logical send attempts before IRC writes

- **Outcome:** Every covered send has a durable, fenced attempt state before publication can start.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Implement the exclusive-writer journal and submit/check/cancel/retry/dismiss state machine, binding logical ID to actor, room and immutable content. Persist attempt generations and deadlines; allow conclusive cancellation only with evidence. Exercise an injected writer without live IRC.
- **Phase:** 1. **Depends on:** CHAT-1. **Ordering:** critical path; may run in parallel once its listed dependencies and contract gates are satisfied.
- **Relevant decisions:** D-33–D-36, D-45, D-47, D-48, D-61.
- **Acceptance signals:** Competing writers, delayed old attempts, crash windows and repeated requests cannot produce a second write. Pre-write cancellation is durable; dismissal survives restart and does not change the outcome.
- **Out of scope:** Ergo publication/history integration, browser pending-send storage and offline send queues.
- **Open questions / stop conditions:** K-1 design resolved by D-81; verify journal durability, fencing and D-79's clock contract rather than selecting new semantics.

### CHAT-6. Connect the shared sender to Ergo with conservative recovery

- **Outcome:** Owner sends recover from authoritative evidence and cannot retry an unprovable outcome.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Route covered owner submissions through the serialized sender, retaining the account and logical ID in IRC. Check authoritative history before retry; recover positive complete matches. Preserve unconfirmed outcomes whenever publication or recording cannot be disproved.
- **Phase:** 1. **Depends on:** CHAT-2, CHAT-3, CHAT-4, CHAT-5. **Ordering:** critical path.
- **Relevant decisions:** D-34–D-38, D-45–D-48, D-61, D-63.
- **Acceptance signals:** Isolated network/crash tests cover lost replies, partial batches and history-write gaps. No new write begins after its deadline, uncertain attempts cannot retry, and account changes cannot move an operation.
- **Out of scope:** Ergo patches, replacing the server, text-match deduplication and migration of unrelated legacy outbox entries.
- **Open questions / stop conditions:** K-1 / Q-3; distinguish covered sends from existing outbox traffic and prove no bypass for their IDs.

### CHAT-7. Publish role and agent-state facts from real sessions

- **Outcome:** Every displayed agent state has real session evidence and explicit freshness.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Normalize session-backed agent/assistant presence, role colors, project scope and idle/working/waiting-on-owner evidence into the shared read API. Include timestamps and stale/unknown states; keep owner membership a separate input.
- **Phase:** 1. **Depends on:** CHAT-1. **Ordering:** critical path; may run in parallel once its listed dependencies and contract gates are satisfied.
- **Relevant decisions:** D-5, D-28–D-30, D-53–D-55, D-57.
- **Acceptance signals:** Ended sessions disappear after reliable inventory; outages preserve honest stale/unknown evidence. Waiting is not invented from registration or IRC login, and assistants have no status pip.
- **Out of scope:** Current-task lines, new status semantics and browser-specific session discovery.
- **Open questions / stop conditions:** K-2 design mapping resolved by D-82/P-71; verify the runtime adapters and automatic typed-question wait transitions before claiming all states work.

### CHAT-8. Expose durable per-recipient bridge delivery outcomes

- **Outcome:** Clients can read each recipient's durable delivery failure and eventual acceptance.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Add structured recipient/logical-message attribution to bridge delivery and acceptance records and expose a typed read projection. Join late authenticated acceptance to earlier failures without scraping trace prose.
- **Phase:** 1. **Depends on:** CHAT-1, CHAT-2. **Ordering:** critical path; may run in parallel once its listed dependencies and contract gates are satisfied.
- **Relevant decisions:** D-41–D-43.
- **Acceptance signals:** Only the intended recipient's acceptance clears its error; dead letters and late replies survive restart. Resent/redirected copies remain independent and existing phone alerts are unchanged.
- **Out of scope:** New browser push, dismissal of delivery errors and milestone-2 progress indicators.
- **Open questions / stop conditions:** K-1 design resolved by D-81; verify native acknowledgement aliases against complete logical messages and authenticated recipient identity.

### CHAT-9. Apply mention routing to complete logical messages

- **Outcome:** Complete messages reach the intended recipients once without granting authority.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Dispatch a complete logical post once: ordinary project messages to the manager, explicit mentions to named sessions, general-room posts only to mentions. Support approved cross-project addressing while preserving executor authority checks and project-specific policies.
- **Phase:** 1. **Depends on:** CHAT-2, CHAT-8. **Ordering:** critical path; may run in parallel once its listed dependencies and contract gates are satisfied.
- **Relevant decisions:** D-4, D-16, D-41–D-43.
- **Acceptance signals:** No partial batch wakes an agent; replay does not create duplicate work. Untagged general messages remain undispatched and routing never grants authority or changes acceptance/phone-alert policy.
- **Out of scope:** Creating general/testing rooms, issue/PR room mapping and executing work inside plateia.
- **Open questions / stop conditions:** Q-3; existing legacy identities and project-specific delivery contracts must remain compatible.

### CHAT-10. Serve a protected Python web adapter with browser owner binding

- **Outcome:** The browser reaches shared chat through one protected Python adapter as its bound owner.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Add the Python 3.11+ application/static serving and typed shared-API adapter. Bind Phone/Desktop profiles through the server-backed cookie and enforce the approved localhost/Tailscale, Host, Origin and mutation protections. Expose history/live, send and membership operations without duplicating shared workflow. Add separate backend 3.11/3.14 checks under D-84, retaining the inbox's 3.10/3.14 matrix.
- **Phase:** 1. **Depends on:** CHAT-3, CHAT-4, CHAT-6, CHAT-7, CHAT-8, CHAT-9. **Ordering:** critical path.
- **Relevant decisions:** D-6, D-25, D-61, D-62, D-75.
- **Acceptance signals:** Synthetic requests reject arbitrary actors and invalid bindings; operations keep their original owner. API failures stay typed and static/API serving uses one protected origin.
- **Out of scope:** Copied chat code, browser IRC credentials, new shared IPC daemon and live service installation.
- **Open questions / stop conditions:** K-1 design resolved by D-81; consume P-70's accepted API/feed through the protected web boundary. Q-7's remaining page-level details stay separate.

### CHAT-11. Build the first text chat page with live history and composition

- **Outcome:** The owner can read, compose and post literal text on phone and desktop.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Introduce Svelte/Vite/TypeScript, Tiptap's minimal schema, pinned toolchain/lockfile and web CI feeding build-test. Implement responsive project/room navigation, literal live/history rendering, paging and Jump to latest, text drafting and posting in existing rooms.
- **Phase:** 1. **Depends on:** CHAT-10. **Ordering:** critical path.
- **Relevant decisions:** D-1, D-23–D-28, D-31, D-38–D-40, D-72.
- **Acceptance signals:** Phone and desktop can read and post through the real adapter; drafts retain exact text across room switches/reload. Keyboard and scrolling rules hold; web checks/build pass alongside existing Python tests.
- **Out of scope:** Images, Markdown, work-room migration and claiming stage 1 complete before CHAT-12–CHAT-14.
- **Open questions / stop conditions:** Q-7; resolve room URL/navigation contract and include owner-access binding from CHAT-10.

### CHAT-12. Complete browser send recovery and remembered dismissal

- **Outcome:** The browser presents every send outcome honestly through delay, failure and reload.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Present the countdown, zero/unconfirmed states, one transient failed-send entry and same-ID retry only after confirmed cancellation. Clear the saved draft at Send, honor shared dismissal, and bound history syncing without persisting pending-send data in the browser.
- **Phase:** 1. **Depends on:** CHAT-11, CHAT-6. **Ordering:** critical path.
- **Relevant decisions:** D-32–D-36, D-45–D-48.
- **Acceptance signals:** Latency, reload and lost-response scenarios match the shared outcome; no local timeout enables retry. A later message replaces the failure, and a new uncertainty is not muted by an old dismissal.
- **Out of scope:** Agent-delivery error dismissal, offline sending and browser pending-send recovery storage.
- **Open questions / stop conditions:** K-1 / Q-3; use the reviewed sender outcomes, not UI inference.

### CHAT-13. Show room users and owner join/leave controls

- **Outcome:** The room shows the correct users, roles and status pips with working membership controls.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Render project/global user lists with role colors, approved pips and freshness. Keep separate joined owner entries and live assistants without pips. Add room Join/Leave controls and mention insertion while preserving drafts.
- **Phase:** 1. **Depends on:** CHAT-11, CHAT-4, CHAT-7. **Ordering:** critical path.
- **Relevant decisions:** D-28–D-30, D-40, D-53–D-64.
- **Acceptance signals:** Phone panels and desktop columns share behavior. Changes from another IRC client appear correctly; a rerender does not undo Leave. Stale evidence cannot manufacture membership or idle status.
- **Out of scope:** Current-task lines, archived-room controls and session scanning in the web app.
- **Open questions / stop conditions:** K-2 design mapping resolved by D-82/P-71; consume verified canonical state facts from CHAT-7.

### CHAT-14. Display bridge-backed agent-delivery errors on messages

- **Outcome:** A posted message's unresolved agent-delivery failure remains visible until acceptance.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Attach persistent per-recipient delivery errors to posted messages using the shared projection, including late acceptance. Keep original and redirected/resubmitted copies independent and preserve bridge alerts.
- **Phase:** 1. **Depends on:** CHAT-11, CHAT-8, CHAT-9. **Ordering:** critical path.
- **Relevant decisions:** D-41–D-43.
- **Acceptance signals:** Errors survive view switches/reloads; only correct recipient acceptance clears them. Viewing an error creates no new phone alert and offers no dismissal.
- **Out of scope:** Queued/delivered/accepted progress badges and browser push.
- **Open questions / stop conditions:** None beyond the reviewed CHAT-8 contract.

### CHAT-15. Store capped images with durable references and safe cleanup

- **Outcome:** Capped image assets survive every valid reference and expire only when proven unused.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Add bounded PNG/JPEG/WebP processing, protected upload/read endpoints, durable capped assets and draft/send/archive reference accounting. Include 24-hour deletion only after proven unreferenced, plus a documented archive export/pinning contract usable without the web process.
- **Phase:** 2. **Depends on:** CHAT-12, CHAT-13, CHAT-14. **Ordering:** critical path.
- **Relevant decisions:** D-18–D-23, D-49, D-68, D-69, D-83–D-86, D-93, D-94.
- **Acceptance signals:** Input/output limits and malformed-image failures are enforced. Drafts, retries, uncertain sends and archives protect assets across restart; only provably unused objects expire. No retained HD attachment and no unconditional HEIC dependency.
- **Out of scope:** Inline rendering, archive UI and live data cleanup.
- **Open questions / stop conditions:** K-3 is settled by D-83–D-86. Implement and verify P-75's catalog/reference/export contract and the approved processing bounds; do not treat approval as runtime qualification.

### CHAT-16. Compose image links through paste and the image button

- **Outcome:** The owner can paste or select images into a persistent draft and send their managed links.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Insert managed placeholder links at the draft position from paste or chooser, upload immediately and post only on Send. Preserve text/images across switches and reloads, tie references to send IDs, and block the whole message on failed or unfinished uploads.
- **Phase:** 2. **Depends on:** CHAT-15, CHAT-11. **Ordering:** critical path.
- **Relevant decisions:** D-17–D-19, D-23, D-33, D-38, D-49, D-67–D-69.
- **Acceptance signals:** Both phone and desktop entry paths use the same durable draft/upload flow. Cancelled selection preserves the draft; failed uploads remain retryable/removable and image links count toward the text limit.
- **Out of scope:** Inline images, external image fetching and retaining an HD source as a saved attachment.
- **Open questions / stop conditions:** K-3 is settled. Verify D-83's unfinished-image reload behavior and P-75's draft/reference transitions.

### CHAT-17. Render managed images inline and verify phone formats

- **Outcome:** Managed images appear inline in the draft and conversation on phone and desktop.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Replace managed link placeholders with inline images in composer and conversation; fit the column and open the stored capped image on selection. Keep outside URLs as links. Complete real phone chooser/paste format verification.
- **Phase:** 2. **Depends on:** CHAT-16. **Ordering:** critical path.
- **Relevant decisions:** D-17, D-21, D-22, D-67–D-69, D-73.
- **Acceptance signals:** Position and draft text remain stable; live/history render only managed objects without outside previews. Record phone behavior privately using safe evidence; unconverted HEIC triggers its separate owner dependency decision.
- **Out of scope:** Animation, video, previews, Markdown and automatic installation of a HEIC decoder.
- **Open questions / stop conditions:** Q-6; conditional HEIC follow-up only if actual phone evidence meets D-68.

### CHAT-18. Implement shared room identities and issue/PR lifecycle mapping

- **Outcome:** Every supported work item resolves to one shared room with a consistent lifecycle.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Introduce stable room kinds, issue/PR associations and lifecycle generations. Create on first use, reuse rooms on reopened/replacement work, preserve per-project legacy mode until cutover, and update the owner's global skills at this stage. Represent main/testing/general/chat-test routing and scope.
- **Phase:** 3. **Depends on:** CHAT-17, CHAT-1, CHAT-4, CHAT-9. **Ordering:** critical path.
- **Relevant decisions:** D-8–D-11, D-16, D-44, D-51–D-59, D-74, D-87, D-89–D-93.
- **Acceptance signals:** Concurrent first use creates one logical room; multiple PRs share the issue room and last-open-PR closure is correct. Unknown tracker evidence does not fabricate closure, and untouched projects retain legacy behavior.
- **Out of scope:** Archive storage, live room provisioning, custom rooms and tracker mutations by plateia.
- **Open questions / stop conditions:** K-4 is settled. Implement P-79/P-80's registry, association and fresh tracker/work-round contract, P-81's shared control store and P-82's receipts/reconnect rules. Unexpected links or ownership go to the manager; setup remains separately authorized.

### CHAT-19. Verify structured room archives with their images and membership cleanup

- **Outcome:** A work room closes only with verified structured history, images and membership cleanup.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Implement admission/drain/freeze, export of the complete enumerated available-history snapshot under D-88's limits, and image pin/copy verification. Follow P-81's immutable publication protocol; part every owner after the required archive steps, using P-78/P-82's generation and operation receipts.
- **Phase:** 3. **Depends on:** CHAT-18, CHAT-3, CHAT-6, CHAT-15. **Ordering:** critical path.
- **Relevant decisions:** D-14, D-15, D-20, D-51, D-70, D-71, D-86–D-94.
- **Acceptance signals:** Archives work with the web process stopped; known missing history/images or failed writes cannot claim success. Verify the running-state posting block and preserve D-88/D-93's late-post and crash limits explicitly. Interruption/reopen races preserve archives and fence old cleanup; no plaintext transcript or half-published archive.
- **Out of scope:** Dismissing uncertain outcomes, the late-send window, archive browser and live legacy deletion.
- **Open questions / stop conditions:** K-3/K-4 are settled. Missing coverage, images, current permissions or required completion receipts remain unresolved outcomes; apply the accepted manager-first recovery rather than weakening verification.

### CHAT-20. Close dismissed send gaps and reconcile bounded late history

- **Outcome:** Dismissed uncertainties close with explicit gaps and bounded, verified later correction.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Allow closure from current history after per-send dismissal, recording unresolved IDs and gap metadata. Check all-client late appearances under D-88 only within 24 hours of closure and on genuine reopen; publish verified immutable archive/image corrections and retain original notes with annotations.
- **Phase:** 3. **Depends on:** CHAT-19, CHAT-5, CHAT-6. **Ordering:** critical path.
- **Relevant decisions:** D-47–D-49, D-65, D-66, D-70, D-86, D-88, D-89, D-92–D-94.
- **Acceptance signals:** Expiry/restart cannot extend the watcher or imply cancellation. Corrected messages occur once; independent gaps persist and uncertain images remain protected. Failed correction preserves the existing archive.
- **Out of scope:** Indefinite watchers, resending, reopening just to correct history and delivery-error dismissal.
- **Open questions / stop conditions:** K-4 is settled. Preserve the original closure deadline and generation checks; incomplete history or failed correction cannot erase gaps or replace a verified archive.

### CHAT-21. Present every room type and its lifecycle in plateia

- **Outcome:** The page follows the canonical room lifecycle without bypassing archived-room rules.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Switch room discovery/navigation to the canonical room model, including global/testing lists, issue/PR first use and reopened rooms. Show grey closing state, verified archival removal, the milestone-1 archived notice and its gap marker. Keep creation/join authority in shared chat.
- **Phase:** 3. **Depends on:** CHAT-18, CHAT-19, CHAT-20, CHAT-13, CHAT-14, CHAT-17. **Ordering:** critical path.
- **Relevant decisions:** D-9, D-14–D-16, D-28, D-51–D-60, D-65, D-71, D-89, D-93, D-94.
- **Acceptance signals:** Phone and desktop expose all configured room types and correct user scope. Old archived links cannot create replacements or join; a reopened room restores its conversation and joins through the bound account.
- **Out of scope:** Archive browsing/search, custom room rules, new workflow logic in the browser and live provisioning.
- **Open questions / stop conditions:** None beyond the reviewed lifecycle contract and the owner's separately performed room provisioning.

### CHAT-22. Provide guarded per-project cutover and owner-specific guidance

- **Outcome:** The manager can review and execute a resumable per-project cutover plan within owner authorization.
- **Implementation owner:** Shared skills, local delivery under D-75.
- **Scope:** Add the resumable per-project cutover procedure/tooling and executed skill guidance: inventory/dry-run, owner warning/closure prerequisites, named parked exceptions, legacy archive, design-room discard without logs, mode switch and new-room convention. Include setup verification for configured owner always-on.
- **Phase:** 3. **Depends on:** CHAT-21, CHAT-18, CHAT-19, CHAT-20. **Ordering:** critical path.
- **Relevant decisions:** D-8, D-12, D-13, D-16, D-44, D-52, D-64, D-71, D-74, D-87–D-94.
- **Acceptance signals:** Synthetic plans refuse unclosed non-exempt work, preserve permanent/parked rooms, isolate other projects and recover from partial failures. No destructive step occurs as a side effect of reading the plan; record completed and remaining steps.
- **Out of scope:** Actually warning/closing/discarding/switching live projects during implementation tests or changing canonical owner authority again.
- **Open questions / stop conditions:** Q-8 operational mapping remains the manager's concrete rollout work; live steps require the applicable owner authorization and D-44 warning.

### CHAT-23. Qualify the complete milestone across service and browser boundaries

- **Outcome:** The complete milestone has reproducible integration evidence across its component boundaries.
- **Implementation owner:** Plateia (`coghex/plateia`), normal PR delivery.
- **Scope:** Add the bounded end-to-end qualification harness/scenarios for the integrated shared APIs, web app, managed images and lifecycle. Cover cross-component failures not already proved by each slice's local tests, using isolated services and invented data; record phone/desktop acceptance evidence.
- **Phase:** 3. **Depends on:** CHAT-22. **Ordering:** critical path.
- **Relevant decisions:** D-1, D-6, D-34–D-49, D-65–D-74, D-86–D-94.
- **Acceptance signals:** Restart during send plus image retention plus archive/reopen preserves the agreed outcomes. Shared work continues with plateia stopped; all three stage checkpoints pass and no deferred feature is needed to complete milestone 1.
- **Explicit web-stopped check (D-93):** In the isolated integration environment, stop only the plateia web process and leave it stopped throughout the scenario. Use independent IRC clients to read/post and address a manager; verify bridge delivery and manager acceptance. Drive an invented work room through freeze, verified structured archive with image copies, and legitimate reopen/owner join through shared chat. Keep the local asset command installed and invoke it on demand; no web listener or resident asset service may be required. Verify shared reconnect recovery and the bounded late check still operate, then start the web process and verify it reads the resulting state without replaying lifecycle actions. Record process/service separation and the observed outcomes; any dependency on a running web process fails this check. Do not stop live services for qualification.
- **Web-served availability (D-94):** Page, uploads and managed image URLs need not work while the web process is stopped. Verify that restart serves the same retained image URLs and archive readers never required those URLs; do not add an independent image server.
- **Out of scope:** A new feature batch, duplicated unit tests, production fault injection and claiming HEIC or live cutover verification without evidence.
- **Open questions / stop conditions:** K-1–K-4's design contracts are resolved; qualification must prove their agreed outcomes and limits. Any triggered D-68 dependency decision still needs owner approval, and owner acceptance determines milestone completion.

D-76 and D-95 approve the slice boundaries and design readiness.
`process-design-doc` processes the epic first and one child
at a time, obtaining separate signoff before each tracker artifact is filed.
Under D-96, bring future child drafts to the project manager for signoff; new
design choices still go to the owner. The manager runs filed-issue reviews. D-75's local skills route does not
waive issue approval, exact-commit review or merge authorization.
