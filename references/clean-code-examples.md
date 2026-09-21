# Taste, demonstrated: what algorithm solutions teach about clean code

Sources: "LeetCode 101: A Grinding Guide", second edition (2024), by
Chang Gao, cited as LeetCode 101 (Gao) with the chapter; the distillation
in `../research/leetcode101-lessons.md`; the anchors in
`../research/anchors.md` and `clean-python.md`; the `bisect`,
`collections`, `itertools` docs. Nothing is copied. Every block below is
original: extract one, run `python3`, its asserts pass.

Algorithm solutions teach taste better than production code because the
problem is fixed. Two solutions to "longest run under a budget" return
the same answers on the same inputs, so nothing that differs between them
is a requirement, a deadline, or a legacy caller; everything left is
taste, and both versions fit on one screen.

Three ideas carry every section. **One template per family**: binary
search is two bounds, a stated convention, and a halving loop (chapter
3); a search is a driver plus one auxiliary descent (chapter 5); dynamic
programming is state in words, then transition, then initial condition
(chapter 6). Recognising it is most of a review: you then read only the
lines where this instance differs from the shape you know. **The
representation that removes the special case**: a dummy node before the
head makes deleting the head ordinary (chapter 12, Linus's
pointer-to-pointer in Python clothing); a padded table kills every
`if i == 0` (chapter 6); the half-open interval kills the not-found flag
(chapter 3). At such an `if`, ask not whether the branch is correct but
what shape of data deletes it. **The invariant stated before the loop**:
what is true between two iterations, and why a nested `while` is still
linear. A diff cannot recover it, so it goes in the docstring.

Read this at the refactor (SKILL.md step 3) and again at the maintainer
pass (step 7). Every pair but 8 agrees; section 8's disagree, its lesson.

## 1. Two pointers: the window is the state

Two indices over one sequence, each moving one way only, and the region
between them is the whole state. The invariant goes in the docstring with
the reason the nested `while` is linear, or a reviewer calls it quadratic.

```python
# as an agent writes it
def longest_run_bloated(sizes_list, budget_value):
    start_position, current_total, best_length = 0, 0, 0
    for end_position in range(len(sizes_list)):
        current_total += sizes_list[end_position]
        while current_total > budget_value:
            current_total -= sizes_list[start_position]
            start_position += 1
        best_length = max(best_length, end_position - start_position + 1)
    return best_length
# as it should be
def longest_run(sizes, budget):
    """Longest contiguous run of sizes summing to <= budget.

    After the while, total == sum(sizes[lo:hi]) <= budget; both pointers
    only move right, so this is O(n), not O(n^2).
    """
    best = lo = total = 0
    for hi, size in enumerate(sizes, 1):
        total += size
        while total > budget:
            total -= sizes[lo]
            lo += 1
        best = max(best, hi - lo)
    return best
case = [2, 1, 5, 1, 1, 2]
assert longest_run_bloated(case, 7) == longest_run(case, 7) == 3
```

**Anchor.** LeetCode 101 (Gao), chapter 2; maintainer-rules.md section 3.

## 2. Binary search: state the convention, then call bisect

Pick one interval convention and keep it. Half-open `[lo, hi)` needs no
guards — an empty list and a target outside the range fall out of
`while lo < hi` — and `found = -1`, a return written as a flag, goes too.

```python
from bisect import bisect_left

# as an agent writes it
def lower_bound_bloated(ts, t):
    left, right, found = 0, len(ts) - 1, -1
    while left <= right:
        middle = left + (right - left) // 2
        if ts[middle] < t:
            left = middle + 1
        else:
            found, right = middle, middle - 1
    return found if found != -1 else len(ts)
# as it should be
def lower_bound(ts, t):
    """Index of the first timestamp >= t, or len(ts) if there is none.

    Half-open [lo, hi): below lo every value is < t, from hi up every
    value is >= t, so lo == hi ends the loop and is the answer, empty
    list included. It is bisect_left.
    """
    lo, hi = 0, len(ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    return lo
a = [1, 3, 3, 7]
for x in (0, 3, 4, 9):
    assert lower_bound_bloated(a, x) == lower_bound(a, x) == bisect_left(a, x)
```

