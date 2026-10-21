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
| Skills repository, `chat/scripts/chatlib.py`, `_round`, `already_posted`, refreshed 2026-10-20 | The sender waits for a PONG after writing IRC messages. Retry reconciliation matches account/text/time in local logs; partially landed posts may be retried whole. There is no message-ID check-and-cancel API. This does not meet D-34's at-most-once contract. |
| Read-only Ergo capability probe, shared sender/bridge source and isolated multiline tests, 2026-10-20 | Ergo advertises `batch`, `message-tags` and `draft/multiline`. Shared chat negotiates multiline and joins each batch in live/history delivery. Ten selected existing tests pass. Oversized posts still become separate batch records without a common logical message ID; leading/trailing blank lines are stripped. See D-37 for the required gaps. No test messages were sent to live rooms. |
| Upstream Ergo at `678d29d5442b00aa4ed79e86f9139443a3e1994a`, publication path | No caller-ID deduplication or per-message expiry check was found. P-36's server-patch proposal was rejected; D-45 changes the deadline to the single writer's decision cutoff. |
| Installed Ergo 2.19.1, read-only capability/history checks for D-45 | Negotiated message tags, batches, multiline, echo-message, labeled-response, CHATHISTORY, server-time and account tags. History replay retained an existing client-only reply tag; repeated reads retained the same value. A mid-window server-msgid lookup succeeded. No tagged test message was posted, and no config/service change or restart was made. |
| Ergo 2.19.1 source, follow-up recovery analysis | Channel delivery precedes history insertion; a history insertion error is logged without undoing delivery. Session logout does not wait for the old command loop to finish. P-39 records why these are insufficient proofs of safe cancellation after an uncertain write. No live fault was induced. |
| Skills repository, `chat/scripts/chat-bridge`, `project_for`, `handle` | Routing uses project channel names/prefixes. Ordinary direct agent delivery is restricted by sender category, unlike V-6's any-agent communication. Queuing precedes logging, with crash replay permitted. These are integration gaps, not evidence that the new contract is implemented. |
| Skills repository, `chat/SKILL.md`, identity and delivery sections | Stable identities, session discovery, history, outbox, delivery and acceptance machinery exist. Their exact guarantees and usable interface require focused verification before designing the web adapter. IRC login alone is not presence evidence. |
| Shared `chat-bridge`, `Deliveries._record`, `_dead`, `accept`, `accepted`, and `pchat trace`, checked for D-41 | The bridge records attempts, dead letters and authenticated replies/acks. Delivery success and recipient acceptance are separate. Attempt records omit a structured recipient field; acceptance closes matching pending items, while late acknowledgements also have a separate record. `trace` renders request-oriented text, not a typed per-message/per-recipient outcome. A shared read contract is required for plateia. |
| GitHub issue inventory, `coghex/plateia` | No open issues on 2026-10-20. Earlier foundation epic #10 and collaboration epic #26 are closed; neither is an open milestone 1 tracker. Recheck overlap before readiness. |
| `.github/workflows/ci.yml`, refreshed 2026-10-20 | Python 3.10/3.14 tests feed an always-running `build-test` aggregate that rejects any non-success dependency. There is no frontend package or Node build yet. D-27 extends this existing gate. |

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
- Dedicated design rooms (rejected by D-16).
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

As clarified by D-33, Send clears the saved draft immediately; explicitly
discarding a draft also clears it. Draft
storage must preserve its attachments; unused-upload cleanup must not remove
images still belonging to a saved draft. The storage mechanism and recovery
of interrupted uploads remain to be designed. Cross-device draft
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
outcome until IRC history settles it.

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
and has its own deadline. The expired original attempt must remain unable
to post, even after a retry is authorized.

Q-3 must cover requests queued before expiry, writes in flight at cutoff,
sender restart, clock disagreement and history lag. The sender's expiry
rule must work without a surviving browser to request cancellation. A stale
or incomplete history view cannot prove a message missing: retyping safely
depends on settled authoritative history, as clarified by D-45. Resolve
the settlement proof before claiming this contract is deliverable.

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
5. On a healthy original connection, an ordered round-trip after the write,
   followed by history retrieval, is the candidate settlement barrier. A
   reconnect alone is not one: an old server session might still process
   buffered bytes. Recovery must prove the prior write can no longer appear
   before declaring absence or allowing another write for the ID. Validate
   this against the installed server's session lifecycle and crash cases;
   a guessed sleep or local socket close is insufficient.
