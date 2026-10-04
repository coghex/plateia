# CI design: the `build-test` check

Plateia's pull requests merge today on the review gate alone, because the
repository has no CI. This design adds the `build-test` check that
[AGENTS.md](../AGENTS.md) already expects, following the convention the owner
uses in the other projects, and then makes the PR drainer require it. It is
proportional to what there is to test: one small, standard-library-only Python
package.

Design state: `ready for issue processing`

Owner: `coghex/plateia`; publication target: `master`. Drafted 2026-10-04 in
the `docs-wip` worktree. The owner reviewed it and approved the rollout on
2026-10-04 at 01:50 ("yes, sounds good, linux only coverage"), settling D-3 to
D-6. All dates and times in this document are UTC.

Status legend: `[ ]` unprocessed · `[#N]` linked to issue N · `[no-issue]`
reviewed and deliberately not tracked separately · `[deferred]` blocked on a
concrete precondition

## Processing status

- [ ] EPIC. Run CI on every pull request and require `build-test` to merge
- [ ] CI-1. Add the CI workflow that publishes `build-test`
- [ ] CI-2. Make the drainer require `build-test`

## Epic contract

- **Goal:** every pull request and every push to `master` runs the package's
  tests on GitHub and reports one check, `build-test`; the PR drainer refuses
  to merge a pull request whose current head lacks a successful `build-test`.
- **Done when:** `.github/workflows/ci.yml` is on `master`; a `master` push
  run reported `build-test` = success; `.drain-prs.json` on `master` sets
  `"required_ci_check": "build-test"` explicitly; and a read-only drainer
  invocation against the updated checkout
  reports `Required checks: build-test, review-approved`.
- **Users and operators:** the owner (reads the result, decides what is
  required); solver and reviewer agents (see the check on their pull
  requests); the kanban PR drainer (enforces it).
- **Arc label:** `None proposed`.

## Current state and evidence

### Plateia

- **No CI.** The only workflow is `.github/workflows/review-gate.yml`, which
  publishes `review-approved` and strips stale approvals. It declares
  `permissions: contents: read` at the top level and widens only the
  stale-approval job to `pull-requests: write`. It uses `actions/checkout@v6`.
- **The drainer's CI gate is disabled.** `.drain-prs.json` is
  `{"required_ci_check": null}`, added by PR #4 (commit `119b148`, closes #3):
  "Let the review gate alone govern merges until CI exists … This repository
  has no build-test workflow yet, so approved pull requests otherwise wait
  forever."
- **The check name is already fixed.** AGENTS.md, Workflow: every change
  passes `review-approved` "and, once CI exists, `build-test`".
- **No branch protection.** `gh api repos/coghex/plateia/branches/master/protection`
  returns 404 (read 2026-10-04), so only the drainer enforces checks.
- **What there is to test.** `packages/routine_inbox` (`__init__`,
  `__main__`, `adapter`, `inbox`, `protocol`, `run`) and one test module,
  `tests/test_inbox.py`. `skill/OPERATING.md` documents the command and the
  floor: "Python 3.10 or newer, standard library only", run from the
  repository root as
  `PYTHONPATH=packages PYTHONDONTWRITEBYTECODE=1 python3 -B -W error::ResourceWarning -m unittest discover -s packages/routine_inbox/tests`.
  The tests use temporary state and a `FakeAdapter`, and patch real
  `socket` and `subprocess` use to fail, so they need no network, chat, git
  configuration or secret. No requirements file, `pyproject.toml` or lockfile
  exists; nothing needs installing.
- **Local run, 2026-10-04:** the command above ran 60 tests in 0.43 s, `OK`,
  on Python 3.14.
- **Design context.** `docs/plateia_design.md` (the owner's protected draft;
  cited, not changed) says required CI and the review gate must pass
  independently for a carried head (P-5, Publication, freshness and CI), and
  leaves the fresh implementation's check selection to Q-16. This design does
  not answer Q-16: it adds the check that the current kanban drainer, and any
  successor, would select.

### Two verified behaviours the design has to respect

