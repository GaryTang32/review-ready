# Security findings a diff can carry

Read this at the prune (SKILL.md step 2) whenever an added line executes a
string, loads a file the process did not write, opens a path built from data,
talks to the network, or names a credential; at the split (step 4), to place
the fix; at the maintainer pass (step 7), where section 9 extends the NAK
list. The threat in a nightly pipeline is rarely an attacker: it is the first
headline with an apostrophe, the filing named `AT&T 10-K.pdf`, the vendor
whose certificate lapsed on a Sunday. The hostile value and the unlucky one
take the same path, and the run meets the unlucky one first. Sources: the
Python, pandas, Requests and PyYAML documentation and the kernel's
`stable-kernel-rules.rst`, paraphrased; the code is original.

Contents: 1 how a finding lands, 2 trace entry to sink, 3 the inventory, 4 SQL
identifiers, 5 secrets, 6 paths, 7 dependency provenance, 8 what a detector
finds and what it cannot, 9 review questions.

## 1. How a finding lands in the series

A security fix is never a passenger. Inside a feature patch it has no message
and no test, cannot be taken alone, and the reviewer who meets a changed
`execute` line in a patch about returns must guess whether it was a fix or an
accident. Four cases:

- **The branch adds the hole in a line the problem needs.** The patch that
  adds the line adds it safe; no commit in the rebuilt series carries the
  unsafe spelling, because a bisect stops anywhere. The message names the
  guard when it is not obvious, and `deferred.md` lists the rewrite so the
  residual diff reconciles.
- **The branch adds it in a line the problem does not need.** Prune it; the
  `deferred.md` line says it must not come back in that form.
- **It was already in the tree** (a context line, a touched file, an audit
  finding). Its own patch, first in the series and depending on nothing after
  it: the one patch someone will want without the rest. The subject names the
  sink (`db: bind the ticker in the prices query`); the body says which input
  reaches which sink, what an unlucky value does today, and what changes for
  legitimate input, normally nothing. Its test passes the hostile value. Never
  "cleanup", never "No functional change.". Outside the problem statement: a
  `deferred.md` line and an open question. Audit severity: `security`.
- **The fix needs a decision the agent cannot make** (rotating a key, a broken
  vendor certificate, a pickle cache another job reads). Open question, first
  in the cover letter: the finding, the options, and that the series leaves
  the line alone meanwhile. Never half-fix.

## 2. Trace the value from where it enters to where it acts

A detector finds a **sink**: a call that executes a string, builds objects
from bytes, opens a path, or sends a request. Whether it is a hole depends on
the **source** of each value in it, which no regex walks. For every hit, walk
each non-literal value in the sink back to where it entered the process.
Outside: a CLI argument, an environment variable, a config file, an HTTP
response, a vendor CSV cell, a filename from a listing, a model's reply, and
**a row read back from the tree's own database** (last night's stored headline
is still the vendor's text). Inside: a literal, a module constant, a run of
`?` sized by `len()`. Then find the step between entry and sink that
**rejects**: a lookup in a fixed mapping, `int(x)`, `date.fromisoformat(x)`, a
containment check. A check that logs and continues is not one. Outside source
and no rejecting step: finding, and the chain goes in `problem.md`.

## 3. The inventory

Secrets and paths follow in sections 5 and 6; SQL continues in section 4. In
the last column "alone" means the hit decides a NAK with no trace, "candidate"
that it starts the trace in section 2.

