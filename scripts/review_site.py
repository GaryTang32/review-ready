#!/usr/bin/env python3
"""review_site.py: one page that shows what a pass does to the code's graph, and why.

usage: review_site.py WORKDIR [WORKDIR ...] [--out review.html] [--title NAME] [--fragment]
       review_site.py --self-test

WORKDIR is a pass's work directory ($W, building-and-landing.md section 1): plan.json and
msgs/, and whichever of problem.md, deferred.md, cover-letter.md, defence.md, size-gate.json
and review-v*.md exist. Several work directories make one page with a tab for each.

The page reads two commits from git and never the checkout: the branch as the owner left it
(refs/review-ready/original/<branch>, else the branch; the fork point in write mode) and the
built series (refs/review-ready/series/<branch>). Every number on it comes from those commits
or from a file in the work directory, and the footer names both commits.

What it shows, for the files the branch or the series changes:
  the graph   every function and class, as the branch wrote it and as the series has it, and
              the calls between them, on one layout, so a dropped helper or a new call is seen
              in place; the code elsewhere in the tree they call or are called by; and, from
              repo_index.py, the code elsewhere on the same subject
  the reasons for each function: its fate (kept, rewritten, dropped, added by the series), its
              lines on both sides, the patch that changes it, and every sentence of deferred.md,
              the commit messages and the defence that names it
  the plan    each patch with its message, split into why and how it was checked
  the evidence  the problem statement, the PR description, the size table, what was deferred,
              and the review rounds with their verdicts

`--fragment` leaves out <!doctype>, <html>, <head> and <body>, for a host that adds them.
The page loads no script from anywhere; fonts come from Google Fonts, with fallbacks.
"""
from __future__ import annotations

import argparse
import ast
import glob
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import repo_map as rm  # noqa: E402  one parser and one resolver for the map, the index and the page
from audit_diff import reviewable_lines  # noqa: E402  the size gate's own count
import reasons as rs  # noqa: E402  the reason gate's own list of changes and their reasons

try:
    import repo_index as ri
except ImportError:                                       # the page still works without the retrieval
    ri = None

DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
NEIGHBOURS = 6            # code elsewhere shown per changed function, each way: the graph stays readable
RESERVED = {"settings", "sidebar", "customize", "upgrade", "downloads"}
TRAILER = re.compile(r"^(Co-Authored-By|Claude-Session|Signed-off-by|Reviewed-by|Acked-by|Fixes|Link):", re.I)


def git(repo, *args, ok=False):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode:
        if ok:
            return ""
        sys.exit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def read(folder, name):
    path = os.path.join(folder, name)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


# ---------------------------------------------------------------- markdown, enough for the work files

def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)


def markdown(text):
    """Headings, paragraphs, two levels of list, tables, fences and quotes, as HTML."""
    out, para, items, table, fence = [], [], [], [], None
    item = re.compile(r"^(\s*)([-*]|\d+[.)])\s+(.*)$")

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            tag = "ol" if items[0][1] else "ul"
            parts, open_sub = [f"<{tag}>"], False
            for level, _numbered, words in items:
                if level and not open_sub:
                    parts[-1] = parts[-1][:-5]           # reopen the last <li> to nest under it
                    parts.append("<ul>")
                    open_sub = True
                elif not level and open_sub:
                    parts.append("</ul></li>")
                    open_sub = False
                parts.append(f"<li>{inline(' '.join(words))}</li>")
            parts.append(("</ul></li>" if open_sub else "") + f"</{tag}>")
            out.append("".join(parts))
            items.clear()
        if table:
            rows = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in table
                    if not re.match(r"^\s*\|[\s:|-]+\|\s*$", row)]
            head, body = rows[0], rows[1:]
            out.append('<div class="table-wrap"><table><thead><tr>' + "".join(f"<th>{inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>"
                                                      for row in body) + "</tbody></table></div>")
            table.clear()

    for line in text.splitlines():
        if fence is not None:
            if line.strip().startswith("```"):
                out.append(f"<pre><code>{html.escape(chr(10).join(fence))}</code></pre>")
                fence = None
            else:
                fence.append(line)
            continue
        if line.strip().startswith("```"):
            flush()
            fence = []
            continue
        if not line.strip():
            flush()
            continue
        if line.lstrip().startswith("|"):
            if para or items:
                flush()
            table.append(line)
            continue
        if table:
            flush()
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush()
            level = 3 if len(heading.group(1)) <= 2 else 4
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            continue
        if line.startswith(">"):
            flush()
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
            continue
        m = item.match(line)
        if m and (items or not para):
            items.append((1 if len(m.group(1)) >= 2 and items else 0, m.group(2)[0].isdigit(), [m.group(3)]))
        elif items and line.startswith(" "):
            items[-1][2].append(line.strip())
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "\n".join(out)


def letter(text):
    """The PR description (plain text, sections ending in a colon) as HTML."""
    lines, section = [], ""
    for line in text.splitlines():
        if line.startswith("[PATCH"):
            continue
        if re.match(r"^[A-Z][^:]{0,90}:$", line):
            section = line
            lines += ["", f"### {line[:-1]}"]
            continue
        body = line[2:] if line.startswith("  ") else line
        if section.startswith("Series") and re.match(r"^\d+/\d+ ", body):
            body = "- " + body
        lines.append(body)
    return markdown("\n".join(lines))


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


def mentions(name, qual, texts):
    """[(source, sentence html)] for the sentences that name a definition as code: `name`, `name()`, or name()."""
    if not name:
        return []
    pattern = re.compile(rf"`{re.escape(qual)}(\(\))?`|`{re.escape(name)}(\(\))?`|(?<![\w.]){re.escape(name)}\(\)")
    found = []
    for source, text in texts:
        hits = [b for b in blocks(text) if pattern.search(b)]
        found += [(source, inline(b)) for b in hits[:3]]
    return found


# ---------------------------------------------------------------- the code, read from git

def module_at(repo, ref, path):
    src = git(repo, "show", f"{ref}:{path}", ok=True)
    try:
        return rm.analyse(rm.Module(path, src, ast.parse(src)))
    except SyntaxError:
        return None


def dumps(mod):
    """{qual: the definition's syntax tree as text}: equal when the code is, whatever the formatting.

    The key "" is the module's own code: its imports, assignments and the `__main__` block.
    """
    if mod is None:
        return {}
    nodes = {(n.lineno, n.name): n for n in ast.walk(mod.tree) if isinstance(n, DEFS)}
    out = {q: ast.dump(nodes[(d["line"], d["name"])]) for q, d in mod.defs.items() if (d["line"], d["name"]) in nodes}
    out[""] = ast.dump(ast.Module(body=[n for n in mod.tree.body if not isinstance(n, DEFS)], type_ignores=[]))
    return out


def tree_at(repo, ref):
    """(modules, call edges) of the commit `ref`, the whole tree, so calls into the rest of it resolve."""
    with tempfile.TemporaryDirectory() as folder:
        rm.export_commit(repo, ref, folder)
        mods = rm.parse_repo(folder, [])
    return mods, rm.build_graph(mods)[0]


def slug(text, taken):
    s = re.sub(r"[^a-z0-9]+", "-", text.split("/")[-1].lower()).strip("-") or "review"
    if s in RESERVED or s.startswith("comment"):
        s = "review-" + s
    while s in taken:
        s += "-2"
    taken.add(s)
    return s


