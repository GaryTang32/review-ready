# Audit: <repo> at <short sha>

Read <N> of <M> Python modules, <L> lines. Map: `repo-map.md`.
Tests at the base: <passing>/<total>. Test command: `<cmd>`.

## What this system does

<Three sentences, derived from the entry points and the call graph, not from
the README. An outsider should be able to stop reading here and know what the
thing is for.>

## How it is wired

<The layers, bottom to top, one clause each. Every import cycle, and which way
you propose the dependency should point. One path traced end to end, from the
entry point to storage, naming each function it passes through.>

Entry points:
- `<file:function>` — <what starts it: CLI, cron, console script, import>

## Architecture

**What an outsider cannot tell today.**
- <finding, with the evidence: "29 modules in one directory covering 6 roles">

**Target tree.**

```
<the proposed layout, every module assigned>
```

| destination | why it exists | modules |
|---|---|---|
| `<dir>/` | <one line: the job this directory has> | <files> |

**Move plan.** <N> rename-only patches, in this order, each `No functional
change.`: <group, group, group>. Import lines change with the move because
nothing would build otherwise; nothing else changes in those patches.

## Findings

| # | severity | file:line | finding | fix | patch |
|---|---|---|---|---|---|
| 1 | correctness | | | | 3/9 |
| 2 | safety | | | | deferred |
| 3 | structure | | | | 5/9 |
| 4 | clarity | | | | 1/9 |

Severity: **correctness** (it computes the wrong answer), **safety** (it hides
an error or a behaviour switch), **structure** (the shape costs every future
reader), **clarity** (a name or a comment misleads). Ordered by churn times
fan-in, so the top of the table is where a defect costs most.

## Not pinned

Hot functions no test reaches. Each must be characterized in its own patch
before anything touches it.

| file:function | callers | what a characterization test must capture |
|---|---:|---|

## Plan

| patch | stage | what | budget |
|---|---|---|---:|
| 1/N | docstrings | | |
| 2/N | deletion | | |
| 3/N | characterization | | |
| 4/N | move | | |
| 5/N | split | | |
| 6/N | refactor | | |

Stages run in the order repo-audit.md section 5 gives. <N> patches is what is
being offered for review now; the rest is deferred below.

## Deferred

| finding | why not now | what it needs first |
|---|---|---|

## Limits of this audit

- Unresolved calls: <n>. The dead and untested lists are conservative by that
  much.
- Dynamic dispatch not visible to the AST: <getattr, registries, plugins,
  templates, SQL column names, anything this tree exposes as a library>.
- Not read: <what was skipped and why>.
- Behaviour verified by: <the test suite, at what pass rate, plus anything run
  by hand>.
