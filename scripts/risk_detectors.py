"""Risk detectors for review-ready: what a clean-looking line can still get wrong.

switch_detectors.py finds the things that change behaviour without being
logic. This module finds the lines that ARE logic and are wrong in a way
style cannot show: a query built from a string, a frame grown inside a loop,
a feature computed from the future, a lock nobody takes.

Same contract as switch_detectors: each entry is (name, regex, group), each
carries one line it must match and one it must not in CASES, and running
this file checks all of them. These are triage over lines, never verdicts.
A regex sees one line: it cannot see that the value inside the braces came
from a constant, and it cannot see a query assembled three lines up. A hit
is where the human trace from input to sink begins.

Groups, and the reference that says what to do about a hit:
  security     security.md
  data         data-correctness.md  (pandas, missing data, time, leakage, numeric)
  perf         algorithm-smells.md
  concurrency  engineering-sops.md
  testing      testing.md           (test files only)

audit_diff.py runs these over the added lines of a branch; repo_map.py runs
them over a whole tree.
"""
import re

RISK_DETECTORS = [
    # --- security: security.md. Names match the ones that file uses. A line
    # detector finds a sink and never a source: each hit is where the trace begins.
    ("SQL interpolated at the call",
     r"\b(execute|executemany|executescript|read_sql|read_sql_query|read_sql_table)\(\s*"
     r"(f[\"']|[\"'][^\"']*[\"']\s*(%|\+|\.format\()|[A-Za-z_]\w*\s*(\+|%)\s)", "security"),
    ("SQL text carrying a format field",
     r"(?i)\bf[\"'][^\"']*\b(SELECT|INSERT|UPDATE|DELETE)\b[^\"']*\{"
     r"|[\"'][^\"']*\b(SELECT|INSERT|UPDATE|DELETE)\b[^\"']*[\"']\s*(%\s|\.format\()", "security"),
    ("shell=True or a command string",
     r"\bshell\s*=\s*True\b|\bos\.(system|popen)\("
     r"|\bsubprocess\.(run|call|check_output|check_call|Popen)\(\s*f?[\"']", "security"),
    ("pickle-family load",
     r"\b(pickle|cPickle|dill|joblib|torch)\.loads?\(|\bpd\.read_pickle\(|\bshelve\.open\("
     r"|\bnp\.load\([^)]*allow_pickle\s*=\s*True", "security"),
    ("yaml loaded without a safe loader",
     r"\byaml\.(unsafe_load|full_load)\(|\byaml\.load\((?![^)]*Loader\s*=\s*(yaml\.)?C?SafeLoader)", "security"),
    ("eval or exec",
     r"(?<![\w.])(eval|exec)\(", "security"),
    ("expression string built by interpolation",
     r"\.(query|eval)\(\s*f[\"']|\bpd\.eval\(\s*f[\"']|\.(query|eval)\(\s*[\"'][^\"']*[\"']\s*(%\s|\.format\()",
     "security"),
    ("TLS verification disabled",
     r"\bverify\s*=\s*False\b|_create_unverified_context|CERT_NONE", "security"),
    ("HTTP call with no timeout",
     r"\b(requests\.(get|post|put|delete|head|patch|request)|urlopen|httpx\.(get|post))\((?![^)]*\btimeout\s*=)",
     "security"),
    ("secret-looking literal",
     r"(?i)\b\w*(api[_-]?key|secret|token|passwd|password|private[_-]?key)"
     r"(?!\w*_(url|uri|name|path|file|dir|env|var|header|field|type|prefix|re|regex|pattern|id)\b)\w*"
     r"[\"']?\s*[:=]\s*[\"'](?!https?://)[^\"'\s]{8,}[\"']",
     "security"),
    ("secret default in an env read",
     r"(?i)(environ\.get|getenv)\(\s*[\"'][A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*[\"']\s*,\s*[\"'][^\"']{6,}[\"']",
     "security"),
    ("secret-bearing value in a log call",
     r"(?i)\b(log|logger|logging|print)\b[^#\n]*\((?=[^#\n]*\b(token|secret|password|passwd|api[_-]?key|authorization|cookie)\b)",
     "security"),
    ("private key or known token prefix",
     r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
     r"|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})", "security"),
    ("path joined with a value from data",
     r"\bos\.path\.join\([^)]*\b(row|rec|record|entry|item|req|request|payload|params|args)\s*[\[.]"
     r"|/\s*(row|rec|record|entry|item|req|request|payload)\s*\[", "security"),
    ("predictable temp path",
     r"\btempfile\.mktemp\(|[\"']/tmp/[^\"']+[\"']", "security"),
    ("world-writable mode",
     r"\b0o?777\b|\b0o?666\b", "security"),
    ("package source changed",
     r"--(extra-)?index-url\b|--trusted-host\b|--find-links\b|\bgit\+(https?|ssh)://|^\s*-f\s+\S", "security"),
    ("archive extracted without a member check",
     r"\.extractall\(", "security"),

    # --- data: data-correctness.md. Names match the bracketed tags in that file.
    # section 2: point-in-time and look-ahead
    ("backward fill or interpolation",
     r"\.(bfill|backfill)\(|method\s*=\s*[\"'](bfill|backfill)[\"']|\.interpolate\(", "data"),
    ("negative shift or centred window",
     r"\.(shift|diff|pct_change)\(\s*-\s*\d|\bperiods\s*=\s*-\s*\d|\bcenter\s*=\s*True\b", "data"),
    ("window includes the current row",
     r"^(?!.*closed\s*=\s*[\"']left[\"'])(?!.*\.shift\().*\.(rolling|ewm|expanding)\(", "data"),
    ("statistic over the whole history",
     r"\.transform\(\s*[\"'](mean|std|median|max|min|sum|rank)[\"']|\.rank\([^)]*pct\s*=\s*True|\bqcut\("
     r"|-\s*[\w\[\]\"'.]+\.mean\(\)\s*\)\s*/\s*[\w\[\]\"'.]+\.std\(\)", "data"),
    ("fit call",
     r"\.fit(_transform)?\(", "data"),
    ("shuffled or random split",
     r"\btrain_test_split\((?![^)]*shuffle\s*=\s*False)|\b(KFold|StratifiedKFold|ShuffleSplit|cross_val_score|cross_validate)\(",
     "data"),
    ("as-of join with a default unnamed",
     r"\bmerge_asof\((?![^)]*allow_exact_matches)", "data"),
    ("row operation across the key",
     r"^(?!.*groupby).*\.(shift|diff|pct_change|ffill|cumsum|cumprod)\(", "data"),
    # section 4: time
    ("naive clock or local date",
     r"\bdate\.today\(\s*\)|\bdatetime\.(now|today)\(\s*\)|\.utcnow\(\s*\)|\bpd\.Timestamp\.(now|today)\(\s*\)",
     "data"),
    ("calendar date taken from a timestamp",
     r"\.dt\.date\b|\.dt\.normalize\(\)|\.dt\.floor\(\s*[\"']D[\"']", "data"),
    ("fixed-offset zone arithmetic",
     r"timedelta\(\s*hours\s*=\s*-?\s*(4|5|8|12|13)\s*\)", "data"),
    ("zone discarded",
     r"tz_(localize|convert)\(\s*None\s*\)|replace\(\s*tzinfo\s*=\s*None\s*\)", "data"),
    ("resample with closed or label unnamed",
     r"^(?!.*\bclosed\s*=)(?!.*\blabel\s*=).*\.resample\(", "data"),
    # section 5: missing data
    ("aggregation with skipna unnamed",
     r"(\]|\)|\bdf\w*|\bseries\w*|\bframe\w*|\bpx\w*|\brets?\w*)\.(mean|sum|std|var|median)\(\s*\)", "data"),
    ("equality against NaN",
     r"(==|!=)\s*(np\.nan|numpy\.nan|math\.nan|float\([\"']nan[\"']\)|pd\.NA|pd\.NaT)\b"
     r"|\b(np\.nan|math\.nan)\s*(==|!=)", "data"),
    ("groupby with dropna unnamed",
     r"\.(groupby|value_counts|nunique)\((?![^)]*dropna\s*=)", "data"),
    ("dropna with no subset",
     r"\.dropna\((?![^)]*subset\s*=)", "data"),
    ("forward fill with no limit",
     r"\.ffill\((?![^)]*limit\s*=)|method\s*=\s*[\"'](ffill|pad)[\"'](?![^)]*limit\s*=)", "data"),
    # section 6: the pandas object model
    ("chained assignment",
     r"\b(df|frame|data|px|prices|out|res|sub|\w+_df|\w+_frame)\w*\[[^\]]+\]\s*\[[^\]]+\]\s*=[^=]"
     r"|\.(loc|iloc)\[[^\]]*\]\s*\[[^\]]+\]\s*=[^=]", "data"),
    ("chained-assignment warning silenced",
     r"chained_assignment|SettingWithCopy", "data"),
    ("series assigned into a frame column",
     r"^\s*\w+\[[\"'][^\"']+[\"']\]\s*=\s*\w+(\[[\"'][^\"']+[\"']\])?\s*[-+*/]\s*\w+(\[[\"'][^\"']+[\"']\])?\s*$"
     r"|\bconcat\([^)]*axis\s*=\s*1", "data"),
    ("positional escape from alignment",
     r"\.values\b(?!\()|\.to_numpy\(\)", "data"),
    ("read with dtypes inferred",
     r"\bpd\.read_(csv|table|excel|json|fwf)\((?![^)]*\b(dtype|parse_dates|converters)\s*=)", "data"),
    ("key cast through str or float",
     r"\.astype\(\s*(str|float|[\"']str[\"']|[\"']float\d*[\"'])\s*\)", "data"),
    ("category dtype introduced",
     r"astype\(\s*[\"']category[\"']\s*\)|\bpd\.cut\(", "data"),
    ("inplace=True",
     r"\binplace\s*=\s*True\b", "data"),
    ("merge with no validate",
     r"\.merge\((?![^)]*\bvalidate\s*=)|\bpd\.merge\((?![^)]*\bvalidate\s*=)", "data"),
    ("row-wise iteration or apply",
     r"\.(iterrows|itertuples)\(\)|\.apply\([^)]*axis\s*=\s*1", "data"),
    ("sort with tie order unnamed",
     r"\.sort_values\((?![^)]*\bkind\s*=)", "data"),
    # section 7: numerical
    ("float equality",
     r"(==|!=)\s*-?\d+\.\d+\b|\b\d+\.\d+\s*(==|!=)", "data"),
    ("int of a true division",
     r"\bint\(\s*[\w.\[\]\"']+\s*/\s*[\w.\[\]\"']+\s*\)", "data"),
    ("round on a stored value",
     r"(?<![\w.])round\(|\.round\(", "data"),
    # section 8: sqlite3 as the store
    ("connection as context manager",
     r"\bwith\s+sqlite3\.connect\(", "data"),
    ("plain insert",
     r"(?i)\bINSERT\s+INTO\b(?!.*\bON\s+CONFLICT\b)", "data"),
    ("insert or ignore",
     r"(?i)\bINSERT\s+OR\s+IGNORE\b", "data"),
    ("stored row rewritten in place",
     r"(?i)\bINSERT\s+OR\s+REPLACE\b|\bREPLACE\s+INTO\b|\bDO\s+UPDATE\s+SET\b", "data"),
    ("DML statement",
     r"(?i)[\"'][^\"']*\b(DELETE\s+FROM|UPDATE\s+\w+\s+SET)\b", "data"),
    ("to_sql call",
     r"\.to_sql\(", "data"),
    ("schema statement",
     r"(?i)\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|VIEW|TRIGGER)\b", "data"),
    ("NOT IN or <> in SQL",   # case-sensitive: Python's `not in (` is lowercase, SQL's is not
     r"[\"'][^\"']*(\bNOT\s+IN\s*\(|<>)", "data"),

    # --- perf: algorithm-smells.md
    ("pop(0) or insert(0, x) on a list",
     r"\.pop\(\s*0\s*\)|\.insert\(\s*0\s*,", "perf"),
    ("membership test against a list literal or list()",
     r"\b(if|elif|while|and|or|not)\b[^#\n]*\bin\s+list\(|\bnot in\s+list\(", "perf"),
    ("recursion limit raised",
     r"\bsetrecursionlimit\(", "perf"),
    ("cache on a method",
     r"^\s{4,}@(functools\.)?(lru_cache|cache)\b", "perf"),
    ("sorted() to take the first or last few",
     r"\bsorted\([^)]*\)\s*\[\s*:?-?\d+\s*:?\s*\]|\.sort_values\([^)]*\)\.(head|tail)\(\s*\d+\s*\)", "perf"),

    # --- concurrency: engineering-sops.md
    ("module state mutated with global",
     r"^\s+global\s+\w", "concurrency"),
    ("future whose result nobody reads",
     r"^(?!\s*[\w\[\]., ]+\s*=(?!=))(?!.*(\.result\(|as_completed|append\(|\{)).*\.submit\(", "concurrency"),
    ("thread started without join or daemon decision",
     r"\bthreading\.Thread\((?![^)]*\bdaemon\s*=)", "concurrency"),

    # --- logging
    ("exception logged without its traceback",
     r"\b(log|logger|logging|LOG)\.(error|critical)\(\s*(e|err|exc|error|ex)\s*\)|"
     r"\b(log|logger|logging|LOG)\.(error|critical)\([^)]*(\bstr\((e|err|exc|error)\)|\{(e|err|exc|error)\}|,\s*(e|err|exc|error)\s*\))",
     "logging"),
]

