# Data correctness: what a clean diff can still get wrong

Read this at step 1 whenever the diff touches a function that takes or
returns a frame, or a feature, a label, a timestamp, an aggregation or a
table in the store; at the prune (SKILL.md step 2) for every hunk a detector
in section 9 names; and at the maintainer pass (step 7), where each section's
review questions extend the NAK list in review-checklist.md. The other
references judge whether a patch is small and obvious. This one judges
whether the number is right, which no formatter, type checker or green test
run reports: a feature that reads tomorrow's close passes every test,
improves the backtest, and degrades in production with nothing to bisect. A
prediction pipeline that reads the future is worse than one that crashes.

Sources: the pandas 3.0 API reference and user guide ("Copy-on-Write",
"Working with missing data"), scikit-learn `model_selection`, NumPy `round`,
the Python docs for `sqlite3`, `datetime`, `zoneinfo`, `math`, `statistics`
and `decimal`, sqlite.org "Datatypes In SQLite"; all paraphrased, the code
original. Blocks headed `# pandas: illustrative` were not run; every other
block runs on Python 3.9 with the standard library alone. The tree in mind: a
nightly prediction pipeline over market and news data, on a Hong Kong host,
against the US market, stored in SQLite.

Contents: 1 the data contract in problem.md, 2 point-in-time and look-ahead,
3 restatement, vintage and survivorship, 4 time, 5 missing data, 6 pandas,
7 numerical, 8 sqlite3 as the store, 9 the detectors.

**Router.** Plain Python over rows, no frame and no SQL in the diff: read
sections 1, 4 and 5 and stop. A feature, a label or a model: add 2 and 3.
pandas or numpy: add 6 and 7. A `sqlite3` call: add 8. Section 9 is a lookup
for a detector name the audit printed.

**Disposal.** A change to anything in this file is a behaviour change however
small its diff: its own patch and its own test, ordered the way
behaviour-switches.md section 4 orders a flip, with the number that moved in
the message (rows, missing count, the metric before and after). Introduced by
this branch in a hunk the problem needs: rewrite it inside that patch.
Introduced in a hunk the problem does not need: prune. Already in the tree
before the branch: one line in `deferred.md`, plus an open question in the
cover letter when a number the user relies on is wrong; never a ride-along
fix. Decided by what the data means rather than by the code (what missing
means, which session owns an after-hours item): one precise question to the
user, never both readings behind a flag. `[name]` is a detector in section 9,
`[shipped: name]` one already in `scripts/switch_detectors.py`; a hit starts
a trace and is never a verdict.

Ruled elsewhere and not repeated: `on=`, `how=`, `validate=` on every merge
(maintainer-rules.md section 5, clean-code-examples.md section 10); `fillna`,
`dropna`, `errors="coerce"` and `value or default` as silent fallbacks, the
`NA` ticker in `read_csv`, the inferred date format, the injected clock, the
passed generator (behaviour-switches.md section 2); `if not df:`
(python-idiom.md section 2); SQL through parameters (python-idiom.md
section 5).

## 1. The data contract in problem.md (step 1)

`problem.md` carries the call chain and the switches. When the change touches
a frame or a table it carries a third block: one row per frame or table that
crosses the boundary of a changed function. No question below can be asked
without it; a reviewer who does not know that one row is one (ticker,
session) cannot judge a merge, a shift or a mean over it.

| Column | What it answers | What goes wrong when it is unstated |
|---|---|---|
| grain | one row per what | a merge fans ticker-day out to ticker-article and every mean is weighted by news volume |
| key | which columns are unique, and what enforces it | `drop_duplicates` standing in for a UNIQUE constraint nobody declared |
| dtypes | of the key and the time column, on each side of every join | section 6, dtype at the boundary |
| units | fraction, percent or bps; raw or split-adjusted; currency; shares or lots | `* 100` applied twice; a `0.02` threshold against a percent column |
| time | the instant the time column means, and its zone: bar open or bar close, session date or UTC date, published or fetched | section 4 |
| known at | the earliest instant this pipeline could have held each value | section 2; a daily close dated D is known at D's close, not at D 00:00 |
| missing means | not traded, not arrived yet, not applicable, or vendor error | four meanings, four correct treatments, one `fillna(0)` |
| rows | the count before and after the change | the only witness a join or a `dropna` leaves |

```
frame     grain                  key (enforced by)          known at                      missing means
prices    ticker, session        ticker+date (UNIQUE)       session close + vendor lag    no row: not fetched; never "no session"
articles  article                id (PRIMARY KEY)           fetched_at, not published_at  ticker NULL: untagged
features  ticker, session, name  date+ticker+name (UNIQUE)  the prior session's close     NULL: an input was missing; never 0
```