6. If the matching complete message is found, record posted and return it
   without writing again. If absent after proven settlement, record the
   cancelled attempt. Before an explicit same-ID retry, check history again
   and allow one new attempt under the exclusive writer. An unproven outcome
   stays unconfirmed without retry, as D-35 requires.

The installed capabilities support tagged history recovery, but the exact
disconnect/restart settlement proof and 64 KiB multiline transport are
still open technical gates. No assertion of completed at-most-once recovery
follows from capability negotiation or these read-only history queries.
The shared skills/service owns this protocol; plateia presents its results.
P-39 records a further source-level gap: even a settled write can have reached
recipients without a durable history entry. Absence needs completeness of
recording as well as completion of the old writer.

### P-38. Mark reloaded history as syncing while a prior send is unsettled

Resolved by D-46 with the owner's time-limit condition: “Syncing history...”
lasts at most 15 seconds from the room's first history request in a loaded
page, then becomes “unconfirmed: awaiting history” until recovery completes.
No pending message or retry entry is restored.

### P-39. Keep irrecoverable send outcomes unconfirmed, or evaluate another server

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

Recommend retaining the no-patch approach with an explicit limitation:
when recording or settlement cannot be proven, the outcome may remain
unconfirmed indefinitely, without retry or a claim that retyping is safe.
D-46 still bounds the syncing presentation to 15 seconds; it does not impose
a deadline for obtaining truth. Found complete IDs remain posted; an attempt
known never to have been written can be cancelled. Lost confirmation plus
missing history alone must never authorize a repeat write.

This limitation needs owner signoff before treating P-37 as deliverable.
If eventual posted-or-cancelled recovery is required for these cases, evaluate
a different IRC server as D-45 directs. Never propose an Ergo patch. No
replacement has been selected, and no live change is authorized.

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

D-34/D-45 require browser-created IDs, one shared sender serializing
submission/check/cancel/retry, and a history check before any same-ID retry.
The writer must not start an IRC write after its attempt's deadline; a write
already in flight remains unconfirmed until settled. Distinguish a fresh
retry from a delayed original attempt, and establish deadline clock semantics,
exclusive writer ownership across restart, and complete authoritative history
coverage after the old write settles. The current PONG/text-match sender
does not satisfy this. D-36 rejects browser recovery storage; enforcement
must work after the browser disappears. P-37 records the proposed protocol
and its unresolved settlement proof as a design-readiness gate. A missing
bridge entry, elapsed timer or newly connected reader is not cancellation.
P-36 is rejected: no IRC patch. If the installed server cannot support the
contract, bring a different server choice to the owner.
P-39 now asks whether permanently unconfirmed outcomes are acceptable when
Ergo's history cannot establish what happened; otherwise evaluate another
server. Do not hide this limitation behind the bounded syncing notice.

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

### Q-4. What are the posting and recovery interactions?

**Draft recovery resolved by D-23:** keep each room's unfinished message and
images across switches and reloads on the same device.
**Disconnected drafting resolved by D-24:** keep typing and disable Send.
D-34 supersedes the minimal framing for submitted messages.
**Keyboard submission resolved by D-31:** desktop Enter sends,
Shift+Enter adds a line; phone Return adds a line and Send posts.
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

**Role and status colors resolved by D-29:** retain role colors, with grey
idle, green working and amber waiting dots for agents. Human users and
assistants have no dot. Similar role/dot hues are acceptable.
Settle membership for project main/work/testing and chat-testing rooms,
human/assistant presence sources, freshness rules and status explanations.
**Current-task line deferred by D-30:** optional milestone-2 setting,
excluded from milestone 1; no default selected.
Reconcile prior D-52 with this milestone and never present missing evidence
as idle or current.

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

**Framework and evaluation resolved by D-25:** Svelte with Python, validated
in real chat slices. D-26/D-27 settle the delegated build/editor/toolchain/CI
choices. **Layout resolved by D-28:** narrow rooms left, wider users right,
chat taking most of the desktop screen; room/user lists behind buttons on
phone. **History scrolling resolved by D-39:** load older on scroll-up,
preserve the reader's place on new arrivals, and provide “Jump to latest”.
Reconcile the earlier browser trust boundary with posting and attachments.

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
Still specify normal archive verification and interruption/retry behavior.
The manager owns D-44's concrete activation procedure and parked-room
mapping. Do not reopen the rejected gradual-migration plan.
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

