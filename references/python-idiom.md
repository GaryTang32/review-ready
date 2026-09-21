# PEP rules for reviewing agent-written Python

Sources, all at peps.python.org, section titles cited as the PEPs print
them: PEP 8 (Style Guide for Python Code), PEP 20 (The Zen of Python),
PEP 257 (Docstring Conventions), PEP 3107, 484, 526, 563, 604 and 649
(annotations and type hints), PEP 3134 and 415 (exception chaining),
PEP 343 (`with`), PEP 572 (assignment expressions), PEP 634 and 635
(pattern matching), PEP 498 (f-strings). The Google Python Style Guide
(2.4 to 3.16) is cited where it decides what PEP 8 leaves open. All
paraphrased; every code fragment is original.

Read this after maintainer-rules.md. Order at the prune (SKILL.md step 2):
maintainer-rules.md, then this file for the lines in the diff, then
behaviour-switches.md for what the diff does not show, then
clean-code-examples.md at the refactor (step 3). Each heading names the step it
serves. At step 7 the NAK list is extended by section 7 here and by
behaviour-switches.md section 6, and by nothing else.

Switches — configuration, data, call sequence, defaults, the runtime
environment — belong to behaviour-switches.md whether or not the line is in
the diff. This file rules only on how the spelling reads; every objection
below whose fix is "its own patch" is that file's. `python-peps.md` is the
research draft this file replaces: nothing should cite it. Cite this file by
heading, not by number; the numbering changed once.

Below, `NAK:` lists triggers, not verdicts. Each is an instance of the
objection in the sentence above it; a review line quotes the hunk, states
that objection, and names the fix (review-checklist.md, "The NAK list").
The verdict is per patch and lives only there.

## 1. Why the PEPs are here, and where they stop (step 2, prune)

If the tree runs black, ruff, flake8 or isort, their findings are not review
material: they are a separate commit with nothing else in it
(maintainer-rules.md section 1), or they are reverted as lines the problem
did not need, or they are one line in `deferred.md`. Never "out of the
series" with no record, and never inside a logic patch; a reviewer who
spends a comment on one has stopped reading the logic. That covers
indentation, blank lines, operator spacing, quote style, trailing commas,
import order, line length on unchanged lines, `== None`, `== True`,
`type(x) == T`, `f = lambda`, unused and wildcard imports. mypy is not on
that list: it reports type errors, not formatting (section 4). Neither is a
bare `except:` — choosing the exception is a design decision, and the bare
form also swallows `SystemExit` and `KeyboardInterrupt`, so it changes what
Ctrl-C does (section 2, exceptions).

Review what is left, which decides whether the patch is right, and report in
this order: what an error becomes, what a missing value becomes, what runs
when, what a name promises. A naming or docstring item is never raised in a
patch that also has a correctness item; fix the correctness one and the
author will be back.

PEP 8 "A Foolish Consistency is the Hobgoblin of Little Minds" yields to the
surrounding file, which is maintainer-rules.md section 3's last row. Two
consequences decide most prune calls:

- The PEPs govern the lines this patch already changes and license nothing
  on the lines it does not. A PEP number is never a reason to touch a line
  the problem statement does not need.
- The same PEP 8 section calls in-passing cleanup an opportunity. It is one
  to send a second patch. Take it in `deferred.md`, not in this hunk.

## 2. PEP 8 essentials a formatter cannot check (step 2, prune)

**"Naming Conventions", "Overriding Principle"; Google 3.16.** Names
visible to users reflect usage, not implementation; descriptiveness scales
with scope, so `i` and `row` are right in five lines and wrong across
nested ones; never encode the type in the name. Module constants are
`UPPER_CASE`, classes `CapWords`, exceptions end in `Error`, and the case
must never lie: an `UPPER_CASE` dict mutated at runtime is not a constant.
NAK: `df_prices_cleaned_final_v2`, `data_dict`, `DataProcessor`, `Utils`, a
`get_` function that writes, `MAX_RETRIES = 3` beside a literal `3` in the
loop. Ask: *does the name say what this is for, and is the same thing
called the same name everywhere?*