# Applied to test files only.
TEST_RISK_DETECTORS = [
    ("test reads the operator's real config or data",
     r"\bload_config\(\s*\)|\bcfg\.db_path\b|[\"'](data|prod|production)/[\w./-]+\.(db|sqlite|parquet|csv)[\"']",
     "testing"),
    ("test opens a database by a fixed path",
     r"sqlite3\.connect\(\s*[\"'](?!:memory:)[^\"']+[\"']|\bcfg\.db_path\b", "testing"),
    ("test sleeps",
     r"\btime\.sleep\(", "testing"),
    ("test reaches the network",
     r"\b(requests\.(get|post)|urlopen|httpx\.(get|post))\(", "testing"),
    ("test skipped or expected to fail with no reason",
     r"@(pytest\.mark\.)?(skip|xfail)\s*(\(\s*\))?\s*$|@unittest\.skip\s*$", "testing"),
]

ALL_RISK_DETECTORS = RISK_DETECTORS + TEST_RISK_DETECTORS

# Detectors that only mean something in a file of a certain kind. A `.values`
# or a `round(` in a module that never touches a frame is not a finding, and
# reporting it teaches the reader to skip the section.
NEEDS_PANDAS = {
    "backward fill or interpolation", "negative shift or centred window", "window includes the current row",
    "statistic over the whole history", "as-of join with a default unnamed", "row operation across the key",
    "calendar date taken from a timestamp", "zone discarded", "resample with closed or label unnamed",
    "aggregation with skipna unnamed", "groupby with dropna unnamed", "dropna with no subset",
    "forward fill with no limit", "chained assignment", "series assigned into a frame column",
    "positional escape from alignment", "key cast through str or float", "category dtype introduced",
    "inplace=True", "merge with no validate", "row-wise iteration or apply", "sort with tie order unnamed",
    "round on a stored value", "int of a true division", "float equality",
}
# Secrets live where nobody reads code: fixtures, YAML, requirements, shell.
# These run over every added text line, tests and non-Python files included.
ANY_FILE = {"private key or known token prefix", "secret-looking literal", "package source changed",
            "TLS verification disabled"}

