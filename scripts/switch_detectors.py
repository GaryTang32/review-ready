"""Behaviour-switch detectors for review-ready.

A switch is anything that changes what the code does without appearing as
logic in the diff: a config key, an environment variable, a CLI or feature
flag, a default, a registration, the clock, the seed, the sample data. The
kernel names the same idea Kconfig options, module parameters, boot
arguments, and initcall order, and requires a new one to arrive in its own
documented patch defaulting to off.

These are line-level triage over the ADDED lines of a diff, not a parser.
A regex that matches a string literal costs a glance; a switch that no
regex matches costs a bisect, so the list errs toward noise. Each entry is
(name, regex, group) and carries one line it must match and one it must
not in CASES; run this file to check them. The three functions at the end
are the commit-level rules no line regex can express.

audit_diff.py imports this module; behaviour-switches.md explains what to
do about each hit.
"""
import re


SWITCH_DETECTORS = [
    # --- config: Configuration: what a run reads before it does anything
    ('env var read', '\\bos\\.environ\\b|\\bos\\.getenv\\(|\\bgetenv\\(|\\bload_dotenv\\(|\\bdotenv_values\\(', 'config'),
    ('config or settings read', '\\b(config|settings|cfg|conf|CONFIG|SETTINGS|CFG)\\b(?:\\[[^\\]]+\\]|\\.\\w+)*\\.get\\(|\\b(config|settings|cfg|conf|CONFIG|SETTINGS|CFG)\\[', 'config'),
    ('feature flag check', '\\b(feature_flags?|is_enabled|flag_enabled|FLAGS?\\.|flags\\.|\\.enabled\\b|if\\s+\\w*enabled\\b|waffle\\.|ldclient\\.|unleash\\.)', 'config'),
    ('settings module import', '^\\s*(from\\s+[\\w.]*settings\\s+import|import\\s+[\\w.]*settings\\b|from\\s+django\\.conf\\s+import\\s+settings|.*\\bBaseSettings\\b)', 'config'),
    ('CLI flag with a default', 'add_argument\\(.*\\b(default\\s*=|action\\s*=\\s*[\\"\']store_(true|false)[\\"\'])', 'config'),
    # --- defaults: Defaults and silent fallbacks: what happens when the input is missing
    ('function parameter default', '^\\s*(async\\s+)?def\\s+\\w+\\s*\\(.*?\\b\\w+\\s*(?::\\s*[^=,)]+)?=\\s*[^,)\\s]', 'defaults'),
    ('dataclass or model field default', '\\b(field|Field)\\(.*?\\bdefault(_factory)?\\s*=', 'defaults'),
    ('dict.get with a non-None default', '\\.get\\(\\s*[^,()]+,(?!\\s*None\\s*\\))(?!\\s*(?:0|\\[\\])\\s*\\)\\s*\\+)\\s*[^)\\s]', 'defaults'),
    ('getattr with a fallback', '\\bgetattr\\(\\s*[^,()]+,\\s*[^,()]+,\\s*[^)]', 'defaults'),
    ('silent fallback with or', '=\\s*[\\w.\\[\\]\\"\']+\\s+or\\s+[\\"\'\\d\\w{\\[(]', 'defaults'),
    ('None or NaN coerced silently', '\\.fillna\\(|\\.dropna\\(|errors\\s*=\\s*[\\"\'](coerce|ignore)[\\"\']|\\bnan_to_num\\(|\\bor\\s+0(\\.0)?\\b|\\bor\\s+[\\"\']{2}', 'defaults'),
    # --- sequence: Call sequence: what runs, in what order, and what registers itself
    ('module-level call', '^(?!(?:def|class|if|elif|else|for|while|with|try|except|finally|return|import|from|assert|raise|del|pass|yield|global|nonlocal|lambda|async|await|match|case)\\b)(?:[A-Za-z_]\\w*(?:\\s*,\\s*[A-Za-z_]\\w*)*(?:\\s*:\\s*[^=\\n]+?)?\\s*=\\s*)?[A-Za-z_][\\w.]*\\s*\\(', 'sequence'),
    ('decorator with arguments', '^\\s*@[\\w.]+\\(', 'sequence'),
    ('registration or scheduling decorator', '^\\s*@[\\w.]*\\b(route|register|receiver|task|command|group|hook|listener|subscribe|on_\\w+|signal|schedule|periodic|cron|middleware|autouse|fixture|before_\\w+|after_\\w+|handler|validator|root_validator|field_validator|event|listens_for)\\b', 'sequence'),
    ('cache or memoise decorator', '^\\s*@(functools\\.)?(lru_cache|cache|cached_property)\\b|\\bst\\.cache_data\\b|\\bjoblib\\.Memory\\(', 'sequence'),
    ('retry, backoff, or sleep', '^\\s*@[\\w.]*\\bretry\\b|\\b(max_retries|retries|backoff|retry_on|stop_after_attempt|stop_after_delay|wait_exponential|wait_fixed|wait_random)\\b|\\btime\\.sleep\\(', 'sequence'),
    ('scheduler or cron registration', '\\b(crontab|schedule\\.every|periodic_task|add_job|BackgroundScheduler|CronTrigger|IntervalTrigger|beat_schedule|Timer)\\(', 'sequence'),
    ('callback or signal hook registration', '\\b(atexit\\.register|signal\\.signal|sys\\.excepthook|sys\\.settrace|add_callback|register_hook|add_hook|add_done_callback)\\b|\\.(connect|subscribe|on)\\(', 'sequence'),
    ('middleware, app, or plugin list edit', '\\b(MIDDLEWARE|middleware|INSTALLED_APPS|hooks|HOOKS|plugins|PLUGINS)\\s*(=|\\+=|\\.append\\(|\\.insert\\(|\\.extend\\()', 'sequence'),
    ('global state mutated at runtime', '^\\s*global\\s+\\w|\\bsys\\.path\\.(insert|append)\\(|\\bos\\.chdir\\(|\\bpd\\.set_option\\(|\\bnp\\.seterr\\(|\\bwarnings\\.(filterwarnings|simplefilter)\\(|\\blogging\\.basicConfig\\(|\\bsettings\\.configure\\(|\\blocale\\.setlocale\\(|\\bos\\.environ\\[[^\\]]+\\]\\s*=', 'sequence'),
    ('dynamic import or reload', '\\b(importlib\\.import_module|importlib\\.reload|__import__|pkgutil\\.iter_modules|entry_points|load_entry_point)\\(', 'sequence'),
    # --- environment: Runtime environment: clock, timezone, randomness, concurrency, versions
    ('wall clock read', '\\b(datetime\\.(now|utcnow|today)|date\\.today|time\\.time|time\\.monotonic|Timestamp\\.(now|today))\\s*\\(|np\\.datetime64\\(\\s*[\\"\']now[\\"\']', 'environment'),
    ('timezone, locale, or date-format dependence', '\\b(tzinfo\\s*=|tz\\s*=|tz_localize\\(|tz_convert\\(|astimezone\\(|ZoneInfo\\(|pytz\\.|utc\\s*=\\s*(True|False)|locale\\.|LC_ALL|LANG\\b|strftime\\(|strptime\\()', 'environment'),
    ('date parsed with an inferred format', '\\bto_datetime\\((?![^)]*\\bformat\\s*=)|\\bdateutil\\.parser\\.parse\\(|\\bparser\\.parse\\(|\\bdateparser\\.', 'environment'),
    ('randomness without a fixed seed', '\\brandom\\.(random|randint|choice|choices|shuffle|sample|uniform|gauss)\\(|\\bnp\\.random\\.(rand|randn|randint|choice|shuffle|permutation|normal|uniform)\\(|\\bdefault_rng\\(\\s*\\)|\\brandom_state\\s*=\\s*None|\\bshuffle\\s*=\\s*True', 'environment'),
    ('seed set as a global side effect', '\\b(random\\.seed|np\\.random\\.seed|manual_seed|set_seed|seed_everything)\\(|PYTHONHASHSEED', 'environment'),
    ('concurrency primitive or worker count', '\\b(ThreadPoolExecutor|ProcessPoolExecutor|multiprocessing\\.(Pool|Process|set_start_method|get_context)|threading\\.Thread|asyncio\\.(gather|create_task|run)|joblib\\.Parallel)\\b|\\b(n_jobs|num_workers|max_workers)\\s*=', 'environment'),
    ('version or platform gate', '\\bsys\\.version_info\\b|\\bsys\\.platform\\b|\\bplatform\\.(system|machine|python_version)\\(|\\b\\w+\\.__version__\\b|\\bversion\\.parse\\(|\\bpackaging\\.version\\b', 'environment'),
    # --- logging: Logging
    ('f-string in a logging call', '\\b(logger|logging|log|LOG|LOGGER|_log|_logger)\\.(debug|info|warning|warn|error|exception|critical)\\(\\s*f[\\"\']', 'logging'),
]