**"Imports".** One per line, grouped standard / third-party / local,
absolute, at the top, no wildcard. An import is also a call: module bodies
run at first import in the order of the block, and a registration decorator
runs then. NAK: an import inside a function hiding the circular import the
new module created, an import whose side effect is the registration
(behaviour-switches.md section 2, "Call sequence, import time"; the fix is a
separate patch that is not "No functional change.", not a line edit), three
unused imports from an abandoned draft, a new third-party dependency inside
a feature patch. Ask: *who uses each new import in this patch, and what
runs when it is imported?*

**"Public and Internal Interfaces", "Designing for Inheritance".**
Documented names are public, `__all__` declares the set, internals carry a
leading underscore, and a name imported into a module is not that module's
interface. When in doubt make an attribute non-public, since demoting it
later breaks callers; add no trivial getters; do not design for subclassing
you have not seen. NAK: every helper re-exported from `__init__.py`, an ABC
with one concrete subclass, `@property` over a plain attribute,
`get_config()` returning `self._config`. Ask: *which names here are new
public surface, who asked for each, and who subclasses this today?*

**"Comments", "Inline Comments"; Google 3.8.5.** A comment that contradicts
the code is worse than none and one that states the obvious distracts, so
comment the intent, never the mechanics: the reader knows Python better
than the author and does not know what the author was trying to do. NAK:
`# Iterate over the rows`, `# Validate input`, banner comments splitting a
function into sections that want to be functions, a comment the second
draft made false. Ask: *does this comment say something the code cannot,
and is it still true after this patch?*

**"Programming Recommendations": comparisons and truth testing; Google
2.14.** Compare singletons with `is`; use truthiness for sequences, never
for a value that may be `None` and legitimately falsy. `0`, `""`, `[]` and
`None` are all false and only one of them means absent; for integers compare
against `0` explicitly. A frame or a Series is in neither camp: `bool(df)`
raises whatever it holds, so the emptiness test is `df.empty` or
`len(df) == 0` and the absence test is `df is None`. For numpy test
`arr.size` — a multi-element or empty array raises `ValueError` in boolean
context, and the sizes that do not raise are worse, since a one-element or
0-d array converts to its own truthiness and `if arr:` on a single `0.0` is
silently false. A missing value never becomes a present one on the way past:
where two readings of a missing input are possible, ask the user one precise
question before writing the hunk, and never implement both behind a flag.
NAK: `cols = cols or DEFAULT` where an empty list is a legal request
(behaviour-switches.md section 2, "Defaults, silent fallbacks"; the fix is
its own patch, not a line edit), `threshold or 0.5` where `0.0` is a real
threshold, `if not df:`. Ask: *which of the falsy values is this test meant
to catch, and are the others legal here?*

**"Programming Recommendations": exceptions; Google 2.4.** Derive from
`Exception`, shape the hierarchy around what the catcher must distinguish,
end custom classes in `Error`, keep the `try` body to the lines that can
raise, chain with `raise X from Y`. `except Exception:` is legal only to
log and re-raise or to keep a top-level loop alive. Never `assert` to
validate an argument: `-O` compiles it away, so the check is not there in
production. NAK: thirty lines inside `try` ending in `except Exception as
e: logger.error(e); return None`, so a parse failure becomes a `None` that
becomes a NULL join three calls later; a `finally` containing `return`,
which cancels the exception in flight; a bare `except:`, which eats
`SystemExit` and `KeyboardInterrupt` too, so the process no longer stops on
Ctrl-C — name the error you expect, or let it propagate. Ask: *which line in
this `try` raises which exception, and what does the caller do with what the
`except` returns?*

**"Programming Recommendations": `with` and context managers.** Use `with`
for acquire-and-release; a manager that does anything else is invoked
through a name that says so, because the reader at the `with` line must
know what happens on exit without opening the class. NAK:
`with suppress_errors():`, a manager that commits on error. Ask: *what
does `__exit__` do with an exception, and would I know it from the name?*

**"Programming Recommendations": return consistency.** Either every `return`
returns a value or none does; write `return None` explicitly. A function
returning a frame on one path and falling off the end on another has a
hidden `None` the caller will not check. NAK: `return result` on the success
path, an `else:` that only logs, an `except` that returns nothing. Ask:
*trace every exit: does each return the same kind of thing?*

