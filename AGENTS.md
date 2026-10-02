# Working in plateia

Plateia is the owner's chat room and dashboard for running projects with AI
agents. It's a web page served from the owner's machine and reached only over
Tailscale. It shows the project chat (a local Ergo IRC server), each project's
manager agent and its work, and the health of the services behind them. This
file is the single authority on **how to work** here, for every agent.
`CLAUDE.md` imports it; don't keep rules anywhere else.

What to build and why lives in [docs/vision.md](docs/vision.md), the owner's
direction. **It hasn't been written yet.** Until it exists, ask the owner
before deciding scope, direction, the technology stack or anything
user-visible. If code and the vision disagree, stop and ask the owner. Never
change the vision to fit code.

## This repository is public; the owner's data is not

Everything here is public: code, commits, issues, pull request descriptions,
review comments and CI logs. The system plateia displays is private. Keep the
two apart:

- **Never commit or post credentials:** passwords, tokens, keys, socket
  passwords, or anything from `~/.config/chat/`, `~/.config/cmux/` or
  similar.
- **Never commit or post real data:**
  - chat messages, manager handoffs or decision logs;
  - real account names other than the owner's public GitHub handle;
  - Tailscale machine or network names, IP addresses;
  - personal filesystem paths (write `~/...`, not a home directory with a
    name in it);
  - screenshots or recordings of the live dashboard.
- **Use made-up data** for tests, fixtures, examples and docs: placeholder
  projects, people and messages. Describe a bug with an invented example
  rather than a pasted log.
- **Configuration and state live outside the repository** and are read at
  runtime. The repository ships only examples (`*.example`). Local files
  that hold real values are listed in `.gitignore`.
- If something private does get pushed, tell the owner at once. Don't try to
  rewrite public history quietly.

## Starting principles

These hold until the vision says otherwise:

- **Private by network.** Listen only on localhost and behind
  `tailscale serve`. Never bind a public interface.
- **Act through chat.** Plateia asks managers for things by posting in chat,
  as the owner. That keeps the chat the complete record, and authority works
  exactly as it does elsewhere. It doesn't type into agent sessions or drive
  cmux directly.
- **Read the system, don't own it.** The chat server, the chat bridge and the
  managers run without plateia. If plateia is down, nothing else breaks.

## Owner authority

- The owner decides direction, design and acceptance. Agents recommend.
  Record a new owner decision with its date in the document that owns the
  topic.
- The owner's assistants carry the owner's authority, as the project's
  manager policy defines.
- Authorization carries across turns within a conversation. It doesn't carry
  to a different kind of action; ask before anything outward-facing that wasn't
  requested.

## Workflow

- The default branch is `master`. Every change lands through a pull request
  that passes the `review-approved` gate (`.github/workflows/review-gate.yml`)
  and, once CI exists, `build-test`. The kanban pipeline's drainer merges
  approved pull requests.
- Documentation that belongs to a code change goes in the same pull request.
- Keep pull requests focused: one issue per pull request unless the owner
  groups them.
