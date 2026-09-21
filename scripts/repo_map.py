#!/usr/bin/env python3
"""repo_map.py: read a whole Python repo and report how it is wired.

usage: repo_map.py [--repo PATH] [--src DIR ...] [--md FILE] [--json FILE] [--top N]

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
  layering        the import graph by package, and every cycle
  hot spots       fan-in, fan-out, churn from git, and size
  the big ones    functions over the length or nesting budget
  untested        public functions no test names or reaches
  dead            defined, never referenced, not exported, not an entry point
  switches        modules that read config, env, flags, or the clock
  dense           short functions with too many paths or a condition with too many terms
  return shapes   functions returning a value on one path and None on another
  duplicates      one name defined in two modules, and renamed copies of one body
  lazy imports    repo modules imported inside a function, usually hiding a cycle
  risks           security, data, performance, concurrency and logging patterns
  weak tests      test functions that assert nothing
  unresolved      calls the resolver could not place, so you can judge the rest
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import code_metrics as cm
    import risk_detectors as rd
except ImportError:  # degrade rather than die when a sibling module is missing
    cm = rd = None

LENGTH_BUDGET = 48
DEPTH_BUDGET = 3
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
    base = os.path.basename(rel)
    return (re.search(r"(^|/)(tests?|__tests__)/", rel) or base.startswith("test_")
            or base.endswith("_test.py") or base == "conftest.py")


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


def build_graph(mods):
    """Resolve calls to (module rel, qualname). Returns edges and unresolved."""
    by_module = {m.name: m for m in mods.values()}
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
            # unique across the repo. For obj.method() the object's type is
            # unknown, so a leaf that names exactly one definition in the tree
            # is taken as that definition; ambiguous leaves stay unresolved.
            if hit is None:
                leaf = target.split(".")[-1]
                cands = index.get(leaf, [])
                if len(cands) == 1:
                    hit = cands[0]
            if hit:
                edges.append((rel, caller, hit[0], hit[1], line))
            elif head not in ("self", "cls") and not target.startswith(("os.", "sys.", "re.", "json.")):
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
        for target in m.import_modules:
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
                if not re.search(r"""["']""" + re.escape(key) + r"""["']|\b""" + re.escape(key) + r"\b", code_text):
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
            for m2 in re.finditer(r"^\s*([\w-]+)\s*=\s*[\"']([\w.]+):([\w.]+)[\"']", text, re.M):
                eps.append((f"{m2.group(2)}:{m2.group(3)}", f"console_script {m2.group(1)}"))
    seen, out = set(), []
    for e in eps:
        if e not in seen:
            seen.add(e); out.append(e)
    return out


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
    dead, untested, big = [], [], []
    for rel, m in prod.items():
        exports = m.all_exports
        for qual, d in m.defs.items():
            key = (rel, qual)
            n = d["name"]
            is_entry = rel in ep_files or n in ("main", "__init__") or n.startswith("__")
            exported = exports is not None and n in exports
            overridden = d["kind"] == "method" and (n.startswith("__") or n in ("run", "handle", "process"))
            # A definition is alive if anything calls it, names it as a value,
            # or mentions it in a string; the last covers getattr and registries.
            if (called[key] == 0 and refs[n] == 0 and not is_entry and not exported
                    and not overridden and n not in test_names):
                dead.append(dict(file=rel, qual=qual, line=d["line"], kind=d["kind"], private=d["private"]))
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
    if cm:
        for rel, m in sorted(mods.items()):
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
    dup_shapes = [v for k, v in prints.items() if len(v) > 1 and not k.startswith("test:")]
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
                dead_total=len(dead), untested_total=len(untested), big_total=len(big),
                edges=len(edges), unresolved=unresolved.most_common(12),
                unresolved_total=sum(unresolved.values()),
                switch_modules=sorted(((m.switch_lines, rel) for rel, m in prod.items()
                                       if m.switch_lines), reverse=True)[:top],
                dead_config=dead_config, python_floor=floor, unparsed=list(UNPARSED), loop_costs=loop_cost_rows[:top],
                dense=dense[:top], return_shapes=shapes[:top], stale_docs=stale_docs[:top],
                weak_tests=weak_tests[:top], lazy_imports=lazy_imports[:top],
                dup_names=dup_names, dup_shapes=dup_shapes[:top], dup_tests=dup_tests[:top],
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
    if r.get("dup_names") or r.get("dup_shapes") or r.get("dup_tests"):
        L += ["## Duplicates", "",
              "\"Search before writing\" checked against the tree. Same shape means the bodies match",
              "once names and constants are erased: a copy that was pasted and renamed.", ""]
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
    L += [f"## Possibly dead ({r['dead_total']})", "",
          "Defined, never called from this tree, not exported, not an entry point, not named",
          "in a test. Check each against dynamic use (getattr, registry, plugin, template)",
          "before deleting; the resolver cannot see those.", "",
          "| file | name | line | kind |", "|---|---|---:|---|"]
    for d in r["dead"]:
        L.append(f"| {d['file']} | {d['qual']} | {d['line']} | {d['kind']} |")
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--src", action="append", default=[],
                    help="limit to this directory (repeatable); default: the whole tree")
    ap.add_argument("--md")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    root = os.path.abspath(args.repo)
    # --src is a DIRECTORY relative to the repo (src/acme), not a package name. A wrong
    # one used to read as an empty, clean tree.
    for d in args.src:
        if not os.path.isdir(os.path.join(root, d)):
            sys.exit(f"--src {d!r} is not a directory under {root}: pass the package DIRECTORY (src/acme), "
                     "and pass the tests too (--src tests), or tested code reads as dead and untested")
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
