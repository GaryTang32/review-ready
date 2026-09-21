# Engineering practice the series has to respect

Read this at the refactor (SKILL.md step 3) when the audit reports a
`concurrency` or `logging` risk or prints "Public surface changed"; at the
split (step 4) to place an API change, a changelog entry or a decision record;
at steps 5 and 6 for trailers and a repost; at the maintainer pass (step 7)
for the round cap and section 9; and before running any tool the skill did
not ship (section 8). Not repeated here: the `Fixes:` format, consent and
sign-off (commit-message.md); log volume (maintainer-rules.md section 5);
`%s` logging arguments and `raise X from err` (python-idiom.md); `fork`
against `spawn`, the worker count as a switch, the dark-launch order
(behaviour-switches.md sections 2 and 4); secrets (security.md section 5);
`await` (python-language.md). Sources: the kernel's `submitting-patches.rst`
and `stable-kernel-rules.rst`, the Python library docs; paraphrased, the code
original, running on Python 3.9 with the standard library alone.

Contents: 1 trailers, 2 reposts and the round cap, 3 concurrency, 4 logging,
5 breaking a caller you cannot see, 6 README, decision records, changelog,
7 project standards, 8 external tools, 9 review questions.

## 1. Trailers: each is a claim, and someone named is making it

| Trailer | The claim | Applies in a company repo when | Written by |
|---|---|---|---|
| `Fixes: <12 hex> ("<subject>")` | that commit introduced the defect this patch removes | the body says the tree computes something wrong, and the cause is a commit on the base. A bug this branch introduced gets none: it is folded into the patch that adds the code, since the series never shows its own history | the agent, after finding the commit |
| `Reported-by:` | this person saw it first | someone other than the author noticed the wrong number; `Closes:` or `Link:` to their report follows when one exists | the agent, with a name and address the user supplied |
| `Tested-by:` | this person ran this patch, where the body says | a human ran it where the tests cannot: the nightly job on the production host, last month's real file | that person's own words only |
| `Reviewed-by:` | they read every line and would defend it | a colleague approved the PR or said so in writing | that person's own words only |
| `Acked-by:` | the owner of the affected code agrees without reviewing it all; may cover only the part that is theirs | the owner of the job, table or config a patch touches said yes | that person's own words only |
| `Cc:` | this person was shown the patch and has not answered | a flip changes their job or report (behaviour-switches.md section 4) | the agent; the user confirms the name |
| `Link:`, `Closes:` | the discussion, vendor notice or ticket; `Closes:` when the patch resolves it | one exists, and the body stands without it | the agent |
| `Co-developed-by:` | a second human author, whose `Signed-off-by:` is the next line | two people wrote the patch | never for the tool |
| the stable tag, `Cc: <stable@vger.kernel.org> # 5.15.x` | the fix must reach the release branches; the kernel takes it only if already on mainline, obviously correct, about a hundred lines at most, and fixing a problem real users hit | a release branch, a deployed tag or a host on last month's checkout carries the bug (ask at step 1). Then the fix must cherry-pick alone, which **reverses the usual order**: the fix goes first, against the code as it stands, and the preparatory refactors follow. NAK: "This fix sits on three refactors. How does it reach the release branch?" | the agent, as a sentence in the body ("Also on release/2024-09; applies cleanly") or the team's own backport trailer; never invent a trailer no tool reads |

**Never manufacture a person's trailer.** The maintainer pass is a subagent:
its ACK lives in `review-vN.md` and never becomes `Reviewed-by:`. On a solo
repo `Fixes:`, `Link:` and the sign-off are the whole block. A person's
trailer survives a rebuild only on a patch `git range-diff` marks `=`
(section 2). NAK: "Who is this reviewer, and where did they say so?"

