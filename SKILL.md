---
name: review-ready
description: The pre-PR pass. Run it when development on a branch is finished and before the pull request is created, to make the PR small, obvious, and ACKable by a Linux kernel maintainer circa 2000. Prunes what the problem does not need, rewrites what stays to the shortest code that does the job (the series must keep at most half the branch's reviewable lines, or say why per file), splits it into bisectable commits with real messages (stacked PRs only for what is still large after the rewrite), writes the PR description, reviews it as the maintainer, then moves the branch onto the series with a one-command undo. Checks security, data correctness and look-ahead leakage, pandas and sqlite pitfalls, algorithmic smells, comments, tests, PEP 8/20/257/484 and behaviour switches. Use when a branch or PR is too big, chunky or verbose, came from Codex, Claude or Copilot, or needs cleaning, shrinking, refactoring, splitting or a maintainer-style review before a PR ("kernel quality", "no AI slop"); or when a whole repo needs auditing or restructuring so an outsider can read it. Not for reviewing one function, one file or an uncommitted diff.
---

# Review Ready

**When this runs: development is finished, the pull request does not exist
yet.** The branch works. This skill makes it reviewable. It builds a shorter,
clean series beside the branch, has it reviewed, then moves the branch onto
it, so the PR that opens is small, obvious, and says why.

The bar is a maintainer in 2000 reading each patch as a plain-text email in
`less`, with no IDE, no CI, no linter, no ticket link and no patience. If
they cannot see what a patch does, why, and whether it is right in one pass,
it is a NAK. A coding agent ignores the constraint behind that bar: a human
with finite attention has to say yes.

**The main job is less code.** Agent-written code is usually two to four
times the length the problem needs, and most of the excess is inside hunks
the problem does need. Splitting and pruning cannot remove it; only a
rewrite can. So the pass has one number it must hit: **the series keeps at
most half the branch's reviewable lines**, per file and in total. The
builder measures that and will not land a series that misses it, unless
each file over the line has a written reason. Everything else in this file
serves that number or keeps the rewrite safe.

Scripts live in `~/.claude/skills/review-ready/scripts/`; `$S` below means
that directory. They read git and a private worktree and import nothing from
the code under analysis. The detector modules and the builder test themselves
(`build_series.py --self-test`: thirty-four situations in scratch repos;
`reasons.py --self-test`: what counts as a change and as its reason, 25 cases;
`repo_map.py --self-test`: the focus map and the whole-tree map, 46 cases;
`repo_index.py --self-test`: the stored graph and its retrieval, 14 cases;
`review_site.py --self-test`: the review page, 17 cases).

## References: read the one the moment calls for

Read the three marked *always*. Read the rest as the audit's findings or the
diff name them; at step 1, read behaviour-switches.md first when the diff
touches config, data, defaults, entry points or the clock.

| File | Read it |
|---|---|
| [minimal-code.md](references/minimal-code.md) | *always*, before step 3: the rewrite procedure, the shrink moves, the size gate |
| [maintainer-rules.md](references/maintainer-rules.md) | *always*, before the first prune: the bar and its reasons |
| [review-checklist.md](references/review-checklist.md) | *always*, before the maintainer pass: the NAK list and the verdict format |
| [building-and-landing.md](references/building-and-landing.md) | at step 0 and whenever a step points to it: the work directory, where tests run, the focus map, overlays, the plan, the build's output, landing, the hand-back |
| [commit-message.md](references/commit-message.md) | before writing the first message |
| [comments.md](references/comments.md) | when the audit reports comment findings, and in every repo audit |
| [python-idiom.md](references/python-idiom.md) | when rewriting Python: what PEP 8, 20, 257 and 484 decide, and where they yield to the kernel rules |
| [python-language.md](references/python-language.md) | when a diff touches types, dataclasses, enums, `__eq__`, generators, exceptions and guard clauses, sorting, paths, properties, async, context managers or `pyproject.toml` |
| [clean-code-examples.md](references/clean-code-examples.md) | when a hunk is ugly and you cannot say why: worked bloated-versus-clean pairs |
| [algorithm-smells.md](references/algorithm-smells.md) | when the audit reports a `perf` risk, a duplicate, or a dense function; or a loop changed |
| [behaviour-switches.md](references/behaviour-switches.md) | at step 1, whenever config, data files, defaults, entry points or the clock are touched: it says which other references the change needs |
| [data-correctness.md](references/data-correctness.md) | whenever a changed function takes or returns rows, a frame, SQL, dates, missing values, features or a model: look-ahead leakage lives here |
| [security.md](references/security.md) | when the audit reports a `security` risk, or the diff touches SQL, subprocess, deserialization, paths, secrets or HTTP |
| [testing.md](references/testing.md) | before writing or judging any test |
| [engineering-sops.md](references/engineering-sops.md) | at step 1 for who outside the repo consumes this code; then for trailers and reposts, concurrency, logging, public API changes, changelogs, and the policy on external tools |
| [repo-audit.md](references/repo-audit.md) | before any audit-mode run |

## Modes

| Mode | Trigger | What you produce |
|---|---|---|
| `pre-pr` (default) | "clean up my branch", "before I open the PR", "too big", "split it" | a pruned, rewritten, bisectable series built beside the branch, reviewed, then landed on it; plus the PR description and the verdict |
| `write` | "implement this the kernel way" | the same, planned as a series before any code exists, on a new branch |
| `review` | "would this pass", "ACK or NAK" | a verdict only; nothing in the tree changes |
| `audit` | "read the whole repo", "restructure", "is this clean" | repo map, audit report, target tree, and a refactor series behind characterization tests |

Say which mode you picked. "Make my branch pass review" is `pre-pr`: they
want it fixed, not judged. A request naming no branch and no PR (a function,
an uncommitted diff) is `review`, because `pre-pr` rewrites history.

## Two ways to run: interactive and automatic

`pre-pr`, `write` and `audit` run one of two ways. Say which, and set it in
the plan as `"run": "interactive"` or `"run": "auto"`.

**Interactive** (the default when the owner is in the conversation): the
owner sees every change and adopts it before anything moves. Stop at four
points, and nowhere else:
1. after step 1: show the problem statement's first paragraph; the owner
   agrees or corrects it. Ask in the same question which tests must pass
   whatever the rewrite does, and put them in the plan's `must_pass`;
2. after the first build: show every change with its reason. Generate the
   page (`review_site.py $W --out $W/review.html`) and send it; ask about
   the changes four to a question, each option naming the function, what
   happens to it and the reason. The owner may also mark them on the page and
   paste back what its "Copy decisions" button gives. Write the answers to
   `$W/decisions.json`: `"adopt"`, `"reject"`, or their words for an edit.
   Put a rejected change back the way the branch had it, make an asked-for
   edit, rebuild, and show only what changed again;
3. after the maintainer pass: show the verdict and each NAK with the fix you
   propose; fixes that change behaviour are decided like any other change;
4. before landing: the builder refuses while any change waits for a
   decision, so landing is a question only when every change is adopted.

**Automatic** (when the owner asks to be left out of it: "just do it", "run
it end to end", "tell me when it's done", or a scheduled or unattended run):
no questions. The steps run in order to the end: the problem statement is
derived from the request and the branch; the maintainer pass is a fresh
subagent, up to three rounds; the series lands when both gates pass, unless
the audit found a second author or a published branch, which wait for the
owner. Every question a step says to ask becomes an open question in the PR
description, verbatim, and every fact you would have asked for is written as
the weaker sentence that is true without it. `must_pass` holds every test the
request names, and every test of the branch that pins a guard at a boundary
(malformed input, NaN, an output format other programs read). Never a guess dressed as a
finding. At the end, one message: the hand-back (step 8) with the review
page, sent as a push notification as well when the harness has one. Nothing
is pushed and no PR is opened in either way unless the owner asked.

Both ways hold the same bar: the size gate and the reason gate decide what
can land, not the way the pass was run.

## Ground rules

- The reader's time is the scarce resource, not the author's and not the
  tool's. Every choice optimises for the person reading the patch.
- **Needed is not the same as as long as written.** "The problem needs this
  function" justifies the function, not its forty lines. Rewrite before you
  split, and stack only what is still large after the rewrite: a stack of
  long PRs is the same review in more sittings.
- One logical change per patch: a bug fix, a cleanup, an API change and a new
  user of that API are four; one change across many files is one.
- Never mix movement with modification, rename with logic, or whitespace with
  anything: nobody sees a one-line logic change inside a 300-line reformat.
- Every patch builds and passes its tests alone, or nothing can be bisected.
- **The series never shows its own history.** New code lands clean the first
  time: no patch introduces a problem a later patch fixes, no "address review
  comments" commit. The PR opens as the first posting.
- What the problem does not need is recorded in `deferred.md`, not deleted
  silently.
- **Every change has a reason.** Each function, method or class the series
  adds, drops or rewrites relative to the branch, each module's own code that
  changes, and each other file that differs is named, with why, in
  `deferred.md`, in the message of the patch that makes it, or in the plan's
  `reasons`. The builder lists the changes (`reasons.md`) and refuses to build
  or land a series with one that has none.
- **No test is lost silently.** Every test already on the target passes at
  every patch, or the build stops. Each of the branch's own tests that fails
  against the series is a change like any other and needs its reason; a test
  file that does not even load counts every test in it. A test in the plan's
  `must_pass` may not fail at all, whatever reason is offered: the builder runs
  it, as the branch has it, against the series, and refuses the build.
- Prefer deleting to modifying, and modifying to adding; search before
  writing. A second copy of an existing function is "we already have that".
- **A risk is never fixed silently.** A security hole, a look-ahead leak, a
  silent NULL coercion, a behaviour change: each is its own patch with its
  own message and test, or an open question in the PR description. A risk
  fix is never cut for length.
- **Every fact in a message has a source**: the user, the ticket, or a
  command whose output you hold. Otherwise ask once, or write the weaker true
  sentence.
- **The branch is never edited, and moves once.** Your versions of files live
  under the work directory; the builder assembles them beside the branch; the
  branch moves at step 8, after the verdict, with the undo written to disk.
- `Signed-off-by` means "I have read this and can defend every line", which
  only the owner can say. The series is handed back unsigned; sign for them
  only when they asked you to.

## Step 0: establish the ground

Set up the work directory, find the target, check what the tests open, and
run the audit, all as in building-and-landing.md sections 1 and 2:

```bash
python3 $S/audit_diff.py --repo <repo> --base <target> --head <head> \
    --check-tip "<test command>" --import-check <pkg> --md $W/audit-before.md
```

Its Ground section decides whether the pass can start: the fork point and
target, a test merge with a target that has moved (`CONFLICTS` stops the
pass), and more than one author or a published branch (both change landing).
Its size table is the baseline the size gate measures against.

**Then map the code around the diff**, before reading any of it:

```bash
python3 $S/repo_map.py --repo <repo> --focus <target>..<head> --md $W/focus.md
```

It reads the head commit from git, never the checkout. For every function
the branch adds or changes it gives the call chain up from it, each caller
with the line that calls it, what it calls, and the loops the branch wrote.
It leads with the additions to act on: used once (inline them), never
called, called only by tests, already in the tree, and classes with one
method. It maps the neighbourhood, not the repo: steps 2 and 3 still change
only what the branch wrote. Its last list is retrieval, not matching: for each
function the branch adds, the code already in the tree on the same subject,
found by the words the two share. That is where "we already have one of
these" shows when nothing was pasted.

**Ask the tree instead of reading it.** `repo_index.py build --repo <repo>
--ref <target>` stores the target's graph and vocabulary in one SQLite file
under `.review-ready/`. Then `search "<words>"`, `about <file>:<function>`,
`callers`, `callees`, `reach` and `sql` answer one question each without
loading the tree into your context (building-and-landing.md section 1).

**On a big branch, do not read the diff top to bottom.** Read the audit and
the focus map, skip the noise classes entirely, then read files in order of
reviewable lines, new files last.

## Step 1: state the problem, the switches, and the data contract

Before touching a hunk, write `problem.md`: one paragraph on what is wrong or
missing in the tree today, how it shows (the failing case, the wrong number,
the missing capability), and what the change does about it. Present tense,
about the code, not about the author's journey. Then what the diff alone
will not tell a reviewer:

- **The call chain**, from the entry point down to the changed line: take
  it from the focus map, and check it by reading, since the resolver is a
  heuristic.
- **The switches**: every config key, environment variable, flag, default,
  data file, import-time effect, clock or seed that controls this behaviour,
  and the position each is in today (behaviour-switches.md).
- **The data contract**, when data is touched: grain and keys, units,
  timezone, what a missing value means, and what was knowable when
  (data-correctness.md).
- **Who outside the repo** imports this package or reads its tables, files
  and reports (engineering-sops.md section 5).
- **Is the diagnosis right?** Walk the failing case through the OLD code. A
  fix at the reader for bad rows a writer produces is a symptom patch; say
  where the defect is, even if the branch fixes it somewhere else.

This file filters every later decision, sets the contract the rewrite
targets, and seeds every commit message. A problem statement
reverse-engineered from a diff always matches the diff, so the owner must
agree with it. Their request stating what they asked for counts as that
agreement; otherwise show them the first paragraph and ask once.

## Step 2: prune

Start from the focus map's "act on these first" list: an addition nothing
calls, or only tests call, is the cheapest line to cut. Then walk every hunk
and ask whether the problem statement needs it. What goes:

1. Whitespace, reformatting, quote style, import reordering on lines not
   otherwise changed.
2. Drive-by renames and refactors. "While I was here" is a deferred item.
3. Abstractions with one user: a class with one method, a base class with one
   implementation, a wrapper that forwards, a config object for two
   parameters, a factory for one product.
4. Comments and docstrings that restate the code or the signature, carry its
   history, narrate the line below them, or outweigh the code they sit on
   (comments.md); and type hints on code this change does not otherwise touch.
5. Defensive code for states that cannot occur, `try/except` "just in case",
   broad excepts, anything that swallows an error.
6. Duplicates of something the tree already has. The audit lists copies and
   the focus map lists code on the same subject; read the original, reuse
   it, and say so in the message.
7. Tests that assert the implementation, tests of trivial code, tests that
   assert nothing, mocks of the thing under test.
8. Generated files, unrelated lockfile churn, notebook outputs, data files.
9. Debug prints, debug logging, TODOs, commented-out code.
10. Options, flags and config knobs nobody asked for.

Items 3, 5, 7 and 10 remove **behaviour**, not noise: the `except` you drop
may be absorbing a real vendor failure. So every dropped hunk gets a line in
`deferred.md`, tagged `noise` (by class, with a count: "whitespace, 212
lines") or `behaviour` (what it did, why it is out, whether it deserves its
own PR). The user decides later; you do not decide silently.

## Step 3: rewrite what stays to the minimum

This is the step that makes the PR short. Follow minimal-code.md section 1
for every file that survived the prune, largest first, with the focus map
open. Its single-use additions are the inlines (move 1), its callers' lines
show what is already guaranteed (move 2), and its duplicates are the reuses
(move 4):

1. Write the file's contract in five lines at most: what each changed
   function takes, returns and raises, and which tests specify it.
2. Write the change **starting from the base version of the file**, not by
   trimming the branch's version. Trimming keeps every layer the agent
   invented. Read the branch only to list the cases it handles.
3. Each case the branch handled that your version does not: back in its
   shortest form if the problem needs it, otherwise a `behaviour` line in
   `deferred.md`.
4. Apply the shrink moves (minimal-code.md section 2) until the file is at
   most half the branch's lines, or until what is left is all needed. In
   that case it goes in the plan's `size_ok` with the moves you tried.

Write each result as an overlay (building-and-landing.md section 3).
Shorter means fewer concepts, not fewer characters: no cramming, no
one-letter names, no clever one-liners.

**Scope: lines inside functions this branch's diff already changes, and
files it adds.** Anything else, however tempting, goes in `deferred.md`:
pre-existing weak tests, duplicates already on the base, a neighbouring
function's smell. That is audit mode's work. The builder refuses a file
the branch never touched unless the plan's `scope_ok` gives its reason.

While rewriting, fix what the audit flagged, reading the reference it names:

| What | Looks like | Reference |
|---|---|---|
| Shape | over two screens or three levels; a condition of four terms; a special case the right representation removes | clean-code-examples.md |
| Algorithm | `pop(0)` in a loop, a frame or string grown in a loop, membership against a list, a full sort to take three, a window recomputed per row. A performance rewrite is measured first and is its own patch | algorithm-smells.md |
| Language contracts | a `Dict[str, Any]` that wanted a `TypedDict`, strings that wanted an enum, a generator consumed twice, an exception stripped of its cause | python-idiom.md, python-language.md |
| Data | look-ahead in a feature, a merge with no keys or `validate`, a missing value turned into a number, a naive timestamp, chained assignment, a commit in a loop | data-correctness.md |
| Security | SQL built from a string, `shell=True`, `pickle` or `eval` of data the process did not write, a secret in source | security.md |
| Tests | one test per behaviour, the smallest that fails without it; one table, not six copies; none reaches the network or the operator's data | testing.md, minimal-code.md section 3 |

A rewrite that changes what a caller sees (a `None` return made to raise, an
`except` narrowed) is a behaviour change to code the owner ran and you did
not. It goes in `deferred.md`'s `behaviour` list and in the hand-back, even
when it is right. Where each fix lands:

- **New code from this branch**: in the patch that introduces that code.
- **Pre-existing code the branch modifies**: a preparatory patch, `No
  functional change.`, placed before the behaviour change it enables. Add a
  characterization test first only when nothing pins that code
  (repo-audit.md section 3); if that is too much for this PR, defer it.
- **A risk in pre-existing lines the branch's diff touches**: its own patch if
  small and certain, otherwise an open question. Never silent, never bundled.

## Step 4: split into a series, and size the PR

Order the patches so each makes the next smaller and more obvious:

1. Preparatory patches: pure moves, renames, extractions, characterization
   tests. Each says `No functional change.` and changes no assertion and no
   expected value. A patch that changes behaviour is never "prep".
2. The behaviour change, as small as it can be, with its tests in the same
   patch. A test in a later patch fails bisect in between.
3. New users of a new interface. A function lands in the patch of its first
   caller: code nothing calls is dead on `main`.
4. Cleanups the change enables.

**Dark-launch only when existing callers' behaviour flips** (behaviour-switches.md
section 4 says when). Then add the new path guarded off, and flip the
default in its own patch whose entire diff is the flip. A new capability
nothing calls yet, or a bug fix, needs no guard. A config or data file
never changes in the same patch as code, unless the line names code (an
entry point, a CI path, an import string); that line moves with what it names.

**Size the PR, not only the patch.** A patch is right when it can be reviewed
in fifteen minutes and described in one sentence. The PR has two budgets,
because there are two risks:
- **Edits to existing code** can break everyone who depends on it: about
  200 reviewable lines, rarely over six patches.
- **New self-contained files** with their tests, imported by nothing until
  the last patch, can break nobody: up to about 500 lines, one patch per
  unit.

**Past that, and only after the rewrite, a stack.** The plan's `"stack"`
cuts one series into PRs. Each slice must be mergeable alone. Open the first
PR only and list the rest under "Followed by", because seven dependent PRs
in one morning get silence. The size gate measures the whole series, so a
stack never replaces the rewrite.

Then write `$W/plan.json` (building-and-landing.md section 4). Do not build
yet: the builder needs every message.

## Step 5: commit messages, then build

Read commit-message.md, then write one file per patch in `$W/msgs/`:
- a subject of `area: imperative summary` in about sixty characters;
- a blank line;
- a body wrapped at 72 that says what is wrong in the tree, why this fix
  rather than the obvious alternative, what is deliberately out of scope,
  and how it was tested.

No `Signed-off-by:` line and no `---` line, because the builder commits the
file verbatim. Use `Fixes:` and the other trailers where they apply
(engineering-sops.md). If the repo has its own subject convention, use it
and keep the body rules. The audience is someone reading `git log` in five years,
without the PR. Then:

```bash
python3 $S/build_series.py $W/plan.json
```

The build never moves the branch (building-and-landing.md section 5). Read
what it prints:
- **the reason table** (`reasons.md`): every change and the sentence that
  justifies it; a MISSING row is a reason you owe, in `deferred.md`, the
  patch's message or the plan's `reasons`, before the series can land;
- **the test table** (`tests.md`): each of the branch's tests that fails
  against the series, with its reason, and every `must_pass` test's result;
  a MUST-PASS row means the rewrite broke what the owner said must hold:
  the fix goes in the series, never in the test;
- **the size table** (`size.md`): every OVER file goes back to step 3;
- **the residual**: every `-` line is in `deferred.md` or rewritten;
- **the original tests** run against the rebuilt code: every failure is
  explained.

A failed size gate fails the build, and the series cannot land. "It is
needed" is not a `size_ok` reason. A file marked CRAMMED got its line count
by joining statements or running lines together; write it out and shrink it
for real.

## Step 6: the PR description

Fill [templates/cover-letter.md](templates/cover-letter.md). It becomes the
pull request body:
- the problem and the approach, a paragraph each;
- a line per patch;
- the size before and after (from `size.md`);
- three lines on how the series was tested;
- a line per deferred item, `size_ok` reason and open question;
- for a stack, what it depends on and what follows.

It stands alone, because the reviewer cannot see `deferred.md`. Past a
screen, the series is too big, not the letter. No "Changes since" block on a
first posting.

## Step 7: the maintainer pass

Change roles. The maintainer gets exactly what review-checklist.md's first
paragraph lists: `problem.md`, the patches, the PR description,
`deferred.md`, `residual.patch`, `size.md`, `focus.md`, that checklist, and the tree
read-only. Not the plan, the conversation, or the reasoning that produced
the series. They read each patch top to bottom as email. Then they do what
three lines of context cannot:
- walk the failing case through the OLD code, then the new, and write both
  walks down;
- grep the tree for every caller of a changed signature;
- run the check at every commit;
- ask where each number came from;
- for the largest patch, sketch the shorter version they would accept.

They return, per patch, ACK or NAK with the objection quoted from the diff
and the fix required. **An ACK on a behaviour patch with no trace written
under it is not an ACK.** Use a fresh subagent where the harness allows it
(building-and-landing.md section 6).

Fix every NAK, rebuild, and review again. Stop when every patch is ACK, or
when the remaining NAKs need a decision from the user; those become open
questions. **Three rounds is the cap.** NAKs still standing at round three
mean the problem statement or the split is wrong: go back to step 1 or 4,
and tell the user which.

## Step 8: run it, land it, hand it back

Run the entry point on the original and on the series where it runs
offline, write `defence.md`, land with `build_series.py $W/plan.json
--land`, and hand back. Each is in building-and-landing.md section 7 and 8.
Landing refuses a series that failed the size gate or the reason gate, and,
in an interactive run, one with a change the owner has not adopted. **Ask
before landing** when the audit showed more than one author or a published
branch; otherwise, in an automatic run, the request to clean the branch is
the authorisation. The hand-back leads
with the before/after size and ends with the undo command copied from
`UNDO.md`. Give the owner the review page too: `python3 $S/review_site.py $W
--out $W/review.html` draws the branch's call graph and the series' on one
layout, with each function's fate, the sentence that justifies it, the plan
and the evidence (building-and-landing.md section 8). Do not push or open
the PR unless asked.

## Write mode

Plan the series before writing code. Derive `problem.md` from the spec; when
it admits two readings, ask which rather than implementing both. Write each
patch to minimal-code.md from the first line, since there is no draft to
shrink later. The plan has no `source`; `branch` names the NEW branch the
series goes to, and each patch is an `overlay` or a `patch`. Implement one
patch at a time: write it, add it to the plan, run the builder, which
rebuilds the whole series from the base each time and updates the branch. A
patch heading past its budget is split or rewritten, not finished big, and
the builder enforces it: with no branch to measure against, each patch's
`budget` is the size check. Write mode requires one per patch and stops the
build when a patch exceeds it, unless `budget_ok` gives the reason. Steps 3
and 5 to 7 apply; there is nothing to land and nothing to undo.

## Review mode

Steps 0, 1 and 7 only, with `--check` in place of `--check-tip`: replaying
the tests at every commit proves bisectability without a worktree. Work files
go outside the repo: review mode changes nothing in the tree, the exclude
file included. No problem statement is the first NAK; reconstruct one from
the diff and label it so. A patch that could be half the length is a NAK
under "Length". Deliver `review-v1.md` and the verdict in chat.

## Audit mode

Read [repo-audit.md](references/repo-audit.md) first. It has the whole
procedure and is the only place the licence to change code nobody asked
about applies. Two commands map the tree before anything is judged:

```bash
python3 $S/repo_map.py --repo $R --src <pkg dir> --src tests --md $W/repo-map.md
python3 $S/comment_audit.py --repo $R --src <pkg dir> --md $W/comments.md
```

Both take directories, and the tests too, or tested code reads as dead. The
work has three phases, in this order: **understand** (the map, then read from
the entry points down and trace one path end to end), **plan** (`audit.md`),
then **trim and rewrite** (the series). The map supplies the plan's raw
material: what no entry point reaches, grouped into dead subtrees; functions
that are copies of each other, with the lines one would save; and a rewrite
order, dependencies first. Check each claim by reading before a patch acts
on it. The order of the work:
1. Fix the architecture so the layout explains itself, landed as pure
   `git mv` patches.
2. **Characterize before refactoring**, because a function nothing pins
   cannot be refactored, only rewritten.
3. Write `audit.md` from [templates/audit.md](templates/audit.md).
4. The series: trim first (verified-dead subtrees, then merged copies), then
   the rewrites to minimal-code.md in the map's rewrite order, and anything
   that changes behaviour last. Stop at a size someone will review and defer
   the rest in writing.

Run the map again and put the before and after numbers in the cover letter.

## The email test

Before landing, ask of each patch: could a maintainer in 2000, reading it as
a text email with no IDE, no CI and no ticket, see what it does, why, and
whether it is right, and would they find nothing to cut? One "no" and the
branch is not ready for its PR.