def load_review(folder, taken):
    """Everything the page shows about one pass, as plain data."""
    folder = os.path.abspath(folder)
    plan = json.load(open(os.path.join(folder, "plan.json")))
    repo = git(os.path.normpath(os.path.join(folder, plan.get("repo", "."))), "rev-parse", "--show-toplevel").strip()
    source = plan.get("source") or plan.get("branch")
    base, onto = plan["base"], plan.get("onto") or plan["base"]
    original = f"refs/review-ready/original/{source}" if plan.get("source") else base
    if not git(repo, "rev-parse", "--verify", "-q", original, ok=True):
        original = source
    series = f"refs/review-ready/series/{source}" if plan.get("source") else plan["branch"]
    if not git(repo, "rev-parse", "--verify", "-q", series, ok=True):
        sys.exit(f"{folder}: no built series at {series}; run build_series.py first")
    landed = git(repo, "merge-base", onto, series).strip()
    sha = {name: git(repo, "rev-parse", ref).strip() for name, ref in
           (("original", original), ("series", series), ("base", base), ("landed", landed))}

    changed = sorted({f for f in git(repo, "diff", "--name-only", base, original).split()
                      + git(repo, "diff", "--name-only", landed, series).split() if f.endswith(".py")})
    before_mods, before_edges = tree_at(repo, original)
    after_mods, after_edges = tree_at(repo, series)
    fork = {rel: dumps(module_at(repo, base, rel)) for rel in changed}
    dump_b = {rel: dumps(before_mods.get(rel)) for rel in changed}
    dump_a = {rel: dumps(after_mods.get(rel)) for rel in changed}

    def definition(mods, key):
        mod = mods.get(key[0])
        if mod is None:
            return None
        if key[1] == "":
            return dict(name=os.path.basename(key[0]), kind="module", line=1, end=mod.lines)
        return mod.defs.get(key[1])

    # Patches: the first commit of the series whose version of a definition differs from its parent's.
    commits = git(repo, "rev-list", "--reverse", f"{landed}..{series}").split()
    patch_of, patches = {}, []
    for i, commit in enumerate(commits):
        files = [f for f in git(repo, "diff", "--name-only", f"{commit}^", commit).split()]
        for rel in (f for f in files if f.endswith(".py")):
            new, old = dumps(module_at(repo, commit, rel)), dumps(module_at(repo, f"{commit}^", rel))
            for qual, text in new.items():
                if old.get(qual) != text:
                    patch_of.setdefault((rel, qual), i)
        added = deleted = 0
        for row in git(repo, "show", "--numstat", "--format=", commit).splitlines():
            a, d, _p = row.split("\t", 2)
            if a != "-":
                added, deleted = added + int(a), deleted + int(d)
        message = [line for line in git(repo, "log", "-1", "--format=%B", commit).splitlines() if not TRAILER.match(line)]
        paragraphs = "\n".join(message[1:]).strip().split("\n\n")
        checked = [p for p in paragraphs if re.match(r"^\s*(Tested|No tests|Testing|Run |Checked)", p)]
        patches.append(dict(subject=message[0], files=files, added=added, deleted=deleted,
                            why=markdown("\n\n".join(p for p in paragraphs if p not in checked)),
                            checked=markdown("\n\n".join(checked)), sha=commit[:10]))

    # Nodes: every definition in a changed file on either side, then what they reach elsewhere.
    nodes, edges = {}, {}

    def add(key, role):
        if key not in nodes:
            nodes[key] = dict(path=key[0], qual=key[1], role=role, test=rm.is_test(key[0]),
                              before=bool(definition(before_mods, key)), after=bool(definition(after_mods, key)))
        return nodes[key]

    for rel in changed:
        for qual in sorted((set(dump_b[rel]) | set(dump_a[rel])) - {""}):
            b, a, f = dump_b[rel].get(qual), dump_a[rel].get(qual), fork[rel].get(qual)
            status = ("unchanged" if a == b == f else "kept" if a == b else "restored" if a == f and b
                      else "rewritten") if a and b else "dropped" if b else "new"
            add((rel, qual), "focus")["status"] = status
    for side, found in (("before", before_edges), ("after", after_edges)):
        reached = defaultdict(int)
        for src, caller, dst, qual, line in found:
            s, d = (src, caller or ""), (dst, qual)
            if s == d or (src not in changed and dst not in changed):
                continue
            inside = d if dst in changed else s
            if (src not in changed or dst not in changed) and reached[inside] >= NEIGHBOURS:
                continue
            if src not in changed or dst not in changed:
                reached[inside] += 1
            for key in (s, d):
                node = add(key, "focus" if key[0] in changed else "context")
                if key[1] == "" and key[0] in changed:            # module code: compared like any definition
                    b, a, f = (dumps_[key[0]].get("") for dumps_ in (dump_b, dump_a, fork))
                    node.setdefault("status", ("unchanged" if a == b == f else "kept" if a == b else "rewritten")
                                    if a and b else "dropped" if b else "new")
                node.setdefault("status", "unchanged" if node["role"] == "focus" else "context")
            edge = edges.setdefault((s, d, "calls"), dict(before=False, after=False, line=line))
            edge[side] = True
    # An unchanged definition stays on the page only while it is connected to one that changes.
    moving = {k for k, n in nodes.items() if n["status"] not in ("unchanged", "context")}
    linked = {k for s, d, _kind in edges for k in (s, d) if {s, d} & moving}
    for key in [k for k, n in nodes.items() if n["status"] in ("unchanged", "context") and k not in linked]:
        del nodes[key]
    edges = {k: e for k, e in edges.items() if k[0] in nodes and k[1] in nodes}

    # Same subject: the code the series lands on, ranked by the words it shares with each changed function.
    if ri:
        store = sqlite3.connect(":memory:")
        ri.store_nodes(store, {rel: m for rel, m in after_mods.items() if rel not in changed})
        for key, node in list(nodes.items()):
            mods = after_mods if node["after"] else before_mods          # a dropped helper is asked as the branch wrote it
            d = definition(mods, key)
            if (node["role"] != "focus" or node["test"] or key[1] == "" or node["status"] == "unchanged"
                    or d["name"] == "main" or d["name"].startswith("__")):
                continue
            for relevance, hit, shared in ri.same_subject(store, *ri.definition_terms(mods[key[0]], d)[:2], top=1):
                path, qual = store.execute("SELECT path, qual FROM nodes WHERE id = ?", (hit,)).fetchone()
                add((path, qual), "lead").setdefault("status", "lead")
                edges[(key, (path, qual), "similar")] = dict(before=node["before"], after=node["after"],
                                                             relevance=round(relevance, 2), shared=ri.readable(shared))

    # The reason gate's view: every change from the branch to the series, its reason, the owner's decision.
    compared = f"refs/review-ready/compared/{source}"
    compared = compared if git(repo, "rev-parse", "--verify", "-q", compared, ok=True) else series
    rows = rs.changes(repo, original, compared)
    if compared == series and landed != sha["base"]:                 # rebased: keep the files the pass is about
        touched = set(git(repo, "diff", "--name-only", base, original).split()) | set(git(repo, "diff", "--name-only", landed, series).split())
        rows = [r for r in rows if r["path"] in touched]
    decisions_path = os.path.join(folder, "decisions.json")
    decisions = json.load(open(decisions_path)) if os.path.exists(decisions_path) else {}
    rows, _problems = rs.account(rows, plan, rs.sources(plan, folder), decisions)
    change_of = {r["key"]: r for r in rows}

    def change_for(key):
        """The gate's row for a definition: its own, or its class's when the class came or went whole."""
        qual = key[1] or rs.MODULE
        row = change_of.get(f"{key[0]}:{qual}")
        if row is None and "." in qual:
            row = change_of.get(f"{key[0]}:{qual.rsplit('.', 1)[0]}")
        return row

    # Reasons: every sentence of the pass's own files that names the definition.
    texts = [("deferred.md", read(folder, "deferred.md"))]
    texts += [(f"message {i + 1}", git(repo, "log", "-1", "--format=%B", c)) for i, c in enumerate(commits)]
    texts += [("defence.md", read(folder, "defence.md"))]
    rounds = sorted(glob.glob(os.path.join(folder, "review-v*.md")), key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
    review_texts = [(os.path.basename(rounds[-1]), read(folder, os.path.basename(rounds[-1])))] if rounds else []

    order = {"focus": 1, "context": 2, "lead": 3}
    columns, column_of = [], {}
    for path in sorted(changed, key=lambda p: (not rm.is_test(p), changed.index(p))):
        if any(k[0] == path for k in nodes):
            column_of[path] = len(columns)
            columns.append(dict(label=path, kind="test" if rm.is_test(path) else "code"))
    if any(n["role"] != "focus" for n in nodes.values()):
        columns.append(dict(label="Elsewhere in the tree", kind="elsewhere"))

    def line_of(key):
        d = definition(after_mods, key) or definition(before_mods, key)
        return d["line"] if d else 0

    keys = sorted(nodes, key=lambda k: (column_of.get(k[0], len(columns) - 1), order[nodes[k]["role"]] if k[0] not in column_of else 0,
                                        k[0] if k[0] not in column_of else "", line_of(k), k[1]))
    index = {k: i for i, k in enumerate(keys)}
    out_nodes = []
    for key in keys:
        n, name = nodes[key], key[1].split(".")[-1]
        sides = {}
        for side, mods in (("before", before_mods), ("after", after_mods)):
            d = definition(mods, key)
            sides[side] = dict(line=d["line"], lines=d["end"] - d["line"] + 1) if d else None
        kind = (definition(after_mods, key) or definition(before_mods, key) or {}).get("kind", "function")
        row = change_for(key) if n["role"] == "focus" else None
        out_nodes.append(dict(
            key=row["key"] if row else None, change=bool(row), reason=row["reason"] if row else None,
            decision=row.get("decision") if row else None,
            id=index[key], path=key[0], qual=key[1], label=key[1] or "module code", kind=kind, role=n["role"],
            status=n["status"], test=n["test"], before=sides["before"], after=sides["after"],
            col=column_of.get(key[0], len(columns) - 1), patch=patch_of.get(key),
            notes=[] if n["role"] != "focus" or not key[1] else mentions(name, key[1], texts),
            review=[] if n["role"] != "focus" or not key[1] else mentions(name, key[1], review_texts)))
    out_edges = [dict(src=index[s], dst=index[d], kind=kind, **e) for (s, d, kind), e in sorted(edges.items(), key=lambda kv: (index[kv[0][0]], index[kv[0][1]]))]

    focus = [n for n in out_nodes if n["role"] == "focus" and n["kind"] != "module"]
    code = [n for n in focus if not n["test"] and n["status"] != "unchanged"]
    tests = [n for n in focus if n["test"] and n["qual"].split(".")[-1].startswith("test")]
    within = [e for e in out_edges if e["kind"] == "calls" and out_nodes[e["src"]]["role"] == out_nodes[e["dst"]]["role"] == "focus"]
    lines_b = reviewable_lines(repo, base, original, renames=False)
    lines_a = reviewable_lines(repo, landed, series, renames=False)
    gate = json.loads(read(folder, "size-gate.json") or "{}")

    start = next((n["id"] for status in ("dropped", "new", "rewritten", "restored") for n in code if n["status"] == status),
                 out_nodes[0]["id"] if out_nodes else None)
    ig = rm.import_graph(after_mods)
    package = lambda path: path.endswith("__init__.py")   # noqa: E731  importing a module runs its packages: not news
    where = [dict(path=rel, runs=after_mods[rel].has_main,
                  imported_by=sorted(m for m, targets in ig.items() if rel in targets and m not in changed),
                  imports=sorted(t for t in ig.get(rel, ()) if t not in changed and not package(t)))
             for rel in changed if rel in after_mods and not rm.is_test(rel) and not (package(rel) and not after_mods[rel].defs)]
    verdicts = []
    for path in rounds:
        text = read(folder, os.path.basename(path))
        found = re.search(r"^#+\s*Verdict:\s*([A-Z]+)", text, re.M)
        verdicts.append(dict(name=os.path.basename(path), verdict=found.group(1) if found else "?",
                             answered=bool(re.search(r"^#+\s*What round \d+ changed", text, re.M)),
                             same=bool(re.search(r"Reviewer:\s*same context", text, re.I))))
    title = (read(folder, "cover-letter.md").splitlines() or [""])[0]
    title = re.sub(r"^\[PATCH[^\]]*\]\s*", "", title) or source
    return dict(
        slug=slug(source, taken), title=title, branch=source, onto=onto, sha=sha, start=start,
        nodes=out_nodes, edges=out_edges, columns=columns, patches=patches,
        stats=dict(
            changes=len(rows), reasoned=sum(1 for r in rows if r["reason"]),
            decided=sum(1 for r in rows if r.get("decision") == "adopt"),
            lines=[sum(lines_b.values()), sum(lines_a.values())],
            functions=[sum(1 for n in code if n["before"]), sum(1 for n in code if n["after"])],
            fates={s: sum(1 for n in code if n["status"] == s) for s in ("kept", "rewritten", "restored", "dropped", "new")},
            tests=[sum(1 for n in tests if n["before"]), sum(1 for n in tests if n["after"])],
            calls=[sum(1 for e in within if e["before"]), sum(1 for e in within if e["after"])],
            gate=gate.get("passed"),
            tree=dict(modules=len(after_mods), definitions=sum(len(m.defs) for m in after_mods.values()), calls=len(after_edges))),
        size=[dict(path=p, before=lines_b.get(p, 0), after=lines_a.get(p, 0), exempt=(plan.get("size_ok") or {}).get(p))
              for p in sorted(set(lines_b) | set(lines_a), key=lambda p: (-lines_b.get(p, 0), p))],
        where=where, verdicts=verdicts, run=plan.get("run") or "auto",
        others=[dict(key=r["key"], kind=r["kind"], fate=r["fate"], reason=r["reason"], decision=r.get("decision"))
                for r in rows if not any(n["key"] == r["key"] for n in out_nodes)],
        problem=markdown(read(folder, "problem.md")), letter=letter(read(folder, "cover-letter.md")),
        deferred=markdown(read(folder, "deferred.md")), defence=markdown(read(folder, "defence.md")),
        tests=markdown(read(folder, "tests.md")),
        review=markdown(read(folder, os.path.basename(rounds[-1]))) if rounds else "")


# ---------------------------------------------------------------- the page

def page(reviews, title, fragment):
    data = json.dumps(dict(reviews=reviews), separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    body = TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)
    if fragment:
        return body
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + body.replace("</style>", "</style>\n</head>\n<body>", 1) + "\n</body>\n</html>\n")


