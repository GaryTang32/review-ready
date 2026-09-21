# Algorithm and data-structure smells a reviewer can name

Sources: docs.python.org (`collections`, `heapq`, `functools`, `graphlib`,
`itertools`, `sys`), the pandas user guide and 2.0/2.1 release notes, and
"LeetCode 101" (Gao, 2024). Paraphrased; every block is original and runs.

Read this at the refactor (SKILL.md step 3) for every loop, recursion,
cache and container the branch adds or changes, and at the maintainer pass
(step 7), where each `NAK:` extends the list. clean-code-examples.md shows
what a clean loop looks like, in eleven pairs, none repeated here; this file
names loops that read clean and are wrong, or ruinous, on production-sized
data. Detectors named below are in `$S/risk_detectors.py`, group `perf`; a
hit starts the trace and is never a verdict.

Contents: part one, twelve rules (1 shortest searched depth-first, 2
dependency order, 3 a window recomputed per row, 4 a list as a queue, 5 a
container grown in a loop, 6 the cost of a changed loop, 7 a sweep that is a
bisection, 8 recursion depth, 9 caches, 10 transitive grouping, 11 consuming
an argument, 12 heap ties); part two, the rest of the canon in one table;
part three, what to say out loud about a changed loop.

**The counterweight outranks every rule.** Brute force when in doubt
(maintainer-rules.md section 4), and n is usually small: stating a loop's
cost is a review action, changing it is a patch. Four disposals, cited by
name below:

- **added**: the loop is on lines this branch adds. Land the clean form in
  the patch that adds it; new code has no old behaviour to keep.
- **own patch**: old code the branch changes for another reason. The logic
  patch stays minimal; the rewrite follows it, test file unchanged, "No
  functional change.", measurement in the message (part three).
- **deferred**: old code the branch does not touch. One line in `deferred.md`.
- **behaviour change**: the fix moves an output (always in rules 1, 2, 7, 10,
  12; often in 3, 5, 6, 9, 11). Own patch, a test that fails before it, old
  and new result in the message; if nobody asked for it, an open question in
  the cover letter, never a silent fix.

## Part one: twelve rules

### 1. "Shortest" searched depth-first

When the name or docstring says shortest, nearest, fewest or closest, the
traversal is breadth-first: DFS returns the first chain it completes, BFS
reaches nodes in order of distance. The anti-pattern is
`find_ultimate_parent()` or `shortest_fx_path(base, quote)` as a recursive
descent. Given `fund -> holdco -> opco -> target` and `fund -> spv -> target`
it answers 3 when `holdco` is listed first and 2 when `spv` is: dict order,
which is the order rows left SQLite, which moves when someone adds an index.
Every one-route fixture passes. Fix: the traversal template
(clean-code-examples.md section 6) with `deque.popleft()` for `stack.pop()`,
marking a node seen when pushed and carrying its distance. Behaviour change,
tested with the two-route case; if any route will do, the fix is the name, so
ask. Weighted edges (fees, spreads) need a heap and rule 12. NAK: "Named
shortest, searched depth-first. DFS finds reachability, BFS finds distance.
Which do you mean?" By hand: grep `def .*(shortest|nearest|closest|fewest)`.

### 2. Dependency order, and the cycle check as a post-condition

Run order for "this after that" comes from `graphlib.TopologicalSorter` (3.9
and later), and a cycle raises: a failure that can be a post-condition on a
count is never a flag inside the loop. Codex writes a `Step` class with a
`visited` flag, a recursive `visit()` and `except RecursionError: pass`. On a
cycle the caller gets a partial order it cannot tell from success, and the
nightly run executes the committee before its features exist. Nor does
`RecursionError` detect cycles: a legal 1,200-stage chain raises it too.

