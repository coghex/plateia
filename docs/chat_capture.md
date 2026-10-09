# Shared chat capture: migration map and checks

Plateia carries reviewed source for the owner's shared chat code. Until the
first activation (`docs/plateia_design.md` D-23), what runs is still the
local skills repository (`~/.codex/skills`, under `chat/`). This page maps
that baseline to plateia's tree. It also says how to run the captured tests
and the drift check. The machine-read record is
[`packages/chat/provenance.json`](../packages/chat/provenance.json): baseline
commit, per-file hashes, and every change with its reason.

## Slices

| Slice | Issue | Captures |
| --- | --- | --- |
| PLT-9 | #11 | The identity and transport core: `chatlib`, `identities`, `binding`, `runstore`, `role_colors`, `pchat`, `agentcli`, `receipt_experiment` (D-69), the test-isolation guard and their tests |
| PLT-15 | #12 | The bridge: `chat-bridge`, `rotate-logs`, their tests, the bridge-dependent `test_receipt_experiment.py`, and `chat/SKILL.md` (D-24) |

Both slices capture from baseline `c572bad`
(`c572bad60b06f262d11a7175f52fd62f23257241`), committed on the skills
repository's `master` (D-19). Each slice starts with a drift check. Any newer
skills commit that touches a captured file is carried in and recorded in
`carried_commits`, and each captured file's `source_commit` names the commit
its content was taken from. When PLT-9 was captured on 2026-10-08, there was
none. When PLT-15 was captured the same day, there was one: `8ff325e`
(`8ff325ee307b92e53157594e570235bc9f2f5229`) adds the "Timing reservations"
paragraph to `chat/SKILL.md`, so that file comes from `8ff325e` and every
other file from `c572bad`.

## Migration map (PLT-9)

The capture mirrors the baseline layout: `chat/scripts/<file>` becomes
`packages/chat/scripts/<file>`. The modules and tests find each other through
paths relative to their own location, so nothing needed rewiring.

