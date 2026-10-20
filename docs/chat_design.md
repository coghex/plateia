# Milestone 1: working chat

This design turns [the accepted vision](vision.md) into a usable chatroom on
phone and desktop. It records the milestone's existing owner decisions and
keeps unresolved choices explicit. It does not authorize implementation,
installation, live configuration or service changes.

Design state: `exploring`

Status legend: `[ ]` unprocessed · `[#N]` linked to issue N · `[no-issue]`
reviewed and deliberately not tracked separately · `[deferred]` blocked on a
concrete precondition

## Processing status

- [ ] EPIC. Deliver milestone 1: working chat on phone and desktop

No child slices are fully defined yet. D-17 records the agreed image delivery
sequence: placeholder link first, inline image later within milestone 1.
Remaining material choices precede full dependency ordering and issue
boundaries.

## Epic contract

- **Goal:** the owner can read live conversations, read earlier messages,
  post as the owner, address agents, see room users and share pasted images
  from both a phone and a desktop browser.
- **Done when:** all milestone 1 room types support that experience;
  closed issue/PR conversations and their images are preserved together;
  PNG sharing works; and the
  five V-12 guarantees hold through connection loss and process restarts.
  The concrete acceptance scenarios follow the decisions below.
- **Users and operators:** one owner; the existing shared chat services and
  agents continue operating independently of the web application.
- **Arc label:** None proposed.
- **Publication:** this document belongs to `coghex/plateia`, on `master`.
  Shared workflow changes belong in the skills repository under D-78; their
  handoff and tracking must be settled before processing affected slices.

## Current state and evidence

Read-only checks on 2026-10-20:

| Evidence | Established fact and limit |
| --- | --- |
| `docs/vision.md` at `228159c` | The owner accepted working chat as milestone 1, including posting, history, room users and pasted PNG images. |
| `README.md`, `packages/routine_inbox/`, repository file inventory | There is no web application. The routine inbox is the only package; it is not an existing browser chat backend. |
| `docs/plateia_design.md`, D-78; retirement PR #35 | The skills repository owns chat. Plateia's captured chat and release packages were removed. Retired outbox-authority and request-store designs are not live capabilities. |
| Skills repository, `chat/SKILL.md` | Current guidance still describes request channels. D-8 settles reconciliation in the owner's global skills when room implementation is reached; the live guidance has not changed. |
| Skills repository, `chat/scripts/chatlib.py`, `post` | Posting authenticates an account and handles IRC message formatting. Its result is a line count; its public signature has no caller-supplied logical operation ID. This alone does not establish browser retry safety. |
| Skills repository, `chat/scripts/chat-bridge`, `project_for`, `handle` | Routing uses project channel names/prefixes. Ordinary direct agent delivery is restricted by sender category, unlike V-6's any-agent communication. Queuing precedes logging, with crash replay permitted. These are integration gaps, not evidence that the new contract is implemented. |
| Skills repository, `chat/SKILL.md`, identity and delivery sections | Stable identities, session discovery, history, outbox, delivery and acceptance machinery exist. Their exact guarantees and usable interface require focused verification before designing the web adapter. IRC login alone is not presence evidence. |
| GitHub issue inventory, `coghex/plateia` | No open issues on 2026-10-20. Earlier foundation epic #10 and collaboration epic #26 are closed; neither is an open milestone 1 tracker. Recheck overlap before readiness. |

The brief identifies general and project testing rooms as not yet configured.
This design does not inspect or publish private configuration. Creating those
rooms in live configuration remains the owner's action.

### Relationship to the earlier design

`plateia_design.md` remains historical design evidence; this document does
not silently inherit its former epic or delivery order.

- D-78 governs chat source ownership and retires the capture/release work.
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
terminal. Selecting a user starts an addressed message. The same features
are available on phone and desktop; layout and interaction details remain
to be discussed.

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
- A one-time cleanup before adopting the new room model: archive completed
  work rooms, retain active/permanent rooms, and discard existing design
  rooms without saved logs (D-12/D-13/D-16).
- Each room's user list, role colors, and agent idle/working/waiting pips.
- Pasted image sharing with PNG support at minimum.
- V-12 reliability, a Python server, localhost listening and access through
  `tailscale serve`.

Out of scope:

- Milestone 2's archive browser/search, browser push, unread indicators,
  highlighted owner mentions and queued/delivered/accepted indicators.
- The board and its workflow, reactions, and tracker mini-cards.
- Dedicated design rooms (rejected by D-16).
- Designing testing-agent workflows, replacing IRC, or controlling sessions
  directly from the web application.
- Any live configuration, accounts, installation, schedules or service changes
  during design work.

Deferring delivery indicators does not defer durable delivery, duplicate-work
prevention or visible unresolved failures. Browser push belongs to milestone
2; the milestone 1 failure-reporting contract must still be settled.

