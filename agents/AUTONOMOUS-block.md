An orchestrator can start an agent from an issue, with no person in the session. These rules hold for every such run,
whatever the harness and the model. Each role's brief is in `agents/` (`implementer.md`, `reviewer.md`, `planner.md`); the harness, the
model and the effort are the orchestrator's settings, never part of a rule.

- **An issue in Todo is the owner's "implement".** The rule "question, recommendation, then implement" (How to work here)
  holds for a session with the owner. In an autonomous run the owner already said it by moving the issue to Todo.
- **The issue is the record** (in Linear since 2026-10-24; agents/README.md "The record"). The run reads the issue's text, comments and links, and writes its progress and its
  questions there. The orchestrator decides from artifacts only: the branch, the pull request, its checks, the reviewer's
  verdict and the issue's state.
- **Never ask in the session: nobody answers there.** Every harness's interactive question tool is off for these runs. A
  question for the owner is one comment on the issue (the question, the options, what each costs, a recommendation); the
  run then moves the issue to Escalated and stops. The owner's calls: anything DECISIONS.md holds or would hold, a
  product or design choice, a change of scope, a cost or security trade-off, and two issues or pull requests that disagree.
- **Work found on the way is a new issue, never a wider pull request.** A task that must come first is created, the
  current issue is linked as blocked by it and goes back to Todo, and the run stops. Anything else is created with its
  first line `Found in KEY` and its one Area label (`follow-up` is the reviewer's label: agents/README.md "Follow-ups").
  A new issue never holds an open choice: the choice is a `Question:` issue in Escalated, assigned to the owner, that
  blocks it (agents/planner.md "The issue", State; the owner's call, 2026-11-23).
- **The fast lane.** An issue an agent creates (work found on the way, or the reviewer's follow-up at any depth) goes to
  Todo with the label `fast-lane` when it passes the fast lane's test (agents/README.md "The fast lane", the owner's
  call, 2026-10-28). Every other issue an agent creates goes to Triage, and only the owner moves an issue out of Triage.
- **Priority and size are set in Triage, then they are the owner's.** The agent that creates an issue sets both
  (agents/planner.md "The issue"); the owner checks them when the issue leaves Triage. A fast-lane issue is never in
  Triage: the fast lane's test (XS or S) is its check. After that no agent changes them: an agent that thinks one is
  wrong says so in a comment.
- **An issue that waits for a condition has a measurable trigger** (the owner's call, 2026-11-01): its line `Take it
  when: …` comes right after the origin line (`Found in KEY`, `From: …`), which stays first: the reviewer reads a
  follow-up's depth from it; with no origin line it is the first line. The trigger is a number or an event that a person can check, never "when friction shows" (agents/planner.md "The issue"). With
  no such trigger the issue is not parked: the owner gives it one or cancels it. Canceled is for work that the owner will
  not do; a parked issue is not canceled.
- **The merge bar.** A review finding blocks a merge only when it meets one of these: wrong behaviour (a result a person or
  the product relies on is wrong); data that can be lost or corrupted; a security or privacy property broken; a documented
  promise broken (a page's claim, a rule of this file, a test's contract); the gates red. Everything else is non-blocking:
  it never holds the pull request past round 1. In round 1 the reviewer marks a non-blocking finding `fix_now` when its
  fix is XS, stays in the files the pull request changes and needs no new choice: round 1 then asks for changes, and the
  implementer fixes it in the same pull request (agents/README.md "Follow-ups", the owner's call, 2026-10-28). On a
  docs-only pull request (`.github/code-changed.sh` prints `false`) the size limit drops: each non-blocking finding whose
  fix stays in those files and needs no new choice is `fix_now`, whatever its size (the owner's call, 2026-11-03: a
  fix round on docs costs one review, a follow-up a new issue, pull request and review). With the
  approve the reviewer puts the rest into the open follow-up issue of the pull request's Area, which collects up to six
  findings of several reviews (the owner's call, 2026-11-16). A finding that is not blocking, not
  `fix_now` and below the floor is not written at all: every finding is acted on (the owner's call, 2026-10-28). A
  finding the reviewer could not confirm never blocks. A review that always finds something new is
  the reason for the bar: without it, the loop does not end.
- **An approve is final.** Nothing is pushed to a branch after its approve: the open non-blocking findings go to the
  follow-up issue, not to the pull request. A commit after an approve leaves the new head without the check `verdict`, and a new review starts as round 1 (the
  job `carry`, or `report` for a push while the review ran). A branch update (only automatic merges of main, whatever main changed) gets the approve's check from the review workflow, with no review. The ruleset does not need an up-to-date
  branch, so pull requests merge in parallel, and nothing merges main into a pull request that is only behind. The
  workflow `branch-update` only finds a conflict with main after each merge, one pull request at a time, and moves the
  issue to In Progress; no session merges main after an approve unless that conflict sends it back.
  A pull request that waits for the owner's merge (a code owner's file) is checked too, and a conflict on it goes back to
  its session as for any approved pull request: one comment per head, the label `escalated` off, and the issue to In
  Progress (the owner's call, 2026-11-23). The owner only merges.
- **Three review rounds at most** (the owner's call, 2026-11-16: two before). Round 2 checks round 1's blocking
  and `fix_now` findings and the lines changed since. A `fix_now` finding still open in round 2 becomes a follow-up line,
  never a block. Round 2 asks for changes when a blocking finding is open. Round 3 checks only the blocking findings that
  round 2 left open and the lines changed since round 2: it marks no `fix_now` and writes no new non-blocking finding. A
  pull request still blocked after round 3 goes to Escalated with its open findings; the owner decides. The owner moves the issue
  to Todo (implement the fix; a comment on the issue only when the implementer needs one, and until an orchestrator
  exists the owner also starts the run with `/implement KEY`), or Canceled (drop it: the owner closes the pull request on GitHub, and
  Done means merged), or accepts the pull request as it is with the label `owner-approved` on GitHub: the review workflow
  then gives its head the check `verdict` and moves the issue to Approved (agents/README.md "The guards"). The answer starts the count again: the next review is round 1. So does a conflict that the branch update found on an
  approved pull request (the owner's call, 2026-10-28), and failed gates on an approved head
  (the owner's calls, 2026-10-28) and a real commit after the approve. Who moves an issue, and when: `agents/README.md`.
- **GitHub merges on the verdict; no agent merges.** An approve verdict moves the issue to Approved, and the review
  workflow's job `report` (main's code only) turns on GitHub's auto-merge with the implementer app's token, so the merge
  starts the workflows: GitHub merges when the ruleset lets the pull
  request through, and the issue goes to Done (the owner's call, 2026-10-25; DECISIONS.md "Delivery"). A pull request that
  changes a file of `.github/CODEOWNERS` gets no auto-merge on the reviewer's approve: the job moves its issue to Escalated
  with the files, and the owner merges it on GitHub, or adds the label `owner-approved`, which is the owner's
  merge too: auto-merge then goes on for that head. Escalated is the one state that waits on the owner. While a pull
  request has no required checks (only when the `gates` workflow is disabled), the reviewer runs the gates on the
  branch merged with main, and "green" means that its verdict lists each required gate and each one passed (`agents/README.md`). No agent merges by hand, turns on auto-merge another way, or changes the repository's rules.
  No agent adds or removes the label `owner-approved`: the workflow cannot tell a session that holds the owner's login
  from the owner.
- **One issue, one worktree, one branch, one pull request.** A run in a worktree that another tool made runs `mise run
  start:deps` there first. The branch merges main before its pull request goes to review, so the gates run on what ships.
- **Identity.** The git author is the owner; a role pushes and reviews with its own GitHub App token, so a reviewer can
  approve or request changes on an implementer's pull request, whatever the issue's Area. No attribution
  lines (the section "Rules"), never `--no-verify`.
- **The guards.** A pull request that changes a file `.github/CODEOWNERS` names (the workflows and the scripts under
  `.github/`, the hooks, each skill's `SKILL.md`, the reviewer's brief and its schema, this section of this file, and
  the files that the repository adds there: its tools' settings, its lock files and the like) needs the owner's review (this file is not in CODEOWNERS: `.github/verdict.py`
  `section_owned` makes a change the owner's when it touches this section or a `## ` heading): such a pull
  request waits for the owner's review and merge, or the owner's label `owner-approved`, as for every Area. The
  implementer's app cannot push a workflow change at all, on purpose: a run that needs a file under `.github/workflows/`
  does the rest, writes that file's change as a patch in one comment on its pull request, moves its issue to Escalated,
  and then ends its turn; the owner's label `apply-workflow-patch` applies the patch and marks the pull request ready, and the review that this mark starts reaches a
  session through the orchestrator (agents/implementer.md step 6, the owner's call, 2026-11-16). The workflow's prose
  (`agents/README.md`, `implementer.md`, `planner.md`) and DECISIONS.md merge on the verdict; the review
  reads main's copies of its rules, never the branch's.
- **No production.** An autonomous run never deploys, never reaches a product's server, and never reads the owner's
  password manager or a production secret. A key that the product's own rules name for a measurement on invented data
  is not a production secret: a run uses it only for what those rules ask.
- **`hands-off` means the owner's.** No agent touches an issue with that label or its branch. One exception: the implementer
  adds one comment to an open `hands-off` issue of the same Area, with the live steps of its own pull request, and the
  link that relates the two issues, nothing else (agents/implementer.md "When you are done" step 5).
