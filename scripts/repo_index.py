#!/usr/bin/env python3
"""repo_index.py: the repo's relationship graph and its vocabulary, in a database you can question.

usage:
  repo_index.py build   --repo R [--ref REF] [--src DIR]... [--db PATH]   index a commit (default HEAD)
  repo_index.py search  "yahoo minute bars cache" [--top N]   what in the tree is about these words
  repo_index.py about   path/to/file.py:qualname  [--top N]   what else is about the same thing as this
  repo_index.py callers NAME          who calls it, with the line
  repo_index.py callees NAME          what it calls
  repo_index.py reach   NAME [--up]   everything it calls, transitively (--up: everything that reaches it)
  repo_index.py sql     "SELECT ..."  any read-only question
  repo_index.py export  DIR           nodes.csv, edges.csv and load.cypher, for a graph database
  repo_index.py --self-test
  (search, about, callers, callees, reach, sql and export take --repo R or --db PATH)

repo_map.py prints one report and forgets the graph. This keeps it: every module, class and
function of one commit as a row, every call, import and containment as an edge, and for each
definition the words it is made of (its name, its docstring, what it calls, the modules it
uses). The store is one SQLite file under <repo>/.review-ready/, read with the standard library:
nothing to install and no server to run. It is rebuilt from git, never from the checkout, and
records the commit it describes, so it cannot disagree with the code it was built from.

Two kinds of question it answers that the report cannot:

  graph      callers, callees and transitive reach, by recursive query, without reading the tree;
  retrieval  `search` and `about` rank definitions by BM25 over their words. Shape-matching finds
             a pasted copy; this finds code on the same subject written differently, which is
             what "does the tree already have one of these?" usually means. The focus map uses
             it for every function a branch adds.

Tables: nodes(id, kind, path, qual, name, line, end, lines, is_test, doc, dead, shape, nterms),
edges(src, dst, kind, line) with kind in calls | imports | defines, terms(term, node, tf, strong),
forms(term, word), meta(key, value). A term is a word's stem; `forms` gives a readable word for it. `dead` is repo_map's reason no entry point reaches the definition; `shape` is
its structure hash, equal for two functions that are the same once renamed.

Retrieval is a lead, not a verdict: it says two definitions share vocabulary, and names the
words, so the claim can be checked by reading both.
"""
from __future__ import annotations

import argparse
import ast
import csv
import math
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import repo_map as rm  # noqa: E402  one parser and one resolver for the report and the store

K1, B = 1.2, 0.75          # BM25: term-frequency saturation, and how much a long definition is discounted
QUERY_TERMS = 10           # `about` asks with a definition's most distinctive words, not all of them
COMMON = 0.2               # a word in more than this share of definitions says nothing about a subject
MIN_SHARED = 3             # fewer shared words than this is coincidence
MIN_STRONG = 2             # of those, how many must come from the name, the docstring or a module it uses
WEAK = 0.5                 # weight of a word that is only a local name, an attribute or a short string
MIN_RELEVANCE = 0.6        # share of a full match on the query; below it the overlap is vocabulary, not subject

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE nodes(id INTEGER PRIMARY KEY, kind TEXT, path TEXT, qual TEXT, name TEXT, line INTEGER, end INTEGER,
                   lines INTEGER, is_test INTEGER, doc TEXT, dead TEXT, shape TEXT, nterms INTEGER);
CREATE TABLE edges(src INTEGER, dst INTEGER, kind TEXT, line INTEGER);
CREATE TABLE terms(term TEXT, node INTEGER, tf INTEGER, strong INTEGER);
CREATE TABLE forms(term TEXT PRIMARY KEY, word TEXT);
CREATE INDEX nodes_name ON nodes(name);
CREATE INDEX nodes_where ON nodes(path, qual);
CREATE INDEX edges_src ON edges(src, kind);
CREATE INDEX edges_dst ON edges(dst, kind);
CREATE INDEX terms_term ON terms(term);
"""

WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])")
STOP = frozenset("""
    the and for with from this that not are was were has have had can will its into than then when where which
    who why how all any each per one two use used using self cls none true false return def class import pass
    str int float bool list dict set tuple len range print get value values name names key keys item items
    data result results out tmp args kwargs arg obj type index line lines file path