## Decisions

D-1 through D-7 restate accepted source decisions. D-8 onward record the
owner's answers in this design conversation. None approves unspecified mechanisms or
delivery slices.

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

### D-7. Leave framework selection and image details for discussion

Owner decision 2026-10-20, V-3/V-12: the owner and designer settle the page
framework and how to evaluate it, and discuss image upload/display. PNG is
required. No candidate, prototype exercise or upload mechanism is selected
by this document.

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
archive live rooms now. D-13 settles which rooms to archive, retain or discard;
preservation and how active work carries forward still need an agreed
contract (Q-8). It does not require milestone 2's archive browser.

### D-13. Archive completed work rooms; discard existing design rooms

Owner decision 2026-10-20, amended by the owner's later answer:

- Archive completed implementation rooms after saving and verifying their
  logs. Finished means the PR is closed out; a room covering several PRs
  remains active while any associated PR is open.
- Keep active implementation rooms and permanent main/general/testing rooms.
  Bring uncertain cases to the owner.
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
screenshots may require cropping before paste. Input decoding and processing
need separate resource limits; the stored-file cap is not a total memory
budget.

### D-23. Keep each room's unfinished message on the same device

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: accept P-16. Each room retains its unfinished message, including
images, across room switches and page reloads on the same device.

Successfully sending or explicitly discarding a draft clears it. Draft
storage must preserve its attachments; unused-upload cleanup must not remove
images still belonging to a saved draft. The storage mechanism and recovery
of interrupted uploads remain to be designed. Cross-device draft
synchronization is not part of this decision.

### D-24. Keep disconnected drafting minimal

Owner decision 2026-10-20, relayed by the project manager as an authenticated
reply: allow typing while disconnected, with Send disabled until
reconnection. Treat this as a low-priority edge case.

Handle it within the normal composer and connection-state behavior. Do not
create a separate offline slice, offline send queue or extra machinery for
it. This does not weaken V-12's existing guarantees for requests already
submitted or acknowledged.

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

