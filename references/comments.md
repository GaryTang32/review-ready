# Comments

A comment earns its place by saying what the code is for and how to use it.
That is the whole brief. Everything else in a comment is a cost the reader
pays on every visit, and most of it is a cost that grows: prose about code is
not checked by anything, so it drifts away from the code silently and then
misleads with the authority of a comment.

The test is the reader's, and it is blunt. A ten-line function does not need
thirty lines of prose above it. Someone who must read more comment than code
to reach the code has been charged twice for one function.

Run [scripts/comment_audit.py](../scripts/comment_audit.py) over the tree
before deciding anything by hand; it reports the budget breaches and the
categories below, worst first, and its false-positive cases are pinned by its
own self-test (`--self-test`).

## 1. What a comment is for

Two things, and they are both about the future reader's next five minutes:

- **What this is and how to call it.** One sentence at the top of a module,
  a class, or a public function. A reader scanning a directory listing should
  be able to place every file from its first line.
- **The fact the code cannot state.** Units, ranges, what a missing value
  means, the invariant the loop maintains, why the obvious alternative is
  wrong, the trap that cost someone a day. This is the only kind of comment
  that gets more valuable over time, because it is the only kind holding
  something the code does not already hold.

Everything else is either already in the code, already in git, or not true
for long.

```python
def sessions_between(start, end):
    """Trading sessions in [start, end), in the exchange's timezone."""
    # The exchange calendar is authoritative; a naive weekday count is wrong
    # around holidays, which is what the half-day table below is for.
    return _calendar.sessions_in_range(start, end)
```

Three lines of prose on three lines of code, and both earn it: the docstring
says what you get and that the interval is half-open, the comment says why
the obvious implementation is wrong.

## 2. What gets cut, and why each one is a NAK

| Kind | The objection |
|---|---|
| **History** | "git log carries this, and it stays true when the code changes." A comment saying what the code used to do, who changed it, which release it arrived in, or which ticket it closed is a claim about the past that nothing updates when the past is undone. The commit message is where that belongs, and it is where a reader already knows to look. |
| **Narration** | "I can read the line under it." A comment that paraphrases the next statement goes stale the moment that statement moves, and it teaches the reader to skip comments. |
| **Restating the signature** | "The signature already says that. Now I read it twice." An `Args:` block whose entries repeat the parameter names with no fact added is the signature retyped in prose, and it is the first thing to rot when a parameter is renamed. |
| **Essays inside a body** | "A paragraph inside a function is a function waiting to be named." Six lines of explanation before a loop is a name the code has not been given. Extract the block into a function whose name says what the paragraph said; then it is checked by the compiler and by the tests. |
| **Banners** | "A section rule in a file means the file is two files." A row of dashes separating a module into parts is a structural finding, not decoration. |
| **Commented-out code** | "Delete it; git remembers." Code behind a `#` forces every reader to decide whether it matters. |
| **TODO** | "Do it, defer it in the cover letter, or delete it." An unowned, undated intention in the source is a wish, and it outlives whoever wrote it. |

Note what is **not** on that list. A date is not history when the date is the
subject: a trading calendar, a fixture, a regulatory deadline, a table of
holidays. "Historical" is not history when it describes the data or the
behaviour, as in a historical replay. The audit script knows the difference
and requires a change-of-code verb or explicit changelog framing before it
calls a date history; when judging by hand, apply the same test. Deleting a
comment that documents a domain fact is a worse outcome than leaving a
comment that documents a commit.

## 3. The budget

Prose should not outweigh code. The script flags a function when its
docstring and comments together exceed its body and amount to at least eight
lines, when a body of three lines or fewer carries a docstring of six or
more, and when a docstring runs past twelve lines over a body under twenty.
A module docstring past thirty lines is a document that wants to be a
document.

These are thresholds for finding candidates, not verdicts. A parser whose
grammar needs explaining, a numerical routine with a derivation, a public
interface with real usage examples: each can legitimately run long. What
cannot run long is a wrapper, an accessor, a dataclass, or a five-line
function doing one obvious thing.

## 4. Docstring shape

- **One line first**, in the imperative or as a noun phrase, ending in a
  period. It is what `help()` shows and what a reader skims.
- **Then the facts the signature cannot carry**, if there are any. Blank line
  first.
- **Document a parameter only when there is something to say about it**:
  units, a range, what `None` means, which of two spellings is accepted. A
  parameter whose name and type say everything gets no line.
- **Document what it raises** when the caller has to handle it.
- **No `Args:` block at all** on a function whose parameters are all obvious.
  A missing block is not an omission; a block full of restatement is a defect.
- **Private helpers often need no docstring.** A short `_name` doing one
  thing, called from one place, is explained by its name and its caller.

## 5. Fixing them is its own patch

Removing a comment changes no behaviour, so it lands as a patch saying
`No functional change.`, and a reviewer verifies that claim by seeing that no
test moved. Two rules keep the series reviewable:

- **Group by what a reviewer can verify in one pass, not by kind.** A patch
  per file, or per small package, reads as a decision a person made there.
  Twenty patches split by category read as a sweep, and a sweep is what nobody
  reviews; twenty one-file patches in a pre-PR series are a patch bomb. In a
  pre-PR pass comment cuts ride in the patch that introduces the code, and a
  comment-only patch exists only for pre-existing code the branch modifies.
- **Never mix a comment patch with a logic patch.** A deleted comment beside
  a changed condition is the classic place a real change hides.

Where a comment is wrong rather than merely long, that is a finding about the
code, not about the comment. Say so in the message: a comment claiming an
invariant the code does not maintain means either the comment or the code is
the bug, and which one it is needs deciding before either is touched.

## 6. Review questions

Added to the maintainer pass in [review-checklist.md](review-checklist.md):

- "What does this comment say that the code does not?"
- "This is history. Which commit message should it have been in?"
- "This narrates the line under it. What happens to it when that line moves?"
- "The `Args:` block repeats the signature. What fact is in it?"
- "Six lines of prose inside a body: what is the function called?"
- "Is this date describing a change to the code, or describing the data?"
- "You deleted comments and changed a condition in the same patch."
