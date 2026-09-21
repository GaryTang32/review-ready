---
name: review-ready
description: The pre-PR pass. Run it when development on a branch is finished and before the pull request is created, to clean and refactor the branch so the PR is small, obvious, and would be ACKed by a Linux kernel maintainer circa 2000. Prunes what the problem does not need, refactors what the branch wrote, splits it into bisectable commits with real messages (stacked PRs when the work is large), writes the PR description, reviews it as the maintainer, then moves the branch onto the series with a one-command undo. Checks security, data correctness and look-ahead leakage, pandas and sqlite pitfalls, algorithmic smells, comments, tests, PEP 8/20/257/484 and behaviour switches. Use when a branch or PR is too big or chunky, came from Codex, Claude or Copilot, or needs cleaning, refactoring, splitting or a maintainer-style review before a PR ("kernel quality", "no AI slop"); or when a whole repo needs auditing or restructuring so an outsider can read it. Not for reviewing one function, one file or an uncommitted diff.
---

# Review Ready

**When this runs: development is finished, the pull request does not exist
yet.** The branch works. This skill makes it reviewable: it builds a clean
series beside the branch, has it reviewed, then moves the branch onto it, so
the PR which opens is small, obvious, and says why.

The bar is a maintainer in 2000 reading each patch as a plain-text email in
`less`. No IDE, no CI, no linter, no ticket link, no patience. If they cannot
see what a patch does, why, and whether it is right in one pass, it is a NAK.
Every rule here exists because a human with finite attention had to say yes,
the constraint a coding agent ignores when it hands you a 5000-line branch.

Scripts live in `~/.claude/skills/review-ready/scripts/`; `$S` below means that
directory. They read git and a private worktree and import nothing from the
code under analysis. The detector modules and the builder test themselves
(`build_series.py --self-test`: twenty-one situations in scratch repos).

## References: read the one the moment calls for

Read the two marked *always*, then whichever the audit or the diff names.
Order at step 1: maintainer-rules.md, then behaviour-switches.md when the diff
touches config, data, defaults, entry points or the clock (it says which
others the change needs), then the rest as the audit's findings name them.

| File | Read it |
|---|---|
| [maintainer-rules.md](references/maintainer-rules.md) | *always*, before the first prune: the bar and its reasons |
| [review-checklist.md](references/review-checklist.md) | *always*, before the maintainer pass: the NAK list and the verdict format |
| [commit-message.md](references/commit-message.md) | before writing the first message |
| [comments.md](references/comments.md) | when the audit reports comment findings, and in every repo audit |
| [python-idiom.md](references/python-idiom.md) | when pruning Python: what PEP 8, 20, 257 and 484 decide, and where they yield to the kernel rules |
| [python-language.md](references/python-language.md) | when a diff touches types, dataclasses, enums, `__eq__`, generators, exceptions and guard clauses, sorting, paths, properties, async, context managers or `pyproject.toml` |
| [clean-code-examples.md](references/clean-code-examples.md) | when a hunk is ugly and you cannot say why: worked bloated-versus-clean pairs |
| [algorithm-smells.md](references/algorithm-smells.md) | when the audit reports a `perf` risk, a duplicate, or a dense function; or a loop changed |
| [behaviour-switches.md](references/behaviour-switches.md) | at step 1, whenever config, data files, defaults, entry points or the clock are touched: read it before the others, it says which of them the change needs |
| [data-correctness.md](references/data-correctness.md) | whenever a changed function takes or returns rows, a frame, SQL, dates, missing values, features or a model: look-ahead leakage lives here. It opens with a router; plain Python needs three sections |
| [security.md](references/security.md) | when the audit reports a `security` risk, or the diff touches SQL, subprocess, deserialization, paths, secrets or HTTP |
| [testing.md](references/testing.md) | before writing or judging any test, including characterization tests |
| [engineering-sops.md](references/engineering-sops.md) | at step 1 for who outside the repo consumes this code; then for trailers and reposts, concurrency, logging, public API changes, changelogs, and the policy on external tools |
| [repo-audit.md](references/repo-audit.md) | before any audit-mode run |

## Modes

