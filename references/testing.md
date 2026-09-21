# What a test in the branch has to prove

Read this at the prune (SKILL.md step 2, item 7) whenever the diff touches a
test file or adds behaviour with no test; at the split (step 4) when setting
`expect_fail_before`; before an audit's characterization stage; and at the
maintainer pass (step 7), where section 10 extends the NAK list. Not repeated
here: assert results not calls, one behaviour per test, no test of trivial
code, the test rides in its patch (maintainer-rules.md section 5); no switch
faked by patching a module constant, both positions tested by passing the value
(behaviour-switches.md sections 2 and 4); the characterization gate and
`import_check` (repo-audit.md sections 1 and 3). The code below is original.

Contents: 1 three claims, 2 doubles, 3 the reason it fails, 4 independence,
5 network and real data, 6 tables over copies, 7 invariants, 8 mutation by
hand, 9 coverage and assertion-free tests, 10 review questions.

## 1. Three claims, and the test that carries each

A test is the evidence for one sentence in one commit message. Find the
sentence; a test with none is a prune.

| Test | The claim it backs | On the parent | Proof that it bites |
|---|---|---|---|
| behaviour | "this patch changes X" | fails | `expect_fail_before` (section 3) |
| characterization | "X is what the code does today" | passes, by definition | one hand mutant (section 8) |
| invariant | "for every input of this shape, P holds" | passes | one hand mutant |

A characterization test records what today's code returns for a real input,
wrong-looking parts included; repo-audit.md section 3 owns the gate and the
order. It is not a specification: correcting the expected value is a bug fix
and lands as one. Not a smoke test: `assert out is not None` pins nothing; pin
the rows, keys and numbers a consumer reads. Not a snapshot: log text, a `repr`
or incidental column order forces the test edit that voids the refactor's
proof. Not written through patched collaborators, which pins the patch: feed it
twenty saved vendor rows under `tests/data/`, never the live database (section
5). NAK: "This pin passes whatever the function returns."

## 2. Doubles: a fake by default, a stub at the process edge, a spy almost never

| Double | What it is | Legitimate where | In this tree |
|---|---|---|---|
| **fake** | a working implementation with a shortcut | anywhere beneath the code under test; the default | a SQLite file under `tmp_path`, or `:memory:`, built by the tree's own schema code; a CSV written to `tmp_path` |
| **stub** | canned answers, no logic | the process edge only: network, clock, random source, subprocess; passed in as an argument | a fetcher returning three saved vendor rows; `as_of=date(2024, 3, 8)`; `random.Random(7)` |
| **spy** | records what it was given | only when the call is the sole observable effect (an alert sent, an order routed) and no read path can be added | a list-appending function passed in; assert the recorded payload, never the call count |
| **mock** | `MagicMock`, `mock.patch`: answers anything, asserts on calls | nothing inside the tree, and never the unit the test names | replace with the real collaborator over a fake store |

"Mocks of the thing under test" (SKILL.md step 2, item 7) is the floor and says
what to delete; this says what to write, so a pruned test leaves no hole. Above
the floor the line is the process boundary: a double stands in only for what
the code reaches outside the process, and arrives through the seam
repo-audit.md section 3 has you build ("pass the dependency in"), never through
`patch("pkg.mod.name")`. `MagicMock` cannot be wrong, which is the defect: every
attribute exists and every call is truthy, so `test_store_closes`, which patches
`sqlite3.connect` and ends in `execute.assert_called_once_with("INSERT ...")`,
is green with the column misspelt and the table absent. The fix runs real SQL
on a fake and reads the result back:

```python
import sqlite3
from datetime import date

def store_closes(conn, fetch, as_of):
    conn.executemany("INSERT OR REPLACE INTO closes VALUES (?, ?, ?)",
                     [(r["ticker"], as_of.isoformat(), r["close"]) for r in fetch(as_of)])
    conn.commit()

def saved_vendor_rows(day):                 # stub: canned rows where the network would be
    return [{"ticker": "AAPL", "close": 189.5}, {"ticker": "MSFT", "close": 402.1}]

def test_a_rerun_for_the_same_day_replaces_rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "prices.db")   # fake: real SQL, real constraint, a directory per test
    conn.execute("CREATE TABLE closes (ticker, day, close, PRIMARY KEY (ticker, day))")
    store_closes(conn, saved_vendor_rows, date(2024, 3, 8))
    store_closes(conn, saved_vendor_rows, date(2024, 3, 8))
    rows = conn.execute("SELECT ticker, close FROM closes ORDER BY ticker").fetchall()
    conn.close()
    assert rows == [("AAPL", 189.5), ("MSFT", 402.1)]
```

