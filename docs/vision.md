# Plateia vision and design guardrails

**Status: accepted by the owner on 2026-10-02.** This is the heading that
`$guide` reviews against.

Plateia is the owner's workplace for running software projects with AI
agents: a chat server in the spirit of Discord, plus a board of issue and
pull-request cards, served from the owner's machine to a phone or desktop
browser. It succeeds the owner's `kanban` project as the way work is directed
(owner decision 2026-10-02). `kanban` remains as the home of the workflow
skills and as a command-line board; new workflow features go into plateia and
the shared services beneath it (V-13).

It exists because the work now happens in many agent sessions at once (a
manager per project plus its workers), and the owner needs one place to see
all of it, talk to any of it, and steer it, from anywhere.

This document holds the direction. Concrete decisions (protocols, storage,
layouts, exact mechanisms) belong in a design document. When the two
disagree, raise it with the owner; don't quietly pick one. New explicit owner
decisions can revise this document: record the decision and its date beside
the change. Keep these principle IDs; retire a principle rather than
renumbering.

## Principles

### V-1. One owner's workplace, not a product

Plateia serves one person: the owner (owner decision 2026-10-02). There are no
other users, no accounts and no sharing. It is general only where the owner
needs that: adding a new project, and setting up a new machine with the
owner's whole workflow, should each take one script and a few minutes.

### V-2. Each project is a server, and its rooms are hashtags

Each project appears as its own server, and each server's rooms are its chat
channels: the project's main room plus one room per request (owner decision
2026-10-02). A room shows the conversation as it happens, including every
manager and worker post, and the owner can post in any room. Rooms for
finished requests fold into an archive and are never deleted: the chat is the
project's record, and the whole archive is searchable.

### V-3. IRC is the backbone, behind one boundary

The existing local IRC server stays the transport and the record (owner
decision 2026-10-02). Plateia is a client of it with a web layer on top.
Everything only a web page can do is built over IRC rather than beside it:

- reactions use IRC's message tags; the server is configured to store them
  and replays them with history (verified 2026-10-02);
- images are stored by plateia, posted as links and shown inline;
- presence is worked out from the real agent sessions, not IRC logins.

All IRC-specific code sits behind one boundary, so replacing IRC later would
mean swapping that layer without touching agents or the interface.

### V-4. Agents are people in the room

Every agent session working on a project appears as an online user in that
project's rooms (owner decision 2026-10-02): the manager, its workers, and
sessions the owner opened by hand. Each has a stable handle generated when the
session first appears, made from the project, its role and a number (for
example `alpha-solver-12`), and a live status: what it is working on, idle, or
waiting on the owner. A session that ends leaves the room.

### V-5. Chat is the only way plateia acts

Everything plateia does to the system is a chat message posted as the owner
(owner decision 2026-10-02). Moving a card, making a request and messaging an
agent all become posts. Plateia never runs skills, edits the tracker or
drives a terminal itself, so the chat stays the complete record, and
authority works exactly as it does when the owner types in a terminal. The
agents do the work.

### V-6. Any agent can be addressed directly

A plain post in a room goes to the project's manager, which decides what to
dispatch. A post that @-mentions an agent's handle goes straight to that
session (owner decision 2026-10-02). For example, `@alpha-solver-12
/autosolve 41` runs that command in that session. Selecting a user in the room
starts such a message.

A direct message follows the same contract as a request to a manager:

- **Queued, delivered, accepted.** A message to a busy agent is queued, so
  the agent reads it at its next opportunity. It is never typed into a prompt
  that holds a half-written message. The agent accepts it the way a manager
  accepts a request, and the room shows which of the three states each
  message has reached.
- **Same claims, same gates.** Work started this way takes the same claims
  and passes the same reviews as any other, so a manager can see it and never
  dispatches the same task twice. It needs no manager approval; the manager
  learns of it from the record and from the board.
- **No silent loss.** If the session ends while a message to it is still
  queued, the message is marked undelivered in its room and the owner is
  notified (V-9).

### V-7. The board directs work

Each project has a board of cards. An issue and its pull request share one
card, and an instruction applies to both; a pull request with no issue has
its own card. Cards are sorted into baskets (owner decision 2026-10-02):

| Basket | Meaning |
| --- | --- |
| Inbox | new or unsorted; no work wanted |
| Approve | review this issue |
| Solve | solve this issue into a pull request |
| In review | pull request open, review loop running |
| Merge | approved; the drainer merges it |
| Hold | keep the work, pause it |
| Done | finished: the issue is closed and post-merge cleanup is complete |

The owner moves cards into **Approve**, **Solve**, **Hold** or back to
**Inbox**. Each move posts a request to the manager (V-5). The other baskets
are status: cards move through them as the work progresses.

- **Hold pauses and keeps.** Work on a held card stops at the agent's next
  safe opportunity, without interrupting it mid-step. Its branch, worktree,
  pull request and approvals are kept, so moving it back resumes where it
  left off.
- **Back to Inbox cancels and cleans up.** The work stops the same way, then
  any pull request is closed with a comment, the branch and worktree are
  deleted, any approval is withdrawn, and the issue is released. Closing a
  pull request this way leaves its still-open issue in Inbox; it never counts
  as done.