# Applied to test files only.
TEST_DETECTORS = [
    ('test patches an attribute or environment', '\\b(mock\\.)?patch(\\.object|\\.dict)?\\(\\s*[\\"\']?[\\w.]+|\\bmonkeypatch\\.(setattr|setenv|setitem|delenv)\\(', 'tests'),
    ('test fakes a module constant', 'patch(\\.object)?\\(\\s*[\\"\'][\\w.]*\\.[A-Z][A-Z0-9_]+[\\"\']|patch\\.object\\(\\s*[\\w.]+,\\s*[\\"\'][A-Z][A-Z0-9_]+[\\"\']|monkeypatch\\.setattr\\(\\s*(?:[\\w.]+,\\s*)?[\\"\'][\\w.]*\\b[A-Z][A-Z0-9_]+[\\"\']', 'tests'),
    ('test asserts calls, not results', '\\.assert_(called|not_called|called_once|called_with|called_once_with|any_call|has_calls|awaited\\w*)\\b|\\.call_count\\b|\\.call_args(_list)?\\b|\\.mock_calls\\b', 'tests'),
    ('test sets an environment variable', '\\bmonkeypatch\\.setenv\\(|patch\\.dict\\(\\s*(os\\.environ|[\\"\']os\\.environ[\\"\'])|\\bos\\.environ\\[[^\\]]+\\]\\s*=|\\bos\\.putenv\\(', 'tests'),
]

