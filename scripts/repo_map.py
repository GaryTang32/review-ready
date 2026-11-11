#!/usr/bin/env python3
"""repo_map.py: read a whole Python repo and report how it is wired.

usage: repo_map.py [--repo PATH] [--src DIR ...] [--md FILE] [--json FILE] [--top N]
       repo_map.py --focus BASE..HEAD [--repo PATH] [--src DIR ...] [--md FILE] [--json FILE]
       repo_map.py --self-test

--focus maps only the code a branch changes and its neighbours, read at HEAD
from git (never the working tree), against the fork point of BASE and HEAD:
  act on these first  functions and classes the branch added that are used once
                  (inline them), never called, called only by tests, or already
                  in the tree under another name or the same shape; classes
                  with one method
  changed functions  for each: the call chain up from it, its callers with the
                  line that calls it (what they already guarantee), what it
                  calls, and the loops that hide a second loop

Answers the questions a maintainer asks before touching an unfamiliar tree:
what are the entry points, what calls what, what is public, what is dead,
where are the cycles, which functions are too big, and what has no test.

Everything comes from the AST plus git; nothing is imported or executed, so
it is safe on a tree you have not read. Call resolution is a documented
heuristic, not a type checker: a call is resolved when the name is
unambiguous within the file or across the repo, and counted as unresolved
otherwise. Unresolved calls are reported, never silently dropped, because a
dead-code claim is only as good as the resolver behind it.

Sections of the report:
  layout          packages, module count, lines, test files
  entry points    __main__ blocks, console_scripts, CLI parsers, schedulers
  layering        the import graph by package, every cycle, and a rewrite order: modules
                  dependencies first, those that import each other grouped (Tarjan)
  hot spots       fan-in, fan-out, churn from git, and size
  the big ones    functions over the length or nesting budget
  untested        public functions no test names or reaches
  dead            what no entry point reaches, by reachability over calls and names used as
                  values: nothing references it, only dead code does, or only tests do
  switches        modules that read config, env, flags, or the clock
  dense           short functions with too many paths or a condition with too many terms
  return shapes   functions returning a value on one path and None on another
  duplicates      copies by token similarity with the lines one function would save, renamed
                  copies of one body, and one name defined in two modules
  lazy imports    repo modules imported inside a function, usually hiding a cycle
  risks           security, data, performance, concurrency and logging patterns
  weak tests      test functions that assert nothing
  unresolved      calls the resolver could not place, so you can judge the rest
"""
from __future__ import annotations

import argparse
import ast
import builtins
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import code_metrics as cm
    import risk_detectors as rd
except ImportError:  # degrade rather than die when a sibling module is missing
    cm = rd = None

LENGTH_BUDGET = 48
DEPTH_BUDGET = 3
INLINE_MAX = 12       # a single-use helper longer than this is a seam, not a layer: inlining it grows the caller
# Files this interpreter could not parse. Every claim in the report is blind to
# them, so the report says so instead of leaving it on stderr.
UNPARSED = []

SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "build", "dist",
             ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", ".eggs"}
SWITCH_HINT = re.compile(
    r"\bos\.environ\b|\bgetenv\(|\bload_dotenv\(|argparse|click\.|typer\.|"
    r"\b(config|settings|cfg|CONFIG|SETTINGS)\b|tomllib|tomli|yaml\.|json\.load\(|"
    r"datetime\.(now|utcnow|today)|date\.today|time\.time|random\.|np\.random\.")
NEST = (ast.If, ast.For, ast.While, ast.Try, ast.AsyncFor)  # `with` is not a decision
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def is_test(rel):
    """A test directory, or a file whose name has test/tests/selftest as a word (test_x, x_test, selftest_x)."""
    base = os.path.basename(rel)
    return bool(re.search(r"(^|/)(tests?|__tests__)/", rel) or base == "conftest.py"
                or re.search(r"(^|_)(self)?tests?(_|\.py$)", base))


def max_depth(node, d=0):
    best = d
    for child in ast.iter_child_nodes(node):
        if isinstance(child, FUNC + (ast.ClassDef,)):
            continue
        nd = d
        if isinstance(child, NEST):
            chain = (isinstance(node, ast.If) and isinstance(child, ast.If)
                     and len(node.orelse) == 1 and node.orelse[0] is child)
            nd = d if chain else d + 1
        best = max(best, max_depth(child, nd))
    return best


def module_name(rel):
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


class Module:
    def __init__(self, rel, src, tree):
        self.rel, self.src, self.tree = rel, src, tree
        self.name = module_name(rel)
        self.is_test = is_test(rel)
        self.lines = src.count("\n") + 1
        self.defs = {}           # local name -> dict
        self.imports = {}        # bound name -> dotted target
        self.import_modules = set()
        self.calls = []          # (caller qualname or None, call expression string, lineno)
        self.has_main = False
        self.refs = Counter()
        self.all_exports = None
        self.switch_lines = 0


def parse_repo(root, src_dirs):
    mods = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if src_dirs and not any(rel == d or rel.startswith(d.rstrip("/") + "/") for d in src_dirs):
                continue
            try:
                src = open(path, encoding="utf-8", errors="replace").read()
                tree = ast.parse(src)
            except (SyntaxError, OSError) as exc:
                print(f"warning: cannot parse {rel}: {exc}", file=sys.stderr)
                UNPARSED.append((rel, f"{exc.__class__.__name__}: {exc}"[:120]))
                continue
            mods[rel] = analyse(Module(rel, src, tree))
    return mods


def analyse(m):
    for node in ast.walk(m.tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                m.imports[a.asname or a.name.split(".")[0]] = a.name
                m.import_modules.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import
                pkg = m.name.rsplit(".", node.level)[0] if "." in m.name else ""
                base = f"{pkg}.{base}".strip(".") if base else pkg
            m.import_modules.add(base)
            for a in node.names:
                m.imports[a.asname or a.name] = f"{base}.{a.name}".strip(".")
        elif isinstance(node, ast.If):
            t = node.test
            if (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name)
                    and t.left.id == "__name__"):
                m.has_main = True
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    try:
                        m.all_exports = set(ast.literal_eval(node.value))
                    except (ValueError, SyntaxError):
                        pass

    def walk_defs(body, cls=None, depth=0):
        for node in body:
            if isinstance(node, FUNC):
                qual = f"{cls}.{node.name}" if cls else node.name
                m.defs[qual] = dict(
                    name=node.name, qual=qual, cls=cls, line=node.lineno,
                    end=node.end_lineno or node.lineno,
                    length=(node.end_lineno or node.lineno) - node.lineno + 1,
                    depth=max_depth(node),
                    params=len(node.args.args) + len(node.args.kwonlyargs),
                    private=node.name.startswith("_"),
                    decorators=[ast.unparse(d) for d in node.decorator_list],
                    doc=bool(ast.get_docstring(node)),
                    kind="method" if cls else "function")
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        m.calls.append((qual, call_name(sub.func), sub.lineno))
            elif isinstance(node, ast.ClassDef):
                m.defs[node.name] = dict(
                    name=node.name, qual=node.name, cls=None, line=node.lineno,
                    end=node.end_lineno or node.lineno,
                    length=(node.end_lineno or node.lineno) - node.lineno + 1,
                    depth=0, params=0, private=node.name.startswith("_"),
                    decorators=[ast.unparse(d) for d in node.decorator_list],
                    doc=bool(ast.get_docstring(node)),
                    bases=[ast.unparse(b) for b in node.bases],
                    kind="class")
                walk_defs(node.body, node.name, depth + 1)
            else:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        m.calls.append((None, call_name(sub.func), sub.lineno))

    walk_defs(m.tree.body)
    # Every name used as a value: a function passed to sort(key=...), submitted
    # to a pool, put in a registry, or referenced in __all__ is used without
    # ever being syntactically called. Counting calls alone calls those dead.
    for node in ast.walk(m.tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            m.refs[node.id] += 1
        elif isinstance(node, ast.Attribute):
            m.refs[node.attr] += 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # getattr(obj, "handler") and registries keyed by name
            if node.value.isidentifier():
                m.refs[node.value] += 1
    m.switch_lines = sum(1 for line in m.src.splitlines() if SWITCH_HINT.search(line))
    return m


# Each alternative is anchored on both sides, so "repo_map" is not "repo" and
# "statistics" is not "stat". Order matters: the first match wins.
ROLES = [
    ("interface", r"(^|[_/])(cli|main|app|web|api|server|report|digest|notify|render|view|ui|dashboard)([_/]|$)"),
    ("orchestration", r"(^|[_/])(pipeline|runner|orchestrator|workflow|scheduler|job|jobs|task|tasks|backfill|batch|cron|driver)([_/]|$)"),
    ("ingest", r"(^|[_/])(fetch|http|client|feed|feeds|crawl|crawler|scrape|scraper|download|source|sources|ingest|reader|edgar|rss)([_/]|$)"),
    ("storage", r"(^|[_/])(db|database|store|storage|repository|cache|persist|dao|migration|migrations|schema)([_/]|$)"),
    ("domain", r"(^|[_/])(feature|features|semantic|semantics|event|events|transform|compute|score|scoring|predict|learn|model|models|rule|rules|metric|metrics|signal|strategy)([_/]|$)"),
    ("infra", r"(^|[_/])(config|settings|setting|log|logging|util|utils|helper|helpers|common|constants|stats|errors|exceptions|types)([_/]|$)"),
]


def role_of(rel, mod):
    base = rel[:-3]
    for role, rx in ROLES:
        if re.search(rx, base, re.I):
            return role
    text = mod.src[:4000]
    if re.search(r"requests\.|httpx\.|urlopen|aiohttp", text):
        return "ingest"
    if re.search(r"sqlite3|psycopg|sqlalchemy|CREATE TABLE|INSERT INTO", text, re.I):
        return "storage"
    return "domain"


def purpose_of(mod):
    """One line saying what the module is for: its docstring, or a summary
    built from the public names it defines."""
    doc = ast.get_docstring(mod.tree)
    if doc:
        first = doc.strip().splitlines()[0].strip()
        if first:
            return first[:100], "docstring"
    pub = [d["name"] for q, d in mod.defs.items() if not d["private"] and d["cls"] is None]
    if pub:
        return "defines " + ", ".join(pub[:4]) + ("..." if len(pub) > 4 else ""), "inferred"
    return "no docstring and no public names", "none"


def layer_depths(ig, rels):
    """Longest internal import chain below each module. 0 = imports nothing
    in this tree; higher = further from the leaves."""
    depth, visiting = {}, set()

    def go(n):
        if n in depth:
            return depth[n]
        if n in visiting:
            return 0  # a cycle: reported separately
        visiting.add(n)
        d = 1 + max([go(x) for x in ig.get(n, ()) if x in rels] or [-1])
        visiting.discard(n)
        depth[n] = d
        return d

    for r in rels:
        go(r)
    return depth


def call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts, cur = [func.attr], func.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr); cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def module_index(mods):
    """Dotted name -> rel, also by every unambiguous dotted suffix: `acme.client` finds
    src/acme/client.py, and `code_metrics` finds a script that a sibling imports by bare name."""
    suffix = defaultdict(set)
    for rel, m in mods.items():
        parts = m.name.split(".")
        for i in range(1, len(parts)):
            suffix[".".join(parts[i:])].add(rel)
    index = {name: next(iter(rels)) for name, rels in suffix.items() if len(rels) == 1}
    index.update({m.name: rel for rel, m in mods.items()})
    return index