| Sink | As it appears in this kind of tree | Fix | NAK | Detector |
|---|---|---|---|---|
| **SQL built by interpolation** | `cur.execute(f"... WHERE ticker = '{t}'")`, and the same through `%`, `.format`, `+`, a `VALUES` list built by `join` "for speed", `pd.read_sql(f"...")`; anything from data inside `executescript`, which accepts no parameters at all. Tonight's `Moody's cuts outlook` ends the string literal and the insert dies, or parses as something else. python-idiom.md section 5 NAKs the f-string spelling; this is every spelling | `?` with a sequence or `:name` with a mapping in `sqlite3`; `params=` in `pd.read_sql_query`; `executemany` with one `(?, ?, ?)`; identifiers by section 4 | "The headline is now SQL. Parameters." | `SQL interpolated at the call`, `SQL text carrying a format field`: candidates, since `IN ({placeholders})` and `SELECT {_COLUMNS}` over a module constant are correct and only reading the braces tells |
| **Shell** | `subprocess.run(f"pdftotext {pdf} {out}", shell=True)`, `os.system`, `os.popen` over a directory of filings. `AT&T 10-K.pdf` is enough: the space splits the name, `&` backgrounds the first half; a name holding `;` or `$(...)` runs | An argv list and no `shell=True`: no shell exists, so each value arrives as one argument whatever it holds. A pipe becomes `stdout=` or a second `run`. `--` before names from data, so one starting with `-` is not an option. `shlex.quote` only when a shell is truly required, and the message says why | "That string is a shell script and the filename is part of it. argv list." | `shell=True or a command string`: candidate; a constant command is merely unnecessary |
| **Pickle and its family** | `pickle.load`, `pd.read_pickle`, `joblib.load`, `shelve`, `dill`, `torch.load`, `np.load(..., allow_pickle=True)`: `features_2026-09-18.pkl` written by the nightly job and read by a notebook on another machine through a synced folder; a downloaded model; a fixture someone committed | Acceptable only on bytes this process wrote, in a directory only it writes. Otherwise frames to parquet or CSV, arrays to `.npz` without pickle, parameters to JSON. A format change is a behaviour change for every reader of the old files: own patch naming them, or an open question when another job reads them (behaviour-switches.md section 2, data row). A pickle that stays gets one comment naming its writer | "Who wrote this file? Unless the answer is 'this process, a minute ago', `pickle.load` is `exec` with a file extension." | `pickle-family load`: candidate; provenance is the trace |
| **YAML** | `yaml.load(f, Loader=yaml.FullLoader)`, written because a warning asked for a `Loader`; `yaml.unsafe_load` | `yaml.safe_load` | "A config file has no business constructing objects." | `yaml loaded without a safe loader`: alone |
| **`eval`, `exec`** | A list column round-tripped through CSV or a TEXT column returns as `"['AAPL', 'MSFT']"` and the agent writes `df["tickers"].apply(eval)`: every cell is now a program. `eval(cfg["agg"])` to turn `"mean"` into a function. `df.query(f"ticker == '{t}'")`, where pandas itself warns that `query` can run arbitrary code on user input | JSON on write and `json.loads` on read; `ast.literal_eval` for rows already stored as literals; a dict from name to function, which raises `KeyError` on any other name; `@t` or a boolean mask in place of the formatted query | "`eval` on a cell. What is in row 40,000?" | `eval or exec`: alone unless the argument is a literal. `expression string built by interpolation`: candidate |
| **TLS off** | The vendor fetch fails with `CERTIFICATE_VERIFY_FAILED` on one machine (no CA bundle, a corporate proxy) and the branch adds `verify=False`, `ssl._create_unverified_context()`, `ssl.CERT_NONE`, `urllib3.disable_warnings()`, `curl -k`, or `pip --trusted-host`. Requests then accepts any certificate from anyone on the path, and tomorrow's training prices come from whoever answered | The right roots: `verify="/path/to/ca.pem"` or `REQUESTS_CA_BUNDLE`; `ssl.create_default_context(cafile=certifi.where())` for `urllib`. A vendor certificate that is truly broken is an open question naming the vendor, never a flag | "You didn't fix the certificate error, you stopped looking. CA bundle." | `TLS verification disabled`: alone. The same line usually lacks `timeout=`, and Requests never times out unless told to: `HTTP call with no timeout` reports it, as reliability |
| **Temp files and modes** | `tempfile.mktemp()` returns a name and creates nothing, so another process can create it first; a fixed `/tmp/prices.csv` is the same race plus two nightly runs overwriting each other; `os.chmod(db, 0o777)` added to cure a cron permission error | `tempfile.mkstemp(dir=target.parent)` (created readable and writable by its owner only), write, then `os.replace(tmp, target)`, which also hands a reader the whole file or the old one. Fix the owner, not the mode. A world-readable database is a finding only on a shared host: ask which machine runs it | "`/tmp/prices.csv`: whose file is that by the time you open it?" | `predictable temp path`, `world-writable mode`: alone |

## 4. SQL: what parameters cannot do

Two things may be interpolated into SQL: a run of `?` sized by `len()` for an
`IN` list, and an identifier looked up in a fixed mapping in the code. Table
names, column names and `ORDER BY` keys cannot be bound. `FROM ?` is a syntax
error; `ORDER BY ?` is worse: it binds the name as a string constant, sorts by
nothing and raises nothing. Quoting the identifier is not the fix, since the
caller can still name any column. Interpolate the mapping's value, never the
input.

```python
import sqlite3