TEMPLATE = r"""<title>__TITLE__</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
/* Layout: a review desk. The summary strip first, then the graph with a reading panel beside it,
   then the plan and the evidence in the order a reviewer asks for them. */
:root {
  --bg: #f3f5f8; --surface: #ffffff; --sunk: #eaeef4; --ink: #172030; --ink-2: #3d4859; --muted: #657084;
  --line: #d6dce6; --grid: #e4e8ef;
  --new: #0f7f5f; --new-soft: #dbf0e8; --dropped: #c0392b; --dropped-soft: #f7e0dc;
  --rewritten: #c98a00; --rewritten-soft: #f6ecd2; --lead: #3b5bdb; --lead-soft: #e3e8fb;
  --kept: #56627a; --context: #98a1b2;
  --display: "IBM Plex Sans Condensed", "Arial Narrow", system-ui, sans-serif;
  --body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0e1218; --surface: #151a22; --sunk: #1b212b; --ink: #e5e9f0; --ink-2: #b6bdca; --muted: #8a93a4;
  --line: #2a313d; --grid: #20262f;
  --new: #24936f; --new-soft: #14352b; --dropped: #de4a5e; --dropped-soft: #3d1d24;
  --rewritten: #b38b12; --rewritten-soft: #352b12; --lead: #6179e8; --lead-soft: #1f2748;
  --kept: #9aa3b4; --context: #5f6878; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0e1218; --surface: #151a22; --sunk: #1b212b; --ink: #e5e9f0; --ink-2: #b6bdca; --muted: #8a93a4;
  --line: #2a313d; --grid: #20262f;
  --new: #24936f; --new-soft: #14352b; --dropped: #de4a5e; --dropped-soft: #3d1d24;
  --rewritten: #b38b12; --rewritten-soft: #352b12; --lead: #6179e8; --lead-soft: #1f2748;
  --kept: #9aa3b4; --context: #5f6878; color-scheme: dark }
* { box-sizing: border-box }
body { background: var(--bg); color: var(--ink); font: 15px/1.55 var(--body); margin: 0 }
.page { max-width: 1320px; margin: 0 auto; padding-block: 20px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px }
h1, h2, h3, h4 { font-family: var(--display); font-weight: 600; line-height: 1.2; text-wrap: balance; margin: 0 }
h1 { font-size: clamp(1.6rem, 1.1rem + 1.6vw, 2.3rem) }
h2 { font-size: 1.35rem }
h3 { font-size: 1.08rem; margin-top: 1.1em }
h4 { font-size: .98rem; margin-top: 1em }
p { margin: .5em 0 }
code, .mono { font-family: var(--mono); font-size: .88em }
:not(pre) > code { background: var(--sunk); border-radius: 4px; padding: .05em .3em; overflow-wrap: anywhere }
pre { background: var(--sunk); border-radius: 8px; padding: 12px 14px; overflow-x: auto; font-size: .82rem; line-height: 1.5 }
a { color: var(--lead) }
button { font: inherit; color: inherit }
:focus-visible { outline: 2px solid var(--lead); outline-offset: 2px }
.eyebrow { font: 500 .74rem/1.3 var(--body); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0 0 6px }
.muted { color: var(--muted) }
.prose { max-width: 70ch; min-width: 0 }
.prose ul, .prose ol { padding-left: 1.25em; margin: .4em 0 }
.prose li { margin: .25em 0 }
.prose blockquote { border-left: 3px solid var(--line); margin: .6em 0; padding: .1em 0 .1em 12px; color: var(--ink-2) }
.table-wrap { overflow-x: auto; max-width: 100% }
table { border-collapse: collapse; font-size: .88rem; width: 100% }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--grid); vertical-align: top }
th { font-weight: 600; color: var(--ink-2); font-size: .8rem; letter-spacing: .02em }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap }

.masthead { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 20px;
  border-bottom: 1px solid var(--line); padding-bottom: 14px }
.brand { font: 600 .95rem/1 var(--mono); letter-spacing: -.01em }
.brand span { color: var(--muted); font-weight: 400 }
.tabs { display: flex; flex-wrap: wrap; gap: 6px }
.tabs a { text-decoration: none; color: var(--ink-2); padding: 6px 12px; border-radius: 999px; border: 1px solid var(--line);
  font-size: .86rem; background: var(--surface) }
.tabs a[aria-current="page"] { background: var(--ink); color: var(--bg); border-color: var(--ink) }

.intro { display: grid; gap: 10px }
.refs { color: var(--muted); font-size: .88rem; display: flex; flex-wrap: wrap; gap: 4px 14px }
.chips { display: flex; flex-wrap: wrap; gap: 8px }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: .8rem;
  font-weight: 500; border: 1px solid var(--line); background: var(--surface); color: var(--ink-2) }
.chip::before { content: ""; width: 8px; height: 8px; border-radius: 50% }
.chip.good::before { background: var(--new) } .chip.bad::before { background: var(--dropped) }
.chip.wait::before { background: var(--rewritten) } .chip.plain::before { background: var(--context) }

.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; display: grid; gap: 4px; min-width: 0 }
.tile .big { font: 600 1.7rem/1.1 var(--display); font-variant-numeric: tabular-nums; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap }
.tile .big .arrow { color: var(--muted); font-weight: 400; font-size: 1.1rem }
.tile .big .was { color: var(--muted); font-weight: 500 }
.tile .note { color: var(--muted); font-size: .82rem }

.where { display: grid; gap: 6px; font-size: .92rem; color: var(--ink-2) }
.where ul { margin: 0; padding-left: 1.2em }

section.block { display: grid; gap: 14px; min-width: 0 }
.block-head { display: flex; flex-wrap: wrap; align-items: end; justify-content: space-between; gap: 10px 20px }
.block-head p { margin: 4px 0 0; max-width: 72ch; color: var(--ink-2) }

.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--surface) }
.seg button { border: 0; background: transparent; padding: 7px 12px; font-size: .86rem; cursor: pointer; color: var(--ink-2) }
.seg button + button { border-left: 1px solid var(--line) }
.seg button[aria-pressed="true"] { background: var(--ink); color: var(--bg) }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: .8rem; color: var(--ink-2) }
.legend span { display: inline-flex; align-items: center; gap: 6px }
.sw { width: 14px; height: 14px; border-radius: 3px; border: 2px solid; background: var(--surface) }
.ln { width: 22px; height: 0; border-top: 2px solid }

.graph-grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; align-items: start }
@media (max-width: 1040px) { .graph-grid { grid-template-columns: minmax(0, 1fr) } }
.graph-scroll { overflow: auto; max-height: 78vh; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; min-width: 0 }
svg.graph { display: block }
.colbg { fill: var(--sunk) }
.colbg.elsewhere { fill: none; stroke: var(--line); stroke-dasharray: 4 4 }
.colhead { font: 500 11.5px var(--mono); fill: var(--muted) }
.node { cursor: pointer }
.node .box { fill: var(--surface); stroke: var(--line); stroke-width: 1.2 }
.node .stripe { fill: var(--kept) }
.node .q { font: 500 12.5px var(--mono); fill: var(--ink) }
.node .m { font: 11px var(--body); fill: var(--muted) }
.node.s-new .stripe { fill: var(--new) } .node.s-new .box { stroke: var(--new) }
.node.s-dropped .stripe { fill: var(--dropped) } .node.s-dropped .box { stroke: var(--dropped); stroke-dasharray: 5 3 }
.node.s-rewritten .stripe, .node.s-restored .stripe { fill: var(--rewritten) }
.node.s-rewritten .box, .node.s-restored .box { stroke: var(--rewritten) }
.node.s-context .stripe, .node.s-unchanged .stripe { fill: var(--context) }
.node.s-context .q, .node.s-unchanged .q { fill: var(--ink-2) }
.node.s-lead .stripe { fill: var(--lead) } .node.s-lead .box { stroke: var(--lead); stroke-dasharray: 2 3 }
.node.selected .box { stroke-width: 2.6; fill: var(--sunk) }
.node, .edge { transition: opacity .25s ease }
.node.ghost { opacity: .16 }
.edge { fill: none; stroke-width: 1.6; stroke: var(--context) }
.edge.plain { stroke: var(--kept); opacity: .55 }
.edge.kept { stroke: var(--kept); opacity: .5 }
.edge.dropped { stroke: var(--dropped); stroke-dasharray: 6 4 }
.edge.new { stroke: var(--new); stroke-width: 2 }
.edge.similar { stroke: var(--lead); stroke-dasharray: 1.5 4; stroke-linecap: round; stroke-width: 2 }
.edge.test { opacity: .35 }
.edge.hidden { opacity: 0 }
.edge.dim { opacity: .08 }
.edge.hl { opacity: 1; stroke-width: 2.6 }
.head-plain, .head-kept { fill: var(--kept) } .head-dropped { fill: var(--dropped) }
.head-new { fill: var(--new) } .head-similar { fill: var(--lead) }
@media (prefers-reduced-motion: reduce) { .node, .edge { transition: none } }

.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; display: grid; gap: 12px;
  position: sticky; top: calc(env(safe-area-inset-top, 0px) + 12px); max-height: 78vh; overflow: auto; min-width: 0 }
.panel h3 { margin: 0; font: 600 1.05rem/1.3 var(--mono); overflow-wrap: anywhere }
.panel .path { color: var(--muted); font-size: .82rem; overflow-wrap: anywhere; margin: 2px 0 0 }
.facts { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; font-size: .88rem }
.facts dt { color: var(--muted) } .facts dd { margin: 0; font-variant-numeric: tabular-nums; min-width: 0 }
.pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: .76rem; font-weight: 600; color: var(--ink);
  background: var(--sunk); border: 1px solid var(--line); white-space: nowrap }
.pill.new { background: var(--new-soft); border-color: var(--new) } .pill.dropped { background: var(--dropped-soft); border-color: var(--dropped) }
.pill.rewritten, .pill.restored { background: var(--rewritten-soft); border-color: var(--rewritten) }
.pill.lead { background: var(--lead-soft); border-color: var(--lead) }
.links { list-style: none; margin: 0; padding: 0; display: grid; gap: 3px; font-size: .85rem }
.links li { display: flex; gap: 6px; align-items: baseline; min-width: 0 }
.links button, .go { background: none; border: 0; padding: 0; cursor: pointer; font-family: var(--mono); font-size: .84rem;
  color: var(--lead); text-align: left; overflow-wrap: anywhere }
.mark { font-family: var(--mono); font-weight: 600; width: 1em; flex: none; text-align: center }
.mark.dropped { color: var(--dropped) } .mark.new { color: var(--new) } .mark.kept { color: var(--muted) }
.reason { border-left: 3px solid var(--line); padding: 2px 0 2px 10px; font-size: .88rem; color: var(--ink-2) }
.reason .src { display: block; font-size: .72rem; letter-spacing: .06em; text-transform: uppercase; color: var(--muted) }
.reason.review { border-color: var(--lead) }

.changes-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; min-width: 0 }
.card h3 { margin: 0 0 6px }
.card ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; font-size: .86rem }
.card li { display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap }

.patch { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px 28px; background: var(--surface);
  border: 1px solid var(--line); border-radius: 12px; padding: 18px 20px }
.patch .top { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 14px }
.patch .num { font: 600 .8rem var(--mono); color: var(--muted) }
.patch .subject { font: 600 1.05rem/1.3 var(--mono); overflow-wrap: anywhere }
.patch .stat { font-size: .82rem; color: var(--muted); font-variant-numeric: tabular-nums }
.patch .touches { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 6px }
@media (max-width: 760px) { .patch { grid-template-columns: minmax(0, 1fr) } }

.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 20px 32px }
@media (max-width: 860px) { .two { grid-template-columns: minmax(0, 1fr) } }
details { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 16px }
details > summary { cursor: pointer; font-weight: 600; font-family: var(--display) }
details[open] > summary { margin-bottom: 8px }
footer { color: var(--muted); font-size: .8rem; border-top: 1px solid var(--line); padding-top: 14px }
.reason.missing { border-color: var(--dropped); background: var(--dropped-soft); padding: 6px 10px; border-radius: 0 6px 6px 0 }
.reason.given { border-color: var(--new) }
.decide { display: inline-flex; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; background: var(--surface) }
.decide button { border: 0; background: transparent; padding: 4px 10px; font-size: .8rem; cursor: pointer; color: var(--ink-2) }
.decide button + button { border-left: 1px solid var(--line) }
.decide button[aria-pressed="true"][data-choice="adopt"] { background: var(--new-soft); color: var(--ink); font-weight: 600 }
.decide button[aria-pressed="true"][data-choice="reject"] { background: var(--dropped-soft); color: var(--ink); font-weight: 600 }
.decisions { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; display: grid; gap: 10px }
.decisions textarea { width: 100%; min-height: 7em; font: .8rem/1.45 var(--mono); background: var(--sunk); color: var(--ink);
  border: 1px solid var(--line); border-radius: 8px; padding: 10px }
.decisions .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center }
.btn { border: 1px solid var(--ink); background: var(--ink); color: var(--bg); border-radius: 8px; padding: 7px 14px; cursor: pointer; font-weight: 600 }
</style>

<div class="page">
  <header class="masthead">
    <div class="brand">review-ready <span>/ review map</span></div>
    <nav class="tabs" id="tabs" aria-label="Reviews"></nav>
  </header>
  <section class="intro" id="intro"></section>
  <section class="tiles" id="tiles" aria-label="Before and after"></section>
  <section class="where" id="where"></section>

  <section class="block" aria-labelledby="graph-title">
    <div class="block-head">
      <div>
        <h2 id="graph-title">How the code's connections change</h2>
        <p>Every function and class in the files this change touches, and the calls between them, on one layout. Switch between the branch as written and the series to watch what moves; select a box to read why.</p>
      </div>
      <div class="seg" role="group" aria-label="Graph view">
        <button type="button" id="view-before" data-view="before">Branch as written</button>
        <button type="button" id="view-after" data-view="after">Series</button>
        <button type="button" id="view-changes" data-view="changes">Changes</button>
      </div>
    </div>
    <div class="legend" id="legend"></div>
    <div class="graph-grid">
      <div class="graph-scroll"><svg class="graph" id="graph" role="img" aria-label="Call graph of the changed files"></svg></div>
      <aside class="panel" id="panel" aria-live="polite"></aside>
    </div>
  </section>

  <section class="block" aria-labelledby="links-title">
    <div class="block-head"><div><h2 id="links-title">Connections that change</h2>
      <p>Calls the series drops or adds, and the code already in the tree that the new functions resemble.</p></div></div>
    <div class="changes-list" id="links"></div>
  </section>

  <section class="block" aria-labelledby="fates-title">
    <div class="block-head"><div><h2 id="fates-title">Every function, its fate and the reason</h2>
      <p>The same information as the graph, as a table. Every change needs a reason: a sentence of the pass's own files that names it, or an entry in the plan. The builder refuses a series with a change that has none.</p></div></div>
    <div class="table-wrap" id="fates"></div>
    <div id="others"></div>
  </section>

  <section class="block" id="decisions" hidden></section>

  <section class="block" aria-labelledby="plan-title">
    <div class="block-head"><div><h2 id="plan-title">The plan, patch by patch</h2>
      <p>Each patch as it would be posted: why it exists, and how it was checked.</p></div></div>
    <div id="plan" style="display:grid;gap:12px"></div>
  </section>

  <section class="block two" id="story"></section>
  <section class="block" id="size"></section>
  <section class="block" id="evidence"></section>
  <footer id="footer"></footer>
</div>

<script type="application/json" id="review-data">__DATA__</script>
<script>
(() => {
  "use strict";
  const DATA = JSON.parse(document.getElementById("review-data").textContent);
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const FATE = {
    new: "Added by the series", rewritten: "Rewritten", restored: "Back to the target's version", dropped: "Dropped",
    kept: "Kept as written", unchanged: "Unchanged, connected", context: "Elsewhere, connected", lead: "Elsewhere, same subject",
  };
  const ORDER = { dropped: 0, new: 1, rewritten: 2, restored: 3, kept: 4, unchanged: 5 };
  const COL_W = 236, GAP = 92, NH = 44, VG = 10, TOP = 44, PX = 14;
  let R = null, view = "changes", selected = null, choice = {};
  const store = () => `review-ready:${R.slug}:${R.sha.series}`;
  function loadChoices() {
    choice = {};
    R.nodes.concat(R.others).forEach((c) => { if (c.key && c.decision) choice[c.key] = c.decision; });
    try { Object.assign(choice, JSON.parse(localStorage.getItem(store()) || "{}")); } catch (e) { /* storage is optional */ }
  }
  function saveChoices() { try { localStorage.setItem(store(), JSON.stringify(choice)); } catch (e) { /* storage is optional */ } }
  const changesList = () => {
    const seen = new Set(), out = [];
    R.nodes.filter((n) => n.change).concat(R.others).forEach((c) => { if (c.key && !seen.has(c.key)) { seen.add(c.key); out.push(c); } });
    return out;
  };
  const interactive = () => R.run === "interactive";
  function decideControl(key) {
    if (!interactive() || !key) return "";
    const now = choice[key];
    return `<span class="decide" role="group" aria-label="Decision on ${esc(key)}">
      <button type="button" data-decide="${esc(key)}" data-choice="adopt" aria-pressed="${now === "adopt"}">Adopt</button>
      <button type="button" data-decide="${esc(key)}" data-choice="reject" aria-pressed="${now === "reject"}">Reject</button></span>`;
  }
  function reasonBox(c) {
    if (!c.change && c.reason === undefined) return "";
    return c.reason ? `<div class="reason given"><span class="src">reason · ${esc(c.reason.source)}</span>${inlineCode(c.reason.text)}</div>`
      : `<div class="reason missing"><span class="src">no reason</span>No sentence names this change and the plan gives no reason. The builder will not land the series until one is written.</div>`;
  }
  function inlineCode(t) { return esc(t).replace(/`([^`]+)`/g, "<code>$1</code>"); }

  const name = (n) => n.qual ? n.qual : n.path.split("/").pop();
  const where = (n) => n.qual ? `${n.path}:${n.qual}` : n.path;
  const trunc = (s, k) => s.length > k ? s.slice(0, k - 1) + "…" : s;
  const truncStart = (s, k) => s.length > k ? "…" + s.slice(s.length - k + 1) : s;
  const span = (side) => side ? side.lines : 0;
  const shown = (o) => view === "before" ? o.before : view === "after" ? o.after : true;
  const edgeState = (e) => e.kind === "similar" ? "similar" : e.before && e.after ? "kept" : e.before ? "dropped" : "new";

  function pick() {
    const want = location.hash.slice(1);
    R = DATA.reviews.find((r) => r.slug === want) || DATA.reviews[0];
    selected = R.start;
    loadChoices();
    render();
  }

  function render() {
    $("tabs").innerHTML = DATA.reviews.length < 2 ? "" : DATA.reviews.map((r) =>
      `<a href="#${r.slug}" ${r === R ? 'aria-current="page"' : ""}>${esc(r.branch.split("/").pop())}</a>`).join("");
    renderIntro(); renderTiles(); renderWhere(); renderLegend(); renderGraph(); renderPanel();
    renderLinks(); renderFates(); renderDecisions(); renderPlan(); renderStory(); renderSize(); renderEvidence(); renderFooter();
  }

  function renderIntro() {
    const s = R.stats, last = R.verdicts[R.verdicts.length - 1];
    const missing = s.changes - s.reasoned;
    const reasons = missing ? `<span class="chip bad">${missing} of ${s.changes} changes have no reason</span>`
      : `<span class="chip good">All ${s.changes} changes have a reason</span>`;
    const decided = changesList().filter((c) => choice[c.key]).length;
    const run = interactive() ? `<span class="chip ${decided === s.changes ? "good" : "wait"}">Interactive run: ${decided} of ${s.changes} decided</span>`
      : `<span class="chip plain">Automatic run</span>`;
    const gate = s.gate === true ? `<span class="chip good">Size gate passed</span>` : s.gate === false
      ? `<span class="chip bad">Size gate not met</span>` : "";
    const review = !last ? `<span class="chip plain">Not reviewed yet</span>`
      : `<span class="chip ${last.verdict === "ACK" ? "good" : "bad"}">Last review: ${esc(last.verdict)} (${R.verdicts.length} round${R.verdicts.length > 1 ? "s" : ""})</span>`
        + (last.answered ? `<span class="chip wait">Changes since that review are not reviewed</span>` : "")
        + (last && last.same ? `<span class="chip wait">Reviewed by the agent that wrote it: a second reader is owed</span>` : "");
    $("intro").innerHTML = `<p class="eyebrow">Pre-PR pass on <code>${esc(R.branch)}</code></p>
      <h1>${esc(R.title)}</h1>
      <div class="refs"><span>branch as written <code>${esc(R.sha.original.slice(0, 10))}</code></span>
        <span>forked at <code>${esc(R.sha.base.slice(0, 10))}</code></span>
        <span>series <code>${esc(R.sha.series.slice(0, 10))}</code> on <code>${esc(R.onto)}</code> at <code>${esc(R.sha.landed.slice(0, 10))}</code></span></div>
      <div class="chips">${gate}${reasons}${review}${run}<span class="chip plain">${R.patches.length} patch${R.patches.length > 1 ? "es" : ""}</span></div>`;
  }

  function tile(label, pair, note) {
    const [b, a] = pair;
    return `<div class="tile"><p class="eyebrow">${label}</p>
      <div class="big"><span class="was">${b.toLocaleString()}</span><span class="arrow" aria-hidden="true">→</span><span>${a.toLocaleString()}</span></div>
      <div class="note">${note}</div></div>`;
  }

  function renderTiles() {
    const s = R.stats, f = s.fates;
    const pct = s.lines[0] ? Math.round(100 * s.lines[1] / s.lines[0]) : 0;
    const fates = [["dropped", "dropped"], ["new", "added"], ["rewritten", "rewritten"], ["restored", "restored"], ["kept", "kept"]]
      .filter(([k]) => f[k]).map(([k, w]) => `${f[k]} ${w}`).join(", ") || "none changed";
    $("tiles").innerHTML =
      tile("Lines to review", s.lines, `non-blank lines changed; the series keeps ${pct}%`) +
      tile("Functions and classes", s.functions, fates) +
      tile("Calls between them", s.calls, "calls inside the changed files") +
      tile("Test functions", s.tests, s.tests[1] > s.tests[0] ? "more tests than the branch had" : s.tests[1] < s.tests[0] ? "fewer tests than the branch had" : "as many as the branch had");
  }

  function renderWhere() {
    const t = R.stats.tree;
    const rows = R.where.map((w) => {
      const by = w.imported_by.length ? `imported by ${w.imported_by.map((p) => `<code>${esc(p)}</code>`).join(", ")}` : "nothing in the tree imports it";
      const uses = w.imports.length ? `; uses ${w.imports.map((p) => `<code>${esc(p)}</code>`).join(", ")}` : "";
      return `<li><code>${esc(w.path)}</code>: ${by}${uses}${w.runs ? "; runs as a script" : ""}</li>`;
    }).join("");
    $("where").innerHTML = `<p><strong>Where it lands.</strong> The tree the series lands on has ${t.modules.toLocaleString()} modules,
      ${t.definitions.toLocaleString()} functions and classes, and ${t.calls.toLocaleString()} calls between them.</p>${rows ? `<ul>${rows}</ul>` : ""}`;
  }

  function renderLegend() {
    const sw = (c, dash, label) => `<span><i class="sw" style="border-color:var(--${c});${dash ? "border-style:dashed" : ""}"></i>${label}</span>`;
    const ln = (c, style, label) => `<span><i class="ln" style="border-top:2px ${style} var(--${c})"></i>${label}</span>`;
    $("legend").innerHTML = sw("kept", 0, "kept as written") + sw("rewritten", 0, "rewritten") + sw("dropped", 1, "dropped")
      + sw("new", 0, "added by the series") + sw("context", 0, "elsewhere, connected") + sw("lead", 1, "elsewhere, same subject")
      + ln("kept", "solid", "call") + ln("dropped", "dashed", "call dropped") + ln("new", "solid", "call added")
      + ln("lead", "dotted", "same subject");
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
  }

  function layout() {
    const cols = R.columns.map((c, i) => ({ ...c, x: PX + i * (COL_W + GAP), n: 0 }));
    const pos = [];
    for (const n of R.nodes) { const c = cols[n.col]; pos[n.id] = { x: c.x, y: TOP + c.n * (NH + VG) }; c.n += 1; }
    const rows = Math.max(1, ...cols.map((c) => c.n));
    return { cols, pos, width: PX * 2 + cols.length * COL_W + Math.max(0, cols.length - 1) * GAP + 70, height: TOP + rows * (NH + VG) + 16 };
  }

  function curve(a, b) {
    const ay = a.y + NH / 2, by = b.y + NH / 2;
    if (a.x === b.x) {
      const x = a.x + COL_W, bend = 26 + Math.min(64, Math.abs(by - ay) * 0.16);
      return `M${x} ${ay} C${x + bend} ${ay} ${x + bend} ${by} ${x + 7} ${by}`;
    }
    const [x1, x2] = a.x < b.x ? [a.x + COL_W, b.x - 7] : [a.x, b.x + COL_W + 7];
    const m = (x1 + x2) / 2;
    return `M${x1} ${ay} C${m} ${ay} ${m} ${by} ${x2} ${by}`;
  }

  function renderGraph() {
    const { cols, pos, width, height } = layout();
    const svg = $("graph");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    const touching = new Set();
    if (selected != null) R.edges.forEach((e) => { if (e.src === selected || e.dst === selected) { touching.add(e.src); touching.add(e.dst); } });
    const heads = ["plain", "kept", "dropped", "new", "similar"].map((k) =>
      `<marker id="head-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path class="head-${k}" d="M0 0 L10 5 L0 10 z"/></marker>`).join("");
    const colsSvg = cols.map((c) => `<rect class="colbg ${c.kind === "elsewhere" ? "elsewhere" : ""}" x="${c.x - 8}" y="30" width="${COL_W + 16}" height="${height - 38}" rx="10"/>
      <text class="colhead" x="${c.x}" y="20">${esc(truncStart(c.label, 33))}${c.kind === "test" ? " · tests" : ""}</text>`).join("");
    const edgesSvg = R.edges.map((e, i) => {
      const state = edgeState(e), visible = shown(e) && shown(R.nodes[e.src]) && shown(R.nodes[e.dst]);
      const style = view === "changes" || state === "similar" ? state : "plain";
      const test = R.nodes[e.src].test ? " test" : "";
      const focus = selected == null ? "" : (e.src === selected || e.dst === selected) ? " hl" : " dim";
      return `<path class="edge ${style}${test}${visible ? focus : " hidden"}" d="${curve(pos[e.src], pos[e.dst])}" marker-end="url(#head-${style})" data-edge="${i}"/>`;
    }).join("");
    const nodesSvg = R.nodes.map((n) => {
      const p = pos[n.id], here = shown(n);
      const meta = n.role !== "focus" ? truncStart(n.path, 34)
        : n.before && n.after ? (span(n.before) === span(n.after) ? `${span(n.after)} lines` : `${span(n.before)} → ${span(n.after)} lines`)
        : n.before ? `${span(n.before)} lines, dropped` : `${span(n.after)} lines, new`;
      const quiet = selected != null && touching.size && !touching.has(n.id) && n.id !== selected ? " ghost" : "";
      return `<g class="node s-${n.status}${here ? quiet : " ghost"}${n.id === selected ? " selected" : ""}" data-id="${n.id}"
          transform="translate(${p.x},${p.y})" tabindex="0" role="button" aria-label="${esc(where(n))}, ${esc(FATE[n.status])}">
        <title>${esc(where(n))}: ${esc(FATE[n.status])}</title>
        <rect class="box" width="${COL_W}" height="${NH}" rx="7"/><rect class="stripe" x="0" y="0" width="5" height="${NH}" rx="2"/>
        <text class="q" x="14" y="18">${esc(trunc(n.role === "focus" ? n.label : name(n), 29))}</text>
        <text class="m" x="14" y="34">${esc(meta)}</text></g>`;
    }).join("");
    svg.innerHTML = `<defs>${heads}</defs>${colsSvg}<g>${edgesSvg}</g><g>${nodesSvg}</g>`;
  }

  function linkList(items) {
    if (!items.length) return `<p class="muted" style="margin:0">none</p>`;
    return `<ul class="links">${items.map(([state, n]) => `<li><span class="mark ${state}" aria-hidden="true">${state === "dropped" ? "−" : state === "new" ? "+" : "·"}</span>
      <button type="button" class="go" data-go="${n.id}">${esc(trunc(where(n), 64))}</button>${state === "kept" ? "" : `<span class="muted">${state === "dropped" ? "dropped" : "added"}</span>`}</li>`).join("")}</ul>`;
  }

  function renderPanel() {
    const n = R.nodes[selected];
    if (!n) { $("panel").innerHTML = `<p class="muted">Select a box in the graph to see what happens to it and why.</p>`; return; }
    const calls = R.edges.filter((e) => e.kind === "calls" && e.src === n.id).map((e) => [edgeState(e), R.nodes[e.dst]]);
    const callers = R.edges.filter((e) => e.kind === "calls" && e.dst === n.id).map((e) => [edgeState(e), R.nodes[e.src]]);
    const leads = R.edges.filter((e) => e.kind === "similar" && (e.src === n.id || e.dst === n.id));
    const lines = (side) => side ? `${side.lines} lines, line ${side.line}` : "not there";
    const patch = n.patch != null ? `<button type="button" class="go" data-patch="${n.patch}">${n.patch + 1}/${R.patches.length}: ${esc(R.patches[n.patch].subject)}</button>` : "none";
    const reasons = n.notes.map(([src, text]) => `<div class="reason"><span class="src">${esc(src)}</span>${text}</div>`).join("");
    const review = n.review.map(([src, text]) => `<div class="reason review"><span class="src">reviewer, ${esc(src)}</span>${text}</div>`).join("");
    const similar = leads.map((e) => {
      const other = R.nodes[e.src === n.id ? e.dst : e.src];
      return `<div class="reason review"><span class="src">${e.src === n.id ? "the tree already has" : "resembles"} · ${Math.round(100 * e.relevance)}% · shares ${esc(e.shared.join(", "))}</span>
        <button type="button" class="go" data-go="${other.id}">${esc(where(other))}</button></div>`;
    }).join("");
    $("panel").innerHTML = `<div><p class="eyebrow"><span class="pill ${n.status}">${esc(FATE[n.status])}</span></p>
        <h3>${esc(n.role === "focus" ? n.label : name(n))}</h3><p class="path">${esc(n.path)}</p></div>
      ${n.change ? reasonBox(n) : ""}${n.change && interactive() ? `<div>${decideControl(n.key)}</div>` : ""}
      <dl class="facts"><dt>Branch</dt><dd>${lines(n.before)}</dd><dt>Series</dt><dd>${lines(n.after)}</dd>
        ${n.role === "focus" ? `<dt>Patch</dt><dd>${patch}</dd>` : ""}</dl>
      ${reasons || review || similar || n.change ? "" : n.role === "focus" ? `<p class="muted" style="margin:0">Not changed by the series.</p>` : ""}
      ${reasons}${review}${similar}
      <div><p class="eyebrow">Called by</p>${linkList(callers)}</div>
      <div><p class="eyebrow">Calls</p>${linkList(calls)}</div>`;
  }

  function renderLinks() {
    const calls = R.edges.filter((e) => e.kind === "calls" && e.before !== e.after);
    const card = (title, list, empty) => `<div class="card"><h3>${title}</h3>${list.length ? `<ul>${list.join("")}</ul>` : `<p class="muted" style="margin:0">${empty}</p>`}</div>`;
    const row = (e, mark, cls) => `<li><span class="mark ${cls}" aria-hidden="true">${mark}</span><button type="button" class="go" data-go="${e.src}">${esc(name(R.nodes[e.src]))}</button>
      <span class="muted">calls</span><button type="button" class="go" data-go="${e.dst}">${esc(name(R.nodes[e.dst]))}</button></li>`;
    const similar = R.edges.filter((e) => e.kind === "similar").map((e) => `<li><button type="button" class="go" data-go="${e.src}">${esc(name(R.nodes[e.src]))}</button>
      <span class="muted">resembles</span><button type="button" class="go" data-go="${e.dst}">${esc(where(R.nodes[e.dst]))}</button>
      <span class="muted">${Math.round(100 * e.relevance)}%, shares ${esc(e.shared.join(", "))}</span></li>`);
    const split = (list, mark, cls) => {
      const code = list.filter((e) => !R.nodes[e.src].test), tests = list.length - code.length;
      return code.map((e) => row(e, mark, cls)).concat(tests ? [`<li class="muted">and ${tests} call${tests > 1 ? "s" : ""} from tests</li>`] : []);
    };
    $("links").innerHTML = card("Calls the series drops", split(calls.filter((e) => e.before), "−", "dropped"), "none")
      + card("Calls the series adds", split(calls.filter((e) => e.after), "+", "new"), "none")
      + card("Already in the tree, same subject", similar, "Nothing in the tree shares a subject with the new code.");
  }

  function renderFates() {
    const rows = R.nodes.filter((n) => n.role === "focus" && n.kind !== "module" && n.status !== "unchanged")
      .sort((a, b) => (a.test - b.test) || (ORDER[a.status] - ORDER[b.status]) || a.id - b.id);
    $("fates").innerHTML = `<table><thead><tr><th>Function</th><th>Fate</th><th class="num">Branch</th><th class="num">Series</th><th>Patch</th><th>Reason</th>${interactive() ? "<th>Decision</th>" : ""}</tr></thead><tbody>
      ${rows.map((n) => `<tr><td><button type="button" class="go" data-go="${n.id}">${esc(n.label)}</button><div class="muted" style="font-size:.78rem">${esc(n.path)}</div></td>
        <td><span class="pill ${n.status}">${esc(FATE[n.status])}</span></td>
        <td class="num">${n.before ? n.before.lines : "–"}</td><td class="num">${n.after ? n.after.lines : "–"}</td>
        <td class="num">${n.patch != null ? `${n.patch + 1}/${R.patches.length}` : "–"}</td>
        <td>${n.reason ? `${inlineCode(n.reason.text)}<div class="muted" style="font-size:.76rem">${esc(n.reason.source)}</div>`
          : n.change ? '<span class="pill dropped">No reason</span>' : '<span class="muted">not a change</span>'}</td>
        ${interactive() ? `<td>${n.change ? decideControl(n.key) : ""}</td>` : ""}</tr>`).join("")}</tbody></table>`;
    $("others").innerHTML = !R.others.length ? "" : `<h3>Changes outside functions</h3><div class="table-wrap"><table><thead><tr><th>Change</th><th>Kind</th><th>Fate</th><th>Reason</th>${interactive() ? "<th>Decision</th>" : ""}</tr></thead><tbody>
      ${R.others.map((c) => `<tr><td><code>${esc(c.key)}</code></td><td>${esc(c.kind)}</td><td><span class="pill ${c.fate === "added" ? "new" : c.fate}">${esc(c.fate)}</span></td>
        <td>${c.reason ? `${inlineCode(c.reason.text)}<div class="muted" style="font-size:.76rem">${esc(c.reason.source)}</div>` : '<span class="pill dropped">No reason</span>'}</td>
        ${interactive() ? `<td>${decideControl(c.key)}</td>` : ""}</tr>`).join("")}</tbody></table></div>`;
  }

  function renderDecisions() {
    const box = $("decisions");
    box.hidden = !interactive();
    if (!interactive()) { box.innerHTML = ""; return; }
    const all = changesList(), decided = all.filter((c) => choice[c.key]);
    const json = JSON.stringify(Object.fromEntries(all.filter((c) => choice[c.key]).map((c) => [c.key, choice[c.key]])), null, 1);
    box.innerHTML = `<div class="decisions"><div class="block-head"><div><h2>Your decisions</h2>
        <p>Adopt or reject each change above. When you are done, copy the decisions and paste them back in the conversation: they become
        <code>decisions.json</code>, a rejected change is put back the way your branch had it, and nothing lands until every change is adopted.</p></div></div>
      <div class="row"><strong>${decided.length} of ${all.length} decided</strong>
        <span class="muted">${decided.filter((c) => choice[c.key] === "adopt").length} adopted, ${decided.filter((c) => choice[c.key] === "reject").length} rejected</span>
        <button type="button" class="btn" id="copy-decisions">Copy decisions</button><span id="copied" class="muted" aria-live="polite"></span></div>
      <label class="muted" for="decisions-text" style="font-size:.8rem">decisions.json</label>
      <textarea id="decisions-text" readonly>${esc(json)}</textarea></div>`;
  }

  function renderPlan() {
    $("plan").innerHTML = R.patches.map((p, i) => {
      const touches = R.nodes.filter((n) => n.patch === i && n.role === "focus" && n.kind !== "module" && !n.test);
      return `<article class="patch" id="patch-${i}"><div class="top"><span class="num">${i + 1}/${R.patches.length} · ${esc(p.sha)}</span>
          <span class="subject">${esc(p.subject)}</span><span class="stat">+${p.added} −${p.deleted} in ${p.files.length} file${p.files.length > 1 ? "s" : ""}</span></div>
        <div class="prose"><p class="eyebrow">Why</p>${p.why || '<p class="muted">The message has no body.</p>'}</div>
        <div class="prose"><p class="eyebrow">How it was checked</p>${p.checked || '<p class="muted">The message does not say.</p>'}</div>
        ${touches.length ? `<div class="touches">${touches.map((n) => `<button type="button" class="pill ${n.status} go" data-go="${n.id}" style="color:var(--ink)">${esc(trunc(n.label, 40))}</button>`).join("")}</div>` : ""}</article>`;
    }).join("");
  }

  function renderStory() {
    $("story").innerHTML = `<div class="prose"><h2>The problem</h2>${R.problem || '<p class="muted">No problem.md in the work directory.</p>'}</div>
      <div class="prose"><h2>What is left out, and what changes on purpose</h2>${R.deferred || '<p class="muted">No deferred.md in the work directory.</p>'}</div>`;
  }

  function renderSize() {
    const rows = R.size.map((r) => `<tr><td><code>${esc(r.path)}</code>${r.exempt ? `<div class="muted" style="font-size:.8rem">kept: ${esc(r.exempt)}</div>` : ""}</td>
      <td class="num">${r.before}</td><td class="num">${r.after}</td><td class="num">${r.before ? Math.round(100 * r.after / r.before) + "%" : "new"}</td></tr>`).join("");
    const [b, a] = R.stats.lines;
    $("size").innerHTML = `<div class="block-head"><div><h2>Size, file by file</h2><p>Non-blank lines changed, whitespace left out: the size gate's own count.
      The target is half of the branch; a file kept above half carries its reason.</p></div></div>
      <div class="table-wrap"><table><thead><tr><th>File</th><th class="num">Branch</th><th class="num">Series</th><th class="num">Kept</th></tr></thead>
      <tbody>${rows}</tbody><tfoot><tr><th>All files</th><th class="num">${b}</th><th class="num">${a}</th><th class="num">${b ? Math.round(100 * a / b) + "%" : ""}</th></tr></tfoot></table></div>`;
  }

  function renderEvidence() {
    const rounds = R.verdicts.map((v) => `<li><code>${esc(v.name)}</code>: <span class="pill ${v.verdict === "ACK" ? "new" : "dropped"}">${esc(v.verdict)}</span>
      ${v.answered ? '<span class="muted">answered in the next version</span>' : ""}</li>`).join("");
    $("evidence").innerHTML = `<div class="block-head"><div><h2>The PR description and the reviews</h2>
        <p>What a maintainer receives, and what the independent reviews said about it.</p></div></div>
      <details open><summary>PR description</summary><div class="prose">${R.letter || '<p class="muted">No cover-letter.md yet.</p>'}</div></details>
      <details><summary>Review rounds (${R.verdicts.length})</summary>${rounds ? `<ul>${rounds}</ul>` : '<p class="muted">No review yet.</p>'}
        <div class="prose">${R.review}</div></details>
      ${R.tests ? `<details ${/FAILED/.test(R.tests) ? "open" : ""}><summary>Tests: the branch's own, and the ones that must pass</summary><div class="prose">${R.tests}</div></details>` : ""}
      ${R.defence ? `<details><summary>Defence: the questions a maintainer will ask</summary><div class="prose">${R.defence}</div></details>` : ""}`;
  }

  function renderFooter() {
    $("footer").innerHTML = `Built by <code>review_site.py</code> from the branch as written (<code>${esc(R.sha.original.slice(0, 12))}</code>) and the series
      (<code>${esc(R.sha.series.slice(0, 12))}</code>), read from git. Nothing in the working tree was read. The same-subject links come from
      the words two definitions share: open both before acting on one.`;
  }

  function select(id) {
    selected = Number(id);
    renderGraph(); renderPanel();
  }

  document.addEventListener("click", (ev) => {
    const go = ev.target.closest("[data-go]"), node = ev.target.closest(".node"), viewBtn = ev.target.closest("[data-view]"), patch = ev.target.closest("[data-patch]");
    const decide = ev.target.closest("[data-decide]"), copy = ev.target.closest("#copy-decisions");
    if (decide) {
      const key = decide.dataset.decide;
      choice[key] = choice[key] === decide.dataset.choice ? undefined : decide.dataset.choice;
      if (!choice[key]) delete choice[key];
      saveChoices(); renderIntro(); renderPanel(); renderFates(); renderDecisions();
      return;
    }
    if (copy) {
      const text = $("decisions-text");
      const done = (msg) => { $("copied").textContent = msg; };
      navigator.clipboard?.writeText(text.value).then(() => done("Copied"), () => { text.select(); done("Select the text below and copy it"); })
        ?? (text.select(), done("Select the text below and copy it"));
      return;
    }
    if (viewBtn) { view = viewBtn.dataset.view; renderLegend(); renderGraph(); return; }
    if (patch) { document.getElementById(`patch-${patch.dataset.patch}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    if (node) { select(node.dataset.id); return; }
    if (go) {
      select(go.dataset.go);
      if (!go.closest("#panel")) $("graph-title").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  document.addEventListener("keydown", (ev) => {
    const node = ev.target.closest && ev.target.closest(".node");
    if (node && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); select(node.dataset.id); document.querySelector(`.node[data-id="${node.dataset.id}"]`)?.focus(); }
  });
  window.addEventListener("hashchange", pick);
  pick();
})();
</script>"""