Known-at is a fact about this pipeline, not the world: an article backfilled
today carries last month's `published_at`, and no run last month held it. The
converse is the owner's own incident class: the acquisition state of a row
(fetched, late, absent) is never evidence about the market (no session, no
news, delisted). "This session never happened" comes from the exchange
calendar, never from the absence of a row the job itself writes.

Fix: write the table from the code and the ticket; what cannot be written
from them is one precise question to the user (SKILL.md step 1). Check the
cheap columns once, where data enters the tree (python-idiom.md section 4,
the boundary rule): key unique and not null, key and time dtypes, zone; not
again in every function downstream. NAK: "You changed a function that takes a
frame and did not say what one row of it is." No regex finds this; an AST
pass can only list the changed functions that take or return a frame.

Review questions: *what is one row of this frame, which columns make it
unique, and what enforces that? What unit is this column in, and who
downstream assumes another? When could this pipeline first have known each
value? What does a missing value mean here, and what does a missing row
mean?*

## 2. Point-in-time correctness and look-ahead (steps 1, 2 and 7)

The question, asked of every feature the diff adds or changes: **what is the
latest instant of any input to this number, and is it before the moment the
prediction is made?** Trace it the way behaviour-switches.md section 3 traces
a switch: name each input, its known-at from section 1, and the decision
time; one input later than the decision is the finding. It is behaviour that
is not in the diff, with the time axis of the data as the hidden input.

| Look for | What it does | Fix | NAK |
|---|---|---|---|
| `.bfill()`, `method="bfill"`, `.interpolate()` on a feature `[backward fill or interpolation]` | copies the next observation into the gap; linear interpolation uses the point after it | `ffill(limit=n)` within the key, or leave it missing (section 5) | "Backward fill copies the future into the past. Which rows got a value they could not have had?" |
| `.shift(-k)`, `.diff(-k)`, `pct_change(-k)`, `center=True` outside the target `[negative shift or centred window]` | reads k rows ahead by definition | legal only on the line that builds the target; name that column `target_*` and keep it out of the feature list | "Negative shift is the future. Is this a feature or the label?" |
| `.rolling(n)` or `.ewm(...)` over a value not yet known at decision time `[window includes the current row]` | the default window is closed on the right, so row t's own value is in it | `.shift(1)` after the window, or `closed="left"`; say which in the docstring | "Your 20-day mean for D contains D's close. When is the prediction for D made?" |
| `groupby(k).transform("mean")`, `(x - x.mean()) / x.std()`, `.rank(pct=True)`, `qcut` over all dates `[statistic over the whole history]` | every row is scaled by rows after it | expanding and lagged, or fitted on the training window only | "That mean includes next year." |
| scaler, encoder, imputer, selector or PCA `.fit` before the split `[fit call]` | test rows shape the transform the model is scored on | fit inside each fold, on train only (a `Pipeline` does it) | "You fitted on rows the model must not have seen." |
| `train_test_split` (default `shuffle=True`), `KFold`, `cross_val_score` on dated rows `[shuffled or random split]` | trains on days after the ones it is tested on | split on sorted unique dates, walk forward, `gap` at least the label horizon | "k-fold assumes exchangeable rows. These have a date." |
| `merge_asof` with `allow_exact_matches` unnamed (default `True`) `[as-of join with a default unnamed]` | a news item stamped 09:35 matches the bar labelled 09:35, whose close prints at 09:40 | join on each side's known-at instant; `allow_exact_matches=False` | "Same stamp is not 'before'. When did that bar finish?" |
| a feature built from the target's own inputs, or from an outcome table (grades, verifications) joined back; no regex | the label leaks through a column with an innocent name | trace the target to its columns; no feature reads one at the same or a later instant | "Trace the label back. Which feature reads the same column?" |
| `shift`, `diff`, `pct_change`, `ffill`, `cumsum` on a long (ticker, date) frame with no `groupby` `[row operation across the key]` | MSFT's first row is differenced against AAPL's last; and `shift(1)` is the previous row, not the previous session, so a missing day makes a two-day return | sort on a total key (section 6), `groupby(key)` first; reindex to the session calendar when "previous session" is meant | "Previous row of what?" |

The test that ships with a feature patch is the truncation test: a
point-in-time feature for row t is unchanged when every row after t is
deleted. It needs no model and no backtest, and every row of the table except
the two about splitting fails it.

