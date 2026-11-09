# Behaviour is not only the lines in the diff

Where these come from: the kernel tree's `submitting-patches.rst`,
`submit-checklist.rst`, `kbuild/kconfig-language.rst` ("Menu attributes"),
`admin-guide/kernel-parameters.rst`, `include/linux/init.h`,
`moduleparam.h`, `Documentation/ABI/README`, `stable-kernel-rules.rst`,
`scripts/checkkconfigsymbols.py`; the Python docs for `argparse`,
`unittest.mock` ("Where to patch"), `random` and `multiprocessing`; pandas
"Working with missing data", `read_csv` (`na_values`) and the 2.0.0 release
notes; PEP 8 "Imports"; Django (`MIDDLEWARE`); Airflow (`catchup`). All
paraphrased, the code original; python-idiom.md section 8 is the PEP-level
version of the same claim. Read this at the prune (SKILL.md step 2), to
decide what a non-code hunk is; at the split (step 4), to order the switch
patches; at the maintainer pass (step 7), where section 6 extends the NAK
list.

## 1. The claim, and the kernel parallel

Behaviour is a function of five inputs: the code, the configuration, the
data, the call sequence, and the runtime environment. A diff shows only the
first, so review all five: the rest move with no logic line changing, in a
YAML value, a CSV row, a new import, a `default=`, a lockfile hash.

The kernel wrote rules for switches before it wrote them for code, because
one tree builds thousands of kernels and the `.config` decides which. A new
Kconfig symbol defaults to n — its value too when no `default` line is given
(kconfig-language.rst, "Menu attributes") — so an existing `.config` builds
the same kernel, `make oldconfig` asks rather than decides, and every symbol
carries help text. Every boot argument has one entry in one documented file,
and a `__setup()` handler without that entry is refused. Every module
parameter is declared beside the variable it sets. Initcall order is by
level then link order, the `obj-y` lines in the Makefiles, so moving one
reorders boot. None of that is about C: each rule exists so a reviewer can
answer "what does this change, and for whom" without running anything.

Two consequences. **The line that reads a switch is where behaviour is
decided, not the line that sets it**: follow the read to every setter and to
the default that applies when no setter runs; a read with no setter is a
switch stuck at its default, a setter with no reader is a dead switch. And
**a hunk in a non-code file can be the whole behaviour change**: sharing a
commit with code, it leaves nobody able to say whether the code is correct
for the old data and the new, or only the new — so config or data changed in
the same commit as code needs its own patch and its own test.

## 2. The inventory

