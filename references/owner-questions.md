# The owner's questions

Asked together at stop 1 of an interactive run, after step 1, one question
each. Each option carries its description from the tables below, so the owner
chooses knowing what the choice changes. Recommend the option marked so,
first in the list.

The question tool may hold four options per question plus a free-text
answer. Where a question has more (the commit cap has six), offer the four
most likely as options, name the others in the question text, and take them
from the free-text answer.

## 1. Is this the problem?

Show the first paragraph of `problem.md`.

| Option | What it means | Expected impact |
|---|---|---|
| **Agree** (Recommended when it is right) | The statement is the brief for the whole pass. | Everything the problem does not need is pruned, with a reason in `deferred.md`. The series solves exactly this, and nothing else. |
| **Correct it** (their words) | The owner restates the problem. | What is kept and what is pruned changes from here on: work the corrected problem needs comes back, work it does not goes to `deferred.md`. The cheapest moment to correct it is now, before the rewrite. |

A wrong statement here produces a clean series of the wrong thing; nothing
later in the pass can catch that.

## 2. Which tests must pass whatever the rewrite does?

List the guard tests you found (boundaries: malformed input, NaN, an empty
file, an output format other programs read), by name, as a multi-select.

| Option | What it means | Expected impact |
|---|---|---|
| **The tests named** | Saved as `must_pass`. The builder runs each as the branch wrote it, against the series, and refuses a series it fails. No reason can excuse a failure. | Those behaviours survive the rewrite for certain. The series may keep a guard or a check it would otherwise cut, so it can end up a little longer. |
| **None** | No test is protected absolutely. | Every test of the branch is still run against the series: one that fails or is left out needs a written reason, and you see each in the review page. A reason can justify a change you would have refused, so read the tests table before adopting. |

## 3. How many rounds of maintainer review at most?

A round is one review by a fresh reader who has not seen the reasoning, and
the rebuild that fixes what they reject. Each round reruns the check at every
commit and reviews every patch again, so each costs time and tokens.

| Option | What it means | Expected impact |
|---|---|---|
| **1** | One review, no second chance. | Fastest and cheapest. Anything the reviewer rejects comes back to you as an open question instead of being fixed, and the series is not landed while a rejection stands. Suits a small branch, or when you want to make the calls yourself. |
| **2** | One review, one fix, one re-review. | Most mechanical objections (a missing test, a long message, a dropped guard) are fixed and checked. Rejections about the approach still come back to you. |
| **3** (Recommended) | The default. | Room to fix, recheck, and fix again. Rejections still standing after three usually mean the problem statement or the split is wrong, which a fourth rewrite would not cure; they come back to you with that diagnosis. |
| **5** | A long leash. | Fewest questions for you, at the most time and cost. Suits a large branch in an automatic run. Beyond three, later rounds tend to polish rather than correct. |

## 4. How many commits at most in the series?

Counted over the whole series, every PR of a stack included. Step 4's
budgets still shape the split: a patch reviewable in fifteen minutes, about
200 lines of edits to existing code per PR, one patch per new unit. The cap
only says when to stop splitting.

| Option | What it means | Expected impact |
|---|---|---|
| **5** | A small PR. | Each commit carries more: preparatory patches fold into the patch they prepare, and work beyond five moves to a follow-up PR in `deferred.md`. Suits a focused fix or one feature. Larger commits are harder to bisect and to review in one sitting. |
| **10** | A typical feature branch. | Room for preparation, the behaviour change, its first users and the cleanups it enables, each in its own commit. Rarely forces anything to be deferred. |
| **20** | A large branch. | Fine-grained commits; past about six, the series is usually cut into stacked PRs so each can merge alone. The reviewer reads more messages, each over a smaller diff. |
| **40** | A guard, not a limit. | Almost never binds outside audit-mode refactors of a whole tree. Stops a runaway split, nothing more. |
| **Custom** (a number) | Any whole number from 1. | 1 squashes the series into one commit, right only when the change is one thing. Other numbers behave like the nearest option above. |
| **Unlimited** (Recommended) | No cap. | The series has as many commits as the split needs, under step 4's budgets. The usual result is two to six. |

With a four-option tool, offer Unlimited, 5, 10 and 20, and end the
question with "For 40 or another number, type it." Put the 40 and Custom
rows' impact in the question text too, so every choice is explained.

## Automatic runs

Nothing is asked. A number the request gives is used ("at most two rounds",
"one commit"); otherwise round cap 3, commit cap unlimited, `must_pass` as
SKILL.md's automatic run describes, and the problem statement derived from
the request and the branch. The hand-back states each of these, so the owner
sees what the pass assumed.
