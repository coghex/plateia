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
  only the plan file: a new file, through the same safe-write layer as
  `stage`.)
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
- **Private data is never opened.** Nothing that resolves into private data
  is opened or walked into, even through an alias: chat state and config,
  `~/.config`, `~/.local/state`, `~/.local/share`, `~/Library`, `~/.ssh`,
  `~/.gnupg`, `~/.aws`, and `~/.codex` and `~/.claude` outside their skills
  folders (sessions, history, settings). Such an alias is named as excluded,
  and the inventory is marked incomplete.
- **Expressions over several lines are matched.** Each search covers the
  last 16 lines together, so a `sys.path.insert(...)` with `/ "chat"` and
  `/ "scripts"` on lines of their own is found.
- **Scripts are read whole, line by line.** Only a script over 64 MiB is
  named as unsearched, which marks the inventory incomplete.
- **Which files are searched.** Only scripts are opened: a `.py`, `.sh`,
  `.bash` or `.zsh` suffix, or the executable bit. Data files are never
  opened.
- **Links are followed, once.** Symlinked folders and scripts inside a root
  are searched. Each resolved folder is walked once, so a cycle ends, and
  each resolved script is counted once, under the first path that reaches
  it, so an alias such as `~/.claude/skills/project-manager` doesn't
  duplicate a known importer.
- **What is skipped.** `.git`, `__pycache__`, `node_modules` and the chat
  folder itself, however it is reached.