**Anchor.** LeetCode 101 (Gao), chapter 3; `bisect` docs, "Searching
Sorted Lists"; maintainer-rules.md section 2 item 6, no duplicates.

## 3. A table instead of a class hierarchy

A base class with one method per subclass, chosen by name, is a dict with
those names as keys: the table is the behaviour and a new cleaner one line
of data. A name missing from it raises, where the hierarchy passes it on.

```python
# as an agent writes it
class Cleaner:
    def apply(self, value):
        return value

class StripCleaner(Cleaner):
    def apply(self, value):
        return value.strip()

def clean_row_bloated(row, spec):
    made = {"strip": StripCleaner}
    return {col: made.get(spec.get(col), Cleaner)().apply(val)
            for col, val in row.items()}
# as it should be
CLEANERS = {"upper": str.upper, "strip": str.strip, "int": int}

def clean_row(row, spec):
    """Apply CLEANERS[spec[col]]; columns not in spec pass through."""
    return {col: CLEANERS[spec[col]](val) if col in spec else val
            for col, val in row.items()}
row, spec = {"ticker": " aapl ", "note": " x "}, {"ticker": "strip"}
assert clean_row_bloated(row, spec) == clean_row(row, spec)
```

**Anchor.** LeetCode 101 (Gao), chapter 7 (the operator table); Pike's
rule 5, data dominates; review-checklist.md, one-implementation classes.

## 4. Dedupe by key, keeping the last

A dict keyed by the identity is the whole algorithm: assignment
overwrites, so the last write wins and insertion order is the output
order. The swallowed `KeyError` turns a bad file into a short answer.

```python
# as an agent writes it
def dedupe_bloated(rows, key_field="id"):
    out, seen = [], {}
    for row in rows:
        try:
            key = row[key_field]
        except KeyError:
            continue                       # skip malformed rows
        if key in seen:
            out[seen[key]] = row           # replace the earlier one
        else:
            seen[key] = len(out)
            out.append(row)
    return out
# as it should be
def dedupe_keep_last(rows, key="id"):
    """Last row per key, in the order the keys first appeared."""
    last = {}
    for row in rows:
        last[row[key]] = row
    return list(last.values())
rows = [{"id": 1, "px": 10}, {"id": 2, "px": 20}, {"id": 1, "px": 11}]
assert dedupe_bloated(rows) == dedupe_keep_last(rows) == [
    {"id": 1, "px": 11}, {"id": 2, "px": 20}]
```

**Anchor.** LeetCode 101 (Gao), chapters 10 and 15; PEP 20, errors never
pass silently; in pandas, `drop_duplicates(subset=key, keep="last")`.

## 5. Count distinct per group

Two structures the `collections` docs name outright: a set for
membership, a `defaultdict` for "first time we see this group". They
delete the setup branch and the scan, leaving intent where bookkeeping was.

```python
from collections import defaultdict

# as an agent writes it
def distinct_per_group_bloated(pairs):
    result, seen = {}, {}
    for i in range(len(pairs)):
        group, key = pairs[i][0], pairs[i][1]
        if group not in seen:            # first time we see the group
            seen[group], result[group] = [], 0
        if key not in seen[group]:       # linear scan of a list
            seen[group].append(key)
            result[group] = result[group] + 1
    return result
# as it should be
def distinct_per_group(pairs):
    """Number of distinct keys per group, from (group, key) pairs."""
    seen = defaultdict(set)
    for group, key in pairs:
        seen[group].add(key)
    return {group: len(keys) for group, keys in seen.items()}
pairs = [("a", 1), ("b", 2), ("a", 1), ("a", 3)]
assert distinct_per_group_bloated(pairs) == distinct_per_group(pairs)
assert distinct_per_group(pairs) == {"a": 2, "b": 1}
```