| Mode | Trigger | What you produce |
|---|---|---|
| `pre-pr` (default) | "clean up my branch", "before I open the PR", "too big", "split it" | a pruned, refactored, bisectable series built beside the branch, reviewed, then landed on it; plus the PR description and the verdict |
| `write` | "implement this the kernel way" | the same, planned as a series before any code exists, on a new branch |
| `review` | "would this pass", "ACK or NAK" | a verdict only; nothing in the tree changes |
| `audit` | "read the whole repo", "restructure", "is this clean" | repo map, audit report, target tree, and a refactor series behind characterization tests |

Say which mode you picked. "Make my branch pass review" is `pre-pr`: they
want it fixed, not judged. A request naming no branch and no PR (a function,
an uncommitted diff) is `review`: `pre-pr` rewrites history.

**When nobody can answer.** In an unattended run every question the steps
below say to ask becomes an open question in the PR description, verbatim;
every fact you would have asked for is written as the weaker sentence that is
true without it; and landing proceeds unless the audit found a second author
or a published branch, which wait. Never a guess dressed as a finding.

## Ground rules

- The reader's time is the scarce resource, not the author's and not the
  tool's. Every choice optimises for the person reading the patch.
- One logical change per patch: a bug fix, a cleanup, an API change and a new
  user of that API are four; one change across many files is one.
- Never mix movement with modification, rename with logic, or whitespace with
  anything: nobody sees a one-line logic change inside a 300-line reformat.
- Every patch builds and passes its tests alone, or nothing can be bisected.
- **The series never shows its own history.** New code lands clean the first
  time: no patch introduces a problem a later patch fixes, no "address review
  comments" commit. Rounds of your own review are not versions, so the PR
  opens as the first posting with no "changes since" anything.
- What the problem does not need is deferred, not deleted, in `deferred.md`.
  **Never prune needed work to hit a number**: work too big for one PR
  becomes stacked PRs.
- Prefer modifying to adding, and deleting to adding; search before writing.
  A second copy of an existing function is "we already have that"; the rule
  of three is about inventing a new layer, not about tolerating copies.
- **A risk is never fixed silently.** A security hole, a look-ahead leak, a
  silent NULL coercion, a behaviour change: each is its own patch with its
  own message and test, or an open question in the PR description.
- **Every fact in a message has a source** — the user, the ticket, or a
  command whose output you hold. Otherwise ask once, or write the weaker true
  sentence: an invented "last week's report showed 23 days" costs the owner
  their credibility the first time someone asks which report.
- **The branch is never edited, and moves once.** Your versions of files live
  under the work directory; the builder assembles them beside the branch; the
  branch moves at step 8, after the verdict, with the undo written to disk.
- `Signed-off-by` means "I have read this and can defend every line", which
  only the owner can say. The series is handed back unsigned with what they
  need to read it; sign for them only when they asked you to.

## Step 0: establish the ground

Set these once; every command below uses them, and a harness that resets the
shell between calls needs them re-set each time:

```bash
S=~/.claude/skills/review-ready/scripts; R=<repo>; B=<the branch>
W=$R/.review-ready/${B//\//_}; mkdir -p $W/msgs $W/overlays
E=$(git -C $R rev-parse --path-format=absolute --git-path info/exclude)
grep -qs '.review-ready/' $E || echo '.review-ready/' >> $E
```

One work directory per branch, so a second PR never inherits the first one's
overlays, and the exclude file rather than `.gitignore`, which would itself be
a tracked change. Then find the **target**: the branch the PR merges into. If
a remote exists, `git fetch`, and remember that fetch moves `origin/main` and
never `main`: the target is whichever is ahead, and the audit says which.

**First, what do the tests open?** Before running anything, at the head and
not in whatever is checked out:
`git grep -nE "load_config\(|db_path|connect\(|requests\.|urlopen|environ" <head> -- tests`
and ask what importing the package itself reads (behaviour-switches.md). A
suite that opens the operator's database or the network is not run blind
(testing.md section 4). Then one command produces the report:

```bash
python3 $S/audit_diff.py --repo <repo> --base <target> --head <head> \
    --check-tip "<test command>" --import-check <pkg> --md $W/audit-before.md
```

Its **Ground** section decides whether the pass can start:

- the **fork point** (the plan's `"base"`) and the target (its `"onto"`). The
  series is built at the fork point and rebased onto the target at the end: a
  patch that "does not apply to my tree", or backs out someone else's fix, is
  dead on arrival;
- a **test merge** with a target that has moved. `CONFLICTS` means the target
  changed the same lines, which no file written at the fork point can
  resolve: stop, and ask the owner to bring the branch up to date (a merge
  from the target inside the branch is fine);
- **more than one author**, or a branch already **published**: both change
  what landing means (step 8).

What else it reports is listed at the top of the script; every finding names
the reference that says what to do about it. `--check-tip` runs the tests
once, at the head, as the baseline; `--check` replays them at every commit,
which is review mode's question, not this one's.

**Where the test command runs.** Never in the user's checkout: in an export or
a private worktree of tracked files only. Absolute interpreter
(`$R/.venv/bin/python`), no `.env`, no local database, no untracked fixture,
and on a `src/` layout `PYTHONPATH=src`, **relative** — an absolute one is the
user's checkout, and every check would then pass or fail on the original code
whatever the series contains. Always set `--import-check`, which catches that.
A result of "did not run", "timed out" or "vacuous" says nothing about the
code: fix the command. No tests, or tests needing a service: say so,
characterize what you will touch, and put "untested beyond this" in the open
questions (testing.md section 4).

**On a big branch, do not read the diff top to bottom.** Read the audit;
skip the noise classes entirely; then files in order of reviewable lines, new
files last. 5000 lines is often 1500 of change wrapped in habit.

## Step 1: state the problem, the switches, and the data contract

Before touching a hunk, write `problem.md`: one paragraph on what is wrong or
missing in the tree today, how it shows (the failing case, the wrong number,
the missing capability), and what the change does about it. Present tense,
about the code, not about the author's journey. Then what the diff alone
will not tell a reviewer:

- **The call chain**, from the entry point down to the changed line.
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

This file filters every later decision and seeds every commit message. A
problem statement reverse-engineered from a diff always matches the diff, so
the owner must agree with it: their request stating what they asked for is
that agreement; otherwise show them the first paragraph and ask once. A
question nobody can answer becomes an open question in the PR, never a guess.

## Step 2: prune

**Where your edits live.** Not on the branch. For each file a patch changes,
write the version that patch should contain to `$W/overlays/<NN>/<path>`,
mirroring the repo path, starting from whichever is closer: `git show
<base>:<path>` or `git show <head>:<path>`. Or cut a diff into `$W/hunks/`
from `git diff <base> <head> -- <path>`, deleting whole hunks and never
editing inside one. A file the series takes unchanged needs neither: the plan
names it under `paths`. Overlays are whole files, so **a change made to patch
NN's overlay is repeated in every later overlay of that path**; the builder
reports one that drops lines an earlier patch added.

Walk every hunk and ask whether the problem statement needs it. What goes:

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
6. Duplicates of something the tree already has. The audit lists them; reuse
   the original and say so in the message.
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

## Step 3: refactor what stays

Pruning removes what should not be there. This step fixes what should be
there but is not yet right. **Scope: lines inside functions this branch's
diff already changes, and files it adds.** Anything else, however tempting,
is `deferred.md`: pre-existing weak tests, duplicates already on the base, a
neighbouring function's smell. That is audit mode's work, and the licence
repo-audit.md grants belongs to audit mode only. The builder refuses a file
the branch never touched unless the plan's `scope_ok` gives its reason (a
characterization test for a function the branch modifies is one).

Work through what the audit flagged, reading the reference it names. Each
line of its report is a category here:

| What | Looks like | Reference |
|---|---|---|
| Shape | over two screens or three levels; a condition of four terms; a special case the right representation removes | clean-code-examples.md |
| Algorithm | `pop(0)` in a loop, a frame or string grown in a loop, membership against a list, a full sort to take three, a window recomputed per row. State the complexity of every loop you changed; a performance rewrite is measured first and is its own patch | algorithm-smells.md |
| Language contracts | a `Dict[str, Any]` that wanted a `TypedDict`, strings that wanted an enum, a generator consumed twice, an exception stripped of its cause | python-idiom.md, python-language.md |
| Data | look-ahead in a feature, a merge with no keys or `validate`, a missing value turned into a number, a naive timestamp, chained assignment, a commit in a loop | data-correctness.md |
| Security | SQL built from a string, `shell=True`, `pickle` or `eval` of data the process did not write, a secret in source | security.md |
| Tests | every added behaviour has a test that fails without it; none reaches the network or the operator's data | testing.md |
| Comments | what survives says what the code is for | comments.md |