- **`-W error::ResourceWarning` does not fail an unclosed-file leak.** A leak
  is reported from an object finaliser, where Python turns the error into an
  "Exception ignored …" message on stderr and carries on. Probe (2026-10-04,
  Python 3.14, scratch test that opens a file and drops it): the run printed
  `Exception ignored while finalizing file … ResourceWarning: unclosed file`,
  then `Ran 1 test … OK`, exit 0. The flag still fails warnings raised
  synchronously, but not the common leak it is there for.
- **An empty discovery passes on older Pythons.** Python 3.12 and later exit
  5 with `NO TESTS RAN` when discovery finds nothing (confirmed locally on
  3.14: exit 5). Python 3.10 and 3.11, which the package supports, exit 0. A
  moved or renamed test directory would pass silently there.

### The owner's convention (read-only, 2026-10-04)

Sources: `~/work/kanban` `f5469d9` (`.github/workflows/ci.yml`,
`tools/drain_prs.py`, `tools/test_ci_workflow.py`), `~/work/quruntul`
`4ece814` (`ci.yml`), `~/work/moskophoros` `c93605c` (`ci.yml`),
`~/work/synarchy` `b3b0c9b` (`ci.yml`), `~/work/hetoimasia` `cf1fd24`
(`validation.yml`, `docs/ci_validation_design.md`).

| Aspect | What the five repositories do |
| --- | --- |
| Required check | All five publish one job named `build-test` that stands for the whole run; it is the only CI context other systems name. kanban's `.drain-prs.json` sets `required_ci_check: "build-test"` and its `master` branch protection requires `build-test` and `review-approved`. quruntul and moskophoros have no branch protection. |
| Aggregate | kanban, quruntul, synarchy and hetoimasia run `build-test` under `if: always()` with `needs:` on the workers, and fail it unless the workers reported the expected result (`success`; synarchy also accepts an expected `skipped` on `master` pushes). kanban explains why: a failed dependency *skips* a dependent job, and "a skipped check is not a failed one" to the drainer or branch protection. kanban's aggregate also refuses an empty `needs`, and `tools/test_ci_workflow.py` fails if a job is missing from `needs`. **moskophoros differs:** its `build-test` has no `needs`; it starts with the run and polls the run's own jobs through `tools/validation/ci.py`, so its check appears at once. |
| Smallest analogue | quruntul (stdlib Python, `unittest`): one `test` worker plus the `build-test` aggregate, 49 lines, no install step. |
| Triggers | `push: branches: [master]` and `pull_request` in all five. kanban adds `workflow_dispatch`; moskophoros and hetoimasia add `edited` because they read a validation plan from the pull-request body. None runs CI on `labeled`/`unlabeled`. |
| Permissions | Top-level `contents: read` (kanban, hetoimasia). moskophoros adds `pull-requests: read`, `packages: read` and `actions: read` for its planner; quruntul declares none and so inherits the repository default. Test and aggregate jobs only read; the one write is synarchy's `resolve-image` (and its called `ci-image.yml`), which holds `packages: write` to publish the CI image. |
| Concurrency | moskophoros and synarchy cancel superseded pull-request runs but give each `master` push its own group. synarchy #1490 records why: with a shared group, 39 of 40 `master` runs ended `cancelled` and a breaking commit never reported its own failure. kanban's `ci-<PR number or ref>` group cancels `master` pushes too. |
| Python | kanban uses the runner's `python3` with no setup step; quruntul uses `actions/setup-python@v5` over 3.11 and 3.13 on Ubuntu and macOS; moskophoros pins `actions/setup-python@v7` with `"3.13"`. |
| Action versions | Newest majors in use: `actions/checkout@v7` (moskophoros, hetoimasia) and `actions/setup-python@v7` (moskophoros). |
| Timeouts | Most jobs set `timeout-minutes`: Python worker 20 (kanban); aggregate 5 (kanban, synarchy) or 10 (quruntul). quruntul's `test` job sets none and falls back to GitHub's 360-minute default. |
| File | `ci.yml`, workflow `name: CI` (kanban, moskophoros, synarchy; quruntul `ci`). |

