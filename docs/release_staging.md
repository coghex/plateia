# Release staging: operator contract and crash evidence

`packages/release/stage_release.py` stages a release that
`build_release.py` built (PLT-11, [release_contract.md](release_contract.md))
without changing the live chat tools. This is PLT-12 (#14) under D-26: plan,
stage and preflight only. Selecting, activating and rolling back a release
are PLT-14's.

**Staging has never been run on the owner's machine.** Everything below was
exercised on invented homes, provenance, state and service definitions, with
fake service and identity commands. Running it for real needs its own owner
authorization.

## Commands

```sh
python3 packages/release/stage_release.py plan --release <release dir> [--dest <root>] [--json] [--out <plan.json>]
python3 packages/release/stage_release.py preflight --release <release dir> [--python <interpreter>] [--json]
python3 packages/release/stage_release.py stage --release <release dir> --dest <root> [--python <interpreter>] [--plan <plan.json>]
python3 packages/release/stage_release.py status --dest <root>
```

- `plan`, `preflight` and `status` never write anything. (`plan --out` writes
  only the plan file.)
- `stage` runs the plan and the preflight first, and writes nothing if either
  blocks.
- **Exit status:** 0 success, 1 refused or blocked, 2 bad usage.
- Standard library only. The environment is the interpreter's own `venv`
  with its bundled pip, installing offline (`--no-index`).

## Plan

### The managed targets (D-26)

Each target, and what it is expected to be, is declared in
`packages/release/staging.json`:

| Target | Expected |
| --- | --- |
| `~/.local/bin/pchat` | a symlink to the skills tree's `chat/scripts/pchat` |
| `~/Library/LaunchAgents/com.coghex.chat-bridge.plist` | Label `com.coghex.chat-bridge`; ProgramArguments naming `chat/scripts/chat-bridge` |
| `~/Library/LaunchAgents/com.coghex.log-rotate.plist` | Label `com.coghex.log-rotate`; ProgramArguments naming `chat/scripts/rotate-logs` |
| `~/.local/share/weechat/python/autoload/role_colors.py` | a symlink to `chat/scripts/role_colors.py` |
| `~/.codex/skills/chat` | a directory holding the captured files |
| `~/.claude/skills/chat` | a symlink to `~/.codex/skills/chat` |
| `~/.codex/skills/chat/scripts` | a directory: the import location |

### What it records for each target

For each target, the plan records:

- its current type, link destination and content hash;
- the evidence of who owns it;
- the proposed replacement. Nothing is replaced: these are PLT-14's.

### Where the expectations come from

Expected contents come from the capture provenance
(`packages/chat/provenance.json`): each captured file's baseline hash, or the
hash from a carried commit. Nothing observed on disk becomes its own baseline.

- **The two folder targets** are checked with #11's drift check. Excluded
  entries (`__pycache__`, `*.pyc`, `.DS_Store`, `install-identities`) are
  skipped. Any other entry the provenance doesn't list is an unknown file.
- **A LaunchAgent** is parsed only for its `Label`, `Program` and
  `ProgramArguments`. Its environment, log paths and other keys are never
  examined, printed or recorded.
- **A LaunchAgent must run the script itself.** launchd executes `Program`
  when it is set, and otherwise the first of `ProgramArguments`. That must be
  either the expected script alone, or a Python interpreter (`python`,
  `python3` or `python3.N`) whose only argument is the expected script.
  Anything else counts as retargeted: a wrapper such as `/bin/echo`, a
  `Program` override, an extra argument, or the script passed as code.

### What blocks staging

A target blocks staging, with a named reason, when it is:

- **unknown ownership:** a wrong type, a different label, an unknown file in
  the folder, or a file that can't be read;
- **changed content:** a file that matches neither the baseline nor a carried
  commit;
- **retargeted:** a link or program path pointing elsewhere;
- **missing.**

Staging never forces past a block, deletes a modified file or applies a patch.

### Path-bound importers of `chat/scripts`

The plan searches two declared roots: the skills tree (`~/.codex/skills`) and
`~/.claude/skills`.

- **Known importers.** The plan reports whether each of the five is found:
  - `project-manager/scripts/launch-worker`, `childrun`, `report` and
    `reconcile`;
  - `model-classes/scripts/modelclass`.
- **Unlisted importers.** Any other file that both changes the import path
  (`sys.path` or `PYTHONPATH`) and names `chat/scripts` is listed by name as
  unlisted. It would otherwise stay on the old code.
- **What counts as changing the import path:** assigning, extending or
  inserting into `sys.path` (including `sys.path = [...] + sys.path`),
  `site.addsitedir`, or `PYTHONPATH`.
- **Which files are searched.** Only scripts are opened: a `.py`, `.sh`,
  `.bash` or `.zsh` suffix, or the executable bit. Data files are never
  opened.
- **What is skipped.** Links aren't followed. `.git`, `__pycache__`,
  `node_modules` and the chat folder itself are skipped.
- **Incomplete inventory.** A root or script that can't be read marks the
  inventory incomplete instead of clean.
- **Not a block.** Unlisted importers and an incomplete inventory don't block
  staging, since staging replaces nothing. They are recorded in the plan and
  the journal for PLT-14.

### Privacy

The plan reads no configuration, credentials, messages or chat state, and
prints none. Paths under the home directory are printed as `~/...`.

## Preflight

Preflight refuses a release, naming the check, in these cases:

- **The manifest doesn't verify.** `build_release.verify` catches a missing,
  extra or tampered artifact, or a missing source commit or interpreter
  requirement.
- **The interpreter doesn't match.** Its implementation, version (against
  `requires_python`) or platform (against `platforms.systems`) differs from
  the manifest. The chosen `--python` reports these itself.