In a real tree the table comes from the tree's own `connect()` or schema
function; DDL copied into a test drifts from production. NAK: "This passes when
the SQL is wrong." Detectors: `test patches an attribute or environment`, `test
asserts calls, not results` (behaviour-switches.md section 7). None finds a test
that imports `mock` and asserts no returned value: `grep -lE
"MagicMock|mock\.patch|mocker\." tests/`, then read the asserts.

## 3. A test fails for the reason it names

A behaviour test fails on the parent, at its own assertion, showing the wrong
value. `expect_fail_before` in `plan.json` is the mechanism: the builder keeps
the patch's test files, reverts every other staged file to the parent, runs the
command, demands a non-zero exit, restores. Set it on every patch that claims a
behaviour change, naming the one test; never on a characterization, move or
refactor patch, which pass on the parent.

The builder also runs the command after the commit and requires it to pass,
so a test name that does not exist, or a missing runner, fails the gate. And it
prints how the parent failed: "by assertion", or "by ERROR" (unittest's
`errors=` with no `failures=`, an `ImportError`). Read the reason yourself all
the same: same command, parent's code, last lines. `AssertionError: 3 != 2` on the number the message
talks about is the reason; `ImportError`, `AttributeError` and `fixture not
found` exit non-zero too, and an empty function clears the same bar. When **the
patch adds a function**, the test dies at import and the gate is free: kill a
hand mutant of the new code instead (section 8). When **a helper the test
imports lives outside `tests/`**, the builder, which tells tests from code by
path (`tests/`, `test_*.py`, `*_test.py`), reverts it with the code and the test
fails at import. Quote the failing line in the message, after
commit-message.md's "fails on the parent commit and passes here". NAK: "It
fails on the parent with ImportError. So would an empty file."

## 4. Independence: order, shared state, the working directory, the clock, the seed

Every test passes alone, in any order, twice running, from any directory, on
any date. The builder cleans its worktree of untracked files and bytecode
before every patch, so a `report.db` written at patch 3 is gone by patch 4; what
it cannot see is order inside one run, or a test that writes outside the tree.
Before building, in a scratch worktree (`git worktree add --detach <tmp>
<head>`, never the user's checkout), run each touched test file alone, one test
alone, the file twice, the suite, then `git status --porcelain --ignored`:
anything new was written by a test. `tmp_path`, never `.gitignore`.

| What leaks | As it appears | Fix |
|---|---|---|
| shared objects | a module-level connection or frame in a test file; a class attribute the tests append to; a `scope="session"` fixture handing every test the same mutable frame | build it per test, or hand out a copy |
| order | `test_1_load`, `test_2_transform`: `unittest` runs methods sorted by name, so the dependency is spelt in the names | each test builds its own input |
| hidden setup | `autouse=True` in `conftest.py`, a switch over every test beneath it (`registration or scheduling decorator` matches it) | a fixture the test names; else the message says what it sets |
| process state | `os.chdir`, `os.environ[...] =`, `pd.set_option`, `random.seed()` with no restore (`global state mutated at runtime`, `test sets an environment variable`) | pass the value in; where the environment itself is under test, `monkeypatch`, which restores it |
| the clock | an expected value built from `date.today()`: green until a weekend, a month-end, a holiday; and this machine is on Hong Kong time while the session date is New York's | pass `as_of` (behaviour-switches.md section 2, runtime environment); literal dates on both sides of the boundary |
| the seed | `random.seed()` in a test module reseeds every later test; a `set` of strings iterates in a different order per process | `random.Random(7)` or `default_rng(7)` built in the test and passed in; sort, or compare sets |
| floats | a summed column compared with `==` depends on summation order | `math.isclose` or `pytest.approx`, tolerance written down |

**A test that does not fail deterministically cannot certify a patch.** When
the check fails at patch N and passes on a re-run, the flake is the finding:
fix it, or skip it with the reason, in its own patch at the head of the series,
or make it an open question for the user. NAK: "Does it pass alone? Twice? On a
Saturday? What does it leave on disk?" No detector for writes in tests: `grep
-nE "open\(|to_csv\(|to_parquet\(|connect\(" tests/`; every path found hangs
off `tmp_path`.

## 5. Tests that reach the network, a real database, or the operator's data

A test reads only what the repo ships and writes only under a directory it was
handed. `import_check` proves the check imports this tree; nothing proves what
the tests touch, so you do. A network fixture turns a vendor outage into a
bisect break; a real database makes the result a function of that file today.

As it appears in the owner's tree: two tests take `load_config().db_path`, the
operator's production SQLite file. One opens it through the tree's `connect()`,
which runs the schema script and the migrations, so the test migrates the live
file, or creates one wherever the config points, then asserts a column exists.
The other searches the live archive, skips when the file or the rows are
absent, then asserts the value it has just proved non-empty, so it cannot fail.
In a clean worktree, where the series is built, the check is green and proves
nothing. **A skip is not a pass**: read the skip count in the check output; a
skip conditioned on the machine (a file, a variable, the network) is this
finding.

The fix is section 2's pattern: the tree's own `connect()` on `tmp_path /
"news.db"`, three inserted articles including the awkward one, the hit and the
miss both asserted. `tmp_path` is a `pathlib.Path` unique to the test function;
under `unittest.TestCase` it is `tempfile.TemporaryDirectory()` with
`addCleanup`. A check that needs the operator's file (is the live schema
migrated?) is an operations check: a script the operator runs, as its own patch
or a line in `deferred.md`, never a silent delete. Such tests already on the
base are not this branch's defect and not a pre-PR pass's to rewrite (in audit
mode they are a prep patch at the head of the series), but they hollow out
every check: the builder refuses a check in which every test skipped, and the
cover letter says how many tests skip in a clean worktree. NAK: "What does this test read that the repo
does not ship?" "It skipped; what did the check prove?" No detector today:
`grep -rnE "load_config\(|db_path|requests\.|httpx\.|urlopen|socket\.|create_engine\(" tests/ conftest.py`,
then `grep -rnE "pytest\.skip|skipTest|skipif" tests/` and read each condition.

## 6. One table, not six copies

Tests that differ in one literal are one test over a table: an agent writes six
ten-line functions, the reviewer diffs them by eye to find the case, and the
next case costs ten more lines instead of one. In a pytest file:
`@pytest.mark.parametrize("raw, expected", [("aapl", "AAPL"), (" msft ",
"MSFT"), ("brk.b", "BRK-B")], ids=["lower case", "padding", "class share"])`.
Match the file: pytest does not apply `parametrize` to `unittest.TestCase`
methods, so there it is a loop over the same list under `with
self.subTest(case):`. Name every case, so the failure names the behaviour. An
`if` in the body, or a case needing its own assertion, is a separate test, not
a row. Copies the branch added are folded before they land; copies already on
the base are a line in `deferred.md` (their own `No functional change.` patch
in audit mode), and the message counts the cases before and after. NAK: "Six tests, one literal
apart. Which literal?" No detector: look for test functions in one file sharing
a name prefix and a body length.

## 7. Invariants: what a pipeline can assert for every input

An example test says this input gives that output; an invariant says what holds
for every input, and a pipeline has a short list that catches what examples
miss. **Rows conserved**: rows in equal rows out plus rows dropped, each drop
counted by reason and returned, not logged; a join that fans out or a filter
that loses rows quietly breaks it. **Idempotent re-run**: the nightly job run
twice for one date leaves the table as if run once. **One row per key, dates
increasing within a key. No future rows**: nothing dated after `as_of`, the
look-ahead guard for anything feeding a prediction. In the standard library,
over a generator with a fixed seed:

```python
import random
from datetime import date, timedelta