CREDENTIAL_FILE = re.compile(
    r"(^|/)(\.env(\.(?!example$|sample$|template$)[\w.-]+)?|\.netrc|\.pypirc|\.htpasswd|id_(rsa|dsa|ecdsa|ed25519)"
    r"|credentials(\.json)?|service[-_]account[\w.-]*\.json|secrets?\.(json|ya?ml|toml|ini|env))$"
    r"|\.(pem|key|p12|pfx|jks|keystore)$", re.IGNORECASE)


def credential_files(paths):
    """Paths whose name alone says credential. `.env.example` is a template and passes."""
    return [p for p in paths if CREDENTIAL_FILE.search(p)]


NEEDS_MODEL = {"fit call", "shuffled or random split"}
# Markers: lines where a question gets asked, not probable defects. On a diff
# they are bounded by the diff and worth listing. Over a whole tree they are an
# inventory, so repo_map reports them as counts per file: "CREATE TABLE in six
# modules" is the finding, not each of the twenty-four lines.
MARKERS = {
    "fit call", "round on a stored value", "to_sql call", "DML statement", "schema statement",
    "plain insert", "insert or ignore", "stored row rewritten in place", "connection as context manager",
    "aggregation with skipna unnamed", "groupby with dropna unnamed", "row operation across the key",
    "window includes the current row", "sort with tie order unnamed", "read with dtypes inferred",
}
USES_PANDAS = re.compile(r"\b(import pandas|from pandas|import numpy|from numpy|pd\.|np\.)")
USES_MODEL = re.compile(r"\b(sklearn|xgboost|lightgbm|statsmodels|torch|keras|fit_transform|train_test_split)\b")


