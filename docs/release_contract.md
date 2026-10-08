# Shared chat releases: contract and build evidence

PLT-11 (#13) builds immutable, pinned releases of the shared chat code that
PLT-9 (#11) and PLT-15 (#12) captured. This page is the release contract and
its build evidence. The decisions behind it are in `docs/plateia_design.md`:
C-7/P-6, D-15, D-23 and D-25. Building a release publishes, installs,
stages and activates nothing; those are later slices of #10.

The builder is `packages/release/build_release.py`. It uses the Python
standard library only, and its tests use only the pip bundled in a `venv`.
Neither needs network access.

```sh
python3 packages/release/build_release.py build --source <plateia checkout> [--commit <rev>] --out <dir>
python3 packages/release/build_release.py verify <dir>/<release>
```

Exit status: `0` success, `1` refused (the message names the cause), `2`
bad usage.

## What a release contains

The first release carries only the shared chat code and its guidance (D-25).
The inbox core joins a release with PLT-13.

| Artifact | Contents |
| --- | --- |
| `plateia_chat-<version>-py3-none-any.whl` | Package `plateia_chat`: the captured commands and modules, unchanged, under `plateia_chat/scripts/`; `provenance.json`; thin entry points in `plateia_chat/__init__.py`. Console scripts `pchat`, `chat-bridge` and `rotate-logs`. |
| `plateia-skill-chat-<version>.zip` | `chat/SKILL.md` and `chat/skill.json`, which declares the skill's name, version and compatible package versions. |
| `manifest.json` | The binding described below. |

`plateia_chat.load(<command>)` returns a captured command's module without
running it: its `__main__` block doesn't run, so loading the bridge doesn't
start it. Each captured command puts its own directory on the import path,
so its modules load from the installed release, never from a checkout.

## Identity and immutability

A release is `plateia-chat-<package version>`. The version is the
`release.json` version plus the source commit as a local label, for example
`0.1.0+g48b901ac3e81`. A release is written to `<out>/<identity>/`:

- **A new release** is first written beside that directory and verified,
  then renamed into place. A refused build leaves no release and no partial
  directory.
- **Rebuilding the same commit** produces the same bytes: fixed zip
  timestamps, sorted members. An identical existing release is reported as
  `already built, identical`, and nothing changes.
- **An existing identity with different bytes or files** is refused and
  left unchanged. An identity is never rebound.

## What the build refuses

- **A dirty source working tree:** modified, staged or untracked files. It
  names up to five of them.
- **A commit not reachable from the source's `origin/master`.** The check
  is offline, against the local ref. Reachability is the source-eligibility
  check: code reaches `master` through `review-approved` and `build-test`.
  The build doesn't query either gate over the network, and documentation
  lands on `master` through `docs-push` ungated.
- **A source without an `origin/master`**, and a revision that isn't a
  commit.
- **An output directory inside the source checkout, or under the live
  tools' locations:** `~/.codex/skills`, `~/.config/chat`, `~/.config/cmux`,
  `~/.local/state`, `~/.local/bin` and `~/Library/LaunchAgents`, plus
  wherever `CHAT_STATE` and `CHAT_CONFIG` point when they are set. Both the
  output path and each protected location are resolved before comparing,
  and the unresolved spellings are checked too. So neither a symlinked
  output path nor a symlinked live location gets through.
- **An artifact that would carry private data:** a home-directory path other
  than the invented `/Users/someone`, a credential pattern, or a chat-state or
  configuration file.
- **An existing identity with different bytes**, as above.

Every payload byte is read from the named commit with git (`ls-tree`,
`cat-file`), never from a working tree. So the manifest's source commit is
exactly what ships. The builder's git commands set `GIT_OPTIONAL_LOCKS=0`, so
they don't refresh the source's index.

## The manifest

`manifest.json` (`plateia-release-manifest/1`) binds:

- **`release`:** the identity.
- **`source.commit`:** the full source commit, from which every payload
  byte comes.
- **`builder`:** the builder checkout's revision, whether its
  `packages/release/` was dirty, and the sha256 of each builder file
  (`build_release.py`, `plateia_chat_init.py`, `release.json`). The
  packaging glue is the builder's, so it is identified separately from the
  payload source.
- **`build_interpreter`:** the implementation and full version that built
  the release.
- **`requires_python`:** `>=3.10`.
- **`dependencies`:**
  - the interpreter (`>=3.10`, CPython), the one pinned dependency;
  - no Python packages;
  - `weechat` as an optional module the host provides.
- **`platforms`:** POSIX: Darwin (macOS) and Linux. Linux is verified in CI
  and macOS by the recorded run below.
- **`package`:** name, version, artifact and commands.
- **`skill`:** name, version, artifact and `compatible_packages`.
- **`artifacts`:** the name, sha256 and size of each artifact.
- **`formats`:** each wire protocol and persistent state format the code
  reads or writes, with the versions it reads and the version it writes
  (none for a format it only reads). Three formats carry an embedded version:
  the identity registry (`version: 1`), child runs (`schema: childrun/2`) and
  the receipt marker (`review-start-receipts-v1` in its file name). The rest
  get documented baseline identifiers such as `channel-log/1`. The chat
  config is written as well as read: `identities.register()` adds accounts,
  identity roles and assistants to it. The inventory also lists
  `role_colors.py`'s WeeChat state (`weechat.look.nick_color_force` and the
  script's `managed` plugin option), and the host interfaces the code uses:
  `launchctl print`, `ps`, the SIGHUP `rotate-logs` sends, and the `CHAT_*`
  environment `pchat agent run` gives its child. A test ties markers in the
  shipped code to the entries that must declare them. The build
  changes no serialized byte.

The manifest has no API version; the shared API slice (PLT-10) adds one.

## What verification refuses

`verify` reads a release directory and never writes to it. It refuses, naming
the failing input:

- a missing or unreadable `manifest.json`;
- a manifest that pins no source commit, or no interpreter requirement
  (`requires_python` or `dependencies.interpreter.requires`);
- a missing artifact, a file the manifest doesn't name, or an artifact whose
  sha256 or size differs from the manifest;
- a wheel whose files don't match its own `RECORD`, or whose name, version or
  `Requires-Python` differ from the manifest;
- a skill archive without a readable `SKILL.md` and `skill.json`, or with a
  malformed declaration;
- a skill whose name or version differ from the manifest, or whose
  compatibility declaration differs from the manifest's;
- a skill whose `compatible_packages` exclude the release's package version.
  The check compares public versions, without the local `+g<commit>` label.

## Installing (tests and evidence only)

A release installs into a fresh `venv` with that venv's own pip, offline:

```sh
python3 -m venv <env>
PIP_NO_INDEX=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CONFIG_FILE=/dev/null \
  <env>/bin/python -m pip install --no-index --no-deps <release>/plateia_chat-*.whl
```

The wheel installs only `plateia_chat/`, its `dist-info` and the three
console scripts. There is no `.pth` file, no editable install and no checkout
on the import path. Staging releases on the owner's machine is PLT-12.

## Tests

`packages/release/tests` runs in `build-test` on Python 3.10 and 3.14. It
has its own empty-run guard, and CI requires the modules `test_build`,
`test_verify`, `test_install` and `test_privacy` by name.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s packages/release/tests -v
```

The tests build from an invented repository that carries the actual captured
chat code and is landed on its own `origin/master`. Its git identity is
invented, and the machine's git configuration is ignored.

- **`test_build`:** builds, binds the manifest, reads payload bytes from the
  recorded commit (including an older one), rebuilds byte-for-byte, and
  leaves the inbox out. It also covers every build refusal, including an
  outside-looking output path that resolves into the checkout, conflicting
  reuse of an identity, and private data in the payload.
- **`test_verify`:** covers every verification refusal, and checks that
  verification leaves its input unchanged.
- **`test_install`:** creates a fresh venv outside any checkout and installs
  with `--no-index`. It runs `pchat --help` and `rotate-logs --help`, loads
  the bridge's code without starting it, and confirms `weechat` is absent.
  It checks each loaded module's file and the import path, so that nothing
  comes from a checkout and every shared-chat module comes from the venv.
  It also checks the installed file list, and that pip wrote nothing to the
  invented home. The checker is itself shown to catch an import from a
  checkout.
- **`test_privacy`:** opens every artifact and archive member. The scan is
  shown to catch home paths, credentials and state files, and to allow the
  invented placeholders and generic `~/...` references.

## Build evidence

**macOS, 2026-10-08.** This was one run on the owner's machine (macOS,
arm64, CPython 3.14.8), using this PR's build step.

- **Inputs:**
  - **Builder:** this PR's `packages/release/build_release.py`, at builder
    revision `706a986f3661ab63bb67581d2c07f22e4c4f1b31`, not dirty: the PR
    head after its second review. Earlier runs with `2ab6c29` and `01d0d57`
    gave the same outcomes and byte-identical artifacts. Only the manifest's
    builder identity and format inventory differed.
  - **Source:** a clean detached checkout of `origin/master` at
    `48b901ac3e81d1f0c2b249f9381cf1af4aa2524a`. That commit contains #11's
    and #12's code.
- **Locations:** the output, the `venv` and an invented `HOME` were in a
  session scratch directory, outside the plateia checkout and the skills
  repository.

| Step | Command | Outcome |
| --- | --- | --- |
| build | `build_release.py build --source <src> --commit 48b901ac3e81d1f0c2b249f9381cf1af4aa2524a --out <out>` | `built: plateia-chat-0.1.0+g48b901ac3e81 from 48b901ac3e81 (2 artifacts)` |
| verify | `build_release.py verify <out>/plateia-chat-0.1.0+g48b901ac3e81` | `verified: ...; 2 artifacts match the manifest` |
| hashes | `shasum -a 256` of each artifact | wheel `29351bf4fd71e85a0644d94134e35ffa7156a5f22873eda0d784abaf96a5f87f`, skill `5b495b854124e063a11960fbf62be8fad4ce1706d25ca094bcc93c0399f8c2af`, both equal to the manifest |
| install | `python3 -m venv <env>`; `<env>/bin/python -m pip install --no-index --no-deps <wheel>` (pip variables above, invented `HOME`) | `Successfully installed plateia-chat-0.1.0+g48b901ac3e81` |
| pchat | `<env>/bin/pchat --help` | exit 0 |
| rotate-logs | `<env>/bin/rotate-logs --help` | exit 0 |
| bridge | `<env>/bin/python -c "import plateia_chat; plateia_chat.load('chat-bridge'); plateia_chat.load('role_colors.py')"` with a module-file check | bridge's `main` callable, not run; `weechat` absent (`None`); 6 shared-chat modules loaded, 0 outside the venv; no checkout on `sys.path` |
| home | files under the invented `HOME` afterwards | none |

**Linux:** CI's `build-test` on Python 3.10 and 3.14 runs the same build,
verify and install path on every pull request.
