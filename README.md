# review-ready

A Claude Code skill. Run it when development on a branch is finished and
before you open the pull request: it turns a large, AI-written branch into a
small series of commits a human will actually read.

```
Before   1 commit, 446 reviewable lines, 15 files, a notebook and a lockfile
After    2 commits, 40 reviewable lines, 3 files, each commit building on its own
```

**[Field guide](https://garytang32.github.io/review-ready/)** — the full record: every rule and the reason it exists, every detector by name, and the development history.

## The problem

Generation got fast. Review did not. A coding agent will hand you 5,000 lines
in one commit, and every one of them is plausible. The reviewer has the same
attention they had ten years ago, so the branch either sits unread or gets an
approval nobody can defend.

The constraint is not new, and neither is the answer. `SubmittingPatches` and
`CodingStyle`, as they stood around 2000, were written for exactly this: one
human, finite attention, who has to say yes. This skill applies those rules to
a branch you already have.

## What it does

1. **Audits** the branch: size against noise, behaviour switches, security and
   data-correctness risks, public signatures that changed, new code that
   duplicates the tree, comments that cost more than they pay for. Then it
   **maps the code around the diff**: for each changed function, its call
   chain, its callers and what it calls, plus the additions used once, never
   called or already in the tree. The rewrite works from those relations
   rather than reading files one at a time.
2. **States the problem** the branch solves, in one paragraph, and checks the
   diagnosis against the old code.
3. **Prunes** what the problem does not need. Everything dropped is recorded,
   tagged as noise or as behaviour, never deleted silently.
4. **Rewrites** what stays to the shortest code that does the job, starting
   from the base file rather than trimming the agent's draft, and only inside
   functions the branch already changed. **The series must keep at most half
   the branch's reviewable lines**, per file and in total. The builder
   measures this and refuses to land a series that misses it, unless each
   file over the line carries a written reason.
5. **Splits** it into a bisectable series, or into stacked PRs when the work is
   still large after the rewrite, and writes a real commit message for each patch.
6. **Reviews** it as a hostile stranger who has not seen your reasoning, and
   who must walk your failing case through the code by hand before an ACK.
7. **Lands** it on your branch, once, with a one-command undo.

## Your branch is safe

The whole pass happens *beside* your branch. The series is built on a detached
HEAD in a private worktree outside the repo and kept in a ref; no branch is created or deleted;
your checkout is never touched. If the session dies half way, you are exactly
where you started.

Only after the review does one command move your branch, and it writes
`UNDO.md` before it prints anything:

```
git -C <repo> checkout -B <branch> refs/review-ready/original/<branch>
```

Untracked files — your `.env`, your data, your notes — are never touched.

## Install

```bash
git clone https://github.com/GaryTang32/review-ready ~/.claude/skills/review-ready
```

That is the whole install. Claude Code picks up skills from
`~/.claude/skills/`. To check it landed:

```bash
python3 ~/.claude/skills/review-ready/scripts/build_series.py --self-test
```

**Requires** Python 3.9+ and git 2.31+. Nothing to pip install: the scripts use
the standard library only, and never import the code they analyse.

## Use

Ask for it in your own words — it triggers on the intent, not on a command:

> I've finished `feature/dedupe-loader` and I'm about to open the PR. Codex
> wrote most of it and it's a mess. Clean it up so the PR isn't chunky.

Four modes, chosen from what you ask for:

| Mode | When | What you get |
|---|---|---|
| `pre-pr` | "before I open the PR", "too big", "split it" | the series, built beside your branch, reviewed, then landed |
| `write` | "implement this the kernel way" | the same, planned as a series before any code exists |
| `review` | "would this pass", "ACK or NAK" | a verdict only; nothing in the tree changes |
| `audit` | "read the whole repo", "is this codebase clean" | a repo map, an audit report, and a refactor series behind characterization tests |

## What it checks

72 line detectors, 10 syntax-tree checks and one file-name check across
security, data correctness, cost, concurrency, logging and tests; 33 behaviour
switches; and function-level measurement of length, nesting, density, loop
cost and return shape. Every detector has a line it must match and one it must
not. The full list is in the [field guide](https://garytang32.github.io/review-ready/).

In audit mode the whole-repo map plans the trim before anything is rewritten:
what no entry point reaches (grouped into dead subtrees), functions that are
copies of each other by token similarity, and a rewrite order that takes
dependencies first.

It is opinionated about things that are easy to get wrong and expensive to
find later: look-ahead leakage in a feature, a merge with no `validate`, a
missing value turned into a number, a naive clock in market code, SQL built
from a string, a secret in a fixture, a test that skips when a database is
absent and reports green.

## Everything it claims, it checks

| Suite | Cases |
|---|---|
| `build_series.py --self-test` | 31 whole situations in scratch repositories |
| `risk_detectors.py` | 72 detectors + 24 syntax-tree programs |
| `switch_detectors.py` | 33 detectors + 3 commit-level rules |
| `code_metrics.py` | 39 |
| `comment_audit.py --self-test` | 17, eight of which must find nothing |
| `repo_map.py --self-test` | 46: the focus map and the whole-tree map, each against a scratch project with one planted case per rule |
| `repo_index.py --self-test` | 14: the stored graph, its queries and its retrieval, including a match that must not be offered |

The builder is the only script that can move anything of yours, so its tests
are whole situations rather than inputs: each one creates a repository, puts
it in a state that once went wrong, and asserts on the exit code, the output
and the resulting git state. 24 of the 31 are the residue of a real defect
found by review or by a run; the latest two are a package that the series
itself creates, and a check that could see the checkout's configuration in
the directory above it. Six pin the size gate: a verbatim series cannot
land, a rewritten one can, keeping more than half needs a stated reason,
and a series that joins statements, runs lines together or deletes blank
lines to halve its line count fails. One holds write mode to each patch's
budget.

## Documentation

The [field guide](https://garytang32.github.io/review-ready/) is the full record: every file and what it
is for, every detector by name, the doctrine behind each rule, and the
development history — three rounds of adversarial review, what each found, and
what changed as a result.

## Status and limits

Graded 21/21 and 26/26 on two fixtures with planted defects, and run against a
7,760-line production tree. Honest limits, stated in full in the guide:

- **Python only.** The taste rules generalise; the scripts do not.
- **The size gate counts lines, not quality.** It fails a Python file that
  reached its count by joining statements onto one line or running lines
  past 120 characters, but dense, clever code of normal shape still passes.
  The maintainer pass is what NAKs that ("clever"), and `size_ok` exists for
  files that truly cannot shrink. This is not hypothetical: on the first
  real agent-written branch the pass was run on, the first rewrite reached
  half per file by chaining calls, and the maintainer pass rejected it as
  packed. Two reviews later the series kept 55% of the branch's lines: the
  module at three quarters of its length with four bug fixes the branch did
  not have, and twice the tests, because the branch's own missed all four.
  Half is the target for padding, not for a branch that is short and wrong.
- **Call resolution is a heuristic**, so every dead-code claim is conservative
  and the count of unresolved calls is printed beside it.
- **It cannot judge whether your problem statement is right.** If the spec is
  wrong, you get a clean implementation of the wrong thing.
- **The maintainer pass is a model reading patches.** Better than the author,
  because it lacks the author's context. Not a domain expert who knows your data.
- **Nobody has run the rebuilt series but the tests.** Where an entry point
  runs offline it is run before and after and the outputs compared; otherwise
  the hand-back says so, and the first real execution is yours.
