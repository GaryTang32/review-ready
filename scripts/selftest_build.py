#!/usr/bin/env python3
"""selftest_build.py: build_series.py against scratch repos, one situation per case.

Each case was once a way to lose the user's work or to certify a series that
proves nothing. Run directly, or as `build_series.py --self-test`.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, "build_series.py")
CHECK = f"{sys.executable} -m unittest discover -q -s tests -t ."
ENV = dict(os.environ, GIT_AUTHOR_NAME="Dana Dev", GIT_AUTHOR_EMAIL="dana@example.com",
           GIT_COMMITTER_NAME="Dana Dev", GIT_COMMITTER_EMAIL="dana@example.com",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

BASE_F = "def func1(rows):\n    return len(rows)\n\n\ndef func2(rows):\n    return rows[0]\n"
BRANCH_F = ('def func1(rows):\n    """Count rows."""\n    return len(list(rows))\n\n\ndef func2(rows):\n    return rows[0]\n'
            '\n\ndef junk():\n    print("debug")\n')
CLEAN_F = "def func1(rows):\n    return len(list(rows))\n\n\ndef func2(rows):\n    return rows[0]\n"
GUARD = 'def func2(rows):\n    if not rows:\n        raise ValueError("no rows")\n'
BASE_T = "import unittest\nimport f\n\n\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertEqual(f.func1([1]), 1)\n"
BRANCH_T = BASE_T + "\n    def test_gen(self):\n        self.assertEqual(f.func1(x for x in [1, 2]), 2)\n"
MSG = ("f: count the rows of a generator\n\nfunc1() calls len() on its argument, so a generator raises TypeError.\n"
       "Materialise it first; callers pass a few dozen rows.\n\nTested: tests.test_f; the new test fails on the parent.\n")


def sh(cwd, *args, ok=False):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=ENV)
    if r.returncode and not ok:
        raise RuntimeError(f"{' '.join(args)}\n{r.stdout}{r.stderr}")
    return r.stdout.strip()


def write(root, rel, text, mode=None):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    if mode:
        os.chmod(path, mode)


def repo(tmp, name, target_moves=False, conflict=False):
    """main with f.py and a test; `feature` adds a generator fix plus junk; optionally main moves on."""
    r = os.path.join(tmp, name)
    os.makedirs(r)
    sh(r, "git", "init", "-q", "-b", "main", ".")
    write(r, "f.py", BASE_F); write(r, "tests/__init__.py", ""); write(r, "tests/test_f.py", BASE_T)
    sh(r, "git", "add", "-A"); sh(r, "git", "commit", "-qm", "base")
    sh(r, "git", "checkout", "-qb", "feature")
    write(r, "f.py", BRANCH_F); write(r, "tests/test_f.py", BRANCH_T)
    sh(r, "git", "commit", "-qam", "feature work")
    sh(r, "git", "checkout", "-q", "main")
    if target_moves:
        text = BASE_F.replace("def func2(rows):\n", GUARD)
        if conflict:
            text = text.replace("return len(rows)", "return len(tuple(rows))")
        write(r, "f.py", text); sh(r, "git", "commit", "-qam", "main moves on")
    k = os.path.join(r, ".review-ready")
    write(k, "msgs/01.txt", MSG); write(k, "overlays/01/f.py", CLEAN_F)
    return r


def plan(r, **over):
    p = {"repo": "..", "base": sh(r, "git", "merge-base", "main", "feature"), "onto": "main", "source": "feature",
         "check": CHECK, "import_check": "f", "out": "series",
         "patches": [{"name": "count", "message": "msgs/01.txt", "overlay": {"f.py": "overlays/01/f.py"},
                      "paths": ["tests/test_f.py"],
                      "expect_fail_before": f"{sys.executable} -m unittest -q tests.test_f.T.test_gen"}]}
    for key, value in over.items():
        if value is None:
            p.pop(key, None)
        elif key == "patch0":
            p["patches"][0].update(value)
            for k2, v2 in value.items():
                if v2 is None:
                    p["patches"][0].pop(k2, None)
        else:
            p[key] = value
    path = os.path.join(r, ".review-ready", "plan.json")
    with open(path, "w") as fh:
        json.dump(p, fh)
    return path


def build(r, *flags, **over):
    path = plan(r, **over) if over or not os.path.exists(os.path.join(r, ".review-ready", "plan.json")) \
        else os.path.join(r, ".review-ready", "plan.json")
    p = subprocess.run([sys.executable, BUILDER, path, *flags], capture_output=True, text=True, env=ENV)
    return p.returncode, p.stdout + p.stderr


def refs(r, prefix="refs/review-ready"):
    return sh(r, "git", "for-each-ref", "--format=%(refname)", prefix).split()


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def build_never_moves_the_branch(tmp):
    r = repo(tmp, "a"); tip = sh(r, "git", "rev-parse", "feature")
    rc, out = build(r)
    assert rc == 0, out
    assert sh(r, "git", "rev-parse", "feature") == tip, "the branch moved during a build"
    assert "refs/review-ready/series/feature" in refs(r) and sh(r, "git", "branch", "--list").count("\n") == 1, refs(r)
    assert "fails on the parent, passes here" in out and "by assertion" not in out.split("passes here")[0], out


@case
def land_moves_it_and_undo_restores_it(tmp):
    r = repo(tmp, "b"); tip = sh(r, "git", "rev-parse", "feature")
    assert build(r)[0] == 0
    rc, out = build(r, "--land")
    assert rc == 0 and sh(r, "git", "rev-parse", "feature") != tip, out
    assert "junk" not in sh(r, "git", "show", "feature:f.py")
    undo = [l.strip() for l in open(os.path.join(r, ".review-ready", "UNDO.md")) if l.strip().startswith("git -C")][0]
    subprocess.run(undo, shell=True, check=True, capture_output=True, env=ENV)     # run from `main`
    assert sh(r, "git", "rev-parse", "feature") == tip, "undo did not restore the user's tip"
    assert sh(r, "git", "rev-parse", "main") != tip, "undo moved another branch"


@case
def stale_base_is_refused_and_leaves_nothing(tmp):
    r = repo(tmp, "c", target_moves=True)
    rc, out = build(r, base="main")
    assert rc != 0 and "fork point" in out and '"onto": "main"' in out, out
    assert sh(r, "git", "worktree", "list").count("\n") == 0 and sh(r, "git", "branch", "--list").count("\n") == 1


@case
def fork_point_plus_onto_keeps_the_targets_work(tmp):
    r = repo(tmp, "d", target_moves=True)
    rc, out = build(r)
    assert rc == 0 and "rebased onto main" in out, out
    assert "no rows" in sh(r, "git", "show", "refs/review-ready/series/feature:f.py"), "the target's guard was backed out"


@case
def conflict_with_the_target_stops_and_sends_it_to_the_owner(tmp):
    r = repo(tmp, "e", target_moves=True, conflict=True); tip = sh(r, "git", "rev-parse", "feature")
    rc, out = build(r)
    assert rc != 0 and "ask the owner to bring the branch up to date" in out and "overlay against" not in out, out
    assert sh(r, "git", "rev-parse", "feature") == tip and "refs/review-ready/series/feature" not in refs(r)


@case
def a_test_that_does_not_exist_proves_nothing(tmp):
    r = repo(tmp, "f")
    rc, out = build(r, patch0={"expect_fail_before": f"{sys.executable} -m unittest -q tests.test_f.T.test_nope"})
    assert rc != 0 and "fails WITH the patch too" in out, out


@case
def a_command_that_cannot_start_did_not_run(tmp):
    r = repo(tmp, "g")
    rc, out = build(r, patch0={"expect_fail_before": ".venv/bin/python -m unittest -q tests.test_f"})
    assert rc != 0 and "did not run" in out and "FAILED" not in out, out
    rc, out = build(r, check=".venv/bin/python -m unittest discover -q -s tests -t .")
    assert rc != 0 and "does not exist in the private worktree" in out, out


@case
def a_green_check_that_ran_nothing_is_refused(tmp):
    r = repo(tmp, "h")
    rc, out = build(r, check=f"{sys.executable} -m unittest discover -q -s f_nothing_here -t . || true",
                    import_check=None, patch0={"expect_fail_before": None})
    rc2, out2 = build(r, check=f"{sys.executable} -m unittest discover -q -s . -p 'zz*.py'",
                      import_check=None, patch0={"expect_fail_before": None})
    assert rc2 != 0 and "VACUOUS" in out2, out2
    rc3, out3 = build(r, check=None, import_check=None, patch0={"expect_fail_before": None})
    assert rc3 != 0 and "no_check_reason" in out3, out3


@case
def an_absolute_pythonpath_is_caught_by_import_check(tmp):
    # The trap needs a src layout: the package is importable only through PYTHONPATH, so an
    # absolute one silently points every check at the user's checkout instead of the series.
    r = os.path.join(tmp, "i"); os.makedirs(r); sh(r, "git", "init", "-q", "-b", "main", ".")
    write(r, "src/pkg/__init__.py", "def up(x):\n    return x\n"); write(r, "tests/__init__.py", "")
    write(r, "tests/test_pkg.py", "import unittest\nimport pkg\n\n\nclass T(unittest.TestCase):\n"
                                  "    def test_up(self):\n        self.assertEqual(pkg.up('a'), 'A')\n")
    sh(r, "git", "add", "-A"); sh(r, "git", "commit", "-qm", "base"); sh(r, "git", "checkout", "-qb", "feature")
    write(r, "src/pkg/__init__.py", "def up(x):\n    return x.upper()\n"); sh(r, "git", "commit", "-qam", "upper")
    sh(r, "git", "checkout", "-q", "main")
    k = os.path.join(r, ".review-ready"); write(k, "msgs/01.txt", MSG)
    write(k, "overlays/01/src/pkg/__init__.py", "def up(x):\n    return x.lower()\n")     # deliberately WRONG
    over = dict(import_check="pkg", patch0={"overlay": {"src/pkg/__init__.py": "overlays/01/src/pkg/__init__.py"},
                                            "paths": None, "expect_fail_before": None})
    sh(r, "git", "checkout", "-q", "feature")               # the checkout holds the ORIGINAL, correct code
    rc, out = build(r, check=f"PYTHONPATH={r}/src {CHECK}", **over)
    assert rc != 0 and "outside the worktree" in out, "a wrong overlay passed because the check tested the checkout:\n" + out
    rc, out = build(r, check=f"PYTHONPATH=src {CHECK}", **over)
    assert rc != 0 and "import_check ok" in out and "check FAILED" in out, out


@case
def a_package_the_series_creates_is_import_checked_once_it_exists(tmp):
    r = os.path.join(tmp, "n"); os.makedirs(r); sh(r, "git", "init", "-q", "-b", "main", ".")
    write(r, "README", "empty\n"); sh(r, "git", "add", "-A"); sh(r, "git", "commit", "-qm", "base")
    sh(r, "git", "checkout", "-qb", "feature")
    write(r, "pkg/__init__.py", "def up(x):\n    return x.upper()\n"); write(r, "tests/__init__.py", "")
    write(r, "tests/test_pkg.py", "import unittest\nimport pkg\n\n\nclass T(unittest.TestCase):\n"
                                  "    def test_up(self):\n        self.assertEqual(pkg.up('a'), 'A')\n")
    sh(r, "git", "add", "-A"); sh(r, "git", "commit", "-qm", "new package"); sh(r, "git", "checkout", "-q", "main")
    write(os.path.join(r, ".review-ready"), "msgs/01.txt", MSG)
    over = {"overlay": None, "expect_fail_before": None,
            "paths": ["pkg/__init__.py", "tests/__init__.py", "tests/test_pkg.py"]}
    rc, out = build(r, import_check="pkg", patch0=over)
    assert rc == 0 and "import_check ok: pkg resolves inside the worktree" in out, out
    rc, out = build(r, import_check="no_such_pkg", patch0=over)
    assert rc != 0 and "at the base or after any patch" in out, out


@case
def code_that_does_not_compile_is_never_landed(tmp):
    r = repo(tmp, "j")
    write(os.path.join(r, ".review-ready"), "overlays/01/f.py", "def func1(rows:\n    return 1\n")
    rc, out = build(r, check=None, no_check_reason="no tests", import_check=None, patch0={"expect_fail_before": None})
    assert rc != 0 and "does not compile" in out, out


@case
def untracked_files_do_not_block_landing_but_tracked_edits_do(tmp):
    r = repo(tmp, "k"); sh(r, "git", "checkout", "-q", "feature")
    write(r, ".env", "KEY=1\n"); write(r, "notes.txt", "mine\n")
    assert build(r)[0] == 0
    write(r, "f.py", BRANCH_F + "# edit\n")
    rc, out = build(r, "--land")
    assert rc != 0 and "would overwrite" in out, out
    sh(r, "git", "checkout", "--", "f.py")
    rc, out = build(r, "--land")
    assert rc == 0 and os.path.exists(os.path.join(r, ".env")) and os.path.exists(os.path.join(r, "notes.txt")), out


@case
def an_overlay_keeps_the_executable_bit(tmp):
    r = repo(tmp, "l"); sh(r, "git", "checkout", "-q", "feature")
    write(r, "run.sh", "#!/bin/sh\necho hi\n", 0o755); sh(r, "git", "add", "run.sh"); sh(r, "git", "commit", "-qm", "script")
    sh(r, "git", "checkout", "-q", "main")
    write(os.path.join(r, ".review-ready"), "overlays/01/run.sh", "#!/bin/sh\necho hi\n")
    rc, out = build(r, patch0={"overlay": {"f.py": "overlays/01/f.py", "run.sh": "overlays/01/run.sh"}})
    assert rc == 0, out
    assert sh(r, "git", "ls-tree", "refs/review-ready/series/feature", "run.sh").startswith("100755"), "mode lost"


@case
def no_branch_is_ever_deleted(tmp):
    r = repo(tmp, "m"); sh(r, "git", "branch", "review-ready-build", "feature"); sh(r, "git", "branch", "feature-clean", "feature")
    rc, out = build(r, branch="review-ready-build", in_place=True)
    assert rc == 0, out
    assert sh(r, "git", "rev-parse", "review-ready-build") == sh(r, "git", "rev-parse", "feature-clean"), "a bystander branch moved"
    rc, out = build(r, branch="feature", in_place=None)
    assert rc != 0 and '"source" alone' in out, out


@case
def the_owner_signs_not_the_message_file(tmp):
    r = repo(tmp, "n")
    write(os.path.join(r, ".review-ready"), "msgs/01.txt", MSG + "\nSigned-off-by: Dana Dev <dana@example.com>\n")
    rc, out = build(r)
    assert rc != 0 and "The owner signs" in out, out
    write(os.path.join(r, ".review-ready"), "msgs/01.txt", MSG + "---\nv2: moved the test\n")
    rc, out = build(r)
    assert rc != 0 and "lands in history" in out, out


@case
def a_local_target_behind_its_remote_is_refused(tmp):
    origin = os.path.join(tmp, "origin.git"); r = repo(tmp, "o")
    sh(tmp, "git", "clone", "-q", "--bare", r, origin); sh(r, "git", "remote", "add", "origin", origin)
    sh(r, "git", "fetch", "-q", "origin"); sh(r, "git", "branch", "-q", "--set-upstream-to=origin/main", "main")
    other = os.path.join(tmp, "colleague"); sh(tmp, "git", "clone", "-q", origin, other)
    write(other, "bob.py", "X = 1\n"); sh(other, "git", "add", "-A"); sh(other, "git", "commit", "-qm", "bob"); sh(other, "git", "push", "-q")
    sh(r, "git", "fetch", "-q", "origin")
    rc, out = build(r)
    assert rc != 0 and "behind 'origin/main'" in out and '"onto": "origin/main"' in out, out


@case
def it_runs_inside_a_linked_worktree(tmp):
    r = repo(tmp, "p"); plan(r); wt = os.path.join(tmp, "p-wt"); sh(r, "git", "worktree", "add", "-q", wt, "feature")
    shutil.copytree(os.path.join(r, ".review-ready"), os.path.join(wt, ".review-ready"))
    path = os.path.join(wt, ".review-ready", "plan.json")
    p = subprocess.run([sys.executable, BUILDER, path], capture_output=True, text=True, env=ENV)
    assert p.returncode == 0 and "Traceback" not in p.stderr, p.stdout + p.stderr
    p = subprocess.run([sys.executable, BUILDER, path, "--land"], capture_output=True, text=True, env=ENV)
    assert p.returncode == 0, p.stdout + p.stderr


@case
def a_file_the_branch_never_touched_needs_a_reason(tmp):
    r = repo(tmp, "q"); write(os.path.join(r, ".review-ready"), "overlays/01/tests/__init__.py", "# tidy\n")
    over = {"overlay": {"f.py": "overlays/01/f.py", "tests/__init__.py": "overlays/01/tests/__init__.py"}}
    rc, out = build(r, patch0=over)
    assert rc != 0 and "OUT OF SCOPE" in out and "tests/__init__.py" in out, out
    rc, out = build(r, patch0=over, scope_ok={"tests/__init__.py": "why"})
    assert rc == 0, out


@case
def every_round_is_measured_against_the_users_branch(tmp):
    r = repo(tmp, "s"); tip = sh(r, "git", "rev-parse", "feature")
    assert build(r)[0] == 0 and build(r, "--land")[0] == 0
    write(os.path.join(r, ".review-ready"), "overlays/01/f.py", CLEAN_F.replace("len(list(rows))", "sum(1 for _ in rows)"))
    rc, out = build(r)
    assert rc == 0, out
    res = open(os.path.join(r, ".review-ready", "residual.patch")).read()
    assert "-def junk" in res, "round two's residual lost sight of what the user's branch had"
    assert sh(r, "git", "rev-parse", "refs/review-ready/original/feature") == tip
    sh(r, "git", "checkout", "-q", "feature"); write(r, "g.py", "Y = 2\n"); sh(r, "git", "add", "-A"); sh(r, "git", "commit", "-qm", "more work")
    rc, out = build(r)
    assert rc != 0 and "--restart" in out, out
    rc, out = build(r, "--land")
    assert rc != 0 and "Landing would drop that work" in out, out


@case
def a_later_overlay_that_undoes_an_earlier_fix_is_reported(tmp):
    r = repo(tmp, "t"); k = os.path.join(r, ".review-ready")
    write(k, "overlays/01/f.py", CLEAN_F.replace("def func1(rows):\n", "def func1(rows):\n    assert rows is not None\n"))
    write(k, "overlays/02/f.py", CLEAN_F + "\n\nTWO = 2\n"); write(k, "msgs/02.txt", MSG.replace("count the rows", "add TWO for"))
    path = plan(r); p = json.load(open(path))
    p["patches"].append({"name": "two", "message": "msgs/02.txt", "overlay": {"f.py": "overlays/02/f.py"}})
    json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc == 0 and "drops 1 line(s) that patch 01 added" in out, out


@case
def a_line_rewritten_by_a_later_patch_is_not_a_revert(tmp):
    r = repo(tmp, "w"); k = os.path.join(r, ".review-ready")
    write(k, "overlays/01/f.py", CLEAN_F.replace("return len(list(rows))", 'n = len(list(rows))\n    return n'))
    # patch 2 refactors that same line into two; every name survives, so it is not a revert
    write(k, "overlays/02/f.py", CLEAN_F.replace("return len(list(rows))", 'count = list(rows)\n    n = len(count)\n    return n'))
    write(k, "msgs/02.txt", MSG.replace("count the rows", "name the list for"))
    path = plan(r); p = json.load(open(path))
    p["patches"].append({"name": "name-it", "message": "msgs/02.txt", "overlay": {"f.py": "overlays/02/f.py"}})
    json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc == 0 and "drops" not in out, out
    # now really revert it: nothing of patch 1's line survives
    write(k, "overlays/02/f.py", CLEAN_F)
    rc, out = build(r)
    assert rc == 0 and "drops 2 line(s) that patch 01 added" in out, out
    rc, out = build(r, overlay_ok={"f.py": "patch 2 reverts it on purpose"})
    assert rc == 0 and "drops" not in out, out


@case
def a_stack_is_cut_at_landing(tmp):
    r = repo(tmp, "u"); k = os.path.join(r, ".review-ready")
    write(k, "overlays/02/f.py", CLEAN_F + "\n\nTWO = 2\n"); write(k, "msgs/02.txt", MSG.replace("count the rows", "add TWO for"))
    path = plan(r, stack=[{"branch": "feature-1", "through": 1}]); p = json.load(open(path))
    p["patches"].append({"name": "two", "message": "msgs/02.txt", "overlay": {"f.py": "overlays/02/f.py"}})
    p["scope_ok"] = {}
    json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc == 0 and "stack: feature-1" in out, out
    assert "feature-1" not in sh(r, "git", "branch", "--list"), "a stack branch appeared before landing"
    rc, out = build(r, "--land")
    assert rc == 0, out
    assert sh(r, "git", "rev-parse", "feature-1") == sh(r, "git", "rev-parse", "feature~1")


def verbose_branch(tmp, name):
    """`feature` also brings parse.py: 120 lines where 3 would do. Patch 2 is a plan entry for it."""
    r = repo(tmp, name); k = os.path.join(r, ".review-ready")
    sh(r, "git", "checkout", "-q", "feature")
    write(r, "parse.py", "def parse(text):\n" + "".join(f"    step_{i} = text  # step {i}\n" for i in range(118))
          + "    return text.split(',')\n")
    sh(r, "git", "add", "parse.py"); sh(r, "git", "commit", "-qm", "parser"); sh(r, "git", "checkout", "-q", "main")
    write(k, "overlays/02/parse.py", "def parse(text):\n    return text.split(',')\n")
    write(k, "msgs/02.txt", MSG.replace("count the rows of a generator", "add parse()"))
    path = plan(r); p = json.load(open(path))
    p["patches"].append({"name": "parse", "message": "msgs/02.txt", "paths": ["parse.py"]})
    json.dump(p, open(path, "w"))
    return r, path


@case
def a_series_that_keeps_most_of_the_branch_cannot_land(tmp):
    r, path = verbose_branch(tmp, "x"); tip = sh(r, "git", "rev-parse", "feature")
    rc, out = build(r)
    assert rc != 0 and "SIZE GATE FAILED" in out and "parse.py" in out, out
    assert "refs/review-ready/series/feature" in refs(r), "a failed gate should still leave the series to review"
    rc, out = build(r, "--land")
    assert rc != 0 and "size gate" in out and sh(r, "git", "rev-parse", "feature") == tip, out
    p = json.load(open(path)); p["size_ok"] = {"parse.py": ""}; json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc != 0 and "needs a reason" in out, out
    p["size_ok"] = {"parse.py": "generated by the grammar tool; reviewed at its source"}; json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc == 0, out


@case
def a_rewritten_series_passes_the_gate_and_lands(tmp):
    r, path = verbose_branch(tmp, "y")
    p = json.load(open(path)); p["patches"][1] = {"name": "parse", "message": "msgs/02.txt",
                                                   "overlay": {"parse.py": "overlays/02/parse.py"}}
    json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc == 0 and "Gate: **passed**" in open(os.path.join(r, ".review-ready", "size.md")).read(), out
    assert build(r, "--land")[0] == 0


@case
def keeping_more_than_half_needs_a_stated_reason(tmp):
    r, path = verbose_branch(tmp, "z")
    p = json.load(open(path)); p["shrink"] = 1.0; json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc != 0 and "shrink_reason" in out, out
    p["shrink_reason"] = "the owner asked for a split only, no rewrite"; json.dump(p, open(path, "w"))
    rc, out = build(r)
    assert rc == 0, out


@case
def joining_statements_to_halve_the_line_count_does_not_pass_the_gate(tmp):
    r, path = verbose_branch(tmp, "c1"); k = os.path.join(r, ".review-ready")
    steps = [f"step_{i} = text" for i in range(118)]
    write(k, "overlays/02/parse.py", "def parse(text):\n" + "".join(
        "    " + "; ".join(steps[i:i + 4]) + "\n" for i in range(0, 118, 4)) + "    return text.split(',')\n")
    p = json.load(open(path)); p["patches"][1] = {"name": "parse", "message": "msgs/02.txt",
                                                   "overlay": {"parse.py": "overlays/02/parse.py"}}
    json.dump(p, open(path, "w"))
    rc, out = build(r)                                   # 31 lines against the branch's 120: under half, and crammed
    assert rc != 0 and "SIZE GATE FAILED" in out and "CRAMMED" in out and "share a line" in out, out
    assert build(r, "--land")[0] != 0
    p["size_ok"] = {"parse.py": "the owner wants these on one line each"}; json.dump(p, open(path, "w"))
    assert build(r)[0] == 0


@case
def running_lines_together_does_not_pass_the_gate_either(tmp):
    r, path = verbose_branch(tmp, "c2"); k = os.path.join(r, ".review-ready")
    names = [f"step_{i}" for i in range(118)]
    write(k, "overlays/02/parse.py", "def parse(text):\n" + "".join(
        "    " + " = ".join(names[i:i + 20]) + " = text\n" for i in range(0, 118, 20)) + "    return text.split(',')\n")
    p = json.load(open(path)); p["patches"][1] = {"name": "parse", "message": "msgs/02.txt",
                                                   "overlay": {"parse.py": "overlays/02/parse.py"}}
    json.dump(p, open(path, "w"))
    rc, out = build(r)                                   # 7 lines, each one statement, each far past 120 characters
    assert rc != 0 and "CRAMMED" in out and "characters" in out, out


@case
def deleting_blank_lines_does_not_pass_the_gate(tmp):
    r = repo(tmp, "c3"); k = os.path.join(r, ".review-ready")
    steps = [f"    step_{i} = text\n" for i in range(120)]
    sh(r, "git", "checkout", "-q", "feature")
    write(r, "parse.py", "def parse(text):\n" + "\n\n".join(steps) + "    return text.split(',')\n")
    sh(r, "git", "add", "parse.py"); sh(r, "git", "commit", "-qm", "parser"); sh(r, "git", "checkout", "-q", "main")
    write(k, "overlays/02/parse.py", "def parse(text):\n" + "".join(steps) + "    return text.split(',')\n")
    write(k, "msgs/02.txt", MSG.replace("count the rows of a generator", "add parse()"))
    path = plan(r); p = json.load(open(path))
    p["patches"].append({"name": "parse", "message": "msgs/02.txt", "overlay": {"parse.py": "overlays/02/parse.py"}})
    json.dump(p, open(path, "w"))
    rc, out = build(r)                                   # 122 lines against 360, and every statement is still there
    assert rc != 0 and "SIZE GATE FAILED" in out and "| `parse.py` | 122 | 122 | OVER" in out, out


@case
def write_mode_holds_each_patch_to_its_budget(tmp):
    r = repo(tmp, "wb")
    over = {"paths": None, "expect_fail_before": None, "overlay": {"f.py": "overlays/01/f.py"}}
    rc, out = build(r, source=None, branch="topic/new", onto=None, base="main", patch0=over)
    assert rc != 0 and '"budget"' in out and "topic/new" not in sh(r, "git", "branch", "--list"), out
    rc, out = build(r, source=None, branch="topic/new", onto=None, base="main", patch0=dict(over, budget=1))
    assert rc != 0 and "against its budget of 1" in out and "topic/new" not in sh(r, "git", "branch", "--list"), out
    rc, out = build(r, source=None, branch="topic/new", onto=None, base="main",
                    patch0=dict(over, budget=1, budget_ok="one call site, cannot be split"))
    assert rc == 0 and "topic/new" in sh(r, "git", "branch", "--list"), out


@case
def write_mode_makes_a_new_branch_and_lands_nothing(tmp):
    r = repo(tmp, "v")
    rc, out = build(r, source=None, branch="topic/new", onto=None, base="main", patch0={"paths": None, "expect_fail_before": None,
                    "budget": 20, "overlay": {"f.py": "overlays/01/f.py"}})
    assert rc == 0 and "write mode" in out, out
    assert "len(list(rows))" in sh(r, "git", "show", "topic/new:f.py")
    rc, out = build(r, "--land")
    assert rc != 0, out


def main():
    failed = 0
    for fn in CASES:
        tmp = tempfile.mkdtemp(prefix="review-ready-selftest-")
        try:
            fn(tmp)
            print("ok   " + fn.__name__.replace("_", " "))
        except Exception as exc:                               # a failed assertion is the report
            failed += 1
            print("FAIL " + fn.__name__.replace("_", " ") + "\n       " + str(exc).strip().replace("\n", "\n       ")[:1200])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(CASES)} cases, {failed} failing")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