```python
def zscore_leaky(xs):
    mu = sum(xs) / len(xs)
    sd = (sum((x - mu) ** 2 for x in xs) / len(xs)) ** 0.5
    return [(x - mu) / sd for x in xs]

def zscore_asof(xs, warmup=3):
    """Score each value against the rows strictly before it; None until warmup."""
    out = [None] * min(warmup, len(xs))
    for t in range(warmup, len(xs)):
        mu = sum(xs[:t]) / t
        sd = (sum((p - mu) ** 2 for p in xs[:t]) / t) ** 0.5
        out.append((xs[t] - mu) / sd if sd else None)
    return out

def unchanged_by_the_future(feature, xs, t):
    return feature(xs)[t] == feature(xs[: t + 1])[t]   # same inputs, same order: exact

closes = [10.0, 10.2, 10.1, 10.4, 10.3, 12.9]         # the last row is tomorrow's jump
assert unchanged_by_the_future(zscore_asof, closes, 4)
assert not unchanged_by_the_future(zscore_leaky, closes, 4)

def walk_forward(n_dates, n_folds, test_size, gap=0):
    """Index ranges over SORTED UNIQUE DATES, never over rows: a row split
    puts half of one session in train and the other half in test."""
    for k in range(n_folds):
        start = n_dates - (n_folds - k) * test_size
        yield range(0, start - gap), range(start, start + test_size)

folds = [(tr[-1], te[0]) for tr, te in walk_forward(100, 3, 10, gap=5)]
assert folds == [(64, 70), (74, 80), (84, 90)]        # a 5-day label needs gap >= 5
```

A leak fix makes the backtest worse. That is the evidence the fix is right,
and the message says so with both numbers, measured walk-forward: "accuracy
0.61 -> 0.54; the 0.61 was reading D's close". A feature change that makes
the backtest better carries the same two numbers and the truncation test, or
it is a NAK: "Better on which split, and what did the feature know?"

Review questions: *for each input of this feature, when was it known, and
when is the prediction made? Does the truncation test exist, and did it fail
before this patch? Is any statistic, scaler or vocabulary computed over rows
later than the row it is applied to? Is the split by date, with a gap of at
least the label horizon?*

## 3. Restatement, vintage and survivorship: the half no regex can see

Section 2 finds look-ahead written in the code. This half lives in the data
source, so the only instrument is a question to the user, asked once per
series whenever a patch touches a table the feature builder reads. An
unanswered one is an open question in the cover letter, never a guess.
`[stored row rewritten in place]` surfaces candidate tables; whether a vendor
ever revises a row is not a fact about the tree.

| Fact about the source | What it does to the number | Fix | The question |
|---|---|---|---|
| **Rows get revised**: a corrected close, a re-tagged article, and every past adjusted price rewritten on each split or dividend | updated in place, the table answers "what do we know now"; re-running last Tuesday today yields a number last Tuesday could not have produced, and a stored prior close beside a fresh download can sit on two adjustment scales, which shows as a wild overnight gap. The upserts of section 8 are exactly this revision | an append-only table with the fetch instant in the key, read as "latest as of T"; or keep the upsert and say in `problem.md` that backtests run on revised data | "Is this table only INSERTed, or also UPDATEd? Then which version did the backtest read?" |
| **The universe is a time series**: today's ticker list | excludes what was delisted, acquired or renamed; a reused symbol joins old news to a different company's prices | a membership table with from and to dates | "Where does the universe for date D come from, and was it known on D?" |
| **Report date is not period end**: a Q2 figure | not knowable on 30 June; joined on the period label it is a quarter-long leak | join a fundamental on its publication instant | "Joined on the period it describes, or on when it was filed?" |
| **The schedule moves too**: a rescheduled earnings date | `INSERT OR IGNORE` keeps the first version forever, a phantom date; `INSERT OR REPLACE` destroys the record that it ever moved | decide whether the consumer wants the schedule as known then or as known now; the message says which | "Which vintage of the calendar does a re-run of an old date read?" |

Review questions: *append-only or revised in place? What did the universe
contain on D? Period or publication? Adjusted as of when?*

## 4. Time correctness (steps 2 and 7)

One rule covers most of it: aware at the boundary, UTC at fixed width in the
store, converted only to bucket by session or to display. The rest is knowing
where the zone gets lost and whose day it is.

