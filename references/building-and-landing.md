# Building and landing: the mechanics

SKILL.md says what to decide at each step. This file covers how the scripts
carry it out safely: the work directory, where the tests run, where your
edits live, the plan, the build and what it prints, the maintainer pass,
landing, and the hand-back. Read the section a step names when you reach it.

Contents: 1 the ground and the focus map, 2 where the tests run, 3 where your edits live,
4 the plan, 5 the build and what to read in it, 6 the maintainer pass,
7 run, defend, land, 8 the hand-back.

## 1. The ground

Set these once. Every command below uses them, and a harness that resets the
shell between calls needs them re-set each time:

```bash
S=~/.claude/skills/review-ready/scripts; R=<repo>; B=<the branch>
W=$R/.review-ready/${B//\//_}; mkdir -p $W/msgs $W/overlays
E=$(git -C $R rev-parse --path-format=absolute --git-path info/exclude)
grep -qs '.review-ready/' $E || echo '.review-ready/' >> $E
```

There is one work directory per branch, so a second PR never inherits the
first one's overlays. The exclude file is used rather than `.gitignore`,
because editing `.gitignore` would itself be a tracked change. Then find the
**target**: the branch the PR merges into. If a remote exists, `git fetch`,
and remember that fetch moves `origin/main` and never `main`: the target is
whichever is ahead, and the audit says which.

**First, what do the tests open?** Before running anything, at the head and
not in whatever is checked out:
`git grep -nE "load_config\(|db_path|connect\(|requests\.|urlopen|environ" <head> -- tests`
and ask what importing the package itself reads (behaviour-switches.md). A
suite that opens the operator's database or the network is not run blind
(testing.md section 5). Then one command produces the report:

```bash
python3 $S/audit_diff.py --repo <repo> --base <target> --head <head> \
    --check-tip "<test command>" --import-check <pkg> --md $W/audit-before.md
```

Its **Ground** section decides whether the pass can start:

