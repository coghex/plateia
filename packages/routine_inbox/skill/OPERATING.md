# Operating contract

This package is a small independently runnable shared tool. It does not choose
the dashboard framework, introduce card-control state, move the existing IRC
boundary, or start/stop any service. The example configuration uses invented
identities. Real routing manifests, messages, checkpoints, decisions and logs
belong outside this public repository; there are no credential fields.

Routine manager clarifications go to an assistant that answers only what existing
approved evidence settles and escalates everything else through the manager
(owner decision 2026-10-02). This package implements that routing decision within
the independently runnable shared tooling described by vision V-13.

## Installation gate

The package and the existing chat adapter must be installed through the source/
update/install lane approved for the shared skills. Staging is not deployment.
`python3 -m routine_inbox.compat --installed <chat-scripts> --output <new-staging-dir>
--package-root <reviewed-package-parent>`
prepares three adapter files and refuses source drift. It never changes the originals.
The adapter loads its explicitly selected package parent without a global Python
or shell environment change. Do not deploy only one
side or restart a live bridge until the complete installation is approved,
backed up and tested. Keep the existing service and credentials in place.

Run offline tests with `PYTHONPATH=packages python3 -B -m unittest discover -s
packages/routine_inbox/tests`. The staged adapter should also pass the installed
bridge recovery tests using temporary state and fake IRC/cmux. No test needs a
real post or owner push.

## Explicit recipients and compatibility

New manager question:

```sh
pchat post '#alpha-job-17' --as a-manager --type question --re alpha-job-17 \
  --to helper 'Which approved default applies to this field?'
```

`--to` is a destination, not an authenticated account or new credential.
An optional `--message-key` is a stable logical message ID; otherwise pchat generates
one. IRC tags carry recipient, logical key, part number and total count. The bridge
retains these as `routing` on every log record; the same metadata survives outbox
retry. The collector waits for all parts and rejects conflicting content.

Old unaddressed posts still wake managers as before. Old direct `@recipient` or
`recipient:` syntax remains supported **only at the beginning**, after optional
message tags and a standard nametag. Mentions in quotes, backticks, transcripts,
prose, or continuation fragments no longer trigger routing. This intentionally
changes the old scan-anywhere behavior: use `--to` for reliable addressing, including
owner escalations. Only part one of a structured owner-addressed message queues
a phone push. Existing authentication, notification destination and subscriptions
are unchanged.

The bridge's existing transport retries are not exactly-once delivery. The inbox
adds conservative action intent and correlation, not a claim that IRC or phone
pushes have acquired a distributed exactly-once guarantee.

## Private setup and periodic command

Create a private manifest from `routing.json.example`, verifying the existing
assistant identity, five project channels/prefixes and manager identities against
authenticated logs and manager metadata. It must not contain passwords, tokens or
the notification URL. Create state in a writable, persistent private directory.

After the approved adapter is available, initialize once:

```sh
<package>/run --config <private-manifest> --state <private-state> initialize
```

Initialization records the current complete-record end of every known channel;
it does not replay old questions. The manifest is pinned to prevent later silent
identity/source changes. A replaced/truncated log, malformed record, conflicting ID,
or missing acceptance log is retained or reported as a blocker, never interpreted
as an empty successful inbox.

One periodic entry point:

```sh
<package>/run --config <private-manifest> --state <private-state> poll --limit 5
```

Read the adjacent `SKILL.md` to handle its result. Use one scheduled task, normally
every ten minutes, with no overlapping run. Scheduling is outside this package.
Collection reads at most 2 MiB of newly appended log data in a call. Per-channel
append cursors allow late/out-of-order replay after activation; stable message and
batch IDs prevent duplicate enrollment. Partial final records are retried next time.
The checkpoint advances independently of unresolved questions.

Before enabling replies, use `check-managers` with the same manifest/state to
verify read-only access to each registered manager surface. The send path repeats
that check immediately before posting. A denied or stale surface stops the action
without changing permissions or trying another transport. A successful interactive
approval does not by itself prove an unattended run will have the same access.

`claim`, `prepare`, and `send` are separate so an answer can be inspected before
transmission. Claims expire after ten minutes; action intent never expires into an
automatic retry. The send path rechecks logs for a resolution before attempting
once. A successful CLI exit only establishes submission. Complete correlated
outgoing fragments plus the exact manager's reply/ack establish acceptance.

Sending requires `send <id> --claim <token>`. An expired or superseded claimant
cannot send or record a result. If an unsent prepared answer's claim expires, a
new claimant must reconsider the evidence and prepare again; its existing action
key is retained. Once a send starts, expiry leaves its outcome uncertain and can
never return it to an automatic send.

For an escalation, the manager should post the later owner resolution using `--to`
the helper destination, the same request ID and `--reply-to` the original question's
part-one server message ID. All resolution fragments must arrive before it closes.
This closes `waiting_owner` without the assistant guessing that silence or an
unrelated manager post settled it.

## Manager and worker guidance to install together

- Workers send questions/blockers in the request channel to their manager.
- Managers answer from settled evidence themselves when possible. Remaining
  routine clarification uses the helper destination with explicit metadata.
- Serious failures and owner-only decisions use the owner destination explicitly;
  do not wait for the routine inbox.
- Managers treat helper answers as clarification under the stated original scope,
  despite the assistant account's broader standing authority. They accept every
  reply fragment explicitly and verify any worker continuation separately.
- An unresolved decision is escalated once and remains recorded until answered.