def enabled(src_hint):
    """Names to skip for a file, given its full source (None = run everything)."""
    if src_hint is None:
        return set()
    skip = set()
    if not USES_PANDAS.search(src_hint):
        skip |= NEEDS_PANDAS
    if not USES_MODEL.search(src_hint):
        skip |= NEEDS_MODEL
    return skip

CASES = {
    "SQL interpolated at the call": (
        'conn.execute(f"SELECT * FROM prices WHERE ticker = \'{ticker}\'")',
        'conn.execute("SELECT * FROM prices WHERE ticker = ?", (ticker,))'),
    "SQL text carrying a format field": ('q = f"SELECT * FROM {table} WHERE day = ?"', 'q = "SELECT * FROM prices WHERE day = ?"'),
    "shell=True or a command string": ('subprocess.run(cmd, shell=True)', 'subprocess.run(["git", "log"], check=True)'),
    "pickle-family load": ('model = pickle.load(fh)', 'model = json.load(fh)'),
    "yaml loaded without a safe loader": ('cfg = yaml.load(fh)', 'cfg = yaml.load(fh, Loader=yaml.SafeLoader)'),
    "expression string built by interpolation": ('hits = df.query(f"ticker == \'{t}\'")', 'hits = df.query("ticker == @t")'),
    "HTTP call with no timeout": ('r = requests.get(url)', 'r = requests.get(url, timeout=10)'),
    "secret-looking literal": ('API_KEY = "sk-live-8f2a9c1d7e"', 'API_KEY = os.environ["API_KEY"]'),
    "secret default in an env read": ('key = os.environ.get("VENDOR_API_KEY", "d41d8cd98f00b204")', 'key = os.environ["VENDOR_API_KEY"]'),
    "secret-bearing value in a log call": ('log.info("calling vendor with token %s", token)', 'log.info("calling vendor %s", vendor)'),
    "private key or known token prefix": ('key = "ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4"', 'key = load_key()'),
    "path joined with a value from data": ('dest = os.path.join(root, row["filename"])', 'dest = os.path.join(root, "prices.csv")'),
    "predictable temp path": ('tmp = "/tmp/prices.csv"', 'fd, tmp = tempfile.mkstemp(dir=target.parent)'),
    "world-writable mode": ('os.chmod(path, 0o777)', 'os.chmod(path, 0o640)'),
    "package source changed": ('--extra-index-url https://pypi.vendor.example/simple', 'requests==2.31.0'),
    "eval or exec": ('value = eval(expr)', 'value = ast.literal_eval(expr)'),
    "TLS verification disabled": ('requests.get(url, verify=False, timeout=5)', 'requests.get(url, timeout=5)'),
    "archive extracted without a member check": ('tar.extractall(dest)', 'tar.extract(member, dest)'),
    "chained assignment": ('df[df.px > 0]["flag"] = 1', 'df.loc[df.px > 0, "flag"] = 1'),
    "inplace=True": ('df.dropna(inplace=True)', 'df = df.dropna()'),
    "merge with no validate": ('out = px.merge(meta, on="ticker", how="left")',
                               'out = px.merge(meta, on="ticker", how="left", validate="m:1")'),
    "negative shift or centred window": ('df["fwd"] = df["close"].shift(-1)', 'df["lag"] = df["close"].shift(1)'),
    "backward fill or interpolation": ('px = px.bfill()', 'px = px.ffill(limit=3)'),
    "window includes the current row": ('df["ma"] = df["close"].rolling(20).mean()',
                                        'df["ma"] = df["close"].rolling(20).mean().shift(1)'),
    "statistic over the whole history": ('df["z"] = df.groupby("ticker")["ret"].transform("mean")',
                                         'df["z"] = df.groupby("ticker")["ret"].transform(lag_mean)'),
    "fit call": ('scaler.fit(X)', 'scaler.transform(X_test)'),
    "shuffled or random split": ('a, b = train_test_split(X, y)', 'a, b = train_test_split(X, y, shuffle=False)'),
    "as-of join with a default unnamed": ('out = pd.merge_asof(news, bars, on="ts")',
                                          'out = pd.merge_asof(news, bars, on="ts", allow_exact_matches=False)'),
    "row operation across the key": ('df["ret"] = df["close"].pct_change()',
                                     'df["ret"] = df.groupby("ticker")["close"].pct_change()'),
    "naive clock or local date": ('today = date.today()', 'today = datetime.now(ET).date()'),
    "calendar date taken from a timestamp": ('df["day"] = df["ts"].dt.date', 'df["session"] = session_of(df["ts"])'),
    "fixed-offset zone arithmetic": ('et = ts + timedelta(hours=-4)', 'et = ts.astimezone(ET)'),
    "zone discarded": ('ts = ts.tz_localize(None)', 'ts = ts.tz_convert("America/New_York")'),
    "resample with closed or label unnamed": ('bars = px.resample("5min").last()',
                                              'bars = px.resample("5min", closed="right", label="right").last()'),
    "aggregation with skipna unnamed": ('m = df["ret"].mean()', 'm = df["ret"].mean(skipna=False)'),
    "equality against NaN": ('bad = df[df["px"] == np.nan]', 'bad = df[df["px"].isna()]'),
    "groupby with dropna unnamed": ('g = df.groupby("sector")', 'g = df.groupby("sector", dropna=False)'),
    "dropna with no subset": ('df = df.dropna()', 'df = df.dropna(subset=["close"])'),
    "forward fill with no limit": ('px = px.ffill()', 'px = px.ffill(limit=2)'),
    "chained-assignment warning silenced": ('pd.set_option("mode.chained_assignment", None)',
                                            'pd.set_option("display.width", 120)'),
    "series assigned into a frame column": ('df["spread"] = ask - bid', 'df["spread"] = (ask - bid).to_numpy()'),
    "positional escape from alignment": ('df["x"] = other["x"].values', 'vals = mapping.values()'),
    "read with dtypes inferred": ('df = pd.read_csv(path)', 'df = pd.read_csv(path, dtype={"ticker": "string"})'),
    "key cast through str or float": ('df["id"] = df["id"].astype(str)', 'df["id"] = df["id"].astype("Int64")'),
    "category dtype introduced": ('df["sector"] = df["sector"].astype("category")', 'df["sector"] = df["sector"].astype("string")'),
    "row-wise iteration or apply": ('for i, row in df.iterrows():', 'total = df["qty"].sum(skipna=False)'),
    "sort with tie order unnamed": ('df = df.sort_values("score")', 'df = df.sort_values("score", kind="stable")'),
    "float equality": ('if ret == 0.05:', 'if math.isclose(ret, 0.05):'),
    "int of a true division": ('bucket = int(price / tick)', 'bucket = price // tick'),
    "round on a stored value": ('row["px"] = round(px, 2)', 'row["px"] = Decimal(px).quantize(CENT)'),
    "connection as context manager": ('with sqlite3.connect(path) as conn:', 'with closing(sqlite3.connect(path)) as conn:'),
    "plain insert": ('conn.execute("INSERT INTO prices VALUES (?, ?)", row)',
                     'conn.execute("INSERT INTO prices VALUES (?, ?) ON CONFLICT (k) DO NOTHING", row)'),
    "insert or ignore": ('conn.execute("INSERT OR IGNORE INTO prices VALUES (?, ?)", row)', 'conn.execute("SELECT 1")'),
    "stored row rewritten in place": ('conn.execute("INSERT OR REPLACE INTO prices VALUES (?, ?)", row)',
                                      'conn.execute("SELECT * FROM prices")'),
    "DML statement": ('conn.execute("DELETE FROM prices WHERE day < ?", (cut,))', 'conn.execute("SELECT 1")'),
    "to_sql call": ('df.to_sql("prices", conn, if_exists="replace")', 'df.to_parquet(path)'),
    "schema statement": ('conn.execute("CREATE TABLE IF NOT EXISTS prices (k TEXT)")', 'conn.execute("SELECT 1")'),
    "NOT IN or <> in SQL": ('q = "SELECT * FROM t WHERE k NOT IN (SELECT k FROM u)"', 'q = "SELECT * FROM t WHERE k = ?"'),
    "test opens a database by a fixed path": ('conn = sqlite3.connect("data/newsbot.db")', 'conn = sqlite3.connect(":memory:")'),
    "pop(0) or insert(0, x) on a list": ('item = queue.pop(0)', 'item = queue.popleft()'),
    "membership test against a list literal or list()": ('if t in list(universe):', 'if t in universe_set:'),
    "recursion limit raised": ('sys.setrecursionlimit(100000)', 'stack = [root]'),
    "cache on a method": ('    @lru_cache(maxsize=None)', '@lru_cache(maxsize=None)'),
    "sorted() to take the first or last few": ('top = sorted(scores)[-3:]', 'top = heapq.nlargest(3, scores)'),
    "module state mutated with global": ('    global _cache', '_cache = {}'),
    "future whose result nobody reads": ('pool.submit(fetch, url)', 'futures = {pool.submit(fetch, u): u for u in urls}'),
    "thread started without join or daemon decision": ('t = threading.Thread(target=run)',
                                                       't = threading.Thread(target=run, daemon=True)'),
    "exception logged without its traceback": ('log.error("fetch failed: %s", err)', 'log.exception("fetch failed")'),
    "test reads the operator's real config or data": ('cfg = load_config()', 'cfg = Config(output_dir=tmp_path)'),
    "test sleeps": ('time.sleep(2)', 'clock.advance(2)'),
    "test reaches the network": ('r = requests.get(URL)', 'r = fake_http.get(URL)'),
    "test skipped or expected to fail with no reason": ('@pytest.mark.skip', '@pytest.mark.skip(reason="needs the vendor sandbox")'),
}