A second read-only comparison of the same revisions reached the same reading,
corrected three rows above (moskophoros's aggregate, synarchy's write, quruntul's
timeout), and added these points, adopted in the design below: no
`pull_request_target`, no secrets and no write permissions; no path filters that could leave the
required check unreported; bounded timeouts; action versions verified at
implementation time; `review-gate.yml` left independent; and none of the
Haskell, engine/GPU, container-image, registry, release, service-lifecycle or
receipt-reuse machinery of the larger repositories. It also checked GitHub's
[workflow syntax reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
and [Python build-and-test tutorial](https://docs.github.com/en/actions/tutorials/build-and-test-code/python).

### The drainer (kanban `tools/drain_prs.py`, `f5469d9`)

- `load_gate_config` reads `.drain-prs.json` from the drainer's checkout. If
  the file is absent, the defaults apply: `build-test` and `review-approved`.
  A key set to `null` disables that gate.
- **How plateia merges.** Plateia has no drainer service (`launchctl print`
  finds no `com.coghex.drain-prs.coghex.plateia`, 2026-10-04). The manager's
  `merge-pr` script starts a fresh `drain_prs.py --path … --repo … --pr N`
  process for each merge, so every merge rereads `.drain-prs.json` from the
  local checkout. PR #4 added the file at 2026-10-04T01:22:13Z and PR #1
  merged under it at 01:22:36Z, with no restart.
- **Read-only gate check.** Every drainer invocation logs `Required checks:
  <ci>, <review>` (or `ci disabled`) right after loading the config.
  `drain_prs.py --path <checkout> --repo coghex/plateia --once --dry-run`
  prints it without merging, labelling or writing state, which makes it the
  read-only way to confirm the gates in effect.
- **A separate, conditional concern.** A long-running polling drainer reads
  the config once, at start-up. Plateia has none; if one is ever installed,
  restarting it after a config change is an operator matter, not part of this
  design.
- A configured check that has reported nothing for a head gets a 10-minute
  grace (`MISSING_CHECK_GRACE_SECONDS = 600`); afterwards the pull request is
  skipped, "no failure, no cooldown, no merge", until the check reports.
- A failed required CI check gets one automatic rerun; if it still fails, the
  drainer records `checks_failed` and does not merge.

## Desired experience

A solver opens a pull request; within a minute or two it shows `build-test`
next to `review-approved`. A failure names the failing test in the job log.
After a merge, the `master` push run shows whether `master` itself is green,
which is the signal the vision's "the default branch is failing its checks"
notification (V-9) will read later. Nothing about CI involves a secret, a write
permission, a schedule or a service.

## Scope

### In scope

- One workflow file, `.github/workflows/ci.yml`, that runs the package's tests
  and publishes `build-test`.
- Replacing the drainer's CI opt-out with an explicit `build-test`
  requirement once `build-test` reports, in a safe order.

### Out of scope

- Deferred follow-ups (D-6): branch protection or rulesets on `master`
  (Q-4), macOS coverage, and finaliser-leak detection (Q-3).
- Testing the review gate itself (quruntul and moskophoros have
  `tools/test_review_gate.py`; plateia's copy has none). A separate change.
- Lint, type checking, coverage, packaging or release jobs.
- Test selection, path filters, CI images, caches, validation planners or
  receipt reuse: nothing here is slow enough to need them.
- `review-gate.yml`: it stays a separate workflow publishing
  `review-approved`; CI neither reads nor changes it.
- Any change to `docs/plateia_design.md`, to Q-16, to the drainer, or to any
  other repository.
- Starting, stopping or installing a drainer service. Plateia has none today,
  and CI-2 does not need one.

## Decisions

Only the owner's own decisions are recorded here.

### D-1. Follow the CI convention of the owner's other projects

Owner request, 2026-10-04 01:26: implement CI, first checking for an existing
design and, if there is none, drafting one that uses the CI convention of the
owner's other projects. No CI design existed. Consequence: the design below adopts the
five repositories' shared shape and cites where it departs from one of them.

### D-2. The required CI check is named `build-test`

AGENTS.md, Workflow (owner's authority document): every change passes
`review-approved` "and, once CI exists, `build-test`". It is also the
drainer's default `required_ci_check`.

### D-3. Python 3.10 and 3.14 on `ubuntu-latest` only (2026-10-04)

Resolves Q-1. The `test` matrix runs Python `"3.10"` (the package's promised
floor) and `"3.14"` (the version in use), on `ubuntu-latest` only: "linux only
coverage". macOS is deferred (D-6).

### D-4. Two pull requests; CI-2 sets the CI check explicitly (2026-10-04)

Resolves Q-2. CI-1 adds the workflow. CI-2 starts only after the hosted
`build-test` of CI-1's merge commit on `master` has succeeded. CI-2 does not
delete `.drain-prs.json`: it replaces `null` with an explicit
`"required_ci_check": "build-test"` and leaves `required_review_check` unset,
so the drainer's default `review-approved` applies. Rejected: one combined pull
request (requires a check before `master` has ever reported it), and deleting
the file (relies on an implicit default instead of stating the gate).

### D-5. The design below is accepted as adjusted (2026-10-04)

The owner accepted P-1 to P-6 below, as adjusted by D-3, D-4 and D-6: the
stdlib `unittest` suite; a stable `build-test` aggregate; `contents: read`
only; pull-request and `master`-push triggers; bounded timeouts; empty-run
protection; cancellation of superseded pull-request runs with a distinct run
for every `master` push; `review-gate.yml` unchanged. Each pull request
merges only when its latest head has a successful hosted `build-test` **and**
`review-approved`, even while the CI gate is still disabled. After CI-2, the
config on `master` and the effective gates are verified with a read-only
`drain_prs.py --once --dry-run`.

### D-6. Deferred follow-ups (2026-10-04)

Finaliser-leak detection (Q-3), macOS coverage, and branch protection (Q-4)
are left out of this arc, each for a separate change if the owner wants it.

## Accepted design (D-5)

The P-identifiers are kept stable; each section is accepted as adjusted by
D-3, D-4 and D-6.

### P-1. One `test` worker and a `build-test` aggregate

Adopt quruntul's shape with kanban's aggregate script (the `needs` form shared
by four of the five, not moskophoros's polling form, which exists to serve its
planner). Workflow
`name: CI` in `.github/workflows/ci.yml`; two jobs:

- **`test`**: checks out the commit, sets up Python (P-3), runs the tests
  (P-4). `timeout-minutes: 10`.
- **`build-test`**: `if: always()`, `needs: [test]`, `timeout-minutes: 5`, no
  checkout. Fails unless every entry of `toJSON(needs)` is `success`, and
  fails if `needs` is empty, using kanban's script unchanged.

Required-check name: `build-test`, the job's id with no `name:` override, so
the context GitHub reports is exactly `build-test`. The D-3 matrix
(`python-version: ["3.10", "3.14"]`, `runs-on: ubuntu-latest`) lives only on
`test`, whose legs report as `test (3.10)` and `test (3.14)`; nothing
requires them by name.

Why the aggregate rather than one job named `build-test`: with a single job
the check name is right today, but the first second job (a lint, a second
package) would either rename the required check or be ungated, and every
other repository already has the aggregate. Cost: one extra runner start of a
few seconds. **Rejected alternative:** a single `build-test` job, simpler by
one job, but it departs from D-1 and has to be restructured later.

### P-2. Triggers, permissions and concurrency

```yaml
on:
  push:
    branches: [master]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || format('push-{0}', github.sha) }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

- Default `pull_request` types (opened, synchronize, reopened): a new head
  reruns CI; label changes do not, so review rounds cost no CI.
- `pull_request`, never `pull_request_target`: the run gets a read-only token
  and no secrets, even for a pull request from a fork of this public
  repository.
- No `paths` or `paths-ignore` filters: a filtered-out run would leave
  `build-test` unreported, and the drainer would skip the pull request after
  its grace period.
- No `edited`: nothing is read from the pull-request body.
- No `workflow_dispatch` (kanban only); the Actions "re-run" button covers a
  rerun. The owner can ask for it.
- `contents: read` is all `actions/checkout` needs; no job writes, reads
  another run, or uses a secret (unlike synarchy's image job, nothing here
  publishes). GitHub also grants `metadata: read` implicitly; that is
  expected. Declared explicitly, unlike quruntul, so the
  workflow never depends on the repository's default token setting.
- Concurrency follows moskophoros/synarchy, not kanban: a superseded
  pull-request run is cancelled, and each `master` push keeps its own run, for
  the reason synarchy #1490 records.

### P-3. Python and dependency setup

`actions/checkout@v7` and `actions/setup-python@v7` (the newest majors in use,
moskophoros), re-verified against the actions' releases when CI-1 is
implemented. There is no dependency step: the package is standard-library
only, so no `pip install`, requirements file or cache. Versions and operating
system: D-3.

### P-4. The test step and its empty-run guard

Run the documented command from the repository root, with
`PYTHONDONTWRITEBYTECODE=1` and `PYTHONPATH=packages` as environment, and `-v`
so the log names each test.

- **Empty-run guard (CI-1):** fail if the output does not report
  at least one test run (as kanban's packaging gate does), so Python
  3.10/3.11 cannot pass an empty discovery. This changes no test policy; it
  only stops the check from passing when nothing was tested.
- **Finaliser-leak guard (deferred, D-6):** failing the job when stderr
  contains `Exception ignored` would make the command's `ResourceWarning`
  intent hold for finaliser leaks. That is a change to test policy and may
  fail code that passes today, so it is not part of CI-1.

### P-5. Failure behaviour

- A test failure, error, timeout or empty run fails `test`; `build-test`
  then fails. A `test` leg that is cancelled or skipped also fails
  `build-test`.
- A whole run cancelled because a newer push superseded it may report
  `build-test` as `cancelled`, or leave no fresh result for that head at all.
  Either way it is never a success. The newer run reports for the new head;
  until it does, the drainer treats the check as pending or missing.
- The drainer reruns a failed `build-test` once, then stops with
  `checks_failed` and does not merge. The fix is a new commit, which reruns
  CI; it is never a bypass, a label change or a config edit.
- A failing `master` push run does not undo the merge. It is visible on the
  commit and is the input for the vision's failing-default-branch
  notification; repairing it is ordinary work.
- `fail-fast: false` on the matrix, as quruntul does, so one failing version
  does not hide the result of the other.

### P-6. Switch-over order for `.drain-prs.json`

While `.drain-prs.json` disables the CI gate, the drainer would merge CI-1
and CI-2 on review alone even if their own `build-test` failed. This order
does not rely on that: both are held to the check by hand before it is
enforced (D-5).

1. **CI-1 adds the workflow.** A `pull_request` run uses the workflow file
   from the pull request's merge commit, so CI-1's own pull request reports
   `build-test`. **Before CI-1 is merged, its latest head must have a
   successful hosted `build-test` and `review-approved`**; the run link and
   head SHA go in the pull request. A known failing workflow is never merged
   on review alone.
2. **CI-1's `master` run succeeds.** The hosted push run for CI-1's merge
   commit reports `build-test` = success, and the negative proofs in
   Verification strategy are recorded. CI-2 does not start before this (D-4).
3. **CI-2 sets the check explicitly.** `.drain-prs.json` changes from
   `{"required_ci_check": null}` to `{"required_ci_check": "build-test"}`,
   leaving `required_review_check` unset so its default `review-approved`
   applies, as in kanban's explicit file. **Before CI-2 is merged, its latest
   head must have a successful hosted `build-test` and `review-approved`**,
   with the run link and head SHA in the pull request, even though the gate
   it switches on is not yet in effect.
4. **The gate takes effect at the next merge.** Once CI-2 is on `master` and
   the local checkout is fast-forwarded, the next `merge-pr` run's fresh
   drainer process reads the new config. Confirm the file on `master` and the
   effective gates with the read-only check (`--once --dry-run`), which must
   report `Required checks: build-test, review-approved`.
5. **Pull requests without the check.** Any open pull request whose head
   predates step 1 and has not been pushed since has no `build-test`; after
   the 10-minute grace the drainer skips it until a push or a branch update
   runs CI. Before CI-2 merges, list the open pull requests and note which
   lack the check.

## Resolved and deferred questions

No question remains open for this arc.

### Q-1. Which Python versions and operating systems should `test` run on?

Resolved by D-3. Options were 3.10 and 3.14 on Ubuntu (recommended), the same
plus macOS as quruntul does, or one pinned version. The owner chose Linux-only
coverage.

### Q-2. Should the switch-over take two pull requests, and delete the file?

Resolved by D-4: two pull requests, and an explicit
`"required_ci_check": "build-test"` rather than deleting `.drain-prs.json`.

### Q-3. Should CI fail on finaliser leaks that today's command lets through?

Deferred by D-6. The documented command's `-W error::ResourceWarning` does not
fail a leak reported from a finaliser (verified above). A later change may add
an `Exception ignored` guard together with the matching `skill/OPERATING.md`
command, and fix or report any leak it exposes. CI-1 runs the command as
documented.

### Q-4. Should `master` get branch protection requiring both checks?

Deferred by D-6. kanban protects `master` with `build-test` and
`review-approved`; quruntul and moskophoros rely on the drainer alone, as
plateia does. It is a repository setting, not a pull request.

## Verification strategy

Evidence the owner can check, in order. Each item cites the hosted run's link
and the head SHA it ran on; a local run is supporting evidence only and never
stands in for the hosted check.

- **CI-1 pull request:** the latest head's checks show `test (3.10)`,
  `test (3.14)`, `build-test` and `review-approved`, all success; the `test`
  logs name the tests run and the count matches the local run (60 at
  drafting).
- **Negative proofs, before cutover, on a throwaway branch of CI-1 that is
  never merged:** a deliberately failing assertion makes `test` and
  `build-test` fail; pointing discovery at an empty directory fails on every
  Python leg, including 3.10; a cancelled or failed `test` leg fails
  `build-test`. Each with its run link and head SHA.
- **`master`:** the push run for CI-1's merge commit reports `build-test` =
  success, and a later push does not cancel it.
- **Permissions:** the workflow declares only `contents: read`; the run's
  "Set up job" token-permission list shows no `write` grant (GitHub's
  implicit `Metadata: read` is expected).
- **CI-2:** its latest head had a successful hosted `build-test` and
  `review-approved` before merging; `.drain-prs.json` on `master` and in the
  fast-forwarded local checkout reads exactly
  `{"required_ci_check": "build-test"}`; `drain_prs.py --once --dry-run`
  against that checkout reports `Required checks: build-test,
  review-approved`; the next merged pull request had a successful
  `build-test` on its merged head.

## Delivery plan

### CI-1. Add the CI workflow that publishes `build-test`

- **Outcome:** every pull request and `master` push reports `build-test`.
- **Scope:** `.github/workflows/ci.yml` per P-1 to P-5, with the D-3 matrix.
  Merged only after its latest head has a successful hosted `build-test` and
  `review-approved` (P-6 step 1). No change to package code,
  `skill/OPERATING.md` or `review-gate.yml`.
- **Phase:** 1
- **Depends on:** none
- **Ordering:** critical path
- **Relevant decisions:** D-1, D-2, D-3, D-5, D-6
- **Acceptance signals:** the CI-1, negative-proof, `master` and permissions
  bullets in Verification strategy.
- **Out of scope:** `.drain-prs.json`; the D-6 follow-ups (finaliser-leak
  detection, macOS coverage, branch protection); review-gate tests.
- **Open questions:** None

### CI-2. Make the drainer require `build-test`

- **Outcome:** the drainer refuses to merge a pull request without a
  successful `build-test`.
- **Scope:** change `.drain-prs.json` from `{"required_ci_check": null}` to
  `{"required_ci_check": "build-test"}`, leaving `required_review_check`
  unset (D-4). The pull-request body records CI-1's successful `master` run
  and the list of open pull requests lacking the check. Merged only after its
  latest head has a successful hosted `build-test` and `review-approved` (P-6
  step 3). The gate applies from the next `merge-pr` run after the merge,
  confirmed with the read-only check.
- **Phase:** 2
- **Depends on:** CI-1, and a successful hosted `build-test` on CI-1's
  `master` merge commit
- **Ordering:** critical path
- **Relevant decisions:** D-2, D-4, D-5
- **Acceptance signals:** the CI-2 bullet in Verification strategy.
- **Out of scope:** deleting `.drain-prs.json`; installing, starting or
  restarting a drainer service; branch protection.
- **Open questions:** None
