#!/usr/bin/env python3
"""build_series.py: rebuild a branch as a bisectable patch series, then land it.

usage: build_series.py PLAN.json            build the series beside the branch
       build_series.py PLAN.json --land     move the branch onto the built series
       options: [--restart] [--force] [--keep-worktree] [--no-check] [--dry-run] [--self-test]

Two acts, on purpose. BUILD commits every patch on a detached HEAD in a private
worktree under <repo>/.review-ready/, runs the check after each commit, and records
the result at refs/review-ready/series/<source>. It creates no branch, deletes no
branch, and never touches the user's branch or checkout, so an interrupted pass
leaves them exactly where they were. LAND is the single moment the user's branch
moves, after the series has been reviewed, and it writes the undo beside the plan.

The plan is JSON. Relative paths inside it resolve against the plan's directory.

{
  "repo": "..",                        # git repo; default: the plan's directory
  "base": "<sha>",                     # the FORK POINT: `git merge-base <onto> <source>`
  "onto": "origin/main",               # the branch the PR targets. When it is ahead of `base` the
                                       # finished series is rebased onto it, the check run at every commit
  "source": "feature/x",               # the user's branch; omit in write mode
  "branch": "feature/y",               # write mode only: the new branch that receives the series
  "check": "python3 -m unittest -q",   # run in the worktree after every commit; leading VAR=value
                                       # assignments are honoured (PYTHONPATH=src, RELATIVE, never absolute)
  "check_timeout": 900,                # seconds; a hung suite is "timed out", not a hang
  "import_check": "acme",           # package the tests import; must resolve INSIDE the worktree
  "python": "/abs/path/python",        # interpreter for the compile floor when `check` names none
  "sign_off": false,                   # true only when the user asked: a sign-off is the owner
                                       # certifying code they have read
  "out": "series",                     # directory for git format-patch output
  "stack": [{"branch": "feature/x-1-prep", "through": 3}],
                                       # stacked PRs: branch k ends at patch `through`; the source
                                       # branch is the top of the stack. Created by --land
  "scope_ok": {"tests/test_old.py": "characterization test for load(), which patch 4 refactors"},
  "overlay_ok": {"pipeline/loader.py": "patch 3 deliberately moves the guard patch 2 added into validate()"},
                                       # silence the "a later overlay drops what an earlier patch added"
                                       # warning for one path, with the reason
                                       # files the user's branch never touched; each needs its reason
  "shrink": 0.5,                       # the series keeps at most this share of the branch's reviewable lines,
                                       # in total and per file (see "The size gate"); above 0.5 needs
  "shrink_reason": "<reason>",         # this, which also goes in the PR description
  "size_ok": {"acme/schema.py": "generated from schema.sql; reviewed there"},
                                       # files exempt from the size gate, each with its reason
  "allow_vacuous_check": "<reason>",   # accept a check that ran zero tests, or skipped them all
  "no_check_reason": "<reason>",       # a plan with no `check`: say why, or the build refuses
  "patches": [
    {
      "name": "loader-uppercase-tickers",
      "message": "msgs/01-loader-uppercase-tickers.txt",
      "author": "Carol Colleague <carol@example.com>",       # a colleague's work keeps their name
      "paths": ["pipeline/loader.py"],                       # whole files as they are on `source`
      "delete": ["pipeline/old.py"],                          # remove files
      "overlay": {"pipeline/loader.py": "overlays/01/pipeline/loader.py"},  # a version you wrote
      "patch": "hunks/01.patch",                              # a unified diff (git apply --index --recount)
      "check": true,                                          # false skips the check for this patch
      "budget": 150,                                          # warn when added+deleted exceeds this. Write mode
                                                              # requires it and stops the build over it, unless
      "budget_ok": "<reason>",                                # this says why the patch cannot be smaller
      "expect_fail_before": "python3 -m unittest tests.test_loader.LoadPricesTest.test_x",
                                                              # must FAIL on the parent's code and PASS with the patch
      "mutate": {"file": "pipeline/loader.py", "find": "> 0", "replace": ">= 0",
                 "test": "python3 -m unittest tests.test_loader"}
                                                              # a characterization test must FAIL when the code is broken
    }
  ]
}

`base` and `onto`. Whole files taken from the branch carry the branch's view of
every line in them. Built on a base that has moved since the fork, such a patch
silently backs out whatever landed in between. So `base` must be the fork point;
the series is built there and then rebased onto `onto`. `git fetch` moves
origin/main and never main, so a local `onto` that is behind its remote is
refused too. A rebase conflict means the target changed the same lines; no
overlay written at the fork point can resolve that, so the build stops and the
owner brings their branch up to date (merge or rebase), and the pass restarts.

Where files come from. `refs/review-ready/original/<source>` is the branch as the
user left it, written when the first build starts. `paths` and the residual
always read it, so every review round is measured against the user's branch,
before and after landing. A branch whose code has changed since (new commits,
an amended file) is not guessed at: the build stops and asks for --restart,
which makes the current tip the new original and keeps the old one.

Gates the builder applies, because each was once a false pass:
  * the worktree is cleaned of untracked files and bytecode before every check;
  * `import_check` runs with the check's own environment and must resolve inside
    the worktree, so an absolute PYTHONPATH (the user's checkout) is caught;
  * exit 126/127 is "did not run"; a check that ran zero tests, or skipped them
    all, is vacuous; both stop the build;
  * `expect_fail_before` must fail on the parent AND pass with the patch, so a
    test name that does not exist, or a missing runner, proves nothing;
  * every changed .py file compiles, even with no `check`;
  * a file the user's branch never touched needs `scope_ok`;
  * a later overlay that drops lines an earlier patch added is reported;
  * the original branch's own tests are run against the rebuilt code, and each
    failure must be a deferred feature's test or a change made on purpose.
  * the series keeps at most `shrink` of the branch (the size gate, below).

The size gate. A series that reproduces the branch line for line has been
split, not made reviewable. After the build, the lines changed against the fork
point in source, tests, config and docs, ignoring whitespace and blank lines,
are compared file by file with the user's branch: the series may keep at most
`shrink` of them in total and in every file the branch changed by 20 lines or
more, unless the file is in `size_ok`. A branch under 100 such lines is not
gated. A Python file also fails, as CRAMMED, when the series has more lines
holding two statements, or more lines past 120 characters, than the branch
had: that halves the line count and nothing else. The table goes to size.md; a
failed gate fails the build and --land refuses the series. Stacking does not
help: the gate measures the whole series.

The residual is written next to the plan as residual.patch, as
`git diff <original> <series>`: a `-` line is something the user's branch had
and the series does not; a `+` line is something the series added. It must
equal deferred.md plus the refactors, line for line.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_diff import classify  # noqa: E402  one definition of "reviewable" for the audit and the gate

SHRINK = 0.5          # the series keeps at most this share of the branch's reviewable lines
SHRINK_FLOOR = 100    # a branch with fewer reviewable lines is not gated: a 30-line fix is not halved
FILE_FLOOR = 20       # nor is one file the branch changed by fewer lines
LONG_LINE = 120       # a line past this that the branch did not have was run together to save lines
REVIEWED = ("source", "test", "config", "docs", "other")

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
# Files an owner expects to find in their working directory even when the series
# does not track them: a pruned notebook or sample file should not vanish.
KEEP_LOCAL = re.compile(r"\.(ipynb|csv|tsv|parquet|feather|xlsx?|db|sqlite3?|pkl|pickle|npy|npz|h5|json|png|jpe?g|pdf)$", re.I)

NOT_RUN_HELP = ("The command runs in a private worktree that holds tracked files only: name the\n"
                "interpreter by absolute path (<repo>/.venv/bin/python, not .venv/bin/python) and\n"
                "rely on no untracked file.")


def check_env(extra=None):
    """Environment for a check run: never read or write stale bytecode.

    The worktree path is reused across patches, and some interpreters keep
    __pycache__ outside the tree (Apple's python3 uses
    ~/Library/Caches/com.apple.python/<abs path>), so a check can pass against
    bytecode compiled from the previous patch. A false pass here is worse than
    no check: it certifies a commit that does not build.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = tempfile.mkdtemp(prefix="review-ready-pyc-")
    env.update(extra or {})
    return env