def build_graph(mods):
    """Resolve calls to (module rel, qualname). Returns edges and unresolved."""
    by_module = {name: mods[rel] for name, rel in module_index(mods).items()}
    # A name imported from a package this tree does not hold is third-party or stdlib: `subprocess.run()`
    # is never this tree's run(), however unique that name is here.
    ours = {seg for m in mods.values() for seg in m.name.split(".")}
    # repo-wide index of simple names -> [(rel, qual)]
    index = defaultdict(list)
    for rel, m in mods.items():
        for qual, d in m.defs.items():
            index[d["name"]].append((rel, qual))
    edges, unresolved = [], Counter()
    for rel, m in mods.items():
        for caller, target, line in m.calls:
            if not target:
                continue
            head, _, tail = target.partition(".")
            hit = None
            # self.method() inside a class
            if head == "self" and caller and "." in caller:
                cls = caller.split(".")[0]
                cand = f"{cls}.{tail.split('.')[0]}" if tail else None
                if cand in m.defs:
                    hit = (rel, cand)
            # a name imported from a repo module
            if hit is None and head in m.imports:
                dotted = m.imports[head]
                full = f"{dotted}.{tail}" if tail else dotted
                mod_part, _, leaf = full.rpartition(".")
                if mod_part in by_module and leaf in by_module[mod_part].defs:
                    hit = (by_module[mod_part].rel, leaf)
                elif dotted in by_module and tail and tail.split(".")[0] in by_module[dotted].defs:
                    hit = (by_module[dotted].rel, tail.split(".")[0])
            # a name defined in this module
            if hit is None and not tail and head in m.defs:
                hit = (rel, head)
            external = head in m.imports and m.imports[head].split(".")[0] not in ours
            # unique across the repo. For obj.method() the object's type is
            # unknown, so a leaf that names exactly one definition in the tree
            # is taken as that definition; ambiguous leaves stay unresolved.
            if hit is None and not external:
                leaf = target.split(".")[-1]
                # production code never calls into a test module: `fh.write()` is not tests' write()
                cands = [c for c in index.get(leaf, []) if m.is_test or not mods[c[0]].is_test]
                if len(cands) == 1:
                    hit = cands[0]
            if hit:
                edges.append((rel, caller, hit[0], hit[1], line))
            elif head not in ("self", "cls") and not external \
                    and not target.startswith(("os.", "sys.", "re.", "json.")):
                unresolved[target] += 1
    return edges, unresolved


def import_graph(mods):
    by_name = {m.name: rel for rel, m in mods.items()}
    # A script that puts its own directory on sys.path imports a sibling by
    # bare name ("import switch_detectors"), which matches no dotted module
    # path. Index the last path segment too, but only when it is unambiguous.
    leaf = defaultdict(list)
    for rel, m in mods.items():
        leaf[m.name.rsplit(".", 1)[-1]].append(rel)
    g = defaultdict(set)
    for rel, m in mods.items():
        # `from app import b` names the module in the alias, not in the `from` part
        for target in m.import_modules | {t for t in m.imports.values() if t in by_name}:
            if not target:
                continue
            hit = False
            for name, trel in by_name.items():
                if target == name or target.startswith(name + "."):
                    hit = True
                    if trel != rel:
                        g[rel].add(trel)
            if not hit:
                head = target.split(".")[0]
                cands = leaf.get(head, [])
                if len(cands) == 1 and cands[0] != rel:
                    g[rel].add(cands[0])
    return g


def find_cycles(g):
    cycles, colour, stack = [], {}, []

    def visit(n):
        colour[n] = 1
        stack.append(n)
        for nxt in sorted(g.get(n, ())):
            if colour.get(nxt, 0) == 0:
                visit(nxt)
            elif colour.get(nxt) == 1 and nxt in stack:
                cycles.append(stack[stack.index(nxt):] + [nxt])
        stack.pop()
        colour[n] = 2

    for n in sorted(g):
        if colour.get(n, 0) == 0:
            visit(n)
    seen, out = set(), []
    for c in cycles:
        key = frozenset(c)
        if key not in seen:
            seen.add(key); out.append(c)
    return out


def rewrite_order(g, rels):
    """Strongly connected components of the import graph (Tarjan), dependencies first.

    A module is rewritten after everything it imports, so its dependencies no longer move under
    it. Modules that import each other form one group: no order within it is safe.
    """
    keep = set(rels)
    index, low, on, stack, groups = {}, {}, set(), [], []
    for root in rels:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root); on.add(root)
        work = [(root, iter(sorted(x for x in g.get(root, ()) if x in keep)))]
        while work:
            node, it = work[-1]
            nxt = next((w for w in it if w not in index or w in on), None)
            if nxt is None:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    group = []
                    while not group or group[-1] != node:
                        group.append(stack.pop()); on.discard(group[-1])
                    groups.append(sorted(group))
            elif nxt in index:
                low[node] = min(low[node], index[nxt])
            else:
                index[nxt] = low[nxt] = len(index)
                stack.append(nxt); on.add(nxt)
                work.append((nxt, iter(sorted(x for x in g.get(nxt, ()) if x in keep))))
    return groups