- the **fork point** (the plan's `"base"`) and the target (its `"onto"`). The
  series is built at the fork point and rebased onto the target at the end. A
  patch that "does not apply to my tree", or backs out someone else's fix, is
  dead on arrival;
- a **test merge** with a target that has moved. `CONFLICTS` means the target
  changed the same lines, which no file written at the fork point can
  resolve. Stop, and ask the owner to bring the branch up to date (a merge
  from the target inside the branch is fine);
- **more than one author**, or a branch already **published**: both change
  what landing means (section 7).

Everything else it reports is listed at the top of the script, and every
finding names the reference that says what to do about it. `--check-tip`
runs the tests once, at the head, as the baseline. `--check` replays them at
every commit, which is review mode's question, not this one's.

**The focus map** comes next, before any of the diff is read:

```bash
python3 $S/repo_map.py --repo <repo> --focus <target>..<head> --md $W/focus.md
```

It exports the fork point and the head with `git archive` into temporary
directories and reads those, so the checkout is never read and nothing in
the repo is written. Pass `--src <pkg dir> --src tests` to limit it to the
package and its tests. Call resolution is the same heuristic as the full map:
a name defined once in the tree is taken to be that definition, so a claim
that something is used once or never is checked by reading before it is
acted on. The report says how many plain-name calls in the changed code it
could not place. Its "never called" rows are the whole-tree map's dead list
for what the branch added, so each carries the same reason: nothing
references it, only dead code calls it, or another file imports it, in which
case the import goes too, or, under a try/except, removing it changes which
branch runs. "The tree already has it" compares with definitions that existed
before the branch and still exist at its head, so a function the branch moved
is not listed as a copy of its old self. Run it again at the series head after the rewrite, if you
want to see what the series still adds and whether anything single-use
remains.

## 2. Where the tests run

Never in the user's checkout: in an export or a private worktree of tracked
files only. Use an absolute interpreter (`$R/.venv/bin/python`), no `.env`,
no local database, no untracked fixture. On a `src/` layout set
`PYTHONPATH=src`, **relative**. An absolute one points at the user's
checkout, and every check would then pass or fail on the original code
whatever the series contains. Always set `--import-check`, which catches
that. A result of "did not run", "timed out" or "vacuous" says nothing about
the code: fix the command. No tests, or tests needing a service: say so,
characterize what you will touch, and put "untested beyond this" in the open
questions (testing.md section 5).

## 3. Where your edits live

Not on the branch. For each file a patch changes, write the version that
patch should contain to `$W/overlays/<NN>/<path>`, mirroring the repo path.
The rewrite step starts it from `git show <base>:<path>`
(minimal-code.md section 1). Alternatively, cut a diff into `$W/hunks/`
from `git diff <base> <head> -- <path>`, deleting whole hunks and never
editing inside one. That only suits a file the rewrite leaves alone. A file
the series takes unchanged needs neither: the plan names it under `paths`.

Overlays are whole files, so **a change made to patch NN's overlay is
repeated in every later overlay of that path**. The builder reports one that
drops lines an earlier patch added.

## 4. The plan

Write `$W/plan.json` (template: templates/plan.example.json; every field is
documented at the top of `build_series.py`).

Per patch:
- the message file;
- how to build it: `paths` (whole files from the user's branch), `overlay`
  (a version you wrote), `patch` (a hand-cut diff) or `delete`;
- `budget`;
- `author`, when the patch is a colleague's work;
- `expect_fail_before`, a command naming ONE test that must fail on the
  parent's code and pass with the patch;
- `mutate`, which breaks the code on purpose and requires a test to fail
  (for a characterization test, or a second new test).

Per plan:
- `base`, `onto`, `source`, `check`, `import_check`, `stack`;
- `scope_ok`, the reason for each file the branch never touched;
- `size_ok`, the reason for each file exempt from the size gate;
- `shrink`, and `shrink_reason` when it is raised above 0.5.

**A stack.** `"stack": [{"branch": "<branch>-1-prep", "through": 3}, ...]`
ends branch k at that patch; the user's branch is the top. Cut where each
slice is mergeable alone: edits to shared code first, each justified on its
own, then the new code with its first user. The builder prints each slice's
size and creates the branches at landing. PR k targets branch k-1. Open the
first PR only, list the rest under "Followed by", and open the next when the
previous merges (`git rebase --onto <target> <branch k-1> <branch k>`).
Write one `cover-letter-k.md` per PR. A new file that nothing imports until
a later patch says so in its message: "new file; nothing imports it until N/N".

## 5. The build and what to read in it

```bash
python3 $S/build_series.py $W/plan.json
```

**The build never moves the branch.** It commits patch by patch on a detached
HEAD in a private worktree, runs the check after every commit from a cleaned
tree, stops at the first failure, and records the result at
`refs/review-ready/series/<branch>`. It creates and deletes no branch, so an
interrupted pass leaves the user where they were. When the target has moved
it rebases the series onto `onto`, checking every commit there too. A
conflict there is the owner's to resolve (section 1), not an overlay's.

Its gates are listed at the top of the script; each was once a false pass.
Three of them change what you write:
- `expect_fail_before` must fail on the parent AND pass with the patch. An
  error there (`ImportError` included) clears the gate while proving no more
  than an empty file would, so pair it with `mutate` (testing.md section 3).
- A file the branch never touched needs `scope_ok`.
- The size gate needs the rewrite, or a `size_ok` reason.

Read three things it prints:

- **The size table**, also in `$W/size.md`: each OVER file goes back to the
  rewrite step (minimal-code.md). A failed gate fails the build, and `--land`
  refuses the series.
- **The residual**, `git diff <the branch as the user left it> <the series>`.
  A `-` line is something their branch had and the series does not; a `+`
  line is something the series added. It must equal `deferred.md` plus your
  rewrites, line for line. A `-` line you cannot account for, in
  `deferred.md` or as a rewrite of the same behaviour, is a change you lost.
- **The original tests**: the branch's own test files, run against the
  rebuilt code. Every failure there is a deferred feature's test or a
  behaviour you changed. Say which, one by one.

Then measure: run `audit_diff.py --base <target> --head
refs/review-ready/series/<branch>` into `audit-after.md`, and again after
the last review round.

## 6. The maintainer pass

Run this as a fresh subagent when the harness has a tool that starts one with
its own context. Messaging an existing agent or session is not that, and
neither is a new process, so do not spend the user's quota to get one. With
none, review from the `.patch` files, not from memory; re-derive every claim
in every message by running it; and put `Reviewer: same context` atop the
verdict so the owner finds a second reader.

Fix every NAK in the overlays (carried forward, section 3), rebuild with the
same plan, and review again. Save each round as `review-vN.md`. The rounds
stay there and in the hand-back, never in the PR body or a commit message.

## 7. Run, defend, land

**Run it.** The owner ran the original branch; nobody has run this code. If an
entry point runs offline (a fixture day, last night's input copied to a temp
dir), run it at `refs/review-ready/original/<branch>` and at the series and
diff the outputs. Each difference maps to a named behaviour patch, or is a
lost change. If you cannot run it, the hand-back opens with: "your runs were
of the original branch; run `<entry point>` on this one before you sign."

**Write `defence.md`.** The first thing that happens to a series is a
question, asked of the owner, not of you. Per patch: the three questions a
maintainer would most likely ask, each answered with `file:line`; each
factual claim in the message and its source; every place the rewrite changed
the owner's code, and why.

**Land it**, once, after the ACK:

```bash
python3 $S/build_series.py $W/plan.json --land
```

This is the only moment the branch moves. Untracked files are left alone. It
refuses:
- tracked edits it would overwrite;
- a branch whose code changed since the build;
- a branch checked out in another worktree;
- a series that failed the size gate.

It writes `$W/UNDO.md`: the original tip, the one command that restores it
from any branch, the files it kept as untracked copies, and what a published
branch now needs. **Ask before landing** when the audit showed more than one
author (the plan's `author` keeps a colleague's name on their work) or a
published branch others may have pulled. Otherwise the request to clean the
branch is the authorisation.

## 8. The hand-back

In chat, a line per item and no prose around them:

- a before/after table: reviewable lines, commits, noise, and the size
  gate's total;
- the series map;
- the verdict and how it was reviewed;
- tests run and skipped, at their tip and at yours;
- **"Behaviour that differs from your branch: …"** (each item one line, or
  "none");
- what was deferred;
- any open question and any `size_ok` reason;
- **the undo command, copied from `UNDO.md`**;
- what is theirs to do: read `series/` and `defence.md` (the series is
  unsigned until they have), `git rebase --signoff <target>`, and `git push
  --force-with-lease` if the branch was published, which you never do for
  them.

Then the PR itself: the title, the body (`cover-letter.md`), and for a stack
which branch is which and that only the first is opened. Once the PR merges,
`$W` and `refs/review-ready/*/<branch>` can go. Do not paste patches into
chat; point at the files. Do not push or open the PR unless asked.