**Anchor.** LeetCode 101 (Gao), chapter 4; pandas groupby().nunique().

## 6. One traversal template, iterative

Every depth-first walk is five lines: pop, skip if seen, mark, record,
push the children. Over module globals it also gains two switches any
caller can leave flipped and a recursion limit that real data meets.

```python
# as an agent writes it
visited, order = set(), []
def dfs(graph, node):
    global visited, order
    if node in visited:
        return
    visited.add(node)
    order.append(node)
    for nxt in graph.get(node, []):
        dfs(graph, nxt)
# as it should be
def reachable(graph, start):
    """Nodes reachable from start, in depth-first preorder."""
    seen, order, stack = set(), [], [start]
    while stack:
        node = stack.pop()
        if node in seen:       # after the pop, so the order matches
            continue
        seen.add(node)
        order.append(node)
        stack.extend(reversed(graph.get(node, ())))
    return order
g = {"a": ["b", "c"], "b": ["d"], "c": ["d", "e"], "d": ["a"], "e": []}
dfs(g, "a")                    # and every caller must reset the globals
assert order == reachable(g, "a") == ["a", "b", "d", "c", "e"]
assert len(reachable({i: [i + 1] for i in range(3000)}, 0)) == 3001
```

**Anchor.** LeetCode 101 (Gao), chapters 5 and 13; Google style 2.5.

## 7. Dynamic programming: name the state, then write the transition

Say in words what `dp[i]` means before writing the loop and the
transition writes itself. The initial value must be the identity of the
operation consuming it — `INF` under a `min` — so no branch is needed.

```python
# as an agent writes it
def distance_to_known_bloated(values):
    result = []
    for i in range(len(values)):
        known = [abs(i - j) for j, v in enumerate(values) if v is not None]
        result.append(min(known) if known else None)
    return result
# as it should be
INF = float("inf")

def distance_to_known(values):
    """Distance from each position to the nearest non-None value.

    State: dist[i] is the distance from i to the nearest known value seen
    so far. Transition: dist[i] = min(dist[i], dist[i - 1] + 1) left to
    right, then its mirror right to left, two passes for two directions.
    """
    dist = [0 if v is not None else INF for v in values]
    for i in range(1, len(dist)):
        dist[i] = min(dist[i], dist[i - 1] + 1)
    for i in range(len(dist) - 2, -1, -1):
        dist[i] = min(dist[i], dist[i + 1] + 1)
    return dist
vals = [None, 3, None, None, 7, None]
assert distance_to_known_bloated(vals) == distance_to_known(vals)
assert distance_to_known([None, None]) == [INF, INF]
```

**Anchor.** LeetCode 101 (Gao), chapter 6; maintainer-rules.md section 5.

## 8. Greedy: the exchange argument is the head comment

The loop is trivial; the proof that the local choice is safe is the work,
and it is one sentence, so it heads the function. The key that sentence
justifies is the algorithm: by start, the version below is simply wrong.

```python
# as an agent writes it
def max_non_overlapping_bloated(intervals):
    if intervals is None or len(intervals) == 0:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])    # sort by start
    chosen = [ordered[0]]                                # always take one
    for i in range(1, len(ordered)):
        if ordered[i][0] >= chosen[len(chosen) - 1][1]:
            chosen.append(ordered[i])
    return chosen
# as it should be
def max_non_overlapping(intervals):
    """Largest set of pairwise non-overlapping [start, end) intervals.

    Exchange argument: the interval ending first leaves the most room for
    the rest, so swapping any chosen one for the earliest-ending
    compatible one never shrinks the answer; that justifies key=iv[1].
    """
    chosen, end = [], float("-inf")
    for start, stop in sorted(intervals, key=lambda iv: iv[1]):
        if start >= end:
            chosen.append((start, stop))
            end = stop
    return chosen
ivs = [(1, 9), (2, 3), (3, 5), (4, 8)]
assert max_non_overlapping(ivs) == [(2, 3), (3, 5)]
assert len(max_non_overlapping_bloated(ivs)) == 1    # sorted by start
```