Check D-28 with realistic synthetic room/user names and conversation content
at desktop and phone sizes and while resizing: rooms narrower than users,
chat taking most of the desktop screen, and both lists reachable through
buttons on phone without losing the current conversation or its draft.

Check D-29 with each role and status, including an amber waiting dot beside
a yellow designer name. Status must also be readable without relying on
color; humans and assistants have no dot, and unknown evidence never
appears as idle.

Verify shared delivery and room lifecycle with the plateia process stopped.
Verify localhost binding and the browser boundary with synthetic requests.
Exact tests and fixtures follow the approved contracts rather than inventing
behavior here.

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

Image composition checks must cover PNG paste while writing a message,
continuing to type with the image visible, and posting only on Send, on
phone and desktop. Verify the early placeholder-link stage and the final
inline-image stage separately; milestone 1 is not complete at the placeholder.
In both stages, an upload failure must retain the whole unsent message and
offer retry or image removal, without posting text alone or a broken link.
Verify that pasting begins the upload while typing can continue, and that a
completed upload alone never posts a message.
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

Draft recovery checks must retain text and images across room switches and
reloads on the same device, including drafts whose uploads are interrupted
or have failed. Cleanup must not remove saved-draft attachments.
Archive checks must show that the conversation and its image files are
preserved together, that references still resolve, and that a missing image
cannot be silently treated as a fully verified archive.
Check stored dimensions and file size against the approved caps independently
of viewport width. Resizing the window changes only display; opening and
archiving use the capped version, with no retained high-resolution original.

Long-message verification must cover exact leading, trailing and internal
line breaks, Unicode split boundaries, negotiated byte/line limits, multiple
batches, interleaved same-sender posts, repeated identical posts, truncated
batches, reconnect and history replay. Assert one web message per logical
post and one instruction dispatch, with no merging of separate posts or
partial dispatch when a fragment is absent. Missing multiline capability
must exercise the specified equivalent contract or an explicit refusal,
never heuristic joining. Existing 10-test evidence is a baseline only.

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

No fully specified child slices yet. Define stable `CHAT-N` entries and mirror them in
Processing status after blocking choices are settled. Each must identify its
owning repository, dependencies and a reviewable outcome. Include D-8's
owner-specific global skill change with the room-model work; do not make
changing the live convention an advance prerequisite for unrelated work.
Keep D-24's disconnected typing within normal composer work. D-34's
deadline/check/cancel/retry contract requires explicit shared-chat delivery
work and network-fault coverage before the web sender can claim safe retry.
Resolve Q-3's writer ownership, settlement and attempt-fencing contract before
defining those slices. Do not defer it as a low-priority local-network edge
case or add an offline send queue.
Include D-36/D-45's writer cutoff, logical ID in authoritative IRC history
and complete history lookup in that contract. Resolve P-37's settlement
proof without an IRC patch. Do not add browser pending-send storage or
reload restoration.
Include D-46's bounded room-level recovery notice with history loading.
Exclude D-30's optional current-task line and setting from all milestone-1
slices; retain only the milestone-2 note.
The first frontend slice includes D-26/D-27's Svelte/Vite setup, exact
dependency pins and web CI job feeding `build-test`. Keep implementation
in normal isolated code worktrees; this document does not install tools.
Include D-16's design-agent use of general in that change, with no dedicated
design-room slice or conversation migration. General's live provisioning
remains the owner's action.
Include D-12/D-44's clean cutover as an explicit per-project rollout step:
manager warning, owner closure of non-exempt tracker work, normal legacy
archive, then project switch and issue/PR work. Include marked parked-room
exceptions and isolation of projects not yet switched. The manager owns
the concrete rollout details; this is not postponed to milestone 2's archive
browser and is not a gradual migration of every active room.
Discard existing design rooms during that rollout without saved logs.

Image sequencing from D-17: first deliver pasted-image insertion as a
placeholder link in the message; then deliver inline image display,
including in the message being composed. Both are milestone 1 work. Record
their final issue boundaries and dependencies once the remaining upload
contract is settled; D-25/D-26 have settled the page framework and editor.
Include image preservation in the archive contract from the first image
slice; placeholder links do not defer D-20's storage obligations.
Apply D-21's upload-time cap in that first slice too; inline rendering is
the later presentation change.

The owner explicitly
approves readiness before issue processing, and each tracker artifact gets
separate signoff. The project manager runs filed-issue reviews.