- **Done follows the tracker.** A card is done when its issue is closed and
  the post-merge cleanup (branch, worktree, claims) has finished; where
  cleanup can't be tracked reliably, a closed issue is enough (owner decision
  2026-10-02). A reopened issue returns to the active board. Merged work
  can't be undone by moving a card.
- **The manager decides how and when.** It keeps its own triage of the
  project's issues and handles pull requests in number order. The order of
  cards on the board is the owner's view only and never an instruction.
- **The latest move wins.** If a card moves several times before the manager
  acts, the manager acts on where it ended up.
- **Races are expected.** Pausing or cancelling moving work is best effort,
  and the board shows the outcome (V-8) rather than pretending it succeeded.

### V-8. The board shows what was asked and what is happening

A card always shows two things: where the owner asked it to be, and what is
actually happening. The second comes from the project's real state: the
tracker, the pipeline's labels and checks, the agent sessions and the chat
record (owner decision 2026-10-02).

- A card the owner moved is highlighted as a pending request until the work
  matches it. An agent still finishing a step after a Hold, or still cleaning
  up after a cancel, stays visibly active on the card.
- Real progress counts whoever drove it, so a fix the owner ran by hand moves
  its card as surely as one the manager ran.
- A card that progresses moves to the top of its new basket. If reality
  overtakes an instruction, for example a pull request merged before its Hold
  took effect, the card follows reality and shows that the instruction was
  overtaken.
- The owner can arrange cards freely, and plateia remembers that order.
- Every card links to its issue and pull request on GitHub.

The board's own state lives in plateia's storage, not in tracker labels.
Plateia reads the tracker politely, without flooding GitHub with requests.

### V-9. Notify only when needed

Plateia sends browser push notifications, replacing the earlier ntfy pushes
(owner decision 2026-10-02). It sends one only when:

- the owner's input is needed; or
- something has gone badly wrong:
  - the chat server, the chat bridge or a manager is down;
  - a request or direct message couldn't be delivered or was never accepted;
  - the default branch is failing its checks;
  - the drainer has stopped with an incident;
- a manager judges that the owner must know.

Tapping a notification opens the room or card it is about. Nothing routine
notifies.

### V-10. Phone and desktop alike

Every feature works in a phone browser and a desktop browser (owner decision
2026-10-02). Neither is a reduced version of the other. On the iPhone,
plateia is added to the Home Screen as a web app, because iOS delivers web
push notifications only to installed web apps; setting up a phone includes
that step.

### V-11. Private by network, public in code

Plateia listens only on the owner's machine and is reached only over the
owner's private Tailscale network. The code is public, but the owner's
credentials and data never enter the repository, its issues or its pull
requests (see [AGENTS.md](../AGENTS.md)).

### V-12. Trustworthy before light

Being trustworthy matters more than being small, so plateia is built on
mature, fully featured, open-source tools (owner decision 2026-10-02). The
server is Python, like the rest of the owner's tooling. The page framework is
chosen by building the same small prototype in several candidates and
comparing them (owner decision pending). What the owner can rely on:

- requests plateia has acknowledged survive restarts;
- interrupted connections recover on their own;
- stale or unknown state is shown as stale or unknown, never as current;
- retrying a request never starts duplicate work;
- failures and incomplete actions stay visible until they are resolved.

### V-13. Plateia is the face; the workflow lives beneath it

Plateia owns browser interaction and presentation (from the first review of
this draft, 2026-10-02). The workflow itself lives in shared
services and skills that work without plateia and serve every interface,
including the command line and `kanban`'s board:

- agent handles and presence;
- delivering, queueing and accepting messages;
- running the work.

Addressing an agent, delivering a message and executing work all keep
functioning when plateia is down. When plateia needs new workflow behavior,
it is added to those shared pieces, not built into the web application.

## Mid-term scope

Plateia's first full version includes:

- **Chat:**
  - one server per project, with its rooms and live chat;
  - search across the whole archive;
  - agent presence with status;
  - direct messages to agents;
  - issue and PR references (`#41`, `PR #42`) shown as live mini-cards
    colored like the board;
  - unread counts, mention badges and a "needs you" list;
  - reactions;
  - pasted images.
- **The board,** with its baskets, colors, Hold and cancel.
- **Push notifications,** including installing plateia on the phone.
- **A setup script** for new projects and new machines.

These are **out of scope** for now:

- longer-lived goals tracked in plateia (requests are the unit of work);
- other users;
- custom agent names;
- interrupting a busy agent mid-step;
- a native phone app;
- live terminal views;
- replacing IRC;
- creating projects from the interface.

## Long-term direction

These ideas may come later. They shape today's choices only by what must not
be ruled out:

- custom names for agents;
- interrupting a busy agent;
- adding a project from the interface instead of a script;
- live views of agent terminals;
- plateia owning the chat protocol itself, if IRC ever holds it back (V-3
  keeps that a one-layer change);
- taking over more of `kanban`'s role as the workflow skills evolve.

## Continuing after a context reset

Read this document, then the design document once one exists. Run `$guide`
after a meaningful batch of work. Update this document only for accepted
changes in intent. When no settled next step exists, discuss the next unmet
part of the mid-term scope before designing more work.
