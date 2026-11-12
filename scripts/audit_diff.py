#!/usr/bin/env python3
"""audit_diff.py: size up a change the way a maintainer would, from git alone.

usage: audit_diff.py [--repo PATH] [--base REF] [--head REF] [--md FILE] [--json FILE]
                     [--check-tip CMD | --check CMD] [--check-timeout S] [--import-check PKG]

  --base REF        the branch the PR targets (default: main/master/develop, or its
                    remote-tracking ref when the local one is behind). The report is of
                    <merge-base REF HEAD>..HEAD, so the fork point is what it measures.
  --check-tip CMD   run CMD once, in a read-only export of the HEAD commit: the baseline
                    a pre-PR pass holds its rebuilt series to (passed, tests run, skipped).
  --check CMD       run CMD in an export of EVERY commit: review mode's question, "is this
                    series bisectable?". Reports ok, FAIL, did not run (exit 126/127),
                    timed out, or vacuous (green with no test run, or all skipped).
  --import-check PKG  import PKG inside the export before the check and require it to
                    resolve there. An absolute PYTHONPATH or an editable install otherwise
                    points the check at the user's checkout, and the result says nothing
                    about the commit. Set it whenever --check or --check-tip is set.
  --md FILE         write the report there; stdout then carries the Ground section and the
                    maintainer's first objections, not the whole thing.

Sections of the report:
  ground          the target and whether it is today's, the fork point, whether the branch
                  still merges, its authors, and whether it is already published
  size            by file class (source, test, config, docs, generated, notebook, data,
                  binary), reviewable lines against noise, new files against edits
  churn           whitespace-only lines per file
  commits         subject, body, mixed file classes, missing sign-off, patches over budget
  smells          debug prints, broad and swallowed excepts, TODOs, commented-out code,
                  f-strings passed to logging, mock-the-callee tests, comment ratio
  behaviour switches  env/config/flag reads, import-time side effects, defaults, sequence,
                  the clock, plus config keys added and never read (switch_detectors.py)
  risk patterns   security, data correctness, performance, concurrency, logging and test
                  risks in added lines, and credential files (risk_detectors.py)
  public surface  signatures that changed or vanished, marked breaking, widened or moved
  duplicates      new functions the tree already defines, by name or by AST shape
  changed functions  over budget, dense, hiding a second loop, returning mixed shapes,
                  documenting a parameter that does not exist, asserting nothing
  comments        comments in the change that cost more than they pay for (comment_audit.py)
  what a maintainer would say first  the findings, each naming the reference that says
                  what to do about it

Everything comes from git and the AST; nothing is imported or executed except the
command you pass, and that runs in a temporary export, never in the working tree.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import defaultdict

LENGTH_BUDGET = 48
DEPTH_BUDGET = 3
PATCH_BUDGET = 150
PR_BUDGET = 200        # reviewable lines a reviewer finishes in one sitting
PR_PATCHES = 8

GENERATED_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "pipfile.lock", "uv.lock", "cargo.lock", "go.sum", "composer.lock",
    "gemfile.lock", "requirements.lock", "flake.lock",
}
GENERATED_DIRS = ("dist/", "build/", "node_modules/", "vendor/", "__snapshots__/")
GENERATED_SUFFIX = (".min.js", ".min.css", "_pb2.py", "_pb2_grpc.py", ".pb.go",
                    ".generated.ts", ".generated.py", ".snap", ".lock")
DATA_SUFFIX = (".csv", ".tsv", ".parquet", ".feather", ".pkl", ".pickle", ".npy",
               ".npz", ".h5", ".hdf5", ".xlsx", ".xls", ".arrow", ".sav", ".dta",
               ".jsonl")
DOC_SUFFIX = (".md", ".rst", ".txt", ".adoc")
CONFIG_SUFFIX = (".toml", ".yaml", ".yml", ".ini", ".cfg", ".json", ".env",
                 ".conf", ".properties")
CODE_SUFFIX = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
               ".scala", ".rb", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift",
               ".sql", ".sh", ".r", ".jl", ".m", ".php", ".ex", ".exs", ".clj")
TRAILER = re.compile(r"^(Signed-off-by|Co-authored-by|Reviewed-by|Acked-by|Tested-by|"
                     r"Reported-by|Suggested-by|Cc|Fixes|Ref|Refs|Link|Closes): ", re.I)
GENERIC_SUBJECT = re.compile(r"^(wip|fix|fixes|fixed|update|updates|updated|misc|"
                             r"changes|more|stuff|tweaks?|cleanup|refactor)\b\s*:?\s*$", re.I)
TOOL_MENTION = re.compile(r"\b(codex|copilot|chatgpt|claude|ai[- ]generated|generated by|llm)\b", re.I)

SMELLS = [
    ("debug print", re.compile(r"^\s*(print\(|console\.(log|debug)\(|dbg!\(|fmt\.Println\()")),
    ("broad except", re.compile(r"^\s*except(\s*:|\s+(Exception|BaseException)\b)")),
    ("TODO/FIXME", re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")),
    ("debug log", re.compile(r"\b(logger|logging|log)\.debug\(")),
    ("commented-out code", re.compile(
        r"^\s*#\s*(def |class |return\b|if\b|for\b|while\b|import\b|from\b|"
        r"[A-Za-z_][\w.]*\s*=\s*\S|[A-Za-z_][\w.]*\(.*\)\s*$)")),
    ("tool mention", TOOL_MENTION),
    ("f-string passed to logging", re.compile(r"\b(logger|logging|log)\.(debug|info|warning|error|exception|critical)\(\s*f[\"']")),
]
# Behaviour-switch and test detectors live in switch_detectors.py next to this
# script so behaviour-switches.md and the audit share one list. Each entry is
# (name, regex, group).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from switch_detectors import (SWITCH_DETECTORS, TEST_DETECTORS, dead_config_keys,
                                  flags_read_never_set, mixed_switch_and_code)
except ImportError:  # the module is missing: fall back to no switch analysis
    SWITCH_DETECTORS, TEST_DETECTORS = [], []
    dead_config_keys = flags_read_never_set = mixed_switch_and_code = None
try:
    import code_metrics as cm
    import risk_detectors as rd
    import comment_audit as ca
except ImportError:  # degrade rather than die when a sibling module is missing
    cm = rd = ca = None
SWITCH_SMELLS = [(n, re.compile(rx)) for n, rx, _g in SWITCH_DETECTORS]
TEST_SMELLS = [(n, re.compile(rx)) for n, rx, _g in TEST_DETECTORS]
SWITCH_GROUP = {n: g for n, _rx, g in SWITCH_DETECTORS + TEST_DETECTORS}
# Added by module_side_effects(), which is an AST check rather than a regex.
SWITCH_GROUP["import-time side effect (module-level statement)"] = "sequence"

SWALLOW_NEXT = re.compile(r"^\s*(pass|return None|return|continue)\s*$")


def git(repo, *args, ok=False):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode != 0 and not ok:
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def ref_exists(repo, ref):
    return subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "-q", ref + "^{commit}"],
                          capture_output=True).returncode == 0


def fresh_target(repo, name):
    """(ref to use, note). `git fetch` moves origin/main and never main, so a local
    target that is behind its remote is swapped for the remote-tracking ref."""
    if not ref_exists(repo, f"refs/heads/{name}"):
        return name, ""
    upstream = git(repo, "rev-parse", "--abbrev-ref", f"{name}@{{upstream}}", ok=True).strip()
    remotes = [upstream] if upstream else git(repo, "for-each-ref", "--format=%(refname:short)",
                                              f"refs/remotes/*/{name}").split()
    for remote in remotes:
        behind = git(repo, "rev-list", "--count", f"{name}..{remote}", ok=True).strip()
        if behind and behind != "0":
            return remote, (f"local `{name}` is {behind} commit(s) behind `{remote}` (fetch never moves a local "
                            f"branch), so `{remote}` is the target")
    return name, ""


def find_base(repo, head):
    for cand in ("main", "master", "develop", "origin/main", "origin/master"):
        if ref_exists(repo, cand):
            target, _note = fresh_target(repo, cand)
            mb = git(repo, "merge-base", target, head).strip()
            if mb:
                return cand, mb
    sys.exit("no main/master/develop branch found; pass --base")


def ground(repo, label, fork, head):
    """What step 0 needs before any work: is the target today's, has it moved, will the
    branch still merge, who wrote the commits, and is the branch already published."""
    target, note = fresh_target(repo, label)
    tip = git(repo, "rev-parse", target + "^{commit}").strip()
    moved = int(git(repo, "rev-list", "--count", f"{fork}..{target}").strip() or 0)
    g = dict(target=target, target_tip=tip[:12], fork=fork, moved=moved, note=note, merge="not needed", conflicts=[])
    if moved:
        r = subprocess.run(["git", "-C", repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", target, head],
                           capture_output=True, text=True)
        if r.returncode == 0:
            g["merge"] = "clean"
        elif r.returncode == 1:
            g["merge"], g["conflicts"] = "CONFLICTS", [l for l in r.stdout.strip().splitlines()[1:] if l]
        else:
            g["merge"] = "could not be tested (git older than 2.38)"
    g["merges_in_branch"] = int(git(repo, "rev-list", "--count", "--merges", f"{fork}..{head}").strip() or 0)
    g["authors"] = sorted(set(git(repo, "log", "--format=%an <%ae>", f"{fork}..{head}").splitlines()))
    up = git(repo, "rev-parse", "--abbrev-ref", f"{head}@{{upstream}}", ok=True).strip()
    g["upstream"] = up
    if up:
        ahead, behind = (git(repo, "rev-list", "--left-right", "--count", f"{head}...{up}", ok=True).split() + ["?", "?"])[:2]
        g["upstream_state"] = f"{ahead} ahead, {behind} behind"
    return g


def classify(path):
    p = path.lower()
    name = os.path.basename(p)
    if name in GENERATED_FILES or p.endswith(GENERATED_SUFFIX) or any(seg in p for seg in GENERATED_DIRS):
        return "generated"
    if p.endswith(".ipynb"):
        return "notebook"
    if p.endswith(DATA_SUFFIX):
        return "data"
    if (re.search(r"(^|/)(tests?|__tests__|spec|testing)/", p) or re.match(r"test_.*\.py$", name)
            or re.search(r"(_test\.py|\.test\.[jt]sx?|\.spec\.[jt]sx?|_spec\.rb|_test\.go)$", name)):
        return "test"
    if p.endswith(DOC_SUFFIX) or re.search(r"(^|/)docs?/", p):
        return "docs"
    if p.endswith(CONFIG_SUFFIX) or name in ("dockerfile", "makefile") or p.startswith(".github/") or name.startswith(".env"):
        if p.endswith(".json") and re.search(r"(^|/)(data|fixtures)/", p):
            return "data"
        return "config"
    if p.endswith(CODE_SUFFIX):
        return "source"
    return "other"


def normalize_rename(path):
    m = re.match(r"^(.*)\{(.*) => (.*)\}(.*)$", path)
    if m:
        return (m.group(1) + m.group(3) + m.group(4)).replace("//", "/")
    if " => " in path:
        return path.split(" => ", 1)[1]
    return path


def parse_numstat(text):
    rows = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], normalize_rename("\t".join(parts[2:]))
        rows[path] = (0, 0, True) if a == "-" else (int(a), int(d), False)
    return rows


def numstat(repo, a, b, *extra):
    return parse_numstat(git(repo, "diff", "--numstat", "-M", *extra, a, b, "--"))


def numstat_commit(repo, sha, *extra):
    return parse_numstat(git(repo, "show", "--numstat", "--format=", "-M", *extra, sha, "--"))


def added_lines(repo, base, head, paths):
    """(path, new_lineno, text) for every added line, plus the set of touched new-side lines per file."""
    added, touched = [], defaultdict(set)
    if not paths:
        return added, touched
    # No pathspec here. Limiting the diff to the new paths hides the old side of
    # every rename, and git then reports a moved file as wholly added: a pure
    # `git mv` series would show thousands of "added" lines and every smell in
    # them. Diff everything, filter afterwards.
    wanted = set(paths)
    out = git(repo, "diff", "-U0", "-M", "--no-color", base, head)
    cur, ln = None, 0
    for line in out.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
            if cur not in wanted:
                cur = None
            continue
        if line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            ln = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if cur and count == 0:
                touched[cur].add(ln)
            continue
        if cur and line.startswith("+"):
            added.append((cur, ln, line[1:]))
            touched[cur].add(ln)
            ln += 1
    return added, touched


def scan_smells(added):
    smells, switches, comment, doc, total = defaultdict(list), defaultdict(list), 0, 0, 0
    in_doc, recent = {}, defaultdict(list)
    for path, ln, text in added:
        total += 1
        s = text.strip()
        q = text.count('"""') + text.count("'''")
        if in_doc.get(path):
            doc += 1
            if q % 2 == 1:
                in_doc[path] = False
        elif q % 2 == 1:
            doc += 1
            in_doc[path] = True
        elif q >= 2 and s.lstrip("rRbBuUfF").startswith(('"""', "'''")):
            doc += 1
        elif s.startswith("#") or s.startswith("//"):
            comment += 1
        for name, rx in SMELLS:
            if rx.search(text):
                smells[name].append((path, ln, s[:90]))
        if classify(path) == "test":
            for name, rx in TEST_SMELLS:
                if rx.search(text):
                    smells[name].append((path, ln, s[:90]))
        else:
            for name, rx in SWITCH_SMELLS:
                if rx.search(text):
                    switches[name].append((path, ln, s[:90]))
        if SWALLOW_NEXT.match(text):
            for pln, ptext in recent[path]:
                if SMELLS[1][1].search(ptext):
                    smells["swallowed exception"].append((path, pln, ptext.strip()[:90]))
                    break
        recent[path] = (recent[path] + [(ln, text)])[-5:]
    return smells, switches, comment, doc, total