import ast


def docstring_lines(src):
    """Line numbers inside docstrings, so prose about date.today() is not code."""
    skip = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return skip
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            skip.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    return skip


def _dotted(func):
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr); func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _stringish(node):
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str)) \
        or (isinstance(node, ast.BinOp) and (_stringish(node.left) or _stringish(node.right))) \
        or (isinstance(node, ast.Call) and _dotted(node.func) in ("str", "repr", "format"))


BLOCKING = {"time.sleep", "requests.get", "requests.post", "requests.put", "requests.request",
            "urllib.request.urlopen", "urlopen", "subprocess.run", "subprocess.check_output"}


# Found by shape or by file name, not by a line regex. Listed so that a
# reference naming one of them can be checked against this module.
OTHER_DETECTORS = {
    "commit inside a loop", "string built with += inside a loop", "frame grown in a loop",
    "blocking call inside async def", "coroutine called and never awaited", "return inside finally",
    "mutable default argument", "__eq__ without __hash__", "generator returned from inside with",
    "contextmanager cleanup outside finally", "credential file committed",
}


def ast_risks(src):
    """Findings that depend on where a line sits: inside a loop, inside an
    async def, inside a finally. A regex cannot know; the AST can."""
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    lines = src.splitlines()

    def text(n):
        return lines[n.lineno - 1].strip()[:100] if 0 < n.lineno <= len(lines) else ""

    async_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}

    def visit(node, in_loop, in_async, awaited):
        for child in ast.iter_child_nodes(node):
            loop = in_loop or isinstance(child, (ast.For, ast.While, ast.AsyncFor))
            asyn = in_async
            if isinstance(child, ast.AsyncFunctionDef):
                asyn, loop = True, False
            elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                asyn, loop = False, False
            if isinstance(child, ast.Call):
                name = _dotted(child.func)
                if in_async and name in BLOCKING:
                    out.append((child.lineno, "blocking call inside async def", "concurrency", text(child)))
                if in_loop and name.endswith(".commit"):
                    out.append((child.lineno, "commit inside a loop", "data", text(child)))
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call) \
                    and _dotted(child.value.func).split(".")[-1] in async_names:
                out.append((child.lineno, "coroutine called and never awaited", "concurrency", text(child)))
            if in_loop and isinstance(child, ast.AugAssign) and isinstance(child.op, ast.Add) \
                    and _stringish(child.value):
                out.append((child.lineno, "string built with += inside a loop", "perf", text(child)))
            if in_loop and isinstance(child, ast.Assign) and isinstance(child.value, ast.Call):
                name = _dotted(child.value.func)
                targets = {t.id for t in child.targets if isinstance(t, ast.Name)}
                used = {n.id for n in ast.walk(child.value) if isinstance(n, ast.Name)}
                if (name.endswith("concat") or name.endswith(".append")) and targets & used:
                    out.append((child.lineno, "frame grown in a loop", "data", text(child)))
            if isinstance(child, ast.Try):
                for stmt in child.finalbody:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Return):
                            out.append((sub.lineno, "return inside finally", "data", text(sub)))
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in child.args.defaults + [d for d in child.args.kw_defaults if d is not None]:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)) or (
                            isinstance(default, ast.Call) and _dotted(default.func) in ("list", "dict", "set")):
                        out.append((child.lineno, "mutable default argument", "data", text(child)))
                        break
            visit(child, loop, asyn, awaited)

    visit(tree, False, False, False)
    out.extend(_structural(tree, text))
    return sorted(set(out))