**Google 2.5, 2.6, 2.7, 2.12.** No mutable global assigned after import;
if one is warranted, underscore it and reach it only through functions. No
mutable default, and no default that should be per call but is evaluated
at definition (`ts=datetime.now()`, `cfg=load_config()`). Both of those are
switches before they are spellings: the fix is a separate patch that is not
"No functional change.", not a line edit (behaviour-switches.md section 2,
"Call sequence, import time" and "Defaults, silent fallbacks"). Nest a function
only when it closes over a local other than `self`; otherwise it belongs
at module level with an underscore, where a test can reach it. One `for`
and at most one `if` in a comprehension: a second `for` is a nested loop
wearing an expression. Ask: *which module-level name is assigned after
import, and by whom; what does the inner function close over?*

## 3. PEP 257 versus "no docstring that restates the signature" (step 2)

**"What is a Docstring?"** In PEP 257's reckoning public modules, functions,
classes and methods (including `__init__`) get a docstring and non-public
ones do not, and PEP 8 offers instead a comment after the `def` line. The
content rules never conflict with "no docstring that restates the
signature"; only the coverage does, and "Settled coverage" below is the
narrower rule this tree uses. Ask: *is this name public in PEP 8's sense
(documented, in `__all__`, imported elsewhere), and did this patch change
it?*

**"One-line Docstrings".** A phrase in the imperative ending in a period,
and explicitly not a restatement of the signature, which introspection
already gives the reader. The PEP states the kernel rule; you are not
choosing between them. NAK: `"""Load data from path."""` on
`def load_data(path)`. Ask: *delete the docstring; what did the reader
lose?*

**"Multi-line Docstrings".** Summary line, blank line, then what the
signature cannot say: side effects, exceptions raised, restrictions on
when the function may be called, units, whether keyword arguments are part
of the interface, the invariant the body maintains between iterations, and
the complexity claim that prices its loops (clean-code-examples.md section
11). A class docstring states the invariant its methods share; a script
docstring doubles as the usage message. NAK: a Google-style block listing
every argument with its type and saying nothing about the cache file it
writes, the `KeyError` it raises, or that the returns are fractions and not
percent; a nested `while` that is linear with no line saying why, which a
reviewer counting nesting reads as quadratic. Ask: *does it name a side
effect, exception, unit, precondition, invariant, or cost, or only the
parameters?*

Settled coverage: a public name this patch adds or changes gets a docstring
only when there is a fact the signature cannot carry — a side effect, an
exception, a unit, a precondition, an invariant, a complexity claim. When
there is none, it gets none, and that absence is not a gap for a later patch
to fill. A private helper gets none either, unless it maintains an
invariant, carries a complexity claim, or has a precondition its caller
cannot see; then it gets those and nothing else, whatever its visibility.
An unchanged function gets nothing from this patch, whatever the tree's
coverage is. A docstring whose only content is the parameter list is deleted
even from a public function.

## 4. Type hints: PEP 3107, 484, 526, 563, 604, 649 (step 2, prune)

**Hint the public boundary, not the body — in a file that already hints.**
PEP 3107 "Fundamentals of Function Annotations": Python attaches no meaning
to an annotation, which is metadata for third-party tools, so a hint proves
nothing about what arrives. A patch that relies on the type points at the
check that already enforces it; a patch that does not adds neither hint nor
check. In a file that hints nothing, add nothing (maintainer-rules.md
section 3, last row). Validation is bounded the same way: check where data
enters the tree — a file, a socket, a request, a user argument — and only
the fields the problem statement depends on. A caller inside the tree is not
a boundary, so do not re-check what it already checked. Validation added
anywhere else is a prune (SKILL.md step 2, item 5); validation the problem
statement does need is its own patch whose message names the input that
failed. NAK: hints plus `isinstance` checks of the same types in the body; a
hint trusted on data from a CSV. Ask: *what enforces this hint at the
boundary, and did the problem statement ask for that check?*

**Never annotate code you are not otherwise changing.** PEP 484 "Rationale
and Goals" and "Non-goals", PEP 526 "Non-goals": hints serve offline
checkers and editors and are never mandatory, not even by convention, so
the PEP that created them disclaims the "hints everywhere" policy agents
apply. A sweep inside a feature patch mixes a formatting patch into a logic
patch and turns a 40-line change into 400; it is its own "No functional
change." commit or one line in `deferred.md` (section 1). NAK: any hinted
signature this patch did not otherwise change. Ask: *which hinted signatures
did this patch change for another reason?*