conn = sqlite3.connect(":memory:")
conn.execute("CREATE TABLE articles (ticker TEXT, title TEXT)")
title = "Moody's cuts outlook"  # interpolated, the apostrophe ends the literal
conn.execute("INSERT INTO articles VALUES (?, ?)", ("MCO", title))

tickers = ["MCO", "SPGI"]
marks = ",".join("?" * len(tickers))  # the braces hold only ? and commas
sql = f"SELECT title FROM articles WHERE ticker IN ({marks})"
assert conn.execute(sql, tickers).fetchall() == [(title,)]

SORT_COLUMNS = {"headline": "title", "ticker": "ticker"}
column = SORT_COLUMNS["headline"]  # KeyError for anything not listed
conn.execute(f"SELECT title FROM articles ORDER BY {column} DESC")
```

Two traps after the fix. A bound value is safe from SQL, not from the
mini-language inside it: `%` and `_` still act in a `LIKE` pattern and FTS5
syntax in a `MATCH` string, so escape or quote them. And `sqlite3` binds `int`,
`float`, `str`, `bytes` and `None`; a `numpy.int64` straight from a frame is
none of those and fails at bind time. The fix is `int(x)`, not a retreat to
the f-string that "worked". A date binds as text, so `WHERE date > ?` compares
strings, right only for ISO `YYYY-MM-DD` on both sides. NAK: "You can't bind a
column name, and `ORDER BY ?` sorts by a constant without telling you."

## 5. Secrets: not in the tree, not in the history, not in the log

Look beyond `.py`: `config.toml`, `.env`, shell scripts, cron and launchd
entries; notebook **outputs** (a printed `os.environ`, a request's headers),
the security half of "never with outputs" (maintainer-rules.md section 5);
recorded HTTP fixtures, which keep `Authorization` and `Cookie` verbatim; the
fallback that hides one, `os.environ.get("VENDOR_API_KEY", "9f8a7b...")`. In
logs: `log.info("headers=%s", headers)`, a dump of the whole config, and the
quiet one: a key sent as `?apikey=` is in the URL, the URL is in the exception
text, the exception is in the nightly log. Key in a header; log names only.

Fix, in order. (1) Rotation is the only fix for a committed secret and only
the owner can do it: open question, first line of the cover letter. (2)
Rebuild so no patch contains it; the branch as the user left it, kept at
`refs/review-ready/original/<branch>`, and any remote the branch reached still
do, so say that in the delivery message. Deleting the line in a later patch un-leaks nothing. (3) Read it from
the environment where it is used and raise when absent (behaviour-switches.md:
who sets this, and what happens when nobody does). `.env` entering
`.gitignore` is its own patch. NAK: "Is that a live key? Then it is burned.
Rotate it; deleting the line does not un-publish it." Detectors: `private key
or known token prefix` and, over the file list, `credential file committed`:
alone. `secret-looking literal` (noisy), `secret default in an env read`,
`secret-bearing value in a log call`: candidates.

## 6. Paths: the join that discards its root, and `..`

behaviour-switches.md covers paths relative to the working directory; this is
the component that comes from data. `root / name` and `os.path.join(root,
name)` return `name` alone when it is absolute and keep every `..` in it, and
`Path.is_relative_to` is lexical, so it passes `..`: resolve, then test
containment, or reject any name that is not a bare filename. As it appears:
`RAW_DIR / row["filename"]` from a vendor manifest; `out_dir / f"{ticker}.csv"`
when the ticker is `BRK/B`, no attacker, and the write lands in a directory
called `BRK`; a vendor archive through `extractall`, which writes wherever
member names point unless `filter="data"` applies (`hasattr(tarfile,
"data_filter")` says whether the floor has it; if not, check each member name
with `inside`).

```python
from pathlib import Path