| Kind | Examples | How a change hides there | What the series must do | The review question |
|---|---|---|---|---|
| **Config, env, flags** | `.yaml`/`.toml`/`.ini`/`.env`; `os.environ`; `--flags`; feature-flag services; `settings.py`; chains like `os.environ.get("X", cfg.get("x", DEFAULT))` | The key is a string, so the only cross-reference is `grep`; the file carries no test; `action="store_true"` implies `default=False`; the setter is a Makefile target, a cron line, or a CI step outside the Python diff; a typo in an env name makes the default permanent and silent | Key or flag lands with its **first reader**, default equal to today's behaviour, both positions tested by passing the value (an argument, a temp config file's path) and never by patching a module constant; the setter that flips it is its own patch (section 4); precedence is one expression in one place, written down; `--help` and the README line ride with the reader | "Who sets this, and what happens when nobody does?" |
| **Data and fixtures** | shipped CSV/parquet; fixtures; seeds; lookup and holiday tables; migrations; schemas, dtypes, `na_values`; model weights; golden files and snapshots | A row edited changes a join count or a category, and review tools fold data files; binaries diff as "Binary files differ"; a lookup table *is* the program, so a new row is a new branch; a fixture edited to make a failing test pass rewrote the specification; `read_csv` turns `NA` into NaN by default, so a ticker called `NA` vanishes from every count; retrained weights change every prediction with no code change | New data small enough to read lands with its reader; a change to **existing** data is its own patch saying what the old rows meant, what the new ones mean, and which output moves; sample-format changes dark-launch beside the old file; migrations expand and contract, one patch each, with row counts and the reversal; binary changes carry a text rendering (`head`, row counts, hash) and the producing command | "Which output changes because this row changed, and by how much?" |
| **Call sequence, import time** | entry points (`__main__`, `[project.scripts]`, Makefile, `CMD`, CI step, notebook cell); `CONFIG = load_config()` at module level; `logging.basicConfig()`; `@app.route`, `@receiver`, `@celery.task`; `__init__.py` import lists; middleware order; cron and DAG schedules; callbacks; retries | Module-level code runs on *any* import — a test, a linter, `python -c`, a worker — once, against the importer's working directory, then stays cached for the process; registration happens at import, so the registered set is the imported set and deleting an import unregisters a handler silently; `df.pipe(a).pipe(b)` and `.insert(0, x)` are behaviour with no logic line; `@lru_cache` on a config reader freezes the first call's config | Import-time effects move into a function called from the entry point, in a prep patch that is **not** "No functional change" (say which importers stop paying it); a patch that adds or removes a registering import lists the registered set before and after; an order change is its own patch naming what now runs before what; a schedule change is a flip (zone named, `catchup` stated, first run after deploy stated); a retry names its exception class and states idempotency | "What runs when this module is imported, and who imports it?" |
| **Defaults, silent fallbacks** | `strict=False`; `cfg.get(key, "%Y-%m-%d")`; `getattr(o, n, d)`; `value or default`; `fillna`, `errors="coerce"`, `dropna`, `skipna`; unnamed library defaults (`how="inner"`, `keep="first"`, `dayfirst=False`) | A default is the behaviour of every caller that does not pass it, which is most of them, so a one-word diff changes every call site without touching one; `.get` with a plausible fallback lets the config key stay dead forever; `or` treats `0`, `""`, `[]` and an empty frame as missing; a mean over a coerced column returns a believable number from a broken one; a library default moves when the pin moves, with no diff at all | A new parameter's default preserves old behaviour, or the patch is a flip and is written as one; changing a default is its own patch and the test that pinned the old value moves with it; one default per key, named, or the key is required and its absence raises; `is None` where zero is legal; name `on=`, `how=`, `validate=`, `format=`, `keep=`, `dropna=` rather than inheriting the library's choice | "Which callers do not pass this, and what do they get now?" |
| **Runtime environment** | library and Python versions, the lockfile; locale and encoding; zone and clock; seeds and hash randomisation; concurrency and start method; working directory; float order | pandas 2.0 infers one datetime format from the first element and raises on the rest where 1.x parsed row by row, so a lockfile hash carries a parser change; `datetime.now()` is the machine's zone (Hong Kong while the market is New York); `random.seed(0)` at module level seeds every user of `random` in the process; `fork` copies module state into workers and `spawn` re-imports, so an import-time effect runs once per worker; `open("config.yaml")` resolves against the working directory | Pin **before** you depend: a version bump is its own patch, before the patch that needs it, naming the changelog entry; inject the clock (`as_of=None` means today, said in the docstring); pass a generator (`default_rng(seed)`) instead of seeding globally; sort anything iterated from a set into an output; resolve paths against `__file__` or a passed root; `encoding=` on every text `open` | "On the machine that runs this: which zone, which version, which directory?" |
| **Dead switches** | a config section nothing reads; a variable read but never set; a parameter every caller passes at its default; a flag whose arms are identical; an `args.x` nobody reads; an alias kept as a comment | A dead switch is a promise with nothing behind it, and readers set it; when someone later makes it live, the value already sitting in the deployed config — put there months ago by someone who believed it worked — is the position the tree flips to | Remove it, in its own patch that says "No functional change" (checkable: grep finds no reader); or make it live, in its own patch that is **not** "No functional change"; never both, and never with the logic patch | "Who reads this key? Who sets this variable?" — the answer "nobody" is the finding |

## 3. Trace the chain, then write it into problem.md

For every added or changed line the detectors in section 7 flag: **name it**
(key path, variable, flag, parameter); **find every setter** by `grep -rn`
across code, config, tests, `.github/`, `Dockerfile`, `Makefile`, docs,
crontabs and DAG files, where "none" is an answer and the most important one;
**find the default** that applies when no setter runs, and any second default
for the same key written elsewhere; **find every reader**, since one key read
in two modules with two defaults is two switches with one name; **find when it
runs**, import or call time and from which entry points; **find the tested
positions**, and whether a test patches a module constant instead of passing
the value; **check history**, since `git log -S` on the key says when it
arrived and whether a reader ever existed. Then write it into `problem.md`:
the call chain, plus a switch table with columns *switch, kind, set by, read
by, default, before, after, tested positions, verdict*. The chain says which
switch is live at each patch; the table turns "the config changed" into a
decision per row.

### Worked example: one commit that moves five switches

