# Auditing a whole repository, and refactoring it safely

Clean mode starts from a diff someone wrote. Audit mode starts from a tree
nobody has justified: read all of it, work out how it is wired, judge it
against the objective, and change what fails. The authority is real. The
discipline that makes it safe is that the output is still a patch series,
and that no function is touched before its behaviour is pinned.

Two failure modes to design against, both of which look like progress:

- **The mega-refactor.** Six hundred files reformatted, renamed, and
  resplit in one commit. It is unreviewable, unbisectable, and unrevertable.
  It recreates the exact problem this skill exists to solve, with the skill's
  own name on it.
- **The confident rewrite of untested code.** A function with no test is a
  function whose behaviour nobody has written down. Rewriting it does not
  preserve behaviour; it replaces behaviour with your reading of the code.
  The tests still pass because there were none.

Read this file with [maintainer-rules.md](maintainer-rules.md) for the bar,
[python-idiom.md](python-idiom.md) and
[clean-code-examples.md](clean-code-examples.md) for what clean means in
Python, and [behaviour-switches.md](behaviour-switches.md) before touching
anything that reads config, data, or the clock.

## 1. Comprehend before judging

You cannot review a tree you have not mapped, and you cannot delete code
whose callers you have not found. Start here, always:

```bash
python3 $S/repo_map.py --repo <repo> --src <pkg dir> --src tests --md <work>/repo-map.md --json <work>/repo-map.json
```

It parses every file, resolves calls, and reports: what each module is for,
the layer it sits in, entry points, import cycles and a rewrite order, hot
spots by churn times fan-in, functions over budget, untested public
functions, definitions no entry point reaches, functions that are copies of
each other, modules that read switches, and the calls it could not resolve.
Nothing is imported or executed, so it is safe on a tree you have not read.

Read the report in this order, because each answer narrows the next:

1. **Entry points.** Everything reachable starts here. A file that does its
   work at the top level and that nothing imports is listed as a script, even
   with no `__main__` block. A module that nothing imports and that runs
   nothing is a candidate for deletion, not for refactoring, unless the tree
   loads it by name: a plugin directory read with `importlib` looks exactly
   like that, so find the loader before deleting.
2. **The module table.** Any row whose purpose reads `defines ...` is a
   module with no docstring: an outsider cannot place it without reading it.
   That is finding number one, and the cheapest to fix.
3. **Layers and cycles.** A cycle means two modules are really one; decide
   which way the dependency should point before anything else, because the
   answer moves code.
4. **Hot spots.** Churn times fan-in is where a defect costs most. Fix here
   first; a tidy module nobody imports and nobody changes can wait forever.
5. **Possibly dead, and duplicates.** These are the trim, and the cheapest
   lines in the tree to remove. The dead list comes from reachability, so a
   function called only by dead code is listed with the function that calls
   it: delete the subtree from its head down. "Only tests reach it" is test
   support living in production code, or a feature nothing uses; decide
   which. A copy cluster is every function alike to one centre function.
   Where the copies are the same function (95% alike or more) one copy
   replaces the rest and the lines saved are real. Where they were edited
   after pasting, the figure is an upper bound: the differences have to
   become parameters.
6. **Unresolved calls.** The dead and untested lists are conservative by
   exactly this much. Say so in the report rather than claiming certainty.

The map is a plan, not a verdict. Its claims rest on a name-matching
resolver: check each dead definition against a plugin, a template or a
caller outside the tree, and read both sides of a copy, before a patch
acts on either.

**Prove the test command tests this tree before you trust a single run.**
An editable install, a stale `site-packages` copy, or an inherited
`PYTHONPATH` will happily import a different checkout, and then every green
run in the series is a test of code you did not change. Import the package
inside the tree and look at where it resolved:

```bash
cd <repo> && <the test interpreter> -c "import <pkg>; print(<pkg>.__file__)"
```

If that path is not inside the repo, fix the command (put the repo's source
root first on `PYTHONPATH`) before going further, and record it in the audit's
limits section. `build_series.py`'s `import_check` enforces this for the
series; nothing enforces it for the baseline run you take by hand.

Then read the code itself, entry point first, following the call graph down.
Trace one complete path end to end before forming an opinion, and write that
trace into `problem.md`. An audit that never ran the code, or never followed
one request from entry to storage, is a style review wearing a costume.

**Verify every deletion by hand.** The resolver sees calls, value references,
and identifier-shaped strings. It cannot see a name built at runtime, a
plugin discovered by entry point, a template variable, a database column
mapped by name, or a public API someone outside this tree imports. For each
candidate, grep the whole tree including non-Python files, check whether the
package exports it, and check whether it is part of a published interface. If
the repo is a library, "nothing in this tree calls it" is not "nobody calls
it".