""".split())


READABLE = {}              # stem -> the shortest word seen with it, for showing a match to a person


def stem(word):
    """`cached`, `caching` and `caches` are `cache`: fold the endings that change a word's form, not its meaning."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 5 and w.endswith("ed"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w


def words(text):
    """The stems of an identifier or a sentence, lower-cased; snake_case and camelCase are split."""
    out = []
    for w in WORD.findall(text or ""):
        w = w.lower()
        if len(w) < 3 or w in STOP:
            continue
        root = stem(w)
        if len(w) < len(READABLE.get(root, w + "?")):
            READABLE[root] = w
        out.append(root)
    return out


def readable(stems):
    return sorted(READABLE.get(root, root) for root in stems)


def definition_terms(mod, d):
    """(words, strong words, first paragraph of the docstring) of one definition.

    `words` counts every word it is made of; the name counts three times and the docstring twice.
    The strong ones say what it is for: its name, its docstring, and the modules it uses. The rest
    are local names, attributes and short strings, which two unrelated functions share easily.
    """
    if not hasattr(mod, "def_nodes"):                     # one walk per module, not one per definition
        mod.def_nodes = {(n.lineno, n.name): n for n in ast.walk(mod.tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    node = mod.def_nodes.get((d["line"], d["name"]))
    terms, strong = Counter(), set()
    for w in words(d["qual"]):
        terms[w] += 3
        strong.add(w)
    if node is None:
        return terms, strong, ""
    doc = ast.get_docstring(node) or ""
    for w in words(doc):
        terms[w] += 2
        strong.add(w)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            terms.update(words(sub.id))
            if sub.id in mod.imports:                     # `yf.download`: the package the alias stands for
                used = words(mod.imports[sub.id].split(".")[0])
                terms.update(used)
                strong.update(used)
        elif isinstance(sub, ast.Attribute):
            terms.update(words(sub.attr))
        elif isinstance(sub, ast.arg):
            terms.update(words(sub.arg))
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and len(sub.value) <= 40 \
                and " " not in sub.value:                 # a column name, a suffix, a key: not a message
            terms.update(words(sub.value))
    return terms, strong, doc.strip().split("\n\n")[0].replace("\n", " ")[:300]


def module_terms(mod):
    terms = Counter()
    for w in words(mod.rel[:-3]):
        terms[w] += 3
    doc = ast.get_docstring(mod.tree) or ""
    for w in words(doc):
        terms[w] += 2
    for target in mod.import_modules:
        terms.update(words(target))
    return terms, set(terms), doc.strip().split("\n\n")[0].replace("\n", " ")[:300]


def store_nodes(conn, mods):
    """Write every module and definition with its words. Returns {(path, qual): id}; a module's qual is ''."""
    conn.executescript(SCHEMA)
    shapes = rm.fingerprints(mods)[0]
    ids, rows, postings = {}, [], []
    for rel in sorted(mods):
        mod = mods[rel]
        entries = [("module", "", os.path.basename(rel)[:-3], 1, mod.lines, module_terms(mod))]
        entries += [(d["kind"], qual, d["name"], d["line"], d["end"], definition_terms(mod, d))
                    for qual, d in mod.defs.items()]
        for kind, qual, name, line, end, (terms, strong, doc) in entries:
            ids[(rel, qual)] = node = len(ids) + 1
            rows.append((node, kind, rel, qual, name, line, end, end - line + 1, int(mod.is_test), doc, None,
                         shapes.get((rel, qual)), sum(terms.values())))
            postings += [(term, node, tf, int(term in strong)) for term, tf in terms.items()]
    conn.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO terms VALUES (?,?,?,?)", postings)
    conn.executemany("INSERT INTO forms VALUES (?,?)", sorted((term, READABLE.get(term, term)) for term in {p[0] for p in postings}))
    return ids


def store_graph(conn, mods, ids, edges, dead):
    """Write the edges (calls, imports, defines) and repo_map's verdict on what nothing reaches."""
    rows = [(ids[(src, caller or "")], ids[(dst, qual)], "calls", line) for src, caller, dst, qual, line in edges
            if (src, caller or "") in ids and (dst, qual) in ids]
    rows += [(ids[(rel, "")], ids[(target, "")], "imports", None)
             for rel, targets in rm.import_graph(mods).items() for target in sorted(targets) if (target, "") in ids]
    for rel, mod in mods.items():
        for qual, d in mod.defs.items():
            rows.append((ids[(rel, d.get("cls") or "")], ids[(rel, qual)], "defines", d["line"]))
    conn.executemany("INSERT INTO edges VALUES (?,?,?,?)", rows)
    conn.executemany("UPDATE nodes SET dead = ? WHERE id = ?",
                     [(row["why"], ids[(row["file"], row["qual"])]) for row in dead if (row["file"], row["qual"]) in ids])


def build(repo, ref, src_dirs, db):
    """Index the commit `ref` of `repo` into the SQLite file `db`, replacing what was there."""
    sha = subprocess.run(["git", "-C", repo, "rev-parse", ref], capture_output=True, text=True)
    if sha.returncode:
        sys.exit(f"{ref}: not a commit in {repo}")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    if os.path.exists(db):
        os.remove(db)
    exclude = subprocess.run(["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"],
                             capture_output=True, text=True).stdout.strip()
    if exclude and ".review-ready/" not in (open(exclude).read() if os.path.exists(exclude) else ""):
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        with open(exclude, "a") as fh:                    # the store is not a change to the repo
            fh.write("\n.review-ready/\n")
    conn = sqlite3.connect(db)
    with tempfile.TemporaryDirectory() as tree:
        rm.export_commit(repo, ref, tree)
        mods = rm.parse_repo(tree, src_dirs)
        edges, _unresolved = rm.build_graph(mods)
        ids = store_nodes(conn, mods)
        store_graph(conn, mods, ids, edges, rm.find_dead(mods, edges, rm.entry_points(tree, mods)))
    conn.executemany("INSERT INTO meta VALUES (?,?)", [("commit", sha.stdout.strip()), ("ref", ref)])
    conn.commit()
    return conn


def ranked(conn, query, top, skip=()):
    """BM25 over the definitions' words. `query` is {word: weight}. Returns [(relevance, node id, shared words)].

    Relevance is the score as a share of what a definition of average length scores when it
    holds every query word once, capped at 1: a threshold then means the same thing for a
    two-word query and a ten-word one.
    """
    total, average = conn.execute("SELECT COUNT(*), AVG(nterms) FROM nodes WHERE nterms > 0").fetchone()
    scores, shared, best = Counter(), defaultdict(list), 0.0
    for term, weight in query.items():
        hits = conn.execute("SELECT t.node, t.tf, n.nterms FROM terms t JOIN nodes n ON n.id = t.node WHERE t.term = ?",
                            (term,)).fetchall()
        if not hits:
            continue
        idf = math.log(1 + (total - len(hits) + 0.5) / (len(hits) + 0.5))
        best += weight * idf
        for node, tf, length in hits:
            scores[node] += weight * idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
            shared[node].append(term)
    found = [(min(1.0, score / best), node, shared[node]) for node, score in scores.items() if node not in skip]
    return sorted(found, key=lambda hit: (-hit[0], hit[1]))[:top]


def distinctive(conn, terms, strong):
    """The few words of a definition that say what it is about: frequent in it, rare in the tree.

    Returns {word: weight}; a word that is not one of its `strong` words weighs WEAK.
    """
    total = conn.execute("SELECT COUNT(*) FROM nodes WHERE nterms > 0").fetchone()[0]
    weighted = []
    for term, tf in terms.items():
        df = conn.execute("SELECT COUNT(*) FROM terms WHERE term = ?", (term,)).fetchone()[0]
        if 0 < df <= COMMON * total:
            weighted.append((tf * math.log(1 + (total - df + 0.5) / (df + 0.5)), term))
    return {term: 1.0 if term in strong else WEAK for _weight, term in sorted(weighted, reverse=True)[:QUERY_TERMS]}


def same_subject(conn, terms, strong, top=3, skip=()):
    """Definitions in the store on the same subject as one made of `terms`: [(relevance, node, shared words)].

    Sharing local names is not sharing a subject: MIN_STRONG of the shared words must be among the
    asking definition's strong ones.
    """
    hits = ranked(conn, distinctive(conn, terms, strong), top + 20, skip)
    return [hit for hit in hits if len(hit[2]) >= MIN_SHARED and len(strong & set(hit[2])) >= MIN_STRONG
            and hit[0] >= MIN_RELEVANCE][:top]


def where(conn, node):
    kind, path, qual, line = conn.execute("SELECT kind, path, qual, line FROM nodes WHERE id = ?", (node,)).fetchone()
    return f"{path}:{qual}" if qual else path, kind, line


def find(conn, name):
    """Node ids for `path:qual`, a path, a qualified name or a bare name."""
    path, _, qual = name.partition(":")
    rows = conn.execute("SELECT id FROM nodes WHERE (path = ? AND qual = ?) OR qual = ? OR (name = ? AND kind != 'module')",
                        (path, qual, name, name)).fetchall()
    if not rows:
        sys.exit(f"nothing named {name!r} in the index")
    return [r[0] for r in rows]


def neighbours(conn, node, column, other):
    return conn.execute(f"SELECT {other}, line FROM edges WHERE {column} = ? AND kind = 'calls' ORDER BY {other}, line",
                        (node,)).fetchall()


def reach(conn, node, up):
    src, dst = ("dst", "src") if up else ("src", "dst")
    return [r[0] for r in conn.execute(
        f"WITH RECURSIVE seen(id) AS (SELECT ? UNION SELECT e.{dst} FROM edges e JOIN seen ON e.{src} = seen.id "
        f"WHERE e.kind = 'calls') SELECT id FROM seen WHERE id != ? ORDER BY id", (node, node))]


def export(conn, out):
    """nodes.csv, edges.csv and load.cypher: the same graph for a graph database (Neo4j's LOAD CSV)."""
    os.makedirs(out, exist_ok=True)
    for table, columns in (("nodes", "id, kind, path, qual, name, line, lines, is_test, dead"),
                           ("edges", "src, dst, kind, line")):
        with open(os.path.join(out, f"{table}.csv"), "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns.split(", "))
            writer.writerows(conn.execute(f"SELECT {columns} FROM {table}"))
    load = ["// Copy nodes.csv and edges.csv into the database's import folder, then run this file.",
            "LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS r",
            "CREATE (:Def {id: toInteger(r.id), kind: r.kind, path: r.path, qual: r.qual, name: r.name,",
            "              line: toInteger(r.line), lines: toInteger(r.lines), is_test: r.is_test = '1', dead: r.dead});",
            "CREATE INDEX def_id IF NOT EXISTS FOR (d:Def) ON (d.id);"]
    for kind in ("calls", "imports", "defines"):
        load += ["LOAD CSV WITH HEADERS FROM 'file:///edges.csv' AS r", f"WITH r WHERE r.kind = '{kind}'",
                 "MATCH (a:Def {id: toInteger(r.src)}), (b:Def {id: toInteger(r.dst)})",
                 f"CREATE (a)-[:{kind.upper()} {{line: toInteger(r.line)}}]->(b);"]
    with open(os.path.join(out, "load.cypher"), "w") as fh:
        fh.write("\n".join(load) + "\n")


def show(conn, hits):
    for relevance, node, shared in hits:
        place, kind, line = where(conn, node)
        doc = conn.execute("SELECT doc FROM nodes WHERE id = ?", (node,)).fetchone()[0]
        print(f"{relevance:5.0%}  {place} ({kind}, line {line})  shares: {', '.join(readable(shared))}"
              + (f"\n       {doc}" if doc else ""))


SELF_TEST = {
    "shop/__init__.py": "",
    "shop/cli.py": "from shop.orders import total_price\n\n\ndef main():\n    print(total_price([{'price': 2, 'qty': 3}]))\n\n\n"
                   "if __name__ == '__main__':\n    main()\n",
    "shop/orders.py": "from shop.money import round_cents\n\n\ndef total_price(lines):\n"
                      "    \"\"\"Sum of price times quantity over the order lines.\"\"\"\n"
                      "    return round_cents(sum(line['price'] * line['qty'] for line in lines))\n\n\n"
                      "def unused_report():\n    return 0\n",
    "shop/money.py": "def round_cents(amount):\n    return round(amount, 2)\n",
    "shop/prices.py": "import urllib.request\n\n\ndef fetch_exchange_rates(currency, cache_dir):\n"
                      "    \"\"\"Download today's exchange rates for a currency and keep them in a cache file.\"\"\"\n"
                      "    cache_file = cache_dir / f'{currency}_rates.json'\n    if cache_file.exists():\n"
                      "        return cache_file.read_text()\n"
                      "    body = urllib.request.urlopen(f'https://rates.example/{currency}').read()\n"
                      "    cache_file.write_bytes(body)\n    return body\n",
    "shop/shipping.py": "def parcel_weight(lines):\n    return sum(line['weight'] * line['qty'] for line in lines)\n",
}
# Written differently, about the same thing as fetch_exchange_rates: no shape or name in common.
SELF_TEST_NEW = ("import requests\n\n\nclass RateStore:\n    def load(self, code, folder):\n"
                 "        saved = folder / (code + '_rates.json')\n        if not saved.exists():\n"
                 "            saved.write_text(requests.get('https://rates.example/' + code).text)\n"
                 "        return saved.read_text()\n")


def self_test():
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="T",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    with tempfile.TemporaryDirectory() as repo:
        for rel, text in SELF_TEST.items():
            os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
            with open(os.path.join(repo, rel), "w") as fh:
                fh.write(text)
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "base"]):
            subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, env=env)
        with open(os.path.join(repo, "shop", "money.py"), "a") as fh:
            fh.write("\n\ndef checkout_only():\n    return 1\n")                # uncommitted: must not be indexed
        db = os.path.join(repo, ".review-ready", "index.sqlite")
        conn = build(repo, "HEAD", [], db)
        named = lambda node: where(conn, node)[0]   # noqa: E731
        total = find(conn, "total_price")[0]
        new = ast.parse(SELF_TEST_NEW)
        mod = rm.analyse(rm.Module("shop/rates.py", SELF_TEST_NEW, new))
        alike = same_subject(conn, *definition_terms(mod, mod.defs["RateStore.load"])[:2])
        out = os.path.join(repo, "graph")
        export(conn, out)
        with open(os.path.join(out, "edges.csv")) as fh:
            exported = list(csv.DictReader(fh))
        checks = [
            ("a caller is found with its line", [(named(n), line) for n, line in neighbours(conn, total, "dst", "src")]
             == [("shop/cli.py:main", 5)]),
            ("callees are found", [named(n) for n, _ in neighbours(conn, total, "src", "dst")] == ["shop/money.py:round_cents"]),
            ("reach follows calls transitively", [named(n) for n in reach(conn, find(conn, "shop/cli.py:main")[0], False)]
             == ["shop/money.py:round_cents", "shop/orders.py:total_price"]),
            ("reach --up finds everything that gets there", [named(n) for n in reach(conn, find(conn, "round_cents")[0], True)]
             == ["shop/cli.py", "shop/cli.py:main", "shop/orders.py:total_price"]),
            ("search ranks the definition the words describe first",
             named(ranked(conn, dict.fromkeys(words("download exchange rates cache"), 1.0), 1)[0][1])
             == "shop/prices.py:fetch_exchange_rates"),
            ("code on the same subject is found though it shares no shape and no name",
             [named(hit[1]) for hit in alike] == ["shop/prices.py:fetch_exchange_rates"]),
            ("the match names the words it rests on, as words", bool(alike) and {"rates", "exists"} & set(readable(alike[0][2]))
             and {"rate", "exist"} <= set(alike[0][2])),
            ("forms of one word are one word", stem("cached") == stem("caching") == stem("caches") == stem("cache")
             and stem("class") == "class" and stem("queries") == stem("query")),
            ("code on another subject is not offered",
             not same_subject(conn, Counter(words("parse yaml config schema validate settings loader")),
                              set(words("parse yaml config schema")))),
            ("what nothing reaches carries repo_map's reason",
             conn.execute("SELECT dead FROM nodes WHERE qual = 'unused_report'").fetchone()[0] == "nothing references it"),
            ("the commit is indexed, not the checkout",
             not conn.execute("SELECT 1 FROM nodes WHERE qual = 'checkout_only'").fetchone()),
            ("the store does not show up as a change to the repo",
             ".review-ready" not in subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True,
                                                   text=True).stdout),
            ("the store records its commit", len(conn.execute("SELECT value FROM meta WHERE key = 'commit'").fetchone()[0]) == 40),
            ("the export holds every edge with both ends",
             len(exported) == conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] and all(e["src"] and e["dst"] for e in exported)),
        ]
        conn.close()
    failed = sum(not ok for _name, ok in checks)
    for name, ok in checks:
        print(("ok   " if ok else "FAIL ") + name)
    print(f"{len(checks)} cases, {failed} failing")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["build", "search", "about", "callers", "callees", "reach", "sql", "export"])
    ap.add_argument("what", nargs="?")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--src", action="append", default=[], help="limit to this directory (repeatable)")
    ap.add_argument("--db", help="default: <repo>/.review-ready/index.sqlite")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--up", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)      # `... | head` ends the output, it is not an error
    if args.self_test:
        sys.exit(self_test())
    if not args.command:
        ap.error("a command is required")
    db = args.db or os.path.join(os.path.abspath(args.repo), ".review-ready", "index.sqlite")
    if args.command == "build":
        conn = build(args.repo, args.ref, args.src, db)
        counts = dict(conn.execute("SELECT kind, COUNT(*) FROM edges GROUP BY kind"))
        print(f"indexed {args.ref} ({conn.execute('SELECT value FROM meta WHERE key = ?', ('commit',)).fetchone()[0][:10]}): "
              f"{conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]} nodes, {counts.get('calls', 0)} calls, "
              f"{counts.get('imports', 0)} imports, {conn.execute('SELECT COUNT(DISTINCT term) FROM terms').fetchone()[0]} "
              f"distinct words -> {db}")
        return
    if not os.path.exists(db):
        sys.exit(f"no index at {db}: run `repo_index.py build --repo {args.repo}` first")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    READABLE.update(conn.execute("SELECT term, word FROM forms"))
    if args.command == "export":
        export(conn, args.what or "graph")
        print(f"wrote nodes.csv, edges.csv and load.cypher to {args.what or 'graph'}")
    elif args.command == "sql":
        try:
            rows = conn.execute(args.what).fetchall()
        except sqlite3.Error as exc:                       # the store is opened read-only: a write lands here
            sys.exit(f"sql: {exc}")
        for row in rows:
            print("\t".join("" if v is None else str(v) for v in row))
    elif args.command == "search":
        show(conn, ranked(conn, dict.fromkeys(words(args.what), 1.0), args.top))
    elif args.command == "about":
        for node in find(conn, args.what):
            rows = conn.execute("SELECT term, tf, strong FROM terms WHERE node = ?", (node,)).fetchall()
            terms, strong = Counter({term: tf for term, tf, _s in rows}), {term for term, _tf, s in rows if s}
            print(f"{where(conn, node)[0]}: asked with {', '.join(readable(distinctive(conn, terms, strong)))}")
            show(conn, same_subject(conn, terms, strong, args.top, skip={node}))
    else:
        for node in find(conn, args.what):
            print(f"{where(conn, node)[0]}:")
            if args.command == "reach":
                found = [(n, None) for n in reach(conn, node, args.up)]
            else:
                found = neighbours(conn, node, *(("dst", "src") if args.command == "callers" else ("src", "dst")))
            for other, line in found:
                print(f"  {where(conn, other)[0]}" + (f"  (line {line})" if line else ""))


if __name__ == "__main__":
    main()