`loader.load_prices()` leaves `date` as the string in the vendor CSV and
`transform.daily_returns()` pairs consecutive closes *in file order*;
`config.yaml` ships `input.path`, `input.date_format: "%Y-%m-%d"` and
`report.path`, and nothing reads it, so all three keys are dead. The user
asked for the configured format to be used, and for returns in date order
even when the file is not. The agent produced one 170-line commit, "feat:
configurable date parsing and chronological returns":

```
import pipeline.loader          runs on every import, tests included
  CONFIG = _load_config()       cwd-relative; FileNotFoundError swallowed -> {}
  DATE_FORMAT = env PIPELINE_DATE_FORMAT > config input.date_format
                > "%Y-%m-%d"    three sources, two defaults, frozen at import
main(argv)                      argparse --strict, default False, set by nobody
  -> load_prices(src, strict)   ValueError -> row dropped when strict is False
  -> summarise -> daily_returns now sorted by date            <- the fix
config.yaml input.date_format   "%Y-%m-%d" -> "%d/%m/%Y"      <- a flip, with code
data/sample.csv                 every row rewritten to d/m/Y  <- its evidence
tests                           mock.patch("pipeline.loader.DATE_FORMAT")
```

One logic line is the fix; the rest are switches. Where the maintainer
stops: the flip shares a commit with the code that makes it possible, so the
tests prove the new code against the new data and nothing proves it against
the old; and a `ValueError` becomes a missing row, so `n_rows` and every
mean fall silently.

A maintainer takes two patches, about 90 lines, tests passing at each. **1/2
`loader: parse the date column with the format from config.yaml`**:
`load_prices(path, date_format=None)`, `None` meaning `input.date_format`
read **on each call** by a twelve-line `configured_date_format()` beside it
that raises `KeyError` when the key is absent — one default, in one place.
An unparseable date raises. Tests pass the format, and read a temporary
config file for the default; none patches the module. The message says this
makes a dead key live, that the shipped value matches the shipped sample so
the report is unchanged, and that a checkout which edited the key sees that
edit take effect. **2/2 `transform: compute returns in date order, not file
order`**: sort each ticker's rows by date before pairing, saying the patch
depends on 1/2. The rest goes to `deferred.md` one line each, the flip and
sample rewrite drafted there behind one open question: did the vendor change
format, and when?

## 4. Dark launch: the order for any patch that flips a switch

**When it applies:** when the change alters behaviour that existing callers
already run, and someone depends on the old behaviour. A new capability
that no existing caller reaches lands without a guard, since nothing is
switched on until its first caller arrives. A bug fix gets none either. A
guard nobody needs is a knob nobody asked for, and it doubles the tests.

A feature that defaults to n is reviewed for what it *adds*, because it
changes nothing. Five patches at most, in this order:

1. **Prepare.** Moves and extractions so the new path is a small addition
   next to the old one. "No functional change."
2. **Add the new path, guarded off.** The switch lands *with its first
   reader*, its default is today's behaviour, tests exercise both positions
   by passing the value, and the message names the callers whose output does
   not change.
3. **Flip.** Change the default, the config value, or the data the reader
   points at, and nothing else; the test that pins the default moves with it.
4. **Migrate remaining callers** that still pass the old position on
   purpose, one patch each, saying why that caller no longer wants it.
5. **Remove the old path and the switch** — in a *later series*, after a
   release or a run has confirmed the new default.

Each rule prevents a failure. Never flip in the patch that adds the path: a
bisect landing there cannot tell a wrong path from a wrong default. Never
remove in the series that flips: the revert would restore nothing. Never add
a switch without its reader, or a reader without naming its setter. Never
test one position; the untested one is what production picks. A knob nobody
asked for is removed instead, and a bug fix gets none at all, because the
wrong number has no user.

The flip's diff is nearly empty and its consequence the largest, so its
message carries the whole review — old behaviour, new behaviour and who sees
it, why now, how to roll back — then the number to watch, and a `Cc:` per
affected job:

```
config: parse the vendor file as day/month/year

Old behaviour: input.date_format is "%Y-%m-%d"; the shipped sample and
the nightly summary job (cron, 06:00 HKT) parse the file that way.

New behaviour: input.date_format is "%d/%m/%Y". Every caller of
load_prices() that does not pass date_format -- main() and the nightly
job -- now parses day-first; callers that pass it are unaffected.
data/sample.csv is rewritten in the new format so the README example and
the acceptance check keep running; its rows and closes are unchanged.

Why now: the vendor's export switched on 1 June (VEND-212); the 2 June
run failed with ValueError on the first row.

Rollback: revert this patch; no data or cache depends on the format,
and tests/test_loader.py covers both positions explicitly. Watch n_rows
on the first nightly run: it should match the vendor's row count.

Cc: <owner of the nightly job>
Signed-off-by: Dana Dev <dana@example.com>
```

