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
- Standard library only. The environment is built by the chosen
  interpreter's own `venv`, with no pip and no activation scripts. The
  package is installed offline (`--no-index`) by that interpreter's bundled
  pip, run from its wheel.

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
- **An aliased root exempts nothing.** A skills folder is exempt only where it
  really is. A declared root that resolves to private data, contains it, or
  is an alias landing inside it is not searched at all and exempts nothing.
  For example, `~/.claude/skills` might be a link to `~/.claude` or to the
  home folder. The root is named as excluded and the inventory is marked
  incomplete, so private settings reached through it are never opened.
- **A hard-linked file is never opened.** A path can't show that a file is a
  hard link to private data, so a script with more than one hard link is
  named as excluded, not opened, and the inventory is marked incomplete. A
  script that changes, or gains a link, between that check and its open is
  not read and is named as unreadable.
- **Expressions over several lines are matched.** Each search covers the
  last 16 lines together, so a `sys.path.insert(...)` with `/ "chat"` and
  `/ "scripts"` on lines of their own is found. The same lines are searched
  again with trailing `#` comments dropped (outside quotes), so a comment
  between `/ "chat"` and `/ "scripts"` doesn't hide an importer.
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

A managed or captured file is read only when it is a regular file with a
single link, and, for a file in the skills tree, only when it is reached
from the tree's root with no alias on the way. A hard link or an alias may
be private data under another name, and a path can't show which, so such a
file is never opened. Its target blocks, naming the file and why it wasn't
read. The file opened must still be the one checked, with one link, before
anything is read. That covers the drift check's hashing, the content checks
behind each link and LaunchAgent, and the folder inventory.