def latest_per_day(rows, as_of):
    """Last-seen row per (ticker, day) up to as_of, sorted; and the rows dropped, counted by reason."""
    kept, dropped = {}, {"future": 0, "superseded": 0}
    for ticker, day, close in rows:
        if day > as_of:
            dropped["future"] += 1
            continue
        if (ticker, day) in kept:
            dropped["superseded"] += 1
        kept[ticker, day] = (ticker, day, close)
    return sorted(kept.values()), dropped

def test_latest_per_day_invariants():
    rng, as_of = random.Random(20240308), date(2024, 3, 8)       # fixed seed: the same inputs at every patch
    for _ in range(200):
        rows = [(rng.choice(["AAPL", "MSFT"]), date(2024, 3, 1) + timedelta(days=rng.randrange(10)),
                 rng.uniform(50, 500)) for _ in range(rng.randrange(30))]
        out, dropped = latest_per_day(rows, as_of)
        keys = [(ticker, day) for ticker, day, _ in out]
        assert len(rows) == len(out) + sum(dropped.values())     # rows conserved
        assert all(day <= as_of for _, day in keys)              # no future rows
        assert keys == sorted(set(keys))                         # one row per key, dates increasing
        assert latest_per_day(out, as_of)[0] == out              # a re-run changes nothing