A refactor that changes what a caller sees (a `None` return made to raise, an
`except` narrowed) is a behaviour change to code the owner ran and you did
not: it goes in `deferred.md`'s `behaviour` list and in the hand-back, even
when it is right. Where each fix lands:

- **New code from this branch**: fold the fix into the patch that introduces
  that code.
- **Pre-existing code the branch modifies**: a preparatory patch, `No
  functional change.`, placed before the behaviour change it enables; if
  nothing pins that code, the characterization test first, in its own patch
  (repo-audit.md section 3). If that is too much for this PR, defer it.
- **A risk in pre-existing lines the branch's diff touches**: its own patch if
  small and certain, otherwise an open question. Never silent, never bundled.

## Step 4: split into a series, and size the PR

Order the patches so each makes the next smaller and more obvious:

1. Preparatory patches: pure moves, renames, extractions, characterization
   tests. Each says `No functional change.`, changes no assertion and no
   expected value (a test's import line or renamed call site may change, and
   the message says "tests: import path only"), and is verifiable in seconds.
   A patch that changes behaviour is never "prep".
2. The behaviour change, as small as it can be, with its tests in the same
   patch. A test in a later patch fails bisect in between.
3. New users of a new interface. A function lands in the patch of its first
   caller, or at least in the same PR: code nothing calls is dead on `main`.
4. Cleanups the change enables.

**A patch that flips a switch is dark-launched.** Add the new path guarded
off; test both positions; flip the default in its own patch whose entire diff
is the flip, so reverting it is the rollback. A config or data file never
changes in the same patch as code, with one exception: a line that names
code (a `pyproject.toml` entry point, a CI path, an import string in a
config) moves in the patch that moves what it names. (behaviour-switches.md)

**Size the PR, not only the patch.** A patch is right when it can be reviewed
in fifteen minutes and described in one sentence. The PR has two budgets,
because there are two risks. **Edits to existing code** can break everyone
who depends on it: about 400 reviewable lines, rarely over eight patches.
**New self-contained files** with their tests, imported by nothing until the
last patch, can break nobody: up to about a thousand lines, one patch per
unit, the message saying "new file; nothing imports it until N/N".

**Past that, a stack.** One plan builds the whole series; `"stack"` cuts it:
`[{"branch": "<branch>-1-prep", "through": 3}, ...]` ends branch k at that
patch, the user's branch being the top. Cut where each slice is mergeable
alone: edits to shared code first, each justified on its own, then the new
code with its first user. The builder prints each slice's size and creates the
branches at landing. PR k targets branch k-1; **open the first PR only**, list
the rest under "Followed by", and open the next when the previous merges
(`git rebase --onto <target> <branch k-1> <branch k>`). Seven dependent PRs in
one morning is a patch bomb, and got the same answer in 2000: silence. One
`cover-letter-k.md` per PR.

Write `$W/plan.json` (template: [templates/plan.example.json](templates/plan.example.json);
every field is documented at the top of `build_series.py`). Per patch: the
message file and how to build it, with `paths` (whole files from the user's
branch), `overlay` (a version you wrote), `patch` (a hand-cut diff) or
`delete`; `budget`; `author`, when the patch is a colleague's work;
`expect_fail_before`, a command naming ONE test that must fail on the
parent's code and pass with the patch; and `mutate`, which breaks the code on
purpose and requires a test to fail (for a characterization test, or a second
new test). Per plan: `base`, `onto`, `source`, `check`, `import_check`,
`stack`, `scope_ok`. Do not build yet: the builder needs every message.

## Step 5: commit messages, then build