**`Optional` is explicit or it is absent.** PEP 484, "Union types": a
parameter defaulting to `None` is hinted `Optional[X]` or `X | None`, since
implicit `Optional` was removed and checkers no longer infer it. The hint is
the reader's notice that a `None` branch exists in the body. NAK:
`def f(cols: list = None)` followed by `for c in cols:`. Ask: *for every
`= None` default, is `None` in the hint, and where does the body handle it?*

**No `Any` as a shortcut.** PEP 484, "The Any type": it is consistent with
every type in both directions, so it documents nothing and makes the checker
pass on anything; it is the hint equivalent of `except Exception: pass`. So
are `cast()` and `# type: ignore`, which assert what the code does not check,
and a type alias with one use, which is the typedef maintainer-rules.md
forbids. NAK: `def run(config: Dict[str, Any]) -> Any`, `Callable[...,
Any]`, `cast(DataFrame, x)` to end an argument with mypy. Ask: *what would
this hint reject?*

**Annotations run, and when depends on the floor.** PEP 526 "Runtime
Effects of Type Annotations": through 3.13 a module-, class- or
signature-level annotation is evaluated and stored while a local annotation
inside a function body is neither, so a bare `x: int` binds nothing; since
3.14 (PEP 649, "Deferred Evaluation Of Annotations Using Descriptors") none
of them is evaluated until something reads `__annotations__`. Check the
tree's floor before claiming an annotation runs at import. The NAK that
holds in both worlds is `cache: dict = {}` in a class body beside
`self.cache` in `__init__`: one object shared by every instance because of
the assignment, not the annotation. PEP 604 "Simplified Syntax": `X | Y`
needs 3.10 wherever it is evaluated, and in an annotation it needs only
`from __future__ import annotations` (PEP 563), which makes annotations
strings and the spelling legal — read the head of the file before calling it
an error. NAK: `int | None` on a 3.8 floor in a module without that import;
`X | Y` in any evaluated position on that floor, where the future import
does not help — a module-level alias, a default computed from it,
`typing.get_type_hints()`, pydantic; `Optional[int]` and `int | None` three
lines apart. Ask: *does this spelling import on the tree's minimum Python,
does anything evaluate it at run time, and is each annotated class attribute
per-instance, shared, or a mere declaration?*

## 5. Six PEPs, one line each (steps 2 and 4)

| PEP and section | Rule | Misuse that is a NAK |
|---|---|---|
| 3134, "Implicit Exception Chaining" and "Explicit Exception Chaining" | Translate with `raise NewError(...) from err`, which PEP 8 "Programming Recommendations" wants precisely because it states the replacement without losing the original traceback. | `raise LoaderError(str(e))`: the stack survives by itself as `__context__`, but the original's type and attributes collapse into a string, so no caller can branch on it, and `__cause__` stays `None`, so the traceback reads "During handling of the above exception" — an accident — instead of naming this as the direct cause. |
| 415, "Proposal" | `raise X from None` suppresses the original; use it only when the original is genuinely noise, and say why in the message. | `from None` everywhere because a linter asked for a `from`. |
| 343, "Specification: The 'with' Statement", "Generator Decorator" | `with` acquires and releases; `__exit__` returning true swallows the exception, and `@contextmanager` swallows it by catching around the `yield`. | `try: yield` / `except Exception: logger.warning(...)`; a `with` around code that acquires nothing, used as a section marker. |
| 572, "Style guide recommendations" | Use `:=` only where it makes the code clearer (match-in-condition, read-until-empty, a subexpression reused in a comprehension); otherwise write the statement. | `if (n := len(rows)) > 0 and (first := rows[0]).valid:`; a walrus in a comprehension whose leaked name is read fifty lines later. |
| 634, "Capture Patterns", "Value Patterns" and "Irrefutable case blocks"; 635, "Motivation" | `match` destructures structured data; a bare name in a pattern captures and always succeeds, so at most one irrefutable case block is allowed and it must be last. A constant is matched by a dotted value pattern. | `case ACTIVE:` is a capture, never a comparison to the constant: placed last it matches every subject and rebinds `ACTIVE`; placed anywhere else CPython refuses to compile the module (SyntaxError: name capture makes remaining patterns unreachable). Write `case Status.ACTIVE:`. `case _: pass` turns an impossible state into a no-op; `match` over four string literals wanted an `if` chain or a dict. |
| 498, "Expression evaluation" | An f-string is evaluated eagerly in the enclosing scope, and interpolation into something later executed is concatenation with a nicer face. | `logger.debug(f"...{df.describe()}")` formats even when debug is off; `cur.execute(f"... WHERE id = {row_id}")` makes the data part of the SQL. |