| Look for | What it does | Fix | NAK |
|---|---|---|---|
| naive beside aware: a value parsed back from SQLite (no timestamp type; it returns `str`), a column `to_sql` wrote (aware stored as naive local time where the database has no zone support), a vendor's aware value; ISO text with mixed offsets compared as text | pandas refuses to merge aware on naive, the good case; in plain Python `naive == aware` is `False` with no error and `<` raises far from the cause; text order is not time order | give the naive side its zone where it enters; never strip the aware side to match | "Which zone is this in, and where did the naive one get it?" |
| `date.today()`, `datetime.now()` with no zone, `utcnow()` bounding a market computation `[naive clock or local date]`; `[shipped: wall clock read]` also fires on the correct `datetime.now(ET)` | the host's date rolls over at 12:00 New York time in summer and 11:00 in winter, mid-session, so a lookback floor, "events from today on", "is this session complete" and the date a grade is filed under run a day ahead for half the session; this tree has graded a half-formed session that way | one `today_et()` beside one `session_of(ts)`, `as_of` injected (behaviour-switches.md section 2); completeness from the last bar against the close, never from a bar count or the date having changed | "`today` where? It is already tomorrow in Hong Kong." |
| `ts.dt.date`, `.dt.normalize()`, `.date()` on UTC or host time `[calendar date taken from a timestamp]` | 20:00 New York news files under the next UTC day; an after-hours, weekend or holiday item has no calendar-day answer at all | convert to the exchange zone first; which session owns the item is a contract decision (section 1) read from the exchange calendar, half-days included, never weekday arithmetic | "Which session owns this row, and what decided that?" |
| `timedelta(hours=-4)`, `+8`, `+12` standing in for a zone `[fixed-offset zone arithmetic]`; `tz_localize(None)`, `replace(tzinfo=None)` `[zone discarded]` | wrong for the half of the year across a DST change; the second throws the answer away so a comparison stops raising | `ZoneInfo`, and fix the side that lacks a zone | "That offset is right until November." |
| `.resample(...)` with `closed=` or `label=` unnamed `[resample with closed or label unnamed]` | both default to left except for `ME`, `YE`, `QE`, `BME`, `BA`, `BQE` and `W`, which default to right, so `"D"` to `"W"` flips the convention with no keyword in the diff; a bar stamped with its start holds prices up to its end, so joined on the stamp it is look-ahead by one bar | name both; carry the instant the bar was complete as its own column | "Is 09:35 when this bar began or when it was known?" |
| `merge_asof` `[as-of join with a default unnamed]` | needs both sides sorted on the `on` key overall, not within `by` groups, and no nulls in it; it raises `ValueError` otherwise. The defaults that pass silently: `direction="backward"`, `allow_exact_matches=True` (section 2), and `tolerance=None`, unbounded staleness, so a halted ticker's news matches a price three weeks old | the sort goes on the line above, never in a `try`; name all three | "How stale may the matched row be?" |

```python
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HKT, ET = ZoneInfo("Asia/Hong_Kong"), ZoneInfo("America/New_York")
naive = datetime(2026, 9, 21, 16, 0)                       # as parsed back from the store
aware = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
assert (naive == aware) is False                           # no error; `<` would raise TypeError
a, b = "2026-09-21T23:30:00+00:00", "2026-09-21T20:00:00-04:00"
assert a > b and datetime.fromisoformat(a) < datetime.fromisoformat(b)   # text order is not time order

host = datetime(2026, 9, 22, 0, 5, tzinfo=HKT)             # the nightly job, five past midnight
assert host.date().isoformat() == "2026-09-22"             # what date.today() returns on this host
assert host.astimezone(ET).isoformat() == "2026-09-21T12:05:00-04:00"    # New York: Monday, mid-session

bar_open = [570, 575, 580]                                 # minutes ET; a bar covers [open, open + 5)
news_at = 575
assert bar_open[bisect_right(bar_open, news_at) - 1] == 575   # merge_asof default: exact match allowed
assert bar_open[bisect_left(bar_open, news_at) - 1] == 570    # allow_exact_matches=False
```

Review questions: *which zone is every timestamp in on each side of this
comparison? Which session owns this row, and what decides that? Is this bar
stamped at its start or its end? On the Hong Kong host at 00:05, what does
this line return?*

## 5. Missing data (steps 2 and 7)

Absent has five spellings: `None` in an object column, NaN in a float
column, `NaT` in a datetime column, `pd.NA` in a nullable one, NULL in the
store. They do not behave alike, and the explicit coercions that
`[shipped: None or NaN coerced silently]` catches are the minority: most
missing values become a number with no keyword in the diff.

```python
import statistics
nan = float("nan")
assert bool(nan)                                   # truthy: `if row["ret"]:` takes the branch
assert not (nan > 0) and not (nan <= 0)            # so `ret > 0` and `not (ret <= 0)` keep different rows
assert nan != nan                                  # so `== nan` selects nothing; isna() or math.isnan() only
assert statistics.median([nan, 1.0, 2.0, 3.0, 4.0]) == 2.0   # the four real values have median 2.5
assert max([1.0, nan]) == 1.0 and max([nan, 1.0]) != 1.0     # the answer depends on row order
gap = None
assert (gap or 0.0) == 0.0                         # "unknown" became "flat", and the sign test after it lies
```

