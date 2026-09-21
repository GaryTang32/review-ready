# Python the language: contracts a diff can silently change

Read this at the refactor step (SKILL.md step 3, "Language contracts") when
the diff touches a type hint, a record type, `__eq__`, a `yield`, a guard
clause, an exception class, an enum, `async def`, a `with`, a sort, a joined
path or a `@property`; at the prune (step 2, item 5) to decide whether a guard
survives; in audit mode for section 12; at the maintainer pass (step 7), where
section 15 holds the NAK line for every rule here. python-idiom.md rules on
how a spelling reads; this file is about the one-token diff (a decorator, a
base class, a bracket, a `yield`) that changes what every caller gets while
each line still reads clean. It does not repeat python-idiom.md on hints,
chaining, `with` and truthiness, algorithm-smells.md rule 11,
data-correctness.md sections 6 and 8, or security.md section 6. Sources:
docs.python.org and PEPs 440, 492, 517, 518, 544, 585, 586, 589, 621,
paraphrased; the code is original. Every `python` block runs on Python 3.9,
standard library only; "(checked)" marks a claim run the same way, unshown.

Contents: 1 the floor, 2 name the replacement, 3 records, 4 `__eq__` and
`__hash__`, 5 generators, 6 EAFP and LBYL, 7 exceptions, 8 enums, 9 async, 10
context managers, 11 sorting, 12 pyproject, 13 paths, 14 properties, 15 NAKs.

**Disposal.** Everything below is a contract change: a behaviour change whose
diff is one token. In code this branch added, fix it in the patch that adds
it. In pre-existing code it is never "No functional change.": its own patch,
a test pinning the contract (the stored text, the hash, the second pass), a
message naming every caller; or, when stored data or another job depends on
the old contract, an open question in the PR description.

## 1. The floor: which Python must this import on?

python-idiom.md ("Annotations run, and when depends on the floor") asks
whether a spelling imports on the tree's minimum Python. The answer is a
declared number, never this machine's interpreter: `requires-python` in
`pyproject.toml`, else `python_requires`. `repo_map.py` prints it under
"Python floor" with each `match`, walrus and `X | None` above it; copy it into
`problem.md`. Its limits: it reads only a `>=X.Y` bound. It knows which
annotations run: under `from __future__ import annotations`, and on a local
variable always, an `X | None` annotation is not reported, while an alias
(`Price = float | None`) or an `isinstance` argument is. Run under a Python
older than the tree's syntax, it cannot parse a module using `match`; the map
lists it under "Files this interpreter could not parse", and every absence the
map claims (dead, untested) is then blind to that file, so rerun with an
interpreter at or above the floor.

| Needs | Spelling (grep the added lines for the names) |
|---|---|
| 3.8 | `TypedDict`, `Literal`, `Protocol`, `get_args`, `cached_property`, `:=` |
| 3.9 | `list[str]`, `dict[str, float]` (PEP 585; below it `TypeError: 'type' object is not subscriptable` at import; `List[str]` beside `list[str]` in one diff is "match the file" on any floor), `zoneinfo`, `str.removeprefix`, `Path.is_relative_to`, `dict \| dict`, `functools.cache`, `asyncio.to_thread` |
| 3.10 | `X \| None` (PEP 604), `match`, `zip(strict=True)`, `dataclass(slots=True, kw_only=True)`, `itertools.pairwise`, `bisect(key=)` |
| 3.11 | `StrEnum`, `tomllib`, `asyncio.TaskGroup`, `asyncio.timeout`, `except*`, `typing.Self`, `Required`/`NotRequired`, `datetime.UTC` |
| 3.12 | `itertools.batched`, `type X = ...`, `def f[T]`, `typing.override` |

