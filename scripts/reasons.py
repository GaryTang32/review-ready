#!/usr/bin/env python3
"""reasons.py: every change a series makes to the branch, and the sentence that justifies it.

usage: reasons.py PLAN.json [--from REF] [--to REF] [--md FILE] [--json FILE]
       reasons.py --self-test

A change is one of:
  a function, method or class whose code differs between the two commits (compared as syntax
  trees, so reformatting alone is not a change), or that one side does not have;
  a module's own code (imports, assignments, the __main__ block), compared the same way;
  a Python file whose bytes differ while all of the above are equal (comments, blank lines);
  a file of any other kind whose content differs, or that one side does not have.
A class added or dropped whole is one change with its methods, and so is a file added or
dropped whole.

Its reason is the first of:
  1. the plan's "reasons" entry for it, keyed "path:qual" (a module's own code: "path:<module>");
  2. a sentence of the pass's own files that names it as code: `name`, `name()`, or name(),
     in deferred.md, a commit message (the plan's msgs files) or defence.md; a module's own
     code counts when a sentence names a name it binds (`TIMEOUT`, `requests`); a file other
     than Python counts when a sentence names its path or its file name;
  3. the plan's "reasons" or "scope_ok" entry for its whole file, keyed by the path, which
     covers every change in that file and is shown as such;
  4. for a test file, a sentence that names the file.
A change with none is MISSING.

The builder runs this after every build (the reason gate): a change with no reason fails the
build and --land refuses the series. With "run": "interactive" in the plan, every change also
needs the owner's decision in decisions.json, keyed the same way: "adopt", "reject", or any
other text, which is a request to change it first. A change the owner rejected must not be in
the series; a change they asked to edit waits until it is shown again and adopted.

Without --from/--to it compares the branch as the owner left it with the built series
(refs/review-ready/original/<branch> and refs/review-ready/series/<branch>), or, in write mode,
the plan's base with its branch.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
TRAILER = re.compile(r"^(Co-Authored-By|Claude-Session|Signed-off-by|Reviewed-by|Acked-by|Fixes|Link):", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
MODULE = "<module>"


def git(repo, *args, ok=False):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode and not ok:
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout if r.returncode == 0 else None


# ---------------------------------------------------------------- what counts as a change

def bound(statements):
    """The names a list of top-level statements binds: assignment targets and imports."""
    names = set()
    for node in statements:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    return names


def module_names(old, new):
    """The top-level names whose binding statement one side has and the other does not."""
    def body(src):
        return {ast.dump(n): n for n in ast.parse(src).body if not isinstance(n, DEFS)}
    a, b = body(old), body(new)
    return sorted(bound([a[k] for k in a.keys() - b.keys()] + [b[k] for k in b.keys() - a.keys()]))


def definitions(src):
    """{qual: (kind, the definition's own code as a syntax-tree dump)}, plus MODULE for the module's code.

    A class's own code leaves its methods out (they are their own entries), so a changed method
    does not also make its class a change.
    """
    tree = ast.parse(src)
    out = {MODULE: ("module code", ast.dump(ast.Module(body=[n for n in tree.body if not isinstance(n, DEFS)],
                                                       type_ignores=[])))}

    def walk(body, prefix):
        for node in body:
            if isinstance(node, FUNCS):
                out[prefix + node.name] = ("method" if prefix else "function", ast.dump(node))
            elif isinstance(node, ast.ClassDef):
                own = ast.ClassDef(name=node.name, bases=node.bases, keywords=node.keywords, decorator_list=node.decorator_list,
                                   body=[n for n in node.body if not isinstance(n, FUNCS)], type_params=[])
                out[prefix + node.name] = ("class", ast.dump(own))
                walk(node.body, f"{prefix}{node.name}.")
    walk(tree.body, "")
    return out


def changes(repo, a, b):
    """[{key, path, qual, name, kind, fate}] for every change from commit `a` to commit `b`."""
    rows = []
    for path in git(repo, "diff", "--name-only", "--no-renames", a, b).split():
        old, new = git(repo, "show", f"{a}:{path}", ok=True), git(repo, "show", f"{b}:{path}", ok=True)
        if old is None or new is None:
            rows.append(dict(key=path, path=path, qual=None, name=os.path.basename(path), kind="file",
                             fate="added" if old is None else "dropped"))
            continue
        found = []
        if path.endswith(".py"):
            try:
                da, db = definitions(old), definitions(new)
            except SyntaxError:
                da = db = None
            if da is not None:
                for qual in sorted(set(da) | set(db)):
                    x, y = da.get(qual), db.get(qual)
                    if x == y:
                        continue
                    found.append(dict(key=f"{path}:{qual}", path=path, qual=qual,
                                      name="" if qual == MODULE else qual.split(".")[-1], kind=(x or y)[0],
                                      fate="rewritten" if x and y else "dropped" if x else "added",
                                      names=module_names(old, new) if qual == MODULE else []))
                whole = {r["qual"] for r in found if r["kind"] == "class" and r["fate"] != "rewritten"}
                found = [r for r in found if not any(r["qual"].startswith(c + ".") for c in whole)]
        if not found:
            found = [dict(key=path, path=path, qual=None, name=os.path.basename(path),
                          kind="comments and layout" if path.endswith(".py") else "file", fate="rewritten")]
        rows += found
    return rows


# ---------------------------------------------------------------- what counts as a reason

def blocks(text):
    """Each paragraph and list item of a markdown or plain-text file, as one line of text."""
    out, cur = [], []
    for line in text.splitlines():
        s = line.strip()
        starts = re.match(r"^\s*([-*]|\d+[.)])\s+", line)
        if not s or s.startswith(("#", "|", "```", "[PATCH")) or TRAILER.match(s) or starts:
            if cur:
                out.append(" ".join(cur))
            cur = [line[starts.end():].strip()] if starts else []
            continue
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    return [b for b in out if b]


def code_name(name, qual=None):
    """A pattern for a definition named as code: `name`, `name()`, `Class.name`, or name() in prose."""
    names = [re.escape(n) for n in dict.fromkeys(filter(None, (qual, name)))]
    alts = "|".join(names)
    return re.compile(rf"`({alts})(\(\))?`|(?<![\w.]){re.escape(name)}\(\)")


def file_name(path):
    base = os.path.basename(path)
    return re.compile(rf"(?<![\w./-])({re.escape(path)}|{re.escape(base)})(?![\w-])")


def mentions(pattern, texts, limit=3):
    """[(source, sentence)] for the sentences of `texts` ([(source, text)]) that match `pattern`."""
    found = []
    for source, text in texts:
        found += [(source, b) for b in blocks(text) if pattern.search(b)][:limit]
    return found


def sources(plan, plan_dir):
    """The pass's own files a reason may come from, as [(label, text)]."""
    def read(path):
        path = path if os.path.isabs(path) else os.path.join(plan_dir, path)
        return open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    texts = [("deferred.md", read("deferred.md"))]
    texts += [(f"message {i + 1}", read(p["message"])) for i, p in enumerate(plan.get("patches", [])) if p.get("message")]
    return texts + [("defence.md", read("defence.md"))]


def account(rows, plan, texts, decisions=None):
    """Attach to each change its reason ({source, text}, or None) and, in interactive mode, the owner's decision.

    Returns (rows, problems): one line per change that has no reason or waits for the owner.
    """
    given = plan.get("reasons") or {}
    interactive = plan.get("run") == "interactive"
    problems = []
    for row in rows:
        reason = None
        if row["key"] in given:
            reason = dict(source="plan reasons", text=given[row["key"]])
        elif row["qual"] not in (None, MODULE):
            hit = mentions(code_name(row["name"], row["qual"]), texts, 1)
            reason = dict(source=hit[0][0], text=hit[0][1]) if hit else None
        elif row["qual"] == MODULE and row.get("names"):       # a constant or an import: named by what it binds
            hit = [h for name in row["names"] for h in mentions(code_name(name), texts, 1)]
            reason = dict(source=hit[0][0], text=hit[0][1]) if hit else None
        elif row["qual"] is None:
            hit = mentions(file_name(row["path"]), texts, 1)
            reason = dict(source=hit[0][0], text=hit[0][1]) if hit else None
        if reason is None and row["path"] in given:
            reason = dict(source=f"plan reasons, for all of {row['path']}", text=given[row["path"]])
        if reason is None and row["path"] in (plan.get("scope_ok") or {}):
            reason = dict(source="plan scope_ok", text=plan["scope_ok"][row["path"]])
        if reason is None and TEST_PATH.search(row["path"]):
            hit = mentions(file_name(row["path"]), texts, 1)
            reason = dict(source=f"{hit[0][0]}, for the test file", text=hit[0][1]) if hit else None
        row["reason"] = reason
        if reason is None:
            problems.append(f"MISSING  {row['key']} ({row['kind']}, {row['fate']}): no sentence names it and the plan "
                            "gives no reason")
        if interactive:
            decision = (decisions or {}).get(row["key"])
            row["decision"] = decision
            if decision is None:
                problems.append(f"WAITING  {row['key']}: the owner has not decided on this change")
            elif decision == "reject":
                problems.append(f"REJECTED {row['key']}: the owner rejected this change and the series still makes it; "
                                "put the branch's version back")
            elif decision != "adopt":
                problems.append(f"EDIT     {row['key']}: the owner asked for a change first: {decision}")
    return rows, problems


def report(rows, problems, plan):
    """The reason table as markdown."""
    head = ["# Reasons: every change the series makes to the branch", "",
            "A change with no reason fails the build and cannot land. A reason is a sentence of deferred.md, a commit "
            "message or defence.md that names the change as code, or an entry of the plan's `reasons`.", ""]
    if plan.get("run") == "interactive":
        head += ["Interactive run: every change also needs the owner's decision in decisions.json.", ""]
    table = ["| change | kind | fate | reason | source |" + (" decision |" if plan.get("run") == "interactive" else ""),
             "|---|---|---|---|---|" + ("---|" if plan.get("run") == "interactive" else "")]
    for r in rows:
        text = (r["reason"]["text"] if r["reason"] else "**MISSING**").replace("|", "\\|")
        cells = [f"`{r['key']}`", r["kind"], r["fate"], text[:300], r["reason"]["source"] if r["reason"] else ""]
        if plan.get("run") == "interactive":
            cells.append(r.get("decision") or "**waiting**")
        table.append("| " + " | ".join(cells) + " |")
    verdict = (["", f"Gate: **passed**. {len(rows)} change(s), each with its reason."] if not problems
               else ["", f"Gate: **FAILED**. {len(problems)} problem(s):", ""] + [f"- {p}" for p in problems]
               + ["", "Name each missing change in deferred.md or in the message of the patch that makes it, saying "
                  "why, or give it an entry in the plan's `reasons`; then rebuild."])
    return "\n".join(head + table + verdict) + "\n"


def gate(repo, a, b, plan, plan_dir):
    """(passed, rows, markdown) for the changes from `a` to `b`."""
    decisions_path = os.path.join(plan_dir, "decisions.json")
    decisions = json.load(open(decisions_path)) if os.path.exists(decisions_path) else {}
    rows, problems = account(changes(repo, a, b), plan, sources(plan, plan_dir), decisions)
    return not problems, rows, report(rows, problems, plan)


# ---------------------------------------------------------------- self-test

BASE = {"pkg/__init__.py": "", "pkg/core.py": "def load(path):\n    return open(path).read()\n", "README.md": "pkg\n"}
BRANCH = {
    "pkg/core.py": "import os\n\n\ndef load(path):\n    return _strip(open(path).read())\n\n\ndef _strip(text):\n"
                   "    return text.strip()\n\n\ndef rows(path):\n    return load(path).splitlines()\n\n\n"
                   "class Cache:\n    def get(self, k):\n        return k\n\n    def put(self, k):\n        return k\n",
    "pkg/notes.py": "# notes\nX = 1\n",
    "requirements.txt": "requests\n",
    "README.md": "pkg reads files\n",
}
SERIES = {
    "pkg/core.py": "def load(path):\n    return open(path).read().strip()\n\n\ndef rows(path):\n    return load(path).splitlines()\n\n\n"
                   "def count(path):\n    return len(rows(path))\n",
    "pkg/notes.py": "# notes, kept short\nX = 1\n",
    "README.md": "pkg reads files\n",
}


def self_test():
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="T",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    with tempfile.TemporaryDirectory() as repo:
        def g(*args):
            return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=env).stdout.strip()

        def commit(files, drop=()):
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(repo, rel)) or repo, exist_ok=True)
                with open(os.path.join(repo, rel), "w") as fh:
                    fh.write(text)
            for rel in drop:
                g("rm", "-q", rel)
            g("add", "-A")
            g("commit", "-qm", "x")
            return g("rev-parse", "HEAD")

        g("init", "-q", "-b", "main")
        base = commit(BASE)
        branch = commit(BRANCH)
        series = commit(SERIES, drop=["requirements.txt"])
        rows = changes(repo, branch, series)
        found = {r["key"]: r for r in rows}
        plan = dict(patches=[])
        texts = [("deferred.md", "- `_strip()`: one caller; inlined into load().\n- requirements.txt: pyproject.toml has them.\n"
                                 "- The `Cache` class had no user.\n"),
                 ("defence.md", "Why is `LIMIT` gone? Nothing read it.\n"),
                 ("message 1", "core: count the rows of a file\n\ncount() returns how many rows a file has.\n")]
        _, problems = account([dict(r) for r in rows], plan, texts)
        missing = sorted(p.split()[1] for p in problems if p.startswith("MISSING"))
        with_reasons = dict(plan, reasons={"pkg/core.py:load": "strips the text itself, now that _strip is gone",
                                           "pkg/core.py:<module>": "the os import had no user", "pkg/notes.py": "a comment"})
        _, none_left = account([dict(r) for r in rows], with_reasons, texts)
        interactive = dict(with_reasons, run="interactive")
        decide = {r["key"]: "adopt" for r in rows}
        _, waiting = account([dict(r) for r in rows], interactive, texts, {})
        _, adopted = account([dict(r) for r in rows], interactive, texts, decide)
        _, rejected = account([dict(r) for r in rows], interactive, texts, dict(decide, **{"pkg/core.py:count": "reject"}))
        _, edit = account([dict(r) for r in rows], interactive, texts, dict(decide, **{"pkg/core.py:count": "call it n_rows"}))
        checks = [
            ("a dropped helper is a change", found.get("pkg/core.py:_strip", {}).get("fate") == "dropped"),
            ("a rewritten function is a change, an identical one is not",
             found.get("pkg/core.py:load", {}).get("fate") == "rewritten" and "pkg/core.py:rows" not in found),
            ("an added function is a change", found.get("pkg/core.py:count", {}).get("fate") == "added"),
            ("a class dropped whole is one change with its methods",
             "pkg/core.py:Cache" in found and not any(k.startswith("pkg/core.py:Cache.") for k in found)),
            ("a changed import is the module's own code", found.get("pkg/core.py:<module>", {}).get("kind") == "module code"),
            ("a comment-only edit is a change to the file", found.get("pkg/notes.py", {}).get("kind") == "comments and layout"),
            ("a dropped file is one change", found.get("requirements.txt", {}).get("fate") == "dropped"),
            ("an unchanged file is not a change", "README.md" not in found),
            ("a sentence naming the change as code is its reason, in prose or backticks",
             "pkg/core.py:_strip" not in missing and "pkg/core.py:count" not in missing and "pkg/core.py:Cache" not in missing),
            ("a file is named by its path", "requirements.txt" not in missing),
            ("what nothing explains is MISSING", missing == ["pkg/core.py:<module>", "pkg/notes.py"]),
            ("a sentence about another change that names this one explains it too", "pkg/core.py:load" not in missing),
            ("the plan's reasons close the gaps, by change and by file", not none_left),
            ("interactive: a change the owner has not decided on waits", len(waiting) == len(rows)),
            ("interactive: every change adopted passes", not adopted),
            ("interactive: a rejected change still in the series fails", any(p.startswith("REJECTED") for p in rejected)),
            ("interactive: a requested edit waits for the edit", any(p.startswith("EDIT") for p in edit)),
            ("only the two commits given are compared: the same file is added from the base, dropped from the branch",
             any(r["key"] == "requirements.txt" and r["fate"] == "added" for r in changes(repo, base, branch))),
        ]
    failed = sum(not ok for _n, ok in checks)
    for name, ok in checks:
        print(("ok   " if ok else "FAIL ") + name)
    print(f"{len(checks)} cases, {failed} failing")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", nargs="?")
    ap.add_argument("--from", dest="a")
    ap.add_argument("--to", dest="b")
    ap.add_argument("--md")
    ap.add_argument("--json")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    if not args.plan:
        ap.error("give the plan")
    plan_dir = os.path.dirname(os.path.abspath(args.plan))
    plan = json.load(open(args.plan))
    repo = git(os.path.normpath(os.path.join(plan_dir, plan.get("repo", "."))), "rev-parse", "--show-toplevel").strip()
    source = plan.get("source")
    a = args.a or (f"refs/review-ready/original/{source}" if source and git(repo, "rev-parse", "-q", "--verify",
                   f"refs/review-ready/original/{source}", ok=True) else source or plan["base"])
    compared = f"refs/review-ready/compared/{source or plan.get('branch')}"
    b = args.b or (compared if git(repo, "rev-parse", "-q", "--verify", compared, ok=True)
                   else f"refs/review-ready/series/{source}" if source else plan["branch"])
    if source and not args.b and b != compared:
        # The series may have been rebased onto the target: compare its own commits, on the branch's base.
        a_tree = git(repo, "merge-base", plan.get("onto") or plan["base"], b).strip()
        if a_tree != git(repo, "rev-parse", plan["base"]).strip():
            print(f"note: the series sits on {a_tree[:10]}, not the fork point; files the target changed since the fork "
                  "are listed too", file=sys.stderr)
    passed, rows, md = gate(repo, a, b, plan, plan_dir)
    if args.md:
        open(args.md, "w").write(md)
    if args.json:
        json.dump(dict(passed=passed, changes=rows), open(args.json, "w"), indent=1)
    if not args.md and not args.json:
        print(md)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