**`Fixes:` names a commit you found (commands below), not one you guessed.**
Read its diff first: a commit that only moved the line inherited the bug.
Never wrap the trailer: the builder's 72-column warning exempts trailer
lines, and only them. On a base that is one root commit, or where the code was
born without the behaviour, nothing "broke" it: that is a missing feature, and
`Fixes:` is for a defect a commit introduced. No blank line inside the
block: git reads only the last paragraph as trailers, so a gap above the
sign-off demotes `Fixes:` to prose (`git interpret-trailers --parse` prints
what git kept). In a pipeline the trailer dates the damage: the body says
which stored rows or predictions written since that commit are wrong, and
recomputing them is an open question, never a silent backfill. NAK: "A bug
fix. Which commit broke it, and how long has the table been wrong?" No
detector: `grep -L "^Fixes:" .review-ready/msgs/*`, then read those subjects.

```bash
git log -S'keep="first"' --oneline <base> -- src/acme/loader.py  # commits that added or removed the token
git log -1 --abbrev=12 --format='Fixes: %h ("%s")' <sha>            # prints the trailer ready to paste
```

## 2. Reposts, and a cap on review rounds

A version is a posting a human received. The skill's own maintainer rounds
are drafts: each is saved as `review-vN.md`, named in the hand-back, and kept
out of the PR body and out of every commit message, because a reviewer shown
"Changes since v1" for a v1 they never got asks the only possible question:
"v2 of what?". It also tells them the first draft did not bisect, which is
the journey the series is forbidden to show. The PR opens unversioned.

After a human has reviewed a pushed version, the repost is v2: the subject
says so, and the PR description gains one block, newest first, counting only
versions a human saw. NAK when it is missing: "Tell me what changed, or I
read all seven again."

```
Changes since v1 (review on the PR): 3/6 split into 3/7 and 4/7 ("does two things"); 1, 2, 6, 7 unchanged
```

Per-patch notes ("v2: test moved into this patch") go in the PR conversation.
In a patch email `git am` cuts everything under a `---` line; on a pushed
branch nothing does, so the note merges into history and a sign-off added
afterwards lands below it. The builder refuses a message with a `---` line.

| Do | Because |
|---|---|
| before each rebuild, write the series sha on the `Series:` line of `review-vN.md` | the next round's summary is produced, not recalled: `git range-diff <base> <old series> <new series>` marks each patch `=` unchanged, `!` changed, `<` dropped, `>` new |
| hand back the undo from `UNDO.md`, which names `refs/review-ready/original/<branch>` | every round takes its whole files and its residual from that ref, so it stays the branch as the user left it. If they commit on the branch afterwards the builder stops and asks (`--restart`) instead of guessing, and the older original is kept under `refs/review-ready/superseded/` |
| where the repo squash-merges: repeat `Fixes:` as the body's last paragraph, and ask in the open questions whether to merge by rebase | `git log` receives the PR body and loses every per-patch message |

**Round three with a NAK standing: stop.** Two rounds remove what fixing can
remove; what is left means something upstream of the patches is wrong:

| The sign | What is wrong |
|---|---|
| the same objection returns on a different patch; fixing patch 3 breaks patch 4 | the split is cut at the wrong seam |
| "not in the problem statement" on a hunk that cannot be dropped | `problem.md` |
| the objection turns on a fact only the user knows | nothing a rebuild can fix: ask |

Do not build round four. Give the user the standing NAKs, quoted, the row each
fits, and the question that settles it. Every round is a fresh subagent given
exactly what review-checklist.md's first paragraph lists, never the last
review, or it reviews the delta instead of what merges. NAK: "Third version and I still
object. You answer my comments, not the problem."

## 3. Concurrency: what a pool worker may share

In a nightly pipeline concurrency is one thing, a `ThreadPoolExecutor` around
network fetches, and two questions decide every hunk: what do the workers
share, and where do their exceptions go? State is safe because a lock covers
it or one thread owns it, never because of the GIL.

