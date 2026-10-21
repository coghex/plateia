# Working in plateia

Plateia is the owner's chat room and dashboard for running projects with AI
agents. It's a web page served from the owner's machine and reached only over
Tailscale. It shows the project chat (a local Ergo IRC server), each project's
manager agent and its work, and the health of the services behind them. This
file is the single authority on **how to work** here, for every agent.
`CLAUDE.md` imports it; don't keep rules anywhere else.

What to build and why lives in [docs/vision.md](docs/vision.md), the owner's
direction (principles V-1 … V-13). `$guide` / `/guide` reviews work against
it. Anything the vision leaves open (scope, the technology stack, anything
user-visible) is the owner's call: ask. If code and the vision disagree, stop
and ask the owner. Never change the vision to fit code.

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

## Workflow

The GitHub-project rules in the owner's global instructions apply. Specific to
this repository: the default branch is `master`, and the drainer needs
`reviewed:approve` and the `review-approved` gate
(`.github/workflows/review-gate.yml`), plus `build-test` once CI exists. One
issue per pull request unless the owner groups them.

Shared-skills issues are the exception: those whose spec names the local
skills repository as the implementation owner. They are delivered without a
pull request, under `docs/chat_design.md` D-75:

- an isolated checkout and branch of the local skills repository;
- opposite-brand review of the exact commit;
- recorded local test results;
- a merge into the canonical checkout only with the owner's or a level-2
  agent's authorization.

The issue links the reviewed commit and its test evidence as completion.
Never copy shared chat code into this repository or give the skills repository
a remote for it. Everything else follows the pull-request rules above (owner
decision 2026-10-21).