- **Incomplete inventory.** A root, folder or script that can't be read, or
  more than 200,000 entries, marks the inventory incomplete instead of
  clean.
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
  - each child run's `state.json` `schema`, following symlinked projects and
    runs;
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
<root>/journal.jsonl                          the journal
<root>/journal.lock                           its lock
<root>/releases/<release>/artifacts/          the release's manifest and artifacts, verified
<root>/releases/<release>/env/                the private venv with the package installed
<root>/releases/<release>/env-inventory.json  every entry of env/, recorded right after install
<root>/releases/<release>/staged.json         written by the complete step: "selected": false
```

- **The guard.** The root, the journal, the lock and the release, environment
  and artifact directories are each checked with `build_release`'s
  protected-location guard. They must be outside this checkout and every live
  location, after resolving symlinks.
- **What the guard covers.** The live locations are the skills tree and all of
  `~/.codex` and `~/.claude`, the chat config and state, `~/.local/bin`,
  LaunchAgents, WeeChat's directories, and every symlink target inside them.
- **Unrelated content is refused, not adopted.** A destination holding
  anything staging didn't create is refused, as is a release directory no
  journaled operation created.

### One safe-write layer

Every write staging makes goes through one layer (`Writer`), including
`plan --out` and every removal during recovery:

- **Plain names.** A path is plain components: letters, digits and `._+-`,
  never `.` or `..`. The release identity must be the package's name and
  version as one such component, and artifact names must be plain and
  distinct.
- **Only staging's own directories, pinned.** The root is held open, and
  every directory on the way is opened relative to the one above it with
  `O_DIRECTORY|O_NOFOLLOW`. Each must be a real directory, owned by this user
  on the root's device, that the journal records staging created, by its
  inode. Every create, rename, delete and tree removal then happens relative
  to those open descriptors. A directory swapped for a link after its check
  therefore can't redirect a write: the write lands in the directory that
  was checked, and the next walk refuses the link.
- **A private root.** A destination writable by other users is refused.
- **Resolved and guarded.** The final path and its parent must resolve under
  the root, and both must sit outside this checkout and every protected
  location.
- **New files only.** A file is created with `O_CREAT|O_EXCL|O_NOFOLLOW`
  under a fresh temporary name, which is journaled before it exists. Its
  inode is journaled right after the create, and it is then renamed into
  place. Nothing is ever opened through a link or written into an existing
  file.
- **Nothing it didn't create.** An entry is replaced, truncated or deleted
  only if the journal records that this operation created it and it is still
  that entry: the same inode, singly linked, this owner. Anything else is
  refused and left unchanged: a symlink, a hardlink, a directory or file
  someone else put there, or a directory replaced by another of the same
  name.
- **The environment** is a directory this operation creates and records; its
  own `venv` run fills it. `venv` and pip address it by path, so the
  directory is checked to still be the one this operation created right
  before and right after each of them, and its contents are inventoried.
  Recovery may remove that directory, with what the run put there, through
  pinned descriptors. Links inside are removed, never followed.
- **The journal** names its own inode and the lock's in its first record. A
  file at the journal's name that isn't singly linked, isn't this user's, or
  isn't the journal its first record names is refused and left
  byte-identical, as is a journal with no lock beside it. The lock is only
  ever opened read-only once it exists.

### Order

Nothing is written until all of these pass, in order:

1. The plan is made. With `--plan`, it is compared with the saved plan.
2. The preflight passes.
3. The destination passes the guard.
4. The journal lock is taken. A second run is refused.
5. Every target is checked again, under the lock, immediately before the
   operation records it. A change since the plan blocks.
6. Every path the operation will touch is inspected. An alias anywhere among
   them is refused before anything changes.

### The journal

The journal is one JSON record per line, each fsynced. It starts with a
header naming the journal's and the lock's inodes. An operation's `begin`
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
- `install-package`, whose outcome also records the environment inventory's
  sha256
- `verify-environment`
- `complete`

Within a step, every directory and file created gets a `created` record with
its inode. Every temporary file gets a `creating` record before it exists.

### Repeats and recovery

- **The same inputs give the same operation id.**
  - **A completed operation** is verified again and reported `already staged,
    verified`. Nothing is written. If it no longer verifies, it is not
    reported staged.
  - **An unfinished one** is resumed: a `resume` record, then every step
    again.
- **The steps are safe to repeat.**
  - Artifacts this operation copied are replaced, and its temporary files
    are removed, each only by its recorded inode. A journaled intent to
    create a name is not evidence: a file at such a name without a creation
    record, like anything else in the directory, is refused and left
    untouched, and the operation stays unfinished.
  - A partial environment, a directory this operation created, is removed and
    created again.
- **Completion means verified.** `complete` is recorded only after
  `verify-environment` succeeds. `staged.json` is written by the `complete`
  step just before its outcome. The journal's `complete` outcome, not that
  file, is what makes an operation complete, and `status` reports it so.
- **Verification is static first.** Nothing in the environment runs until
  these hold:
  - the artifacts match the manifest;
  - the environment matches, entry by entry, the inventory this operation
    recorded right after install, whose sha256 is in the journal. That covers
    startup hooks (`.pth` files), bytecode, the interpreter link, `pyvenv.cfg`,
    the commands and their permission bits;
  - every file the verified wheel hashes is installed with the same bytes;
  - every file the installed `RECORD` hashes still matches, and the package
    directory holds nothing `RECORD` doesn't list;
  - each command is executable, calls the wheel's entry point, and runs the
    staged interpreter, read from its shebang or from the `/bin/sh` launcher
    pip writes for long paths.
- **Then the probe.** Only then does the environment's interpreter run once,
  to confirm that it loads `plateia_chat` from itself at the manifest's
  version, with this checkout off its import path, and that the interpreter
  is the operation's and meets the manifest.
- **Different inputs are a conflict.** While an operation is unfinished, a run
  with different inputs (another interpreter, a changed target, another
  release) is refused, naming the inputs that differ. Repeating the run with
  the operation's own inputs finishes it.
- **A torn last journal line** is truncated under the lock, durably, before
  the next record, and the `resume` record notes the bytes dropped. Only a
  final fragment that has no newline and starts like one of staging's
  records counts as torn. `status`, which never writes, ignores it. Any
  other unreadable content is a refusal, and the journal is left unchanged.
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
- **Importers:** an importer expression spread over several lines is named. An
  alias into the invented chat config, state or `~/.claude`
  is never opened and is named as excluded. A 2.4 MB importer is found, and
  one over the cap is named as unsearched. A sixth importer in each root is
  named, including one using
  `sys.path` assignment and ones reached through a symlinked folder or
  script. A cycle ends. An alias of a known importer isn't duplicated. A
  missing known importer is reported, an unreadable root makes the inventory
  incomplete, and data files are never opened.
- **Recovery:** the table below, plus a conflicting retry, a torn journal line
  and a damaged completed release. Also: a stray file in the artifacts
  directory, or a file at a journaled temporary name with no creation
  record, is refused and left untouched.
- **Child runs behind symlinks:** an unsupported schema in a symlinked
  project or run is refused.
- **Alias matrix:** at every write site (journal, lock, `releases`, the
  release directory, `artifacts`, an artifact, a temporary file, `env`,
  `env-inventory.json`, `staged.json`), a symlink to protected state, a
  hardlink to the invented identity registry for a file site, and a
  directory staging didn't create for a directory site. Each is refused with
  the registry and the whole home byte-identical. Also covered:
  - a directory staging created, replaced by another of the same name;
  - traversal through the release identity, an artifact name and
    `plan --out`;
  - `plan --out` through a symlink, a hardlink or an existing file;
  - a journal staging didn't create, which is left byte-identical;
  - the artifacts directory, the release directory or the environment
    swapped for a link into protected state at the exact journal boundary
    after its check. Nothing reaches protected state, and a writer-level
    test shows the write landing in the pinned original directory.
- **Changed environment:** on a completed retry, a modified `__init__.py`, an
  added `.pth` startup hook, a changed command, a cleared executable bit or
  an edited `pyvenv.cfg` is refused before the environment's interpreter
  runs, and the registry the planted code would write is unchanged. The
  deeper wheel, `RECORD` and entry-point checks are tested with the inventory
  re-recorded to match a change.
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

### Crash recovery (invented fixtures, stager at `95b390d`)

Each row is a fresh invented home. The run was stopped right after the
journal record in the first column, then run again with the same inputs.
That covers every record the journal holds after its header: the operation,
every intent and outcome, and every directory and file created. The last two
rows also left damage before stopping: a manifest copy cut short, and a venv
half written. "Home unchanged" means every file, link and directory in the
home compares byte-identical to before the first run.

macOS arm64, CPython 3.14.8:

| Interrupted after | Status after the interruption | Release dir holds | Rerun | Operations | Release dirs | Home unchanged |
| --- | --- | --- | --- | --- | --- | --- |
| begin | unfinished | no release dir | resumed and staged | 1 | 1 | yes |
| intent create-release-dir | unfinished | no release dir | resumed and staged | 1 | 1 | yes |
| created releases | unfinished | no release dir | resumed and staged | 1 | 1 | yes |
| created <release> | unfinished | (empty) | resumed and staged | 1 | 1 | yes |
| outcome create-release-dir | unfinished | (empty) | resumed and staged | 1 | 1 | yes |
| intent copy-artifacts | unfinished | (empty) | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| creating <release>/artifacts/.manifest.json.<random>.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/.manifest.json.d270391e2c44.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/manifest.json | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| creating <release>/artifacts/.plateia-skill-chat-0.1.0+g04cab9561a11.zip.<random>.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/.plateia-skill-chat-0.1.0+g04cab9561a11.zip.615b216f7836.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/plateia-skill-chat-0.1.0+g04cab9561a11.zip | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| creating <release>/artifacts/.plateia_chat-0.1.0+g04cab9561a11-py3-none-any.whl.<random>.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/.plateia_chat-0.1.0+g04cab9561a11-py3-none-any.whl.cc9760477c81.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/plateia_chat-0.1.0+g04cab9561a11-py3-none-any.whl | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| outcome copy-artifacts | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| intent create-environment | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/env | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| outcome create-environment | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| intent install-package | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| creating <release>/.env-inventory.json.<random>.tmp | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| created <release>/.env-inventory.json.48b42da26c2f.tmp | unfinished | .env-inventory.json.48b42da26c2f.tmp, artifacts, env | resumed and staged | 1 | 1 | yes |
| created <release>/env-inventory.json | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| outcome install-package | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| intent verify-environment | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| outcome verify-environment | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| intent complete | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| creating <release>/.staged.json.<random>.tmp | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| created <release>/.staged.json.90916afab3ad.tmp | unfinished | .staged.json.90916afab3ad.tmp, artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| created <release>/staged.json | unfinished | artifacts, env, env-inventory.json, staged.json | resumed and staged | 1 | 1 | yes |
| outcome complete | complete | artifacts, env, env-inventory.json, staged.json | already staged, verified | 1 | 1 | yes |
| creating <release>/artifacts/.plateia_chat-0.1.0+g04cab9561a11-py3-none-any.whl.<random>.tmp (and the manifest copy cut short) | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/env (and the venv half written) | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |

No interruption was ever reported complete. `staged.json` exists only once
the `complete` step has written it, and the operation is complete only at
that step's outcome. On Linux, the same scenarios run in CI on both Python
versions (`test_a_crash_after_any_journaled_intent_or_outcome_reconciles_to_one_operation`,
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
