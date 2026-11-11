"""code_metrics.py: function-level measurements shared by audit_diff and repo_map.

Length and nesting stay the primary budget, because a maintainer reading a
patch in `less` can count lines and indentation by eye and cannot verify a
McCabe number. Everything here is secondary: it reports only where it
DISAGREES with that budget or sees what the budget cannot.

  cyclomatic()      reported only for functions inside the length and depth
                    budget whose count is over 10: the short flat function
                    with a dense predicate or a long elif ladder
  bool_terms()      the largest single condition; over 3 terms wants a name
  signature()       parameters (self/cls excluded) and distinct locals
  return_shapes()   a value on one path and None on another; two literal
                    kinds; every caller now needs an `if`
  fingerprint()     identifier- and constant-blind shape of a function, for
                    finding the copy that was pasted and renamed
  asserts_nothing() a test that passes if the function returns anything
  public_api()      name -> signature text, to diff between two revisions
  docstring_params  parameters a docstring documents that the signature lacks

Run this file to self-test.
"""
from __future__ import annotations

import ast
import hashlib
import io
import keyword
import re
import textwrap
import tokenize
import zlib
from collections import Counter, defaultdict
from itertools import combinations

LENGTH_BUDGET = 48
DEPTH_BUDGET = 3
CC_BUDGET = 10
BOOL_BUDGET = 3
PARAM_BUDGET = 5
LOCAL_BUDGET = 10
DUP_MIN_STATEMENTS = 5
SHINGLE = 5              # tokens per shingle
COPY_MIN_LINES = 8       # shorter functions look alike by accident
COPY_MIN_SHINGLES = 30
COPY_SIMILARITY = 0.8    # three hand-edited copies of one function scored 0.82 to 0.91; parallel but
                         # distinct functions (two format handlers, two test cases) scored 0.61 to 0.75
SKETCH = 16              # smallest shingle hashes kept per function to find candidate pairs
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
# `with` is not a decision: a loader that is for -> with -> with has no branch
# in it, and counting the with-blocks pushes correct resource handling over
# the budget. Only constructs that fork or repeat control flow count.
NEST = (ast.If, ast.For, ast.While, ast.Try, ast.AsyncFor)


def max_depth(node, d=0):
    best = d
    for child in ast.iter_child_nodes(node):
        if isinstance(child, FUNC + (ast.ClassDef, ast.Lambda)):
            continue
        nd = d
        if isinstance(child, NEST):
            chain = (isinstance(node, ast.If) and isinstance(child, ast.If)
                     and len(node.orelse) == 1 and node.orelse[0] is child)
            nd = d if chain else d + 1
        best = max(best, max_depth(child, nd))
    return best