## 2. The architecture an outsider can read

The target: someone who has never seen the repo opens it, reads the top-level
directory listing, and can say what the system does and where to look. That is
a property of layout and naming, not of documentation.

**Rules that produce it.**

- **Group by role, not by kind.** `ingest/`, `storage/`, `domain/`,
  `pipeline/`, `cli/` tells a reader what the code does. `helpers/`,
  `utils/`, `models/`, `services/` tells them only what shape it is, which
  they could already see. A directory named for a layer of the system beats a
  directory named for a language construct.
- **Depth follows dependency.** A package should import downward. Put the
  modules that import nothing else in the tree at the bottom, the entry
  points at the top, and let the import graph be readable as a stack. The
  layer column of the report is that stack as it stands today.
- **One sentence at the top of every module**, saying what it is for and who
  calls it. This is the single highest-value change in most trees: it is
  cheap, it is `No functional change.`, and it turns the module table from
  `defines a, b, c` into an index.
- **No catch-all package.** `utils/` accumulates whatever had no home, which
  means it is the one package whose contents nobody can predict. Move each
  module next to its only caller, or name the package for what the code
  actually is.
- **Size is a structural signal.** A module over roughly 500 lines, or one
  defining more than about fifteen public names, is usually two modules whose
  seam is already visible in its own section comments.
- **A README where the package is not obvious**, three sentences: what lives
  here, what depends on it, what it depends on.
- **Tests mirror the tree.** `tests/ingest/test_feeds.py` next to
  `ingest/feeds.py`. A reader looking for a module's tests should not search.

**Proposing the move.** Write the target tree in the report as a listing,
each destination with the reason it exists and the modules that go there.
Then land it as pure renames: `git mv` only, no edits to the moved file
beyond the import lines that must change, one patch per coherent group,
each saying `No functional change.` in its body. `git diff -M` shows a rename
as a rename, so a four-hundred-line move stays a one-screen review. Never
move and modify in the same patch; the split that follows a move is a later
patch, and the reviewer of that patch gets a real diff instead of a file
appearing from nowhere.

An import-only change is still a change: run the tests after every move
patch, and remember that moving a module changes its import path, which is a
behaviour switch for anything that imports it by string.

## 3. Characterize before you refactor

This is the gate, and it is not negotiable, because it is the difference
between refactoring and rewriting.

> A refactor changes the code and not the behaviour. If nothing pins the
> behaviour, you cannot know you did that, so the claim in the commit
> message is false.

For any function you intend to change that the untested list names:

1. **Write a characterization test first, in its own patch.** Not a test of
   what the function should do: a test of what it does now, including the
   parts you think are wrong. Feed it a real input, record the actual output,
   assert that. If the current behaviour is surprising, the test says so and
   the surprise is now documented.
2. **Run that test against the unchanged code.** It must pass. A
   characterization test that fails on the parent is not characterizing
   anything, it is a bug report; land it as one, with the fix.
2b. **Prove the test is load-bearing.** A characterization test must pass on
   the code as it stands, so `expect_fail_before` cannot gate it, and
   `assert result is not None` passes every gate above while pinning nothing.
   Break the code on purpose: change one operator or one constant in the
   function, run the test, and watch it fail. `build_series.py` does this when
   the patch carries a `mutate` entry (file, find, replace, test) and refuses
   to continue if the test still passes with the code broken. A test that
   survives the mutation is decoration, and the refactor behind it is an
   unpinned rewrite.
3. **Then refactor, in a separate patch, and do not touch the test.** The
   test file being unchanged in the refactor patch is the reviewer's proof
   that behaviour was preserved. If the refactor forces a test edit, either
   the test was testing the implementation, or the behaviour changed. Both
   are findings; neither is a refactor.
4. **A behaviour change is never a refactor.** If the audit found a genuine
   bug, it gets its own patch, its own failing-then-passing test, a message
   that says what was wrong, and it is not described as a cleanup.

`build_series.py` enforces the mechanical half: `expect_fail_before` proves a
new test actually tests the change, and the per-patch check proves every
commit stands alone. `audit_diff.py --check` proves the same thing read-only
on a branch you did not build.

Where behaviour is genuinely unpinnable in reasonable time (a function that
calls a network, a clock, and a database in one body), the honest first patch
is the seam that makes it testable: pass the dependency in rather than
reaching for it. That patch is itself a refactor, so it needs a
characterization test at the level above it, wherever the nearest testable
boundary is. Say in the message that the test sits one level up and why.

