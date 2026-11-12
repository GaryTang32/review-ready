# Minimal code: the rewrite step

Pruning deletes hunks the problem does not need. It cannot touch the more
common waste in agent-written code: hunks the problem *does* need, written
at two to four times the length a maintainer would accept. A forty-line
function that should be ten survives every prune, because the problem needs
the function. This step rewrites what survived until it is as short as the
problem allows, and the builder's size gate checks that it did.

Contents: 1 the procedure, 2 the shrink moves, 3 tests, 4 what is never cut
for length, 5 the size gate and `size_ok`, 6 a worked example.

## 1. The procedure

Work file by file, largest first (the audit's size table, or `size.md` from
the last build), with `focus.md` open (SKILL.md step 0). For each changed
function it gives the call chain, every caller with the line that calls it,
what the function calls, and which additions are used once, never called or
already in the tree. Read the code with those relations in hand, not file by
file in isolation: most of the moves below need to know who else touches the
code.

1. **Write the contract** for the file's changed code in five lines at most:
   what each changed function takes, returns and raises, and which surviving
   tests specify it. If you cannot write it in five lines, the file does more
   than the problem needs; go back to the prune.
2. **Start from the base, not from the branch.** Open `git show <base>:<path>`
   (or an empty file for a new one) and write the change the contract needs.
   Editing the branch's version line by line anchors you to its shape: you
   will delete a comment here and a blank line there and keep every layer.
   Read the branch only to list the cases it handles.
3. **Account for every case you dropped.** For each case the branch handled
   and your version does not: either the problem needs it, and it comes back
   in its shortest form, or it goes in `deferred.md` as `behaviour`, saying
   what it did. Nothing is lost silently, and nothing is kept just because the
   agent wrote it.
4. **Run the tests**, the surviving ones and the branch's own (the builder
   runs the latter for you).
5. **Measure** against the branch. The target is half or less, per file and
   in total. Still over: go through section 2 again, one move at a time.
   Still over after that: section 5.

"Shorter" means fewer concepts for the reader, not fewer characters. Joining
lines, one-letter names, nested comprehensions three deep and walrus chains
make code shorter and harder to read. The maintainer NAKs that as "clever",
and the size gate's line count is no defence.

## 2. The shrink moves

In rough order of how much they usually save in agent code.