Read commit-message.md, then write one file per patch in `$W/msgs/`. A
subject of `area: imperative summary` in about sixty characters, a blank
line, then a body wrapped at 72 that says what is wrong in the tree, why this
fix rather than the obvious alternative, what is deliberately out of scope,
and how it was tested. No `Signed-off-by:` line and no `---` line: the builder
commits the file verbatim, and refuses both. Use `Fixes:` and the other
trailers where they apply (engineering-sops.md). If the repo has its own
subject convention, use it and keep the body rules.

The audience is someone reading `git log` in five years, without the PR. Then:

```bash
python3 $S/build_series.py $W/plan.json
```

**The build never moves the branch.** It commits patch by patch on a detached
HEAD in a private worktree, runs the check after every commit from a cleaned
tree, stops at the first failure, and records the result at
`refs/review-ready/series/<branch>`. It creates and deletes no branch, so an
interrupted pass leaves the user where they were. When the target has moved it
rebases the series onto `onto`, checking every commit there too; a conflict
there is the owner's to resolve (step 0), not an overlay's. Its
gates, listed at the top of the script, were each once a false pass. Two
change what you write: `expect_fail_before` must fail on the parent AND pass
with the patch, and an error there (`ImportError` included) clears the gate
while proving no more than an empty file would, so pair it with `mutate`
(testing.md section 3); and a file the branch never touched needs `scope_ok`.

Read two things it prints. **The residual**, `git diff <the branch as the user
left it> <the series>`: a `-` line is something their branch had and the
series does not, a `+` line something the series added. It must equal
`deferred.md` plus your refactors, line for line: a `-` you cannot find in
`deferred.md` is a change you lost. **The original tests**: the branch's own
test files, run against the rebuilt code. Every failure there is a deferred
feature's test or a behaviour you changed; say which, one by one.

Now measure: `audit_diff.py --base <target> --head refs/review-ready/series/<branch>`
into `audit-after.md`, and again after the last review round. If the
reviewable lines barely fell, either the prune was too gentle or the work is
needed: say which, and if it is needed, stack it.

## Step 6: the PR description

Fill [templates/cover-letter.md](templates/cover-letter.md). It becomes the
pull request body: the problem, the approach, one line per patch, how the
series was tested (and whether anything was run end to end, step 8), what was
deferred and why, open questions, and, for a stacked PR, what it depends on
and what follows. It stands alone: the reviewer cannot see `deferred.md`, so
the one-line reasons are the whole deferred list. Budget it in items, not
lines: a paragraph for the problem and one for the approach, a line per
patch, three for testing, one per deferred item and per open question. Past
a screen, the series is too big, not the letter. No
"Changes since" block on a first posting; that block appears only after a
human has reviewed a pushed version, and counts only those.

## Step 7: the maintainer pass

Change roles. The maintainer gets exactly what review-checklist.md's first
paragraph lists: `problem.md`, the patches, the PR description, `deferred.md`,
`residual.patch`, that checklist, and the tree read-only. Not the plan, the
conversation, or the reasoning that produced the series. They read each patch
top to bottom as email, then do what three lines of context cannot:
walk the failing case through the OLD code to confirm the diagnosis, then
through the new code to confirm the fix, and write both walks down; grep the
tree for every caller of a changed signature; diff what a "moved verbatim"
claims; run the check at every commit themselves; and ask where each number
in a message came from. They return, per patch, ACK or NAK with the
objection quoted from the diff and the fix required. **An ACK on a behaviour
patch with no trace written under it is not an ACK.** A missing sign-off is
not a NAK here.

Run this as a fresh subagent when the harness has a tool that starts one with
its own context; messaging an existing agent or session is not that, and
neither is a new process, so do not spend the user's quota to get one. With
none, review from the `.patch` files, not
from memory; re-derive every claim in every message by running it; and put
`Reviewer: same context` atop the verdict so the owner finds a second reader.

Fix every NAK in the overlays (carried forward, step 2), rebuild with the
same plan, review again. Stop when every patch is ACK, or when the remaining
NAKs need a decision from the user; those become open questions. **Three
rounds is the cap.** NAKs still standing at round three mean the problem
statement or the split is wrong: stop, go back to step 1 or 4, and tell the
user which. Save each round as `review-vN.md`; the rounds stay there and in
the hand-back, never in the PR body or a commit message.

## Step 8: run it, land it, hand it back