def churn(root, rels):
    out = {}
    r = subprocess.run(["git", "-C", root, "log", "--no-merges", "--format=%H", "--name-only"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return out
    counts = Counter(l.strip() for l in r.stdout.splitlines() if l.strip() and "/" in l or l.endswith(".py"))
    for rel in rels:
        out[rel] = counts.get(rel, 0)
    return out


CONFIG_EXT = (".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".properties")
CONFIG_KEY = re.compile(r"""^\s*["']?([A-Za-z_][\w.-]*)["']?\s*[:=]""")
# Sections a tool reads rather than this code, where "nothing reads it" is expected.
TOOL_SECTION = re.compile(r"^(tool|build-system|project|black|isort|ruff|mypy|pytest|flake8|"
                          r"coverage|setuptools|poetry|metadata|options|packages)\b", re.I)
LOCKFILE = re.compile(r"(^|/)(package-lock|yarn|poetry|uv|Cargo|Gemfile)\.")


def config_switches(root, code_text):
    """Config-file keys that no Python file in the tree mentions.

    A key nobody reads is a dead switch: the tree tells a reader the behaviour
    is configurable when it is not, and whoever sets it sees nothing happen.
    The AST cannot find these because the key is not Python. Tool sections are
    skipped, since the tool reads those.
    """
    # One regex search of the whole tree per key is quadratic: a repo with a hundred config files
    # spent half its run here. A key made only of word characters is read if and only if it is one
    # of the tree's words, so those are a set lookup; other keys are searched once and remembered.
    words = set(re.findall(r"\w+", code_text))
    seen = {}

    def read_somewhere(key):
        if key not in seen:
            seen[key] = key in words if re.fullmatch(r"\w+", key) else bool(re.search(
                r"""["']""" + re.escape(key) + r"""["']|\b""" + re.escape(key) + r"\b", code_text))
        return seen[key]

    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(files):
            if not fn.endswith(CONFIG_EXT):
                continue
            rel = os.path.relpath(os.path.join(base, fn), root).replace(os.sep, "/")
            if LOCKFILE.search(rel):
                continue
            try:
                text = open(os.path.join(base, fn), encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            section, dead = "", []
            for line in text.splitlines():
                bracket = re.match(r"^\s*\[([^\]]+)\]", line)
                if bracket:
                    section = bracket.group(1)
                    continue
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if not line[:1].isspace() and stripped.endswith(":"):
                    section = stripped[:-1]
                    continue
                m = CONFIG_KEY.match(line)
                if not m:
                    continue
                key = m.group(1)
                if len(key) < 3 or TOOL_SECTION.match(section) or TOOL_SECTION.match(key):
                    continue
                if not read_somewhere(key):
                    dead.append((section, key))
            if dead:
                out.append((rel, dead))
    return out


def _unevaluated_annotations(tree):
    """ids of nodes inside annotations that never run: every annotation under
    `from __future__ import annotations`, and a local variable's annotation always."""
    postponed = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                    and any(a.name == "annotations" for a in n.names) for n in tree.body)
    roots = []
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        if postponed:
            a = fn.args
            roots += [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]
                      if x is not None and x.annotation is not None]
            roots += [fn.returns] if fn.returns is not None else []
        roots += [n.annotation for n in ast.walk(fn)
                  if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    if postponed:
        roots += [n.annotation for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)]
    return {id(n) for r_ in roots for n in ast.walk(r_)}


def python_floor(root, mods):
    """The minimum Python the tree declares, and syntax in the tree above it.

    Without this, "does this spelling import on the tree's minimum Python?" is
    a guess in both directions: every `X | Y`, `match`, and builtin generic is
    either a false NAK or a missed one.
    """
    floor, where = None, None
    for cfg in ("pyproject.toml", "setup.cfg", "setup.py"):
        p_ = os.path.join(root, cfg)
        if not os.path.exists(p_):
            continue
        text = open(p_, encoding="utf-8", errors="replace").read()
        m_ = re.search(r"(requires-python|python_requires)\s*=\s*[\"']\s*>=?\s*(\d+)\.(\d+)", text)
        if m_:
            floor, where = (int(m_.group(2)), int(m_.group(3))), cfg
            break
    above = []
    if floor:
        for rel, m in sorted(mods.items()):
            unevaluated = _unevaluated_annotations(m.tree)
            for node in ast.walk(m.tree):
                if id(node) in unevaluated:
                    continue
                need = None
                if node.__class__.__name__ == "Match":
                    need = ((3, 10), "match statement")
                elif isinstance(node, ast.NamedExpr):
                    need = ((3, 8), "walrus")
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and any(
                        isinstance(side, ast.Constant) and side.value is None for side in (node.left, node.right)):
                    need = ((3, 10), "`X | None` evaluated at runtime")
                if need and need[0] > floor:
                    above.append(dict(file=rel, line=node.lineno, what=need[1],
                                      needs=f"{need[0][0]}.{need[0][1]}"))
    return dict(floor=f"{floor[0]}.{floor[1]}" if floor else None, where=where, above=above[:12])


def entry_points(root, mods):
    eps = []
    for rel, m in sorted(mods.items()):
        if m.has_main:
            eps.append((rel, "__main__ block"))
        for qual, d in m.defs.items():
            for dec in d.get("decorators", []):
                if re.search(r"\b(command|group|task|route|app\.|cli\.|schedule|cron|periodic)\b", dec):
                    eps.append((f"{rel}:{qual}", f"@{dec[:40]}"))
        if re.search(r"argparse\.ArgumentParser|click\.command|typer\.Typer", m.src):
            eps.append((rel, "CLI parser"))
    for cfg in ("pyproject.toml", "setup.py", "setup.cfg"):
        p = os.path.join(root, cfg)
        if os.path.exists(p):
            text = open(p, encoding="utf-8", errors="replace").read()
            for m2 in re.finditer(r"^\s*([\w-]+)\s*=\s*[\"']?([\w.]+):([\w.]+)[\"']?\s*$", text, re.M):
                eps.append((f"{m2.group(2)}:{m2.group(3)}", f"console_script {m2.group(1)}"))
    seen, out = set(), []
    for e in eps:
        if e not in seen:
            seen.add(e); out.append(e)
    return out


# Decorators that wrap a function without registering it anywhere. Any other decorator (a route, a
# CLI command, a fixture, a task) is taken as registration, which keeps the function alive.
PLAIN_DECORATOR = re.compile(
    r"^(property|staticmethod|classmethod|abstractmethod|cached_property|contextmanager|asynccontextmanager|"
    r"lru_cache|cache|wraps|overload|dataclass|total_ordering|"
    r"(functools|contextlib|abc|typing|dataclasses)\.\w+|\w+\.(setter|getter|deleter))\b")


def bodies(m):
    """(qualname or None, node) for every top-level function, method, and other statement of a module."""
    for node in m.tree.body:
        if isinstance(node, FUNC):
            yield node.name, node
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                yield (f"{node.name}.{sub.name}" if isinstance(sub, FUNC) else node.name), sub
        else:
            yield None, node


def value_refs(m, node, imported, by_name, repo_names):
    """Definitions that `node` names as a value; reference_graph() states the rules."""
    bound = {a.arg for a in ast.walk(node) if isinstance(a, ast.arg)}
    bound |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load) and n.id not in bound:
                out |= {(m.rel, n.id)} if n.id in m.defs else imported(m.imports.get(n.id, ""))
        elif isinstance(n, ast.Attribute):
            via = n.value.id if isinstance(n.value, ast.Name) and n.value.id not in bound else None
            target = m.imports.get(via)
            if target is None:
                out |= by_name[n.attr]
            elif target.split(".")[0] in repo_names:      # else third-party: subprocess.run is not our run()
                out |= imported(f"{target}.{n.attr}") or by_name[n.attr]
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.isidentifier():
            out |= by_name[n.value]
    return out


def class_refs(mods, rel, qual, by_name):
    """What a class keeps alive: its base classes in this tree, and the methods something may call unseen.

    Dunders are called by the language. A method may override one its base calls: for a builtin
    base that is knowable (list has no unregister()); for any other base this tree does not
    define it is not, so every method of such a class stays alive.
    """
    m = mods[rel]
    out, inherited, unknown = set(), set(), False
    for base in (b.split("[")[0].split(".")[-1] for b in m.defs[qual].get("bases", [])):
        base_classes = {k for k in by_name[base] if mods[k[0]].defs[k[1]]["kind"] == "class"}
        out |= base_classes
        builtin = getattr(builtins, base, None)
        if isinstance(builtin, type):
            inherited |= set(dir(builtin))
        elif not base_classes:
            unknown = True
    return out | {(rel, q) for q, x in m.defs.items() if x["cls"] == qual
                  and (unknown or x["name"].startswith("__") or x["name"] in inherited)}


def reference_graph(mods, edges):
    """{caller: definitions it calls or names as a value}, the graph a dead-code claim has to survive.

    Calls come from the resolver. A bare name can only be a definition in the same module or an
    imported one, alias included. An attribute through an imported module is exact. Any other
    attribute, and any string that is an identifier, keeps EVERY definition of that name alive: the
    object's type is unknown, and getattr() and registries name functions by string.
    """
    by_module = {name: mods[rel] for name, rel in module_index(mods).items()}
    repo_names = {seg for m in mods.values() for seg in m.name.split(".")}
    by_name = defaultdict(set)
    for rel, m in mods.items():
        for qual, d in m.defs.items():
            by_name[d["name"]].add((rel, qual))

    def imported(dotted):
        mod_part, _, leaf = dotted.rpartition(".")
        hit = by_module.get(mod_part)
        return {(hit.rel, leaf)} if hit and leaf in hit.defs else set()

    g = defaultdict(set)
    for src_rel, caller, dst_rel, dst_qual, _line in edges:
        g[(src_rel, caller)].add((dst_rel, dst_qual))
    for rel, m in sorted(mods.items()):
        for qual, node in bodies(m):
            g[(rel, qual)] |= value_refs(m, node, imported, by_name, repo_names)
        for qual, d in m.defs.items():
            if d["kind"] == "class":
                g[(rel, qual)] |= class_refs(mods, rel, qual, by_name)
    return g


def reach(g, roots):
    seen, todo = set(roots), list(roots)
    while todo:
        for nxt in g.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def find_dead(mods, edges, eps):
    """Definitions no entry point reaches, as rows grouped into dead subtrees.

    Roots: module-level code, anything registered by a decorator, __all__, console scripts and
    main(). A definition with no path from a root is dead even when something calls it, if the
    caller is dead too; counting references alone keeps such a subtree alive for ever.
    """
    g = reference_graph(mods, edges)
    index = module_index(mods)
    prod = {(rel, q) for rel, m in mods.items() if not m.is_test for q in m.defs}
    roots = {(rel, None) for rel, m in mods.items() if not m.is_test}
    for rel, qual in prod:
        d, m = mods[rel].defs[qual], mods[rel]
        if (d["name"] == "main" or any(not PLAIN_DECORATOR.match(dec) for dec in d["decorators"])
                or (m.all_exports is not None and d["name"] in m.all_exports)):
            roots.add((rel, qual))
    for where, _how in eps:                       # console scripts: "pkg.mod:func"
        modname, _, fn = where.partition(":")
        if fn and modname in index:
            roots.add((index[modname], fn))
    live = reach(g, roots)
    tested = reach(g, {(rel, None) for rel, m in mods.items() if m.is_test}
                   | {(rel, q) for rel, m in mods.items() if m.is_test for q in m.defs})
    dead = {k for k in prod - live if not (mods[k[0]].defs[k[1]]["cls"] and (k[0], mods[k[0]].defs[k[1]]["cls"]) in prod - live)}
    callers = defaultdict(set)
    for a, bs in g.items():
        for b in bs:
            if a in dead and a != b:
                callers[b].add(a)
    rows = []
    for rel, qual in sorted(dead):
        d = mods[rel].defs[qual]
        above = sorted(f"{r}:{q}" for r, q in callers[(rel, qual)])
        why = ("only tests reach it" if (rel, qual) in tested else
               f"reached only from dead code: {above[0]}" if above else "nothing references it")
        rows.append(dict(file=rel, qual=qual, line=d["line"], kind=d["kind"], private=d["private"],
                         lines=d["length"], why=why, head=not above))
    return rows


def analyse_repo(root, src_dirs, top):
    mods = parse_repo(root, src_dirs)
    if not mods:
        return None
    edges, unresolved = build_graph(mods)
    ig = import_graph(mods)
    cycles = find_cycles(ig)
    ch = churn(root, list(mods))
    eps = entry_points(root, mods)

    prod = {r: m for r, m in mods.items() if not m.is_test}
    tests = {r: m for r, m in mods.items() if m.is_test}

    refs = Counter()
    for m in mods.values():
        refs.update(m.refs)
    called = Counter()
    called_from_test = set()
    callers_of = defaultdict(set)
    for src_rel, caller, dst_rel, dst_qual, _line in edges:
        called[(dst_rel, dst_qual)] += 1
        callers_of[(dst_rel, dst_qual)].add(src_rel)
        if mods[src_rel].is_test:
            called_from_test.add((dst_rel, dst_qual))

    # Pinning, in three honest tiers. A bare-name match against every identifier
    # in every test file calls `load`, `rows` or `parse` "tested" because some
    # test has a local of that name, and that list feeds the characterization
    # gate. So: called directly by a test; reachable from a test through the
    # call graph; or merely named in a test, which proves nothing.
    out_edges = defaultdict(set)
    for src_rel, caller, dst_rel, dst_qual, _line in edges:
        out_edges[(src_rel, caller)].add((dst_rel, dst_qual))
    reachable, frontier = set(called_from_test), list(called_from_test)
    while frontier:
        node = frontier.pop()
        for nxt in out_edges.get(node, ()):
            if nxt not in reachable:
                reachable.add(nxt); frontier.append(nxt)
    # names any test file mentions at all (import lists, strings, patches)
    test_text = "\n".join(m.src for m in tests.values())
    test_names = set(re.findall(r"[A-Za-z_]\w*", test_text))

    fan_in = Counter()
    fan_out = Counter()
    for a, bs in ig.items():
        fan_out[a] = len(bs)
        for b in bs:
            fan_in[b] += 1

    ep_files = {e[0].split(":")[0] for e in eps}
    dead = find_dead(mods, edges, eps)
    untested, big = [], []
    for rel, m in prod.items():
        for qual, d in m.defs.items():
            key = (rel, qual)
            n = d["name"]
            is_entry = rel in ep_files or n in ("main", "__init__") or n.startswith("__")
            if (d["kind"] != "class" and not d["private"] and key not in called_from_test
                    and not is_entry and called[key] + refs[n] > 0):
                tier = ("reachable from a test, never called by one" if key in reachable
                        else "named in a test only: UNPROVEN" if n in test_names
                        else "no test reaches it")
                untested.append(dict(file=rel, qual=qual, line=d["line"], callers=len(callers_of[key]), tier=tier))
            if d["kind"] != "class" and (d["length"] > LENGTH_BUDGET or d["depth"] > DEPTH_BUDGET):
                big.append(dict(file=rel, qual=qual, line=d["line"], length=d["length"],
                                depth=d["depth"], params=d["params"], churn=ch.get(rel, 0)))

    hot = []
    for rel, m in prod.items():
        hot.append(dict(file=rel, lines=m.lines, defs=len(m.defs), fan_in=fan_in[rel],
                        fan_out=fan_out[rel], churn=ch.get(rel, 0), switches=m.switch_lines))
    hot.sort(key=lambda h: (-(h["churn"] * max(h["fan_in"], 1)), -h["lines"]))
    big.sort(key=lambda b: (-b["length"], -b["depth"]))
    untested.sort(key=lambda u: -u["callers"])

    pkgs = defaultdict(list)
    for rel in prod:
        pkgs[os.path.dirname(rel) or "."].append(rel)

    dead_config = config_switches(root, "\n".join(m.src for m in mods.values()))
    floor = python_floor(root, mods)

    dense, shapes, stale_docs, weak_tests, lazy_imports, loop_cost_rows = [], [], [], [], [], []
    same_name, prints = defaultdict(list), defaultdict(list)
    risks = defaultdict(list)
    by_name = {m.name: rel for rel, m in mods.items()}
    shingled, lengths = {}, {}
    if cm:
        for rel, m in sorted(mods.items()):
            src_lines = m.src.splitlines()
            for node in ast.walk(m.tree):
                if not isinstance(node, FUNC):
                    continue
                if m.is_test:
                    if node.name.startswith("test") and cm.asserts_nothing(node):
                        weak_tests.append(dict(file=rel, name=node.name, line=node.lineno))
                    fp = cm.fingerprint(node)
                    if fp:
                        prints["test:" + fp].append((rel, node.name, node.lineno))
                    continue
                got = cm.measure(node)
                if got["dense"]:
                    dense.append(dict(file=rel, name=node.name, line=node.lineno, why="; ".join(got["dense"])))
                if got["return_shape"]:
                    shapes.append(dict(file=rel, name=node.name, line=node.lineno, why=got["return_shape"]))
                if got["loop_costs"]:
                    loop_cost_rows.append(dict(file=rel, name=node.name, line=node.lineno, why=got["loop_costs"][:2]))
                if got["stale_doc_params"]:
                    stale_docs.append(dict(file=rel, name=node.name, line=node.lineno,
                                           params=got["stale_doc_params"]))
                extra = [w for w in got["over_budget"] if "parameters" in w or "locals" in w]
                if extra and not any(b["file"] == rel and b["line"] == node.lineno for b in big):
                    big.append(dict(file=rel, qual=node.name, line=node.lineno, length=got["length"],
                                    depth=got["depth"], params=got["params"], churn=ch.get(rel, 0)))
                fp = cm.fingerprint(node)
                if fp:
                    prints[fp].append((rel, node.name, node.lineno))
                sh = cm.copy_shingles(src_lines, node)
                if sh:
                    shingled[(rel, node.name, node.lineno)] = sh
                    lengths[(rel, node.name, node.lineno)] = got["length"]
                # a repo module imported inside a function body
                for sub in ast.walk(node):
                    targets = []
                    if isinstance(sub, ast.ImportFrom):
                        base = sub.module or ""
                        if sub.level:
                            pkg = m.name.rsplit(".", sub.level)[0] if "." in m.name else ""
                            base = f"{pkg}.{base}".strip(".") if base else pkg
                        # `from . import db` names the module in the alias, not in `module`
                        targets = [base] if sub.module else [f"{base}.{a.name}".strip(".") for a in sub.names]
                    elif isinstance(sub, ast.Import):
                        targets = [a.name for a in sub.names]
                    for target in targets:
                        hit = [n for n in by_name if target == n or n.endswith("." + target)]
                        if hit and by_name[hit[0]] != rel:
                            lazy_imports.append(dict(file=rel, name=node.name, line=sub.lineno,
                                                     target=by_name[hit[0]]))
            if not m.is_test:
                for node in m.tree.body:
                    if isinstance(node, FUNC) and not node.name.startswith("__") \
                            and node.name not in ("main", "run", "setup", "handle"):
                        same_name[node.name].append(rel)
    if rd:
        for rel, m in sorted(mods.items()):
            for ln, name, group, text in rd.scan_source(m.src, is_test=m.is_test):
                risks[(group, name)].append((rel, ln, text))
    dup_names = {n: sorted(set(v)) for n, v in same_name.items() if len(set(v)) > 1}
    # Copies by similarity: the shape hash only finds bodies that are identical once renamed.
    copies = [dict(similarity=low, members=[f"{r_}:{n_} (line {l_})" for r_, n_, l_ in members],
                   exact=low >= cm.COPY_EXACT,
                   removable=sum(lengths[k] for k in members) - max(lengths[k] for k in members))
              for low, members in (cm.copy_clusters(cm.near_copies(shingled)) if cm else [])]
    copies.sort(key=lambda c: -c["removable"])
    in_copy = {(r_, n_) for c in copies for r_, n_ in (x.split(" (")[0].split(":") for x in c["members"])}
    dup_shapes = [v for k, v in prints.items() if len(v) > 1 and not k.startswith("test:")
                  and not all((r_, n_) in in_copy for r_, n_, _l in v)]
    dup_tests = [v for k, v in prints.items() if len(v) > 2 and k.startswith("test:")]

    depths = layer_depths(ig, set(prod))
    arch = []
    for rel, m in sorted(prod.items()):
        purpose, how = purpose_of(m)
        arch.append(dict(file=rel, role=role_of(rel, m), purpose=purpose, purpose_from=how,
                         layer=depths.get(rel, 0), lines=m.lines, defs=len(m.defs),
                         fan_in=fan_in[rel], fan_out=fan_out[rel],
                         dir=os.path.dirname(rel) or "."))
    by_role = defaultdict(list)
    for a in arch:
        by_role[a["role"]].append(a)

    # Architecture findings: what stops an outsider understanding the tree.
    findings = []
    biggest_dir = max(pkgs.items(), key=lambda kv: len(kv[1])) if pkgs else (None, [])
    if len(biggest_dir[1]) >= 8 and len({a["role"] for a in arch if a["dir"] == biggest_dir[0]}) >= 3:
        roles_here = Counter(a["role"] for a in arch if a["dir"] == biggest_dir[0])
        findings.append(dict(
            kind="flat package",
            detail=f"{biggest_dir[0]} holds {len(biggest_dir[1])} modules covering "
                   f"{len(roles_here)} roles ({', '.join(sorted(roles_here))}). Nothing about a "
                   f"file's location says what it does.",
            fix="Group by role into subpackages; the moves are pure renames, one patch each."))
    nodoc = [a for a in arch if a["purpose_from"] != "docstring"]
    if nodoc:
        findings.append(dict(
            kind="modules with no docstring", detail=f"{len(nodoc)} of {len(arch)} modules open with no "
            "sentence saying what they are for, so the reader must read the code to find out.",
            fix="One line at the top of each: what it is for and who calls it."))
    # Only a real entry point is "the top": a module with a __main__ block or a
    # console_script. A module merely named report/ or view/ is rendering that
    # other code is entitled to call, and flagging it is noise, as a tree
    # reorganised into role packages shows immediately.
    ep_modules = {e[0].split(":")[0] for e in eps}
    for a in arch:
        if a["file"] in ep_modules and a["fan_in"] > 0:
            findings.append(dict(kind="entry point imported by others",
                                 detail=f"{a['file']} is an entry point, but {a['fan_in']} module(s) import it. "
                                        "Whatever they need is not entry-point code.",
                                 fix="Move the shared part down a layer; an entry point is imported by nobody."))
    for a in arch:
        if a["lines"] > 500:
            findings.append(dict(kind="module too big to skim", detail=f"{a['file']} is {a['lines']} lines and "
                                 f"defines {a['defs']} names.",
                                 fix="Split along the seam its own sections already suggest; pure moves first."))
    for name, where in sorted(dup_names.items())[:6]:
        findings.append(dict(kind="one name, two definitions",
                             detail=f"`{name}` is defined in " + " and ".join(where[:3]) + ".",
                             fix="Keep one. If they differ, the difference is the finding; if they do not, "
                                 "one of them is a copy."))
    for c in copies[:6]:
        findings.append(dict(kind="copied function",
                             detail=" and ".join(c["members"][:3]) + f" are at least {c['similarity']:.0%} alike: "
                                    f"one would do, saving {c['removable']} lines.",
                             fix="Keep one, called from every place; where they differ, the difference is a parameter "
                                 "or a bug in one of them."))
    for members in dup_shapes[:6]:
        findings.append(dict(kind="renamed copy",
                             detail=" and ".join(f"{r_}:{n_}" for r_, n_, _l in members[:3])
                                    + " have the same body once names and constants are erased.",
                             fix="One function with a parameter, called from both places."))
    for cfg_rel, cfg_keys in dead_config:
        findings.append(dict(
            kind="config key nothing reads",
            detail=f"{cfg_rel} declares "
                   + ", ".join(f"`{(sec + '.') if sec else ''}{k}`" for sec, k in cfg_keys[:5])
                   + (f" and {len(cfg_keys) - 5} more" if len(cfg_keys) > 5 else "")
                   + (": no Python file mentions them, so setting them does nothing."
                      if len(cfg_keys) > 1 else
                      ": no Python file mentions it, so setting it does nothing."),
            fix="Read the key where the behaviour lives, or delete it and the line that documents it."))
    misdir = [a for a in arch if a["dir"] != "." and os.path.basename(a["dir"]) in {"utils", "helpers", "common", "misc"}]
    if misdir:
        findings.append(dict(kind="catch-all package",
                             detail="a utils/helpers/common package holds " + ", ".join(a["file"] for a in misdir[:5]),
                             fix="Name the package for what the code is, or move each module next to its one caller."))

    return dict(root=root, modules=len(mods), prod=len(prod), tests=len(tests),
                total_lines=sum(m.lines for m in mods.values()),
                packages={k: len(v) for k, v in sorted(pkgs.items())},
                entry_points=eps, cycles=cycles, hot=hot[:top], big=big[:top],
                untested=untested[:top], dead=dead[:top],
                dead_total=len(dead), dead_lines=sum(d["lines"] for d in dead), untested_total=len(untested), big_total=len(big),
                edges=len(edges), unresolved=unresolved.most_common(12),
                unresolved_total=sum(unresolved.values()),
                switch_modules=sorted(((m.switch_lines, rel) for rel, m in prod.items()
                                       if m.switch_lines), reverse=True)[:top],
                dead_config=dead_config, python_floor=floor, unparsed=list(UNPARSED), loop_costs=loop_cost_rows[:top],
                dense=dense[:top], return_shapes=shapes[:top], stale_docs=stale_docs[:top],
                weak_tests=weak_tests[:top], lazy_imports=lazy_imports[:top],
                dup_names=dup_names, dup_shapes=dup_shapes[:top], dup_tests=dup_tests[:top],
                copies=copies[:top], copies_removable=sum(c["removable"] for c in copies),
                copies_exact=sum(c["removable"] for c in copies if c["exact"]),
                rewrite_order=rewrite_order(ig, sorted(prod)),
                risks={f"{g}|{n}": v for (g, n), v in risks.items()},
                architecture=arch, by_role={k: [a["file"] for a in v] for k, v in sorted(by_role.items())},
                arch_findings=findings,
                import_graph={k: sorted(v) for k, v in sorted(ig.items())})


def render(r, top):
    L = [f"# Repo map: {r['root']}", "",
         f"{r['prod']} production modules, {r['tests']} test modules, "
         f"{r['total_lines']} lines, {r['edges']} resolved calls.", ""]
    L += ["## Layout", "", "| package | modules |", "|---|---:|"]
    for p, n in r["packages"].items():
        L.append(f"| {p} | {n} |")
    L.append("")
    L += ["## What each module is for", "",
          "Role is inferred from the name and the code; purpose is the module docstring's",
          "first line, or, where there is none, a list of the public names it defines.",
          "A row whose purpose reads `defines ...` is a module an outsider cannot place",
          "without reading it.", "",
          "| layer | role | file | purpose | lines | in | out |",
          "|---:|---|---|---|---:|---:|---:|"]
    for a in sorted(r["architecture"], key=lambda a: (a["layer"], a["role"], a["file"])):
        mark = "" if a["purpose_from"] == "docstring" else " *"
        L.append(f"| {a['layer']} | {a['role']} | {a['file']} | {a['purpose']}{mark} "
                 f"| {a['lines']} | {a['fan_in']} | {a['fan_out']} |")
    L += ["", "`*` = no module docstring; the purpose shown was inferred.", ""]
    L += ["## Grouped by role", "",
          "This is the shape the tree would have if a file's location told you its job.",
          "Layer 0 imports nothing else in the tree; each higher layer sits on the ones below.", ""]
    ORDER = ["interface", "orchestration", "domain", "ingest", "storage", "infra"]
    for role in ORDER:
        files = r["by_role"].get(role)
        if not files:
            continue
        L.append(f"- **{role}/** ({len(files)})")
        for f in files:
            L.append(f"  - {f}")
    for role, files in r["by_role"].items():
        if role not in ORDER:
            L.append(f"- **{role}/** ({len(files)}): " + ", ".join(files))
    L.append("")
    if r["arch_findings"]:
        L += ["## What stops an outsider understanding this tree", ""]
        for f in r["arch_findings"][:12]:
            L.append(f"- **{f['kind']}.** {f['detail']}")
            L.append(f"  Fix: {f['fix']}")
        L.append("")
    if r.get("unparsed"):
        L += ["## Files this interpreter could not parse", "",
              "Every claim below (dead, untested, cycles, duplicates) is blind to these files. Rerun with an",
              "interpreter at least as new as the tree's floor before trusting an absence.", ""]
        L += [f"- {rel}: {why}" for rel, why in r["unparsed"]]
        L.append("")
    fl = r.get("python_floor") or {}
    L += ["## Python floor", ""]
    if fl.get("floor"):
        L.append(f"The tree declares Python >= {fl['floor']} ({fl['where']}). Every spelling question is answered "
                 "against that, not against the interpreter on this machine.")
        for a in fl.get("above", []):
            L.append(f"- {a['file']}:{a['line']}: {a['what']} needs {a['needs']}, above the declared floor")
    else:
        L.append("No `requires-python` found. Whether `X | Y`, `match`, or `list[str]` is legal here is "
                 "undecidable from the tree; say so in the audit's limits, and ask.")
    L.append("")
    L += ["## Entry points", ""]
    if r["entry_points"]:
        L += [f"- `{a}` ({b})" for a, b in r["entry_points"][:top]]
    else:
        L.append("- none found: nothing in this tree is started directly.")
    L.append("")
    L += ["## Layering", ""]
    if r["cycles"]:
        L.append("Import cycles (each one means the two modules are really one):")
        for c in r["cycles"][:10]:
            L.append(f"- {' -> '.join(c)}")
    else:
        L.append("No import cycles.")
    L.append("")
    if r.get("rewrite_order"):
        L += ["Rewrite order, dependencies first, so each module's imports are settled before it is touched.",
              "Modules in one bracket import each other and move together:", ""]
        L += [f"{i}. " + (g[0] if len(g) == 1 else "[" + ", ".join(g) + "]") for i, g in enumerate(r["rewrite_order"], 1)]
        L.append("")
    L += ["## Hot spots", "",
          "Ordered by churn times fan-in: the code most changed and most depended on.", "",
          "| file | lines | defs | fan-in | fan-out | commits | switch lines |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for h in r["hot"]:
        L.append(f"| {h['file']} | {h['lines']} | {h['defs']} | {h['fan_in']} | {h['fan_out']} "
                 f"| {h['churn']} | {h['switches']} |")
    L.append("")
    L += [f"## The big ones ({r['big_total']} over {LENGTH_BUDGET} lines or {DEPTH_BUDGET} levels)", "",
          "| file | function | line | length | depth | params | commits |", "|---|---|---:|---:|---:|---:|---:|"]
    for b in r["big"]:
        L.append(f"| {b['file']} | {b['qual']} | {b['line']} | {b['length']} | {b['depth']} "
                 f"| {b['params']} | {b['churn']} |")
    L.append("")
    if r.get("dense") or r.get("return_shapes") or r.get("stale_docs"):
        L += ["## Short but dense, and inconsistent returns", "",
              "Inside the length and depth budget, so the primary gate passes them. A condition with",
              "more than three terms wants a name; a function returning a value or None makes every",
              "caller carry an `if`.", ""]
        for x in r.get("dense", []):
            L.append(f"- {x['file']}:{x['line']} `{x['name']}`: {x['why']}")
        for x in r.get("return_shapes", []):
            L.append(f"- {x['file']}:{x['line']} `{x['name']}`: {x['why']}")
        for x in r.get("stale_docs", []):
            L.append(f"- {x['file']}:{x['line']} `{x['name']}`: docstring documents "
                     f"{', '.join(x['params'])}, which the signature does not have")
        L.append("")
    if r.get("loop_costs"):
        L += ["## Loops that hide a second loop", "",
              "Shape, not slowness: n is usually small and brute force is often right. The review action is",
              "to say what n is. A rewrite is its own patch with a measurement in the message.", ""]
        for x in r["loop_costs"]:
            L.append(f"- {x['file']}:{x['line']} `{x['name']}`: " + "; ".join(x["why"]))
        L.append("")
    if r.get("dup_names") or r.get("dup_shapes") or r.get("dup_tests") or r.get("copies"):
        L += ["## Duplicates", "",
              "\"Search before writing\" checked against the tree. A copy is two functions whose tokens,",
              f"with names and literals erased, are at least {cm.COPY_SIMILARITY:.0%} alike: pasted, then edited." if cm else "",
              "Same shape means the bodies match exactly once renamed. One name with two definitions",
              "is only a question until one of the other two lines says the bodies match.", ""]
        if r.get("copies"):
            same, edited = r["copies_exact"], r["copies_removable"] - r["copies_exact"]
            parts = []
            if same:
                parts.append(f"{same} lines where the copies are the same function ({cm.COPY_EXACT:.0%} alike or more)")
            if edited:
                parts.append(f"at most {edited} lines where they were edited after pasting: there the differences "
                             "have to become parameters, so the saving is less")
            L += ["One function per cluster would save " + ", and ".join(parts) + ".", ""]
        for c in r.get("copies", []):
            L.append(f"- copy ({c['similarity']:.0%} alike, {'' if c['exact'] else 'at most '}{c['removable']} lines to save): "
                     + ", ".join(c["members"]))
        for name, where in sorted(r.get("dup_names", {}).items()):
            L.append(f"- one name, two definitions: `{name}` in {', '.join(where)}")
        for members in r.get("dup_shapes", []):
            L.append("- same shape: " + ", ".join(f"{a}:{b} (line {c})" for a, b, c in members))
        for members in r.get("dup_tests", []):
            L.append(f"- {len(members)} tests with one shape, which wants parametrize: "
                     + ", ".join(b for _a, b, _c in members[:5]))
        L.append("")
    if r.get("lazy_imports"):
        L += ["## Repo modules imported inside a function", "",
              "Usually a circular import worked around rather than fixed. Decide which way the",
              "dependency should point; the import moves to the top when the cycle is gone.", ""]
        for x in r["lazy_imports"]:
            L.append(f"- {x['file']}:{x['line']} `{x['name']}` imports {x['target']}")
        L.append("")
    if r.get("risks"):
        L += ["## Risk patterns", "",
              "Triage over lines, not verdicts. Each hit is where a trace from input to sink begins.", ""]
        grouped = defaultdict(list)
        for key, items in r["risks"].items():
            group, name = key.split("|", 1)
            grouped[group].append((name, items))
        for group in ("security", "data", "perf", "concurrency", "logging", "testing"):
            if group not in grouped:
                continue
            L.append(f"- **{group}**")
            for name, items in sorted(grouped[group], key=lambda kv: -len(kv[1])):
                if rd and name in rd.MARKERS:
                    per_file = Counter(rel for rel, _ln, _t in items)
                    L.append(f"  - {name}: {len(items)} line(s) in {len(per_file)} file(s): "
                             + ", ".join(f"{os.path.basename(f)} ({n})" for f, n in per_file.most_common(6)))
                    continue
                L.append(f"  - {name} ({len(items)})")
                for rel, ln, text in items[:3]:
                    L.append(f"    - {rel}:{ln}: `{text}`")
        L.append("")
    if r.get("weak_tests"):
        L += ["## Tests that assert nothing", "",
              "Each passes if the code under test returns at all.", ""]
        L += [f"- {x['file']}:{x['line']} `{x['name']}`" for x in r["weak_tests"]]
        L.append("")
    L += [f"## Public functions no test calls ({r['untested_total']})", "",
          "Only a direct call from a test pins a function. \"Reachable\" means a test gets there through",
          "other code, which exercises it without asserting on it. \"Named in a test only\" means some test",
          "has an identifier with the same name, which proves nothing. Ordered by how many modules depend",
          "on each: the top of this list is what to characterise before refactoring anything.", "",
          "| file | function | line | callers | pinned? |", "|---|---|---:|---:|---|"]
    for u in r["untested"]:
        L.append(f"| {u['file']} | {u['qual']} | {u['line']} | {u['callers']} | {u.get('tier', '')} |")
    L.append("")
    L += [f"## Possibly dead ({r['dead_total']}, {r.get('dead_lines', 0)} lines)", "",
          "No entry point reaches these: not module-level code, a registered or exported name, a",
          "console script, or anything those call or name. One reached only from dead code is dead",
          "with it, so delete a subtree from its head down. \"Only tests reach it\" is test support",
          "living in production code, or a feature nothing uses. A name is kept alive by any",
          "attribute or string that spells it, so this list errs towards silence; still check each",
          "against a plugin, a template or a caller outside this tree before deleting.", "",
          "| file | name | line | kind | lines | why |", "|---|---|---:|---|---:|---|"]
    for d in r["dead"]:
        L.append(f"| {d['file']} | {d['qual']} | {d['line']} | {d['kind']} | {d['lines']} | {d['why']} |")
    L.append("")
    if r.get("dead_config"):
        L += ["## Config keys nothing reads", "",
              "A key no Python file mentions is a dead switch: the tree says the behaviour",
              "is configurable when it is not. Read it, or delete it.",
              "",
              "This finds keys never named in the code. It does not find the commoner case:",
              "a key parsed into a config object whose field nothing later reads. Check the",
              "config module's fields against the dead list above for those.", ""]
        for rel, dead in r["dead_config"]:
            L.append(f"- **{rel}**")
            for section, key in dead[:10]:
                L.append(f"  - `{(section + '.') if section else ''}{key}`")
            if len(dead) > 10:
                L.append(f"  - ... and {len(dead) - 10} more")
        L.append("")
    if r["switch_modules"]:
        L += ["## Modules that read switches", "",
              "Config, environment, flags, the clock, or randomness: behaviour these modules",
              "produce depends on more than their diff.", ""]
        L += [f"- {rel} ({n} line(s))" for n, rel in r["switch_modules"]]
        L.append("")
    L += [f"## Unresolved calls ({r['unresolved_total']})", "",
          "The resolver could not place these, so dead-code and untested claims above are",
          "conservative by exactly this much. Most are third-party or builtins.", ""]
    L += [f"- `{name}` ({n})" for name, n in r["unresolved"]]
    L.append("")
    return "\n".join(L)


# ---- focus mode: the neighbourhood of a diff, read from git ---------------------------

def export_commit(repo, ref, dest):
    """Write the tracked files of `ref` under `dest`. The map reads a commit, never the checkout."""
    tar = subprocess.run(["git", "-C", repo, "archive", "--format=tar", ref], capture_output=True)
    if tar.returncode:
        sys.exit(f"git archive {ref}: {tar.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:                  # Python before 3.12 has no extraction filter
            tf.extractall(dest)


def changed_lines(repo, base, head):
    """{rel: line numbers on the head side that the diff adds or changes} for .py files."""
    diff = subprocess.run(["git", "-C", repo, "diff", "-U0", "--no-renames", base, head, "--", "*.py"],
                          capture_output=True, text=True, check=True).stdout
    out, rel = defaultdict(set), None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            rel = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and rel:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, n = int(m.group(1)), int(m.group(2) or 1)
            out[rel].update(range(start, start + n) if n else [start])   # n == 0: a deletion after `start`
    return out


def fingerprints(mods):
    """({(rel, qual): shape hash}, {(rel, qual): shingles}) for production functions big enough to be a copy."""
    shapes, shingled = {}, {}
    if not cm:
        return shapes, shingled
    for rel, m in mods.items():
        if m.is_test:
            continue
        src_lines = m.src.splitlines()
        for node in ast.walk(m.tree):
            qual = next((q for q, d in m.defs.items() if d["line"] == node.lineno), None) if isinstance(node, FUNC) else None
            if not qual:
                continue
            fp, sh = cm.fingerprint(node), cm.copy_shingles(src_lines, node)
            if fp:
                shapes[(rel, qual)] = fp
            if sh:
                shingled[(rel, qual)] = sh
    return shapes, shingled


def call_chain(key, callers_of, limit=8):
    """Shortest path from a caller nothing calls (an entry point, module-level code) down to `key`."""
    paths, seen = [[key]], {key}
    for _ in range(limit):
        nxt = []
        for path in paths:
            ups = callers_of.get(path[0], set())
            if not ups:
                return path
            for c in sorted(ups, key=str):
                if c not in seen:
                    seen.add(c)
                    nxt.append([c] + path)
        if not nxt:
            break
        paths = nxt
    return paths[0]


def func_node(mod, d):
    return next((n for n in ast.walk(mod.tree) if isinstance(n, FUNC) and n.lineno == d["line"]), None)


def unplaced_calls(mod, qual, node, placed):
    """Bare-name calls in `node` the resolver could not place. A method call or a builtin was never a
    candidate for "unused" or "used once", and neither is a name the function binds itself."""
    bound = {a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg)}
    bound |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    bound |= {n.name for n in ast.walk(node) if isinstance(n, FUNC + (ast.ClassDef,)) and n is not node}
    known = set(dir(__import__("builtins"))) | bound | set(mod.imports)
    return sum(1 for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id not in known and (mod.rel, qual, c.lineno) not in placed)


def addition_findings(mods, old, old_fp, old_sh, key, prod, tests, refs, ep_names):
    """What to do about a function or class the branch added: [(group, finding)]."""
    rel, qual = key
    m, d = mods[rel], mods[rel].defs[qual]
    name, where = d["name"], f"{rel}:{qual}"
    if (name.startswith("__") or name == "main" or d["decorators"] or where in ep_names
            or (m.all_exports is not None and name in m.all_exports)):
        return []
    out = []
    if not prod and refs[name] == 0:
        out.append(("test_only" if tests else "unused", dict(where=where, kind=d["kind"], line=d["line"],
                                                             tests=len(tests))))
    elif len(prod) == 1 and refs[name] <= 1 and d["length"] <= INLINE_MAX:
        out.append(("inline", dict(where=where, kind=d["kind"], length=d["length"], site=f"{prod[0][0]}:{prod[0][2]}")))
    if d["kind"] == "class":
        methods = [q for q, x in m.defs.items() if x["cls"] == qual and not x["name"].startswith("__")]
        if len(methods) <= 1:
            out.append(("one_method", dict(where=where, methods=methods)))
        return out
    node = func_node(m, d)
    twin = old_fp.get(cm.fingerprint(node)) if cm and node else None
    mine = cm.copy_shingles(m.src.splitlines(), node) if cm and node else None
    score, alike = max(((cm.similarity(mine, sh), k) for k, sh in old_sh.items()), default=(0, None)) if mine else (0, None)
    if not twin and score >= cm.COPY_SIMILARITY:
        twin = alike
    same = [f"{r}:{q}" for r, om in old.items() if r != rel and not om.is_test
            for q, x in om.defs.items() if x["name"] == name and x["kind"] != "class"]
    if twin or same:
        out.append(("dupes", dict(where=where, shape=f"{twin[0]}:{twin[1]}" if twin else None,
                                  alike=round(score, 2) if twin == alike else 1.0, name=same[:3])))
    return out


def index_edges(mods, edges):
    """Call sites per callee (one per line), production callers per callee, callees per caller."""
    sites, callers_of, callees = defaultdict(list), defaultdict(set), defaultdict(set)
    for src_rel, caller, dst_rel, dst_qual, line in edges:
        if (src_rel, line) not in {(r, n) for r, _c, n in sites[(dst_rel, dst_qual)]}:
            sites[(dst_rel, dst_qual)].append((src_rel, caller, line))
        if not mods[src_rel].is_test:
            callers_of[(dst_rel, dst_qual)].add((src_rel, caller))
        callees[(src_rel, caller)].add((dst_rel, dst_qual))
    return sites, callers_of, callees


def focus_map(repo, base, head, src_dirs):
    fork = subprocess.run(["git", "-C", repo, "merge-base", base, head], capture_output=True, text=True)
    if fork.returncode:
        sys.exit(f"no merge base for {base} and {head}")
    fork = fork.stdout.strip()
    with tempfile.TemporaryDirectory() as th, tempfile.TemporaryDirectory() as tb:
        export_commit(repo, head, th)
        export_commit(repo, fork, tb)
        mods, old = parse_repo(th, src_dirs), parse_repo(tb, src_dirs)
        ep_names = {e[0] for e in entry_points(th, mods)}
    lines = changed_lines(repo, fork, head)
    edges, _unresolved = build_graph(mods)
    is_new = lambda rel, qual: rel not in old or qual not in old[rel].defs   # noqa: E731
    changed = [(rel, qual) for rel, ls in lines.items() if rel in mods and not mods[rel].is_test
               for qual, d in mods[rel].defs.items()
               if is_new(rel, qual) or (d["kind"] != "class" and any(d["line"] <= n <= d["end"] for n in ls))]

    sites, callers_of, callees = index_edges(mods, edges)
    old_shapes, old_sh = fingerprints(old)
    old_fp = {fp: key for key, fp in old_shapes.items()}
    refs = sum((m.refs for m in mods.values() if not m.is_test), Counter())
    placed = {(src_rel, caller, line) for src_rel, caller, _r, _q, line in edges}

    r = dict(fork=fork, head=head, changed=len(changed), inline=[], unused=[], test_only=[], dupes=[],
             one_method=[], functions=[], unresolved=0, unparsed=list(UNPARSED))
    for key in changed:
        rel, qual = key
        d = mods[rel].defs[qual]
        prod = [c for c in sites[key] if not mods[c[0]].is_test]
        tests = [c for c in sites[key] if mods[c[0]].is_test]
        if is_new(rel, qual):
            for group, finding in addition_findings(mods, old, old_fp, old_sh, key, prod, tests, refs, ep_names):
                r[group].append(finding)
        node = func_node(mods[rel], d) if d["kind"] != "class" else None
        if node is None:
            continue
        r["unresolved"] += unplaced_calls(mods[rel], qual, node, placed)
        loops = cm.loop_costs(node) if cm else []
        if not is_new(rel, qual):        # only the loops the branch wrote: pre-existing ones are not this PR's
            loops = [c for c in loops if int(re.search(r"line (\d+)", c).group(1)) in lines[rel]]
        src = {c[0]: mods[c[0]].src.splitlines() for c in prod}
        r["functions"].append(dict(
            where=f"{rel}:{qual}", length=d["length"], new=is_new(rel, qual), tests=len(tests), loops=loops,
            chain=[f"{cr}:{cq or '<module>'}" for cr, cq in call_chain(key, callers_of)],
            callers=[dict(at=f"{cr}:{n}", code=src[cr][n - 1].strip()[:100]) for cr, _q, n in prod],
            calls=sorted(f"{cr}:{cq}" for cr, cq in callees[key])))
    return r


def render_focus(r):
    L = [f"# Focus map: {r['fork'][:10]}..{r['head']}", "",
         f"The {r['changed']} function(s) and class(es) this branch adds or changes, and their neighbours, read "
         f"from git at {r['head']}, never the working tree. Calls are resolved by a heuristic: a claim that "
         f"something is unused or used once is only as good as the resolver, and calls it could not place are "
         f"counted, not dropped: {r['unresolved']} plain-name call(s) in the changed code it could not "
         f"place.", ""]
    if r["unparsed"]:
        L += ["**Could not parse, so blind to:** " + ", ".join(f"`{u[0]}`" for u in r["unparsed"]), ""]
    L += ["## Act on these first", ""]
    groups = [
        ("Added and used once: inline at the caller (minimal-code.md move 1)", r["inline"],
         lambda x: f"`{x['where']}` ({x['kind']}, {x['length']} lines), called once, at `{x['site']}`"),
        ("Classes with at most one method: a function (move 1)", r["one_method"],
         lambda x: f"`{x['where']}`" + (f": only `{x['methods'][0]}`" if x["methods"] else ": no methods")),
        ("Added and never called: prune it, or it lands with its first caller", r["unused"],
         lambda x: f"`{x['where']}` ({x['kind']}, line {x['line']})"),
        ("Added and called only by tests: dead in production", r["test_only"],
         lambda x: f"`{x['where']}` ({x['tests']} test call(s))"),
        ("Added, and the tree already has it: reuse the original (move 4)", r["dupes"],
         lambda x: f"`{x['where']}`" + (f": {x['alike']:.0%} like `{x['shape']}`" if x["shape"] else "")
                   + (f": same name as {', '.join(f'`{n}`' for n in x['name'])}" if x["name"] else "")),
    ]
    for title, items, fmt in groups:
        if items:
            L += [f"### {title}", ""] + [f"- {fmt(x)}" for x in items] + [""]
    if not any(items for _t, items, _f in groups):
        L += ["Nothing: no single-use, unused, test-only or duplicate additions were found.", ""]
    L += ["## Changed functions", "",
          "Callers show what each call site already guarantees, so a check inside the function that repeats it "
          "can go (move 2). Loops are listed only where one hides a second loop; change complexity only where "
          "the data is large and the change is measured (algorithm-smells.md).", ""]
    for f in r["functions"]:
        L += [f"### `{f['where']}` ({'new' if f['new'] else 'changed'}, {f['length']} lines)", ""]
        if len(f["chain"]) > 1:
            L.append("- Chain: " + " -> ".join(f"`{c}`" for c in f["chain"]))
        L.append(f"- Callers: {len(f['callers'])} in code, {f['tests']} in tests")
        L += [f"  - `{c['at']}`: `{c['code']}`" for c in f["callers"][:6]]
        if len(f["callers"]) > 6:
            L.append(f"  - and {len(f['callers']) - 6} more")
        if f["calls"]:
            L.append("- Calls: " + ", ".join(f"`{c}`" for c in f["calls"][:10]))
        L += [f"- Loop: {c}" for c in f["loops"][:3]]
        if len(f["loops"]) > 3:
            L.append(f"- and {len(f['loops']) - 3} more loop(s) the branch wrote")
        L.append("")
    return "\n".join(L)


SELF_TEST_BASE = {
    "pkg/__init__.py": "",
    "pkg/cli.py": "from pkg.pipeline import run\n\n\ndef main():\n    run('in.csv')\n\n\n"
                  "if __name__ == '__main__':\n    main()\n",
    "pkg/pipeline.py": "from pkg.loader import load\n\n\ndef run(path):\n    return len(load(path))\n",
    "pkg/loader.py": "def load(path):\n    return [line for line in open(path)]\n",
    "pkg/util.py": "def unique(items):\n    seen = set()\n    out = []\n    for item in items:\n"
                   "        if item not in seen:\n            seen.add(item)\n            out.append(item)\n"
                   "    return out\n",
    "tests/__init__.py": "",
    "tests/test_loader.py": "from pkg.loader import load\n\n\ndef test_load():\n    assert load(__file__)\n",
}
SELF_TEST_BRANCH = {
    "pkg/loader.py": "from pkg.fmt import Formatter\n\n\ndef load(path):\n    rows = [line for line in open(path)]\n"
                     "    return _strip(rows)\n\n\ndef _strip(rows):\n    return [Formatter().fmt(r) for r in rows]\n\n\n"
                     "def dedupe(rows):\n    seen = set()\n    kept = []\n    for row in rows:\n"
                     "        if row not in seen:\n            seen.add(row)\n            kept.append(row)\n"
                     "    return kept\n\n\ndef never_called():\n    return 1\n\n\ndef only_tested():\n    return 2\n",
    "pkg/fmt.py": "class Formatter:\n    def fmt(self, row):\n        return row.strip()\n",
    "tests/test_loader.py": "from pkg.loader import load, only_tested\n\n\ndef test_load():\n    assert load(__file__)\n"
                            "\n\ndef test_only():\n    assert only_tested() == 2\n",
}


DEPTH = ("def {name}(node, d=0):\n    best = d\n    for child in children(node):\n        if isinstance(child, {skip}):\n"
         "            continue\n        {var} = d\n        if isinstance(child, NEST):\n            {var} = d + 1\n"
         "        best = max(best, {name}(child, {var}))\n    return best\n")
SELF_TEST_FULL = {
    "app/__init__.py": "",
    "app/cli.py": "import subprocess\nfrom app import a, b, handlers as h\nfrom app.core import process as run_it\n"
                  "from app.registry import Registry, Stack, Worker\n\n\ndef main():\n    subprocess.run(['true'])\n"
                  "    table = {'a': h.on_a, 'd': a.walk_depth, 'e': b.nesting_depth, 'l': (a.load, b.load), 'm': a.Marker}\n"
                  "    Registry().dispatch('on_b')\n    Worker().start()\n    Stack().append(1)\n    return run_it(table)\n\n\n"
                  "if __name__ == '__main__':\n    main()\n",
    "app/handlers.py": "def on_a(x):\n    return x\n\n\ndef on_b(x):\n    return x\n",
    "app/legacy.py": "def on_a(x):\n    return -x\n",
    "app/core.py": "def process(table):\n    return sorted(table)\n\n\ndef run(cmd):\n    return cmd\n\n\n"
                   "def orphan_head():\n    return orphan_leaf() + 1\n\n\ndef orphan_leaf():\n    return 1\n\n\n"
                   "def test_support():\n    return 0\n",
    "app/registry.py": "import threading\nfrom contextlib import contextmanager\n\n\ndef register(fn):\n    return fn\n\n\n"
                       "class Registry:\n    def dispatch(self, name):\n        return getattr(self, name, None)\n\n"
                       "    @property\n    def unused_prop(self):\n        return 1\n\n\n"
                       "class Worker(threading.Thread):\n    def run(self):\n        return 1\n\n"
                       "    def hook_a_framework_may_call(self):\n        return 2\n\n\n"
                       "class Stack(list):\n    def push(self, x):\n        return x\n\n    def append(self, x):\n        return x\n\n\n"
                       "@contextmanager\ndef scoped():\n    yield\n\n\n@register\ndef plugin_entry():\n    return 1\n",
    "app/a.py": "from app import b\n\nNEST = ()\nchildren = list\n\n\nclass Marker(dict):\n    pass\n\n\n" + DEPTH.format(name="walk_depth", skip="(int, str, bytes)", var="nd")
                + "\n\ndef load(path):\n    rows = []\n    with open(path) as fh:\n        for line in fh:\n"
                  "            if line.strip():\n                rows.append(line.split(','))\n    if not rows:\n"
                  "        raise ValueError(path)\n    return rows\n",
    "app/b.py": "from app import a\n\nNEST = ()\nchildren = list\n\n\n" + DEPTH.format(name="nesting_depth", skip="(int, str)", var="level")
                + "\n\ndef load(key):\n    total = 0\n    for part in key.split('.'):\n        total += len(part)\n"
                  "        if total > 10:\n            break\n    while total % 3:\n        total += 1\n"
                  "    return {'key': key, 'size': total}\n",
    "selftest_app.py": "from app.core import process, test_support\n\n\ndef check():\n    assert process({}) == [] and test_support() == 0\n",
}


def self_test_full():
    """The whole-tree map against a scratch project with one planted case per rule."""
    with tempfile.TemporaryDirectory() as root:
        for rel, text in SELF_TEST_FULL.items():
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            with open(os.path.join(root, rel), "w") as fh:
                fh.write(text)
        r = analyse_repo(root, [], 50)
    dead = {f"{d['file']}:{d['qual']}": d["why"] for d in r["dead"]}
    order = r["rewrite_order"]
    copies = [c["members"] for c in r["copies"]]
    pos = {rel: i for i, group in enumerate(order) for rel in group}
    return [
        ("nothing calls it: dead", dead.get("app/core.py:orphan_head") == "nothing references it"),
        ("called only by dead code: dead with it",
         dead.get("app/core.py:orphan_leaf") == "reached only from dead code: app/core.py:orphan_head"),
        ("subprocess.run() does not keep this tree's run() alive", "app/core.py:run" in dead),
        ("h.on_a names one module's on_a, not the other's", "app/legacy.py:on_a" in dead and "app/handlers.py:on_a" not in dead),
        ("a function imported under an alias is alive", "app/core.py:process" not in dead),
        ("a function named only in a string is alive", "app/handlers.py:on_b" not in dead),
        ("a registering decorator keeps a function alive", "app/registry.py:plugin_entry" not in dead),
        ("@contextmanager and @property do not", {"app/registry.py:scoped", "app/registry.py:Registry.unused_prop"} <= set(dead)),
        ("a method of a class with a foreign base is left alone",
         "app/registry.py:Worker.hook_a_framework_may_call" not in dead),
        ("list has no push(), so an unused Stack.push is dead; append() overrides",
         "app/registry.py:Stack.push" in dead and "app/registry.py:Stack.append" not in dead),
        ("selftest_app.py is a test file", r["tests"] == 1 and dead.get("app/core.py:test_support") == "only tests reach it"),
        ("nothing else is reported dead", len(dead) == 8),
        ("an edited, renamed copy is found", len(copies) == 1 and len(copies[0]) == 2 and "walk_depth" in copies[0][0]),
        ("one name with two unrelated bodies is not a copy",
         "load" in r["dup_names"] and not any("load" in m for c in copies for m in c)),
        ("modules that import each other are one group", ["app/a.py", "app/b.py"] in order),
        ("dependencies come first in the rewrite order",
         pos["app/core.py"] < pos["app/cli.py"] and pos["app/a.py"] < pos["app/cli.py"]),
    ]


def self_test():
    """--focus against a scratch repo with one planted case per finding, then the whole-tree map."""
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="T",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def git(*a):
        subprocess.run(["git", "-C", r, *a], check=True, capture_output=True, env=env)

    def write(files):
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(r, rel)), exist_ok=True)
            with open(os.path.join(r, rel), "w") as fh:
                fh.write(text)

    with tempfile.TemporaryDirectory() as r:
        git("init", "-q", "-b", "main")
        write(SELF_TEST_BASE); git("add", "-A"); git("commit", "-qm", "base")
        git("checkout", "-qb", "feature")
        write(SELF_TEST_BRANCH); git("add", "-A"); git("commit", "-qm", "feature")
        git("checkout", "-q", "main")
        write({"pkg/loader.py": "def checkout_only():\n    return 0\n"})      # uncommitted, on another branch
        status = subprocess.run(["git", "-C", r, "status", "--porcelain"], capture_output=True, text=True).stdout
        text = render_focus(focus_map(r, "main", "feature", []))
        after = subprocess.run(["git", "-C", r, "status", "--porcelain"], capture_output=True, text=True).stdout
    checks = [
        ("a helper used once is an inline candidate", "`pkg/loader.py:_strip` (function, 2 lines), called once"),
        ("a class with one method is named", "`pkg/fmt.py:Formatter`: only `Formatter.fmt`"),
        ("an addition nothing calls is named", "`pkg/loader.py:never_called`"),
        ("an addition only tests call is named", "`pkg/loader.py:only_tested` (1 test call(s))"),
        ("a renamed copy of an existing function is found", "`pkg/loader.py:dedupe`: 100% like `pkg/util.py:unique`"),
        ("the chain runs from the entry point down",
         "`pkg/cli.py:main` -> `pkg/pipeline.py:run` -> `pkg/loader.py:load`"),
        ("a caller is shown with the line that calls", "`pkg/pipeline.py:5`: `return len(load(path))`"),
        ("test callers are counted apart", "Callers: 1 in code, 1 in tests"),
    ]
    failed = 0
    for name, needle in checks:
        ok = needle in text
        failed += not ok
        print(("ok   " if ok else "FAIL ") + name)
    for name, ok in [("the checkout is never read", "checkout_only" not in text),
                     ("an unchanged function is not listed", "### `pkg/pipeline.py:run`" not in text),
                     ("nothing in the repo is written", status == after)]:
        failed += not ok
        print(("ok   " if ok else "FAIL ") + name)
    if failed:
        print(text)
    full = self_test_full()
    for name, ok in full:
        failed += not ok
        print(("ok   " if ok else "FAIL ") + name)
    print(f"{len(checks) + 3 + len(full)} cases, {failed} failing")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--src", action="append", default=[],
                    help="limit to this directory (repeatable); default: the whole tree")
    ap.add_argument("--md")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--focus", metavar="BASE..HEAD", help="map only what the branch changes, read from git")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    root = os.path.abspath(args.repo)
    # --src is a DIRECTORY relative to the repo (src/acme), not a package name. A wrong
    # one used to read as an empty, clean tree.
    for d in args.src:
        if not os.path.isdir(os.path.join(root, d)):
            sys.exit(f"--src {d!r} is not a directory under {root}: pass the package DIRECTORY (src/acme), "
                     "and pass the tests too (--src tests), or tested code reads as dead and untested")
    if args.focus:
        base, sep, head = args.focus.partition("..")
        if not sep or not base or not head:
            sys.exit("--focus takes BASE..HEAD, for example main..feature/x")
        r = focus_map(root, base, head, args.src)
        text = render_focus(r)
    else:
        r = analyse_repo(root, args.src, args.top)
        if r is None:
            sys.exit("no Python files found")
        text = render(r, args.top)
    if args.src and not any(re.search(r"(^|/)tests?(/|$)", d) for d in args.src):
        text = ("> **No test directory was given with --src.** Everything below about \"untested\", \"no test reaches "
                "it\" and \"possibly dead\" is blind to the tests. Rerun with `--src <pkg dir> --src tests`.\n\n") + text
    print(text if not args.md else f"report written to {args.md} ({len(text.splitlines())} lines)")
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        open(args.md, "w").write(text)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        json.dump(r, open(args.json, "w"), indent=2, default=list)


if __name__ == "__main__":
    main()
