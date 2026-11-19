#!/usr/bin/env python3
"""summary.py: the end of a pass, in one page: what changed, and whether it is ready to merge.

usage: summary.py <work dir> [--md FILE]      (default: <work dir>/summary.md)
       summary.py --self-test

It reads only what the pass wrote (plan.json, size-gate.json, reasons.json and the
size, reasons, tests and comment tables, review-vN.md, decisions, defence.md, run.md,
UNDO.md) and git, and recommends one of:

  READY TO MERGE    every gate passed, the last review is an ACK, nothing waits for the owner
  WAITING FOR YOU   only the owner's decisions are missing
  ANOTHER ROUND     something failed and the owner's round cap leaves room to fix it
  NOT READY         something failed and the round cap is spent: the owner decides
  REBUILD FIRST     the records are older than the series, so nothing here can be judged

Every line of the verdict names the file its evidence comes from: a recommendation the
owner cannot check is not one. The exit code is 0 for READY TO MERGE, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_series import MAX_ROUNDS, reviews  # noqa: E402  the builder's own count of rounds and verdicts


def read(w, name):
    path = os.path.join(w, name)
    return open(path, encoding="utf-8", errors="replace").read() if os.path.exists(path) else ""


def load(w, name):
    try:
        return json.loads(read(w, name) or "{}")
    except ValueError:
        return {}


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def problems(text):
    """The `- KIND ...` lines a gate table ends with."""
    return [line[2:] for line in text.splitlines() if re.match(r"- [A-Z-]{4,}\s", line)]


def evidence(w):
    """[(name, state, what, source)] where state is ok, fail, wait or note."""
    plan = load(w, "plan.json")
    gate, recorded = load(w, "size-gate.json"), load(w, "reasons.json")
    repo = os.path.normpath(os.path.join(w, plan.get("repo", ".")))
    source = plan.get("source")
    rows = []

    series = git(repo, "rev-parse", "-q", "--verify", f"refs/review-ready/series/{source}") if source else ""
    if not gate:
        rows.append(("build", "fail", "no completed build: size-gate.json is missing, so the series never passed every "
                     "commit's check", "size-gate.json"))
        return plan, rows
    if series and gate.get("series") != series:
        rows.append(("fresh", "stale", f"the records describe {gate.get('series', '?')[:10]}, the series is now "
                     f"{series[:10]}", "size-gate.json"))
        return plan, rows
    rows.append(("build", "ok", "the check passed at every commit of the series (the builder records a series only "
                 "after that)", "size-gate.json"))

    size = read(w, "size.md")
    total = re.search(r"Total outside `size_ok`: (\d+) -> (\d+) \(([^)]*)\)", size)
    keep = f"{total.group(1)} -> {total.group(2)} reviewable lines ({total.group(3)})" if total else "see the table"
    exempt = ", ".join(f"`{k}`" for k in plan.get("size_ok") or {})
    rows.append(("size", "ok" if gate.get("passed") else "fail",
                 f"{keep}; at most {plan.get('shrink', 0.5):.0%} may stay" + (f"; exempt with a reason: {exempt}" if exempt else "")
                 + ("" if gate.get("passed") else "; " + "; ".join(l.strip("| ") for l in size.splitlines()
                                                            if "| OVER" in l or "| CRAMMED" in l)[:400]), "size.md"))

    reasons = read(w, "reasons.md")
    owed = problems(reasons)
    missing = [p for p in owed if p.startswith(("MISSING", "REJECTED"))]
    waiting = [p for p in owed if p.startswith(("WAITING", "EDIT"))]
    changes = len(re.findall(r"^\| `", reasons, re.M))
    rows.append(("reasons", "fail" if missing or recorded.get("passed") is False else "ok",
                 f"{changes - len(missing)} of {changes} changes carry a written reason"
                 + (": " + "; ".join(missing[:5]) if missing else ""), "reasons.md"))
    if plan.get("run") == "interactive":
        rows.append(("decisions", "wait" if waiting else "ok",
                     f"{len(waiting)} change(s) wait for your decision: " + "; ".join(waiting[:5]) if waiting
                     else "you adopted every change", "decisions.json, reasons.md"))

    tests = read(w, "tests.md")
    failing = problems(tests)
    must = plan.get("must_pass") or []
    first = (read(w, "original-tests.txt").splitlines() or [""])[0]
    state = re.search(r": (\w[\w ]*)$", first)
    rows.append(("tests", "fail" if failing or recorded.get("tests_passed") is False else "ok",
                 (f"{len(failing)} test problem(s): " + "; ".join(failing[:5])) if failing else
                 (f"every test of your branch that fails against the series has its reason; {len(must)} must-pass "
                  f"test(s) pass" + (f"; your branch's test files run against the series: {state.group(1)}, and "
                                     "each failure is explained in the table" if state and state.group(1) != "ok" else "")),
                 "tests.md"))

    prose = read(w, "prose.md")
    share = re.search(r"Total: (\d+) of (\d+) added Python lines are prose \((\d+%)\)", prose)
    if not prose or "prose_passed" not in gate:
        rows.append(("comments", "fail", "the comment gate has not run on this series (it was built before the gate "
                     "existed): rebuild to check it", "prose.md"))
    else:
        rows.append(("comments", "fail" if gate.get("prose_passed") is False else "ok",
                     (f"{share.group(1)} of {share.group(2)} added Python lines are comments or docstrings "
                      f"({share.group(3)})" if share else "see the table")
                     + ("; " + "; ".join(problems(prose)[:4]) if problems(prose) else ""), "prose.md"))

    base = plan.get("base")
    if series and base:
        n = len(git(repo, "rev-list", f"{base}..{series}").split())
        cap = plan.get("max_commits")
        rows.append(("commits", "fail" if cap and n > cap else "ok",
                     f"{n} commit(s)" + (f", within your cap of {cap}" if cap else ", no cap set"), "plan.json, git"))

    rounds, cap = reviews(w), plan.get("max_rounds", MAX_ROUNDS)
    if not rounds:
        rows.append(("review", "fail", "no maintainer review has been written yet", "review-vN.md"))
    else:
        name, verdict = rounds[-1]
        line = re.search(r"^#+\s*Verdict:.*$", read(w, name), re.M)
        same = re.search(r"Reviewer:\s*same context", read(w, name), re.I)
        rows.append(("review", "ok" if verdict == "ACK" else "fail",
                     f"round {len(rounds)} of at most {cap}: {line.group(0).lstrip('# ') if line else verdict}", name))
        if same:
            rows.append(("second reader", "note", "the review ran in the same context that wrote the series, so it "
                         "lacks a fresh reader's distance: ask a colleague, or a fresh session, to read it", name))

    run = read(w, "run.md")
    rows.append(("run", "ok" if run.strip() else "note",
                 (run.strip().splitlines()[0][:200] if run.strip() else
                  "nobody ran the series' entry point; your runs were of the original branch"), "run.md"))
    rows.append(("defence", "ok" if read(w, "defence.md").strip() else "note",
                 "defence.md answers the questions a maintainer will ask" if read(w, "defence.md").strip()
                 else "defence.md is not written: you will be asked questions it would have answered", "defence.md"))
    undo = re.search(r"git -C \S+ checkout -B \S+ \S+", read(w, "UNDO.md"))
    rows.append(("landed", "note", f"landed on your branch; undo with `{undo.group(0)}`" if undo
                 else "not landed: your branch is as you left it", "UNDO.md"))
    return plan, rows


def recommend(plan, rows, rounds):
    """(verdict, [reasons]) from the evidence rows and the review rounds."""
    state = {name: s for name, s, _w, _src in rows}
    cite = {name: f"{what} ({src})" for name, _s, what, src in rows}
    if "stale" in state.values():
        return "REBUILD FIRST", [cite["fresh"] + ": rebuild, then summarise again"]
    failed = [n for n, s in state.items() if s == "fail"]
    rounds, cap = len(rounds), plan.get("max_rounds", MAX_ROUNDS)
    if failed:
        why = [cite[n] for n in failed]
        if state.get("review") == "fail" and "no maintainer review" in cite["review"]:
            return "ANOTHER ROUND", why + ["the maintainer pass (step 7) has not run: run it before deciding"]
        if rounds < cap:
            return "ANOTHER ROUND", why + [f"{rounds} of {cap} review round(s) used: fix the above, rebuild, review again"]
        return "NOT READY", why + [f"all {cap} review round(s) are spent with the above still standing: decide each, "
                                   "take it over, or raise max_rounds"]
    if state.get("decisions") == "wait":
        return "WAITING FOR YOU", [cite["decisions"]]
    why = [cite[n] for n in ("build", "size", "reasons", "tests", "comments", "commits", "review") if n in cite]
    before = [cite[n] for n in ("run", "second reader", "defence") if state.get(n) == "note"]
    return "READY TO MERGE", why + [f"before you merge: {b}" for b in before]


def summarise(w):
    plan, rows = evidence(w)
    verdict, why = recommend(plan, rows, reviews(w))
    mark = {"ok": "ok", "fail": "**FAILED**", "wait": "**waiting**", "note": "note", "stale": "**stale**"}
    md = [f"# Summary: {plan.get('source') or plan.get('branch') or os.path.basename(w)}", "",
          f"## Recommendation: {verdict}", ""]
    md += [f"- {line}" for line in why]
    md += ["", "## The evidence", "", "| check | state | what was found | source |", "|---|---|---|---|"]
    md += [f"| {n} | {mark[s]} | {what.replace('|', '/')} | `{src}` |" for n, s, what, src in rows]
    md += ["", "Each line above comes from the file named beside it; open that file to check it. Nothing here is "
           "recalled: rerun `summary.py` after any rebuild or review."]
    return verdict, "\n".join(md) + "\n"


def self_test():
    """Five passes in a scratch repo, one per recommendation, plus one with stale records."""
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        g = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True, text=True).stdout.strip()  # noqa: E731
        g("init", "-q", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        open(os.path.join(tmp, "a.py"), "w").write("X = 1\n"); g("add", "."); g("commit", "-qm", "base")
        open(os.path.join(tmp, "a.py"), "w").write("X = 2\n"); g("commit", "-qam", "series")
        tip = g("rev-parse", "HEAD")
        g("update-ref", "refs/review-ready/series/feature", tip)
        w = os.path.join(tmp, ".review-ready")
        os.makedirs(w)

        def write(name, text):
            path = os.path.join(w, name)
            if text is None:
                os.path.exists(path) and os.remove(path)
            else:
                open(path, "w").write(text if isinstance(text, str) else json.dumps(text))

        def setup(**over):
            files = {"plan.json": {"repo": "..", "source": "feature", "base": "main", "run": "auto", "max_rounds": 2,
                                   "must_pass": ["test_nan"]},
                     "size-gate.json": {"series": tip, "passed": True, "prose_passed": True},
                     "reasons.json": {"series": tip, "passed": True, "tests_passed": True},
                     "size.md": "Total outside `size_ok`: 400 -> 150 (37%). Gate: **passed**.\n",
                     "reasons.md": "| `a.py:<module>` | module code | rewritten | X is 2 | deferred.md |\n\nGate: **passed**.\n",
                     "tests.md": "Gate: **passed**.\n",
                     "prose.md": "Total: 5 of 60 added Python lines are prose (8%). Gate: **passed**.\n",
                     "review-v1.md": "# Review\n\n## Verdict: ACK\n", "review-v2.md": None,
                     "run.md": "Ran the nightly job on both: identical output.\n", "defence.md": "Q: why? A: a.py:1\n"}
            files.update(over)
            for name, text in files.items():
                write(name, text)
            return summarise(w)

        verdict, md = setup()
        checks.append(("every gate passed and the last review ACKs: ready to merge", verdict == "READY TO MERGE"))
        checks.append(("each line names its source", all(re.search(r"\(\S+\.(md|json)", l) for l in md.split("## The evidence")[0].splitlines() if l.startswith("- "))))
        verdict, md = setup(**{"run.md": None})
        checks.append(("a series nobody ran is ready, with that said first", verdict == "READY TO MERGE" and "nobody ran" in md))
        verdict, md = setup(**{"review-v1.md": "## Verdict: NAK (1 of 2 patches need work)\n"})
        checks.append(("a NAK under the round cap is another round", verdict == "ANOTHER ROUND" and "1 of 2 review" in md))
        verdict, md = setup(**{"review-v1.md": "## Verdict: NAK\n", "review-v2.md": "## Verdict: NAK\n"})
        checks.append(("a NAK at the round cap is not ready, for the owner to decide", verdict == "NOT READY"))
        verdict, md = setup(**{"size-gate.json": {"series": tip, "passed": False, "prose_passed": True},
                               "size.md": "| `a.py` | 100 | 90 | OVER: at most 75 |\n\nTotal outside `size_ok`: 400 -> 300 (75%). Gate: **FAILED**.\n"})
        checks.append(("a failed gate is another round, and names the file over", verdict == "ANOTHER ROUND" and "OVER" in md))
        verdict, md = setup(**{"review-v1.md": None})
        checks.append(("no review yet: another round, the maintainer pass", verdict == "ANOTHER ROUND" and "has not run" in md))
        verdict, md = setup(**{"plan.json": {"repo": "..", "source": "feature", "base": "main", "run": "interactive"},
                               "reasons.md": "| `a.py:<module>` | m | r | X | d |\n\n- WAITING  a.py:<module>: the owner has not decided\n"})
        checks.append(("interactive: undecided changes wait for the owner", verdict == "WAITING FOR YOU"))
        verdict, md = setup(**{"size-gate.json": {"series": tip, "passed": True}, "prose.md": None})
        checks.append(("a series built before the comment gate is not called ready", verdict == "ANOTHER ROUND"
                       and "has not run" in md))
        verdict, md = setup(**{"review-v1.md": "Reviewer: same context\n\n## Verdict: ACK\n"})
        checks.append(("a same-context ACK is ready, with a second reader owed", verdict == "READY TO MERGE"
                       and "before you merge: the review ran in the same context" in md))
        verdict, md = setup(**{"size-gate.json": {"series": "0" * 40, "passed": True}})
        checks.append(("records older than the series: rebuild first", verdict == "REBUILD FIRST"))
        verdict, md = setup(**{"plan.json": {"repo": "..", "source": "feature", "base": "main", "max_commits": 1},
                               "review-v1.md": "## Verdict: ACK\n"})
        checks.append(("a series within the commit cap passes that check", verdict == "READY TO MERGE" and "within your cap of 1" in md))
    failed = sum(not ok for _n, ok in checks)
    for name, ok in checks:
        print(("ok   " if ok else "FAIL ") + name)
    print(f"{len(checks)} cases, {failed} failing")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work", nargs="?")
    ap.add_argument("--md")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    if not args.work:
        ap.error("the work directory ($W) is required")
    verdict, md = summarise(os.path.abspath(args.work))
    out = args.md or os.path.join(args.work, "summary.md")
    with open(out, "w") as fh:
        fh.write(md)
    print(md)
    print(f"written to {out}")
    sys.exit(0 if verdict == "READY TO MERGE" else 1)


if __name__ == "__main__":
    main()