def _own_nodes(fn):
    """Nodes of fn, not descending into nested defs, classes, or lambdas."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, FUNC + (ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def cyclomatic(fn):
    n = 1
    for node in _own_nodes(fn):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler, ast.IfExp)):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            n += 1 + len(node.ifs)
        elif node.__class__.__name__ == "match_case":
            n += 1
    return n


def bool_terms(fn):
    """Terms in the largest single condition, flattening nested and/or."""
    def count(node):
        if isinstance(node, ast.BoolOp):
            return sum(count(v) for v in node.values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return count(node.operand)
        return 1
    best, where = 0, 0
    for node in _own_nodes(fn):
        if isinstance(node, ast.BoolOp):
            c = count(node)
            if c > best:
                best, where = c, node.lineno
    return best, where


def signature(fn):
    a = fn.args
    names = [x.arg for x in getattr(a, "posonlyargs", []) + a.args + a.kwonlyargs]
    if a.vararg:
        names.append("*" + a.vararg.arg)
    if a.kwarg:
        names.append("**" + a.kwarg.arg)
    params = [n for n in names if n not in ("self", "cls")]
    local = set()
    for node in _own_nodes(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            local.add(node.id)
    return params, sorted(local - set(params))


def signature_text(fn):
    try:
        return f"{fn.name}({ast.unparse(fn.args)})"
    except Exception:
        return fn.name + "(...)"


def _kind(value):
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return "None"
    if isinstance(value, ast.Constant):
        return type(value.value).__name__
    for cls, name in ((ast.Dict, "dict"), (ast.List, "list"), (ast.Tuple, "tuple"), (ast.Set, "set"),
                      (ast.JoinedStr, "str"), (ast.ListComp, "list"), (ast.DictComp, "dict"),
                      (ast.SetComp, "set")):
        if isinstance(value, cls):
            return name
    return "value"


def return_shapes(fn):
    """None when consistent; otherwise a sentence saying what disagrees."""
    if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in _own_nodes(fn)):
        return None
    kinds = {}
    for node in _own_nodes(fn):
        if isinstance(node, ast.Return):
            kinds.setdefault(_kind(node.value), node.lineno)
    ann = ast.unparse(fn.returns) if fn.returns is not None else ""
    declared_optional = bool(re.search(r"\bNone\b|\bOptional\b", ann))
    concrete = {k for k in kinds if k not in ("None", "value")}
    if "None" in kinds and (concrete or "value" in kinds) and not declared_optional:
        other = sorted(k for k in kinds if k != "None")
        return (f"returns {' or '.join(other)} on one path and None on another "
                f"(line {kinds['None']}); every caller needs an `if`")
    if len(concrete) > 1:
        return f"returns {' and '.join(sorted(concrete))} on different paths"
    return None


class _Blind(ast.NodeTransformer):
    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node):
        node.arg, node.annotation = "_", None
        return node

    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)


def fingerprint(fn):
    """Hash of the function's shape with names and constants erased, or None
    when the body is too small to call a copy."""
    body = [s for s in fn.body
            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    n_stmts = sum(1 for s in body for node in ast.walk(s) if isinstance(node, ast.stmt))
    if n_stmts < DUP_MIN_STATEMENTS:
        return None
    import copy
    blind = [_Blind().visit(copy.deepcopy(s)) for s in body]
    dump = "".join(ast.dump(s, annotate_fields=False) for s in blind)
    return hashlib.sha1(dump.encode()).hexdigest()[:16]


NOT_CODE = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)


def shingles(src):
    """Hashes of a function's token 5-grams with names and literals erased.

    The shape hash above says two bodies are identical once renamed. This says
    how alike they are, so a copy that was pasted and then edited in one place
    still shows. Empty when the text does not tokenize.
    """
    toks = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(textwrap.dedent(src)).readline):
            if t.type in NOT_CODE:
                continue
            toks.append(t.string if t.type == tokenize.OP or keyword.iskeyword(t.string) else
                        "N" if t.type == tokenize.NAME else "S" if t.type == tokenize.STRING else "0")
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return frozenset()
    return frozenset(zlib.crc32(" ".join(toks[i:i + SHINGLE]).encode()) for i in range(len(toks) - SHINGLE + 1))


def copy_shingles(src_lines, fn):
    """The shingles of `fn`, or None when it is too small to call a copy of anything."""
    if (fn.end_lineno or fn.lineno) - fn.lineno + 1 < COPY_MIN_LINES:
        return None
    got = shingles("\n".join(src_lines[fn.lineno - 1:fn.end_lineno]))
    return got if len(got) >= COPY_MIN_SHINGLES else None


def similarity(a, b):
    return len(a & b) / len(a | b) if a or b else 0.0


def near_copies(items):
    """[(similarity, a, b)] for every pair in {key: shingles} at or above COPY_SIMILARITY.

    Comparing all pairs is quadratic. Two sets that similar share most of
    their smallest hashes (a bottom-k MinHash sketch), so only pairs sharing
    a quarter of a sketch are compared exactly. A hash held by hundreds of
    functions is boilerplate and proposes no pairs.
    """
    index = defaultdict(list)
    for key, s in items.items():
        for h in sorted(s)[:SKETCH]:
            index[h].append(key)
    shared = Counter()
    for keys in index.values():
        if len(keys) <= 300:
            shared.update(combinations(sorted(keys), 2))
    out = [(round(similarity(items[a], items[b]), 2), a, b) for (a, b), n in shared.items() if n >= SKETCH // 4]
    return sorted((x for x in out if x[0] >= COPY_SIMILARITY), key=lambda x: (-x[0], x[1], x[2]))


def copy_clusters(pairs):
    """Group near_copies() pairs into clusters: [(lowest similarity in the cluster, sorted members)]."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _sim, a, b in pairs:
        parent[find(a)] = find(b)
    groups, low = defaultdict(list), {}
    for key in parent:
        groups[find(key)].append(key)
    for sim, a, _b in pairs:
        low[find(a)] = min(sim, low.get(find(a), 1.0))
    return sorted((low[root], sorted(members)) for root, members in groups.items())