- **A wire or state version is unsupported.**
  - For each format the live tools use (`live_versions` in `staging.json`, the
    D-19 baseline), the release must read the live version and write only that
    version.
  - Each format must be declared, with well-formed read and write versions.
  - A format the release declares that the live tools don't use is refused:
    there is no evidence it's compatible.
- **The existing state holds an embedded version the release doesn't read, or
  that evidence can't be read.** The embedded versions are:
  - the identity registry's `version`;
  - each child run's `state.json` `schema`;
  - receipt-experiment marker names.
- **The chat config is missing or doesn't parse.**
- **An inventory can't be listed.** A child-runs or experiments directory
  that can't be listed is refused as unreadable evidence, never read as an
  empty inventory.
- **The release identity can't name a directory.** The identity must be the
  package's name and version as one plain path component, and artifact
  names must be plain and distinct. An absolute or traversing identity is
  refused before anything is written.

Privacy:

- Preflight reads only those version fields, and checks that the config
  parses. It never prints or records a value, including in refusals.
- It provisions nothing, changes no access and writes no settings.

**API:** until a manifest declares an API version, the API check reports
"not applicable", never "pass" (D-23). A declared API version is refused for
now, because no consumer compatibility matrix exists to check it against.

## Stage

### Layout and destination

`stage` writes only under `--dest <root>`:

```
<root>/journal.jsonl      the journal
<root>/journal.lock       its lock
<root>/releases/<release>/.operation    the owning operation's id
<root>/releases/<release>/artifacts/    the release's manifest and artifacts, verified
<root>/releases/<release>/env/          the private venv with the package installed
<root>/releases/<release>/staged.json   written last: complete, "selected": false
```

- **The guard.** The root, the journal, the lock and the release, environment
  and artifact directories are each checked with `build_release`'s
  protected-location guard. They must be outside this checkout and every live
  location, after resolving symlinks.
- **What the guard covers.** The live locations are the skills tree and all of
  `~/.codex` and `~/.claude`, the chat config and state, `~/.local/bin`,
  LaunchAgents, WeeChat's directories, and every symlink target inside them.
- **Links under the destination are never followed.** Every path staging
  writes, reads back or removes under the root is checked component by
  component right before each step: the journal, lock, marker, artifacts,
  environment, `staged.json` and their temporary files. A symlink anywhere
  among them is refused. Files are opened with `O_NOFOLLOW`, and replaced
  by renaming, so nothing is ever written through a link.
- **Unrelated content is refused, not adopted.** A destination holding
  anything staging didn't create is refused, as is a release directory no
  journaled operation created.