The future import rescues only the PEP 585 and 604 spellings, only inside an
annotation, and with python-idiom.md's traps (an alias, a base class,
`get_type_hints()`, pydantic). As it appears: the agent developed on 3.12, the
tree declares `>=3.9`, and the patch adds `def load(path: str | Path) ->
list[Row]` to a module without that import, `from enum import StrEnum`, and
`zip(tickers, weights, strict=True)`. All pass the author's tests and fail at
import on the host that runs the nightly job. The other direction is as real:
the owner's tree declares `>=3.11`, so a NAK on `X | None` there is the false
one. A floor its rivals contradict (the CI matrix, `.python-version`, ruff's
`target-version`, the interpreter on the job's host), or no floor at all, is a
finding and an open question. Fix: change the spelling, or move the floor in
its own patch naming the machines that run the code.

## 2. A NAK that removes something names what replaces it

python-idiom.md NAKs `Dict[str, Any]`; review-checklist.md deletes the base
class with one implementation. Left there, the next draft is the same thing
respelt: `Mapping[str, object]`, the ABC renamed. Name the replacement.

| Removed | Replacement | What the replacement does not do |
|---|---|---|
| `Dict[str, Any]` on a record: a parsed row, a config section, the payload an LLM call returns | `TypedDict` naming the keys; `total=False` when every key is optional (`Required`/`NotRequired` need 3.11) | It is erased at run time: the value is a plain `dict` and nothing is checked (checked). The boundary check python-idiom.md demands ("Hint the public boundary") is still owed |
| the same, where the keys are data: vendor columns, tickers, a key set that varies per run | stays a dict with the value type named: `dict[str, float]` | |
| `str` with a closed set of values: `mode`, `how`, `keep`; in the owner's tree, record fields whose legal values live in a trailing comment (`# "rss" \| "extracted" \| "unknown"`) | `Literal[...]`, one alias read by the hint and by the boundary check through `get_args`, so the alias has two users; the branches are exactly those values and the last `else` raises | Not checked at run time either. Values that are stored, or compared in several modules, have earned an enum (section 8) |
| ABC with one implementation | nothing: delete it | |
| ABC with two real implementations: two price vendors; the SQLite store and the fake the tests pass in | in a file that hints, a `Protocol` beside the consumer: no inheritance, no base for the implementers to import. In a file that does not hint, nothing; duck typing already works | `@runtime_checkable` plus `isinstance` checks that the method names exist, never their signatures (checked). It is not validation |

No detector: `git diff <base>..<head> | grep -nE "^\+.*(\[str, *Any\]|-> *Any\b|\bABC\b|abstractmethod)"`.

## 3. dict, NamedTuple, dataclass: pick one, and know what a switch changes

Keys that are data stay a dict. Fields that are the program become a
`NamedTuple` (immutable, hashable, already a tuple for `executemany`,
`csv.writer`, `zip`) or a `@dataclass` (mutable, methods, `__post_init__`);
`attrs` or pydantic only where the tree already imports it. The commit body
says which and why. Converting one into another changes a contract:

| Contract | dict | `NamedTuple` | `@dataclass` | `@dataclass(frozen=True)` |
|---|---|---|---|---|
| `==` | by content | by content; equals a plain tuple, and any other NamedTuple with the same values, so a `Quote` equals a `Bar` | same class and equal fields | same |
| hashable: set member, dict key, `lru_cache` argument | no | yes | **no**: `eq=True` sets `__hash__` to `None` | yes while every field is; a `list` field fails at the first `hash()`, not at definition |
| `<`, `sorted` | `TypeError` | field by field | `TypeError`; with `order=True`, field by field in definition order, so reordering fields re-sorts | same |
| mutation | yes | no; `_replace` copies | yes | `FrozenInstanceError`; `dataclasses.replace` copies |
| `a, b = row`, `len`, `row[0]`, `executemany(sql, rows)`, `csv.writer` | keys only | all of them | none; `executemany` raises "parameters are of unsupported type" | none |
| `json.dumps` | an object | a list, names lost | `TypeError`; `asdict` first | same |
| adding a field | invisible | changes `len`, breaks every unpack, adds a CSV column, breaks the `executemany` binding count | a new positional parameter of the generated `__init__` | same |

As it appears: the agent "modernises" `Bar` from a NamedTuple to a dataclass
inside a feature patch, and `for ticker, close in bars` and an `executemany`
three modules away now raise. Or it adds `fetched_at` in the middle of the
field list, and every positional `Bar("AAPL", 189.5, 1_000)` still runs, with
the volume in `fetched_at`. **Field order is a positional signature**: new
fields go last with a default, or construction is by keyword. `audit_diff.py`
reads `def` lines for its public-surface report, so a generated `__init__` is
not in it; `dataclass or model field default` matches `field(default=...)`,
not a bare `mode: str = "paper"`, which is as much a switch.

## 4. `__eq__` without `__hash__`, and whether a key stays a key

A class that defines `__eq__` and not `__hash__` gets `__hash__ = None`, and
`@dataclass` does exactly that.

| Look for | What it does | Fix |
|---|---|---|
| `@dataclass` or `__eq__` added to `Article` "so the test can compare two records" | `set(articles)`, the dedupe dict keyed on the record, and the `lru_cache` on a function that takes one raise `TypeError: unhashable type`, in the nightly run and not in the test that motivated the change | `frozen=True` when nothing assigns to a field (grep for it; freezing is a behaviour change too); else key on the identifying fields, `(a.url, a.published_at)`, never on the object |
| `unsafe_hash=True` on a record the code mutates | change a field after insertion and the entry is still listed and can no longer be found | the same: freeze it, or key on fields that never change |
| an id used as a dict key on both sides of SQLite and a frame | `1`, `1.0` and `True` are one key (the first key object stays, the last value wins), so `int` against `float64` matches, and stops matching once either side passes through `str`: `"1234"` against `"1234.0"` | one dtype for the key, fixed at the read (data-correctness.md section 6) |
| a key that can be NaN | NaN equals nothing, itself included: a dict finds it by object identity only, so two missing ids are two keys and `Counter` over a float column counts each NaN apart (checked) | drop or name the missing key before grouping, and count it |

```python
from dataclasses import dataclass

@dataclass
class Fill:
    ticker: str
    qty: int
@dataclass(unsafe_hash=True)
class Position:
    ticker: str
    qty: int

assert Fill.__hash__ is None                       # eq=True, not frozen: {Fill("AAPL", 10)} is a TypeError
pos = Position("AAPL", 10)
book = {pos: "open"}
pos.qty = 20
assert pos not in book and list(book) == [pos]     # still listed, no longer found
assert {1: "int", 1.0: "float", True: "bool"} == {1: "bool"}
```

NAK: "It was hashable a minute ago. What holds one in a set?" No detector:
`git grep -n "<Class>"` for each class the diff decorates or gives an `__eq__`.

## 5. Generators: lazy, single-use, and one token away from a list

algorithm-smells.md rule 11 covers the consumer that reads its argument twice.
This is the producer's side: four diffs, each a switch with no logic line.

| The diff | What changed for every caller |
|---|---|
| `[...]` becomes `(...)`, or `return rows` becomes `yield row`, "to save memory" | The value is single-use: `n = sum(1 for _ in rows)` and then `total = sum(r.px for r in rows)` gives `0.0` with no error. `len()` and indexing raise; `if rows:` is always true. The same holds for `map`, `filter`, `zip`, `csv.reader`, a `sqlite3` cursor, `read_csv(chunksize=)`, `Path.glob`, `os.scandir` |
| a `yield` added anywhere in a function | Calling it runs nothing. `store_all(rows)` with a `yield` in its body and no consumer writes no rows and raises nothing; so does `map(store, rows)`. Every exception moves to the consumer's line, so a `try` around the call catches nothing, and python-idiom.md's "which line in this `try` raises?" is answered "none" |
| a generator returned from inside `with` | The file, connection or temporary directory is released before the first row is read: `I/O operation on closed file`, `Cannot operate on a closed database`, raised in the consumer by a traceback that points away from the bug. The mirror: a `with` inside a generator function stays open when the consumer stops early (`next(gen)`, `break`) until the generator is closed or collected |
| a generator expression reading a name assigned later | Only the outermost iterable is evaluated at creation; the condition and the element read their names at iteration. `per_day[d] = (r for r in rows if r.day == d)` in a loop over `d` filters every entry by the last `d`, and an `as_of` read inside one is read when consumed |

```python
def parse(line):
    ticker, px = line.split(",")
    return ticker, float(px)
rows = (parse(l) for l in ["AAPL,189.5", "MSFT,402.1"])    # one bracket away from a list
assert bool(rows) and sum(1 for _ in rows) == 2            # never falsy; len(rows) raises TypeError
assert sum(px for _, px in rows) == 0                      # the second pass is empty, silently
day = "2026-09-17"
todays = (r for r in [("AAPL", "2026-09-17"), ("AAPL", "2026-09-18")] if r[1] == day)
day = "2026-09-18"                                         # read at iteration, not at creation
assert list(todays) == [("AAPL", "2026-09-18")]
```

Fix: across a function boundary return a list or tuple. Keep a generator only
when the rows cannot fit in memory; then the name says so (`iter_filings`),
the docstring says "one pass", and the caller opens the resource and passes it
in. In pre-existing code the message names every caller and its use of the
value: truth test, `len`, second loop, index. No detector: grep the added
lines for `yield`, `map(`, `filter(`, `(... for ...)`, then open every caller.

## 6. EAFP or LBYL: which guards survive the prune

SKILL.md step 2 item 5 deletes "defensive code for states that cannot occur".
This makes it decidable, and names the form to ask for.

| The guard | Verdict | Why |
|---|---|---|
| states a domain precondition: a negative notional, an `as_of` after today's session, an empty universe, weights that do not sum to one, a batch about to be written when half of it is invalid | survives, at the top, raising a specific error | the next operation would accept it and be wrong, or fail late, far away, or after a side effect: arithmetic does not object to a negative quantity, and a failed INSERT does not un-send the order before it |
| predicts the exception the next line raises anyway: `if df is None: raise ValueError("df is None")` above `df.merge(...)` | pruned | the next line says it as well |
| pre-checks state outside the process: `if os.path.exists(p): open(p)`; SELECT, then INSERT | replaced by the attempt and one specific `except` | the check is the race: the file is rotated between the two calls, the other writer inserts first. `except FileNotFoundError`; a UNIQUE key with `ON CONFLICT` (data-correctness.md section 8) |
| approximates the operation: `if s.isdigit(): int(s)` | replaced by `int(s)` and `except ValueError` | a second, wrong parser: `"\u00b2".isdigit()` (a superscript two) is true and `int("\u00b2")` raises; `"-5"` and `" 7 "` fail the check and convert fine (checked) |
| a wide `try` for one lookup: `except KeyError` around `score(weights[t])` | one line in the `try`, the rest after it or in `else:` | it also catches the `KeyError` from a bug inside `score`. Often the right form is no `try` at all: a missing key that means a bug is `weights[t]`, propagating |
| `weights.get(t, 0.0)` offered as the neutral spelling | a switch, not a guard (`dict.get with a non-None default`; behaviour-switches.md section 2) | a missing weight has just become a number |

The usual sequence: the reviewer NAKs a thirty-line `try` and the next draft
is `if key in d and d[key] is not None and ...`, the other error. Name the form.

## 7. Exception hierarchy: what "Broad except." needs as its answer

review-checklist.md says catch the error you expect; python-idiom.md
("Conflicts", `except Exception`) licenses a broad catch only in a top-level
loop that records and moves on. Neither says what that loop catches instead.

| Rule | As it appears | Fix |
|---|---|---|
| one package base under every error the package raises on purpose | the per-ticker loop catches `Exception`, so a `TypeError` from a bug is filed as one more skipped ticker | `class AcmeError(Exception)`; third-party errors translated at the boundary with `raise FetchError(...) from err`; the loop catches the base, records, moves on; anything else stops the run with its traceback |
| a new exception class is caught by name somewhere, or carries an attribute a handler reads (`err.ticker`, `err.status`), not a message to parse | five empty subclasses nobody catches: the typedef maintainer-rules.md section 3 forbids | raise `ValueError`, `LookupError`, `FileNotFoundError`; check with `git grep -nE "except .*\b<Name>\b"` |
| an exception's base class is a switch | `class LoaderError(Exception)` becomes `(ValueError)` and is now caught by every existing `except ValueError`, including the one around `float(cell)`, which turns a vendor outage into "bad cell, skipped". The reverse is as quiet: `raise KeyError(t)` replaced by `raise MissingPriceError(t)` sails past every `except KeyError` | for the transition derive from both, `(AcmeError, KeyError)`, and drop the second base in a later patch; the message lists each `except` that gains or loses the error |
| clause order | a base listed above its subclass makes the subclass clause dead: the first match wins | most specific first |
| an exception must survive pickling to cross a `ProcessPoolExecutor` | `__init__(self, ticker, status)` calling `super().__init__(f"{ticker}: {status}")` fails to unpickle with a `TypeError` about missing arguments | pass the same arguments up, `super().__init__(ticker, status)`, and format in `__str__` |

Detectors: `broad except`, `swallowed exception` (audit_diff.py smells) find
the symptom only. For a changed base grep the diff: `^[-+]class \w+\(.*Error`.

## 8. Enum or string constant, and what a member becomes at the boundary

Both directions. Two strings compared in one module are literals under a
`Literal[...]` hint; an Enum for one call site is the one-user abstraction. A
set has earned an enum when it is closed, compared in more than one module,
and an illegal value must raise where it enters: `Mode(raw)` raises
`ValueError`, the boundary check. What the base class decides (run on 3.9 and
3.13; the 3.11 change is in "What's New in Python 3.11"):

| | `class Mode(Enum)` | `class Mode(str, Enum)` | `class Mode(StrEnum)`, 3.11+ |
|---|---|---|---|
| `Mode.LIVE == "live"` | **False**, no error: the branch is never taken | True | True |
| `json.dumps(m)` | `TypeError` | `"live"` | `"live"` |
| a `sqlite3` parameter | refused: `InterfaceError`, `ProgrammingError` on newer Pythons | stored as `'live'` | stored as `'live'` |
| `str(m)` | `Mode.LIVE` | `Mode.LIVE` | `live` |
| `f"{m}"` | `Mode.LIVE` | `live` through 3.10, `Mode.LIVE` from 3.11 | `live` |

As it appears: a plain Enum meets `json.dumps` or `execute`, raises, and the
agent's fix is `str(mode)` or `default=str`. The table now holds `'Mode.LIVE'`
beside a year of `'live'`, and `WHERE mode = 'live'` misses tonight's rows.
Write `.value`; read with `Mode(row["mode"])`, since SQLite and JSON hand back
a `str`. With `(str, Enum)`, an f-string that builds a filename or a cache key
changes spelling when the floor moves to 3.11.

```python
import json
import sqlite3
from enum import Enum

class Plain(Enum):
    LIVE = "live"
class Mode(str, Enum):
    LIVE = "live"
assert Plain.LIVE != "live" and Mode.LIVE == "live"
assert json.dumps(Plain.LIVE, default=str) == '"Plain.LIVE"'   # the "fix" that corrupts the column
raw = sqlite3.connect(":memory:").execute("SELECT ?", (Mode.LIVE,)).fetchone()[0]
assert type(raw) is str and Mode(raw) is Mode.LIVE             # a str comes back: rebuild the member
```

One-line switches: adding or removing the `str` base; changing a member's
value, a data migration since old rows keep the old text; `auto()` on anything
stored, since a new member renumbers the rest; storing `.name`. Each is its
own patch, with a test that round-trips the member through `json.dumps` and an
in-memory table and pins the stored text.

## 9. async and await (skip when `git grep -c "async def"` finds nothing)

The owner's pipeline is synchronous today; a branch that adds the first `async
def` changed the architecture, an open question before it is a refactor.

| Look for | What it does | Fix |
|---|---|---|
| a coroutine called with no `await` `[coroutine called and never awaited]`, the AST pass in `risk_detectors.py`. It sees a bare call statement to a name defined `async def` in the same file; not `if f():`, not `x = f()` never awaited, not a coroutine imported from elsewhere: for those list the names (`git grep -hoE "async def \w+"`) and grep their call sites | returns a coroutine object: truthy, never run (checked). `if fetch_headlines(tickers):` is always true, `save(rows)` alone on a line saves nothing, nothing raises, the tests pass; the only trace is `RuntimeWarning: coroutine ... was never awaited` on stderr. A `unittest.TestCase` method declared `async def` is the same bug inside a test: it returns a coroutine and passes | `await` it. `IsolatedAsyncioTestCase`; under pytest confirm the async plugin ran the test (a skip is not a pass, testing.md section 5) |
| a blocking call in a coroutine `[blocking call inside async def]`, which knows `time.sleep`, `requests.*`, `urlopen`, `subprocess.run`, `check_output`; read for `sqlite3`, `pd.read_csv` and heavy frame work yourself | the loop stops for every task, so the concurrency the message claims does not exist | `await asyncio.to_thread(...)`, or stay synchronous |
| `gather(*(fetch(t) for t in universe))` `[concurrency primitive or worker count]` | every request at once, against a vendor with a rate limit | `asyncio.Semaphore(n)`; `n` is a switch the message names |
| `return_exceptions=True` | a failure becomes a value in the results list: `rows.extend(r)` fails far away, an `isinstance` filter drops it, which is `except Exception: pass` in async spelling | read each result, and raise or record the exceptions by name |
| `as_completed` feeding an output | `gather` returns in argument order, `as_completed` in completion order: the file differs run to run | sort before it leaves |
| `create_task(...)` with the result dropped | the loop keeps only a weak reference; the task can be collected mid-flight | keep the task and await it |

## 10. Context managers: say what `__exit__` releases

python-idiom.md asks what `__exit__` does with an exception. The second
question is what it releases, and the manager's name rarely says.

| `with ...` | On exit | What it does not do |
|---|---|---|
| `sqlite3.connect(db) as conn`; `with conn:` | commits, or rolls back on an exception; nothing when no transaction is open | **does not close.** A loop over tickers leaks a handle per iteration and can hold the write lock. Detector: `connection as context manager`, which matches the literal `with sqlite3.connect(`, not the tree's own `connect()` wrapper |
| `closing(sqlite3.connect(db)) as conn` | closes | does not commit: pending writes are rolled back with no error. Both, in this order: `with closing(connect(db)) as conn, conn:` (data-correctness.md section 8) |
| `ThreadPoolExecutor() as pool` | `shutdown(wait=True)`: blocks until every submitted task ends | does not raise what the tasks raised; read each `future.result()` (`future whose result nobody reads`) |
| `tempfile.TemporaryDirectory() as d` | deletes the directory | a path returned from inside is dangling: section 5's generator in another shape |
| `pd.option_context(...)`, `np.errstate(...)`, `decimal.localcontext()`, `warnings.catch_warnings()` | restores a global setting | releases nothing: inside the block the setting is a switch, and `simplefilter("ignore")` there silences the canary |
| a `@contextmanager` whose cleanup follows the `yield` with no `try`/`finally` | runs the cleanup only when the body succeeds (checked) | when the body raises, the run record stays "started" and the lock file stays. python-idiom.md covers the manager that swallows; this one skips. `grep -nA8 "@contextmanager"` and look for `finally` |
| a long `with conn:` body that fetches a URL after its first INSERT | commits at the end | does not release SQLite's write lock before then: it is held across the network round trip, and the other scheduled job gets `database is locked`. Fetch first, then write |
| a hand-rolled `opened = []` with `finally: for f in opened: f.close()` | stops at the first `close()` that raises | `contextlib.ExitStack`: `enter_context` each; all are closed, in reverse order, whatever raises (checked) |

```python
import sqlite3
conn = sqlite3.connect(":memory:")
with conn:
    conn.execute("CREATE TABLE runs (day TEXT)")
assert conn.execute("SELECT COUNT(*) FROM runs").fetchone() == (0,)    # still open after the with
conn.close()
```

## 11. Sorting: stable in Python, not in a frame

`sorted`, `list.sort`, and `min`/`max` (the first of equals wins) are
deterministic on ties; `sort_values`, `sort_index` and `np.argsort` default to
`kind="quicksort"`, which is not stable. data-correctness.md section 6 owns
the frame rule and its NAK; `sort with tie order unnamed` marks every
`sort_values(` with no `kind=`. Put a tie-breaker column in `by=` so the key
is total; failing that `kind="stable"`, which pandas honours only for a single
sort column, and a message saying input order breaks the tie. A small fixture
hides the bug: on numpy 2.5 and pandas 3.0, sixteen rows or fewer always came
back in stable order and seventeen never did.

```python
rows = [("AAPL", 2), ("MSFT", 1), ("NVDA", 2)]
assert sorted(rows, key=lambda r: r[1], reverse=True)[0] == ("AAPL", 2)     # reverse=True keeps input order on ties
assert sorted(rows, key=lambda r: r[1])[::-1][0] == ("NVDA", 2)             # [::-1] flips them: another row wins
scores = [("A", 0.5), ("B", None), ("C", 0.1)]                             # a bare None key: TypeError part-way
assert sorted(scores, key=lambda r: (r[1] is None, r[1]))[-1] == ("B", None)   # say where missing sorts, in the key
assert sorted([3.0, float("nan"), 1.0, 2.0])[0] == 3.0                     # a NaN key: unsorted, and no error
```

A `list.sort()` that raises part-way may leave the list half reordered. A class
with only `__lt__` sorts, then raises `TypeError` on `>=`: `total_ordering`.

## 12. `pyproject.toml`: metadata as audit findings

In pre-pr mode `pyproject.toml` is a config file: a change to it is its own
patch, the lockfile riding with the dependency that caused it
(maintainer-rules.md sections 1 and 5). In audit mode report these as findings
in `audit.md`; `repo_map.py` reads only the floor and `console_scripts` here.

| Finding | As it appears | Fix |
|---|---|---|
| no floor, or one its rivals contradict | section 1 | declare it; own patch naming the machines |
| no `[build-system]` (PEP 517, 518) | the backend is implied, so the build depends on what is installed | add the table naming the backend the tree already uses; own patch |
| one value, two sources | `setup.py` or `setup.cfg` beside `[project]` with a different name, version or dependency list; `requirements.txt` beside `[project.dependencies]`; a version in `__version__`, in `[project]` and in a tag | one place, the rest deleted or derived (`dynamic = ["version"]`, PEP 621): behaviour-switches.md section 5's dead switch |
| a version or specifier that is not PEP 440 | `1.0-SNAPSHOT`, `1.0.0-final`, `1.2.0-14-gabc1234` from `git describe`, `latest`; `requires-python = "3.11"` with no operator, or Poetry's `"^3.11"` inside `[project]`. Legal, though surprising: `v2.1` and `1.0-beta` normalise to `2.1` and `1.0b0`, and a calendar version `2026.09.20` becomes `2026.9.20` in the wheel name | fix the string. Check: `python3 -c "from pip._vendor.packaging.version import Version; print(Version('2026.09.20'))"` (pip's vendored copy; `InvalidVersion` when it is not) |
| an import its distribution does not declare | importable here because `requirements.txt`, a `dev` extra or another package's dependency installed it; a clean `pip install .` fails at import. Names differ: `dateutil` is `python-dateutil`, `yaml` is `PyYAML`, `sklearn` is `scikit-learn`, `bs4` is `beautifulsoup4`. An optional extra imported at the top of a module the base install loads is the same finding (python-idiom.md, "Conflicts", licenses the function-level import) | declare it; own patch with the reason. List the top-level imports with `grep -rhoE -e "^import \w+" -e "^from \w+" --include="*.py" src`, de-duplicate, then strike the stdlib and the package itself |
| a lower bound the code contradicts | `pandas>=1.5` declared and `DataFrame.map` (2.1) called; the lockfile hides it on this machine | raise the bound; own patch |
| an entry point that moved | `[project.scripts] acme = "acme.cli:main"` after the audit moved `cli.py` into a subpackage: an import by string appears in no `import` line (review-checklist.md, "Was this file moved") | the move patch carries the entry-point edit, or the commit in between cannot start; the message says why config rides with code here |
| a data file the wheel does not ship | a holiday table read through `Path(__file__).parent` works from the checkout and an editable install and is absent from the built wheel | declare it as package data; test from a built wheel |

## 13. A joined path that discards its root

security.md section 6 has the hostile case; here the join has no attacker.

| Look for | What it does | Fix |
|---|---|---|
| `out_dir / cfg["report_name"]`, `os.path.join(RAW_DIR, row["path"])`: a path joined with a value from config or data | an absolute right operand wins, silently: `os.path.join("/data/raw", "2026", "/filings/AAPL.pdf")` is `"/filings/AAPL.pdf"` (checked). The vendor manifest starts sending a leading slash, or someone sets `report_name = "/tmp/out.csv"` in `config.toml`, and the nightly output lands outside `output_dir` while the job reports success | decide at the boundary what the value may be, a bare filename (`Path(name).name == name`) or a relative path through security.md's `inside()`, and reject the rest with a `ValueError` naming the value. `.lstrip("/")`, the agent's fix, handles the slash and not `..` |

## 14. A property that hides work

python-idiom.md NAKs the trivial getter. The opposite defect: attribute access
promises a stored value, constant time, no I/O, the same answer twice.

| Look for | What it does | Fix |
|---|---|---|
| a `@property` that runs a query, reads a file or rebuilds a frame. No detector: `grep -nA6 "@property"` and look for `open(`, `execute(`, `read_`, `requests.`, a `for` | runs where nobody can see it: once per iteration of `for t in tickers: book.prices[t]`, again in `hasattr(book, "prices")` and `getattr(book, "prices", None)`, which run the getter (checked), again in any `repr` or log line. It is maintainer-rules.md section 3's control-flow macro in Python spelling. A derived path such as `output_dir / "acme.db"` is fine | a method whose name says the work (`load_prices()`), called once, the result passed down |
| `@cached_property` `[cache or memoise decorator]` (switch_detectors.py); `@lru_cache` or `@cache` on a method `[cache on a method]` | trades the repeat for staleness: the value stays in the instance `__dict__` until `del obj.attr` (checked), so an object that outlives one as-of date (a module-level config, a pipeline object reused across a backfill loop) serves the first date's frame to the second | the message says how long the instance lives and what makes the value stale; algorithm-smells.md rule 9 owns caches |

## 15. What agent code does with the language, and why each is a NAK

Extends review-checklist.md's NAK list, after maintainer-rules.md section 6.

| Habit | The maintainer's objection |
|---|---|
| `str \| None`, `list[str]`, `StrEnum`, `zip(strict=True)` in a tree that declares `>=3.9` | "Your pyproject says 3.9 and this fails at import on 3.9. Which one is wrong? Its own patch either way." |
| Answers the `Dict[str, Any]` NAK with `Mapping[str, object]` | "Same hint, longer. Name the keys, and show me the check: a TypedDict is a dict." |
| `mode: str  # "paper" or "live"` | "That comment is doing a type's job. `Literal`, and the `else` raises." |
| An ABC over the store and its test fake; `isinstance(x, SomeProtocol)` as validation | "Two implementations: a `Protocol` beside the consumer, nobody inherits. And that `isinstance` checks the method is called `closes`, not that it takes a ticker." |
| Turns a NamedTuple into a dataclass in a feature patch; adds a field in the middle of a record | "That was a tuple for a reason: who unpacks it, who hands it to `executemany`? And every positional caller now puts the volume in `fetched_at`. At the end, with a default." |
| Adds `@dataclass` or `__eq__` "so the test can compare"; `unsafe_hash=True` on a record the code mutates | "It was hashable a minute ago. What holds one in a set? `frozen=True`, or key on the fields. Change a field under `unsafe_hash` and the dict can't find it." |
| Swaps a list for a generator "to save memory"; adds a `yield` to a function that returned | "Who reads it twice, who calls `len`, who tests `if rows:`? How many rows were there? Every caller just got a generator and your diff shows none of them." |
| Returns a generator from inside `with`; `map(store, rows)`; `try:` around a call to a generator function | "The file is closed before I read the first row. Nothing ran, so nothing in that `try` raises. Write the loop." |
| `if os.path.exists(p): open(p)`; SELECT, then INSERT; `if s.isdigit(): int(s)` | "The check is the race, or a second parser that disagrees about `-5`. Do it and catch the one error." |
| Replaces the wide `try` with `if key in d and d[key] is not None and ...`; a guard restating the next line's exception | "Now it's the other mistake. One line in the `try`. And the next line raises that already: what precondition is this?" |
| `except Exception` in the per-ticker loop | "Catch your package's base. A `TypeError` is a bug, not a skipped ticker." |
| Five empty exception subclasses; a changed base class | "Who catches each by name? Nobody: `ValueError`. And name every `except` that catches it now and didn't before." |
| Compares a plain `Enum` member with `"live"`; `str(mode)` or `default=str` to get one into SQLite or JSON | "Always False, no error. And you stored `'Mode.LIVE'` beside a year of `'live'`: `.value` out, `Mode(raw)` in, pin the text." |
| An Enum for two strings used once; `auto()` on a stored one | "One user: `Literal`. And insert a member, every stored integer changes meaning." |
| Calls a coroutine without `await` | "That's a coroutine object. It's truthy and it never ran." |
| `requests.get` inside `async def`; `gather(..., return_exceptions=True)` unchecked | "Your message says concurrent and this blocks the loop. And who reads the exception out of that list?" |
| `with sqlite3.connect(db) as conn:`; `closing(conn)` and no commit | "What does that `__exit__` release? Not the connection. And yours closed and rolled back: where did tonight's rows go?" |
| `@contextmanager` cleanup after the `yield`, no `finally` | "And when the body raises?" |
| `sorted(xs, key=k)[::-1]` for descending; a sort key that can be `None` or NaN | "`reverse=True`; yours flips the ties. `None` raises half-way and NaN doesn't raise at all: where does missing sort?" |
| `version = "1.0-SNAPSHOT"` in three files; an import only `requirements.txt` names | "Not PEP 440, and which of the three is true? `pip install .` on a clean machine?" |
| `out_dir / cfg["name"]` | "If that value starts with a slash, `out_dir` is gone." |
| A `@property` that runs a query; `@cached_property` on a long-lived object | "It reads like an attribute and costs a query per loop: a method, called once. How long does the instance live, and what makes that stale?" |