ASSERT_CALL = re.compile(r"^(assert\w*|fail|raises|warns|assert_\w+|approx)$")


def asserts_nothing(fn):
    """True when a test function contains no assertion of any kind."""
    for node in _own_nodes(fn):
        if isinstance(node, ast.Assert):
            return False
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if ASSERT_CALL.match(name or ""):
                return False
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                c = item.context_expr
                if isinstance(c, ast.Call):
                    f = c.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name in ("raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns"):
                        return False
    return True


def call_contract(fn):
    """The part of a signature a caller depends on: names, kinds, and defaults.

    Annotations are left out on purpose. Adding `rows: List[Dict]` breaks no
    caller, so it is not a surface change; it is a type-hint sweep, which the
    prune list already handles.
    """
    a = fn.args
    parts = []
    pos = getattr(a, "posonlyargs", []) + a.args
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    for arg, default in zip(pos, defaults):
        parts.append(arg.arg + ("=" + ast.unparse(default) if default is not None else ""))
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    elif a.kwonlyargs:
        parts.append("*")
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        parts.append(arg.arg + ("=" + ast.unparse(default) if default is not None else ""))
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return f"{fn.name}({', '.join(parts)})"


def compatible_widening(before, after):
    """True when `after` only appends parameters that have defaults, so every
    existing call still works. It is still a new switch with a default."""
    def params(sig):
        inner = sig[sig.index("(") + 1:sig.rindex(")")]
        out, depth, cur = [], 0, ""
        for ch in inner:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur.strip()); cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur.strip())
        return out
    b, a = params(before), params(after)
    if a[:len(b)] != b:
        return False
    return all("=" in x or x.startswith("*") for x in a[len(b):])