```

The empty input is among the two hundred, which is where an `if not rows:`
special case gets found. An invariant is never the implementation run a second
time: each line is checkable without knowing how the function works. Nor does it
replace the example test: `day > as_of` mutated to `>=` survives all four,
because none says the `as_of` day itself is kept (section 8). Over a frame:

```python
# pandas: illustrative
out = prices.merge(sectors, on="ticker", how="left", validate="many_to_one")
assert len(out) == len(prices)                     # unique right key, left join: rows conserved
assert not out.duplicated(["ticker", "date"]).any()
assert out.groupby("ticker")["date"].is_monotonic_increasing.all()   # non-strict; the line above makes it strict
assert (out["date"] <= as_of).all()                # as_of is a pd.Timestamp
```

Hypothesis is the tool for this **when the repo already depends on it**: grep
`pyproject.toml` and the lockfile, and never add it for this (a dependency is
its own patch with a reason, maintainer-rules.md section 5). It must be
deterministic or it breaks the per-patch check (section 4):
`@settings(derandomize=True, database=None)` seeds from a hash of the test
function, so every run tries the same cases, and stops it writing
`.hypothesis/examples` into the working directory. A counter-example it finds
lands as a plain example test. NAK: "Three inputs. What is true of all of them?"

## 8. Mutation by hand: the proof a test is load-bearing

For the one or two tests a patch's claim rests on, break the code and watch:

1. Pick the token the message is about: comparison, join key, `keep=`, guard.
2. Change it in the series worktree or a `git archive` export: `<=` to `<`,
   `keep="last"` to `"first"`, `how="left"` to `"inner"`, delete the guard.
3. Run the one test: it fails at its assertion and shows the value.
4. `git checkout -- <file>`; `git status` clean. A mutant is never committed.
5. Quote it in the message, where a reviewer can re-run it: "Mutant: `day >
   as_of` to `>=` fails test_the_as_of_day_is_kept (AssertionError: 4 != 5)."

Required where `expect_fail_before` cannot vouch: every characterization test
before the refactor it guards, since it passes on the parent by definition and
nothing else shows the pin is not vacuous; a new function whose test dies at
import on the parent (section 3); any test the maintainer pass doubts.

**A mutant that survives names the missing case.** `<=` to `<` survives when no
fixture row sits on the boundary: add the row dated exactly `as_of`. If no
input can tell the two apart, the condition is redundant, and that is a prune.
One validated run of this skill shows the use: an audit refactor turned the
filter `notional > 0` into the guard `if notional <= 0: continue`, not its
inverse for NaN, and the suite stayed green. The maintainer pass caught it; the
series pinned the NaN row and proved the pin by putting the wrong guard back by
hand: two named tests failed, `1 != 0`. Never a tool: `mutmut` and `cosmic-ray`
run for hours on a branch this size and return a backlog nobody asked for, and
nothing is installed into the user's environment. One to three hand mutants per
patch. NAK: "Would this test notice if the code were wrong? Show me the mutant."

## 9. Coverage is a signal and a bad target; tests that assert nothing

Line coverage says which lines ran, not which results were checked, and the
cheapest way to raise it is a test that calls the function and asserts nothing,
which is what an agent writes when told to add tests. Use it as a question about
a changed hunk ("which test reaches the `except` branch you added?"), never as
a finding ("coverage is 61%"), a target, a reason to add a test, or a reason to
install `coverage`. `repo_map.py`'s untested list is not coverage either: it is
a call edge *or a name mention*, so a function called `rows`, `load` or `parse`
counts as tested when any test has a local of that name. Before calling a patch
a refactor, open the test said to pin the function and find the call.

| A test that cannot fail | As it appears |
|---|---|
| no assertion | `def test_pipeline_runs(): run_pipeline(cfg)`. `expect_fail_before` does not see it: on the parent it raises, at the patch it passes |
| an assertion that is always true | `assert out is not None` on a function that always returns a frame; `assert len(df) >= 0`; `assert isinstance(out, pd.DataFrame)`; an `assert` inside `if rows:` or a `for` over a result that may be empty; skip-when-empty, then assert-non-empty |
| an assertion on the double | `m.return_value = 5`, then `assert f() == 5` with `f` patched |
| `pytest.raises(Exception)` | a typo's `NameError` satisfies it; name the class and `match=` the message |
| an expected value computed by the formula under test | it shares the bug; work the number out by hand from the fixture and write the literal |

The honest size measure is assertions added against behaviour lines added:
"180 added test lines, 4 assertions" is the finding. NAK: "`test_loader_runs`
asserts nothing; it passes if the function returns anything at all." No script
counts assertions today: per touched test file compare `grep -cE "^\s*def
test_"` with `grep -cE "\bassert\b|self\.assert|pytest\.raises"`, and read every
test in a file where the second is not clearly the larger.

## 10. Review questions for the maintainer pass

Added to the NAK list in review-checklist.md; the fix for each is in the section
that raised it.

- "Which sentence in the message does this test back?"
- "This pin passes whatever the function returns. What did it record?"
- "You patched something inside the tree. Where is the process boundary?"
- "It fails on the parent with ImportError. So would an empty file."
- "Alone, twice, in any order, on a Saturday, from another directory?"
- "It failed once and passed on the re-run. Why is the series built on it?"
- "What does this test read that the repo does not ship? It skipped: what did
  the check prove?"
- "Six tests, one literal apart. Which literal?"
- "Three examples. What holds for every input?"
- "Show me the mutant this test kills."
- "Where is the assertion, and can it be false?"
- "You quoted a coverage number. Which hot function does nothing pin?"
