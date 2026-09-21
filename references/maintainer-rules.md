# Maintainer rules

Where these come from: Linus Torvalds's `Documentation/CodingStyle` (the
1996 original), `Documentation/SubmittingPatches` as it stood in the 2.4
era, and the way patches were actually accepted or refused on linux-kernel
around 2000. The letter of those documents is about C and tabs. The spirit
is about one human reviewer with finite attention, and that is what
transfers. Each rule is stated the way the maintainer would state it, then
translated for a modern Python, data, or full-stack tree.

Contents: 1 separate your changes, 2 describe your changes, 3 style and what
it protects, 4 taste, 5 the modern tree, 6 what agent code does and why each
one is a NAK.

## 1. Separate your changes

SubmittingPatches, paraphrased: separate each logical change into its own
patch. If your changes include a bug fix and a performance improvement for
the same driver, that is two patches. If they include an API change and a
new driver that uses the API, that is two patches. If one patch depends on
another, say so. Do not mix cosmetic changes with functional ones; the
cosmetic ones hide the functional ones. And the half that is usually left
out: a single change made to numerous files is a single patch. Splitting is
by logical change, never by file count, and a rule that manufactures patches
manufactures PRs.

Why: a reviewer verifies one claim per patch. "This moves code and changes
nothing" can be verified in a minute. "This moves code, renames three
things, and changes one condition" cannot be verified at all, because the
reviewer cannot find the condition.

Modern reading:

- A refactor patch changes no assertion and no expected value. If it does, it
  was not a refactor. A rename or a move may change a test's import line or
  call site, and the message says so ("tests: import path only"); a
  characterization patch adds tests and touches no code.
- A formatter run (black, prettier, isort) is its own commit with nothing
  else in it, or it is not in the series.
- A rename is its own patch. `git diff -M` should show it as a rename with
  a tiny diff.
- Move, then modify. Patch N moves the function verbatim; patch N+1 changes
  it. Never both in one patch.
- A lockfile change rides with the dependency change that caused it, and
  never inside a feature patch.

## 2. Describe your changes

Say what is wrong in the tree and why this patch is the right fix. Problem
before solution. The reader must be able to judge the patch from the
description without the diff, and judge the diff from the description
without the ticket. Format and examples are in commit-message.md.

## 3. Style, and what it protects

| Kernel rule (1996 wording) | What it protects | Modern reading |
|---|---|---|
| Tabs are 8 characters. If you need more than 3 levels of indentation, you're screwed anyway, and should fix your program. | the reader's ability to follow control flow | Nesting depth at most 3 inside a function. Flatten with guard clauses, early return, `continue`, or extract the inner body. |
| Functions should be short and sweet, and do just one thing. They should fit on one or two screenfuls of text (80x24). | one claim per function | About 48 lines. Longer is fine only for a flat sequence where nothing nests: a big table, a long chain of simple cases. |
| A function with more than 5 to 10 local variables: rethink it. | working memory | Same. Many locals means the function does several things. |
| Global names need to be descriptive. Local names should be short; `tmp` is fine for a loop temporary. Encoding the type in the name is brain damage. | signal-to-noise | `i`, `row`, `df` are fine locally. `df_prices_cleaned_final_v2` is not. No `str_name`, no `IUser`, no `AbstractBaseFooFactory`. |
| Comments say WHAT the code does and WHY, never HOW. Put them at the head of the function. Comments inside a function body mean the function is too complex. | the comment as summary, not crutch | A docstring on a public function when name and signature do not say it all. No docstring that restates the signature. No inline comment that narrates the next line. A block that needs a comment wants to be a function whose name is that comment. |
| Don't hide things behind typedefs. | reading code without chasing definitions | No alias, wrapper class, or base class with one use. Deep modules behind shallow interfaces: one function that does real work beats three layers that forward to each other. |
| Centralised exiting of functions (the `goto out` pattern). | error handling in one place | Error cases return early at the top; one happy path; `with` for resources. No `try` around code that cannot raise. Never `except Exception: pass`. |
| Macros that affect control flow are a very bad idea. | no hidden behaviour | No decorator that swallows exceptions, no `__getattr__` magic, no import-time side effects, no metaclass for a config object. |
| Use the existing style: tabs, 80 columns, K&R braces. | consistency the eye can rely on | Match the file you are in. Never reformat a line you did not otherwise change. |

## 4. Taste

Taste is the part no linter checks and the part that decides whether the
maintainer trusts you with the next patch.

- **Remove the special case by choosing the right representation.**
  Linus's example: deleting a node from a singly linked list. Everyone
  first writes `if (node == head)`. The version that keeps a pointer to the
  pointer has no special case, because the head stopped being special. When
  you meet an `if` for "the first one", "the empty one", "the missing key",
  ask whether a different shape of data would make it vanish. In pandas
  terms, an `if df.empty` guard usually means the operation was written for
  the wrong axis or the wrong default.