### Order

Nothing is written until all of these pass, in order:

1. The plan is made. With `--plan`, it is compared with the saved plan.
2. The preflight passes.
3. The destination passes the guard.
4. The journal lock is taken. A second run is refused.
5. Every target is checked again, under the lock, immediately before the
   operation records it. A change since the plan blocks.

### The journal

The journal is one JSON record per line, flushed and fsynced. The `begin`
record holds:

- **the operation id**, derived from its binding;
- **the binding:**
  - the release and its manifest's sha256;
  - the chosen interpreter;
  - the destination and release directory;
  - each target's digest;
- **the full manifest;**
- **the rechecked targets, the importer inventory and every compatibility
  result.**

Each step then journals an `intent` before it runs and an `outcome` after:

- `create-release-dir`
- `copy-artifacts`
- `create-environment`
- `install-package`
- `verify-environment`
- `complete`

### Repeats and recovery

- **The same inputs give the same operation id.**
  - **A completed operation** is verified again (artifacts, environment and
    interpreter) and reported `already staged, verified`. Nothing is written.
    If it no longer verifies, it is not reported staged.
  - **An unfinished one** is resumed: a `resume` record, then every step
    again.
- **The steps are safe to repeat.**
  - Stray partial copies are removed and the artifacts recopied and verified.
  - A partial environment, inside the operation's own marked directory, is
    removed and created again.
- **Completion means verified.** `complete` is recorded only after
  `verify-environment` succeeds.
- **The verification checks** that the artifacts match the manifest. The
  environment must load `plateia_chat` from itself at the manifest's version,
  with this checkout off its import path, and its interpreter must be the
  operation's and meet the manifest.
- **The installed payload must match too:**
  - every file the verified wheel hashes is installed with the same bytes;
  - every file the installed `RECORD` hashes, including the generated
    commands, still matches;
  - the package directory holds nothing `RECORD` doesn't list;
  - each command is executable, calls the wheel's entry point, and runs the
    staged interpreter, read from its shebang or from the `/bin/sh` launcher
    pip writes for long paths.
- **Different inputs are a conflict.** While an operation is unfinished, a run
  with different inputs (another interpreter, a changed target, another
  release) is refused, naming the inputs that differ. Repeating the run with
  the operation's own inputs finishes it.
- **A torn last journal line** from a crash mid-write is truncated under the
  lock, durably, before the next record. A complete last record that only
  lacks its newline gets one. The `resume` record notes the bytes dropped.
  `status`, which never writes, ignores a torn last line. Any other
  unreadable line is a refusal.
- **Nothing is ever selected.** `status` reports each operation `complete` or
  `unfinished (last: …)`, always with `selected: no`.

### What staging never does

All external commands go through one recorded boundary. Staging runs only:

- the chosen interpreter, for its probe and `venv`;
- the environment's own pip and interpreter.

Staging never calls `install-identities` or any identity rollout, creates
accounts, starts, restarts or loads a service, or starts the merge worker or
the drainer. It doesn't change configuration, service definitions or
schedules, and it posts nothing to chat.

## Evidence

### Tests

The tests are in `packages/release/tests/test_stage.py`, built on fixtures in
`_staging.py`. CI requires `test_stage` by name, on Python 3.10 and 3.14.

Each test runs against its own invented home holding every managed target:
- the skills tree with its five importers;
- both LaunchAgents and an unmanaged one;
- the WeeChat link, `~/.claude` link and `pchat` link;
- chat config and state with secret-looking values: the outbox, delivery
  records, history, registry, child runs and the receipt marker.

The tests cover:
- **Happy path:** all seven targets and the five importers are planned. The
  staged artifacts and environment verify, and the journal shows the
  operation complete. Every file, link and directory in the invented home is
  byte-identical before and after, compared by hash, mode and link target.
- **Blocking**, each staging nothing:
  - unknown ownership: a foreign label, a file where a link belongs, an
    unknown file in the folder;
  - a changed file;
  - a retargeted link or program;
  - a missing target;
  - a change after a saved plan, and a change between the plan and the
    recheck under the lock;
  - an interpreter with the wrong version, implementation or platform;
  - an unsupported, malformed or unreadable state version, and a missing
    config;
  - an unsupported wire version;
  - an undeclared, malformed or extra format, and no platform systems;
  - a tampered or missing artifact, or an unpinned manifest.