def module_side_effects(src, changed):
    """Module-level statements that run at import time and touch changed lines."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if not any(node.lineno <= l <= (node.end_lineno or node.lineno) for l in changed):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            calls = [n for n in ast.walk(node.value) if isinstance(n, ast.Call)]
            names = {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in calls}
            # Naming a file is not reading it: a path constant built from __file__ runs at import and touches nothing.
            if calls and not names <= {"getLogger", "compile", "Path", "frozenset", "set", "dict", "list", "tuple",
                                       "namedtuple", "TypeVar", "resolve", "dirname", "abspath", "join", "expanduser",
                                       "with_name", "with_suffix", "joinpath"}:
                out.append((node.lineno, ast.unparse(node)[:80]))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            out.append((node.lineno, ast.unparse(node)[:80]))
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
                continue        # the __main__ guard is the opposite of an import-time effect
            out.append((node.lineno, ast.unparse(node).splitlines()[0][:80]))
    return out


def leading_env(cmd):
    """(the VAR=value assignments that open a shell command, the interpreter it names)."""
    tokens, env = cmd.split(), {}
    while tokens and re.match(r"^[A-Za-z_]\w*=", tokens[0]):
        key, _, value = tokens.pop(0).partition("=")
        env[key] = value
    interp = tokens[0] if tokens and "python" in os.path.basename(tokens[0]) else sys.executable
    return env, interp


def check_commits(repo, base, head, cmd, tip_only=False, timeout=900, import_check=None):
    """Run cmd against a read-only export of every commit in base..head, or of the tip alone."""
    shas = git(repo, "log", "--reverse", "--format=%H", f"{base}..{head}").split()
    if tip_only:
        shas = shas[-1:]
    results = {}
    for sha in shas:
        tmp = tempfile.mkdtemp(prefix="review-ready-check-")
        try:
            tar = subprocess.run(["git", "-C", repo, "archive", "--format=tar", sha], capture_output=True, check=True)
            with tarfile.open(fileobj=__import__("io").BytesIO(tar.stdout)) as tf:
                try:
                    tf.extractall(tmp, filter="data")
                except TypeError:              # Python before 3.12 has no extraction filter
                    tf.extractall(tmp)
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            if import_check:
                # Does the command import THIS export, or the user's checkout through an
                # absolute PYTHONPATH or an editable install? Then every result is about other code.
                lead, interp = leading_env(cmd)
                probe = subprocess.run([interp, "-c", f"import {import_check} as m; print(m.__file__)"], cwd=tmp,
                                       capture_output=True, text=True, env=dict(env, **lead))
                where = probe.stdout.strip()
                if probe.returncode or not os.path.realpath(where).startswith(os.path.realpath(tmp) + os.sep):
                    results[sha[:10]] = ("did not run", [f"import check: {import_check} resolves to "
                                                         f"{where or probe.stderr.strip()[-160:]}, outside the export.",
                                                         "Use a RELATIVE PYTHONPATH (PYTHONPATH=src); an absolute one "
                                                         "is the user's checkout."])
                    continue
            try:
                r = subprocess.run(cmd, shell=True, cwd=tmp, capture_output=True, text=True, env=env, timeout=timeout)
            except subprocess.TimeoutExpired:
                results[sha[:10]] = ("timed out", [f"no result after {timeout}s: one test waits on something, or the "
                                                   "suite is slow. Use the fast subset the change touches."])
                continue
            # 126 and 127 are the shell saying the command never started. The export holds
            # tracked files only, so a relative `.venv/bin/python` is not there: that is a
            # fact about the command, and says nothing about the commit.
            state = "ok" if r.returncode == 0 else "did not run" if r.returncode in (126, 127) else "FAIL"
            out = r.stdout + r.stderr
            ran = re.search(r"^Ran (\d+) tests?", out, re.M)
            skipped = re.search(r"skipped=(\d+)", out)
            if state == "ok" and ran and (ran.group(1) == "0" or (skipped and skipped.group(1) == ran.group(1))):
                state = "vacuous"           # green with nothing run certifies nothing
            counts = f"{ran.group(1)} tests, {skipped.group(1) if skipped else 0} skipped" if ran else "test count not recognised"
            results[sha[:10]] = (state, out.strip().splitlines()[-6:] + [f"[{counts}]"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return results


def py_symbols(src):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [], []
    funcs, classes = [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append((node.name, node.lineno, node.end_lineno, cm.max_depth(node) if cm else 0))
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("__")]
            bases = [ast.unparse(b) for b in node.bases]
            classes.append((node.name, node.lineno, methods, bases))
    return funcs, classes


def defined_in(repo, ref, name):
    pat = r"^[[:space:]]*(async[[:space:]]+)?(def|class)[[:space:]]+" + re.escape(name) + r"[[:space:](:]"
    return subprocess.run(["git", "-C", repo, "grep", "-q", "-E", "-e", pat, ref, "--", "*.py"],
                          capture_output=True).returncode == 0


def usages(repo, head, name):
    out = git(repo, "grep", "-n", "-w", "-e", name, head, "--", ".", ok=True)
    uses, test_uses = 0, 0
    for line in out.splitlines():
        try:
            _, path, _, text = line.split(":", 3)
        except ValueError:
            continue
        if path.endswith(".ipynb"):
            continue
        if re.match(r"^\s*(async\s+)?(def|class)\s+" + re.escape(name) + r"\b", text):
            continue
        if re.match(r"^\s*(from\s+\S+\s+import|import\s)", text):
            continue
        if classify(path) == "test":
            test_uses += 1
        else:
            uses += 1
    return uses, test_uses


def rename_map(repo, base, head):
    """{new path: old path} for files git considers renamed."""
    out = {}
    for line in git(repo, "diff", "--name-status", "-M", base, head).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            out[parts[2]] = parts[1]
    return out


def head_functions(repo, head, skip_tests=True):
    """{fingerprint: [(path, name, line)]} and {name: [path]} for the head tree."""
    prints, names = defaultdict(list), defaultdict(list)
    for path in git(repo, "ls-tree", "-r", "--name-only", head).splitlines():
        if not path.endswith(".py") or (skip_tests and classify(path) == "test"):
            continue
        src = git(repo, "show", f"{head}:{path}", ok=True)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names[node.name].append(path)
                fp = cm.fingerprint(node)
                if fp:
                    prints[fp].append((path, node.name, node.lineno))
    return prints, names


def surface_changes(repo, base, head, paths, renames=None):
    """Public functions whose signature changed or vanished: callers this diff cannot see."""
    out, renames = [], renames or {}
    for path in paths:
        if not path.endswith(".py") or classify(path) != "source":
            continue
        old_path = renames.get(path, path)
        if old_path != path:
            out.append(dict(path=path, name="(module)", kind="moved: the import path changes for every caller",
                            before=old_path, after=path))
        before = cm.public_api(git(repo, "show", f"{base}:{old_path}", ok=True) or "")
        after = cm.public_api(git(repo, "show", f"{head}:{path}", ok=True) or "")
        for name, sig in sorted(before.items()):
            if name not in after:
                out.append(dict(path=path, name=name, kind="BREAKING: removed or renamed", before=sig, after=""))
            elif after[name] != sig:
                kind = ("widened (compatible, but a new default is a new switch)"
                        if cm.compatible_widening(sig, after[name]) else "BREAKING")
                out.append(dict(path=path, name=name, kind=kind, before=sig, after=after[name]))
    return out


def commit_report(repo, base, head):
    out = git(repo, "log", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", f"{base}..{head}")
    res = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        sha, subj, body = rec.strip("\n").split("\x1f", 2)
        body_lines = [l for l in body.splitlines() if l.strip() and not TRAILER.match(l)]
        ns = numstat_commit(repo, sha)
        nsw = numstat_commit(repo, sha, "-w", "--ignore-blank-lines")
        classes = sorted({classify(p) for p in ns})
        add = sum(a for a, d, b in ns.values())
        dele = sum(d for a, d, b in ns.values())
        logic = sum(a + d for a, d, b in nsw.values())
        churn = max(0, add + dele - logic)
        flags = []
        if len(subj) > 72:
            flags.append("subject over 72 chars")
        elif len(subj) > 65:
            flags.append("subject over 65 chars")
        if not body_lines:
            flags.append("no body")
        if GENERIC_SUBJECT.match(subj.strip()):
            flags.append("generic subject")
        if "generated" in classes and ("source" in classes or "test" in classes):
            flags.append("generated files mixed with code")
        if "notebook" in classes and len(classes) > 1:
            flags.append("notebook mixed with other files")
        if churn > 0 and logic > 0 and churn >= 0.1 * (churn + logic):
            flags.append("whitespace churn mixed with logic")
        if TOOL_MENTION.search(subj + " " + body):
            flags.append("mentions the tool")
        if not re.search(r"^Signed-off-by:", body, re.M):
            flags.append("unsigned: the owner signs once they have read it")
        if add + dele > PATCH_BUDGET:
            flags.append(f"{add + dele} lines, over the {PATCH_BUDGET}-line budget")
        res.append(dict(sha=sha[:10], subject=subj, subject_len=len(subj), body_lines=len(body_lines),
                        files=len(ns), added=add, deleted=dele, churn=churn, classes=classes, flags=flags))
    return res


def notebook_outputs(repo, head, path):
    raw = git(repo, "show", f"{head}:{path}", ok=True)
    if not raw:
        return None
    try:
        nb = json.loads(raw)
    except json.JSONDecodeError:
        return None
    cells = nb.get("cells", [])
    with_out = [c for c in cells if c.get("outputs")]
    chars = sum(len(json.dumps(c.get("outputs"))) for c in with_out)
    return dict(cells=len(cells), cells_with_outputs=len(with_out), output_chars=chars)


def audit(repo, base, head, check=None, tip_only=False, timeout=900, import_check=None):
    files = numstat(repo, base, head)
    files_w = numstat(repo, base, head, "-w", "--ignore-blank-lines")
    by_class = defaultdict(lambda: dict(files=0, added=0, deleted=0))
    churn = {}
    for path, (a, d, binary) in files.items():
        cls = "binary" if binary else classify(path)
        by_class[cls]["files"] += 1
        by_class[cls]["added"] += a
        by_class[cls]["deleted"] += d
        if not binary and path in files_w:
            aw, dw, _ = files_w[path]
            c = (a + d) - (aw + dw)
            if c > 0:
                churn[path] = c
    reviewable = sum(v["added"] + v["deleted"] for k, v in by_class.items()
                     if k in ("source", "test", "config", "docs", "other"))
    noise = sum(v["added"] + v["deleted"] for k, v in by_class.items()
                if k in ("generated", "notebook", "data", "binary"))

    code_paths = [p for p in files if classify(p) in ("source", "test") and not files[p][2]]
    added, touched = added_lines(repo, base, head, code_paths)
    config_paths = [p for p in files if classify(p) == "config" and not files[p][2]]
    added_all, _ = added_lines(repo, base, head, config_paths)
    smells, switches, comment, doc, total_added = scan_smells(added)

    over_budget, new_symbols = [], []
    dense, shapes, stale_docs, no_assert, comment_findings, costs = [], [], [], [], [], []
    risks = defaultdict(list)
    new_names, all_new_defs = set(), set()
    for path, ln, text in added:
        m = re.match(r"^(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)", text)
        if m and classify(path) == "source":
            if m.group(1) == "def":
                all_new_defs.add((path, m.group(2)))
            if not defined_in(repo, base, m.group(2)):
                new_names.add((path, m.group(1), m.group(2)))
    if rd:
        # Secrets and package sources do not care what language the file is in.
        text_paths = [p for p in files if not files[p][2] and not p.endswith(".py")
                      and classify(p) not in ("generated", "notebook")]
        for path, ln, text in added_lines(repo, base, head, text_paths)[0]:
            for _ln, name, group, shown in rd.scan_text([(ln, text)]):
                risks[(group, name)].append((path, ln, shown))
        brought = git(repo, "diff", "--name-only", "--diff-filter=AR", "-M", base, head, "--").splitlines()
        for path in rd.credential_files(brought):
            risks[("security", "credential file committed")].append((path, 1, "the file name alone says credential"))
    moved_from = rename_map(repo, base, head)
    for path in code_paths:
        if not path.endswith(".py"):
            continue
        src = git(repo, "show", f"{head}:{path}", ok=True)
        if not src:
            continue
        funcs, classes = py_symbols(src)
        ch = touched.get(path, set())
        # A re-quoted or re-indented line is not a line the branch WROTE: counting it made a
        # pure reformat of a pre-existing loop read as "this branch wrote a nested loop".
        squash = lambda t: re.sub(r"\s+", "", t).replace("'", '"').rstrip(",")
        base_lines = {squash(l) for l in git(repo, "show", f"{base}:{moved_from.get(path, path)}", ok=True).splitlines()}
        head_lines = src.splitlines()
        wrote = {l for l in ch if 0 < l <= len(head_lines) and squash(head_lines[l - 1]) not in base_lines}
        if classify(path) == "source":
            for ln, text in module_side_effects(src, ch):
                switches["import-time side effect (module-level statement)"].append((path, ln, text))
        try:
            tree = ast.parse(src)
        except SyntaxError:
            tree = None
        is_test = classify(path) == "test"
        for node in (ast.walk(tree) if tree is not None and cm else ()):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start, end = node.lineno, node.end_lineno or node.lineno
            if not any(start <= l <= end for l in ch):
                continue
            m_ = cm.measure(node)
            # How much of the function this branch actually wrote. A one-line
            # import edit inside a 150-line function does not put the other 149
            # lines in scope for this PR.
            share = round(100 * len([l for l in wrote if start <= l <= end]) / max(end - start + 1, 1))
            if m_["over_budget"] and not is_test:
                over_budget.append(dict(path=path, name=node.name, line=start, length=m_["length"],
                                        depth=m_["depth"], why=", ".join(m_["over_budget"]), share=share))
            if m_["dense"] and not is_test:
                dense.append(dict(path=path, name=node.name, line=start, why="; ".join(m_["dense"])))
            if m_["return_shape"] and not is_test:
                shapes.append(dict(path=path, name=node.name, line=start, why=m_["return_shape"]))
            # Only a loop the branch wrote: the sentence names a line, so ask whether that line is theirs.
            theirs = [c for c in m_["loop_costs"]
                      if any(int(n) in wrote for n in re.findall(r"at line (\d+)", c))]
            if theirs and not is_test:
                costs.append(dict(path=path, name=node.name, line=start, why=theirs[:3]))
            if m_["stale_doc_params"]:
                stale_docs.append(dict(path=path, name=node.name, line=start, params=m_["stale_doc_params"]))
            if is_test and node.name.startswith("test") and cm.asserts_nothing(node):
                no_assert.append(dict(path=path, name=node.name, line=start))
        if rd:
            skip = rd.docstring_lines(src)
            mine = [(ln, text) for p_, ln, text in added if p_ == path and ln not in skip]
            for ln, name, group, text in rd.scan(mine, is_test=is_test, src_hint=src):
                risks[(group, name)].append((path, ln, text))
            if not is_test:
                for ln, name, group, text in rd.ast_risks(src):
                    if ln in ch:
                        risks[(group, name)].append((path, ln, text))
        if ca:
            found, _stats = ca.scan_file(path, src)
            for f in found:
                if f["line"] in ch or any(abs(f["line"] - l) <= 1 for l in ch):
                    comment_findings.append(f)
        cls_info = {c[0]: c for c in classes}
        for p, kind, name in sorted(new_names):
            if p != path:
                continue
            uses, test_uses = usages(repo, head, name)
            entry = dict(path=path, kind=kind, name=name, uses=uses, test_uses=test_uses)
            if kind == "class" and name in cls_info:
                _, _, methods, bases = cls_info[name]
                entry["methods"] = len(methods)
                entry["bases"] = bases
            new_symbols.append(entry)

    duplicates, surface = [], []
    if cm:
        prints, names = head_functions(repo, head)
        renames = rename_map(repo, base, head)
        base_api = {}
        for path, name in sorted(all_new_defs):
            others = [p_ for p_ in names.get(name, []) if p_ != path]
            base_src = git(repo, "show", f"{base}:{renames.get(path, path)}", ok=True) or ""
            if path not in base_api:
                base_api[path] = set(re.findall(r"^(?:async\s+)?def\s+(\w+)", base_src, re.M))
            if name in base_api[path]:
                continue  # the def line changed; the function is not new
            if others and not name.startswith("__") and name not in ("main", "run", "setup", "handle"):
                duplicates.append(dict(path=path, name=name, kind="same name",
                                       where=", ".join(sorted(set(others))[:3])))
        new_lookup = {(p_, n_) for p_, n_ in all_new_defs}
        for fp, members in prints.items():
            if len(members) < 2:
                continue
            fresh = [m_ for m_ in members if (m_[0], m_[1]) in new_lookup]
            for path, name, line in fresh:
                twins = [f"{p_}:{n_}" for p_, n_, _l in members if (p_, n_) != (path, name)]
                if twins:
                    duplicates.append(dict(path=path, name=name, kind="same shape", where=", ".join(twins[:3])))
        surface = surface_changes(repo, base, head, [p_ for p_ in files if not files[p_][2]], renames)

    notebooks = {}
    for path in files:
        if classify(path) == "notebook":
            info = notebook_outputs(repo, head, path)
            if info:
                notebooks[path] = info

    commits = commit_report(repo, base, head)
    switch_files = [p for p in files if classify(p) in ("config", "data")]
    dead_keys, unset_flags = [], []
    if dead_config_keys is not None:
        added_config = [text for path, _ln, text in added_all
                        if classify(path) == "config" and not path.endswith(".json")]
        # Code and docs only. Including the config files themselves would make
        # every key match itself and the dead-key check would never fire.
        code_text = "\n".join(git(repo, "show", f"{head}:{p}", ok=True)
                              for p in git(repo, "ls-tree", "-r", "--name-only", head).splitlines()
                              if p.endswith((".py", ".sh", ".sql", ".md", ".ipynb", ".rst", ".txt")))
        dead_keys = dead_config_keys(added_config, code_text)
        added_code = "\n".join(t for _p, _l, t in added)
        unset_flags = flags_read_never_set(added_code, code_text)
    for c in commits:
        if ("config" in c["classes"] or "data" in c["classes"]) and ("source" in c["classes"]):
            c["flags"].append("config or data changed with code: a switch flip needs its own patch and test")
    checks = check_commits(repo, base, head, check, tip_only, timeout, import_check) if check else {}
    for c in commits:
        if c["sha"] in checks:
            c["check"], c["check_tail"] = checks[c["sha"]]

    hints = []
    if reviewable > PATCH_BUDGET:
        hints.append(f"{reviewable} reviewable lines: at {PATCH_BUDGET} lines a patch that is at least "
                     f"{math.ceil(reviewable / PATCH_BUDGET)} patches.")
    brought = set(git(repo, "diff", "--name-only", "--diff-filter=A", base, head, "--").splitlines())
    in_new = sum(a + d for p_, (a, d, b_) in files.items()
                 if p_ in brought and not b_ and classify(p_) in ("source", "test", "config", "docs", "other"))
    if reviewable > PR_BUDGET:
        hints.append(f"{reviewable} reviewable lines is more than one sitting ({PR_BUDGET}): {in_new} in new files, "
                     f"{reviewable - in_new} in edits to existing code. Rewrite before you split: AI-written code is "
                     f"usually two to four times the length the problem needs, and the series may keep at most half "
                     f"of it (the builder's size gate). Only what is still over budget after the rewrite becomes "
                     f"stacked PRs (plan field \"stack\"); a stack does not shrink the total.")
    if noise:
        hints.append(f"{noise} lines in generated files, notebooks, data or binaries: none of it is reviewed by eye. A data "
                     "file the code reads is a behaviour switch and gets its own patch (behaviour-switches.md); the rest "
                     "does not belong in the series.")
    churn_total = sum(churn.values())
    if churn_total:
        hints.append(f"{churn_total} lines of whitespace-only churn in {len(churn)} file(s): separate or drop.")
    if len(commits) == 1 and reviewable > PATCH_BUDGET:
        hints.append("one commit: cannot be bisected or reviewed in one sitting.")
    for name, items in smells.items():
        hints.append(f"{len(items)} added line(s) with {name}.")
    if total_added >= 40 and (comment + doc) > 0.25 * total_added:
        hints.append(f"{100 * (comment + doc) // total_added}% of added code lines are comments or docstrings; "
                     "most of them restate the code.")
    mine = [f for f in over_budget if f.get("share", 100) >= 30]
    touched_only = [f for f in over_budget if f.get("share", 100) < 30]
    if touched_only:
        hints.append(f"{len(touched_only)} oversized function(s) are pre-existing and merely touched by this branch "
                     "(under 30% of their lines): out of scope for this PR. Defer, or a preparatory patch behind "
                     "a characterization test.")
    if mine:
        hints.append(f"{len(mine)} function(s) this branch wrote or substantially changed exceed the budget "
                     f"({LENGTH_BUDGET} lines or {DEPTH_BUDGET} levels): flatten or split before the PR.")
    if False and over_budget:
        hints.append(f"{len(over_budget)} changed function(s) exceed the budget "
                     f"({LENGTH_BUDGET} lines or {DEPTH_BUDGET} levels).")
    if dense:
        hints.append(f"{len(dense)} changed function(s) are short but dense: a condition with too many terms "
                     "or too many paths for its size. Name the predicate.")
    if costs:
        hints.append(f"{len(costs)} function(s) this branch wrote hide a second loop (a nested loop, a membership "
                     "test against a list, a window recomputed per row). Say what n is. If it is small, say so "
                     "and move on; if not, the rewrite is its own measured patch (algorithm-smells.md).")
    if shapes:
        hints.append(f"{len(shapes)} changed function(s) return different shapes on different paths; "
                     "every caller now needs an `if`.")
    breaking = [x for x in surface if x["kind"].startswith("BREAKING")]
    moved = [x for x in surface if x["kind"].startswith("moved")]
    widened = [x for x in surface if x["kind"].startswith("widened")]
    if breaking:
        hints.append(f"{len(breaking)} public signature(s) BROKEN: " + ", ".join(x["name"] for x in breaking[:4])
                     + ". Callers outside this diff fail and cannot be seen from here: an API patch with its own "
                       "message, naming who calls it (engineering-sops.md).")
    if moved:
        hints.append(f"{len(moved)} module(s) moved: the import path changes for every caller, including anything "
                     "that imports by string.")
    if widened:
        hints.append(f"{len(widened)} public signature(s) widened compatibly: " + ", ".join(x["name"] for x in widened[:4])
                     + ". No caller breaks, but each new default is a new switch (behaviour-switches.md).")
    if duplicates:
        hints.append(f"{len(duplicates)} new function(s) duplicate something the tree already has: "
                     + ", ".join(f"{d['name']} ({d['kind']})" for d in duplicates[:4])
                     + ". Reuse it, and say so in the message.")
    if stale_docs:
        hints.append(f"{len(stale_docs)} docstring(s) document a parameter the signature does not have.")
    if no_assert:
        hints.append(f"{len(no_assert)} changed test(s) assert nothing: "
                     + ", ".join(t["name"] for t in no_assert[:4]) + ".")
    by_group = defaultdict(int)
    for (group, _name), items in risks.items():
        by_group[group] += len(items)
    for group, n in sorted(by_group.items(), key=lambda kv: -kv[1]):
        ref = {"security": "security.md", "data": "data-correctness.md", "perf": "algorithm-smells.md",
               "concurrency": "engineering-sops.md", "logging": "engineering-sops.md",
               "testing": "testing.md"}.get(group, "")
        hints.append(f"{n} added line(s) carry a {group} risk; trace each from input to sink ({ref}).")
    if comment_findings:
        kinds = defaultdict(int)
        for f in comment_findings:
            kinds[f["kind"]] += 1
        hints.append("comments in the change: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
                     + " (comments.md).")
    lonely = [s for s in new_symbols if s["uses"] <= 1]
    if lonely:
        hints.append(f"{len(lonely)} new top-level symbol(s) have one user or none outside tests.")
    one_method = [s for s in new_symbols if s.get("methods") is not None and s["methods"] <= 1]
    if one_method:
        hints.append(f"{len(one_method)} new class(es) with at most one method: those are functions.")
    nb_out = [p for p, i in notebooks.items() if i["cells_with_outputs"]]
    if nb_out:
        hints.append(f"{len(nb_out)} notebook(s) carry outputs.")
    if switch_files:
        hints.append(f"{len(switch_files)} config or data file(s) change: {', '.join(switch_files[:4])}. Each is a behaviour switch; "
                     "say in problem.md who reads it, and land a flip in its own patch with a test for both positions.")
    gcount = defaultdict(int)
    for name, items in switches.items():
        gcount[SWITCH_GROUP.get(name, "other")] += len(items)
    for group, n in sorted(gcount.items(), key=lambda kv: -kv[1]):
        hints.append(f"{n} added line(s) touch a {group} switch: trace who reads it and what the default does.")
    for key in dead_keys:
        hints.append(f"config key '{key}' is added but nothing reads it: a dead switch.")
    for flag in unset_flags:
        hints.append(f"env var '{flag}' is read but nothing in the tree sets it.")
    broken = [sha for sha, (state, _) in checks.items() if state == "FAIL"]
    unrun = [sha for sha, (state, _) in checks.items() if state in ("did not run", "timed out")]
    vacuous = [sha for sha, (state, _) in checks.items() if state == "vacuous"]
    if vacuous:
        hints.append(f"--check is green at {len(vacuous)} commit(s) with no test run, or every test skipped: tests that "
                     "skip without a database or a fixture prove nothing here. Say so; do not report it as passing.")
    if unrun:
        hints.append(f"--check did not start at {len(unrun)} commit(s): the command runs in an export of tracked "
                     "files only. Name the interpreter by absolute path and rely on no untracked file; "
                     "nothing is known about bisectability yet.")
    elif broken:
        hints.append(f"series breaks at commit {broken[0]} under --check; it cannot be bisected.")
    elif checks and tip_only:
        (state, tail), = checks.values()
        hints.append(f"--check-tip: the tests pass at the head {tail[-1]}. That is the baseline the rebuilt series is held to.")
    elif checks:
        hints.append(f"--check passes at every one of {len(checks)} commit(s).")
    if not hints:
        hints.append("Nothing mechanical to object to; the remaining questions are taste and correctness.")

    return dict(base=base, head=head, by_class=dict(by_class), reviewable=reviewable, noise=noise,
                churn=churn, commits=commits, smells={k: v for k, v in smells.items()},
                comment_lines=comment, docstring_lines=doc, added_code_lines=total_added,
                over_budget=over_budget, new_symbols=new_symbols, notebooks=notebooks, hints=hints,
                loop_costs=costs, dense=dense, return_shapes=shapes, stale_docs=stale_docs, no_assert=no_assert,
                duplicates=duplicates, surface=surface, comment_findings=comment_findings,
                risks={f"{g}|{n}": v for (g, n), v in risks.items()},
                switches={k: v for k, v in switches.items()}, switch_files=switch_files, checks=checks,
                dead_keys=dead_keys, unset_flags=unset_flags)


def render_ground(g):
    L = ["## Ground", "",
         f"- target: `{g['target']}` at {g['target_tip']}" + (f" ({g['note']})" if g["note"] else ""),
         f"- fork point: {g['fork'][:12]}. The plan's `\"base\"` is this sha and `\"onto\"` is `{g['target']}`."]
    if g["moved"]:
        L.append(f"- the target has moved {g['moved']} commit(s) since the fork; a test merge of the branch is: **{g['merge']}**")
        if g["conflicts"]:
            L.append("  - " + ", ".join(g["conflicts"][:8]) + ". The target changed the same lines. STOP: no overlay written at "
                     "the fork point can resolve this. Ask the owner to bring the branch up to date (merge or rebase, on "
                     "their branch) before the pass starts.")
    else:
        L.append("- the target has not moved since the fork")
    if g["merges_in_branch"]:
        L.append(f"- the branch contains {g['merges_in_branch']} merge commit(s); the fork point is the last merge from the target, which is fine")
    if len(g["authors"]) <= 1:
        L.append(f"- one author: {g['authors'][0] if g['authors'] else 'none recorded'}")
    if len(g["authors"]) > 1:
        L.append("- **more than one author**: " + "; ".join(g["authors"]) + ". A rebuilt patch is authored by whoever the plan "
                 "names (`\"author\"`): ask the user whose each patch is before anyone's work changes hands.")
    if g["upstream"]:
        L.append(f"- the branch is published: tracks `{g['upstream']}` ({g['upstream_state']}). Landing the series will need "
                 "`git push --force-with-lease`, which removes the old commits for everyone who has them: say so, and never push.")
    if not g["upstream"]:
        L.append("- not published: the branch tracks no upstream")
    return L + [""]


def render(r):
    L = [f"# Diff audit: {r['base']}..{r['head']}", ""]
    if r.get("ground"):
        L += render_ground(r["ground"])
    L += ["## Size", "", "| class | files | added | deleted | total |", "|---|---:|---:|---:|---:|"]
    for cls in ("source", "test", "config", "docs", "other", "generated", "notebook", "data", "binary"):
        v = r["by_class"].get(cls)
        if v:
            L.append(f"| {cls} | {v['files']} | {v['added']} | {v['deleted']} | {v['added'] + v['deleted']} |")
    L += ["", f"Reviewable (source, test, config, docs): **{r['reviewable']}** lines. "
             f"Noise (generated, notebook, data, binary): **{r['noise']}** lines.", ""]
    if r["churn"]:
        L += ["## Whitespace-only churn", ""]
        for p, c in sorted(r["churn"].items(), key=lambda kv: -kv[1]):
            L.append(f"- {p}: {c} lines")
        L.append("")
    L += [f"## Commits ({len(r['commits'])})", "",
          "| sha | subject | len | body lines | files | +/- | churn | check | classes | flags |",
          "|---|---|---:|---:|---:|---|---:|---|---|---|"]
    for c in r["commits"]:
        L.append(f"| {c['sha']} | {c['subject'][:60]} | {c['subject_len']} | {c['body_lines']} | {c['files']} | "
                 f"+{c['added']}/-{c['deleted']} | {c['churn']} | {c.get('check', '')} | {', '.join(c['classes'])} | {'; '.join(c['flags'])} |")
    L.append("")
    failed = [c for c in r["commits"] if c.get("check") in ("FAIL", "did not run", "timed out", "vacuous")]
    if failed:
        L += ["### What the check printed", ""]
        for c in failed[:4]:
            L.append(f"- {c['sha']} ({c['check']}):")
            L += [f"      {line[:160]}" for line in c.get("check_tail", [])]
        L.append("")
    L += ["## Smells in added code", "",
          f"Added code lines: {r['added_code_lines']}; comment lines: {r['comment_lines']}; "
          f"docstring lines: {r['docstring_lines']}.", ""]
    for name, items in r["smells"].items():
        L.append(f"- **{name}** ({len(items)})")
        for path, ln, text in items[:6]:
            L.append(f"  - {path}:{ln}: `{text}`")
        if len(items) > 6:
            L.append(f"  - ... and {len(items) - 6} more")
    if not r["smells"]:
        L.append("- none")
    L.append("")
    if r.get("surface"):
        L += ["## Public surface changed", "",
              "Callers outside this diff cannot be seen from here. Each of these is an API change:",
              "its own patch, a message naming who calls it, and a deprecation path if anyone does.", "",
              "| file | name | change | before | after |", "|---|---|---|---|---|"]
        for x in r["surface"]:
            L.append(f"| {x['path']} | {x['name']} | {x['kind']} | `{x['before']}` | `{x['after']}` |")
        L.append("")
    if r.get("duplicates"):
        L += ["## New code that duplicates the tree", "",
              "\"Search before writing\" is a rule the diff can be checked against. Same name means a",
              "second definition elsewhere; same shape means the body matches another function once",
              "names and constants are erased, which is what a pasted-and-renamed copy looks like.", "",
              "| file | function | kind | already at |", "|---|---|---|---|"]
        for d in r["duplicates"]:
            L.append(f"| {d['path']} | {d['name']} | {d['kind']} | {d['where']} |")
        L.append("")
    if r.get("risks"):
        L += ["## Risk patterns in added lines", "",
              "Triage, not verdicts: a regex sees one line. Each hit is where the trace from input",
              "to sink begins. A confirmed finding is its own patch or an open question, never a",
              "silent fix inside an unrelated patch.", ""]
        grouped = defaultdict(list)
        for key, items in r["risks"].items():
            group, name = key.split("|", 1)
            grouped[group].append((name, items))
        for group in ("security", "data", "perf", "concurrency", "logging", "testing"):
            if group not in grouped:
                continue
            L.append(f"- **{group}**")
            for name, items in sorted(grouped[group], key=lambda kv: -len(kv[1])):
                L.append(f"  - {name} ({len(items)})")
                for path, ln, text in items[:3]:
                    L.append(f"    - {path}:{ln}: `{text}`")
        L.append("")
    if r.get("dense") or r.get("return_shapes") or r.get("stale_docs") or r.get("no_assert") or r.get("loop_costs"):
        L += ["## Changed functions worth a second look", ""]
        for x in r.get("dense", []):
            L.append(f"- {x['path']}:{x['line']} `{x['name']}`: {x['why']}. Name the predicate or split the ladder.")
        for x in r.get("return_shapes", []):
            L.append(f"- {x['path']}:{x['line']} `{x['name']}`: {x['why']}.")
        for x in r.get("loop_costs", []):
            L.append(f"- {x['path']}:{x['line']} `{x['name']}`: " + "; ".join(x["why"]) + ".")
        for x in r.get("stale_docs", []):
            L.append(f"- {x['path']}:{x['line']} `{x['name']}`: docstring documents {', '.join(x['params'])}, "
                     "which the signature does not have. Which is right?")
        for x in r.get("no_assert", []):
            L.append(f"- {x['path']}:{x['line']} `{x['name']}`: asserts nothing; it passes if the code returns at all.")
        L.append("")
    if r.get("comment_findings"):
        L += ["## Comments in the change", ""]
        for f in r["comment_findings"][:15]:
            L.append(f"- {f['file']}:{f['line']}: {f['kind']}: {f['detail']}")
        if len(r["comment_findings"]) > 15:
            L.append(f"- ... and {len(r['comment_findings']) - 15} more")
        L.append("")
    if r["over_budget"]:
        L += ["## Changed Python functions over budget", "",
              "`written` is the share of the function's lines this branch touched. Under 30% the function is",
              "pre-existing and merely touched: out of scope for this PR unless a preparatory patch is planned.", "",
              "| file | function | line | length | depth | written |", "|---|---|---:|---:|---:|---:|"]
        for f in sorted(r["over_budget"], key=lambda f: (-f.get("share", 100), -f["length"])):
            L.append(f"| {f['path']} | {f['name']} | {f['line']} | {f['length']} | {f['depth']} "
                     f"| {f.get('share', 100)}% |")
        L.append("")
    if r["new_symbols"]:
        L += ["## New top-level symbols", "", "| file | kind | name | uses | in tests | note |", "|---|---|---|---:|---:|---|"]
        for s in r["new_symbols"]:
            note = []
            if s["uses"] == 0:
                note.append("no user outside tests")
            elif s["uses"] == 1:
                note.append("single user")
            if s.get("methods") == 0:
                note.append("no methods: a data holder; keyword arguments or a dict?")
            elif s.get("methods") == 1:
                note.append("one method: a function")
            if s.get("bases"):
                note.append("bases: " + ", ".join(s["bases"]))
            L.append(f"| {s['path']} | {s['kind']} | {s['name']} | {s['uses']} | {s['test_uses']} | {'; '.join(note)} |")
        L.append("")
    if r["switches"] or r["switch_files"] or r["dead_keys"] or r["unset_flags"]:
        L += ["## Behaviour switches touched", "",
              "Each is something that changes what the code does without being logic in",
              "the diff. Trace who reads it and what the default does before pruning.", ""]
        for p in r["switch_files"]:
            L.append(f"- **config or data file changed**: {p}")
        for key in r["dead_keys"]:
            L.append(f"- **config key nothing reads**: `{key}` (a dead switch: remove it, or read it and say what the shipped value does)")
        for flag in r["unset_flags"]:
            L.append(f"- **env var read but never set**: `{flag}` (nothing in the tree, CI, or docs sets it)")
        groups = defaultdict(list)
        for name, items in r["switches"].items():
            groups[SWITCH_GROUP.get(name, "other")].append((name, items))
        for group in ("config", "defaults", "sequence", "environment", "logging", "tests", "other"):
            if group not in groups:
                continue
            total = sum(len(i) for _n, i in groups[group])
            L.append(f"- **{group}** ({total} line(s))")
            for name, items in sorted(groups[group], key=lambda kv: -len(kv[1])):
                L.append(f"  - {name} ({len(items)})")
                for path, ln, text in items[:3]:
                    L.append(f"    - {path}:{ln}: `{text}`")
                if len(items) > 3:
                    L.append(f"    - ... and {len(items) - 3} more")
        L.append("")
    if r["notebooks"]:
        L += ["## Notebooks", ""]
        for p, i in r["notebooks"].items():
            L.append(f"- {p}: {i['cells']} cells, {i['cells_with_outputs']} with outputs ({i['output_chars']} chars of output)")
        L.append("")
    L += ["## What a maintainer would say first", ""]
    L += [f"- {h}" for h in r["hints"]]
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--md", help="write the markdown report here as well as to stdout")
    ap.add_argument("--json", help="write the structured result here")
    ap.add_argument("--check", help="run this against a read-only export of EVERY commit (review mode: is the series bisectable?)")
    ap.add_argument("--check-tip", help="run this once, against an export of the head: the baseline before a pre-PR pass")
    ap.add_argument("--check-timeout", type=int, default=900, help="seconds before a check is reported as timed out")
    ap.add_argument("--import-check", help="package the check imports; must resolve inside the export")
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    if not ref_exists(repo, args.head):
        sys.exit(f"head {args.head} not found in {repo}")
    if args.base:
        if not ref_exists(repo, args.base):
            sys.exit(f"base {args.base} not found")
        base = git(repo, "merge-base", args.base, args.head).strip()
        label = args.base
    else:
        label, base = find_base(repo, args.head)
    result = audit(repo, base, args.head, check=args.check or args.check_tip, tip_only=bool(args.check_tip),
                   timeout=args.check_timeout, import_check=args.import_check)
    result["base_label"] = label
    result["base"] = f"{label} ({base[:10]})"
    result["ground"] = ground(repo, label, base, args.head)
    text = render(result)
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        with open(args.md, "w") as fh:
            fh.write(text)
        hints = text[text.index("## What a maintainer would say first"):] if "## What a maintainer" in text else ""
        print(f"report written to {args.md} ({len(text.splitlines())} lines)\n\n" + "\n".join(render_ground(result["ground"])) + "\n" + hints)
    else:
        print(text)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as fh:
            json.dump(result, fh, indent=2, default=list)


if __name__ == "__main__":
    main()