| The GIL (CPython's; free-threaded builds from 3.13 have none) makes atomic | It does not |
|---|---|
| one operation on a built-in: `rows.append(x)`, `seen[k] = v`, `q.popleft()` | read-then-write: `n += 1`, `d[k] = d.get(k, 0) + 1`, `if k not in cache: cache[k] = load(k)`, `if spent < budget: spend()` |
| | anything with I/O, `time.sleep` or a lock wait inside it: the GIL is released there, and another thread runs exactly in the gap |
| | anything across processes: under `ProcessPoolExecutor` each worker owns a copy of every module global, so a counter or cache filled in a worker never reaches the parent, and a `threading.Lock` guards nothing |

**As it appears in the owner's tree:**

| Shared state | What happens | Fix |
|---|---|---|
| a throttle helper (read the last-request stamp, sleep the difference, write the stamp, through `global _last_request`) written twice: one vendor's copy under a lock, the other's bare | no reader can tell whether the lock is needed or its absence is a bug. One thread calls each today; the day a fetch moves into the pool the bare copy's sleep is the race window: every worker reads the same stamp, sleeps the same interval and fires together, and the vendor receives the burst the helper exists to prevent while the code reads as polite | one throttle object per vendor, its lock held across read, sleep and write, built once and passed in. The twin is "Duplicate of X in Y"; the audit's duplicates table lists same-name and same-shape functions |
| lazy module globals filled by check-then-set inside pool workers, no lock: `if _model is None: _model = load()`, a `_failed` flag beside it | two workers find it empty and both load: idempotent today, a doubled vendor call the day the loader is a fetch | load it on the main thread before the pool starts and hand it to the workers: no lock, no `global` |

The merge may say "No functional change." only if its message shows one
thread reaches each copy today. If a worker reaches the bare one it is a bug
fix: its own patch, a test of the spacing, `Fixes:`. NAK: "Two throttles, one
with a lock. Which is right, and why are there two?"

**A future is where an exception goes to be forgotten.** `pool.submit(f, x)`
stores whatever `f` raises in the future; the `with` block waits at exit and
raises nothing; `pool.map` raises only when its result is iterated. A
`sqlite3` connection refuses by default to work off the thread that opened
it: one more stored exception. NAK: "Where does this worker's exception go?"

```python
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch(ticker):                                   # stands in for the vendor call
    if ticker == "BRK.B":
        raise ValueError("vendor rejects the dot: BRK.B")
    return ticker, 100.0

conn = sqlite3.connect(":memory:")                   # opened on the main thread
conn.execute("CREATE TABLE closes (ticker TEXT PRIMARY KEY, close REAL)")
tickers = ["AAPL", "BRK.B", "MSFT"]

with ThreadPoolExecutor(max_workers=3) as pool:      # workers write through the main thread's connection
    for t in tickers:
        pool.submit(lambda t=t: conn.execute("INSERT INTO closes VALUES (?, ?)", fetch(t)))
assert conn.execute("SELECT COUNT(*) FROM closes").fetchone() == (0,)    # three exceptions, read by nobody

failed = {}
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = {pool.submit(fetch, t): t for t in tickers}                # workers fetch and return
    for fut in as_completed(futures):
        try:
            conn.execute("INSERT INTO closes VALUES (?, ?)", fut.result())    # the owning thread writes
        except ValueError as err:                    # the one error fetch() is known to raise
            failed[futures[fut]] = err
conn.commit()
assert conn.execute("SELECT COUNT(*) FROM closes").fetchone() == (2,)
assert list(failed) == ["BRK.B"]                     # returned and counted, not logged and forgotten
```

| In the diff | What happens | Fix | NAK |
|---|---|---|---|
| `check_same_thread=False` | silences that refusal and serialises nothing; the docs leave write ordering to the caller | one owner: workers fetch and return, the thread that opened the connection writes. Otherwise a connection per worker, opened inside the task, and then `database is locked` wants a `timeout=` and a reason in the message | "Which thread owns this connection?" |
| a bare `threading.Thread(target=...)` | its exception goes to `threading.excepthook`, which prints to stderr while the caller carries on; non-daemon it holds the process open, daemon it is killed mid-write at exit | use the pool; otherwise `join()` it and decide `daemon=` in the diff | "Who waits for this thread, and who hears it fail?" |
| `max_workers=16`, `n_jobs=-1` | a knob (behaviour-switches.md section 2; `concurrency primitive or worker count` finds it); against a rate-limited vendor the extra workers only queue in the throttle | the number and its measurement in the message, or the old number | "Why 16? What did 4 measure?" |
| `asyncio.run(...)` inside a helper | `RuntimeError` when a loop is already running, so it works from the scheduler and fails from a notebook; a loop built and torn down per call | one `asyncio.run` at the entry point, or none; "make it async" is a rewrite and goes to `deferred.md` | "Why is there an event loop inside a helper?" |
| `asyncio.create_task(...)` unbound; `gather(..., return_exceptions=True)` unread | the loop holds tasks weakly, so an unreferenced one can vanish mid-flight; exceptions come back as list items nobody inspects | keep the reference and await it; check every item | "Where does the exception go?" |

Detectors, all triage: `module state mutated with global`, `future whose
result nobody reads` (a bare `.submit(` only; for a bound future find its
`.result()` by hand), `thread started without join or daemon decision`, by
AST `blocking call inside async def` and `coroutine called and never awaited`
(`risk_detectors.py`). None joins a `global` to a pool: grep each hit's
callers up to the entry point and see whether one is submitted.

## 4. Logging: every level has a reader, and the traceback is the message

| Level | Its reader | In this pipeline | A NAK when |
|---|---|---|---|
| DEBUG | whoever replays this one run | the values needed to reproduce a decision | the agent left it behind (maintainer-rules.md section 5) |
| INFO | the operator reading last night's log top to bottom | one line per stage with numbers in it: rows in, rows out, dropped by reason, tickers skipped | per row, per ticker or per request; "Starting" and "Done" with no number |
| WARNING | someone who should act this week | a recoverable surprise and its count: a source skipped, a fallback taken | it fires on every normal run, which teaches the reader to skip warnings |
| ERROR | someone who must act today | work that did not happen: a stage with no output, a ticker with no prediction | the function then returns normally |

| In the diff | Why it is wrong | Fix | NAK |
|---|---|---|---|
| `log.error("article failed for %s: %s", url, err)` in the pool's `.result()` loop | keeps `str(err)` and discards the type, the file and the line; for a `KeyError` the whole entry reads `article failed for <url>: 'close'` | `log.exception(msg)`, which is `log.error(msg, exc_info=True)`, prints the traceback and belongs only inside an `except`; a handled error worth its stack is `log.warning(..., exc_info=True)` | "`'close'`. That is the whole error. Where did it fail?" |
| `except FetchError as err: log.error(...); return None` | ERROR then a normal return is `except: pass` with paperwork; naming the exception walks it past the `broad except` smell, and the `None` becomes a missing row nobody counts | decide: recovered is WARNING at most and the caller learns it from the return value (section 3's `failed`); not recovered raises. What an `except` returns is behaviour: own patch, own test, never inside a logging cleanup | "Logged at ERROR and returned normally. Which is it?" |
| `log.error(...)` then `raise`, at every level of the chain | one failure, five entries | below the boundary raise `from err` and say nothing; the boundary (CLI top level, the per-ticker loop, the `.result()` loop; python-idiom.md on `except Exception`) logs once | "The same error five times. Which do I read?" |
| `log.info("got %s", df)`; a vendor payload, an article body, headers, a URL carrying `?apikey=` | size, licensed text and secrets (security.md section 5) in a file nobody guards, on a line nobody can grep | the shape and the key: `rows=%d tickers=%d as_of=%s` | "What does this print on a bad day, and who can read the file?" |
| `logging.basicConfig(...)`, `addHandler`, root `setLevel` at module level or in a library function | `basicConfig` does nothing once the root logger has a handler, so the first module imported wins, and the tests, the CLI and the scheduled task get three different logs | `log = logging.getLogger(__name__)` is the only logging statement outside a function; configure once in the entry point; moving it changes what prints, so its own patch | "What decides the log level here, import order?" |

Detectors: `exception logged without its traceback` (`risk_detectors.py`)
sees `.error(` and `.critical(` whose arguments end in `e`, `err`, `exc` or
`error` or hold `str(e)` or `{e}`; it misses `.warning(`, a bare
`log.error(err)` and any other name, so `grep -nE
"log(ger)?\.(warning|error|critical)\("` the touched files and read each hit
inside an `except`. `global state mutated at runtime` has `basicConfig(`.

## 5. Breaking a caller you cannot see

The diff shows the definition, not the notebook, `scripts/`, the scheduled
task's command line, the dashboard's SQL or the next stage reading the
parquet file. `audit_diff.py` prints "Public surface changed":

| Kind in the report | It means | Do |
|---|---|---|
| `BREAKING: removed or renamed` | the public name is gone from that file | grep every caller, non-Python files included; migrate them all in this series, or deprecate (below); its own patch, and the message says who calls it |
| `BREAKING` | a parameter removed, renamed, reordered or inserted before the end, or a default changed | the same; a changed default is a flip (behaviour-switches.md section 4) |
| `widened (compatible, but a new default is a new switch)` | parameters appended, all with defaults | existing calls still work; the default must reproduce the old behaviour |
| `moved: the import path changes for every caller` | the file was renamed | `python -m pkg.mod` in the scheduler, `[project.scripts]`, imports by string, pickles naming the old path |

It sees public functions and methods, never what they return, and **for a
pipeline the output is the API**: a frame's columns, their order, dtypes and
index; a dict's keys; a file's path; a table's columns. No signature changes,
the type checker is silent, and tests that count rows pass:

| The output change, as it appears | What the consumer meets |
|---|---|
| `close` renamed `adj_close` "for clarity" | `KeyError` at 06:00, which is the good case |
| `date` turned `object` by a trip through CSV; an integer column gone float because one row is missing | a join that matches nothing; the id `1234.0` |
| `reset_index()`; both sides of a merge now carrying `volume` | the key is a column; `volume_x` |
| column order changed | a consumer inserting by position writes closes into the volume column, and nothing fails |
| `date=2024-03-08/` becoming `2024/03/08/` | the reader globs nothing and reports an empty day |

Ask the user once, at step 1, what outside this repo imports the package or
reads its tables and files, and put the answer in `problem.md`. A change to
the output is its own patch, its message lists old and new columns and who
reads them, and a test pins `list(out.columns)` against a literal, each
column's `dtype.kind`, and the index type. NAK: "You renamed a column. Who
reads this frame?" No detector: `git diff <base>..<head> | grep -nE
"^[-+].*(rename\(|columns ?=|reset_index|set_index|astype\(|suffixes|to_(csv|parquet|sql)\(|(CREATE|ALTER) TABLE)"`.

**Deprecation.** When something outside the repo uses the old name: add the
new one, keep the old as a one-line alias calling `warnings.warn("...",
DeprecationWarning, stacklevel=2)` (so the warning names the caller's line),
and remove the alias in a **later series**, after a release or a confirmed
run: a flip and its removal are never one series (behaviour-switches.md
section 4). For data, both columns for one release. By default Python shows
that warning only when the calling line is in `__main__`, and under pytest; a
nightly log will not, so announce it in the changelog's `Deprecated` line and
grep the known callers. With no outside user, the usual case, skip the alias:
migrate every caller and say what was grepped.

## 6. README, decision records and the changelog

| Document | Its reader | It changes in this series when | Agent habit to prune |
|---|---|---|---|
| README | someone running the thing | a patch makes one of its lines false: the run command, a flag, a config key, the schedule. The line rides in that patch (behaviour-switches.md section 2) | a rewritten README, a "Features" list, badges; `docs/IMPLEMENTATION_SUMMARY.md` or `BUGFIX_<x>.md` narrating the work, which is a commit message in the wrong place: its facts move into the messages and the file goes |
| decision record (an ADR): `docs/adr/NNNN-<slug>.md`, or the tree's own convention | whoever would re-litigate it in a year | a decision spans the series and outlives it: SQLite not Postgres, a scheduled task not cron, New York session dates on a Hong Kong host. Context, decision, the alternative rejected, consequences; under forty lines; after merge never edited except "Superseded by NNNN"; a docs-only patch beside the patches it explains | one per patch, or a template filled for its own sake: a per-patch "why" is the commit message |
| `CHANGELOG.md` | the operator and the consumers of the output, who do not read `git log` | the tree already has one **and** a user can observe the change | an entry per commit; "Refactored loader for maintainability" |

In audit mode the decision record is not optional: `audit.md` lives in
`.review-ready/`, which git is told to ignore, so the target tree's rationale is
otherwise thrown away. NAK: "This describes your week. What is true next year?"

**The changelog does not contradict "history belongs in git".** comments.md
cuts history from *code comments* because nothing updates a comment when the
change it records is undone, and `git log` already holds it. A changelog is
a release document: append-only, so a revert adds an entry instead of
falsifying one, and sorted by what a user observes (Added, Changed,
Deprecated, Removed, Fixed, Security), which commit subjects are not. It
belongs in the repo; a comment recording a code change does not. The kernel
keeps none and builds release notes from `git log`, which settles the rest:

| Rule | Reason |
|---|---|
| a tree without a changelog does not get one from this series | a knob nobody asked for (SKILL.md step 2, item 10) |
| an entry only for what a user can observe: a number that moves, a column, a flag, a flip, a deprecation | a refactor, a test or a comment patch changes nothing the operator sees; `Deprecated` and `Removed` are where section 5's deprecation is announced |
| the entries are one docs-only patch, last in the series, each a sentence from its patch's message (old behaviour, new behaviour, who sees it), in the file's own format | the rule for config and data: otherwise nobody can tell whether the entry describes the old code or the new. Where the repo's convention or CI wants the entry beside the change, the repo wins |

NAK: "A changelog entry for a refactor. What would the operator notice?" By
hand (it is a docs file to `audit_diff.py`): `git log --stat <base>..<head> -- "CHANGELOG*"`.

## 7. Project standards as audit findings

Findings in `audit.md`, or lines in `deferred.md` in pre-pr mode: decisions
for the owner, never fixes folded into a feature series. A pre-pr run meets
them only when the branch touches `pyproject.toml`, a requirements file or
the lockfile. python-language.md owns the metadata rules. The owner's tree
has `src/acme`, one `pyproject.toml` with ranges, a committed `uv.lock`.

| Finding | As it appears | Why it matters | Disposal |
|---|---|---|---|
| flat layout | the package directory at the repo root | `import pkg` resolves to the working directory whether or not anything is installed, so tests pass against the tree by accident and the built artifact is never exercised; it is why `import_check` exists | a paragraph in `audit.md`, not a mandate; the move to `src/` is a `git mv` group in the architecture stage if the owner wants it |
| more than one declaration | `setup.py`, `requirements.txt` and `pyproject.toml` each listing dependencies; `__version__` beside `[project] version` | nobody knows which the deploy reads: a dead switch (behaviour-switches.md section 5) | say which file each install path reads (README, scheduler, Dockerfile); consolidating is its own series |
| ranges with no lock | an application declaring `pandas>=2.0` with no lockfile committed | the range is a switch whose position is "whatever resolved that day"; no reviewer knows what the tests ran against, and library defaults move with it | an application: ranges in `pyproject.toml` say what is supported, a committed lockfile says what ran, and both exist. A library others install: ranges only, as wide as tested, never `==` |
| lockfile churn | 900 lines of `uv.lock` inside a feature patch; every hash moved because a newer tool rewrote the file; `pyproject.toml` changed and the lock did not | generated, unreadable, and it carries real behaviour | its own patch with the `pyproject.toml` line that caused it; the generating command (`uv lock --upgrade-package pandas`) and the versions that moved, by name, in the message; an unrelated upgrade sweep is reverted |

## 8. External tools: install nothing, run only what the tree already runs

| Rule | Why, and as it appears | NAK |
|---|---|---|
| **Install nothing, ever**: no `pip install`, `uv add`, `uv tool install`, `uv run --with`, `pipx`, `brew`, `npx`, `pre-commit install` | the scripts read git and a private worktree; an install changes the environment the tests run in and what `import_check` resolves. A test command can do it too: `uv run` syncs the project environment before it runs anything, and the owner's `pyproject.toml` records that a dev-only `uv run --extra dev pytest` would uninstall an optional dependency the nightly job needs. Use the test command the user or the README gives, verbatim, or `<repo>/.venv/bin/python -m pytest`, which syncs nothing | "What did you install on my machine to tell me that?" |
| **Run ruff, mypy, bandit, vulture or radon only when the tree already configures it and its environment already holds it** | configured means a `[tool.<name>]` table, `ruff.toml`, `mypy.ini`, a `setup.cfg` section, a `.pre-commit-config.yaml` hook, a CI step or a Makefile target: the project's decision, already made. The command is the tree's own, flags included, never stricter (`--strict`, `--select ALL`) | "This tree does not run mypy. Why am I reading its output?" |
| **Only what the branch introduced counts** | the commands below run the tree's own command against every commit of the series, and against the base | "It failed before you started. Which patch made it worse?" |
| **Findings follow python-idiom.md** ("Why the PEPs are here") | on a line the branch added, fixed in the patch that adds the line; anywhere else a separate commit or `deferred.md`; never a review comment in a logic patch | "That is a formatter's job. Where is the logic?" |
| **Never add a tool or its config in the series** | no new `[tool.ruff]`, `mypy.ini`, `.pre-commit-config.yaml`, CI workflow or dev dependency, and no `# noqa` or `# type: ignore` to quiet one. Adopting a gate is the owner's decision and its own PR; a branch that arrives with a new linter table and the reformat it caused loses both at the prune | "I did not ask for a linter. Who maintains this config?" |
| **A tool's silence is not a review** | clean ruff, mypy and bandit runs say nothing about look-ahead, a future nobody reads, a renamed column or a wrong problem statement; bandit cannot know whether the value inside the braces came from outside the process (security.md). A verdict never cites a tool as a reason to ACK | "The linter is clean. What did you read?" |

```bash
python3 $S/audit_diff.py --repo <repo> --base <base> --head <head> \
    --check "<repo>/.venv/bin/ruff check ."     # absolute path: the export has no venv
tmp=$(mktemp -d) && git -C <repo> archive <base> | tar -xf - -C "$tmp" \
    && (cd "$tmp" && <repo>/.venv/bin/ruff check .); echo "base: $?"
```

`--check` runs the command in a read-only export of every commit after the
base and records one of five states (ok, FAIL, did not run, timed out, vacuous)
with the last lines the command printed. Green on the base,
red at patch k: patch k introduced it, and the fix goes into patch k. Red on
the base: the tree does not hold that gate; one line in `deferred.md`.

| Tool | If the tree configures it | Either way |
|---|---|---|
| ruff, black, isort, flake8 | `--check`, base then series | findings follow python-idiom.md |
| mypy | the tree's flags only | no hints added to untouched code to quiet it (python-idiom.md, type hints) |
| bandit | its hits are triage, like the `security` group | the trace from input to sink is security.md's |
| vulture, radon | a second opinion only | every deletion is still verified by hand (repo-audit.md section 1); a composite score maps to no patch, and `code_metrics.py` reports cyclomatic only where it disagrees with length and depth |
| interrogate, coverage gates, mutmut, pip-audit | not run | docstring coverage pushes against comments.md; mutation and coverage are testing.md sections 8 and 9; a CVE in a dependency already pinned is not this series' defect |

## 9. Review questions for the maintainer pass

Added to the NAK list in review-checklist.md; each fix is in its section.

- "A bug fix with no `Fixes:`. Which commit broke it, and since when?"
- "Who is this `Reviewed-by`, and where did they say so?"
- "This fix sits on three refactors. How does it reach the release branch?"
- "v2 of what? What changed since I last read it?"
- "Third round and I still object. What is the problem?"
- "What do these workers share, and what makes that safe?"
- "Where does this worker's exception go? Which thread owns this connection?"
- "Why 16 workers? What did 4 measure?"
- "Logged at ERROR and returned normally. Which is it?"
- "`'close'`. That is the whole error. Where is the traceback?"
- "You changed a public signature. Who calls it that this diff does not show?"
- "You renamed a column. Who reads this frame, this file, this table?"
- "Old name removed in the series that adds the new one. What runs tonight?"
- "A changelog entry for a refactor. What would the operator notice?"
- "900 lines of lockfile in a feature patch. Which dependency moved, and why?"
- "I did not ask for a linter. The linter is clean: what did you read?"