| Look for | What it does | Fix | NAK |
|---|---|---|---|
| `.mean()`, `.sum()`, `.std()` with no arguments `[aggregation with skipna unnamed]` | pandas skips NaN (`skipna=True`), so a column 90% missing yields the mean of the rest; the sum of an all-missing day is `0.0` (`min_count=0`), a believable flat day | the missing count goes in the message; `min_count=1` where empty must stay missing; decide it, do not inherit it | "How many of these rows were missing, and what did they contribute?" |
| the same reduction moved across `.to_numpy()` | `np.mean` propagates NaN and `np.nanmean` skips it: the refactor flipped the rule | keep the library, or name the function that matches the old rule | "No functional change? pandas skipped those; numpy does not." |
| `== np.nan`, `!= math.nan` `[equality against NaN]` | never true, so the filter is empty or total | `.isna()`, `math.isnan()` | "That comparison is always false." |
| `groupby(key)`, `value_counts()`, `nunique()` on a key that can be missing `[groupby with dropna unnamed]` | `dropna=True` drops those rows, so per-ticker counts do not add up to the frame; `count()` omits NaN and `size()` does not | name `dropna=`; the test reconciles the group total against `len(df)` | "Your groups sum to fewer rows than the frame." |
| `.dropna()` with no `subset=` `[dropna with no subset]` | drops a row for a NaN in any column, so a sparse column added upstream deletes training rows downstream | `subset=` the columns this step needs | "Which column's NaN removes the row?" |
| `.ffill()` with no `limit=`, or across the key `[forward fill with no limit]` | a delisted ticker stays flat for years; AAPL's last close fills MSFT's first gap | `groupby(key).ffill(limit=n)`; say how stale is acceptable | "Filled from how long ago, and from which ticker?" |
| `pct_change()` under a pandas 1.x pin | it forward-filled before differencing (`fill_method="pad"`), so a missing close became a 0% return; 3.0 fills nothing | the pin bump is its own patch, before this one (behaviour-switches.md, "Runtime environment") | "Same line, different returns. Which pandas?" |

**`fillna` is a statement about the world**, and the message defends it.
`fillna(0)` on a return says the price did not move when the truth is that
nobody looked; `ffill` says nothing changed since the last observation;
`bfill` reads the future (section 2); `dropna` says the row never existed and
changes the grain. When no statement is defensible the value stays missing to
the end: a NULL input yields a NULL output, no prediction and no grade,
because a missing grade is recoverable on the next run and a wrong one is
not. A patch that adds a fill carries the missing count before it and names
the consumer who needs a number there.

Review questions: *how many were missing, and what became of them? Which of
the five spellings can reach this line? What does this fill claim about the
market, and who asked for it? Does a missing input reach the output as
missing?*

## 6. pandas: the object model (steps 2 and 7)

| Look for | What it does | Fix | NAK |
|---|---|---|---|
| two indexing operations left of `=`: `df[mask]["x"] = 1`, `df.loc[mask]["x"] = 1`, a write into a filtered `sub` `[chained assignment]` | writes to a temporary. Before 3.0 it sometimes landed and `SettingWithCopyWarning` said so; under Copy-on-Write, the only mode in 3.0, it never lands and the warning is `ChainedAssignmentError`: the pandas pin is a switch for this line | one `.loc[rows, col] = value`; `.copy()` when a detached frame is what you want, said in a comment | "This write may not land. One `.loc`." |
| `pd.set_option("mode.chained_assignment", None)`, a `filterwarnings` on it `[chained-assignment warning silenced]`; `[shipped: global state mutated at runtime]` fires too | a dead canary | remove the silencer in its own patch and read what it was hiding | "What was it warning about?" |
| `df["x"] = series`, `a / b`, `concat(axis=1)` `[series assigned into a frame column]`; `.values`, `.to_numpy()` `[positional escape from alignment]` | index alignment is a join with no keyword: unmatched labels become NaN, binary operations return the union of both axes, the row count does not change, nothing warns; a filter keeps the old labels, so a fresh `0..n-1` Series assigned after it lands mostly as NaN. `.to_numpy()` pairs by position, right only when the rows are known parallel | say which join it is: positional with the reason on the line, or a `merge` with `validate=` | "These two are joined on their index. Same labels, same order, unique on both sides?" |
| a read with no `dtype=` or `parse_dates=` `[read with dtypes inferred]`; `.astype(str)` on a key `[key cast through str or float]` | pandas refuses `int64` against strings and datetimes against `object`, the good case. Two `object` columns spelling one key two ways are never checked: `"1234"` and `"1234.0"`, a `date` and the ISO text SQLite returned, `BRK.B` and `BRK-B`, a trailing space; zero matches is a legal many-to-one, so `validate="m:1"` passes. One missing value promotes an integer column to `float64`: id `1234` becomes `1234.0`, then `"1234.0"` | `dtype=` and `parse_dates=` at the read, ids as `str` or nullable `Int64`, in the patch that adds the read; the test asserts the match count (`indicator=True`), not the shape | "What dtype is this key on each side, and how many rows matched?" |
| `astype("category")`, `pd.cut`, `qcut` feeding a `groupby` `[category dtype introduced]` | `observed=False` emits every unobserved combination, rows for tickers that did not trade; the default became `True` in 3.0 | name `observed=` | "Which pandas decides how many rows this returns?" |
| `df.loc[len(df)] = row`, `df = pd.concat([df, piece])` in a loop `[frame grown in a loop]` | the first overwrites an existing label once any row has been dropped; the second leaves duplicate labels, every piece starting at 0, which then poison alignment, and re-infers dtypes each round, so one all-missing piece turns an integer column to float. `DataFrame.append` is gone since 2.0 | collect dicts or frames in a list, build once with `ignore_index=True`, assert the key unique | "Build it once. What is the index after the third piece?" |
| `.iterrows()`, `.apply(..., axis=1)` `[row-wise iteration or apply]` | the body gets one Series per row, so one dtype per row: integers arrive as floats, a mixed row as `object`, and a write to `row` changes nothing in the frame. Speed is not the objection (maintainer-rules.md section 4, brute force) | `itertuples()` keeps the column dtypes | "Your share count is a float inside this loop." |
| `sort_values` feeding `drop_duplicates(keep=)`, `head`, `groupby().first()`, a top-N with equal scores `[sort with tie order unnamed]` | `sorted` and `list.sort` are stable; `sort_values` defaults to `kind="quicksort"`, which is not, honours `kind=` only for a single sort column, and puts NaN last (`na_position`): the pick differs from run to run. In plain Python a NaN key leaves `sorted` silently unsorted and a `None` key raises `TypeError` mid-sort | make the key total by adding the tie-breaker to `by=`; where none exists, `kind="stable"` and the message says input order breaks the tie | "Two rows share that date. Which one wins, and is it the same one tomorrow?" |