- **API:** "not applicable", never "pass". A declared API version is refused.
- **Importers:** a sixth importer in each root is named, a missing known
  importer is reported, an unreadable root makes the inventory incomplete,
  and data files are never opened.
- **Recovery:** the table below, plus a conflicting retry, a torn journal line
  and a damaged completed release.
- **Concurrency:** a run while the journal lock is held is refused.
- **Destinations:**
  - through a link into a managed target, under `~/.local/bin`, or inside
    this checkout: refused, nothing created;
  - unrelated content, or a release directory without a journal: refused.
- **Privacy:** none of the secret values appears in any command's output or
  in the plan or journal, on both a passing run and a failing preflight.
- **Isolation:** the fake `launchctl`, `systemctl`, `install-identities`,
  `pchat`, `chat-bridge`, `rotate-logs`, merge-worker and drainer commands
  record no call. Every recorded command is the chosen interpreter or the
  staged environment's.

### Crash recovery (invented fixtures, stager at `5de31b0`)

Each row is a fresh invented home. The run was stopped right after the
journal record named in the first column, then run again with the same
inputs. "Home unchanged" means every file, link and directory in the home
compares byte-identical to before the first run.

macOS arm64, CPython 3.14.8:

| Interrupted after | Status after the interruption | On disk | Rerun | Operations | Release dirs | Home unchanged |
| --- | --- | --- | --- | --- | --- | --- |
| begin | unfinished | no release dir | resumed and staged | 1 | 1 | yes |
| intent create-release-dir | unfinished | no release dir | resumed and staged | 1 | 1 | yes |
| outcome create-release-dir | unfinished | .operation | resumed and staged | 1 | 1 | yes |
| intent copy-artifacts | unfinished | .operation | resumed and staged | 1 | 1 | yes |
| outcome copy-artifacts | unfinished | .operation, artifacts | resumed and staged | 1 | 1 | yes |
| intent create-environment | unfinished | .operation, artifacts | resumed and staged | 1 | 1 | yes |
| outcome create-environment | unfinished | .operation, artifacts, env | resumed and staged | 1 | 1 | yes |
| intent install-package | unfinished | .operation, artifacts, env | resumed and staged | 1 | 1 | yes |
| outcome install-package | unfinished | .operation, artifacts, env | resumed and staged | 1 | 1 | yes |
| intent verify-environment | unfinished | .operation, artifacts, env | resumed and staged | 1 | 1 | yes |
| outcome verify-environment | unfinished | .operation, artifacts, env | resumed and staged | 1 | 1 | yes |
| intent complete | unfinished | .operation, artifacts, env | resumed and staged | 1 | 1 | yes |
| outcome complete | complete | .operation, artifacts, env, staged.json | already staged, verified | 1 | 1 | yes |
| intent copy-artifacts (partial copy) | unfinished | .operation, artifacts | resumed and staged | 1 | 1 | yes |
| intent create-environment (partial env) | unfinished | .operation, artifacts, env | resumed and staged | 1 | 1 | yes |

The last two rows left damage before stopping: a truncated wheel copy, and an
environment with only `pyvenv.cfg`. Both were redone in place. No
interruption was ever reported complete, and `staged.json` appeared only once
the operation was complete. On Linux, the same scenarios run in CI on both
Python versions (`test_a_crash_after_any_journaled_intent_or_outcome_reconciles_to_one_operation`,
`test_a_partial_copy_and_a_partial_environment_are_redone_in_place`).

## Before the change

- **Drift check.** The PR reports #11's drift check, run read-only against the
  live skills tree before any change. It found only `chat/SKILL.md` matching
  the already-carried commit `8ff325e`, the same as for #12 and #13. There was
  no newer commit and no uncommitted edit under `chat/`.
- **Nothing installed changed.** These gave identical output before and after
  the work:
  - the skills repository's HEAD and `status --short`;
  - `launchctl list | grep -i chat`;
  - `readlink ~/.local/bin/pchat`.