```python
from graphlib import CycleError, TopologicalSorter

def run_order(needs):
    """Stages ordered so each follows all it needs; raises CycleError."""
    return list(TopologicalSorter(needs).static_order())

needs = {"features": ["prices", "news"], "committee": ["features"]}
assert run_order(needs) == ["prices", "news", "features", "committee"]
try:
    run_order({**needs, "prices": ["committee"]})
    raise AssertionError("a cycle must raise")
except CycleError as err:
    assert "prices" in err.args[1]             # the cycle, for the message
```

Below a 3.9 floor it is Kahn's algorithm: a queue of stages with nothing left
to wait for, then `if len(order) != len(stages): raise`. Pass predecessors as
lists: ties come out in insertion order, and a set of strings iterates in
per-process hash order, so independent stages swap between runs. A run that
used to finish now raises: behaviour change. NAK: "Your cycle detection is a
swallowed RecursionError; its result is a short list the caller cannot tell
from success." By hand: grep `RecursionError`.

### 3. A window recomputed per row, and the leading zero

A sum, mean or count over sliding or arbitrary ranges is computed once, as a
cumulative array with a leading zero: `prefix = list(accumulate(values,
initial=0))`, so `sum(values[lo:hi])` is `prefix[hi] - prefix[lo]` with no
case for `lo == 0`, and the empty range is zero. It is the dummy head again.
The anti-pattern is `for i in range(len(prices)):` around `prices[max(0, i -
w + 1):i + 1]`: O(n * w), about 200 million additions per ticker at w = 200
over ten years of minute bars. In pandas it is `.rolling(w).sum()`, with a
trap: for an integer window `min_periods` defaults to `w`, so the first `w -
1` rows are NaN where the hand loop's `max(0, ...)` gave partial windows;
`min_periods=1` reproduces those, so say which the consumer wants. On floats
a prefix difference is not bit-identical to re-adding the window: keep
hand-written prefix arrays to integers (counts, volume), use the library on
floats, and put the tolerance in the test. The range twin: an amount marked
on every day of `[start, end)` is `delta[start] += x`, `delta[end] -= x` and
one `accumulate`. NAK: "This re-adds the window on every row: cost in n and
w? The leading zero deletes your `max(0, i - w)`; with `rolling`, tell me
what the first w - 1 rows become." By hand: grep `[max(0,` and `sum(` over a
slice; the sure form needs the AST, a slice bound on the loop variable.

### 4. A list used as a queue