```python
# pandas: illustrative
df[df.ret > 0]["flag"] = 1                   # chained: never lands under Copy-on-Write
df.loc[df.ret > 0, "flag"] = 1               # one indexing call: lands
up = df[df.ret > 0]                          # keeps df's labels, say 0, 3, 4
up["rank"] = pd.Series(range(len(up)))       # labels 0, 1, 2: aligned by label, so 0, NaN, NaN
up = up.assign(rank=range(len(up)))          # a range is positional, and the line says so
latest = (df.sort_values("date")             # quicksort: tie order unspecified, so "last"
            .drop_duplicates("ticker", keep="last"))   # among same-date rows is a coin flip
latest = (df.sort_values(["date", "fetched_at"])       # total key: the data decides the tie
            .drop_duplicates("ticker", keep="last"))
```

Review questions: *is this assignment one indexing call? What is each side's
index, and is the pairing by label or by position on purpose? What dtype is
each key after the read, and how many rows matched? Is every sort that feeds
a pick sorted on a total key?*

## 7. Numerical (steps 2 and 7)

```python
import math, random
from decimal import Decimal, ROUND_HALF_UP
assert (10.2 - 10.0) / 10.0 < 0.02 <= 10.2 / 10.0 - 1     # one 2% move, two labels
assert sum([0.1] * 10) != 1.0 and math.fsum([0.1] * 10) == 1.0
assert sum([1e16, 1.0, -1e16]) == 0.0 and math.fsum([1e16, 1.0, -1e16]) == 1.0
assert not math.isclose(1e-12, 0.0) and math.isclose(1e-12, 0.0, abs_tol=1e-9)
assert -7 // 2 == -4 and int(-7 / 2) == -3                 # floor versus truncate
assert round(2.5) == 2 and round(2.675, 2) == 2.67         # half-even, on a binary float
assert Decimal("2.675").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("2.68")
draws = random.Random(7); first = [draws.random() for _ in range(3)]
draws = random.Random(7); draws.random()                   # one new draw upstream
assert [draws.random() for _ in range(3)] != first         # every draw after it moved
```