def run(args, cwd=None, ok=False):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0 and not ok:
        sys.exit(f"$ {' '.join(args)}\n{r.stdout}{r.stderr}".rstrip())
    return r


def git(repo, *args, ok=False):
    return run(["git", "-C", repo, *args], ok=ok).stdout


def ref_exists(repo, ref):
    return run(["git", "-C", repo, "rev-parse", "--verify", "-q", ref + "^{commit}"], ok=True).returncode == 0


def sha(repo, ref):
    return git(repo, "rev-parse", ref + "^{commit}").strip()


def tree(repo, ref):
    return git(repo, "rev-parse", ref + "^{tree}").strip()


def shell(cmd, cwd, timeout, extra_env=None):
    """Run a check command. Returns (state, output): ok | failed | did not run | timed out."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                           env=check_env(extra_env), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = "".join(o if isinstance(o, str) else (o or b"").decode(errors="replace") for o in (exc.stdout, exc.stderr))
        return "timed out", out
    out = r.stdout + r.stderr
    if r.returncode in (126, 127):          # the shell never started the command
        return "did not run", out
    return ("ok" if r.returncode == 0 else "failed"), out


def last_line(out):
    lines = out.strip().splitlines()
    return lines[-1][:110] if lines else "no output"


def test_counts(out):
    """(tests run, skipped) read from a unittest or pytest summary; None when neither is recognised."""
    m = re.search(r"^Ran (\d+) tests?", out, re.M)
    if m:
        sk = re.search(r"skipped=(\d+)", out)
        return int(m.group(1)), int(sk.group(1)) if sk else 0
    if re.search(r"\b\d+ (passed|failed|skipped|errors?|deselected)\b", out) or "no tests ran" in out:
        n = lambda word: sum(int(x) for x in re.findall(rf"\b(\d+) {word}\b", out.strip().splitlines()[-1]))
        skipped = n("skipped")
        return n("passed") + n("failed") + n("errors?") + skipped, skipped
    return None


def errored_not_asserted(out):
    """True when a test run failed by error (import, name, missing runner), not by assertion."""
    if re.search(r"No module named|ModuleNotFoundError|ImportError|AttributeError: .*has no attribute|not found", out):
        return True
    return bool(re.search(r"errors=\d+", out)) and not re.search(r"failures=\d+", out)


def split_env(check):
    """(leading VAR=value assignments as a dict, the first real token) of a shell command."""
    try:
        tokens = shlex.split(check or "")
    except ValueError:
        tokens = (check or "").split()
    env = {}
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        key, _, value = tokens.pop(0).partition("=")
        env[key] = value
    return env, (tokens[0] if tokens else None)


def check_message(path, idx, sign_off):
    with open(path) as fh:
        lines = fh.read().rstrip("\n").split("\n")
    subject = lines[0] if lines else ""
    # Two things a message file may not carry, because the builder commits it verbatim.
    if not sign_off and any(re.match(r"^Signed-off-by:", l) for l in lines):
        sys.exit(f"message {idx:02d} ({os.path.basename(path)}) carries a Signed-off-by line. The owner signs, after "
                 'reading the series (git rebase --signoff). Remove the line, or set "sign_off": true if they asked.')
    if any(l.strip() == "---" for l in lines):
        sys.exit(f"message {idx:02d} ({os.path.basename(path)}) has a '---' line. On a pushed branch nothing cuts it: the "
                 "note below it lands in history for ever. Review notes go in the PR conversation.")
    warn = []
    if not subject.strip():
        warn.append("empty subject")
    if len(subject) > 65:
        warn.append(f"subject is {len(subject)} chars; keep it near 60")
    if subject.rstrip().endswith("."):
        warn.append("subject ends with a period")
    if ":" not in subject:
        warn.append("subject has no 'area:' prefix")
    if len(lines) > 1 and lines[1].strip():
        warn.append("no blank line after the subject")
    if not [l for l in lines[2:] if l.strip()]:
        warn.append("no body: the message must say what was wrong and why this fix")
    # Trailers are parsed by tools, one per line: "Fixes: <sha> (\"subject\")" may not wrap.
    long = [l for l in lines[1:] if len(l) > 72 and "http" not in l
            and not re.match(r"^[A-Z][A-Za-z-]+: \S", l)]
    for l in long:
        warn.append(f"line {lines.index(l) + 1} is {len(l)} chars, over 72: {l[:60]!r}")
    if any(w in " ".join(lines).lower() for w in ("codex", "copilot", "chatgpt", "claude", "ai-generated")):
        warn.append("mentions the tool")
    # A message that never says how the change was tested leaves the reviewer
    # to take the claim on trust, which is the one thing a maintainer will not do.
    body_text = " ".join(lines[1:]).lower()
    if not re.search(r"\btest(s|ed|ing)?\b|\bverif|\bfails? on\b|\bpasses\b|no functional change", body_text):
        warn.append("body never says how this was tested")
    for w in warn:
        print(f"warning: message {idx:02d} ({os.path.basename(path)}): {w}")
    return subject


def reviewable_lines(top, a, b):
    """{path: lines changed} for the files a reader reviews by eye.

    Whitespace and blank lines are ignored on both sides, so pruning churn
    alone cannot pass the size gate; only less code can.
    """
    out = {}
    for line in git(top, "diff", "--numstat", "--no-renames", "-w", "--ignore-blank-lines", a, b, "--").splitlines():
        add, dele, path = line.split("\t", 2)
        if add != "-" and classify(path) in REVIEWED:
            out[path] = int(add) + int(dele)
    return out


def cramming(top, ref, path):
    """(statements that share a line with another, lines past LONG_LINE) in a Python file at `ref`.

    The size gate counts lines. Joining statements with `;`, putting a body on its `if` line, or
    running several lines into one long one halves the count and saves the reader nothing, so the
    gate compares these two numbers with the branch's and fails a file that gained either.
    """
    if not path.endswith(".py"):
        return 0, 0
    shown = run(["git", "-C", top, "show", f"{ref}:{path}"], ok=True)
    try:
        tree = ast.parse(shown.stdout) if shown.returncode == 0 else None
    except SyntaxError:
        tree = None
    if tree is None:
        return 0, 0
    starts = Counter(n.lineno for n in ast.walk(tree) if isinstance(n, ast.stmt))
    return (sum(c - 1 for c in starts.values() if c > 1),
            sum(1 for line in shown.stdout.splitlines() if len(line) > LONG_LINE))


def size_gate(top, base, original, series, plan):
    """The series against the branch, file by file and in total. Returns (passed, markdown)."""
    shrink, size_ok, scope_ok = plan.get("shrink", SHRINK), plan.get("size_ok") or {}, plan.get("scope_ok") or {}
    before, after = reviewable_lines(top, base, original), reviewable_lines(top, base, series)
    total_b = sum(n for p, n in before.items() if p not in size_ok)
    total_a = sum(n for p, n in after.items() if p not in size_ok)
    gated = total_b >= SHRINK_FLOOR
    rows, over = [], []
    for path in sorted(set(before) | set(after), key=lambda p: (-before.get(p, 0), p)):
        b, a = before.get(path, 0), after.get(path, 0)
        if path in size_ok:
            verdict = f"kept: {size_ok[path]}"
        elif not b:
            verdict = "added by the series" + (f": {scope_ok[path]}" if path in scope_ok else "")
        elif gated and b >= FILE_FLOOR and a > shrink * b:
            verdict = f"OVER: at most {int(shrink * b)}"
            over.append(path)
        else:
            (joined_b, long_b), (joined_a, long_a) = cramming(top, original, path), cramming(top, series, path)
            crammed = [f"{joined_a - joined_b} more statement(s) share a line than on the branch"] * (joined_a > joined_b) \
                + [f"{long_a - long_b} more line(s) run past {LONG_LINE} characters"] * (long_a > long_b)
            verdict = "CRAMMED: " + ", ".join(crammed) if gated and crammed else "ok"
            if verdict != "ok":
                over.append(path)
        rows.append(f"| `{path}` | {b} | {a} | {verdict} |")
    pct = f"{100 * total_a // total_b}%" if total_b else "-"
    if not gated:
        result = f"not gated: the branch has under {SHRINK_FLOOR} reviewable lines outside `size_ok`"
    elif total_a > shrink * total_b or over:
        result = "FAILED"
    else:
        result = "passed"
    md = ["# Size: the series against the branch", "",
          f"Lines changed against the fork point, ignoring whitespace and blank lines, in source, tests, config "
          f"and docs. The series keeps at most {shrink:.0%} (`shrink`) of the branch: in total, and in every file "
          f"the branch changed by {FILE_FLOOR} lines or more. A Python file is CRAMMED when the series has more "
          f"statements sharing a line, or more lines past {LONG_LINE} characters, than the branch had. A file "
          f"listed in `size_ok` is exempt, with its reason.",
          "", "| file | branch | series | verdict |", "|---|---:|---:|---|", *rows, "",
          f"Total outside `size_ok`: {total_b} -> {total_a} ({pct}). Gate: **{result}**."]
    if result == "FAILED":
        if any("| CRAMMED" in row for row in rows):
            md += ["", "A CRAMMED file reached its line count by joining statements or lines: fewer lines, the "
                   "same amount to read. Write it out one statement to a line and shrink it for real."]
        md += ["", "Rewrite each OVER file smaller (SKILL.md step 3, references/minimal-code.md). A file that "
               "truly cannot shrink goes in the plan's `size_ok` with the reason a reviewer would accept, and the "
               "reason goes in the PR description. Splitting into a stack does not change the total."]
    return result != "FAILED", "\n".join(md) + "\n"


def fresh_target(top, name):
    """Refuse a local branch that is behind its remote: `git fetch` never moves it."""
    if not name or not ref_exists(top, f"refs/heads/{name}"):
        return
    upstream = git(top, "rev-parse", "--abbrev-ref", f"{name}@{{upstream}}", ok=True).strip()
    candidates = [upstream] if upstream else [r for r in git(top, "for-each-ref", "--format=%(refname:short)",
                                                           f"refs/remotes/*/{name}").split() if r]
    for remote in candidates:
        behind = git(top, "rev-list", "--count", f"{name}..{remote}", ok=True).strip()
        if behind and behind != "0":
            sys.exit(f"{name!r} is {behind} commit(s) behind {remote!r}. `git fetch` moves {remote}, never {name}, so a "
                     f"series built on {name} lands on a stale tree.\nUse the remote-tracking ref: "
                     f'"onto": "{remote}", "base": "<git merge-base {remote} <source>>"')


def resolve_files_from(top, source, restart):
    """The ref whole files and the residual come from: the branch as the user left it."""
    original = f"refs/review-ready/original/{source}"
    landed = f"refs/review-ready/landed/{source}"
    tip = sha(top, source)
    if not ref_exists(top, original):
        git(top, "update-ref", original, tip)
        return original
    if sha(top, original) == tip:
        return original
    if ref_exists(top, landed) and tree(top, landed) == tree(top, source):
        return original                 # a review round after landing: same code, perhaps signed or reworded
    if restart:
        n = len(git(top, "for-each-ref", "--format=%(refname)", f"refs/review-ready/superseded/{source}/").split()) + 1
        git(top, "update-ref", f"refs/review-ready/superseded/{source}/{n}", sha(top, original))
        git(top, "update-ref", original, tip)
        for stale in (landed, f"refs/review-ready/series/{source}", f"refs/review-ready/series-of/{source}"):
            git(top, "update-ref", "-d", stale, ok=True)
        print(f"  restart: {source} at {tip[:10]} is the new original; the previous one is kept at "
              f"refs/review-ready/superseded/{source}/{n}")
        return original
    sys.exit(f"{source} has changed since this pass first read it ({sha(top, original)[:10]} -> {tip[:10]}), and not "
             "by landing a series: new commits, or amended code.\nA rebuild takes whole files from the original, so "
             "that work would be dropped without a word. Either\n  --restart   the current tip is the new original "
             "(overlays written from the old one are stale: rewrite them), or\n  put the branch back first:  "
             f"git checkout -B {source} {original}")


def build(args, plan, plan_dir, resolve):
    repo = resolve(plan.get("repo", "."))
    top = git(repo, "rev-parse", "--show-toplevel").strip()
    base, source, branch, onto = plan["base"], plan.get("source"), plan.get("branch"), plan.get("onto")
    check = None if args.no_check else plan.get("check")
    timeout = plan.get("check_timeout", 900)
    sign_off = plan.get("sign_off", False)
    out_dir = resolve(plan.get("out", "series"))
    patches = plan.get("patches") or sys.exit("plan has no patches")
    check_vars, first_token = split_env(check)
    python = (first_token if first_token and "python" in os.path.basename(first_token) else None) \
        or plan.get("python") or sys.executable

    for ref in (base, source, onto):
        if ref and not ref_exists(top, ref):
            sys.exit(f"ref not found: {ref}")
    if source and branch and not plan.get("in_place"):
        if branch == source:
            sys.exit('"branch" is the NEW branch a write-mode series goes to; "source" is the user\'s branch being '
                     'rebuilt. For a pre-PR pass give "source" alone: the series is kept at '
                     f"refs/review-ready/series/{source} until --land moves the branch.")
    if source:
        branch = None                   # pre-PR: the series lives in a ref, no branch is made or deleted
    elif not branch:
        sys.exit('write mode (no "source") needs "branch": the new branch that receives the series')
    if branch and ref_exists(top, f"refs/heads/{branch}") and not args.force:
        sys.exit(f"branch {branch} already exists; --force replaces it (its tip is saved first)")
    if not check and not plan.get("no_check_reason") and not args.no_check:
        sys.exit('the plan has no "check". A series nothing ran is not landed on anyone\'s branch: add the test '
                 'command, or say why there is none in "no_check_reason" (it goes in the hand-back).')
    for i, p in enumerate(patches, 1):
        if "message" not in p:
            sys.exit(f"patch {i} has no message")
        if not os.path.exists(resolve(p["message"])):
            sys.exit(f"patch {i}: message file missing: {p['message']} (messages are step 5; the build comes after)")
        if not any(k in p for k in ("paths", "delete", "overlay", "patch")):
            sys.exit(f"patch {i} has no paths/delete/overlay/patch")
        if p.get("paths") and not source:
            sys.exit(f"patch {i} uses paths but the plan has no source branch; use overlay or patch in write mode")
        if not source and not (isinstance(p.get("budget"), int) and p["budget"] > 0):
            sys.exit(f'patch {i} has no "budget". Write mode has no branch to measure the series against, so each '
                     "patch states the lines (added plus deleted) it may take, and the build holds it to that.")
        if "budget_ok" in p and not str(p["budget_ok"]).strip():
            sys.exit(f'patch {i}: "budget_ok" needs the reason the patch is over its budget')
    stack = plan.get("stack") or []
    ends = [s.get("through") for s in stack]
    if stack and (ends != sorted(set(ends)) or not all(isinstance(e, int) and 1 <= e < len(patches) for e in ends)):
        sys.exit('"stack": each "through" is a patch number, ascending, below the last patch (the source branch is the top)')
    shrink = plan.get("shrink", SHRINK)
    if isinstance(shrink, bool) or not isinstance(shrink, (int, float)) or not 0 < shrink <= 1:
        sys.exit(f'"shrink" is the share of the branch the series may keep: a number above 0 and at most 1, not {shrink!r}')
    if shrink > SHRINK and not plan.get("shrink_reason"):
        sys.exit(f'"shrink" {shrink} keeps more than the default {SHRINK}: say why in "shrink_reason", which also '
                 "goes in the PR description")
    for key in ("size_ok", "scope_ok", "overlay_ok"):
        blank = [k for k, v in (plan.get(key) or {}).items() if not str(v).strip()]
        if blank:
            sys.exit(f'"{key}" needs a reason for every path; none given for: {", ".join(blank)}')

    for name in (onto, base):
        fresh_target(top, name)
    files_from = resolve_files_from(top, source, args.restart) if source else None
    if files_from:
        target = onto or base
        fork = git(top, "merge-base", target, files_from, ok=True).strip()
        if not fork:
            sys.exit(f"{target} and {source} share no history")
        if fork != sha(top, base):
            moved = git(top, "rev-list", "--count", f"{fork}..{target}").strip()
            sys.exit(f"base {base!r} is not where {source} forked from {target!r}: the fork point is {fork[:12]}, "
                     f"and {target} has {moved} commit(s) the branch has never seen.\n"
                     "Whole files taken from the branch would silently back that work out.\n"
                     f'Build at the fork point and land on today\'s tree:\n  "base": "{fork}", "onto": "{target}"\n'
                     "Overlays already written from another base are stale: rewrite them from the fork point's files.")

    if args.dry_run:
        print(f"would build from {base} in {top}" + (f", taking files from {source} as the user left it"
                                                     if source else f" onto new branch {branch} (write mode)"))
        for i, p in enumerate(patches, 1):
            check_message(resolve(p["message"]), i, sign_off)
            print(f"  {i:02d} {p.get('name', '')}: paths={p.get('paths', [])} delete={p.get('delete', [])} "
                  f"overlay={list(p.get('overlay', {}))} patch={p.get('patch')}")
        return

    # `.git` is a file in a linked worktree; ask git where the exclude file is.
    exclude = git(top, "rev-parse", "--git-path", "info/exclude").strip()
    exclude = exclude if os.path.isabs(exclude) else os.path.join(top, exclude)
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    if ".review-ready/" not in (open(exclude).read() if os.path.exists(exclude) else ""):
        with open(exclude, "a") as fh:
            fh.write("\n.review-ready/\n")

    wt = os.path.join(top, ".review-ready", "wt-" + (source or branch).replace("/", "_"))
    if os.path.exists(wt):
        git(top, "worktree", "remove", "--force", wt, ok=True)
        shutil.rmtree(wt, ignore_errors=True)
    git(top, "worktree", "prune")
    git(top, "worktree", "add", "-q", "--detach", wt, base)
    if os.path.exists(os.path.join(wt, ".gitmodules")):
        print("warning: this tree has submodules and the private worktree does not populate them. A check that "
              "imports from one fails here for that reason alone: say so in the hand-back.")

    def stop(msg):
        print(msg + f"\nNothing has moved. Worktree left at {wt}")
        sys.exit(1)

    import_check = plan.get("import_check")
    if check and not import_check:
        print('warning: no "import_check". Nothing proves the check imports THIS tree and not an editable install '
              "or an absolute PYTHONPATH pointing at the user's checkout.")
    def import_resolves():
        """True once `import_check` imports from inside the worktree; stops when it imports from anywhere else."""
        probe = ("import importlib,sys; m=importlib.import_module(%r); "
                 "sys.stdout.write(getattr(m,'__file__','') or '')" % import_check)
        try:
            r = subprocess.run([python, "-c", probe], cwd=wt, capture_output=True, text=True,
                               env=check_env(check_vars))
        except (FileNotFoundError, PermissionError):
            stop(f"import_check: the interpreter named in `check` ({python}) does not exist in the private "
                 f"worktree.\n{NOT_RUN_HELP}")
        where = (r.stdout or "").strip()
        if r.returncode != 0:
            return False, (r.stderr or "").strip()[-400:]
        if not os.path.realpath(where).startswith(os.path.realpath(wt) + os.sep):
            stop(f"import_check FAILED: {import_check!r} resolves to\n  {where}\nwhich is outside the worktree\n  {wt}\n"
                 "Every check would test a tree this series never touched: an absolute PYTHONPATH (the user's "
                 "checkout) or an editable install. Use a RELATIVE PYTHONPATH in `check`.")
        print(f"  import_check ok: {import_check} resolves inside the worktree")
        return True, ""

    # A package the series itself creates cannot import at the base: ask again after each patch.
    import_pending, import_error = bool(import_check and check), ""

    def ask_import():
        nonlocal import_pending, import_error
        if import_pending:
            resolved, import_error = import_resolves()
            import_pending = not resolved

    ask_import()

    rows, added_by, last_overlay = [], {}, {}
    for i, p in enumerate(patches, 1):
        name = p.get("name", f"patch-{i:02d}")
        for path in p.get("paths", []):
            git(wt, "checkout", files_from, "--", path)
        for path in p.get("delete", []):
            git(wt, "rm", "-q", "-r", "--", path)
        for dst, src in p.get("overlay", {}).items():
            dstp = os.path.join(wt, dst)
            before = open(dstp, errors="replace").read().splitlines() if os.path.exists(dstp) else []
            os.makedirs(os.path.dirname(dstp), exist_ok=True)
            shutil.copyfile(resolve(src), dstp)
            git(wt, "add", "--", dst)
            # copyfile carries no mode, and `git show ref:path` cannot: a nightly script
            # rebuilt through an overlay would lose its executable bit.
            mode = git(top, "ls-tree", files_from, "--", dst, ok=True).split(" ")[0] if files_from else ""
            if mode == "100755" or (not mode and os.access(resolve(src), os.X_OK)):
                git(wt, "update-index", "--chmod=+x", "--", dst)
            after = open(dstp, errors="replace").read().splitlines()
            # Overlays are whole files, so a fix made in patch k's overlay is silently
            # undone by patch k+1's unless it was carried forward. A line the later patch
            # rewrote is not a line it reverted: when every name and literal of the old
            # line is still somewhere in the new file, the change survived in another shape.
            if dst in added_by and dst not in (plan.get("overlay_ok") or {}):
                k, lines = added_by[dst]
                present = set(after)
                later = set(re.findall(r"[A-Za-z_]\w*|\"[^\"]*\"|'[^']*'", "\n".join(after)))
                gone = [l for l in lines if l.strip() and l not in present
                        and not set(re.findall(r"[A-Za-z_]\w*|\"[^\"]*\"|'[^']*'", l)) <= later]
                if gone:
                    print(f"warning: patch {i:02d}'s overlay of {dst} drops {len(gone)} line(s) that patch {k:02d} "
                          f"added, and no name or literal from them survives, e.g. {gone[0].strip()[:70]!r}. A fix "
                          "made to an earlier overlay must be carried into every later overlay of that file; a patch "
                          "that undoes its predecessor shows the series' own history. Deliberate (the line moved to "
                          'another file, or the patch reverts it on purpose): say so in the message and in "overlay_ok".')
            added_by[dst] = (i, [l for l in after if l not in set(before)])
        if p.get("patch"):
            git(wt, "apply", "--index", "--recount", "--whitespace=nowarn", resolve(p["patch"]))
        # Leftovers from the previous patch's check (a report.db a test wrote, a
        # cache directory) would let this patch pass on state it did not create.
        git(wt, "clean", "-xdfq", ok=True)
        staged = git(wt, "diff", "--cached", "--name-only").strip().splitlines()
        if not staged:
            stop(f"patch {i} ({name}) stages nothing.")
        efb = p.get("expect_fail_before")
        if efb and not args.no_check:
            non_test = [f for f in staged if not TEST_PATH.search(f)]
            for f in non_test:
                parent = run(["git", "-C", wt, "show", f"HEAD:{f}"], ok=True)
                target_path = os.path.join(wt, f)
                if parent.returncode == 0:
                    with open(target_path, "w") as fh:
                        fh.write(parent.stdout)
                elif os.path.exists(target_path):
                    os.remove(target_path)
            state, out = shell(efb, wt, timeout, None)
            for f in non_test:
                run(["git", "-C", wt, "checkout", "--", f], ok=True)
            git(wt, "clean", "-xdfq", ok=True)
            if state in ("did not run", "timed out"):
                stop(f"\npatch {i}/{len(patches)} ({name}): expect_fail_before {state}: {last_line(out)}\n{NOT_RUN_HELP}")
            if state == "ok":
                stop(f"\npatch {i}/{len(patches)} ({name}): expect_fail_before PASSED against the parent's code, so "
                     "the test does not test this change.")
            state_after, out_after = shell(efb, wt, timeout, None)
            git(wt, "clean", "-xdfq", ok=True)
            if state_after != "ok":
                stop(f"\npatch {i}/{len(patches)} ({name}): expect_fail_before fails WITH the patch too "
                     f"({last_line(out_after)}).\nIt proves nothing: a test name that does not exist and a missing "
                     "runner both \"fail on the parent\". Name a test this patch makes pass.")
            how = ("by ERROR, not by assertion: legitimate when the error is a name this patch adds, worthless "
                   "otherwise; read it (testing.md section 3)" if errored_not_asserted(out) else "by assertion")
            print(f"  {i:02d}/{len(patches)}  fails on the parent, passes here; on the parent it failed {how}: "
                  f"{last_line(out)}")
        subject = check_message(resolve(p["message"]), i, sign_off)
        cmd = ["commit", "-q", "-F", resolve(p["message"])]
        if sign_off:
            cmd.append("--signoff")
        if p.get("author"):
            cmd += ["--author", p["author"]]
        git(wt, *cmd)
        ask_import()
        ns = git(wt, "show", "--numstat", "--format=", "HEAD")
        add = dele = files = 0
        for line in ns.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0] != "-":
                add += int(parts[0]); dele += int(parts[1]); files += 1
        # The floor under everything: a series whose files do not compile is not landed,
        # with or without a test command.
        changed_py = [f for f in staged if f.endswith(".py") and os.path.exists(os.path.join(wt, f))]
        if changed_py and not args.no_check:
            try:
                r = subprocess.run([python, "-m", "py_compile", *changed_py], cwd=wt, capture_output=True,
                                   text=True, env=check_env())
            except (FileNotFoundError, PermissionError):
                stop(f"compile floor: interpreter {python} not found.\n{NOT_RUN_HELP}")
            if r.returncode != 0:
                stop(f"\npatch {i}/{len(patches)} ({name}) does not compile under {python}:\n"
                     f"{(r.stdout + r.stderr).strip()[-600:]}\nIf that interpreter is older than the tree's Python "
                     'floor, name the right one in "python".')
        if p.get("mutate") and not args.no_check:
            mu = p["mutate"]
            target_path = os.path.join(wt, mu["file"])
            original_text = open(target_path).read()
            if mu["find"] not in original_text:
                stop(f"\npatch {i} ({name}): mutate.find {mu['find']!r} is not in {mu['file']}")
            open(target_path, "w").write(original_text.replace(mu["find"], mu["replace"], 1))
            state, out = shell(mu["test"], wt, timeout, None)
            open(target_path, "w").write(original_text)
            git(wt, "clean", "-xdfq", ok=True)
            if state in ("did not run", "timed out"):
                stop(f"\npatch {i}/{len(patches)} ({name}): mutate.test {state}: {last_line(out)}\n{NOT_RUN_HELP}")
            if state == "ok":
                stop(f"\npatch {i}/{len(patches)} ({name}): the test still PASSES with {mu['file']} broken "
                     f"({mu['find']!r} -> {mu['replace']!r}).\nIt pins nothing, so the refactor it guards would be "
                     "an unpinned rewrite.")
            print(f"  {i:02d}/{len(patches)}  test is load-bearing: it fails when {mu['file']} is broken")
        status = "skipped"
        if check and p.get("check", True):
            state, out = shell(check, wt, timeout, None)
            if state == "did not run":
                stop(f"\ncheck did not run at patch {i}/{len(patches)} ({name}): {last_line(out)}\n{NOT_RUN_HELP}")
            if state == "timed out":
                stop(f"\ncheck timed out after {timeout}s at patch {i}/{len(patches)} ({name}). Use the fast subset "
                     'the series touches as "check", raise "check_timeout", and run the full suite once at the end.')
            # Vacuous before failed: unittest exits 5 on "NO TESTS RAN" since Python 3.12.
            counts = test_counts(out)
            if counts and (counts[0] == 0 or counts[0] == counts[1]) and not plan.get("allow_vacuous_check"):
                stop(f"\ncheck is VACUOUS at patch {i}/{len(patches)} ({name}): {counts[0]} test(s) ran, {counts[1]} "
                     "skipped. Green with nothing run certifies nothing (tests that skip without a database, a "
                     'discovery path that finds no tests). Fix the command, or state why in "allow_vacuous_check".')
            if state == "failed":
                stop(f"\ncheck FAILED at patch {i}/{len(patches)} ({name}): {subject}\n"
                     + "\n".join(out.strip().splitlines()[-25:]))
            status = f"ok ({counts[0]} tests" + (f", {counts[1]} skipped)" if counts and counts[1] else ")") \
                if counts else "ok (test count not recognised)"
        rows.append((i, subject, files, add, dele, status))
        print(f"  {i:02d}/{len(patches)}  +{add:<5}-{dele:<5} {files:>2} file(s)  check={status:<14} {subject}")
        if p.get("budget") and add + dele > p["budget"]:
            if source or p.get("budget_ok"):
                print(f"warning: patch {i:02d} is {add + dele} lines against a budget of {p['budget']}; split it"
                      + (f" (kept: {p['budget_ok']})" if p.get("budget_ok") else ""))
            else:
                stop(f"\npatch {i:02d} ({name}) is {add + dele} lines against its budget of {p['budget']}. In write "
                     "mode the budget is the size check: split the patch, or write it smaller "
                     '(references/minimal-code.md). If it truly cannot be either, give the reason in "budget_ok".')

    if import_pending:
        stop(f"import_check: cannot import {import_check!r} in the worktree with the check's environment "
             f"({check_vars or 'no assignments'}), at the base or after any patch:\n{import_error}\n"
             "On a src layout the check needs PYTHONPATH=src, relative, so it names the worktree's code.")

    series_tip = sha(wt, "HEAD")
    shas = git(wt, "rev-list", "--reverse", f"{base}..HEAD").split()

    residual, size_passed, size_md = "", True, ""
    if files_from:
        # Scope: a pre-PR pass cleans what the branch wrote. A file the branch never
        # touched is someone else's code, and the owner will be asked to defend it.
        theirs = set(git(top, "diff", "--name-only", base, files_from).split("\n"))
        ours = [f for f in git(wt, "diff", "--name-only", base, "HEAD").split("\n") if f]
        allowed = plan.get("scope_ok") or {}
        outside = [f for f in ours if f not in theirs and f not in allowed]
        if outside:
            stop("\nOUT OF SCOPE: the series changes file(s) the user's branch never touched:\n  " + "\n  ".join(outside)
                 + '\nThat is audit mode, or a deferred item. If a file truly belongs (a characterization test for code '
                 'the branch modifies), list it with its reason in "scope_ok".')
        residual = git(top, "diff", files_from, series_tip)
        residual_stat = git(top, "diff", "--stat", "--summary", files_from, series_tip)
        with open(os.path.join(plan_dir, "residual.patch"), "w") as fh:
            fh.write(residual)
        size_passed, size_md = size_gate(top, base, files_from, series_tip, plan)
        with open(os.path.join(plan_dir, "size.md"), "w") as fh:
            fh.write(size_md)

        # The per-patch check ran the PRUNED suite. Run the tests the user's branch
        # came with against the rebuilt code: that is the only place a behaviour that
        # only a pruned test covered can show.
        their_tests = [f for f in git(top, "ls-tree", "-r", "--name-only", files_from).split("\n") if TEST_PATH.search(f)]
        differs = [f for f in their_tests
                   if git(top, "rev-parse", f"{files_from}:{f}", ok=True) != git(wt, "rev-parse", f"HEAD:{f}", ok=True)]
        if check and differs:
            git(wt, "checkout", files_from, "--", *differs)
            state, out = shell(check, wt, timeout, None)
            git(wt, "reset", "-q", "--hard", "HEAD")
            git(wt, "clean", "-xdfq", ok=True)
            report = os.path.join(plan_dir, "original-tests.txt")
            with open(report, "w") as fh:
                fh.write(f"The user's own test files ({len(differs)} differ from the series) run against the rebuilt "
                         f"code: {state}\n\n{out}")
            if state == "ok":
                print(f"  the branch's original tests pass against the rebuilt code {test_counts(out) or ''}")
            else:
                print(f"  the branch's ORIGINAL tests against the rebuilt code: {state}: {last_line(out)}\n"
                      f"  Each failure is a deferred feature's test, or a behaviour this series changed: say which, one "
                      f"by one, in deferred.md and the hand-back. Full output: {report}")

    landed_on = base
    if onto and sha(top, onto) != sha(top, base):
        cmd = ["git", "-C", wt, "rebase", "-q"]
        if check:
            cmd += ["--exec", f"git clean -xdfq && {check}"]
        r = subprocess.run(cmd + [onto], capture_output=True, text=True, env=check_env())
        if r.returncode != 0:
            unmerged = git(wt, "diff", "--name-only", "--diff-filter=U", ok=True).split()
            stopped = git(wt, "log", "-1", "--format=%s", "REBASE_HEAD", ok=True).strip()
            subprocess.run(["git", "-C", wt, "rebase", "--abort"], capture_output=True)
            if unmerged:
                stop(f"\nthe series builds at the fork point but does not apply to {onto}: \"{stopped}\" conflicts in "
                     f"{', '.join(unmerged)}.\n{onto} changed the same lines after the branch forked. No overlay "
                     "written at the fork point can resolve that, and the resolution needs someone who knows both "
                     f"changes: ask the owner to bring the branch up to date (git merge {onto}, or rebase, on their "
                     "branch), then restart the pass from step 0. A merge from the target in the branch is fine.")
            stop(f"\nthe series passes at the fork point and FAILS on {onto}:\n"
                 + "\n".join((r.stdout + r.stderr).strip().splitlines()[-25:])
                 + f"\nSomething that landed on {onto} since the fork breaks it.")
        landed_on = onto
        series_tip = sha(wt, "HEAD")
        shas = git(wt, "rev-list", "--reverse", f"{onto}..HEAD").split()
        print(f"  rebased onto {onto}" + (": the check passes at every commit there too" if check else ""))

    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.endswith(".patch"):
            os.remove(os.path.join(out_dir, f))
    git(wt, "format-patch", "-q", "-o", out_dir, f"{landed_on}..HEAD")
    git(top, "worktree", "remove", "--force", wt, ok=True) if not args.keep_worktree else None
    git(top, "worktree", "prune")

    print(f"\nbuilt {len(rows)} patch(es), +{sum(r[3] for r in rows)}/-{sum(r[4] for r in rows)}; "
          f"patches written to {out_dir}")
    if stack:
        prev = 0
        for s in stack + [{"branch": source or branch, "through": len(shas)}]:
            a = sum(r[3] + r[4] for r in rows[prev:s["through"]])
            print(f"  stack: {s['branch']:<40} patches {prev + 1}-{s['through']}  {a} line(s)")
            prev = s["through"]
    if source:
        git(top, "update-ref", f"refs/review-ready/series/{source}", series_tip)
        git(top, "update-ref", f"refs/review-ready/series-of/{source}", sha(top, files_from))
        if residual.strip():
            print(f"\nresidual, as `git diff <your branch as you left it> <the series>`: a '-' line is something the "
                  f"branch had and the series does not; a '+' line is something the series added. It must equal "
                  f"deferred.md plus the refactors. Full diff in {os.path.join(plan_dir, 'residual.patch')}:")
            print(residual_stat.rstrip())
        else:
            print("\nresidual: none (the series reproduces the branch exactly)")
        with open(os.path.join(plan_dir, "size-gate.json"), "w") as fh:
            json.dump({"series": series_tip, "passed": size_passed}, fh)
        print("\n" + "\n".join(l for l in size_md.splitlines() if "| OVER" in l or "| CRAMMED" in l or l.startswith("Total outside")))
        print(f"\n{source} has NOT moved. The series is at refs/review-ready/series/{source}: review it there "
              f"(git log {landed_on}..refs/review-ready/series/{source})" + (
                  f", and when the verdict is ACK:\n  python3 {os.path.abspath(__file__)} {os.path.abspath(args.plan)} "
                  "--land" if size_passed else
                  f".\nSIZE GATE FAILED: the series keeps too much of the branch to land. Full table in "
                  f"{os.path.join(plan_dir, 'size.md')}"))
        if not size_passed:
            sys.exit(1)
    else:
        if ref_exists(top, f"refs/heads/{branch}"):
            git(top, "update-ref", f"refs/review-ready/replaced/{branch}", sha(top, f"refs/heads/{branch}"))
            print(f"  replaced branch {branch}; its previous tip is kept at refs/review-ready/replaced/{branch}")
        git(top, "update-ref", f"refs/heads/{branch}", series_tip)
        for s in stack:
            git(top, "update-ref", f"refs/heads/{s['branch']}", shas[s["through"] - 1])
        print(f"\nbranch {branch} holds the series (write mode: no residual, nothing to land)")


def land(args, plan, plan_dir, resolve):
    repo = resolve(plan.get("repo", "."))
    top = git(repo, "rev-parse", "--show-toplevel").strip()
    source = plan.get("source") or sys.exit("--land moves the user's branch onto the series; write mode has none")
    series, original = f"refs/review-ready/series/{source}", f"refs/review-ready/original/{source}"
    landed, built_from = f"refs/review-ready/landed/{source}", f"refs/review-ready/series-of/{source}"
    if not ref_exists(top, series):
        sys.exit(f"no series has been built for {source}: run the build first")
    tip = sha(top, source)
    same_code = ref_exists(top, landed) and tree(top, landed) == tree(top, source)
    if tip != sha(top, original) and not same_code:
        sys.exit(f"{source} has changed since the series was built from it ({sha(top, original)[:10]} -> {tip[:10]}). "
                 "Landing would drop that work. Rebuild (with --restart if the new commits are meant to stay).")
    if sha(top, built_from) != sha(top, original):
        sys.exit("the recorded series was built from an older original; rebuild before landing")
    new_tip = sha(top, series)
    try:
        with open(os.path.join(plan_dir, "size-gate.json")) as fh:
            gate = json.load(fh)
    except (OSError, ValueError):
        gate = {}
    if gate.get("series") != new_tip:
        sys.exit("no size-gate verdict for this series (size-gate.json is missing or from another build): rebuild")
    if not gate.get("passed"):
        sys.exit(f"the series failed the size gate; see {os.path.join(plan_dir, 'size.md')}. Rewrite the OVER files "
                 'smaller, or give each a reason in "size_ok", then rebuild.')

    current = git(top, "rev-parse", "--abbrev-ref", "HEAD").strip()
    for block in git(top, "worktree", "list", "--porcelain").split("\n\n"):
        lines = block.strip().splitlines()
        if lines and f"branch refs/heads/{source}" in lines \
                and os.path.realpath(lines[0][len("worktree "):]) != os.path.realpath(top):
            sys.exit(f"{source} is checked out in another worktree ({lines[0][len('worktree '):]}); moving it from "
                     "here would desynchronise that checkout. Run --land from there.")
    leaving = []
    if current == source:
        status = [l for l in git(top, "status", "--porcelain").splitlines() if ".review-ready/" not in l]
        modified = [l for l in status if not l.startswith("??")]
        incoming = set(git(top, "ls-tree", "-r", "--name-only", new_tip).split("\n"))
        clash = [l for l in status if l.startswith("??") and l[3:].rstrip("/") in incoming]
        # Untracked files are the user's (.env, data/, notes): a reset never touches
        # them unless the new tip tracks the same path. Only that, and edits to
        # tracked files, can lose work.
        if modified or clash:
            sys.exit(f"{source} is checked out and landing would overwrite:\n  " + "\n  ".join((modified + clash)[:8])
                     + "\nAsk the owner to commit or stash their tracked edits. Untracked files are left alone.")
        leaving = [f for f in git(top, "diff", "--name-only", "--diff-filter=D", tip, new_tip).split("\n") if f]

    git(top, "update-ref", f"refs/review-ready/backup/{source}", tip)
    if current == source:
        git(top, "reset", "--hard", "-q", new_tip)
        kept = []
        for f in leaving:
            if KEEP_LOCAL.search(f) and not os.path.exists(os.path.join(top, f)):
                os.makedirs(os.path.dirname(os.path.join(top, f)) or top, exist_ok=True)
                with open(os.path.join(top, f), "wb") as fh:
                    fh.write(subprocess.run(["git", "-C", top, "show", f"{original}:{f}"], capture_output=True).stdout)
                kept.append(f)
    else:
        git(top, "update-ref", f"refs/heads/{source}", new_tip, tip)
        kept = []
    git(top, "update-ref", landed, new_tip)

    base = sha(top, plan.get("onto") or plan["base"])
    shas = git(top, "rev-list", "--reverse", f"{base}..{new_tip}").split()
    made = []
    for s in plan.get("stack") or []:
        ref, mark = f"refs/heads/{s['branch']}", f"refs/review-ready/stack/{s['branch']}"
        if ref_exists(top, ref) and not (ref_exists(top, mark) and sha(top, mark) == sha(top, ref)) and not args.force:
            sys.exit(f"stack branch {s['branch']} exists and this tool did not make it; --force replaces it "
                     "(its tip is saved first)")
        if ref_exists(top, ref):
            git(top, "update-ref", f"refs/review-ready/replaced/{s['branch']}", sha(top, ref))
        git(top, "update-ref", ref, shas[s["through"] - 1])
        git(top, "update-ref", mark, shas[s["through"] - 1])
        made.append(s["branch"])

    undo = f"git -C {top} checkout -B {source} {original}"
    authors = sorted(set(git(top, "log", "--format=%an <%ae>", f"{base}..{original}").splitlines()))
    upstream = git(top, "rev-parse", "--abbrev-ref", f"{source}@{{upstream}}", ok=True).strip()
    lines = [f"# Undo for {source}", "",
             f"Your branch as you left it: {sha(top, original)} ({original})",
             f"The series it now holds:    {new_tip}", "",
             "Put it back, from any branch (switches to it; refuses rather than overwrite uncommitted edits;",
             "discards anything committed on the branch after the rewrite):", "", f"    {undo}", ""]
    if upstream:
        lines += [f"The branch tracks {upstream}, which still holds the old commits. Publishing the rewrite needs",
                  "`git push --force-with-lease`, and that removes those commits from the remote for everyone.", ""]
    if len(authors) > 1:
        lines += ["The original branch carried commits by more than one person:", *[f"  - {a}" for a in authors],
                  "Each rebuilt patch is authored by whoever the plan names. Check that nobody's work changed hands.", ""]
    if leaving:
        gone = [f for f in leaving if f not in kept]
        if kept:
            lines += ["No longer tracked by the series, kept in your working directory as untracked files:",
                      *[f"  - {f}" for f in kept], ""]
        if gone:
            lines += ["No longer in your working directory (the series does not carry them):",
                      *[f"  - {f}" for f in gone],
                      f"Restore a local copy:  git restore --source={original} --worktree -- <path>", ""]
    with open(os.path.join(plan_dir, "UNDO.md"), "w") as fh:
        fh.write("\n".join(lines))
    print(f"{source} now holds the series ({tip[:10]} -> {new_tip[:10]}).")
    print("\n".join(lines[2:]))
    if made:
        print("stack branches: " + ", ".join(made) + f"; {source} is the top. Open the first PR only; the next when it merges.")
    print(f"written to {os.path.join(plan_dir, 'UNDO.md')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", nargs="?")
    ap.add_argument("--land", action="store_true", help="move the user's branch onto the built series")
    ap.add_argument("--restart", action="store_true", help="the branch's current tip is the new original")
    ap.add_argument("--force", action="store_true", help="replace an existing write-mode or stack branch (tip saved first)")
    ap.add_argument("--keep-worktree", action="store_true")
    ap.add_argument("--no-check", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="run the scenario suite in scratch repos")
    args = ap.parse_args()
    if args.self_test:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.exit(subprocess.run([sys.executable, os.path.join(here, "selftest_build.py")]).returncode)
    if not args.plan:
        ap.error("PLAN.json is required")
    plan_path = os.path.abspath(args.plan)
    plan_dir = os.path.dirname(plan_path)
    with open(plan_path) as fh:
        plan = json.load(fh)

    def resolve(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(plan_dir, p))

    (land if args.land else build)(args, plan, plan_dir, resolve)


if __name__ == "__main__":
    main()