ALL_DETECTORS = SWITCH_DETECTORS + TEST_DETECTORS

# name -> (a line that must match, a line that must not)
CASES = {
    'env var read': ('fmt = os.environ.get("PIPELINE_DATE_FORMAT", "%Y-%m-%d")', 'fmt = args.date_format'),
    'config or settings read': ('fmt = CONFIG.get("input", {}).get("date_format")', 'counts[row["ticker"]] = counts.get(row["ticker"], 0) + 1'),
    'feature flag check': ('if flags.new_loader_enabled:', 'if rows:'),
    'settings module import': ('from django.conf import settings', 'from pipeline.loader import load_prices'),
    'CLI flag with a default': ('parser.add_argument("--strict", action="store_true", default=False)', 'parser.add_argument("src")'),
    'function parameter default': ('def load_prices(path, strict=False):', 'def load_prices(path):'),
    'dataclass or model field default': ('keep: str = field(default="last")', 'keep: str'),
    'dict.get with a non-None default': ('fmt = cfg.get("date_format", "%Y-%m-%d")', 'fmt = cfg.get("date_format")'),
    'getattr with a fallback': ('fn = getattr(loader, "load_prices", None)', 'fn = getattr(loader, name)'),
    'silent fallback with or': ('fmt = args.fmt or "%Y-%m-%d"', 'if strict or verbose:'),
    'None or NaN coerced silently': ('df["close"] = pd.to_numeric(df["close"], errors="coerce")', 'df["close"] = pd.to_numeric(df["close"])'),
    'module-level call': ('CONFIG = _load_config()', '    rows = load_prices(path)'),
    'decorator with arguments': ('@app.route("/prices")', '@property'),
    'registration or scheduling decorator': ('@receiver(post_save, sender=Price)', '@staticmethod'),
    'cache or memoise decorator': ('@functools.lru_cache(maxsize=None)', '@functools.wraps(fn)'),
    'retry, backoff, or sleep': ('@retry(stop=stop_after_attempt(3))', 'def retry_count(rows):'),
    'scheduler or cron registration': ('sched.add_job(rebuild_report, "cron", hour=6)', 'report = build_schedule(rows)'),
    'callback or signal hook registration': ('atexit.register(flush_report)', 'rows.append(row)'),
    'middleware, app, or plugin list edit': ('MIDDLEWARE.insert(0, "audit.Middleware")', 'rows.insert(0, row)'),
    'global state mutated at runtime': ('sys.path.insert(0, os.getcwd())', 'paths.insert(0, root)'),
    'dynamic import or reload': ('mod = importlib.import_module(name)', 'import pipeline.loader'),
    'wall clock read': ('as_of = datetime.now()', 'as_of = datetime(2024, 1, 2)'),
    'timezone, locale, or date-format dependence': ('df["date"] = df["date"].dt.tz_localize("America/New_York")', 'ticker = row["ticker"].upper()'),
    'date parsed with an inferred format': ('df["date"] = pd.to_datetime(df["date"])', 'df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")'),
    'randomness without a fixed seed': ('idx = np.random.permutation(len(df))', 'rng = np.random.default_rng(42)'),
    'seed set as a global side effect': ('np.random.seed(0)', 'rng = np.random.default_rng(0)'),
    'concurrency primitive or worker count': ('with ThreadPoolExecutor(max_workers=8) as pool:', 'for path in paths:'),
    'version or platform gate': ('if pd.__version__ >= "2.0":', 'version = record["version"]'),
    'f-string in a logging call': ('logger.info(f"loaded {len(rows)} rows from {path}")', 'logger.info("loaded %d rows from %s", len(rows), path)'),
    'test patches an attribute or environment': ('with mock.patch("pipeline.loader.DATE_FORMAT", "%Y-%m-%d"):', 'rows = load_prices(path, date_format="%Y-%m-%d")'),
    'test fakes a module constant': ('with mock.patch("pipeline.loader.DATE_FORMAT", "%Y-%m-%d"):', 'with mock.patch("pipeline.loader.open", mock_open(read_data=csv)):'),
    'test asserts calls, not results': ('fake_write.assert_called_once_with(rows, path)', 'self.assertEqual(recs[0]["n_rows"], 2)'),
    'test sets an environment variable': ('monkeypatch.setenv("PIPELINE_DATE_FORMAT", "%d/%m/%Y")', 'fmt = os.environ.get("PIPELINE_DATE_FORMAT")'),
}