| Baseline path | Plateia path | Capture |
| --- | --- | --- |
| `chat/scripts/agentcli.py` | `packages/chat/scripts/agentcli.py` | changed: `who`'s hint names the config's assistant identity; a notice that failed partway queues only its unconfirmed remainder (#19, D-72) |
| `chat/scripts/binding.py` | `packages/chat/scripts/binding.py` | changed: one docstring path example |
| `chat/scripts/chatlib.py` | `packages/chat/scripts/chatlib.py` | changed: docstring config example uses placeholders; posts go part by part, each confirmed, and a failure reports every part's state (#19, D-72) |
| `chat/scripts/identities.py` | `packages/chat/scripts/identities.py` | byte-for-byte |
| `chat/scripts/pchat` | `packages/chat/scripts/pchat` | changed: a post that failed partway queues only its unconfirmed remainder; `status` shows posts awaiting a delivery check and partly published dead letters (#19, D-72) |
| `chat/scripts/receipt_experiment.py` | `packages/chat/scripts/receipt_experiment.py` | byte-for-byte (first review in PLT-9, D-69) |
| `chat/scripts/role_colors.py` | `packages/chat/scripts/role_colors.py` | changed: owner, assistant and other identity names read from the chat config at runtime; stale forced colors dropped |
| `chat/scripts/runstore.py` | `packages/chat/scripts/runstore.py` | byte-for-byte |
| `chat/scripts/tests/_isolation.py` | `packages/chat/scripts/tests/_isolation.py` | byte-for-byte |
| `chat/scripts/tests/test_binding.py` | `packages/chat/scripts/tests/test_binding.py` | changed: invented names and paths |
| `chat/scripts/tests/test_identities.py` | `packages/chat/scripts/tests/test_identities.py` | changed: invented names; the colors test supplies its config |
| `chat/scripts/tests/test_silence.py` | `packages/chat/scripts/tests/test_silence.py` | changed: invented names |

Fixtures use invented data. Projects `alpha`, `beta`, `gamma` and `delta`
have agent prefixes `alp` and `bet`, the owner is `pat` and the assistant is
`sam`. Two fixed names stay in the code because they aren't private data:

- `chatbridge`, the bridge's fixed IRC account, which `chatlib` invites into
  channels;
- the kanban tool's state directory, which the isolation guard protects.

Plateia-only files, none of them in the baseline:

- `packages/chat/provenance.json`, `packages/chat/drift_check.py`;
- `packages/chat/tests/test_provenance.py` holds the captured files to the
  record, and `packages/chat/tests/test_drift_check.py` tests the drift check
  on invented trees;
- `packages/chat/scripts/tests/test_start_kind.py` is the standalone
  `start_kind()` test (D-69);
- `test_role_colors.py` covers runtime names, renamed identities losing
  their forced colors, the palette and prefix-only recoloring;
- `test_authority.py` covers cited delegation;
- `test_isolation_guard.py` shows the guard failing closed.

## Migration map (PLT-15)

`chat/SKILL.md` becomes `packages/chat/SKILL.md`, beside `scripts/`, as in the
baseline.

| Baseline path | Plateia path | Source | Capture |
| --- | --- | --- | --- |
| `chat/scripts/chat-bridge` | `packages/chat/scripts/chat-bridge` | `c572bad` | changed: message-ID deduplication covers the whole channel log, and the outbox resends only unconfirmed parts after checking the record (deliberate deviations, below) |
| `chat/scripts/rotate-logs` | `packages/chat/scripts/rotate-logs` | `c572bad` | byte-for-byte |
| `chat/scripts/tests/test_bridge.py` | `packages/chat/scripts/tests/test_bridge.py` | `c572bad` | changed: invented names |
| `chat/scripts/tests/test_receipt_experiment.py` | `packages/chat/scripts/tests/test_receipt_experiment.py` | `c572bad` | changed: invented names (first review of the bridge's receipt routing, D-69) |
| `chat/scripts/tests/test_rotate_logs.py` | `packages/chat/scripts/tests/test_rotate_logs.py` | `c572bad` | byte-for-byte |
| `chat/scripts/tests/fixtures/codex-interrupted-background.txt` | `packages/chat/scripts/tests/fixtures/codex-interrupted-background.txt` | `c572bad` | byte-for-byte |
| `chat/SKILL.md` | `packages/chat/SKILL.md` | `8ff325e` | changed: says where the owner's and assistants' accounts are configured; invented examples; describes partial-post queueing (#19) |

The bridge tests use the owner `pat` and assistant `sam`, with projects
`nova`, `alpha`, `gamma`, `delta`, `epsilon` and `zeta` (prefixes `nov`,
`alp`, `gam`, `del`, `eps`, `zet`). They use `nova`/`nov` where the core
tests use `beta`/`bet`. The bridge sorts the accounts it wakes, and one routing
test's expected order needs the prefix to sort after `manager`.

`chat-bridge` deviates from `c572bad` in one line, by owner decision on
2026-10-08, relayed on PR #16's first review. The baseline built the bridge's
message-ID deduplication set from only the newest 5,000 lines of a channel
log. A catch-up cut short leaves the checkpoint behind, so after a restart a
catch-up longer than that replays older messages: they were logged a second
time, and a request already accepted was queued again. The captured bridge
reads every line. `test_replay_dedup.py` reproduces the failure with six full
1,000-message pages, a dropped connection and a restart. The running bridge
keeps the baseline behavior until activation (D-23).

A second deliberate deviation, by owner decision on 2026-10-09 (D-72), repairs
partial-post delivery (#19). Before it, `chatlib.post` waited once, after the
whole post, for the server's answer. A long post whose completion timed out
after the server had committed it was then queued whole, and every outbox flush
posted it again under new message IDs.

Now each part is confirmed before the next is written. `pchat`, agent notices
and the bridge's announcements queue only the unconfirmed remainder. The
bridge checks a part that may already be in the channel against the channel
record before any resend, and the guarantee stays at-least-once with that
check. `test_outbox_resend.py` reproduces the reposting with a fake server and
covers recovery. It changes `chatlib.py`, `pchat`, `agentcli.py`, `chat-bridge`
and `SKILL.md`. The running tools keep the baseline behavior until activation
(D-23).

`SKILL.md` keeps three things that point outside the capture, recorded in its
provenance entry's `external_references`:

- the timing-reservation link,
  `../project-manager/references/timing-reservation.md`, verbatim from
  `8ff325e`;
- the paragraph on `chat/scripts/install-identities` and its options;
- the other skills and tools it names (`project-manager`, `modelclass`,
  `docs-push`).

Plateia-only files added with PLT-15:

- `test_skill_guidance.py` reads `pchat`'s own argument parsers and checks
  that every `pchat` subcommand and option the guidance mentions exists, and
  that its list of post types is `pchat`'s;
- `test_replay_dedup.py` shows message-ID deduplication after acceptance
  recovery, across a crash, a restart, and a restart after a cut-short
  catch-up of more than 5,000 messages.

Plateia-only file added with #19:

- `test_outbox_resend.py` covers partial-post delivery against a fake server
  with an injected clock. It tests per-part confirmation, remainder-only
  queueing, record reconciliation, crash and restart recovery, independent
  delivery, refusals and the one-day dead letter.

Not captured, by design: `install-identities` is excluded from every capture
(D-20).

## Running the tests

From the repository root, as `build-test` runs them on Python 3.10 and 3.14:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s packages/chat/scripts/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s packages/chat/tests -v
```

The first command is the captured suite plus plateia's tests of captured
behavior. CI also requires, by name, that `test_binding`, `test_identities`,
`test_silence`, `test_start_kind`, `test_bridge`, `test_receipt_experiment`
and `test_rotate_logs` each ran. The second is the capture tooling. Neither needs an IRC server,
cmux, the network or the real home directory. `_isolation` gives each run a
throwaway home and refuses any access to live state.

Run them with `XDG_CONFIG_HOME` and `XDG_STATE_HOME` unset (`env -u
XDG_CONFIG_HOME -u XDG_STATE_HOME ...`, as CI does) when either is exported
under the real home. Otherwise a test that restores the environment trips
the guard, which refuses to write a live path back, and the restore stops
partway.

## Drift check

```sh
python3 -B packages/chat/drift_check.py ~/.codex/skills          # or --json
```

It compares the skills repository's working tree under `chat/`, minus the
listed exclusions, against the recorded baseline. It reports changed,
missing, added and type- or mode-changed files, and changed symlink targets,
without following links. Paths are relative to the tree. It never writes and
never fixes anything.

Exit status: `0` means no drift, `1` means drift, `2` means the check could
not finish. An unreadable input or a missing tree is reported as an error,
never as "no drift".

A file that matches the commit carried in for it is still reported, as
`changed (matches carried commit <commit>)`.

At PLT-9's capture (2026-10-08) the live tree reported no drift. At PLT-15's
capture (2026-10-08) it reported one difference, `chat/SKILL.md`, changed
(matches carried commit `8ff325e`), and nothing else.