Whatever removes from the front, inserts at the front, or trims itself to the
last N is a `collections.deque`: `list.pop(0)` and `insert(0, x)` shift every
element, O(n) a call. The anti-pattern is the tick or headline buffer,
`buf.append(x)` then `if len(buf) > 200: buf.pop(0)`; its twin `window =
window[1:] + [x]`; a BFS frontier read with `pop(0)`. `deque(maxlen=200)` and
one `append` replace both lines, so it wins on taste before cost. A deque
does not slice, and indexes in O(n) away from its ends: convert at the use
with `list(buf)`, and keep the list when the code slices more than it pushes.
Outputs are identical: added, own patch, or deferred. NAK: "That list is a
queue. `deque`, and `maxlen` deletes your length check." Detector: `pop(0) or
insert(0, x) on a list`. By hand: grep `[1:] + [` and `del buf[0]`.

### 5. A container grown inside a loop

Nothing is rebuilt to add one item. `out = pd.concat([out, fetch(t)])` per
ticker, `out = out + [x]`, `np.append(arr, x)` and `text += piece` each copy
everything so far: n copies, O(n^2). The pandas user guide says it outright:
`concat` makes a full copy, so collect first, `frames = [fetch(t) for t in
universe]`, then `pd.concat(frames, ignore_index=True)`. Cost is the smaller
half. `ignore_index` defaults to False, so every chunk keeps its own 0..k
labels and `out.loc[0]` is one row per ticker. The empty seed `pd.DataFrame()`
takes part in dtype resolution under rules pandas is changing (2.1 deprecated
ignoring empty and all-NA entries), so result dtypes ride on the lockfile.
`DataFrame.append` was the same copy and is gone in 2.0; `df.loc[len(df)] =
row` is the same smell a row at a time. `sql += " AND ..."` is the injection
finding first (python-idiom.md section 5, PEP 498 row). "No functional
change." holds only if index and dtypes come out the same. NAK: "Nothing is
appended to a frame in a loop. Collect, concat once, and say what you did
with the index." Detectors: `frame or string grown in a loop`, `quadratic
string build`. By hand: grep `.loc[len(`, `np.append(`, `x = x + [`.

### 6. The cost of every changed loop, and `x in list`

For each loop the branch adds or changes, the author can say its cost in n
and what n is on the nightly run. It is the one rule here that is only a
question: "n is the eleven sectors" closes it and no code changes. It needs
asking because the scripts budget length and nesting, never cost. Three
shapes. `seen = []` with `if t not in seen:` scans `seen` per row: up to 12.5
million comparisons over 5,000 names where a set does 5,000 lookups
(`dict.fromkeys(tickers)` when first-seen order matters). The nested-loop
join: clean-code-examples.md section 10. And `df[df["ticker"] == t]` inside
`for t in universe:`, a full scan per ticker where `df.groupby("ticker")` is
one pass. That rewrite is not automatically "No functional change.":
`groupby` skips names absent from `df` where the loop saw an empty frame,
yields keys sorted (`sort=True`), and drops NaN keys (`dropna=True`). And
`"AAPL" in df["ticker"]` tests the index labels, not the values: a bug in the
same spelling. NAK: "What is this loop's cost in n, and what is n on the
nightly run? A nested loop or `in` against a list needs the answer in the
docstring, or a set." Detectors: `membership test against a list literal or
list()`; `row loop over a frame` (data group). By hand: grep `seen = []`.

### 7. A sweep that is a bisection

When a predicate is monotone in a parameter, the crossing is found by
bisection, and the docstring states the monotonicity, the bounds, and the
tolerance or that the domain is integer and the answer exact. The
anti-pattern is calibration as a grid: `for threshold in np.arange(0.0, 1.0,
0.001):` with a `break`, "the largest lookback that keeps the backtest under
60 s", a yield solver as a linear scan. The answer is a grid point: a sweep
over the model below reports 145,000 shares at a step of 500 and 144,900 at
300, and its test uses the same grid, so it passes forever.

```python
def last_ok(ok, cap):
    """Largest n in [0, cap] with ok(n); ok is true up to some n, then false."""
    lo, hi = 0, cap + 1        # true at lo, false at hi; hi is never evaluated
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo

assert last_ok(lambda n: 0.004 * n ** 0.6 <= 5.0, 10_000_000) == 145_049
```

On a float domain loop `while hi - lo > tol`, `tol` named, and return the
side you claim. Sorted data, not a function, is `bisect`
(clean-code-examples.md section 2). A deliberate grid (the desk quotes steps
of 0.001) is legal when the docstring says so and the step has a name. The
reported number moves: behaviour change. NAK: "This predicate is monotone, so
it is a bisection: state the monotonicity and the tolerance. Today your
answer changes when someone edits the step." By hand: grep `in np.arange(`
and `in np.linspace(`; a hint at best.

### 8. Recursion depth on real data

Every recursion over data has a depth bound the author can name; when the
data sets the depth, it is an explicit stack (clean-code-examples.md section
6 walks 3,001 nodes). CPython's default limit is 1000 frames, counting those
already on the stack. The anti-pattern is a recursive flatten of nested JSON
from a news API, a recursive `_resolve_dependencies()`, a chain of ticker
renames followed by self-call: depth 4 on the fixture, 1,200 on one odd
filing. The agent's repair is `sys.setrecursionlimit(10000)` by the imports.
It bounds nothing, trades a clean `RecursionError` for a C-stack overflow (the
`sys` docs warn a too-high limit can crash), and is a process-wide
import-time switch (behaviour-switches.md section 2). Fix: the stack form
behind a characterization test, or reject the input at the boundary with the
depth in the error. Deleting the `setrecursionlimit` line is its own patch
and not "No functional change.": say which inputs now fail, and how. NAK:
"How deep does this go on production data? Name the bound or use a stack.
`setrecursionlimit` is never the fix." Detector: `recursion limit raised`.

### 9. Caches: what they return, hold, key on, and are bounded by

behaviour-switches.md has the cache as a switch (it freezes the first call's
config). Here, correctness and lifetime:

- **Returns** the same object to every caller. With `@lru_cache` on
  `load_prices(ticker)`, one `df["ret"] = ...` downstream edits every later
  caller's frame, and the damage depends on call order, so it shows in
  production and not in the test. The `functools` docs rule out caching a
  function that must return distinct mutable objects. Cache an immutable, or
  keep the cached function private and have the accessor return `.copy()`.
- **Holds** `self` when it decorates a method: every instance stays alive
  until evicted, so a long-running job leaks a loader and its frames per
  cycle. Cache a module-level function on plain values, or `cached_property`.
- **Bounded by** nothing under `@cache`, which is `lru_cache(maxsize=None)`
  and needs 3.9: keyed on a date or ticker in an all-day process, a leak with
  a nice name. `maxsize=N` is a capacity decision the message explains.
- **Keys on** hash and equality. An unhashable argument fails loudly; the
  quiet failure is `date(2024, 1, 2)` against `datetime(2024, 1, 2)`, or
  `f("AAPL")` against `f(ticker="AAPL")`: separate entries, a hit rate of
  zero, and nothing says so. Assert on `cache_info()` in the test.

A hand-rolled LRU (a dict beside a list with `.remove()`) is
`OrderedDict.move_to_end` and `popitem(last=False)`, or `lru_cache`.
Returning a copy is a behaviour change for a caller that relied on the shared
mutation: grep the callers. NAK: "What does this cache hand back, and can a
caller mutate it? What bounds the key space? Why is it on a method?"
Detector: `cache on a method`. By hand: grep `@cache` and `maxsize=None`.

### 10. Transitive grouping done pairwise

Grouping by a "same as" relation (shares an ISIN, a CUSIP, a normalised name,
co-occurs in an article) is connected components: if A~B and B~C then A, B
and C are one group, whatever order the pairs arrive in. A loop that attaches
each pair to the first group holding either member never merges two existing
groups. That failure mode is what union-find has to teach a reviewer.

```python
def groups_pairwise(pairs):                    # as an agent writes it
    groups = []
    for a, b in pairs:
        for g in groups:
            if a in g or b in g:
                g.update((a, b))
                break
        else:
            groups.append({a, b})
    return groups

def groups(pairs):
    """Connected components of the 'same issuer' pairs, as sets."""
    leader = {}
    def find(x):                               # walk up to the group's leader
        while leader.setdefault(x, x) != x:
            x = leader[x]
        return x
    for a, b in pairs:
        leader[find(a)] = find(b)              # merging leaders merges groups
    out = {}
    for x in leader:
        out.setdefault(find(x), set()).add(x)
    return list(out.values())

pairs = [("A", "B"), ("C", "D"), ("B", "C")]
assert groups_pairwise(pairs) == [{"A", "B", "C"}, {"C", "D"}]   # C twice
assert groups(pairs) == groups(pairs[::-1]) == [{"A", "B", "C", "D"}]
```

`find` walks uncompressed chains: fine for thousands of names; past that,
`networkx.connected_components` if the tree already depends on it. The test
reorders the pairs, because the result must not depend on their order. Groups
change: behaviour change. The message gives how many production groups merged
and the size of the largest, since one noisy link ("Holdings") chains
unrelated issuers; group ids already persisted in the registry are an open
question for the user. NAK: "Is this relation transitive? If A~B and B~C, are
A and C together? Show me the three-pair case, in both orders." No detector;
ship the question.

### 11. Does this function consume its argument?

A function leaves its argument as found, or its docstring says it consumes or
mutates it. **An iterator read twice**: a generator, `csv.reader`, a
`sqlite3` cursor, a file, `zip`, a chunked `read_csv` give one pass and are
then empty, with no error. **Mutation behind a return**: `def enrich(df):
df["sector"] = ...; return df` edits the caller's frame and the `return`
hides it, so comparing `prices` with `enrich(prices)` compares a frame with
itself. Also `rows.sort()` in a summariser, a recursive walker appending to a
shared `path` without popping, a one-element list as an out-parameter.

```python
import sqlite3

def up_down(returns):                          # as an agent writes it
    ups = sum(r > 0 for r in returns)
    downs = sum(r < 0 for r in returns)        # a second pass
    return ups, downs

rets = [0.01, -0.02, 0.03, -0.01]
assert up_down(rets) == (2, 2)
assert up_down(r for r in rets) == (2, 0)      # a generator: silently wrong
cur = sqlite3.connect(":memory:").execute("SELECT 1 UNION SELECT 2")
assert len(list(cur)) == 2 and list(cur) == [] # a cursor is one pass too
```

The standard library's convention is the rule: `list.sort()` mutates and
returns `None`; `sorted()` returns a new list and leaves its input. Doing
both misleads about one. Fix: one pass, or `rows = list(rows)` on the first
line; `df.assign(...)` instead of writing into the argument; a tuple instead
of an out-parameter. The copy is a behaviour change for the caller that
ignored the return value and relied on the mutation: grep the callers. NAK:
"Does this mutate what it was given? Then the docstring says so, or it
copies. And what happens when I pass a generator?" Detector: `inplace=True`
(data group); the rest needs the AST, a parameter mutated and then returned.

### 12. Heap entries that can compare on the payload

A tuple on a heap compares left to right, so `heappush(heap, (score, row))`
compares the rows when two scores tie, and dicts do not order: `TypeError:
'<' not supported between instances of 'dict' and 'dict'`. Scores tie
constantly in finance (zero returns, integer ranks, rounded z-scores), so it
is a production crash no fixture with distinct scores reproduces. Fix: a
counter as the second element, `(score, next(arrival), row)` with `arrival =
itertools.count()`, the `heapq` docs' own recipe, which also returns ties in
arrival order; or, for a finite collection, `heapq.nlargest(k, rows,
key=...)`, which has no tuple to trap. `sorted(rows, key=...)[:k]` is just as
right at a few thousand rows: the detector `sorted() to take the first or
last few` is a prompt to state n and k, not a finding, and the `heapq` docs
themselves send large k to `sorted` and k == 1 to `max`. A bug fix: own
patch, with the tie test. NAK: "What happens when two priorities tie?" By
hand: grep `heappush(` and read the tuple.

## Part two: the rest of the canon, one row each

The audit judged these rarely relevant to a pull request in a pandas and
SQLite tree. They are on record so the shape is recognised and the library
call named. None licenses a rewrite, and a hand-written one is "Duplicate of
X in Y" (review-checklist.md).

| Family | Where it surfaces in ordinary code | The idiom or the call | When it is not worth a reviewer's attention |
|---|---|---|---|
| Backtracking: modify, recurse, restore | enumerating parameter or portfolio combinations; a walker sharing one `path` list | `itertools.product`, `combinations`, `permutations`; pass `path + [x]` down, or return tuples (rule 11) | The template, always. The only finding is the shared list that is not restored. |
| Monotonic stack, monotone deque | "bars until the next higher close"; rolling max or min | `Series.rolling(w).max()`; by hand, a stack of indices whose values only decrease | It turns O(n^2) into O(n) and nothing else. Rule 6 states the cost; the rewrite waits for a measurement and its own patch. |
| `heapq` for top-k | top-N signals, worst N drawdowns | `heapq.nlargest(k, xs, key=...)`, `Series.nlargest(k)`, `Counter.most_common(k)` | `sorted(...)[:k]` at a few thousand rows is correct, stable and obvious; k == 1 is `max`. Only rule 12's tie matters. |
| Counting sort, bucket sort | counts per category, histograms, top-k by frequency | `Counter`, `value_counts()`, `np.bincount`, `pd.cut` | Nobody hand-writes one here; if they did, it duplicates clean-code-examples.md section 5. |
| Bit flags and masks | exchange condition codes or FIX bitmasks from a feed; an agent packing booleans | `enum.IntFlag` with named members, then `flags & Cond.ODD_LOT` | A mask from upstream is a magic number until named, and nothing more. A mask the patch invented is "Clever.": boolean columns. |
| Grid and matrix walks with direction vectors | almost never; a correlation matrix is not a grid walk | `DIRS = ((0, 1), (1, 0), (0, -1), (-1, 0))` and one bounds check | Interview-only here. If it appears, ask what problem needed it. |
| Divide and conquer | a hand-written merge sort, quickselect or median | `list.sort`, `heapq.nsmallest`, `statistics.median`, `np.partition`; `heapq.merge` for sorted streams | Always a duplicate. Its residues are shipped: the dispatch table (clean-code-examples.md section 3) and memoisation (rule 9). |
| The dummy head and other sentinels | `if i == 0`, `if prev is None`, a branch for the first or last iteration | the leading zero (rule 3), `accumulate(..., initial=)`, `INF` under `min`, `float("-inf")` as the first end, `zip(xs, xs[1:])` | A hand-rolled linked list is itself the NAK. The sentinel idea is always worth it: "Special case for the first / empty / missing one." |
| Tries | prefix lookup over tickers or issuer names | a sorted list and `bisect_left` for the prefix range; `str.startswith` with a tuple; `LIKE 'abc%'` on an indexed column | Never hand-built at a few thousand names: a dict of dicts with an end marker is "New abstraction, one user." |
| Level-order grouping | "group the DAG by stage"; a feature's depth from the raw table | one `deque`: `len(q)` when a level starts is that level's size, so pop exactly that many; or `TopologicalSorter.get_ready()`, whose batches are the stages that can run together | Two lists and a swap is correct; rewrite only on added lines. It arises only once rule 1 put BFS on the table. |
| String algorithms (KMP, Manacher, rolling hash) | substring search in headlines, ticker extraction, fuzzy issuer names | `in`, `str.find`, `re`, `difflib.SequenceMatcher` | A hand-written matcher is a duplicate. The string items worth a look are `+=` in a loop (rule 5) and SQL built from text. |
| Mathematics (gcd, sieve, fast power, shuffle, reservoir sampling) | lot-size rounding, sampling rows, shuffling a universe | `math.gcd`, `pow(b, e, m)`, `math.fsum`, `random.sample`, a passed `default_rng(seed)` | Call the library and name it in the message (clean-code-examples.md section 9). The review item is the seed (behaviour-switches.md, runtime environment), never the algorithm. |

## Part three: what to say out loud about any changed loop

Before the PR opens, the agent can say each of these in one sentence for
every loop, recursion and cache the branch adds or changes. A sentence that
cannot be said is the finding; one that can closes it.

1. What n is on the nightly run (rows, tickers, days, articles), and the cost
   in n.
2. Which container each collection is, by the operation done most: membership
   is a set or dict, both ends a deque, smallest-first a heap, an ordered
   range a sorted list and `bisect`.
3. The invariant between two iterations (clean-code-examples.md section 11),
   and for a search, whether the question is reachability or distance.
4. What happens on empty input, a tie, a cycle, a depth of ten thousand, and
   the same input in another order; a reordered fixture tests the last.
5. Whether the function leaves its arguments as found, and whether it can be
   handed a generator.
6. What a cache returns, holds, keys on, and is bounded by.
7. Whether the fix moves any output. If so it is a behaviour change, whatever
   its motive.

**A performance rewrite is never mixed into a logic patch.** Measure first,
on production-sized input, and confirm with `python -m cProfile -s cumtime`
that this loop is where the time goes. Then it is its own patch after the
logic patch, test file unchanged, and the message carries n, the before and
after timings, and the command that produced them. No number, no patch: an
unmeasured rewrite is "Clever.", and it goes to `deferred.md`.