# --- commit-level rules, over the file list rather than over lines ---
SWITCH_FILE = re.compile(
    r"\.(ya?ml|toml|ini|cfg|json|env|conf|properties|csv|tsv|parquet|pkl|pickle|joblib"
    r"|npy|npz|h5|hdf5|sql|sqlite|db|feather|xlsx?|pt|pth|onnx|safetensors)$"
    r"|(^|/)(fixtures?|seeds?|data|migrations|alembic|schemas?|weights|models|artifacts"
    r"|golden|snapshots?|__snapshots__)(/|$)|(^|/)\.env")
CODE_FILE = re.compile(r"\.py$")
TEST_PATH = re.compile(r"(^|/)(tests?|testing)(/|$)|(^|/)test_\w+\.py$|_test\.py$|conftest\.py$")


def mixed_switch_and_code(paths):
    """The rule: config or data changed in the same commit as code is a
    behaviour change that needs its own patch and its own test. Returns
    (switch files, code files) when both are non-empty, else None. A commit
    that is only config and data (a flip patch) passes."""
    switch = [p for p in paths if SWITCH_FILE.search(p) and not TEST_PATH.search(p)]
    code = [p for p in paths if CODE_FILE.search(p) and not TEST_PATH.search(p)]
    return (switch, code) if switch and code else None


CONFIG_KEY = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*[:=]")


def dead_config_keys(added_config_lines, code_text):
    """Keys added to a config file that no code (tree plus patch) mentions:
    a config section added but never read."""
    keys = {m.group(1) for line in added_config_lines for m in [CONFIG_KEY.match(line)] if m}
    return sorted(k for k in keys if not re.search(r"\b" + re.escape(k) + r"\b", code_text))


ENV_READ = re.compile(r"(?:os\.environ(?:\.get)?\(?\[?|getenv\()\s*[\"']([A-Z][A-Z0-9_]+)[\"']")


def flags_read_never_set(added_code, everything_else):
    """Environment variables the patch reads that nothing in the tree, CI,
    docs, or env files sets or documents: a flag read but never set."""
    names = set(ENV_READ.findall(added_code))
    return sorted(n for n in names if n not in everything_else)


def main():
    """Self-test: every detector matches its positive case and not its negative."""
    failed = 0
    for name, rx, _group in ALL_DETECTORS:
        pos, neg = CASES[name]
        r = re.compile(rx)
        ok = bool(r.search(pos)) and not r.search(neg)
        failed += not ok
        print(("ok   " if ok else "FAIL ") + name)
    assert set(CASES) == {n for n, _, _ in ALL_DETECTORS}
    assert mixed_switch_and_code(["pipeline/loader.py", "config.yaml"]) == (["config.yaml"], ["pipeline/loader.py"])
    assert mixed_switch_and_code(["pipeline/loader.py", "tests/test_loader.py"]) is None
    assert mixed_switch_and_code(["config.yaml", "data/sample.csv"]) is None
    assert mixed_switch_and_code(["tests/fixtures/prices.csv", "pipeline/loader.py"]) is None
    assert dead_config_keys(["dedupe:", "  enabled: true", "  keep: last"],
                            "if cfg['dedupe']['enabled']:") == ["keep"]
    assert dead_config_keys(["  date_format: '%Y-%m-%d'"], "fmt = cfg['input']['date_format']") == []
    assert flags_read_never_set('os.environ.get("PIPELINE_DATE_FORMAT", "x")',
                                "README: nothing; .env: nothing") == ["PIPELINE_DATE_FORMAT"]
    assert flags_read_never_set('os.getenv("PIPELINE_STRICT")', "ci.yml: PIPELINE_STRICT=1") == []
    print(f"commit-level rules ok; {len(ALL_DETECTORS)} detectors, {failed} failing")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