**Anchor.** LeetCode 101 (Gao), chapter 1 (the proof is harder than the
loop); commit-message.md, name the rejected alternative.

## 9. The standard library is the tree you search first

A running quantity is a fold with a name: `accumulate(pnl)` is the curve,
`accumulate(curve, max)` the peak. The same move retires a hand-written
`Counter`, a comparator, a list used as a queue, a heap kept by hand.

```python
from itertools import accumulate

# as an agent writes it
def drawdowns_bloated(pnl):
    curve, peaks = [], []
    for i in range(len(pnl)):
        curve.append(sum(pnl[:i + 1]))          # rebuilt from scratch
        peaks.append(max(curve))                # rescanned every step
    return curve, [peaks[i] - curve[i] for i in range(len(curve))]
# as it should be
def drawdowns(pnl):
    """Cumulative PnL and the drawdown from its running peak."""
    curve = list(accumulate(pnl))
    peaks = accumulate(curve, max)
    return curve, [peak - value for peak, value in zip(peaks, curve)]
pnl = [3.0, -1.0, 4.0, -6.0, 2.0]
assert drawdowns_bloated(pnl) == drawdowns(pnl)
assert drawdowns(pnl)[1] == [0.0, 1.0, 0.0, 6.0, 4.0]
```

**Anchor.** LeetCode 101 (Gao), chapter 10 (the book writes the hand
version once, to teach; in a tree it is the duplicate); Pike's rules 1-2.

## 10. Joins: name the key, the type, and the cardinality

A join makes three decisions and a nested loop states none of them: the
key, the type (does every left row survive), and the cardinality (how many
right rows may match). The last is the one agents skip.

```python
# as an agent writes it
def attach_sector_bloated(rows, sectors):
    out = []
    for row in rows:
        for s in sectors:
            if s["ticker"] == row["ticker"]:
                merged = dict(row)
                merged["sector"] = s["sector"]
                out.append(merged)
    return out
# as it should be
def attach_sector(rows, sectors):
    """Attach each row's sector: key ticker, left join, many-to-one.

    The loop above is merge(how="inner") with no validate: a missing
    ticker drops the position, a ticker twice duplicates it, and the row
    count is the only witness. In pandas: rows.merge(sectors,
    on="ticker", how="left", validate="m:1").
    """
    by_ticker = {s["ticker"]: s["sector"] for s in sectors}
    if len(by_ticker) != len(sectors):
        raise ValueError("a ticker appears twice in sectors")
    return [dict(row, sector=by_ticker.get(row["ticker"])) for row in rows]
rows = [{"ticker": "AAPL", "qty": 5}, {"ticker": "ZZZ", "qty": 1}]
sectors = [{"ticker": "AAPL", "sector": "tech"}]
assert attach_sector(rows, sectors)[1]["sector"] is None
assert len(attach_sector_bloated(rows, sectors)) == 1  # ZZZ disappeared
```

**Anchor.** maintainer-rules.md section 5 and review-checklist.md, "Join
with no keys, no how, no validate"; behaviour-switches.md section 3.4.

## 11. Reading a solution like a maintainer

Five questions on any function in the series, answerable in a minute from
the code in front of you; a "no" is a finding, not a note.

- **The invariant.** What is true between two iterations, and where is it
  written down? If only in the author's head, the next change breaks it.
- **The template.** Which known shape is this, and where does it differ?
  Those lines are the review; if it matches none, ask why first.
- **The count of special cases.** Branches for the first element, the
  empty input, the missing key, the last iteration: what representation
  deletes each? The count can usually go to zero.
- **The names.** Short for indices, one word for state, never the type in
  the name; a boolean recording that something happened is a `return`.
- **The complexity.** A claim about n. A linear-looking nested loop needs
  that argument in the docstring; an optimisation needs its measurement
  in the message and a patch of its own.

Run a block before quoting it: a pair whose assertion you have not watched
pass is an opinion, and one whose assertion passes is evidence.