1. **Inline the single-use layer** (`focus.md`: "used once", "classes with
   at most one method"). A helper called once, a class with one
   method, a wrapper that forwards, a base class with one subclass, a
   factory for one product, a constants module for one constant. Inline it
   at its caller; it can come back at the third user.
2. **Validate once, at the boundary.** Agent code re-checks types, `None`,
   emptiness and ranges in every function along the call chain. The focus
   map shows each caller's line: what every caller already passes needs no
   check inside. Check where
   the data enters (user input, file, network, the public function) and
   trust it below. A check for a state the caller cannot produce is deleted,
   not deferred.
3. **One error handler at the edge.** `try/except` wrapped around each step,
   logging and re-raising, becomes nothing: let it propagate to the one place
   that can do something about it. A handler is kept only when it changes
   the outcome (retry, fallback, a better message to a user).
4. **Use what the language and the tree already have.** A hand-written loop
   that is `sum`, `any`, `all`, `max(key=)`, `sorted(key=)`, `dict.get`,
   `setdefault`, `Counter`, `defaultdict`, `itertools`, `pathlib`, `str.join`,
   or a pandas method. A function the tree already defines (`focus.md`:
   "the tree already has it"). The shortest code is the code you call.
5. **Data instead of branches.** An `if/elif` chain mapping a value to a
   value, or parallel branches differing in one constant, becomes a dict or a
   tuple of rows and one lookup.
6. **Early return instead of nesting.** Guard clauses at the top delete the
   `else` blocks and an indentation level from everything below them.
7. **No knobs nobody asked for.** A parameter every caller passes the same
   value, a flag with one position used, a config key for a constant, a
   `**kwargs` passed through "for flexibility". The value goes inline.
8. **No compatibility for code that never shipped.** A shim, alias or
   deprecation path for the branch's own earlier draft has no user.
9. **Names that are used once and explain nothing.** `result = f(x);
   return result` is `return f(x)`. A temporary stays only when its name
   says something the expression does not.
10. **Comments, docstrings and hints, per comments.md and python-idiom.md.**
    No docstring that restates the signature, no comment narrating the next
    line, no history. Type hints on the public surface the change adds.
11. **Logging that nobody reads.** "Entering function", "processing row",
    "done" are deleted. A log line stays when an operator would act on it.

### Algorithms and patterns

**A better algorithm is a shrink move only where the cost is real.** The
focus map lists the loops the branch wrote that hide a second loop, and the
callers show how often the function runs. Where n is large (rows of a
table, files of a repo, a hot path with many callers), the standard
replacements in algorithm-smells.md and clean-code-examples.md are usually
shorter as well as faster: a set for membership, a dict for a join, `bisect`,
`heapq`, two pointers, a window kept rather than recomputed. Where n is a
few dozen, brute force is shorter, obvious and right; a clever algorithm
there adds code and risk and saves nothing. A rewrite for speed is measured
first and is its own patch.

**A design pattern is not a shrink move.** Strategy, factory, repository,
observer, an abstract base class, dependency injection for one
implementation: each adds a layer, and layers are what make agent code long.
A pattern earns its place when three real cases already exist, not for the
one in this branch. The structural ideas that do shorten code are already
moves above: data instead of branches (move 5), a representation that
removes the special case (maintainer-rules.md section 4), and one function
that does real work rather than three that forward to each other (move 1).

## 3. Tests

Agent branches often carry as many test lines as code lines. testing.md is
the standard; for length, three rules do most of the work:

- **One test per behaviour in the problem statement**, the smallest that
  fails without the change. An edge case gets a test only where the code has
  a branch for it.
- **One table, not six copies** (testing.md section 6): cases that differ in
  input and expected value are rows of one parametrised test.
- **Delete** tests of the standard library, of trivial accessors, of mocks,
  and of internal calls rather than results. Fixtures used by one test go
  inline.

Characterization tests and tests of both switch positions are written only
when the rules that require them apply (repo-audit.md section 3,
behaviour-switches.md section 4), not by default.

## 4. What is never cut for length

- A risk fix: security, look-ahead, a silent NULL coercion. Each keeps its
  own patch and test (SKILL.md ground rules).
- Error handling at a real boundary: I/O, the network, a subprocess, input
  from a user or a vendor file.
- The test that pins the behaviour change.
- A name or a one-line comment that tells the reader *why*.
- Behaviour the problem needs. If the rewrite cannot be half the length
  without losing it, that is a `size_ok` reason, not a reason to drop it.

## 5. The size gate and `size_ok`

The builder writes `size.md`: per file, the lines the branch changed and the
lines the series changes, against the fork point, ignoring whitespace and
blank lines. The series may keep at most `shrink` (default 0.5) in total and
in every file the branch changed by 20 lines or more. Branches under 100
such lines are not gated. A failed gate fails the build, and `--land` refuses
the series. A stack does not get around it: the gate measures the whole
series. Nor does cramming: a Python file is marked CRAMMED, and fails, when
the series has more statements sharing a line (`a = 1; b = 2`, a body on its
`if` line) or more lines past 120 characters than the branch had.

A file that cannot shrink goes in the plan's `size_ok` with the reason, and
the reason goes in the PR description. Reasons a reviewer accepts:

- generated, vendored or migration code, reviewed at its source;
- a data table or fixture whose rows are the content;
- a file whose every line the contract needs, after section 2 was applied:
  say which moves you tried and what each left.

"It is needed" is not a reason; it was needed before the rewrite too.
Raising `shrink` for the whole plan needs `shrink_reason`, for example when
the owner asked for a split only.

## 6. A worked example

The branch, 31 lines, to read a price threshold per ticker:

```python
class ThresholdConfigLoader:
    """Loads threshold configuration."""

    def __init__(self, config: dict, default_threshold: float = 0.05):
        self.config = config
        self.default_threshold = default_threshold

    def get_threshold(self, ticker: str) -> float:
        """Get the threshold for a ticker.

        Args:
            ticker: The ticker symbol.

        Returns:
            The threshold as a float.
        """
        if ticker is None:
            raise ValueError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError("ticker must be a string")
        try:
            thresholds = self.config.get("thresholds", {})
            if ticker in thresholds:
                value = thresholds[ticker]
                result = float(value)
                return result
            else:
                return self.default_threshold
        except Exception as e:
            logger.error(f"Failed to get threshold for {ticker}: {e}")
            raise
```

The contract: given the config and a ticker, the ticker's threshold, or 0.05.
One caller, which always passes a ticker string and never the default. The
rewrite, 2 lines:

```python
def threshold(config, ticker):
    return float(config.get("thresholds", {}).get(ticker, 0.05))
```

What went, and where it is recorded: the class and its constructor (one
user: move 1); the `None` and type checks (the caller cannot produce either:
move 2); the `try` that logs and re-raises (move 3); the `if/else` (move 4,
`dict.get`); `default_threshold` (move 7: no caller passes it, so it is a
`noise` line in `deferred.md`); the docstring (move 10). Nothing in the
problem needed any of it, and the reader now checks one line instead of
thirty-one.