**Run it.** The owner ran the original branch; nobody has run this code. If an
entry point runs offline (a fixture day, last night's input copied to a temp
dir), run it at `refs/review-ready/original/<branch>` and at the series and diff
the outputs: each difference maps to a named behaviour patch, or is a lost
change. If you cannot, the hand-back opens with: "your runs were of the
original branch; run `<entry point>` on this one before you sign."

**Write `defence.md`**: the first thing that happens to a series is a
question, asked of the owner, not of you. Per patch: the three questions a
maintainer would most likely ask, each answered with `file:line`; each factual
claim in the message and its source; every place step 3 changed the owner's
code, and why.

**Land it**, once, after the ACK:

```bash
python3 $S/build_series.py $W/plan.json --land
```

The only moment the branch moves. Untracked files are left alone; it refuses
tracked edits it would overwrite, a branch whose code changed since the build,
and a branch checked out in another worktree. It writes `$W/UNDO.md`: the
original tip, the one command that restores it from any branch, the files it
kept as untracked copies, and what a published branch now needs. **Ask before
landing** when the audit showed more than one author (the plan's `author`
keeps a colleague's name on their work) or a published branch others may have
pulled. Otherwise the request to clean the branch is the authorisation.

**Hand back** in chat, a line per item and no prose around them: a
before/after table (reviewable lines, commits, noise); the series map; the
verdict and how it was reviewed; tests run and skipped, at their tip and at
yours;
**"Behaviour that differs from your branch: …" (each item one line, or
"none")**; what was deferred; any open question; **the undo command, copied
from `UNDO.md`**; and what is theirs to do: read `series/` and `defence.md`
(the series is unsigned until they have), `git rebase --signoff <target>`,
and `git push --force-with-lease` if the branch was published, which you
never do for them. Then the PR itself: the title, the body
(`cover-letter.md`), and for a stack which branch is which and that only the
first is opened. Once the PR merges, `$W` and `refs/review-ready/*/<branch>` can
go. Do not paste patches into chat; point at the files. Do not push or open
the PR unless asked.

## Write mode

Plan the series before writing code. Derive `problem.md` from the spec; when
it admits two readings, ask which rather than implementing both.
The plan has no `source`; `branch` names the NEW branch the series goes to,
and each patch is an `overlay` or a `patch`. Implement one patch at a time:
write it, add it to the plan, run the builder, which rebuilds the whole
series from the base each time and updates the branch. A patch heading past
its budget is split, not finished big. Steps 3 and 5 to 7 apply; there is
nothing to land and nothing to undo, because the branch is new.

## Review mode

Steps 0, 1 and 7 only, with `--check` in place of `--check-tip`: replaying
the tests at every commit proves bisectability without a worktree. Work files
go outside the repo: review mode changes nothing in the tree, the exclude
file included. No problem statement is the first NAK; reconstruct one from
the diff and label it so. Deliver `review-v1.md` and the verdict in chat.

## Audit mode

Read [repo-audit.md](references/repo-audit.md) first; it has the whole
procedure and is the only place the licence to change code nobody asked about
applies. Two commands map the tree before anything is judged:

```bash
python3 $S/repo_map.py --repo $R --src <pkg dir> --src tests --md $W/repo-map.md
python3 $S/comment_audit.py --repo $R --src <pkg dir> --md $W/comments.md
```

Both take directories, and the tests too, or tested code reads as dead. Then
read from the entry points down, trace one path end to end, and judge against
the objective using the references above. The order of the work: fix the
architecture so the layout explains itself (group by role, depth follows
dependency, one sentence atop every module, no catch-all `utils/`, tests
mirror the tree), landed as pure `git mv` patches; **characterize before
refactoring**, because a function nothing pins cannot be refactored, only
rewritten; write `audit.md` from [templates/audit.md](templates/audit.md);
then the series, docstrings and verified deletions first and anything that
changes behaviour last, stopping at a size someone will review and deferring
the rest in writing. Run the map again and put the before and after numbers
in the cover letter.

## The email test

Before landing, ask of each patch: could a maintainer in 2000, reading it as
a text email with no IDE, no CI and no ticket, see what it does, why, and
whether it is right? One "no" and the branch is not ready for its PR.