The skills root's private-data boundary is checked first, before any target
is observed. A skills root that resolves into private data
(`~/.codex/skills` linked to `~/.claude`, say) exempts no managed file.
Nothing under it, or reached through it, is read: every target blocks,
naming the root, and the root is excluded from importer discovery too.

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
  therefore can't redirect a write staging makes itself: the write lands in
  the directory that was checked, and the next walk refuses the link. This
  doesn't hold for the `venv` and pip steps, which write by path; see
  [below](#venv-and-pip-d-71).
- **A private root.** A destination writable by other users is refused.
- **The root itself is pinned.** The destination is resolved once and
  checked by the guard. That resolved path is then opened from `/` one name
  at a time, without following links. The root and any missing parent are
  created relative to their pinned parent. An ancestor swapped for a link
  after the guard makes the open fail, and nothing is created. `plan --out`
  opens its directory the same way.
- **Resolved and guarded.** The final path and its parent must resolve under
  the root, and both must sit outside this checkout and every protected
  location.
- **New files only.** A file is created with `O_CREAT|O_EXCL|O_NOFOLLOW`
  under a fresh temporary name, which is journaled before it exists. Its
  inode is journaled right after the create. It is then linked into place
  with `link()`, which never replaces an entry, and the temporary name is
  removed. The final name is checked again just before. A replacement first
  removes the file this operation created there, checked by its inode. So an
  entry that appears at the name in the meantime, whatever it is, is left as
  it is, and the write is refused. Nothing is ever opened through a link or
  written into an existing file.
- **Nothing it didn't create.** An entry is replaced, truncated or deleted
  only if the journal records that this operation created it and it is still
  that entry: the same inode, singly linked, this owner. Anything else is
  refused and left unchanged: a symlink, a hardlink, a directory or file
  someone else put there, or a directory replaced by another of the same
  name.
- **The environment** is a directory this operation creates and records; its
  own `venv` run fills it, and its contents are inventoried. Recovery may
  remove that directory, with what the run put there, through pinned
  descriptors. Links inside are removed, never followed.
- **The journal** names its own inode and the lock's in its first record. A
  file at the journal's name that isn't singly linked, isn't this user's, or
  isn't the journal its first record names is refused and left
  byte-identical, as is a journal with no lock beside it. The lock is only
  ever opened read-only once it exists.

### `venv` and pip (D-71)

`venv` and pip are separate processes. They're given the
environment's absolute path and write by that path while they run, so the
pinned descriptors above don't cover their writes. The owner's decision
[D-71](plateia_design.md#d-71-plt-12-stagings-threat-boundary-for-concurrent-directory-swaps) (2026-10-09, [#14's amendment](https://github.com/coghex/plateia/issues/14#issuecomment-6077164625)) narrows requirement 8 for this one case. If
another process running as the same user swaps a directory on that path for
a link while a single `venv` or pip step runs, that step's writes can be
redirected. They may land before staging sees the swap. **Staging doesn't
claim that no write happens during such a swap.**

What staging does instead:

- **Before each step**, the path is checked name by name against the
  directories staging created and pinned: the root, `releases`, the release
  directory and `env`, each by its inode, none of them a link. Any difference
  is refused, naming the directory, and the process isn't run.
- **After each step**, the same check runs, whether the process succeeded,
  failed or couldn't start. A difference is refused, naming the directory
  that changed and saying the process's writes may have landed elsewhere. The
  step's outcome is recorded `failed`, never `ok`. No later step runs, and
  the operation stays unfinished. A rerun meets the link and refuses it.
- **Aliases that exist before a run, or appear between its journaled steps,**
  are refused before anything is written, as above.

The environment's contents are checked too, because pip runs the
environment's own interpreter, startup hooks included:

- **What `venv` builds.** `venv` runs in the chosen interpreter with
  `-I -S` and builds the environment with no pip and no activation scripts.
  What it leaves therefore holds no code:
  - `bin/`, holding links to the interpreter;
  - empty `include/`, `lib/` and `lib/pythonX.Y/site-packages/`;
  - `lib64` as a link to `lib`, where the platform makes one;
  - `pyvenv.cfg`.
- **Right after `venv`**, the environment is inventoried through
  descriptors: every entry, link target, file hash and permission bit. That
  inventory is checked against the layout above and the chosen interpreter,
  never adopted as it was observed:
  - every link in `bin/` must lead, inside the environment, to the chosen
    interpreter;
  - `pyvenv.cfg` must name that interpreter and keep the system
    `site-packages` out;
  - nothing else may be there.

  A `.pth` hook, a `sitecustomize` or any other entry added as `venv`
  returns is refused, naming it, before pip or anything else in the
  environment runs. Only then does the inventory become the evidence, and
  its sha256 goes in the `create-environment` outcome.
- **Pip comes from the interpreter, not the environment.** Pip runs from the
  chosen interpreter's bundled wheel (`ensurepip/_bundled`, the pip `venv`
  would install), as `bin/python -I -B -c <bootstrap> <wheel> install …`.
  Nothing of pip is installed into the environment. The wheel's name and
  sha256 go in the `install-package` outcome. The environment's interpreter
  starts with its site processing over a `site-packages` checked empty.
- **Immediately before pip**, the environment must still match that
  inventory exactly. A `.pth` hook or link, a `sitecustomize`, a changed
  `pyvenv.cfg` or a replaced interpreter link added between the steps is
  refused, naming the entry. Pip never runs.
- **After pip**, what it left is trusted only if it's that inventory plus
  entries the verified wheel accounts for. They're derived from the wheel
  alone: each member at its place in `site-packages`, the `dist-info` files
  pip writes itself (`INSTALLER`, `REQUESTED`, `direct_url.json` and the
  rewritten `RECORD`), a command for each console script the wheel
  declares, and the directories holding them. Pip runs with `--no-compile`,
  so no bytecode is expected. Each file must match the wheel, and the
  installed `RECORD` is checked too. It never authorises an entry, though:
  a `RECORD` naming anything else is refused. Only then is the environment
  recorded as the install inventory.

A swap that is undone before the step ends leaves nothing for the
after-step check to find. D-71 doesn't require staging to prevent writes
redirected during a step, and this check doesn't claim to.

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
  sha256 and the bundled pip wheel's name and sha256
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
    created again. That holds whenever an unfinished operation resumes, even
    after its install or verification succeeded, so recovery never runs an
    environment it finds on disk. It removes it, without following links
    inside, and builds it again.
- **Completion means verified.** `complete` is recorded only after
  `verify-environment` succeeds. Before it writes `staged.json`, the
  `complete` step checks again: the environment's path against the pinned
  directories, and the artifacts and environment against the trusted
  evidence. A change since verification (the environment swapped for a link,
  say, or a hook added) is refused, naming it, and the operation stays
  unfinished. `staged.json` is written just before the step's outcome. The
  journal's `complete` outcome, not that file, is what makes an operation
  complete, and `status` reports it so.
- **Verification is static first.** Every check reads through pinned
  descriptors: the artifacts read back, the journaled inventory, and one
  snapshot of the environment that every other check is derived from.
  Nothing in the environment runs until these hold:
  - the artifacts match the manifest;
  - the environment matches, entry by entry, the inventory this operation
    recorded right after install, whose sha256 is in the journal. That covers
    startup hooks (`.pth` files and `sitecustomize`), bytecode, the
    interpreter link, `pyvenv.cfg`, the commands and their permission bits;
  - every file the verified wheel hashes is installed with the same bytes;
  - every file the installed `RECORD` hashes still matches, and the package
    directory holds nothing `RECORD` doesn't list;
  - each command is executable and calls the wheel's entry point. It runs
    the environment's `bin/python`, whose links lead to the chosen
    interpreter, read from its shebang or from the `/bin/sh` launcher pip
    writes for long paths. Any other interpreter is refused, even another
    name in `bin/`;
  - each command is the launcher pip writes, judged by its structure, never
    by its `RECORD` hash. It's parsed (nothing runs): after the shebang, or
    the `/bin/sh` preamble, only `import re` and `import sys`, the one
    import of the wheel's entry point, and an `if __name__ == "__main__":`
    block. That block normalises `sys.argv[0]` with exactly one of the forms
    pip's versions write, statement for statement, and then exits with the
    entry point's result. The forms are distlib's `re.sub`, the `.exe`
    slice (with or without the `-script.pyw` branch), and
    `removesuffix('.exe')`. This is checked at install, and again whenever
    the environment is verified;
  - what the environment's own startup would establish, from the same
    snapshot: `bin/python` leads, through links inside the environment, to
    the operation's chosen interpreter; `pyvenv.cfg` names that
    interpreter's directory and version and keeps the system
    `site-packages` out, so the prefix is the environment and its one
    `lib/pythonX.Y/site-packages` is `purelib`; there's no `sitecustomize`
    or `usercustomize` anywhere in it; and the import path's additions are
    that `site-packages` plus the directory lines of its `.pth` files, each
    pinned by the inventory. An environment staging built has none: `venv`
    builds none, and the wheel may not carry a startup hook.
- **Then the import check.** Nothing from the environment ever runs after
  pip. The operation's chosen interpreter, which preflight checked and which
  lives outside the environment, runs once with `-I -S -B` (no site, `.pth`
  or `sitecustomize`) and `/` as its working directory. Its input comes on
  stdin: the package's sources and its `METADATA` and `entry_points.txt`,
  each read through the pinned environment and hash-checked against the
  verified wheel, and the installed `RECORD`, checked against the journaled
  inventory. A minimal in-memory finder imports `plateia_chat` from those
  bytes, running its initializer, so an import-time error under the chosen
  interpreter is refused. It reports:
  - the interpreter's implementation, version and platform, which must be
    the operation's and meet the manifest;
  - the module's location, inside the environment's `site-packages`;
  - the version `importlib.metadata` reads, which must be the manifest's;
  - the interpreter's own import path. With the static additions above, it
    must keep this checkout off it, and the interpreter's own library must
    hold no `plateia_chat` to shadow the staged one;
  - the installed `RECORD`'s hash for each imported source, which must
    match.

  The environment's path is checked against the pinned directories just
  before and after the check, and its contents compared with the trusted
  inventory afterwards. A change around it is refused, and the operation
  isn't recorded complete. A swap during it can't run code: the check never
  starts anything by a path inside the environment.

  What this doesn't observe is the environment's own interpreter starting
  up: its `venv` detection and its site processing. Those facts are derived
  from the pinned files above, not watched.
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

- the chosen interpreter, for its preflight probe, finding its bundled pip
  wheel, `venv` and the import check;
- the environment's own interpreter, only to run pip from that wheel.

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
  - round 8's fixture: the captured `chat/scripts/chatlib.py` replaced by a
    hard link to the invented chat config, and, separately, the whole
    `chat/scripts` folder moved into the invented private state with a link
    left in its place. With `open` and `os.open` set to fail on the private
    inodes, the full plan never opens them, blocks with the reason named,
    and prints no private value;
  - round 9's fixture: `~/.codex/skills` is a link to `~/.claude`, which
    holds a chat folder with private content at `chat/scripts/chatlib.py`.
    With `open` and `os.open` set to fail on that inode, the full plan never
    opens it. Every target blocks, naming the skills root, and staging is
    refused;
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
  one over the cap is named as unsearched. Round 5's fixture is covered:
  `~/.claude/skills` is a link to `~/.claude` (and, separately, to the home
  folder and to another folder inside `~/.claude`), with the managed chat link
  kept through it and a `settings.py` link to the private settings file. The
  settings file is never opened, the root is named as excluded, and the
  inventory is incomplete. The same holds when the skills root itself
  resolves to `~/.claude` or to the invented home, with an executable-looking
  `settings.py` link to the private settings file beside it. Round 7's
  fixture is covered too: the invented chat config hard-linked to
  `<skills root>/unrelated/scripts/settings.py`. With `open` and `os.open`
  set to fail on the config's inode, plan and discovery never open it, name
  the candidate as excluded, and report the inventory incomplete. Round 8's
  importer, with a comment after `/ "chat"` and `/ "scripts"` on the next
  line, is named. A sixth
  importer in each root is named, including one using
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
  - round 9's fixture: a foreign `artifacts/manifest.json` appears at the
    journal boundary after its temporary file is announced, or after its
    creation is recorded. Separately, on a resumed run, a foreign file takes
    the place of the manifest this operation copied. It's never replaced: it
    stays byte-identical and the operation unfinished;
  - the artifacts directory, the release directory or the environment
    swapped for a link into protected state at the exact journal boundary
    after its check. Nothing reaches protected state, and a writer-level
    test shows the write landing in the pinned original directory.
- **A swap while `venv` or pip runs (D-71):** there's one test for `venv` and
  one for pip. At the process boundary, just as the real process starts, the
  destination, the release directory or `env` is swapped for a link into the
  invented chat state, and it's still swapped at the check after the step.
  Each case:
  - is refused, naming the swapped directory;
  - records only a `failed` outcome for the step, and no step at or after it
    recorded `ok`;
  - starts no later step (after a `venv` swap, pip never runs);
  - leaves the operation unfinished, with no `staged.json`.

  A retry refuses the conflicting directory by name. The journal stays
  byte-identical, still holding that one operation, and nothing in the home
  changes. The tests deliberately don't assert that no write landed during
  the swap. Pre-existing and between-step conflicts keep their
  byte-preservation checks in the alias matrix above.
- **Changed environment:** on a completed retry, a modified `__init__.py`, an
  added `.pth` or `sitecustomize` startup hook, a changed command, a cleared executable bit or
  an edited `pyvenv.cfg` is refused before the environment's interpreter
  runs, and the registry the planted code would write is unchanged. The
  deeper wheel, `RECORD` and entry-point checks are tested with the inventory
  re-recorded to match a change. In recovery, a run interrupted after its
  install or its verification is then tampered with: altered package code,
  a `.pth` hook, a `sitecustomize` hook or a changed `pyvenv.cfg`, each
  planted to overwrite the invented registry. The environment it finds never
  runs. It's rebuilt, and the registry and the whole home are unchanged.
- **Concurrency:** a run while the journal lock is held is refused.
- **Destinations:**
  - through a link into a managed target, under `~/.local/bin`, or inside
    this checkout: refused, nothing created;
  - unrelated content, or a release directory without a journal: refused;
  - an ancestor swapped for a link into the invented chat state after the
    guard, or after the pin's own recheck, with the destination's parent
    present or missing: refused, nothing created inside protected state;
  - `status` never follows a link at the journal.
- **Environment integrity:**
  - **Right after `venv` (round 9):** before anything is inventoried, a
    `.pth` hook or a `sitecustomize` is added, each written to leave a
    marker and overwrite the invented registry. Each is refused at
    `create-environment` as outside `venv`'s layout, before pip or anything
    else in the environment runs. The marker never appears, the operation is
    unfinished and the home is unchanged.
  - **Between `venv` and pip:** a `.pth` link to an external hook, a `.pth`
    file, a `sitecustomize`, a changed `pyvenv.cfg` or a replaced interpreter
    link. Each is refused before pip runs. Pip never appears in the recorded
    calls, the operation is unfinished, and the home is unchanged.
  - **During pip:** an unlisted hook added, or a file `venv` built changed.
    Neither is recorded as installed.
  - **A rewritten command (round 8):** right after pip returns, `pchat`
    keeps pip's launcher lines but its body is replaced, either by the entry
    point's import and a `raise`, or by the original with a statement
    appended, and its `RECORD` row updated to match. The install is refused,
    naming the command, and nothing is recorded installed.
  - **A launcher's interpreter or normalisation (round 9):** right after pip
    returns, `pchat`'s interpreter becomes `env/bin/missing-python`, or its
    normalisation becomes `sys.argv[0] = sys.argv[0].endswith()`. Each has
    its `RECORD` row updated. Each is refused at install, naming the
    command, and nothing is recorded installed.
  - **A change after verification (round 8):** right after
    `verify-environment` records `ok`, the environment is swapped for a
    link into the invented private state, or a `.pth` hook is added. The
    `complete` step refuses, naming the change, with no `staged.json`, the
    operation unfinished and the home unchanged.
  - **After pip, with `RECORD` rewritten (round 7):** right after pip
    returns, a `.pth` hook, a `sitecustomize` or a module inside the package
    is added, each with a matching row appended to the installed `RECORD`,
    and each written to leave a marker and overwrite the invented registry.
    Each is refused at install, naming the entry, before any staged code
    runs. The import check never runs, the marker never appears, the operation is
    unfinished with no install inventory, and the home is unchanged.
  - **Around the import check:** a hook added, or the environment swapped
    for a link. Each is refused, and verification isn't recorded complete.
  - **An environment swapped at the import check (round 7):** at its
    process boundary, the environment is swapped for a copy carrying a
    `.pth` hook, a `sitecustomize`, a changed package initializer and a
    `bin/python` that's a script, each written to leave a marker and
    overwrite the invented registry. Nothing in it runs: the marker never
    appears, the home is unchanged, the operation is refused and unfinished
    with no `staged.json`, and no recorded call starts anything inside the
    destination except pip.
  - **A package that fails to import:** a genuine release that verifies,
    whose package initializer raises, is refused by the import check,
    naming the error, and the operation isn't recorded complete. That's
    the live-import coverage the earlier in-environment probe gave.
- **Privacy:** none of the secret values appears in any command's output or
  in the plan or journal, on both a passing run and a failing preflight.
- **Isolation:** the fake `launchctl`, `systemctl`, `install-identities`,
  `pchat`, `chat-bridge`, `rotate-logs`, merge-worker and drainer commands
  record no call. Every recorded command is the chosen interpreter or the
  staged environment's.

### Crash recovery (invented fixtures, stager at `787f780`)

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
| created <release>/artifacts/.manifest.json.b8bb49ac54e4.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/manifest.json | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| creating <release>/artifacts/.plateia-skill-chat-0.1.0+g04cab9561a11.zip.<random>.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/.plateia-skill-chat-0.1.0+g04cab9561a11.zip.a5e44bdfa3bc.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/plateia-skill-chat-0.1.0+g04cab9561a11.zip | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| creating <release>/artifacts/.plateia_chat-0.1.0+g04cab9561a11-py3-none-any.whl.<random>.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/.plateia_chat-0.1.0+g04cab9561a11-py3-none-any.whl.b07cdb58dab4.tmp | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/artifacts/plateia_chat-0.1.0+g04cab9561a11-py3-none-any.whl | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| outcome copy-artifacts | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| intent create-environment | unfinished | artifacts | resumed and staged | 1 | 1 | yes |
| created <release>/env | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| outcome create-environment | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| intent install-package | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| creating <release>/.env-inventory.json.<random>.tmp | unfinished | artifacts, env | resumed and staged | 1 | 1 | yes |
| created <release>/.env-inventory.json.995cc3885f9a.tmp | unfinished | .env-inventory.json.995cc3885f9a.tmp, artifacts, env | resumed and staged | 1 | 1 | yes |
| created <release>/env-inventory.json | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| outcome install-package | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| intent verify-environment | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| outcome verify-environment | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| intent complete | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| creating <release>/.staged.json.<random>.tmp | unfinished | artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
| created <release>/.staged.json.71f49c730064.tmp | unfinished | .staged.json.71f49c730064.tmp, artifacts, env, env-inventory.json | resumed and staged | 1 | 1 | yes |
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