The incoming file-byte/pixel limits and processing-memory budget still need
their own technical contract: shrinking the output does not bound decoding
the input. [Pillow's image documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html)
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

For Q-7, recommend React with Vite for the browser page, served by the Python
backend. Evaluate the choice through the actual phone/desktop chat slices:
live/history continuity, draft recovery, inline image composition and
responsive layout. A framework does not supply V-12's durable delivery
contract; that remains shared-service and backend work.

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
conventions to the project; this recommendation accepts that tradeoff for a
Python-backed chat client. Tiptap is evidence of an integration option,
not an editor-library decision.

Proposed evaluation method: use this written comparison for the owner's
selection, then verify real chat on phone and desktop during delivery.
Do not add a separate three-framework prototype exercise. Both the framework
choice and this evaluation method remain for owner signoff.

## Open questions

### Q-1. Where does the new room model live?

**Resolved by D-8.** Issue/PR rooms are the standard shared model; include an
owner-specific global skill change when the room-model work is implemented.

### Q-2. What is a work room's exact lifecycle?

**Opening resolved by D-9:** create on first use, including issue review.
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
Remaining: PR closure without merge, issue/PR reopening, replacement PRs,
and other work without a tracker item. Existing rooms, including grouped request
rooms, are addressed under Q-8. Settle how closure preserves
a complete file log despite outages or late posts, and how completion or
failure of preservation becomes visible. No archive browser in this milestone.

### Q-3. What interface separates plateia from shared chat?

Settle discovery, history/live handoff, posting as the owner, stable operation
identity, mention routing, delivery failure facts and presence. Verify the
canonical source before specifying changes. New workflow behavior belongs in
the skills repository. Do not revive retired packages by inference.

### Q-4. What are the posting and recovery interactions?

**Draft recovery resolved by D-23:** keep each room's unfinished message and
images across switches and reloads on the same device.
**Disconnected behavior resolved by D-24:** keep typing, disable Send,
and keep this a minimal edge case within normal chat delivery.
Still settle acknowledgement, uncertain outcomes,
safe retry and long-message handling. Distinguish server acceptance from
recipient acceptance without importing milestone 2's full receipt display.
Settle how undelivered messages remain visible and how the owner is notified
before browser push exists.

### Q-5. How are room membership and session status presented?

Settle membership for project main/work/testing and chat-testing rooms,
human/assistant presence sources, freshness rules, status explanations and
the role/pip palette. Reconcile prior D-52 with this milestone and never
present missing evidence as idle or current.

### Q-6. How should images be uploaded, displayed and retained?

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
Remaining: PNG paste on phone and desktop,
input/processing limits, link access, unused-upload cleanup and archive mechanics.
Plateia stores images; shared chat records their links.

### Q-7. Which page framework, evaluation method and browser layout?

P-18 proposes React with Vite, selected from the written comparison and
verified in real chat slices. The owner must approve the framework and
evaluation method. Layout remains open. Reconcile the earlier browser trust
boundary with posting and attachments.

### Q-8. How should existing rooms be archived and migrated?

D-12 requires an explicit initial archive/migration step.
**Selection resolved by D-13:** archive completed implementation rooms after
saving and verifying logs; retain active/permanent rooms and ask about
uncertain ones. Completed means closed-out PRs. Discard existing design rooms
at rollout without migration or saved logs; new design conversations use
general (D-16). The current reporting channel stays in use for now.

**Archived behavior resolved by D-14:** leave active lists and reject new
posts until reopened, retaining the file log.
**Closing transition resolved by D-15:** block new submissions, settle
already-accepted posts and verify the archive; keep the title grey and
problems visible until verified closure.
Still specify archive completeness and verification,
active-room mapping (including grouped or unlinked
request rooms), interruption/retry behavior and the rollout sequence.
Include the conversation's images in the archive and its verification
(D-20).
For rooms being archived, preserve conversation history and references.
Discarded design rooms are exempt. Do not assume existing JSONL
files alone prove a complete archive, or that removing a room from a client
is archiving it.

## Verification strategy

Use invented projects, accounts, messages, tracker items and images. Never
publish live configuration, private chat, personal paths or live screenshots.

The eventual design must make these observable: phone/desktop parity;
history/live continuity; correct project and general-room routing; no
duplicate work after retry; acknowledgement durability; automatic recovery;
honest presence freshness; durable unresolved errors; preserved room logs;
and image failures that cannot masquerade as a sent message.

Verify shared delivery and room lifecycle with the plateia process stopped.
Verify localhost binding and the browser boundary with synthetic requests.
Exact tests and fixtures follow the approved contracts rather than inventing
behavior here.

The initial cleanup needs synthetic examples of closed-out PR work,
incomplete implementation work, existing design rooms, permanent rooms and
ambiguous rooms. Show that archived work records survive, active
implementation work remains reachable, and design rooms are discarded
without conversation migration or saved logs.
Verify that new design conversations use general, that completing a design
never closes general, and that discarding a design room does not mark
unfinished tracker work complete.

Closing-state scenarios must show the grey room title until verification
succeeds, preserve already-accepted posts, and leave failures visible rather
than prematurely removing the room.

Image composition checks must cover PNG paste while writing a message,
continuing to type with the image visible, and posting only on Send, on
phone and desktop. Verify the early placeholder-link stage and the final
inline-image stage separately; milestone 1 is not complete at the placeholder.
In both stages, an upload failure must retain the whole unsent message and
offer retry or image removal, without posting text alone or a broken link.
Verify that pasting begins the upload while typing can continue, and that a
completed upload alone never posts a message.
Draft recovery checks must retain text and images across room switches and
reloads on the same device, including drafts whose uploads are interrupted
or have failed. Cleanup must not remove saved-draft attachments.
Archive checks must show that the conversation and its image files are
preserved together, that references still resolve, and that a missing image
cannot be silently treated as a fully verified archive.
Check stored dimensions and file size against the approved caps independently
of viewport width. Resizing the window changes only display; opening and
archiving use the capped version, with no retained high-resolution original.

## Delivery plan

No fully specified child slices yet. Define stable `CHAT-N` entries and mirror them in
Processing status after blocking choices are settled. Each must identify its
owning repository, dependencies and a reviewable outcome. Include D-8's
owner-specific global skill change with the room-model work; do not make
changing the live convention an advance prerequisite for unrelated work.
Keep D-24's disconnected behavior within normal composer work; it does not
drive slice boundaries or add offline machinery.
Include D-16's design-agent use of general in that change, with no dedicated
design-room slice or conversation migration. General's live provisioning
remains the owner's action.
Include D-12's initial cleanup as an explicit rollout step before
adopting the new room model. Its selection and preservation contract (Q-8)
must be settled before defining the affected slice and execution procedure;
it is not postponed to milestone 2's archive browser.
Discard existing design rooms during that rollout without saved logs.

Image sequencing from D-17: first deliver pasted-image insertion as a
placeholder link in the message; then deliver inline image display,
including in the message being composed. Both are milestone 1 work. Record
their final issue boundaries and dependencies once the upload contract and
page framework are settled.
Include image preservation in the archive contract from the first image
slice; placeholder links do not defer D-20's storage obligations.
Apply D-21's upload-time cap in that first slice too; inline rendering is
the later presentation change.

The owner explicitly
approves readiness before issue processing, and each tracker artifact gets
separate signoff. The project manager runs filed-issue reviews.