## 4. What to fix first

Rank by expected damage, not by offence to taste. The report's hot-spot
ordering, churn times fan-in, is the first cut. Then:

1. **Correctness risks in hot code.** A swallowed exception, a silent NULL
   coercion, an unvalidated join, a bare `except` in the module every other
   module imports.
2. **Dead switches, dead code, then copies.** Deleting is the safest patch
   there is and makes everything after it smaller. Verify each by hand
   first. Merging copies comes next: where two copies differ, the difference
   is a parameter or a bug in one of them, and the message says which.
3. **Missing characterization on hot untested functions.** This is the
   enabling work; nothing else in the list is safe without it.
4. **Architecture: docstrings, then moves, then splits.** Cheap to review,
   large effect on comprehension.
5. **Internal cleanups in the big functions.** Guard clauses, naming, the
   representation that removes the special case.
6. **Everything else, deferred in writing.** A finding you are not fixing
   goes in `deferred.md` with its reason, not in an unbounded series.

Stop the series at a size someone will actually review. An audit that
produces forty patches has produced a backlog, not a patch series: land the
first coherent group, get it merged, and let the rest be the next series.
Say that in the cover letter, with the count of what remains.

## 5. The order of the series

Each stage is separately reviewable and separately revertable:

1. Docstrings and comments that only say what a module is for. `No
   functional change.`
2. Deletions of verified-dead code. Each with the evidence in the message;
   a dead subtree is one patch.
3. Characterization tests. Prod code untouched.
4. Pure moves and renames. `No functional change.`
5. Splits of oversized modules, as moves of whole functions.
6. Internal refactors, one function or one idea per patch, tests unchanged.
   Merging a copy is one of these. Take modules in the map's rewrite order,
   dependencies first, so what a module imports has stopped moving before
   the module is touched.
7. Bug fixes found along the way, each with its own failing-then-passing test.
8. Behaviour changes, only if asked for, each flagged in the cover letter.

Running the audit again at the end is the check: the second report should
show fewer findings in exactly the places the series claimed to fix, and no
new ones. Put the before and after numbers in the cover letter.

## 6. What full authority licenses, and what it does not

**Audit mode only.** A pre-PR pass has no such licence: its scope is what the
branch wrote, the builder refuses a file the branch never touched, and
everything else is a line in `deferred.md`. In audit mode, full authority
licenses changing the code without asking first, deleting what is dead,
moving what is misplaced, and rewriting what is unclear.

It does not license:

- **Changing behaviour silently.** Any behaviour change is called out in its
  own patch and in the cover letter, whatever the audit thinks of the old
  behaviour.
- **One large commit.** Authority over the content is not authority over the
  reviewer's attention.
- **Refactoring what nothing pins.** Characterize first, or leave it and say
  why in `deferred.md`.
- **Rewriting to taste where the tree has a settled convention.** Match the
  file. A consistent tree that differs from your preference is not a defect,
  and PEP 8 says so in "A Foolish Consistency is the Hobgoblin of Little
  Minds".
- **Touching what the objective does not reach.** Generated files, vendored
  code, and a subsystem the user excluded stay untouched.

## 7. The report

Write `audit.md` before writing any patch:

```
# Audit: <repo> at <sha>

## What this system does
<three sentences from the entry points and the module table, not from the README>

## How it is wired
<layers, cycles, the one path traced end to end>

## Architecture
<what an outsider cannot tell today, the target tree, the move plan>

## Findings
| # | severity | file:line | finding | fix | patch |
<severity: correctness, safety, structure, clarity. Ordered by churn x fan-in.>

## Not pinned
<hot functions with no test: what must be characterized before it can be touched>

## Plan
<the series, in the order of section 5, with a line budget per patch>

## Deferred
<everything found and not fixed, with reasons>

## Limits of this audit
<unresolved calls, dynamic dispatch, non-Python callers, what was not read>
```

The last section is not modesty, it is the thing that makes the rest
trustworthy. An audit that claims complete knowledge of a tree it inferred
from an AST is making a claim it cannot support.

## 8. Review questions for audit mode

Added to the maintainer pass in [review-checklist.md](review-checklist.md):

- "Did you map the tree before judging it, or grep for smells?"
- "Which entry point reaches this code?"
- "What pins this function's behaviour, and did that test exist before your
  patch?"
- "You call this a refactor: is the test file unchanged in this patch?"
- "You deleted it: what did you grep, and is this a library?"
- "You moved it: is anything in this patch other than the move?"
- "This series is N patches: which are you asking me to merge today?"
- "What did the second audit run say?"