# ---------------------------------------------------------------- self-test

class Balance(HTMLParser):
    VOID = {"meta", "link", "br", "img", "input", "hr", "rect", "path", "source"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'}")
        else:
            self.stack.pop()


SELF_TEST_BASE = {"pkg/__init__.py": "", "pkg/core.py": "def load(path):\n    return open(path).read()\n"}
SELF_TEST_BRANCH = {
    "pkg/core.py": "def load(path):\n    return _strip(open(path).read())\n\n\ndef _strip(text):\n    return text.strip()\n\n\n"
                   "def count_rows(path):\n    text = load(path)\n    return len(text.splitlines())\n",
    "tests/test_core.py": "from pkg.core import count_rows\n\n\ndef test_count(tmp_path):\n    (tmp_path / 'a').write_text('x\\ny')\n"
                          "    assert count_rows(tmp_path / 'a') == 2\n",
}
SELF_TEST_SERIES = {
    "pkg/core.py": "def load(path):\n    return open(path).read().strip()\n\n\ndef count_rows(path):\n    return len(load(path).splitlines())\n",
    "tests/test_core.py": SELF_TEST_BRANCH["tests/test_core.py"],
}


def self_test():
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="T",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    with tempfile.TemporaryDirectory() as repo:
        def g(*args):
            return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=env).stdout.strip()

        def write(files):
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
                with open(os.path.join(repo, rel), "w") as fh:
                    fh.write(text)

        g("init", "-q", "-b", "main")
        write(SELF_TEST_BASE); g("add", "-A"); g("commit", "-qm", "base")
        base = g("rev-parse", "HEAD")
        g("checkout", "-qb", "feature"); write(SELF_TEST_BRANCH); g("add", "-A"); g("commit", "-qm", "count rows")
        g("checkout", "-q", "--detach", base); write(SELF_TEST_SERIES); g("add", "-A")
        g("commit", "-qm", "core: count the rows of a file\n\nload() strips the text itself.\n\nTested: tests/test_core.py.")
        g("update-ref", "refs/review-ready/series/feature", g("rev-parse", "HEAD")); g("checkout", "-q", "main")
        write({"pkg/core.py": "def checkout_only():\n    return 0\n"})          # uncommitted: must not be read
        w = os.path.join(repo, ".review-ready", "feature")
        os.makedirs(os.path.join(w, "msgs"))
        files = {"plan.json": json.dumps(dict(repo="../..", base=base, onto="main", source="feature", run="interactive",
                                              patches=[dict(name="count")])),
                 "decisions.json": json.dumps({"pkg/core.py:_strip": "adopt"}),
                 "deferred.md": "# Deferred\n\n## noise\n- `_strip()`: one caller; inlined into `load()`.\n",
                 "problem.md": "# Problem\n\nNothing counts the rows of a file.\n",
                 "cover-letter.md": "[PATCH 0/1] core: count the rows of a file\n\nProblem.\n\nSeries:\n  1/1 core: count rows (+4/-1)\n",
                 "size-gate.json": json.dumps(dict(passed=True)),
                 "review-v1.md": "# Review\n\n## Verdict: ACK\n\n`_strip()` was rightly inlined.\n"}
        for name, text in files.items():
            with open(os.path.join(w, name), "w") as fh:
                fh.write(text)
        out = os.path.join(repo, "review.html")
        subprocess.run([sys.executable, os.path.abspath(__file__), w, "--out", out], check=True, capture_output=True)
        text = open(out).read()
        data = json.loads(re.search(r'<script type="application/json" id="review-data">(.*?)</script>', text, re.S)
                          .group(1).replace("<\\/", "</"))["reviews"][0]
        nodes = {n["qual"]: n for n in data["nodes"]}
        edge = lambda a, b: next((e for e in data["edges"] if data["nodes"][e["src"]]["qual"] == a      # noqa: E731
                                  and data["nodes"][e["dst"]]["qual"] == b), None)
        checker = Balance()
        checker.feed(text)
        fragment = subprocess.run([sys.executable, os.path.abspath(__file__), w, "--fragment", "--out", "-"],
                                  check=True, capture_output=True, text=True).stdout
        checks = [
            ("a helper the series inlines is dropped", nodes.get("_strip", {}).get("status") == "dropped"),
            ("its reason is the sentence of deferred.md that names it",
             any(src == "deferred.md" and "inlined" in sentence for src, sentence in nodes["_strip"]["notes"])),
            ("the reviewer's sentence about it is attached apart", bool(nodes["_strip"]["review"])),
            ("a rewritten function knows its patch", nodes["load"]["status"] == "rewritten" and nodes["load"]["patch"] == 0),
            ("a function both sides have, written the same way, is kept",
             nodes["count_rows"]["status"] in ("kept", "rewritten") and nodes["count_rows"]["before"] and nodes["count_rows"]["after"]),
            ("a call the series removes exists on the branch side only",
             (lambda e: bool(e) and e["before"] and not e["after"])(edge("load", "_strip"))),
            ("a test's call to the code is drawn", bool(edge("test_count", "count_rows"))),
            ("the size comes from git, the gate's count", data["stats"]["lines"][0] > data["stats"]["lines"][1] > 0),
            ("the last review's verdict is read", data["verdicts"][-1]["verdict"] == "ACK"),
            ("each change carries the reason the gate found", (nodes["_strip"].get("reason") or {}).get("source") == "deferred.md"),
            ("a change nothing explains shows as missing", nodes["count_rows"]["change"] and nodes["count_rows"]["reason"] is None
             and data["stats"]["reasoned"] < data["stats"]["changes"]),
            ("an interactive run shows the owner's recorded decisions",
             data["run"] == "interactive" and nodes["_strip"]["decision"] == "adopt" and nodes["load"]["decision"] is None),
            ("the checkout is never read", "checkout_only" not in text),
            ("every tag is closed", not checker.errors and not checker.stack),
            ("nothing in the data can end the script early", "</script" not in text.split('id="review-data">', 1)[1].split("</script>", 1)[0]),
            ("--fragment leaves the document wrapper to the host", "<html" not in fragment and "<body" not in fragment
             and fragment.startswith("<title>")),
            ("the page loads no script from anywhere", not re.search(r"<script[^>]+src=", text)),
        ]
    failed = sum(not ok for _name, ok in checks)
    for name, ok in checks:
        print(("ok   " if ok else "FAIL ") + name)
    print(f"{len(checks)} cases, {failed} failing")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workdirs", nargs="*")
    ap.add_argument("--out", default="review.html", help="the page to write; - for standard output")
    ap.add_argument("--title", default="Review map")
    ap.add_argument("--fragment", action="store_true", help="no <!doctype>, <html>, <head> or <body>")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    if not args.workdirs:
        ap.error("give at least one work directory")
    taken = set()
    text = page([load_review(w, taken) for w in args.workdirs], args.title, args.fragment)
    if args.out == "-":
        sys.stdout.write(text)
        return
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {args.out} ({len(text) // 1024} KB, {len(args.workdirs)} review(s))")


if __name__ == "__main__":
    main()