## 6. Conflicts with the kernel rules, and how each is settled (any step)

- **79 columns (PEP 8 "Maximum Line Length") versus match the file.** A
  formatter setting, not a review item: new lines take the tree's
  configured limit or the file's prevailing one, unchanged lines are never
  re-wrapped.
- **Imports at the top (PEP 8 "Imports") versus lazy imports.** A
  function-level import is allowed when the message says why (startup time,
  optional dependency, a circular import a later patch removes), never to
  hide a dependency this patch introduced.
- **`except Exception` to log and re-raise versus "broad except".** The
  PEP's case is the kernel's case: legal at a boundary where the
  alternative is losing unrelated work (CLI top level, worker loop, request
  handler), and only when the handler re-raises, exits, or moves to the
  next unit with the error recorded. Inside library code, or followed by
  `return None`, it is a NAK.
- **"Practicality beats purity" versus one logical change per patch.**
  Practicality governs what the code does; the patch boundary is not a code
  decision. A hack lands as its own patch whose message says it is one.
- **The tree's `def f(x: int = None)` versus explicit `Optional`, and
  `X | None` versus `Optional[X]`.** Fix the spelling on signatures this
  patch touches and leave the rest; use the form the file uses, or on a
  silent file the one that imports on the tree's floor (section 4, which
  says when `X | None` does). Never both in one diff.
- **An exhaustive `case _:` versus "no defensive code for impossible
  states".** `case _: raise SomeSpecificError(subject)` asserts an
  invariant, cheap and loud, and stays. `case _: pass`, `return None`, or
  `logger.warning(...)` make the impossible silent and are NAKs.
- **f-strings as the one obvious way versus `%`-style logging.** f-strings
  everywhere except a logging call's format string, where the arguments
  stay separate so formatting is deferred, and any string executed later
  (SQL, shell, `eval`), where the value goes through the consumer's own
  parameter mechanism.

## 7. What agent code does with these PEPs, and why each is a NAK (step 7)

Extends the NAK list, reported in section 1's order.

| Habit | The maintainer's objection |
|---|---|
| Converts every `%` and `.format` in the file it opened to f-strings | "That is a formatting patch wearing a bug fix. Which of those lines did the problem need?" |
| Adds type hints to every function in the file it opened | "Which of those signatures did this patch change? Own patch or out." |
| `def f(cols: list = None)` | "`None` is not a `list`. Say `Optional`, and show me the branch." |
| `Dict[str, Any]` on a new public function; `cast(...)` or `# type: ignore` to get past mypy | "What would that hint reject? The checker was right: fix the type or the code." |
| Docstring of `Args:` and `Returns:` on `_helper()` | "That one is private. Unless it states an invariant or a cost, a comment after the `def`, or nothing." |
| Thirty lines in `try`, `except Exception: return None` | "Which line raises what, and what does the caller do with that `None`?" |
| `raise MyError(str(e))` | "You kept the traceback and threw away the exception. `from err`." |
| `@contextmanager` with `except Exception` around the `yield` | "The `with` line does not say it swallows. Name it or stop." |
| `value = value or DEFAULT` on a number; `if not df:` | "Zero is a legal value and you just replaced it: `is None`. The second one raises whatever the frame holds: `df.empty`." |
| `assert isinstance(x, int)` as validation | "`-O` deletes your check. Raise `ValueError`." |
| `match` over four strings with `case CONST:` and `case _: pass` | "That doesn't compile — and if you move it last it eats every case. `Status.CONST`, or an `if`/`elif`. The `_` turned an impossible state into a no-op: raise." |
| f-string inside `logger.debug` in a row loop | "You format every row to decide not to print it. `%s` arguments." |
| f-string inside `cur.execute` | "The ticker is now SQL. Parameters." |
| Walrus to save two lines | "Rewrite it as statements. What was lost besides a line?" |
| Every helper re-exported from `__init__.py` | "That is public surface forever. Who asked for each name?" |
| `_cache = {}` at module level, filled and never invalidated | "Who assigns that after import, and what does the second caller see?" |
| `def f(seen=[])` or `def f(ts=datetime.now())` | "Evaluated once, at import. Use `None` and assign inside." |