A flip with no "why now" waits; the standard is stable-kernel-rules.rst's
for a backport, a real problem for real users. If rollback needs more than a
revert (a data reload, a cache purge, a re-run), list the steps; if it
cannot be rolled back at all, it is a removal, not a flip.

## 5. Dead switches, and how to spot them

Before pruning, grep for the readers of every key in every config file the
diff touches, and for the setters of every variable it reads, across the
tree, CI, Dockerfile and docs. Name the dead ones in `problem.md` ("dead:
`input.path`, `report.path`"), so the reviewer knows what "config" means
here; `scripts/checkkconfigsymbols.py` is the kernel's version of that grep.
Spot them by: a key absent from `git grep`; an env var read in one line and
set in no file; a parameter whose every caller passes the default; two arms
of a flag that are textually identical; `args.x` that nothing reads after
`parse_args()`; an alias kept as a comment (`git log -S` finds it).

The trap: making a dead key live is a flip whose old position is "ignored"
and whose new is "whatever the file says". Its patch is **not** "No
functional change", and its message says the key has been in the file and
ignored since commit X; that from here it is read; that its shipped value
matches the old behaviour, so the shipped tree sees no change; and that a
tree which edited the key will now see that edit take effect.

## 6. The review questions for the maintainer pass

An extension of the NAK list in review-checklist.md: the objection, then the
fix demanded.

- "Config or data changed in the same commit as code." Own patch for the
  switch, old and new behaviour in its message, the test that pins the
  position with it; the code patch then proves itself against the old data.
- "Switch and reader in different patches." Together, or it is dead between.
  "You flipped the default in the patch that added the path." Split. "You
  removed the old path in the series that flips it." Later series; the
  revert has to restore something. "Only one position is tested." Test both,
  by passing the value. "The flip message lacks old or new behaviour, why
  now, or rollback." Add it. "A knob nobody asked for." Out.
- "Who sets this, and what happens when nobody does?" Say it in the message.
  "Three sources for one value." One expression, one place, order written
  down. "A second default for this key, in code." One default, named, or the
  key is required and its absence raises.
- "This row changes an output." Name the output, old and new value. "This
  fixture edit makes the failing test pass." Say why the old fixture was
  wrong, or restore it. "Snapshot updated with the code that changed it."
  Own patch, saying why each value is now right.
- "This runs at import." Move it into a function called from the entry
  point, and name the importers that stop seeing the effect. "This import
  registers or unregisters something." List the registered set before and
  after. "Hook, middleware, or pipe order changed." Own patch; what now runs
  before what, and who sees it; test the effect, not the list. "Schedule
  changed with the logic." Own patch, zone named, `catchup` and first-run
  behaviour stated. "Retry on `Exception`." Name it, state idempotency.
- "`or` on a value that can be zero or empty." `is None`. "Empty input
  yields `0.0`." `None` or NaN, or name the consumer who wants zero. "An
  unnamed library default." Name `on=`, `how=`, `format=`, `keep=`.
- "Nothing reads this key." Remove it (No functional change) or read it (not
  No functional change; say what the shipped value does). "Nothing sets it."
  Setter in the series, or drop the read.

## 7. The detectors

Run over added lines, per file: `SWITCH_SMELLS` on non-test files,
`TEST_SMELLS` on test files. `../scripts/switch_detectors.py` holds this list
compiled and importable with no side effects. A hit starts the trace in
section 3 and is never a verdict. Four decide a NAK alone: `module-level
call` outside `__init__.py`, `test fakes a module constant`, `dict.get with
a non-None default` on a config-ish name, `date parsed with an inferred
format`.

```python
SWITCH_SMELLS = [  # (name, regex), over ADDED lines of non-test files
    ("env var read", r"\bos\.environ\b|\bos\.getenv\(|\bgetenv\(|\bload_dotenv\(|\bdotenv_values\("),
    ("config or settings read", r"\b(config|settings|cfg|conf|CONFIG|SETTINGS|CFG)\b(?:\[[^\]]+\]|\.\w+)*\.get\(|\b(config|settings|cfg|conf|CONFIG|SETTINGS|CFG)\["),
    ("feature flag check", r"\b(feature_flags?|is_enabled|flag_enabled|FLAGS?\.|flags\.|\.enabled\b|waffle\.|ldclient\.|unleash\.)"),
    ("settings module import", r"^\s*(from\s+[\w.]*settings\s+import|import\s+[\w.]*settings\b|from\s+django\.conf\s+import\s+settings)|\bBaseSettings\b"),
    ("CLI flag with a default", r"add_argument\(.*\b(default\s*=|action\s*=\s*[\"']store_(true|false)[\"'])"),
    ("function parameter default", r"^\s*(async\s+)?def\s+\w+\s*\(.*?\b\w+\s*(?::\s*[^=,)]+)?=\s*[^,)\s]"),
    ("dataclass or model field default", r"\b(field|Field)\(.*?\bdefault(_factory)?\s*="),
    # lookaheads sit before the whitespace, or `\s*` backtracks around them; `counts.get(k, 0) + 1` is a counter
    ("dict.get with a non-None default", r"\.get\(\s*[^,()]+,(?!\s*None\s*\))(?!\s*(?:0|\[\])\s*\)\s*\+)\s*[^)\s]"),
    ("getattr with a fallback", r"\bgetattr\(\s*[^,()]+,\s*[^,()]+,\s*[^)]"),
    ("silent fallback with or", r"=\s*[\w.\[\]\"']+\s+or\s+[\"'\d\w{\[(]"),
    ("None or NaN coerced silently", r"\.fillna\(|\.dropna\(|errors\s*=\s*[\"'](coerce|ignore)[\"']|\bnan_to_num\(|\bor\s+0(\.0)?\b|\bor\s+[\"']{2}"),
    ("module-level call", r"^(?!(?:def|class|if|elif|else|for|while|with|try|except|finally|return|import|from|assert|raise|del|pass|yield|global|nonlocal|lambda|async|await|match|case)\b)(?:[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*(?:\s*:\s*[^=\n]+?)?\s*=\s*)?[A-Za-z_][\w.]*\s*\("),
    ("decorator with arguments", r"^\s*@[\w.]+\("),
    ("registration or scheduling decorator", r"^\s*@[\w.]*\b(route|register|receiver|task|command|hook|listener|subscribe|on_\w+|signal|schedule|periodic|cron|middleware|autouse|fixture|before_\w+|after_\w+|handler|validator|event|listens_for)\b"),
    ("cache or memoise decorator", r"^\s*@(functools\.)?(lru_cache|cache|cached_property)\b|\bst\.cache_data\b|\bjoblib\.Memory\("),
    ("retry, backoff, or sleep", r"^\s*@[\w.]*\bretry\b|\b(max_retries|retries|backoff|retry_on|stop_after_attempt|stop_after_delay|wait_exponential|wait_fixed)\b|\btime\.sleep\("),
    ("scheduler or cron registration", r"\b(crontab|schedule\.every|periodic_task|add_job|BackgroundScheduler|CronTrigger|IntervalTrigger|beat_schedule|Timer)\("),
    ("callback or signal hook registration", r"\b(atexit\.register|signal\.signal|sys\.excepthook|sys\.settrace|add_callback|register_hook|add_hook|add_done_callback)\b|\.(connect|subscribe|on)\("),
    ("middleware, app, or plugin list edit", r"\b(MIDDLEWARE|middleware|INSTALLED_APPS|hooks|HOOKS|plugins|PLUGINS)\s*(=|\+=|\.append\(|\.insert\(|\.extend\()"),
    ("global state mutated at runtime", r"^\s*global\s+\w|\bsys\.path\.(insert|append)\(|\bos\.chdir\(|\bpd\.set_option\(|\bnp\.seterr\(|\bwarnings\.(filterwarnings|simplefilter)\(|\blogging\.basicConfig\(|\bsettings\.configure\(|\blocale\.setlocale\(|\bos\.environ\[[^\]]+\]\s*="),
    ("dynamic import or reload", r"\b(importlib\.import_module|importlib\.reload|__import__|pkgutil\.iter_modules|entry_points|load_entry_point)\("),
    ("wall clock read", r"\b(datetime\.(now|utcnow|today)|date\.today|time\.time|time\.monotonic|Timestamp\.(now|today))\s*\(|np\.datetime64\(\s*[\"']now[\"']"),
    ("timezone, locale, or date-format dependence", r"\b(tzinfo\s*=|tz\s*=|tz_localize\(|tz_convert\(|astimezone\(|ZoneInfo\(|pytz\.|utc\s*=\s*(True|False)|locale\.|LC_ALL|LANG\b|strftime\(|strptime\()"),
    ("date parsed with an inferred format", r"\bto_datetime\((?![^)]*\bformat\s*=)|\bdateutil\.parser\.parse\(|\bparser\.parse\(|\bdateparser\."),
    ("randomness without a fixed seed", r"\brandom\.(random|randint|choice|choices|shuffle|sample|uniform|gauss)\(|\bnp\.random\.(rand|randn|randint|choice|shuffle|permutation|normal|uniform)\(|\bdefault_rng\(\s*\)|\brandom_state\s*=\s*None|\bshuffle\s*=\s*True"),
    ("seed set as a global side effect", r"\b(random\.seed|np\.random\.seed|manual_seed|set_seed|seed_everything)\(|PYTHONHASHSEED"),
    ("concurrency primitive or worker count", r"\b(ThreadPoolExecutor|ProcessPoolExecutor|multiprocessing\.(Pool|Process|set_start_method|get_context)|threading\.Thread|asyncio\.(gather|create_task|run)|joblib\.Parallel)\b|\b(n_jobs|num_workers|max_workers)\s*="),
    ("version or platform gate", r"\bsys\.version_info\b|\bsys\.platform\b|\bplatform\.(system|machine|python_version)\(|\b\w+\.__version__\b|\bversion\.parse\(|\bpackaging\.version\b"),
    ("f-string in a logging call", r"\b(logger|logging|log|LOG|LOGGER|_log|_logger)\.(debug|info|warning|warn|error|exception|critical)\(\s*f[\"']"),
]
TEST_SMELLS = [  # test files only
    ("test patches an attribute or environment", r"\b(mock\.)?patch(\.object|\.dict)?\(\s*[\"']?[\w.]+|\bmonkeypatch\.(setattr|setenv|setitem|delenv)\("),
    ("test fakes a module constant", r"patch(\.object)?\(\s*[\"'][\w.]*\.[A-Z][A-Z0-9_]+[\"']|patch\.object\(\s*[\w.]+,\s*[\"'][A-Z][A-Z0-9_]+[\"']|monkeypatch\.setattr\(\s*(?:[\w.]+,\s*)?[\"'][\w.]*\b[A-Z][A-Z0-9_]+[\"']"),
    ("test asserts calls, not results", r"\.assert_(called|not_called|called_once|called_with|called_once_with|any_call|has_calls|awaited\w*)\b|\.call_count\b|\.call_args(_list)?\b|\.mock_calls\b"),
    ("test sets an environment variable", r"\bmonkeypatch\.setenv\(|patch\.dict\(\s*(os\.environ|[\"']os\.environ[\"'])|\bos\.environ\[[^\]]+\]\s*=|\bos\.putenv\("),
]
```

A strict superset of the lists in `scripts/audit_diff.py`, which carries six
configuration and default patterns and one merged test smell. The audit
reports none of the rest, so you check it yourself: every call-sequence and
runtime-environment detector, settings imports, `getattr` fallbacks, the
non-`None` distinction on `dict.get`, silent NaN coercion, and the test
smell split into patching, faking a constant, asserting calls, and setting
the environment. Its AST pass does catch module-level statements on changed
lines; that is the one overlap.

No line regex expresses the commit-level rule; `audit_diff.py` applies it to
every commit's file list and prints it in the commits table: **config or data
changed in the same commit as code is a behaviour change that needs its own
patch and its own test.**
Non-test `.py` on one side; on the other `.yaml`, `.toml`, `.ini`, `.cfg`,
`.json`, `.env`, `.csv`, `.parquet`, `.pkl`, `.npy`, `.sql`, `.xlsx`, or
anything under `fixtures/`, `seeds/`, `data/`, `migrations/`, `schemas/`,
`weights/`, `golden/`, `snapshots/`. Both non-empty is the finding; a commit
that is only config and data is a flip patch and passes, as does a fixture
beside the behaviour it pins. One exception: a line that names code (an entry
point in `pyproject.toml`, a CI path) moves with the code it names.

A path constant computed from `__file__` (`CONFIG_PATH = Path(__file__)
.resolve().parent.parent / "config.yaml"`) names a file and reads nothing: it
is not an import-time effect, and the audit does not report it. Reading that
file at module level is.