| Look for | What it does | Fix | NAK |
|---|---|---|---|
| `==` or a threshold on a computed float: `df[df.ret == 0]`, `ret >= 0.02`, `assert x == 0.3` `[float equality]` | two algebraically equal formulas put the same move on opposite sides of the threshold (first line above), so "simplify the return formula" relabels rows; the test is flaky or passes for the wrong reason | `math.isclose`, `np.isclose`, `pytest.approx` with the tolerance stated, and `abs_tol` whenever one side can be zero, since a relative tolerance never matches zero; integer ticks or basis points when the boundary matters. Exact `==` is right in one test only: same inputs, same operations, same order (section 2's truncation test) | "What tolerance, and why that one?" |
| a sum, `cumsum` or `cumprod` whose input order or grouping changed | row order changes the low bits, so a frame sorted differently upstream moves a rank, a tie or an exact test with no arithmetic in the diff; `cumprod` of returns and `exp(cumsum(log1p(r)))` differ in the last digits | compare with a tolerance; never rewrite a working sum as `fsum` without a failing case | "You reordered the rows. Which downstream comparison is exact?" |
| `int(a / b)`, `a / b` as an id, a key or a bucket `[int of a true division]` | `/` always yields a float; `//` floors and `int()` truncates, so they disagree on every negative return | `//` or `int()` chosen for the negative case, which the test covers | "Which way does -3.5 go, and did the old code agree?" |
| `round(`, `.round(` on a value that is stored or compared `[round on a stored value]` | `round`, `np.round` and `Series.round` round half to even on binary floats, so neither `2.5` nor `2.675` goes where a trader expects | round for display only. This tree produces scores and magnitudes, so converting a float pipeline to `Decimal` inside a cleanup is a prune; where fills, P&L or a reconciliation to the cent appear, that module takes integer cents or `Decimal` built from the vendor's string, rounding mode named, in its own series | "Rounded for whom? The next stage wanted the digits." |
| a patch that adds, removes or reorders a consumer of the generator | behaviour-switches.md carries the seed rules (pass a generator, never seed globally, sort what leaves a set); what it leaves out is that draw order is behaviour: one new draw upstream moves every draw after it (last lines above) | not "No functional change."; the seed is stored in the run record, or derives from the as-of date, so a re-run of D reproduces D | "Same seed, different sample. What drew first?" |

Review questions: *is any computed float compared with `==`, used as a key,
or sitting on a threshold? What tolerance, absolute or relative? Does this
refactor reorder a sum or a stream of draws? Floor or truncate on negatives?*

## 8. sqlite3 as the store (steps 2, 4 and 7)

Re-running a failed night is the normal operation of a nightly pipeline, so
every writer is run twice in its test and the two tables compared. A changed
`CREATE TABLE` is a migration and a changed writer is a behaviour change,
whatever the size of the hunk.

| Look for | What it does | Fix | NAK |
|---|---|---|---|
| INSERT, UPDATE or DELETE with no `commit()` in reach; a `commit()` per row `[DML statement]` | `sqlite3` opens a transaction before the first write and holds it until `commit()`; closing without one discards the night with no error. A commit per row leaves a partial night, one at the end leaves none | say which, per writer; one transaction per unit (ticker, article) is the defensible middle | "Where does this commit, and what is in the table if it dies on row 500?" |
| `with sqlite3.connect(db) as conn:` `[connection as context manager]` | `with conn:` commits or rolls back and does not close, so a loop over tickers leaks handles (python-idiom.md section 2, the `with` rule) | `with closing(sqlite3.connect(db)) as conn, conn:` | "What does that `__exit__` release?" |
| a writer that records only the latest item each run, or decides "final" from its own earlier rows | the owner's incident class: every skipped night is a permanent hole, and "a later row exists" is read as "this one will never arrive" | heal over a window of recent sessions; "final" comes from the market's state (section 1), never from "I already ran" | "What does tomorrow's run do about the night you missed?" |
| `INSERT INTO` with no conflict clause `[plain insert]`; `INSERT OR IGNORE` `[insert or ignore]`; `INSERT OR REPLACE`, `DO UPDATE SET` `[stored row rewritten in place]` | four behaviours: duplicate every row on the re-run; freeze the first vintage (section 3); delete and re-insert, so every column the statement does not name returns to its default and another writer's work is gone; update only what is named | UNIQUE on the key from section 1 plus `ON CONFLICT(key) DO UPDATE SET` naming its columns | "Run it twice. What is in the table?" |
| a nullable column in a UNIQUE key | NULLs are distinct under UNIQUE, so the conflict never fires and every re-run adds a row | key columns are `NOT NULL` | "Your upsert key can be NULL." |
| `to_sql(` `[to_sql call]` | `if_exists="replace"` drops the table with its UNIQUE index and declared types; `"append"` duplicates without one; `index=True` writes the index as a column | create the table from the one schema, then append through the upsert | "You dropped the constraint that made the re-run safe." |
| `CREATE TABLE` outside the schema module, or a changed one `[schema statement]` | the owner's tree defines tables in four modules and repeats column names as literals in the readers. `IF NOT EXISTS` does nothing to a database that already has the table, so the new column exists in the tests' fresh database and not in production: green until the nightly run says `no such column` | one module owns the schema; the `ALTER TABLE` is its own patch, tested against a database built from the old schema (behaviour-switches.md section 2, expand and contract) | "What does your patch do to last night's file?" |
| a test that opens the real database path `[test opens a database by a fixed path]` | passes or fails with last night's data, and may write to it | `:memory:` or `tmp_path`, built from the one schema | "That is not a test, it is a query." |
| a declared `DATE`, `TIMESTAMP` or `BOOLEAN`; a column with no declared type; a comparison on stored text | a declared type is a preference, not a constraint. ISO text stays text and compares as text, right only at fixed width in one offset (section 4). INTEGER affinity turns `'12'` into `12` and `1234.0` into `1234`; a column with no type converts nothing, so `'12' = 12` is false. NaN goes in and NULL comes out, `bool` comes back `0` or `1`, a `date` comes back `str` | convert at the one read function and assert the Python type there; store UTC ISO at fixed width or epoch integers | "What Python type comes back from this column, and which comparison depends on it?" |
| `!=`, `<>`, `NOT IN` on a nullable column `[NOT IN or <> in SQL]` | none selects a NULL row, and `NOT IN` over a list containing NULL selects nothing; `AVG`, `SUM` and `COUNT(col)` skip NULL while `COUNT(*)` does not | `IS NOT`, or `OR col IS NULL`, written out | "Where did the untagged rows go?" |

```python
import sqlite3
from contextlib import closing

def store_close(conn, ticker, day, close):
    with conn:                                   # one transaction: commits or rolls back, never closes
        conn.execute(
            "INSERT INTO prices (ticker, day, close) VALUES (?, ?, ?)"
            " ON CONFLICT(ticker, day) DO UPDATE SET close = excluded.close",   # SQLite >= 3.24
            (ticker, day, close))

with closing(sqlite3.connect(":memory:")) as conn:
    conn.execute("CREATE TABLE prices (ticker TEXT NOT NULL, day TEXT NOT NULL,"
                 " close REAL, note TEXT, UNIQUE (ticker, day))")
    for _ in range(2):                           # the re-run is the test
        store_close(conn, "AAPL", "2026-09-18", 231.5)
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone() == (1,)
    conn.execute("UPDATE prices SET note = 'halted'")
    conn.execute("INSERT OR REPLACE INTO prices (ticker, day, close) VALUES ('AAPL', '2026-09-18', 231.5)")
    assert conn.execute("SELECT note FROM prices").fetchone() == (None,)        # REPLACE deleted the row first
    conn.execute("INSERT INTO prices VALUES ('MSFT', '2026-09-18', ?, NULL)", (float("nan"),))
    assert conn.execute("SELECT close FROM prices WHERE ticker = 'MSFT'").fetchone() == (None,)   # NaN in, NULL out
    assert conn.execute("SELECT '2026-9-5' > '2026-10-01'").fetchone() == (1,)  # dates compare as text
    assert conn.execute("SELECT COUNT(*) FROM prices WHERE note NOT IN ('x', NULL)").fetchone() == (0,)
```

Review questions: *where does each writer commit, and what survives a crash?
Run it twice: same table? Which columns does the upsert not name, and who
else writes them? Is this the only definition of the table, and how does an
existing database get the new column? What type comes back, and is any
comparison done on text?*

## 9. The detectors

Same contract as behaviour-switches.md section 7: a named line regex over the
ADDED lines of non-test files, erring toward noise, whose hit starts the
trace in the section given and is never a verdict. Three decide a NAK alone:
`chained assignment`, `equality against NaN`, and `naive clock or local date`
in a module that handles market dates. Four only mark the line where the
question gets asked: `fit call`, `round on a stored value`, `to_sql call`,
`DML statement`. A call split across lines defeats every "unnamed keyword"
pattern, which then fires; that costs a glance.

| Section | Detectors |
|---|---|
| 2 | backward fill or interpolation; negative shift or centred window; window includes the current row; statistic over the whole history; fit call; shuffled or random split; as-of join with a default unnamed; row operation across the key |
| 3 | stored row rewritten in place (candidates only) |
| 4 | naive clock or local date; calendar date taken from a timestamp; zone discarded; fixed-offset zone arithmetic; resample with closed or label unnamed |
| 5 | aggregation with skipna unnamed; equality against NaN; groupby with dropna unnamed; dropna with no subset; forward fill with no limit |
| 6 | chained assignment; chained-assignment warning silenced; series assigned into a frame column; positional escape from alignment; read with dtypes inferred; key cast through str or float; category dtype introduced; frame grown in a loop; row-wise iteration or apply; sort with tie order unnamed |
| 7 | float equality; int of a true division; round on a stored value |
| 8 | connection as context manager; DML statement; plain insert; insert or ignore; to_sql call; schema statement; NOT IN or <> in SQL; test opens a database by a fixed path (test files only) |

What no regex sees, and the agent checks by hand: the contract of section 1;
every question in section 3; a target that leaks through an innocently named
column; the missing rate behind an aggregation; whether two indexes match; a
`commit()` inside a loop or absent from a writer (an AST pass finds both); and
one table's `CREATE TABLE` in two modules (`git grep -c "CREATE TABLE"`).