## 8. PEP 20, the Zen, mapped to the kernel rules (any step)

PEP 20, "The Zen of Python", is nineteen lines. Each has a kernel rule that
says the same thing in the maintainer's words, and each is broken by agent
code in a characteristic way.

| Aphorism | Kernel rule | Where agent code breaks it |
|---|---|---|
| Beautiful is better than ugly. | Taste (maintainer-rules.md section 4): "Obvious beats clever." Beauty here is the reader's speed, not the author's pleasure. | A one-line comprehension with three conditions and a walrus. |
| Explicit is better than implicit. | "Every merge names its keys, its type, and its cardinality"; "no `__getattr__` magic, no import-time side effects, no decorator that swallows". | `df1.merge(df2)` with no `on=`; `inplace=True`; a decorator that retries and swallows. |
| Simple is better than complex. | "No abstraction without three users"; "That's a class with one method. It's a function." | Strategy pattern for one strategy. |
| Complex is better than complicated. | "Deep modules behind shallow interfaces: one function that does real work beats three layers that forward to each other." Complexity the problem needs lives in one place; complication is layers. | `Loader -> LoaderBase -> LoaderMixin -> _load_impl`. |
| Flat is better than nested. | "More than 3 levels of indentation: fix your program." Guard clauses, early return, `continue`. | `if ok: if df is not None: for row in df: if row.valid: try:`. |
| Sparse is better than dense. | "Functions should fit on one or two screenfuls and do just one thing." One claim per line, one claim per function. | Eight chained pandas calls on one line; a 200-line `run()`. |
| Readability counts. | The ground rule: "The reader's time is the scarce resource, not the author's." | Everything in maintainer-rules.md section 6. |
| Special cases aren't special enough to break the rules. | Linus's linked-list rule: "Remove the special case by choosing the right representation." | `if df.empty: return ...` before every operation; `if i == 0:` inside the loop. |
| Although practicality beats purity. | "When in doubt, use brute force"; "Obvious beats clever, even when longer". Practicality licenses the code, never the patch: one logical change per patch is not negotiable. | "Practical" bundling of three fixes in one patch. |
| Errors should never pass silently. | "Never `except Exception: pass`"; "An error is handled where it can be handled or propagated, never caught and dropped." | `except Exception: return None`. |
| Unless explicitly silenced. | The `except FileNotFoundError:` with the reason in a comment; `raise X from None` with the reason in the message; `contextlib.suppress(SpecificError)`. Explicit means the reader can see which error and why. | Silence with no named error and no reason. |
| In the face of ambiguity, refuse the temptation to guess. | Step 1: "ask the user one precise question and wait"; write mode: "when the spec admits two readings ask which one rather than implementing both"; "NULL becomes a value silently". | Implements both readings behind a flag; coerces NULL to 0. |
| There should be one, and preferably only one, obvious way to do it. | "Duplicate of X in Y: reuse X, and say so"; "Search before writing." | A second `parse_date()` next to the tree's `parse_date()`. |
| Although that way may not be obvious at first unless you're Dutch. | The tree's existing way is the obvious way: "Match the file you are in." The maintainer's taste, not the agent's, decides. | Rewrites the tree's idiom into the agent's idiom. |
| Now is better than never. | "TODO: do it." The fix for the stated problem lands in this series. | A `TODO` where the fix should be. |
| Although never is often better than *right* now. | "or put it in the cover letter": `deferred.md`; "While I was here" is a second patch. | The drive-by refactor inside the fix. |
| If the implementation is hard to explain, it's a bad idea. | The commit message test: if the body cannot say why in a paragraph, the design is wrong; the email test. | A message that describes the diff because the reason cannot be stated. |
| If the implementation is easy to explain, it may be a good idea. | "May": easy to explain is necessary, not sufficient. "An ACK with reservations is a NAK"; the patch still has to bisect and test. | "Simple refactor, no tests needed." |
| Namespaces are one honking great idea. | "New helper module for one caller: put it next to the caller." A namespace is a boundary with contents on both sides; a module per function is a filing cabinet. Also `area:` in the subject, and `_name` for internals. | `utils/helpers/common.py` holding one function. |
