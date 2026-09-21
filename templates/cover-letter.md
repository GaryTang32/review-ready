[PATCH 0/N] <area>: <one-line summary of the series>

<Problem. Two to four sentences: what is wrong in the tree, how it shows,
who noticed. Present tense, about the code.>

<Approach. Two to four sentences: what the series does, why this shape,
what was considered and rejected. If a patch flips a switch (config,
default, data), say which one and what the old and new behaviour are. If a
public signature changes, say who calls it.>

Series:
  1/N <subject> (+a/-d): <why it comes first>
  2/N <subject> (+a/-d): <one clause>
  ...

Testing:
  <command>; passes at every patch, <N> tests, <k> skipped. <new test> fails
  before patch k and passes after it.
  Run end to end on: <the rebuilt series: command, input, what was compared |
  the original branch only: the owner runs <entry point> before signing>

Stacked PRs (only when the work was cut into more than one):
  Depends on: <PR or branch that must merge first, or "nothing">
  Followed by: <what comes next and why it is separate>

Deferred (not in this series; this list is all the reviewer will see):
  - <item>: <why it is separate, and whether it deserves its own PR>

Open questions:
  - <a decision the reviewer or user should make, or "none">

(On a first posting the description ends here. Rounds of the skill's own
maintainer pass are drafts, not versions, and are never mentioned. After a
human has reviewed a pushed version, the repost adds:)

Changes since v1 (review on the PR):
  - <what their review changed, and which patch it landed in>
