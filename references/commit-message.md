# Commit messages

The message is the only part of a patch that survives into `git log`,
`git blame`, and the bisect result someone reads at two in the morning in
five years. The diff says what changed. The message says why, and why this
way. Write for that reader.

## Shape

```
area: imperative summary of the change

What is wrong in the tree today, stated as a fact about the code and
its behaviour: the symptom, the failing case, the wrong number.

Why this fix, and why not the obvious alternative. What was
considered and rejected, in a sentence.

What is deliberately not done here, if a reader would expect it.

How it was tested: a command, a case, a number.
```

No `Signed-off-by:` line in the file: the builder commits it verbatim and
refuses one. The owner adds theirs after reading the series.

## Rules and their reasons

- **Subject: `area: summary`, about 60 characters, imperative, no trailing
  period.** `area` is the module or subsystem (`loader:`, `report:`,
  `ci:`), because a maintainer scans hundreds of subjects and routes by
  prefix. Imperative ("add", "drop", "fix") because the subject completes
  "this patch will ...". If the repo already uses `feat(scope):`, use
  that; the body rules still hold.
- **Blank line, then body wrapped at 72.** Git and email both assume it. A
  body that is one 400-character line is unreadable in every tool the
  reviewer has.
- **Problem first.** The reader must be able to disagree with the
  diagnosis before seeing the cure. A message that opens with the solution
  skipped the part the reviewer is there to check.
- **Present tense about the tree, not past tense about you.** "The loader
  keeps duplicate rows", not "I noticed duplicates" or "Updated the
  loader". The author's process is not part of the tree's history.
- **Name the alternative you rejected.** One sentence. It stops the
  reviewer from suggesting it and shows the choice was made, not
  defaulted.
- **Say what is out of scope when a reader would expect it.** "The report
  still counts the duplicates; that is a separate change" saves a round
  trip.
- **Say how you tested.** "Ran the tests" is weak. "`python3 -m unittest
  tests.test_loader`; the new test fails on the parent commit and passes
  here" is a claim the reviewer can check.
- **Prep patches say so.** A patch that moves or renames with no behaviour
  change ends its body with "No functional change." That sentence is a
  promise; the reviewer checks it by looking for any changed test.
- **The owner signs it.** `Signed-off-by:` with the author's name and email
  (`git rebase --signoff <target>` over a finished series). In the kernel it
  certifies origin (the trailer itself dates from 2004; the accountability it
  names is older); here it certifies that the person named has read the patch
  and can defend every line. That is a statement only they can make, so the
  pre-PR pass hands the series back unsigned with `defence.md` beside it, and
  signs only when asked to. Never sign as the tool.
- **Never describe the diff.** "Added function X, modified Y, updated
  tests" is what `git show --stat` is for. If the body only lists changes,
  delete it and start again from the problem.
- **Never mention the tool.** Codex, Claude, Copilot, "AI-generated": none
  of it. The reviewer reviews a change and the human who submits it.
- **Never write "improve", "enhance", "clean up", or "refactor for
  maintainability" without saying what was hard before.** Those words
  carry no information and mark a message written after the fact.
- **No ticket number as the whole message.** A `Ref: JIRA-1234` trailer is
  fine. The message must stand without it, because the ticket will be gone
  or unreadable long before the code is.

## Trailers

After the body, before the sign-off, one per line. Each is a claim someone
can check; never add one that is not true.

- `Fixes: 1a2b3c4d5e6f ("loader: keep duplicate rows")`: this patch fixes a
  defect that commit introduced. Twelve characters of sha and the quoted
  subject, so it survives a rebase of neither. It is what lets a backport
  find every fix for a bad commit.
- `Reported-by:`, `Tested-by:`, `Reviewed-by:`, `Acked-by:` with a name and
  address: only with that person's agreement, and only for what they did.
- `Link:` or `Ref:` to the ticket or discussion. The message must still
  stand without it.

A version is a posting a human received. Rounds of the skill's own maintainer
pass are drafts: they live in `review-vN.md` and never in a message, and the
PR opens unversioned with no "Changes since" block. After a human has reviewed
a pushed version, the repost is v2 and the PR description says what their
review changed. Per-patch review notes go in the PR conversation: on a pushed
branch nothing cuts a `---` line, so whatever sits under it is history for
ever (the builder refuses one). engineering-sops.md section 2 has the rest.

**Every fact has a source.** The shape asks for a symptom, a failing case, a
number. Each comes from the user, the ticket, or a command whose output you
hold (quote the command). What you do not have, you ask for once at step 1,
or you write the weaker true sentence: "load_prices() returns both rows; the
new test shows it". The figures in the examples below stand for facts their
author held. An invented one is worse than none: the first "which report?"
ends the owner's standing with that reviewer.

## Examples

**Bad (typical agent output):**

```
feat: add dedupe and improvements

- Added DedupeConfig and DedupeStrategy classes
- Refactored loader for better maintainability
- Improved report formatting
- Added comprehensive tests
- Updated dependencies
```

Five changes, no problem, no reason, and "comprehensive" is a review
verdict the author is not entitled to give.

**Good (the same change after pruning, patch 2 of 2):**

```
loader: drop duplicate (ticker, date) rows, keeping the last

The vendor file repeats a row whenever a close is restated, so
load_prices() returns two rows for the same ticker and date and every
downstream mean is weighted toward restated days. Last week's report
showed AAPL with 23 trading days in a 21-day month.

Keep the last occurrence, since a restatement supersedes the original.
Using file order rather than a timestamp is deliberate: the vendor
file carries no restatement time, so file order is the only ordering
there is.

Tickers are normalised by the previous patch; this one relies on it,
so "aapl" and "AAPL" on the same date count as one row.

The report still sums n_rows over the rows it is given, which is now
the deduplicated set; no change needed there.

Means already written to the report table since then are wrong for
any ticker with a restated day; recomputing them is a follow-up.

Test: tests/test_loader.py::test_duplicate_dates_keep_last fails on
the parent commit and passes here.

Fixes: 3f1c2ab9d0e4 ("loader: read the vendor price file")
```

**Good (the first behaviour patch, 1 of 2; the next relies on it). It is not
a "prep" patch: prep patches change no behaviour and say so:**

```
loader: uppercase tickers on read

Rows arrive with mixed-case tickers ("aapl", "Aapl", "AAPL") and every
consumer compares them as strings, so one instrument is counted up to
three times in the report.

Normalise at the boundary, in load_prices(), so no consumer has to.
Doing it in report.py instead would fix the report and leave
transform.py wrong.

Test: tests/test_loader.py::test_tickers_are_uppercased fails on the
parent commit and passes here.
```

**Good (pure move):**

```
report: move summarise() above write_report()

write_report() calls summarise(), and the next patch changes
summarise() in a way that is easier to review with the callee above
the caller. Moved verbatim.

No functional change.
```