- **When in doubt, use brute force.** (Ken Thompson, via Rob Pike.) A clear
  loop over a hundred rows beats a clever index over the same rows.
  Optimise when a measurement says to, in its own patch, with the number in
  the message.
- **Deleting code is the best patch.** Negative line count and the same
  tests passing needs almost no review.
- **Obvious beats clever.** Of two correct versions, the one a stranger
  understands faster is right, even when it is longer.
- **No abstraction without three users.** The first user does not know what
  the abstraction is. The second is a coincidence. The third shows the
  shape. Until then, duplicate and wait. This is about inventing a layer (a
  base class, a registry, a plugin point). A second copy of a FUNCTION the
  tree already has is the opposite case, and the answer in 2000 was one
  line: "we already have that in lib/". Reuse it.

## 5. The modern tree: Python, data, notebooks

Things the 2000 maintainer never saw, judged by the same principles.

- **pandas and SQL.** Every merge names its keys (`on=`), its type (`how=`),
  and its cardinality (`validate=`). Silent index alignment is a hidden
  join. `inplace=True` is a hidden side effect. A NULL that becomes 0 or ""
  is a hidden bug; if the input may be missing, the code and the message
  both say what happens then.
- **Join keys have a dtype.** An object key joined to an int64 key matches
  nothing, and `validate="m:1"` still passes, because zero matches is a legal
  many-to-one. Check the key dtypes on both sides and the row count after.
- **Sort ties have an order only if you name one.** Python's `sorted()` is
  stable; `DataFrame.sort_values` defaults to quicksort, which is not. When a
  sort feeds `drop_duplicates(keep="last")`, `head`, or `groupby().first()`,
  say `kind="stable"` or sort on a total key.
- **Every aggregation skips missing values by default.** Ask how many rows
  were missing and what they contributed: a mean over a column that is 90%
  NaN returns a believable number with no keyword in the diff to grep.
- **A trading session is not a calendar day.** `groupby(ts.dt.date)` on a
  host in another timezone files part of each day's news under the wrong
  session, and `merge_asof` returns wrong rows silently when its inputs are
  not sorted on the key.
- **Does the function consume its argument?** `def enrich(df): df["x"] = ...;
  return df` mutates the caller's frame while the return value hides it. Copy
  at the boundary or say in the name that it mutates. The same question goes
  to a generator passed in and iterated twice.
- **Notebooks.** Never in a patch with outputs. Strip them, or better, move
  the logic into a module and leave the notebook as a caller. A notebook
  diff is unreviewable by construction.
- **Type hints.** Match the file. Add them to code you are changing anyway;
  do not add them to code you are not.
- **Tests.** One behaviour per test, named for the behaviour. Assert
  results, not calls: a test built on `mock.assert_called_with(...)` tests
  the implementation, breaks on the next refactor, and passes while the
  behaviour is wrong. No test for a dataclass default, a getter, or a
  constant. The test lives in the same patch as the behaviour it covers.
- **Logging.** No `debug` calls left behind. No log inside a per-row loop.
  No `print` in library code.
- **Configuration.** A new knob is a new interface with a support burden.
  It needs a user who asked for it and a default that keeps the old
  behaviour.
- **Dependencies.** Adding one is its own patch with the reason. Bumping
  unrelated ones is not part of a feature.
- **Generated code, migrations, snapshots.** Their own patch, with the
  command that produced them in the message, so the reviewer regenerates
  and diffs instead of reading.

## 6. What agent-written code does, and why each one is a NAK

| Habit | The maintainer's objection |
|---|---|
| Reformats the whole file it touched | "I can't find your change." |
| Renames things "for clarity" inside a feature patch | "Now every caller is in the diff and I can't see the logic." |
| Adds a class with one method | "That's a function." |
| Adds a base class with one implementation | "You designed for a second implementation that doesn't exist. Delete it; add it back when it does." |
| Adds a config dataclass for two parameters | "Two keyword arguments." |
| Wraps a call in `try/except Exception` | "Which error do you expect, and why is ignoring it correct?" |
| Adds a docstring to every function | "The signature already says that. Now I read it twice." |
| Adds `# validate input` above the validation | "I can see it's validation." |
| Writes a helper module for one caller | "Put it next to the caller." |
| Tests that mock the function under test | "This test passes when the code is wrong." |
| Leaves `print("DEBUG: ...")` | "You didn't read your own patch." |
| Commits the notebook with outputs | "Three thousand lines of base64 in my inbox." |
| Bumps the lockfile | "Unrelated. Separate patch, with a reason." |
| Adds a `TODO` | "Do it, or put it in the cover letter." |
| Writes "refactored for maintainability" | "What was hard before?" |
| Mentions the tool that wrote it | "I'm reviewing you, not the tool. Sign it or don't send it." |