root = Path("/data/filings")
assert root / "/etc/passwd" == Path("/etc/passwd")  # absolute operand wins
assert (root / "../../etc/passwd").is_relative_to(root)  # lexical: '..' passes

def inside(root: Path, name: str) -> Path:
    """Resolved path of name under root. Raises ValueError when it escapes."""
    full = (root / name).resolve()
    if not full.is_relative_to(root.resolve()):
        raise ValueError(f"{name!r} resolves outside {root}")
    return full

assert inside(root, "2026/MCO-10K.pdf").name == "MCO-10K.pdf"
```

`is_relative_to` needs 3.9; before that, `os.path.commonpath`. NAK: "Can that
name be absolute, or contain `..`? Then the root you joined it to is a
suggestion." Detectors: `path joined with a value from data` (weak), `archive
extracted without a member check`: candidates.

## 7. Dependency provenance

maintainer-rules.md section 5 makes a new dependency its own patch with a
reason. The security half is a hand check, and agent code needs it more: a
model imports a package that does not exist under a plausible name, and such
names get registered by strangers. For each added requirement: is the name
exactly the project's (`python-dateutil`, not `dateutil`); who publishes it; is
it pinned to the version the tests ran against, with hashes in the lockfile;
does the diff add `--extra-index-url`, a `git+https` source or
`--trusted-host`, which change who can ship you code (`package source changed`
finds those). The message carries the answers. NAK: "Who publishes this, and
did you check the spelling or did the tool?"

## 8. What a detector finds, and what it cannot

A line-level detector finds sinks and never a source. The security detectors
run over the added lines of every file, fixtures included, since that is where
a recorded token lives; when the audit report has no security section, grep
for the sink spellings in section 3 yourself. Seven hits decide a NAK alone:
`yaml loaded without a safe loader`, `eval or exec` on a non-literal, `private
key or known token prefix`, `credential file committed`, `TLS verification
disabled`, `predictable temp path`, `world-writable mode`. Every other name in
this file finds a sink and leaves the source to section 2.

No regex finds: a value read back from the tree's own tables and interpolated;
a whitelist that interpolates the input instead of the mapping's value; `..`
and symlink escapes through a variable; an f-string starting on the line after
the `(`; secrets in notebook outputs, fixtures and tracebacks; who wrote a
pickle; who publishes a package. Read for those by hand.

ruff's `S` rules or bandit list the same sinks. That does not put these under
python-idiom.md section 1's formatter rule: a formatter finding is a separate
commit or out, a security finding is a correctness finding and blocks.

## 9. The review questions for the maintainer pass

An extension of the NAK list in review-checklist.md: the objection, then the
fix demanded.

- "What is inside those braces, and where did it enter the process?" Bind
  values; look identifiers up in a fixed mapping; a run of `?` for `IN`. "It
  came from our own table." A stored headline is still vendor text.
- "That string becomes a shell command." argv list, `--` before names from
  data. "Who wrote the file this loads?" Not this process: parquet, JSON,
  `safe_load`, the format change in its own patch. "`eval` on data."
  `json.loads`, or a dict of functions.
- "Is that a live key?" Open question: rotate, and name the backup ref and any
  pushed remote. "What does this log line print when the config holds a
  token?" Names, not values; the key leaves the URL.
- "Can this name be absolute, or contain `..`?" Resolve and test containment,
  or reject. "A fixed name under `/tmp`; `chmod 777`." `mkstemp` beside the
  target, `os.replace`; fix the owner.
- "You turned off certificate checking." CA bundle, or an open question naming
  the vendor. "New dependency: who publishes it, and which version did the
  tests run against?" In the message.
- "You fixed a hole inside a feature patch." Own patch, first in the series,
  the hostile value in its test. "You call this 'No functional change.'" It
  changes what a hostile input does.
