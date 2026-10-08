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
`carried_commits`. When PLT-9 was captured on 2026-10-08, there was none.

## Migration map (PLT-9)

The capture mirrors the baseline layout: `chat/scripts/<file>` becomes
`packages/chat/scripts/<file>`. The modules and tests find each other through
paths relative to their own location, so nothing needed rewiring.

| Baseline path | Plateia path | Capture |
| --- | --- | --- |
| `chat/scripts/agentcli.py` | `packages/chat/scripts/agentcli.py` | changed: `who`'s hint names the config's assistant identity |
| `chat/scripts/binding.py` | `packages/chat/scripts/binding.py` | changed: one docstring path example |
| `chat/scripts/chatlib.py` | `packages/chat/scripts/chatlib.py` | changed: docstring config example uses placeholders |
| `chat/scripts/identities.py` | `packages/chat/scripts/identities.py` | byte-for-byte |
| `chat/scripts/pchat` | `packages/chat/scripts/pchat` | byte-for-byte |
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

Not captured, by design: `chat-bridge`, `rotate-logs`, `tests/test_bridge.py`,
`tests/test_receipt_experiment.py`, `tests/test_rotate_logs.py` and
`tests/fixtures/` arrive in PLT-15. `install-identities` is excluded from every
capture (D-20).

## Running the tests

From the repository root, as `build-test` runs them on Python 3.10 and 3.14:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s packages/chat/scripts/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s packages/chat/tests -v
```

The first command is the captured suite plus plateia's tests of captured
behavior. The second is the capture tooling. Neither needs an IRC server,
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

It compares the skills repository's working tree under `chat/scripts/`, minus
the listed exclusions, against the recorded baseline. It reports changed,
missing, added and type- or mode-changed files, and changed symlink targets,
without following links. Paths are relative to the tree. It never writes and
never fixes anything.

Exit status: `0` means no drift, `1` means drift, `2` means the check could
not finish. An unreadable input or a missing tree is reported as an error,
never as "no drift".

At PLT-9's capture (2026-10-08) the live tree reported no drift.