def _is_generator(fn):
    """True when fn yields in its own body (nested defs do not count)."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))
    return False


LAZY_CALLS = {"map", "filter", "zip", "reader", "DictReader", "iterdump", "iterrows", "itertuples", "islice"}


def _structural(tree, text):
    """Findings that are a shape, not a line: a class, a with block, a decorator."""
    out = []
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    generators = {f.name for f in funcs if _is_generator(f)}

    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        defined = {b.name for b in cls.body if isinstance(b, ast.FunctionDef)} | {
            t.id for b in cls.body if isinstance(b, ast.Assign) for t in b.targets if isinstance(t, ast.Name)}
        if "__eq__" in defined and "__hash__" not in defined:
            out.append((cls.lineno, "__eq__ without __hash__", "data", text(cls)))

    def lazy(value, bound):
        if value is None or not bound & {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}:
            return False
        if isinstance(value, ast.GeneratorExp):
            return True
        return isinstance(value, ast.Call) and _dotted(value.func).split(".")[-1] in generators | LAZY_CALLS

    def returns_in(block, bound):
        for stmt in block:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.Return) and lazy(stmt.value, bound):
                out.append((stmt.lineno, "generator returned from inside with", "data", text(stmt)))
            for field in ("body", "orelse", "finalbody"):
                returns_in(getattr(stmt, field, []) or [], bound)
            for h in getattr(stmt, "handlers", []) or []:
                returns_in(h.body, bound)

    for w in (n for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))):
        bound = {n.id for item in w.items if item.optional_vars is not None
                 for n in ast.walk(item.optional_vars) if isinstance(n, ast.Name)}
        if bound:
            returns_in(w.body, bound)

    def bare_yield(block, guarded):
        """The yield statement with cleanup after it and no finally or with around it."""
        for i, stmt in enumerate(block):
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            is_yield = isinstance(stmt, (ast.Expr, ast.Assign)) and isinstance(stmt.value, ast.Yield)
            if is_yield and not guarded and block[i + 1:]:
                return stmt
            if isinstance(stmt, ast.Try):
                hit = bare_yield(stmt.body, guarded or bool(stmt.finalbody)) or bare_yield(stmt.orelse, guarded)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                hit = bare_yield(stmt.body, True)
            else:
                hit = None
                for field in ("body", "orelse"):
                    hit = hit or bare_yield(getattr(stmt, field, []) or [], guarded)
            if hit:
                return hit
        return None

    for fn in funcs:
        if any(_dotted(d.func if isinstance(d, ast.Call) else d).split(".")[-1]
               in ("contextmanager", "asynccontextmanager") for d in fn.decorator_list):
            stmt = bare_yield(fn.body, False)
            if stmt:
                out.append((stmt.lineno, "contextmanager cleanup outside finally", "data", text(stmt)))
    return out


def scan_source(src, is_test=False):
    """Regex detectors over code lines only, plus the AST checks."""
    skip = docstring_lines(src)
    lines = [(i, t) for i, t in enumerate(src.splitlines(), 1) if i not in skip]
    found = scan(lines, is_test, src_hint=src)
    if not is_test:
        found += ast_risks(src)
    return sorted(found)


def scan(lines, is_test=False, src_hint=None):
    """[(lineno, name, group, text)] for (lineno, text) pairs. src_hint is the
    file's full source, used to switch off detectors that need pandas or a
    model library when the file has neither."""
    table = (TEST_RISK_DETECTORS + [d for d in RISK_DETECTORS if d[0] in ANY_FILE]) if is_test \
        else RISK_DETECTORS
    off = enabled(src_hint)
    compiled = [(n, re.compile(rx), g) for n, rx, g in table if n not in off]
    out = []
    for lineno, text in lines:
        stripped = text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for name, rx, group in compiled:
            if rx.search(text):
                out.append((lineno, name, group, stripped[:100]))
    return out


def scan_text(lines):
    """[(lineno, name, group, text)] for a file that is not Python: only the
    detectors whose finding does not depend on the language."""
    compiled = [(n, re.compile(rx), g) for n, rx, g in RISK_DETECTORS if n in ANY_FILE]
    return [(lineno, name, group, text.strip()[:100])
            for lineno, text in lines for name, rx, group in compiled if rx.search(text)]


def main():
    failed = 0
    for name, rx, _group in ALL_RISK_DETECTORS:
        pos, neg = CASES[name]
        r = re.compile(rx)
        ok = bool(r.search(pos)) and not r.search(neg)
        failed += not ok
        print(("ok   " if ok else "FAIL ") + name)
    assert set(CASES) == {n for n, _, _ in ALL_RISK_DETECTORS}, "every detector needs a case"
    ast_cases = [
        ("commit inside a loop", "def f(conn, rows):\n    for r in rows:\n        conn.execute('x')\n        conn.commit()\n"),
        (None, "def f(conn, rows):\n    for r in rows:\n        conn.execute('x')\n    conn.commit()\n"),
        ("string built with += inside a loop", "def f(rows):\n    out = ''\n    for r in rows:\n        out += f'{r},'\n    return out\n"),
        (None, "def f(where):\n    sql = 'SELECT 1'\n    sql += ' WHERE ' + ' AND '.join(where)\n    return sql\n"),
        ("frame grown in a loop", "def f(chunks):\n    out = None\n    for c in chunks:\n        out = pd.concat([out, c])\n    return out\n"),
        (None, "def f(chunks):\n    return pd.concat(chunks)\n"),
        ("blocking call inside async def", "import time\nasync def f():\n    time.sleep(1)\n"),
        (None, "import time\ndef f():\n    time.sleep(1)\n"),
        ("coroutine called and never awaited", "async def save(x):\n    pass\nasync def f():\n    save(1)\n"),
        (None, "async def save(x):\n    pass\nasync def f():\n    await save(1)\n"),
        ("return inside finally", "def f():\n    try:\n        return 1\n    finally:\n        return 2\n"),
        ("mutable default argument", "def f(seen=[]):\n    return seen\n"),
        (None, "def f(seen=None):\n    return seen or []\n"),
    ]
    ast_cases += [
        ("__eq__ without __hash__", "class K:\n    def __eq__(self, o):\n        return True\n"),
        (None, "class K:\n    def __eq__(self, o):\n        return True\n    def __hash__(self):\n        return 1\n"),
        (None, "class K:\n    __hash__ = None\n    def __eq__(self, o):\n        return True\n"),
        ("generator returned from inside with",
         "def rows(p):\n    with open(p) as fh:\n        return (l.strip() for l in fh)\n"),
        ("generator returned from inside with",
         "def parse(fh):\n    for l in fh:\n        yield l\ndef rows(p):\n    with open(p) as fh:\n        return parse(fh)\n"),
        (None, "def rows(p):\n    with open(p) as fh:\n        return [l.strip() for l in fh]\n"),
        (None, "def rows(p, lock):\n    with lock:\n        return (x for x in p)\n"),
        ("contextmanager cleanup outside finally",
         "from contextlib import contextmanager\n@contextmanager\ndef run(db):\n    db.start()\n    yield db\n    db.finish()\n"),
        (None, "from contextlib import contextmanager\n@contextmanager\ndef run(db):\n    db.start()\n"
               "    try:\n        yield db\n    finally:\n        db.finish()\n"),
        (None, "from contextlib import contextmanager\n@contextmanager\ndef run(p):\n    with open(p) as fh:\n        yield fh\n"),
        (None, "from contextlib import contextmanager\n@contextmanager\ndef run(db):\n    db.start()\n    yield db\n"),
    ]
    for want, src in ast_cases:
        kinds = {k for _l, k, _g, _t in ast_risks(src)}
        ok = (want in kinds) if want else not kinds
        failed += not ok
        print(("ok   " if ok else "FAIL ") + "ast: " + (want or "clean") + ("" if ok else f" got={kinds}"))
    doc = 'def f():\n    """date.today() rolls over at noon."""\n    return 1\n'
    ok = not [k for _l, k, _g, _t in scan_source(doc)]
    failed += not ok
    print(("ok   " if ok else "FAIL ") + "docstring prose is not code")
    got = credential_files([".env", "deploy/.env.prod", ".env.example", "keys/vendor.pem", "src/app.py",
                            "config/secrets.yaml", "tests/fixtures/id_rsa", "docs/keys.md"])
    ok = got == [".env", "deploy/.env.prod", "keys/vendor.pem", "config/secrets.yaml", "tests/fixtures/id_rsa"]
    failed += not ok
    print(("ok   " if ok else "FAIL ") + "credential file names" + ("" if ok else f" got={got}"))
    hits = {n for _l, n, _g, _t in scan_text([(1, "--extra-index-url https://x.example/simple"),
                                              (2, "api_token: 'ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4'"),
                                              (3, "requests==2.31.0")])}
    ok = hits == {"package source changed", "private key or known token prefix", "secret-looking literal"}
    failed += not ok
    print(("ok   " if ok else "FAIL ") + "any-file scan finds secrets outside Python" + ("" if ok else f" got={hits}"))
    hits = {n for _l, n, _g, _t in scan([(1, 'TOKEN = "ghp_a1b2c3d4e5f6g7h8i9j0k1l2m3n4"')], is_test=True)}
    ok = "private key or known token prefix" in hits
    failed += not ok
    print(("ok   " if ok else "FAIL ") + "a recorded token in a test file is found")
    print(f"{len(ALL_RISK_DETECTORS)} detectors, {failed} failing")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
