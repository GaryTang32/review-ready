#!/usr/bin/env python3
"""comment_audit.py: find comments that cost more than they pay for.

usage: comment_audit.py [--repo PATH] [--src DIR ...] [--md FILE] [--json FILE]
                        [--top N] [--ratio R] [--min-code N]

A comment earns its place by saying what the code is for and how to use it.
It does not earn its place by restating the signature, narrating the next
line, or carrying the history of how the code got here: git already holds
the history, and it holds it more accurately, because a comment about a
change made three years ago is never updated when the change is undone.

The test applied here is the reader's: a ten-line function does not need
thirty lines of prose above it, and a reader who must get through more
comment than code to reach the code has been charged twice.

What it reports, worst first:
  over budget     functions whose comment and docstring lines outweigh their
                  code, or whose docstring is long for a short body
  history         changelog entries, dates, versions, authors, "previously",
                  "used to", ticket numbers: what git records
  restates        an Args block whose entries add nothing the signature
                  does not already say
  narration       a comment that paraphrases the line under it
  essay           a block of prose inside a function body, where a named
                  function would say the same thing and be checkable
  banner          rules and boxes drawn in punctuation
  dead            commented-out code
  stale           a docstring documenting a parameter the signature does not have

Nothing is imported or executed; the report comes from the AST and the raw
lines. Nothing is written to the tree.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
import tokenize
from collections import defaultdict

RATIO = 1.0            # comment+docstring lines per line of code
MIN_CODE = 4           # below this a function is too small to judge by ratio
PROSE_FLOOR = 8        # fewer prose lines than this is never worth a finding
LONG_DOC_BODY = 20     # a body under this many lines ...
LONG_DOC = 12          # ... should not carry a docstring longer than this
MODULE_DOC = 30
ESSAY = 6              # consecutive comment lines inside a function body
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "build",
             "dist", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages"}

# History means a claim about how the code changed, which git records and a
# comment does not update. A date is not a history marker on its own: in a
# trading calendar or a fixture, dates are the subject matter. Require an
# explicit changelog framing or a code-change verb, so domain facts survive.
HISTORY = re.compile(
    r"\b(previously|formerly|used to be|used to return|used to call|no longer|"
    r"was (called|named|a)\b|renamed (from|to)|"
    r"(changed|added|removed|introduced|deprecated|fixed) in (v?\d|release|version)|"
    r"back when|for backwards[- ]compat|kept for compat|legacy reason|"
    r"changelog|revision history|modified by|updated by|author:|created on|"
    r"(jira|ticket|issue|bug|case)[ -]?#?\s*[A-Z]{2,}-\d+|"
    r"see (commit|PR\b|pull request)|per (the )?(review|discussion)|"
    r"(refactor(ed|ing)|rewrote|migrated) (this|the|it|from|to)\b)", re.I)
BANNER = re.compile(r"^\s*#\s*[-=*_~#+]{6,}\s*$|^\s*#\s*[-=*_~#+]{3,}.*[-=*_~#+]{3,}\s*$")
# Commented-out code, decided by parsing rather than by the first word: prose
# beginning "if the archive grows..." or "from it, so..." is not code, and a
# first-word regex calls both of them dead.
BLOCK_HEADER = re.compile(
    r"^(def|class|if|elif|else|for|while|try|except|finally|with)\b.*:\s*$")
CODE_NODES = (ast.Assign, ast.AugAssign, ast.Return, ast.Raise,
              ast.Import, ast.ImportFrom, ast.If, ast.For, ast.While, ast.Try,
              ast.With, ast.FunctionDef, ast.ClassDef, ast.Assert, ast.Delete)
# Directives to a tool, not to the reader. "# fmt: off" even parses as an
# annotated assignment, and "Note: something" parses the same way, which is why
# a bare annotation never counts as code.
PRAGMA = re.compile(r"^#\s*(fmt|type|noqa|pylint|mypy|pyright|pragma|isort|ruff|yapf|"
                    r"flake8|coverage|nosec|pragma no cover|pytype|bandit)\b", re.I)


def is_dead_code(comment):
    """True when the comment body is Python rather than prose about Python."""
    stripped = comment.strip()
    if PRAGMA.match(stripped):
        return False
    body = comment.split("#", 1)[1].strip() if "#" in comment else stripped
    if not body or len(body) < 4:
        return False
    if BLOCK_HEADER.match(body):
        return True
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return False
    for node in tree.body:
        if isinstance(node, CODE_NODES):
            return True
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            return True
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return True
    return False


ARGS_HEAD = re.compile(r"^\s*(Args|Arguments|Parameters|Params|Keyword Arguments)\s*:\s*$", re.I)
SECTION_HEAD = re.compile(r"^\s*(Returns?|Raises?|Yields?|Examples?|Notes?|Attributes|See Also)\s*:\s*$", re.I)
ARG_ENTRY = re.compile(r"^\s*\*{0,2}(\w+)\s*(\([^)]*\))?\s*:\s*(.*)$")
STOP = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "at", "by", "with",
        "this", "that", "it", "its", "we", "then", "now", "from", "into", "as", "is", "are",
        "be", "each", "all", "if", "not", "no", "so", "do", "does", "get", "set", "value",
        "values", "list", "dict", "string", "str", "int", "bool", "float", "none", "object"}
TYPE_WORDS = {"str", "int", "float", "bool", "list", "dict", "set", "tuple", "optional",
              "any", "none", "object", "path", "sequence", "iterable", "mapping", "callable",
              "the", "a", "an", "of", "to"}


def words(text):
    return [w for w in re.findall(r"[a-z_]{3,}", text.lower()) if w not in STOP]


def doc_span(node):
    """(first, last) line numbers of a docstring, or None."""
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return None
    return first.lineno, (first.end_lineno or first.lineno)


def comment_lines(src):
    """{lineno: text} for every comment, and the set of lines holding code."""
    comments, code = {}, set()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return comments, code
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            comments[tok.start[0]] = tok.line.rstrip("\n")
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                              tokenize.DEDENT, tokenize.ENDMARKER, tokenize.STRING):
            code.add(tok.start[0])
        elif tok.type == tokenize.STRING:
            code.add(tok.start[0])
    return comments, code


def restates_signature(doc, arg_names):
    """Args entries that add nothing the signature already says."""
    lines = doc.splitlines()
    empty, total = [], 0
    inside = False
    for line in lines:
        if ARGS_HEAD.match(line):
            inside = True
            continue
        if SECTION_HEAD.match(line):
            inside = False
            continue
        if not inside:
            continue
        m = ARG_ENTRY.match(line)
        if not m:
            continue
        name, _type, desc = m.group(1), m.group(2), m.group(3)
        if name not in arg_names:
            continue
        total += 1
        # Strip the parameter's own name and pure type nouns; what is left is
        # the only thing the signature did not already carry.
        left = [w for w in re.findall(r"[A-Za-z_]+", desc.lower())
                if w not in TYPE_WORDS and w != name.lower()
                and w not in name.lower().split("_")]
        if len(left) < 2:
            empty.append(name)
    return empty, total


def narrates(comment, next_code):
    """True when a comment mostly repeats the identifiers on the next line."""
    ctext = comment.split("#", 1)[1] if "#" in comment else comment
    cw = words(ctext)
    if not (2 <= len(cw) <= 9):
        return False
    low = next_code.lower()
    hit = sum(1 for w in cw if w in low or w.rstrip("s") in low or (w + "s") in low)
    return hit / len(cw) >= 0.6


def scan_file(rel, src):
    comments, code_lines = comment_lines(src)
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [], dict(file=rel, error=str(exc))
    lines = src.splitlines()
    findings = []

    def add(kind, line, detail, fix, weight):
        findings.append(dict(file=rel, line=line, kind=kind, detail=detail, fix=fix, weight=weight))

    # module docstring
    span = doc_span(tree)
    if span and (span[1] - span[0] + 1) > MODULE_DOC:
        add("over budget", span[0],
            f"module docstring is {span[1] - span[0] + 1} lines",
            f"Say what the module is for and who calls it. Move the rest into a doc or delete it "
            f"(budget {MODULE_DOC}).", 3)

    owner = {}   # lineno -> enclosing function qualname
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start, end = node.lineno, node.end_lineno or node.lineno
        body_start = node.body[0].lineno if node.body else start
        span = doc_span(node)
        doc_len = (span[1] - span[0] + 1) if span else 0
        own_comments = [ln for ln in comments if body_start <= ln <= end]
        # a comment block directly above the def belongs to it too
        above, probe = [], start - 1
        while probe in comments and not BANNER.match(comments[probe]):
            above.append(probe); probe -= 1
        own_comments += above
        doc_range = range(span[0], span[1] + 1) if span else ()
        code_n = len([ln for ln in code_lines
                      if body_start <= ln <= end and ln not in doc_range])
        prose_n = doc_len + len(own_comments)
        for ln in own_comments + ([span[0]] if span else []):
            owner[ln] = node.name

        if code_n >= MIN_CODE and prose_n >= PROSE_FLOOR and prose_n > RATIO * code_n:
            add("over budget", start,
                f"{node.name}(): {prose_n} lines of comment and docstring against {code_n} lines of code",
                "Keep the sentence that says what it does and how to call it; delete the rest.",
                4 + min(prose_n - code_n, 20) / 10)
        if code_n and code_n <= 3 and doc_len >= 6:
            add("over budget", span[0],
                f"{node.name}(): {doc_len}-line docstring over {code_n} line(s) of code",
                "A body this short is read faster than the paragraph above it. One sentence.",
                4 + min(doc_len, 20) / 10)
        elif doc_len > LONG_DOC and code_n < LONG_DOC_BODY:
            add("over budget", span[0],
                f"{node.name}(): {doc_len}-line docstring on a {code_n}-line body",
                f"A body this short is its own explanation. Budget is {LONG_DOC} lines.", 3.5)
        if span:
            doc = ast.get_docstring(node) or ""
            have = {a.arg for a in getattr(node.args, "posonlyargs", []) + node.args.args + node.args.kwonlyargs}
            for extra in (node.args.vararg, node.args.kwarg):
                if extra:
                    have.add(extra.arg)
            gone, inside = [], False
            for dline in doc.splitlines():
                if ARGS_HEAD.match(dline):
                    inside = True
                elif SECTION_HEAD.match(dline):
                    inside = False
                elif inside:
                    m_ = ARG_ENTRY.match(dline)
                    if m_ and m_.group(1) not in have and dline.startswith(" "):
                        gone.append(m_.group(1))
            if gone:
                add("stale", span[0],
                    f"{node.name}(): docstring documents {', '.join(gone)}, which the signature does not have",
                    "Not a matter of taste: the docstring or the signature is wrong. Decide which, "
                    "and say so in the message.", 4.5)
            names = {a.arg for a in node.args.args + node.args.kwonlyargs} | \
                    {a.arg for a in node.args.posonlyargs} if hasattr(node.args, "posonlyargs") else \
                    {a.arg for a in node.args.args + node.args.kwonlyargs}
            empty, total = restates_signature(doc, names)
            if total and len(empty) >= max(2, total // 2):
                add("restates", span[0],
                    f"{node.name}(): Args entries for {', '.join(empty[:4])} add nothing the signature says",
                    "Drop the Args block, or keep only the parameters with a fact in them "
                    "(units, ranges, what None means).", 3)

    # line-level passes
    run_start, run = None, 0
    for ln in sorted(comments):
        text = comments[ln]
        stripped = text.strip()
        where = owner.get(ln)
        tag = f"{where}(): " if where else ""
        if BANNER.match(text):
            add("banner", ln, f"{tag}a rule drawn in punctuation",
                "A section rule in a file means the file is two files.", 1)
        elif is_dead_code(text):
            add("dead", ln, f"{tag}commented-out code",
                "Delete it; git remembers.", 2.5)
        if HISTORY.search(stripped):
            add("history", ln, f"{tag}{stripped[:74]}",
                "git log carries this, and it stays true when the code changes. Delete the sentence.", 4)
        nxt = lines[ln] if ln < len(lines) else ""
        if nxt.strip() and not nxt.strip().startswith("#") and narrates(text, nxt):
            add("narration", ln, f"{tag}{stripped[:60]!r} restates the line under it",
                "Delete it, or if the block needs a name, make it a function whose name says that.", 2)
        # essays: consecutive comment lines inside a body
        if run_start is None or ln != run_start + run:
            if run >= ESSAY and owner.get(run_start):
                add("essay", run_start,
                    f"{owner[run_start]}(): {run} consecutive comment lines inside the body",
                    "A paragraph inside a function is a function waiting to be named.", 3)
            run_start, run = ln, 1
        else:
            run += 1
    if run >= ESSAY and run_start is not None and owner.get(run_start):
        add("essay", run_start, f"{owner[run_start]}(): {run} consecutive comment lines inside the body",
            "A paragraph inside a function is a function waiting to be named.", 3)

    total_doc = sum((doc_span(n)[1] - doc_span(n)[0] + 1)
                    for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
                    and doc_span(n))
    stats = dict(file=rel, lines=len(lines), code=len(code_lines),
                 comments=len(comments), docstring=total_doc)
    return findings, stats


def walk(root, src_dirs):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(base, fn), root).replace(os.sep, "/")
            if src_dirs and not any(rel == d or rel.startswith(d.rstrip("/") + "/") for d in src_dirs):
                continue
            yield rel, os.path.join(base, fn)


def render(report, top):
    r = report
    L = [f"# Comment audit: {r['root']}", "",
         f"{r['files']} files, {r['code']} lines of code, {r['comments']} comment lines and "
         f"{r['docstring']} docstring lines: **{r['share']}%** of the tree is prose.", ""]
    if not r["findings"]:
        L += ["Nothing over budget. Comments here say what the code is for and stop.", ""]
        return "\n".join(L)
    L += ["A comment earns its place by saying what the code is for and how to use it.",
          "History belongs in git, which keeps it accurate; narration belongs nowhere.", "",
          "## By kind", "", "| kind | count | what it costs |", "|---|---:|---|"]
    COST = {
        "over budget": "the reader gets through more prose than code to reach the code",
        "history": "a claim about the past that nothing updates when the code changes",
        "restates": "the signature read twice",
        "narration": "a line that goes stale the moment the code under it moves",
        "essay": "an explanation where a named function would be checkable",
        "banner": "a file pretending to be two files",
        "dead": "code the reader must decide whether to trust",
        "stale": "a documented parameter that does not exist: the docstring or the signature is wrong",
    }
    for kind, n in sorted(r["by_kind"].items(), key=lambda kv: -kv[1]):
        L.append(f"| {kind} | {n} | {COST.get(kind, '')} |")
    L += ["", f"## Worst first ({min(top, len(r['findings']))} of {len(r['findings'])})", ""]
    for f in r["findings"][:top]:
        L.append(f"- **{f['file']}:{f['line']}** &mdash; {f['kind']}: {f['detail']}")
        L.append(f"  {f['fix']}")
    L.append("")
    if r["heaviest"]:
        L += ["## Heaviest files", "", "| file | code | comment | docstring | prose share |",
              "|---|---:|---:|---:|---:|"]
        for s in r["heaviest"]:
            L.append(f"| {s['file']} | {s['code']} | {s['comments']} | {s['docstring']} | {s['share']}% |")
        L.append("")
    L += ["## What to do with them", "",
          "These are patches, not edits to make while passing. Comment removal changes no",
          "behaviour, so it lands as its own commit saying \"No functional change.\", and a",
          "reviewer checks it by seeing that no test moved. Group by file, not by kind: one",
          "patch per file reads as a decision, twenty patches by category read as a sweep.", ""]
    return "\n".join(L)


def main():
    global RATIO, MIN_CODE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--src", action="append", default=[])
    ap.add_argument("--md")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--ratio", type=float, default=RATIO)
    ap.add_argument("--min-code", type=int, default=MIN_CODE)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    RATIO, MIN_CODE = args.ratio, args.min_code
    root = os.path.abspath(args.repo)
    findings, stats, errors = [], [], []
    for d in args.src:
        if not os.path.isdir(os.path.join(root, d)):
            sys.exit(f"--src {d!r} is not a directory under {root}: pass the package DIRECTORY (src/acme), not its name")
    for rel, path in walk(root, args.src):
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        f, s = scan_file(rel, src)
        if "error" in s:
            errors.append(s)
            continue
        findings += f
        stats.append(s)
    findings.sort(key=lambda f: (-f["weight"], f["file"], f["line"]))
    for s in stats:
        prose = s["comments"] + s["docstring"]
        s["share"] = round(100 * prose / max(s["code"], 1))
    code = sum(s["code"] for s in stats)
    com = sum(s["comments"] for s in stats)
    doc = sum(s["docstring"] for s in stats)
    report = dict(root=root, files=len(stats), code=code, comments=com, docstring=doc,
                  share=round(100 * (com + doc) / max(code, 1)),
                  findings=findings, by_kind=dict(sorted(
                      __import__("collections").Counter(f["kind"] for f in findings).items())),
                  heaviest=sorted([s for s in stats if s["code"] >= 20],
                                  key=lambda s: -s["share"])[:10],
                  errors=errors)
    text = render(report, args.top)
    print(text)
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        open(args.md, "w").write(text)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump(report, open(args.json, "w"), indent=2)


def self_test():
    """Each case is a snippet that must produce a kind, or must produce none."""
    cases = [
        ("history", '''
def load(path):
    """Read the file."""
    # Previously this used csv.reader; changed in v2.1 to DictReader.
    return open(path).read()
'''),
        ("narration", '''
def load(path):
    """Read the file."""
    # open the path and read it
    data = open(path).read()
    return data
'''),
        ("restates", '''
def load(path, limit, mode):
    """Read the file.

    Args:
        path: The path.
        limit: The limit.
        mode: The mode.
    """
    return open(path).read()[:limit]
'''),
        ("over budget", '''
def summarise(rows):
    """One record per key."""
    # Walk the rows.
    # Group them by key.
    # Count how many rows each key has.
    # Track the last value seen for the key.
    # Then sort the keys.
    # Then build one record per key.
    # The record has the key, the count and the last value.
    # Return the list of records.
    out = {}
    for row in rows:
        k = row["k"]
        if k not in out:
            out[k] = {"k": k, "n": 0, "last": None}
        out[k]["n"] += 1
        out[k]["last"] = row["v"]
    return [out[k] for k in sorted(out)]
'''),
        ("over budget", '''
def add(a, b):
    """Add two numbers together and return the sum of them.

    This function takes the first number and the second number and
    performs an addition of the two, producing a new number which is
    the arithmetic sum. It does not mutate either input. It works for
    integers and for floats, and for anything else supporting plus.
    There is no error handling because plus raises on its own.
    """
    return a + b
'''),
        ("banner", '''
# ----------------------------------------
def f():
    return 1
'''),
        ("dead", '''
def f():
    # return legacy_value(x)
    return 1
'''),
        ("essay", '''
def f(rows):
    out = []
    # The loop below walks every row.
    # It checks whether the row is valid.
    # Validity means the price is positive.
    # A negative price is a vendor error.
    # We drop those rows silently.
    # That matches what the old report did.
    for r in rows:
        if r > 0:
            out.append(r)
    return out
'''),
        (None, '''
def dedupe_keep_last(rows, key="id"):
    """Last row per key, in the order the keys first appeared.

    A row with no key raises: skipping it silently would report a clean
    load of the wrong row count.
    """
    last = {}
    for row in rows:
        last[row[key]] = row
    return list(last.values())
'''),
        ("stale", '''
def load(path):
    """Read the file.

    Args:
        path: CSV file, UTF-8.
        encoding: codec to use, defaults to the locale's.
    """
    return open(path).read()
'''),
        (None, '''
# fmt: off
TABLE = {"a": 1}
# fmt: on
# noqa: E501
# type: ignore[arg-type]
'''),
        (None, '''
def f(rows):
    """Count rows."""
    # Note: vendor files repeat a row when a close is restated.
    return len(rows)
'''),
        (None, '''
def search(conn, query):
    """Rows matching query, newest first."""
    # Substring matching is what CJK needs, so route those queries to the
    # LIKE path below. Cheap at this corpus size;
    # if the archive grows past ~1M rows, swap in a trigram-tokenized index.
    return _like(conn, query)
'''),
        (None, '''
# Articles store the filing's document URL; cik + accession are recoverable
# from it, so no schema change is needed.
_URL_RE = re.compile(r"/edgar/data/")
'''),
        (None, '''
# 2026 (2026-07-03 is a FULL closure: July 4 falls on a Saturday).
# 2025-01-09 was an unscheduled closure, a national day of mourning.
HALF_DAYS = {"2026-11-27", "2026-12-24"}


def is_half_day(day):
    """True when the exchange closes early on that calendar day."""
    return day in HALF_DAYS
'''),
        (None, '''
def replay(calls, as_of):
    """Grade calls as they would have been graded at as_of.

    historical replay renders the same verdicts it would have at the time.
    """
    return [grade(c, as_of) for c in calls]
'''),
        (None, '''
def sessions_between(start, end):
    """Trading sessions in [start, end), in the exchange's timezone."""
    # The exchange calendar is authoritative; a naive weekday count is
    # wrong around holidays.
    return _calendar.sessions_in_range(start, end)
'''),
    ]
    failed = 0
    for want, src in cases:
        found, _ = scan_file("t.py", src)
        kinds = {f["kind"] for f in found}
        ok = (want in kinds) if want else (not kinds)
        failed += not ok
        label = want or "clean"
        print(("ok   " if ok else "FAIL ") + f"{label:12} got={sorted(kinds) or 'none'}")
    print(f"{len(cases)} cases, {failed} failing")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