def public_api(src):
    """{qualname: signature text} for public top-level functions and methods."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    api = {}
    for node in tree.body:
        if isinstance(node, FUNC) and not node.name.startswith("_"):
            api[node.name] = call_contract(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for sub in node.body:
                if isinstance(sub, FUNC) and (not sub.name.startswith("_") or sub.name == "__init__"):
                    api[f"{node.name}.{sub.name}"] = call_contract(sub)
    return api


ARGS_HEAD = re.compile(r"^\s*(Args|Arguments|Parameters|Params|Keyword Arguments)\s*:\s*$", re.I)
SECTION_HEAD = re.compile(r"^\s*(Returns?|Raises?|Yields?|Examples?|Notes?|Attributes|See Also)\s*:\s*$", re.I)
ARG_ENTRY = re.compile(r"^\s{2,}\*{0,2}([A-Za-z_]\w*)\s*(\([^)]*\))?\s*:")


def docstring_params(fn):
    """Parameters the docstring documents that the signature does not have."""
    doc = ast.get_docstring(fn)
    if not doc:
        return []
    a = fn.args
    have = {x.arg for x in getattr(a, "posonlyargs", []) + a.args + a.kwonlyargs}
    if a.vararg:
        have.add(a.vararg.arg)
    if a.kwarg:
        have.add(a.kwarg.arg)
    inside, stale = False, []
    for line in doc.splitlines():
        if ARGS_HEAD.match(line):
            inside = True
            continue
        if SECTION_HEAD.match(line) or (inside and line.strip() and not line.startswith(" ")):
            inside = False
            continue
        if inside:
            m = ARG_ENTRY.match(line)
            if m and m.group(1) not in have:
                stale.append(m.group(1))
    return stale


LISTY_CALLS = {"list", "sorted", "split", "tolist", "readlines", "keys", "values", "items"}
AGG_CALLS = {"sum", "min", "max", "len", "sorted", "mean", "median", "std", "any", "all"}
LOOPS = (ast.For, ast.While, ast.AsyncFor)


def loop_costs(fn):
    """Places where a changed loop hides a second loop. Each entry is a
    sentence the reviewer can say out loud.

    The budget elsewhere is on a function's LENGTH and NESTING; this is the
    only measure of its COST. It reports shape, not slowness: n is usually
    small, brute force is often right, and the counterweight is part of the
    rule. Stating the complexity is the review action. Changing it is a
    separate patch with a measurement in its message.
    """
    listy = set()
    for node in _own_nodes(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = node.value
            is_list = isinstance(v, (ast.List, ast.ListComp))
            if isinstance(v, ast.Call):
                f = v.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                is_list = name in LISTY_CALLS
            if is_list:
                listy.add(node.targets[0].id)
    out = []

    def visit(node, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNC + (ast.ClassDef, ast.Lambda)):
                continue
            inner = depth + 1 if isinstance(child, LOOPS) else depth
            if isinstance(child, LOOPS) and depth >= 1:
                out.append((child.lineno, f"a loop inside a loop at line {child.lineno}: say what n and m are"))
            if depth >= 1:
                if isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                    out.append((child.lineno, f"a comprehension inside a loop at line {child.lineno}: "
                                              "that is a second loop"))
                if isinstance(child, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in child.ops):
                    for comp in child.comparators:
                        if isinstance(comp, ast.Name) and comp.id in listy:
                            out.append((child.lineno, f"`in {comp.id}` inside a loop at line {child.lineno}: "
                                                      f"{comp.id} is a list, so each test scans it; a set"))
                        elif isinstance(comp, (ast.List, ast.ListComp)) or (
                                isinstance(comp, ast.Call) and getattr(comp.func, "id", "") in ("list", "sorted")):
                            out.append((child.lineno, f"membership against a list built inside a loop "
                                                      f"at line {child.lineno}"))
                if isinstance(child, ast.Call):
                    f = child.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name in AGG_CALLS and any(isinstance(a, ast.Subscript) and isinstance(a.slice, ast.Slice)
                                                 for a in child.args):
                        out.append((child.lineno, f"`{name}(...)` over a slice inside a loop at line "
                                                  f"{child.lineno}: the window is recomputed per row; "
                                                  "prefix sums or a rolling update"))
            visit(child, inner)

    visit(fn, 0)
    seen, uniq = set(), []
    for line, text in sorted(out):
        if line not in seen:
            seen.add(line); uniq.append(text)
    return uniq


def measure(fn):
    """All the numbers for one function, and the reasons it is worth a look."""
    length = (fn.end_lineno or fn.lineno) - fn.lineno + 1
    depth = max_depth(fn)
    cc = cyclomatic(fn)
    terms, term_line = bool_terms(fn)
    params, local = signature(fn)
    reasons = []
    if length > LENGTH_BUDGET:
        reasons.append(f"{length} lines")
    if depth > DEPTH_BUDGET:
        reasons.append(f"{depth} levels deep")
    if len(params) > PARAM_BUDGET:
        reasons.append(f"{len(params)} parameters")
    if len(local) > LOCAL_BUDGET:
        reasons.append(f"{len(local)} locals")
    in_budget = length <= LENGTH_BUDGET and depth <= DEPTH_BUDGET
    dense = []
    if in_budget and cc > CC_BUDGET:
        dense.append(f"{cc} paths in {length} lines")
    if terms > BOOL_BUDGET:
        dense.append(f"a condition with {terms} terms at line {term_line}")
    shape = return_shapes(fn)
    costs = loop_costs(fn)
    return dict(loop_costs=costs, name=fn.name, line=fn.lineno, length=length, depth=depth, cc=cc,
                params=len(params), locals=len(local), bool_terms=terms,
                over_budget=reasons, dense=dense, return_shape=shape,
                stale_doc_params=docstring_params(fn))


def _fn(src):
    return ast.parse(src).body[0]


def self_test():
    failed = 0

    def check(label, ok):
        nonlocal failed
        failed += not ok
        print(("ok   " if ok else "FAIL ") + label)

    flat_with = "def f(paths):\n    for p in paths:\n        with open(p) as a:\n            with open(p) as b:\n                return a, b\n"
    check("with-blocks are not nesting", max_depth(_fn(flat_with)) == 1)
    nested = "def f(x):\n    for a in x:\n        if a:\n            for b in a:\n                if b:\n                    return b\n"
    check("real nesting still counts", max_depth(_fn(nested)) == 4)
    dense = "def f(a, b, c, d):\n    if a and b and not c or d:\n        return 1\n    return 0\n"
    check("compound predicate counts its terms", bool_terms(_fn(dense))[0] == 4)
    check("compound predicate raises cyclomatic", cyclomatic(_fn(dense)) == 5)
    check("dense shows in measure()", bool(measure(_fn(dense))["dense"]))
    simple = "def f(rows):\n    out = {}\n    for r in rows:\n        out[r['k']] = r\n    return list(out.values())\n"
    check("simple function is clean", not measure(_fn(simple))["dense"] and not measure(_fn(simple))["over_budget"])
    mixed = "def f(p):\n    if not p:\n        return None\n    return {'a': 1}\n"
    check("dict-or-None is flagged", "None" in (return_shapes(_fn(mixed)) or ""))
    optional = "def f(p) -> 'dict | None':\n    if not p:\n        return None\n    return {'a': 1}\n"
    check("declared Optional is not flagged", return_shapes(_fn(optional)) is None)
    kinds = "def f(p):\n    if p:\n        return 'error'\n    return True\n"
    check("two literal kinds are flagged", "bool" in (return_shapes(_fn(kinds)) or ""))
    gen = "def f(p):\n    for x in p:\n        yield x\n    return\n"
    check("generator with bare return is not flagged", return_shapes(_fn(gen)) is None)
    a = "def parse_a(raw):\n    out = []\n    for line in raw:\n        if line:\n            out.append(line.strip())\n    return out\n"
    b = "def clean_b(text):\n    res = []\n    for row in text:\n        if row:\n            res.append(row.strip())\n    return res\n"
    c = "def other(text):\n    res = []\n    for row in text:\n        if row:\n            res.append(row.upper())\n    return res\n"
    check("renamed copy has the same fingerprint", fingerprint(_fn(a)) == fingerprint(_fn(b)) is not None)
    check("different method call is a different shape", fingerprint(_fn(a)) != fingerprint(_fn(c)))
    check("tiny functions have no fingerprint", fingerprint(_fn("def f(x):\n    return x + 1\n")) is None)
    check("test with no assertion", asserts_nothing(_fn("def test_runs():\n    load('x')\n")))
    check("assert statement counts", not asserts_nothing(_fn("def test_x():\n    assert load('x') == 1\n")))
    check("self.assertEqual counts", not asserts_nothing(_fn("def test_x(self):\n    self.assertEqual(load('x'), 1)\n")))
    check("pytest.raises counts", not asserts_nothing(_fn("def test_x():\n    with pytest.raises(ValueError):\n        load('x')\n")))
    check("mock.assert_called counts", not asserts_nothing(_fn("def test_x(m):\n    m.assert_called_once()\n")))
    stale = 'def f(path):\n    """Read it.\n\n    Args:\n        path: where.\n        encoding: the codec.\n    """\n    return path\n'
    check("docstring documents a missing parameter", docstring_params(_fn(stale)) == ["encoding"])
    api = public_api("def load(path, strict=False):\n    pass\ndef _h():\n    pass\nclass C:\n    def run(self, n):\n        pass\n")
    check("public_api skips private names", set(api) == {"load", "C.run"} and "strict=False" in api["load"])
    hinted = public_api("def load(path: str, strict: bool = False) -> list:\n    pass\n")
    plain = public_api("def load(path, strict=False):\n    pass\n")
    check("annotations alone are not a surface change", hinted == plain)
    widened = public_api("def load(path, strict=False, tz='ET'):\n    pass\n")
    check("a new parameter is a surface change", widened != plain)
    check("appending a defaulted parameter is compatible", compatible_widening(plain["load"], widened["load"]))
    check("a new required parameter is breaking",
          not compatible_widening(plain["load"], "load(path, strict=False, tz)"))
    check("renaming a parameter is breaking", not compatible_widening("load(path)", "load(filename)"))
    nested = "def f(rows, names):\n    out = []\n    for r in rows:\n        for n in names:\n            out.append((r, n))\n    return out\n"
    check("a loop inside a loop is named", any("loop inside a loop" in c for c in loop_costs(_fn(nested))))
    member = "def f(rows, universe):\n    wanted = list(universe)\n    out = []\n    for r in rows:\n        if r in wanted:\n            out.append(r)\n    return out\n"
    check("membership against a list in a loop", any("is a list" in c for c in loop_costs(_fn(member))))
    asset = "def f(rows, universe):\n    wanted = set(universe)\n    out = []\n    for r in rows:\n        if r in wanted:\n            out.append(r)\n    return out\n"
    check("membership against a set is clean", not loop_costs(_fn(asset)))
    window = "def f(xs, k):\n    out = []\n    for i in range(k, len(xs)):\n        out.append(sum(xs[i - k:i]) / k)\n    return out\n"
    check("window recomputed per row", any("recomputed per row" in c for c in loop_costs(_fn(window))))
    flat = "def f(rows):\n    out = {}\n    for r in rows:\n        out[r['k']] = r\n    return list(out.values())\n"
    check("a single flat loop is clean", not loop_costs(_fn(flat)))
    sig = signature(_fn("def f(self, a, b, *rest, k=1, **kw):\n    x = a\n    y = b\n    return x + y\n"))
    check("signature excludes self and counts locals", sig == (["a", "b", "k", "*rest", "**kw"], ["x", "y"]))
    depth_a = ("def max_depth(node, d=0):\n    best = d\n    for child in ast.iter_child_nodes(node):\n"
               "        if isinstance(child, FUNC + (ast.ClassDef, ast.Lambda)):\n            continue\n        nd = d\n"
               "        if isinstance(child, NEST):\n            nd = d + 1\n"
               "        best = max(best, max_depth(child, nd))\n    return best\n")
    depth_b = depth_a.replace("FUNC + (ast.ClassDef, ast.Lambda)", "(ast.FunctionDef, ast.ClassDef)").replace("nd", "level")
    other = ("def load(path, strict):\n    rows = []\n    with open(path) as fh:\n        for line in fh:\n"
             "            if strict and not line.strip():\n                raise ValueError(path)\n"
             "            rows.append(line.split(','))\n    return rows\n\n")
    sets = {name: copy_shingles(src.splitlines(), _fn(src)) for name, src in
            (("a", depth_a), ("b", depth_b), ("c", other))}
    check("a copy edited in one place and renamed is still a copy",
          [(a, b) for _s, a, b in near_copies(sets)] == [("a", "b")])
    check("which the exact shape hash misses", fingerprint(_fn(depth_a)) != fingerprint(_fn(depth_b)))
    check("unrelated functions are not copies", similarity(sets["a"], sets["c"]) < 0.3)
    check("a function too small to be a copy has no shingles", copy_shingles(flat.splitlines(), _fn(flat)) is None)
    check("a method tokenizes like a function", len(shingles(textwrap.indent(depth_a, "    "))) == len(sets["a"]))
    check("copies cluster", copy_clusters(near_copies(sets)) == [(near_copies(sets)[0][0], ["a", "b"])])
    print(f"{failed} failing")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    self_test()
