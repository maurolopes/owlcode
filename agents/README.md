# The agent workflow

How a change moves from an issue to main with no person in the session, and the briefs of the roles that move it.
AGENTS.md "Autonomous runs" holds the rules; this file is the workflow end to end: the loop, the record, the states, the
rounds, the follow-ups, the guards, and how a product is set up for it. The briefs are plain text for any harness: Claude
Code, Pi or another. Nothing here names a model, an effort level or a harness's own tools: those are the
orchestrator's settings, one per role. This document lives in the product that is changing it (DECISIONS.md "Delivery");
the whole loop later moves to its own public repository (DECISIONS.md "The agent loop moves to its own public repository").
This file holds the loop's rules only. A product keeps its own rules in its AGENTS.md, or in the files that its AGENTS.md
points to: the names of its gates, its deploy, its server and its data. Where a rule here needs one of them, it names the
product's AGENTS.md.

A skill is the owner's way into a brief from a harness. Each harness reads skills from its own folders: Claude Code from
`.claude/skills/`, Pi from `.agents/skills/` and `.pi/skills/` (each one's documentation, 2026-10-27). So a new skill has one copy, in `.agents/skills/NAME/`,
and `.claude/skills/NAME` is a link to it (Claude Code follows a linked skill folder). Every skill is kept so, a product's
own skills too. A skill holds only its trigger and points to a brief in `agents/` (or, for a product skill, to the
rules it follows). It names no tool of one harness: it names each action by what it does ("read the issue with its
relations", "move the issue to In Progress", "add one comment"), done with `.github/linear.py` or the harness's own Linear
connection. The skills: `implement`, `issue` and `triage` start the briefs below; `queue` lists what waits on
the owner, each pull request and issue with its reason and its one action ("The issue states"); `measure` prints the
loop's numbers ("What to measure"). A product can add its own skills.

| File | Role | Starts when | Ends with |
|---|---|---|---|
| `implementer.md` | Implements one issue | The issue enters Todo with no open "blocked by", an issue in Todo loses its last open "blocked by", an issue in Todo whose only open "blocked by" enters In Review (or Approved with its pull request open) starts as a draft stack ("What can start now"), or the issue enters In Progress after a review asked for changes or a required check failed | The pull request marked ready for review and the issue in In Review, then the fix round in the same session (below) |
| `reviewer.md` | Reviews one pull request | The pull request is marked ready for review (`.github/workflows/claude-code-review.yml`), or `carry` starts it after a real commit that follows the approved one (a conflict resolved by hand too), from `carry` or, for a push while the review ran, from `report` (round 1): never on any other push, and never after an automatic merge of main | One formal review on the pull request, approve or request changes, the verdict in its body; the workflow reports it as the check `verdict` |
| `verdict.schema.json` | The reviewer's verdict | | |
| `local-review.md` | Reviews the implementer's own diff, read-only, before its first ready mark ("The local review") | The implementer runs `.github/local_review.py` (`implementer.md` "When you are done", step 2) | Findings printed for the implementer; no verdict and no check |
| `planner.md` | Turns the owner's idea into issues, and sorts Triage with the owner ("The triage") | The owner asks (the skill `issue`), or asks for a triage (the skill `triage`), only in a session with the owner | Issues in Triage with their area, project, proposed priority and blocked-by links, or a project for a larger feature; never an issue in Todo. A triage ends with the groups and the checks shown, and only the issues that the owner accepted moved |

## The loop

1. **The owner moves an issue to Todo.** That is the owner's "implement" (AGENTS.md "How to work here"). An issue that
   an agent files in the fast lane ("The fast lane") is in Todo from its start: the owner's "implement" of the issue it
   came from covers it.
2. **The implementer** starts a worktree whose branch carries the issue's key, in lower case (`mise run start
   abc-12-short-name`). It implements and opens a draft pull request at its first push: Linear links the pull request to
   the issue by the key in the branch name and moves the issue to In Progress (its "Draft PR" automation). It merges
   main, pushes (the push hook runs the fast checks, and the `gates` workflow starts), and marks the pull request ready
   for review. That mark is the review signal and the In Review transition (Linear's "PR opened" automation when it fires
   on the mark, else the implementer). Nothing else starts a review: not a push, not a comment. The one exception is
   not the implementer's: a real commit after the approve, for which `carry` starts a review as round 1 (step 5).
   A pull request with no issue key in its branch or its title gets no review: the workflow writes a failed check
   `verdict`, "No issue: put its key in the title". The one exception is a pure bump of the loop's files (a branch
   `loop-bump-SHORT`: "Setting up a product"): it gets its review with no issue. Such a branch that changes a product file
   gets the failed check "A bump that changes a product file needs its issue".
3. **The reviewer**, in a fresh context on another machine, reviews against the merge bar while the gates run (it does
   not wait for them: the required checks hold a red gate), and posts
   one formal review: approve or request changes, inline comments for the findings, the verdict JSON and the follow-up
   list in the review's body. On approve it files the follow-up issue ("Follow-ups") and moves the issue to Approved; the workflow then turns on
   auto-merge, or, when the pull request changes a code owner's file, moves the issue to Escalated instead (step 5). In
   round 1 it requests changes for a blocking finding or for a `fix_now` one ("Follow-ups"), and in round 2 for a
   blocking one. On request changes it moves the issue to In Progress. When blocking findings remain after round 3, or
   a finding is the owner's call, or the issue's "Needs the owner" names a step of the owner before the merge (a check of the result that the run made, never a choice), the step is not done yet (no `owner-approved` label, no comment of the owner that
   says it is done) and no finding asks for changes, it moves the issue to Escalated with the question.
4. **The fix round.** The implementer fixes the blocking and the `fix_now` findings, leaves the follow-up lines
   ("Follow-ups"), answers in one pull request comment, merges main if it moved, pushes, and marks the pull request ready
   again. It converts the pull request to draft before its first change: a draft tells a second run that the round is
   held. The ready mark is round 2, and the one after a second fix round is round 3. There is no round 4.
5. **An approve is final:** nothing is pushed to the branch after it. A push gives a new head, which has no check
   `verdict` until a new review. One push is allowed: a merge of main (GitHub's "Update branch", or the owner's). The
   ruleset does not need one: **pull requests merge in parallel** (the owner's call, 2026-11-02). A pull request
   that is only behind main is left alone, and the merge of a clean one needs no update. What still needs a person is a
   conflict, which GitHub's auto-merge waits on for ever. The workflow `branch-update` finds it: after each merge it asks
   GitHub to merge main into the oldest approved pull request whose `mergeable` is CONFLICTING, one at a time, and GitHub
   refuses. A conflict sends the pull request back: the workflow comments on it, turns its
   auto-merge off and moves the issue to In Progress, and an
   implementer merges main as in a fix round; the review after the resolution is round 1 ("Review rounds"). Failed gates
   on an approved head go back the same way, since no session watches a pull request after its approve, and the
   review after the fix is round 1 too. Only a head with a successful check `verdict` is in the queue. The review workflow's `carry`
   job gives a merge of main the approve's check with no review when every commit since the approved one is the automatic
   merge of main, also when main changed lines that the pull request changes too (before it, such an update got a
   new review as round 1). The owner accepts the small risk that two pull requests, reviewed and green apart,
   break together: the next pull request's gates test its branch merged with main, so a combined break shows within
   minutes, the `gates` workflow runs on main once each night, the product's deploy can test main before it
   ships (its AGENTS.md), and a red main is fixed first. (GitHub's merge queue would test the
   combination, but it needs a repository owned by an organization.) A real commit after the approved one (a push while the review ran, or later) gets a new review as round 1 too: `carry`, or `report` for a push while the review ran, comments and starts it (the owner's call, 2026-11-01). **GitHub merges:** the approve turned on
   auto-merge with the implementer app's token, so the merge starts the workflows, and the ruleset `main` lets the merge through when the required checks (the product's gates and `verdict`) are green
   (the branch need not be up to date). A pull request that changes a file of `.github/CODEOWNERS` gets no auto-merge: the
   workflow moves its issue to Escalated with the files, and the owner merges it on GitHub (the ruleset's code owner
   review let three such pull requests through on the reviewer app's approve alone). The owner's label
   `owner-approved` is that merge too: on the labeled head it turns on auto-merge also for a code owner's file. The same holds for the rerun of `verdict-rerun`.
  When the pull request changes a file under `.github/workflows/` and GitHub can merge at once (`mergeStateStatus` CLEAN, UNSTABLE or HAS_HOOKS), neither
   job calls `gh pr merge`: GitHub would merge it at once as the app, which has no permission `workflows`, and refuse. The job posts one
   comment ("Ready to merge...") and goes on as after an auto-merge, and the owner merges on GitHub. With a required check still pending,
   auto-merge goes on as always. The workflow
   `branch-update` checks such a pull request for a conflict too (the label `escalated`, no auto-merge, a successful
   `verdict`), after the auto-merge ones, so the owner finds it ready to merge. A conflict on it goes back to its
   session as on any other approved pull request (the owner's call, 2026-11-23): one comment per head, the label
   `escalated` off and the issue to In Progress, so the orchestrator wakes the session that opened the pull request. The
   owner's merge is no new round. A merge of
   main into it is carried like any other; a real commit after its approve gets a new review as round 1, and
   that review's verdict moves the issue out of Escalated, as from In Review: an approve back
   to Escalated (`report`: a code owner's file), a request-changes to In Progress, a needs-decision to Escalated (the
   owner's call, 2026-10-28). The owner is on the
   ruleset's bypass list, so a merge by hand is always possible and recorded as a bypass. The workflow skips the move to
   Escalated when the pull request merged already, so Linear's Done stays. Linear moves the
   issue to Done on the merge.
6. **The owner deploys** main, with the deploy that the product's AGENTS.md names.

Measured on 25 pull requests (2026-10-19 to 2026-10-24, before this loop): a reviewed pull request took 8.4 h median, of
which 21 min was work; 90% of the time waited for the owner's next session; 19 of 52 reviews and 21 commits came after a
first approve; no pull request needed a third blocking round. The loop removes the two causes: the hand-offs are
automatic, and an approve ends the pull request.

## The record

- **Linear** holds the issues: one team per repository ("The teams" below), the states and labels below, and its GitHub integration moving an issue on the
  draft pull request, the ready mark and the merge. An open item is in Linear or nowhere: a task, the owner's own step (`hands-off`) or a
  question for the owner (Escalated). There is no roadmap file (deleted 2026-10-25).
- **GitHub** holds the code, the pull request, the reviews and the checks. The review state is the verdict, the inline
  comments are the findings, one comment per round is the implementer's answer, and the verdict JSON is in the body of the
  formal review (approve or request changes). Nothing about a review is written to a file on the branch: the habit "follow-ups into the roadmap file on the branch,
  then merge" made the tail measured above.
- The runs reach Linear two ways, and a key is never in a file (the owner's rule, 2026-10-24). On a runner,
  `.github/linear.py` (standard library) with `LINEAR_API_KEY` in the environment: the Actions secret. In the owner's
  own account, Linear's MCP server (https://mcp.linear.app), signed in once with the owner's Linear account: no key on the
  disk. Every `linear.py` command the briefs name is the action, not the tool: a session with the MCP server does the
  same through it. The owner's own account also reads with a read-only key from its login keychain, through `mise run
  linear:read -- list|issue ...`, and writes through the MCP server. The agents' macOS user
  runs on the same Mac, but it is not the owner's account: its sessions get each team's Linear key ("The teams") from `agent_user.py` and
  use `.github/linear.py` for reads and writes, as on a runner. An orchestrator puts the key into a run's environment from its secret store, never into a file.

### The teams

The product's AGENTS.md holds the one table of the Linear teams and their repositories, in its section "The teams" (the
owner's call, 2026-11-17). The skills read it there. A new team is a new row of that table, after `linear.py setup TEAM`
("Setting up a product"). Its columns, with a row of an example product:

| Team | Repository | The owner's checkout | The Linear key |
|---|---|---|---|
| ABC | acme/product | `~/dev/product` | `LINEAR_API_KEY` |

- **An issue's key names its team, and its team names its repository.** The pull request of `ABC-12` lands in
  the repository of the team ABC (`acme/product`). Every `linear.py` command that takes `TEAM` gets the
  issue's team, and every `gh` command for the issue's pull request gets `-R REPOSITORY`, or runs in that repository's
  checkout.
- **Each team has the key of its workspace.** A Linear key reaches one workspace. Every team of this workspace uses `LINEAR_API_KEY` (the owner's call, 2026-11-24); a team of another workspace would have its own variable
  in the table's last column. `.github/linear.py` sends the key of the team that a
  command names, by its TEAM or by its issue key's prefix, and `LINEAR_API_KEY` for a command that names no team. A
  session of the agents' user gets the key from the agents' keychain (`.workflow/agent_user.py`), and `mise run
  linear:read` gets the read-only key from the owner's login keychain. A skill that reads every team reads each one
  with its key: a team whose key is missing (`linear.py: no VARIABLE in the environment`) is one line in the report,
  and the other teams still show.
- **One issue, one repository.** Work that changes two repositories is two issues. The part that uses what the
  other part makes is blocked by it.

### Naming another issue

This list is the one copy for every brief: an issue's text, a pull request's text and a commit message keep the same
words. A pull request's text and its commit messages put a closing word only before the key of the issue that the pull
request closes.

- **Closing words:** close, fix, resolve, complete and implement, in each form ("closes", "fixed", "resolving",
  "implements"). At the merge, Linear closes each issue whose key follows one, and it does not read "not" before the
  word (a pull request named an issue after "does not fix", and Linear moved that issue to Done with no fix).
- **Non-closing link words:** ref, refs, references, part of, contributes to, toward, towards, updates (Linear's GitHub
  and GitLab pages together). Such a word links the pull request to the issue: Linear moves that issue to In Progress
  at the draft and to In Review at the ready mark, and leaves it there after the merge (a pull request wrote "part
  of" before another issue's key, and that issue showed In Review with no pull request of its own).
- **Reference-only words:** "related to" and "relates to". They link and change no state.

Name another issue by its key alone (also after "see"), or after a reference-only word. Never put another issue's key
after a closing word or a non-closing link word. An issue's text follows the same rule: a pull request that copies the
text gets the same effect.

## What every run gets

- The worktree to work in, set up as `mise run start` sets one up (`mise run start:deps` in a worktree made by another tool).
- The issue: its text, its comments, its labels and its links (`python3 .github/linear.py list TEAM --key KEY`).
- This repository's rules: AGENTS.md and the files it points to.
- For the reviewer: the pull request, its diff, the round number, its earlier verdicts (its own earlier reviews on the
  pull request), whether the merge with main is clean, whether `.github/code-changed.sh` calls the diff code, and the
  checks' result (`reviewer.md`).

## What the orchestrator trusts

Only artifacts: the branch, the pull request, its checks, the reviews, and the issue's state. It never reads a run's
transcript to decide what happens next. So the owner can stop a run, take it over or restart it, and the orchestrator
carries on from what is on GitHub and in Linear. An orchestrator keeps no state of its own: when it crashes, the next one
starts from the same artifacts (Orchestrator.inc kept the state in itself and lost it in a crash, 2026-10-23). The one
exception is the record of each session's host in its local state file: a lost record costs only the wakes
and the closes of a host that is no longer `session_host`.

## The issue states

Triage, Backlog (parked: work that waits for a condition, or an open blocker; a condition gets a measurable `Take it when:` line, and with no trigger the owner gives one or cancels the issue; an open blocker is named by its link: `planner.md` "The issue"; a flake issue that the hunt cannot make fail again is parked this way, never canceled: "A flake that did not repeat" below), Todo, In Progress,
In Review, Escalated, Approved, Done (merged), Canceled (the owner dropped the issue), and Linear's own Duplicate. Only
the owner moves an issue from Triage to Todo or to Backlog, and from Backlog to Todo (in a triage, the owner's answer in
the session is the move: `planner.md` "The triage"), and only the owner answers an issue in Escalated. Two moves
out of Escalated are not the owner's. One is the verdict of a new review that `carry` started after a real commit
("The loop" step 5): it answers nothing, it reviews code the earlier approve no longer covers. The other is
`verdict-rerun`'s move to Approved after a rerun that `gates-retry` started with no person (a setup failure, or
a `web` job with only flaky tests: the branch is not at fault), described below. So its move to In Progress does not start the review rounds again: they count from the comment of `carry`
("Review rounds"). The review workflow knows that move by its request-changes: the reviewer's last verdict while
the issue was in Escalated. Any other move from Escalated to In Progress is the owner's answer: Linear merges two close
moves by one account into one history entry, and every Linear key here is the owner's, so the owner's move to Todo and
the claim right after it show as one move to In Progress. So no agent moves an issue from Escalated to In
Progress, except the reviewer through its request-changes: a new such move would count as an answer. The review workflow's move to Approved on the owner's label `owner-approved` is the owner's
answer, which the workflow carries to Linear ("The guards"), and so is the owner's rerun of failed gates on an approved head that `verdict-rerun` carries (move to Approved; the workflow takes the label `escalated` off): it starts the review rounds again, as any answer
does. A rerun that `gates-retry` started (a setup failure or a `web` job with only flaky tests, once, attempt 1 only) is carried the same way when it passes, and it also starts the rounds again. An agent creates
an issue in Todo only in the fast lane ("The fast lane"); every other issue it creates starts in Triage. The implementer
never takes an issue from Backlog. Labels: `follow-up` (an issue the reviewer filed from a pull request's non-blocking findings: "Follow-ups"), `fast-lane` (an
issue an agent created in Todo: "The fast lane"), `hands-off` (the owner works on it:
no agent touches it, except that the implementer may add one comment with the live steps of its own pull request and relate the two issues, nothing else: "When you are done" step 5 of agents/implementer.md; the owner's own work, such as an account, a sign-up, a secret or data on the server, is assigned to
the owner and carries it), `restart` (stop the run and start it again from the issue as it is now, on the issue's branch and pull request; the
run that acts on it removes the label first, which is its claim: a second run then finds no label), and one label of the
group Area (`backend`, `frontend`, `workflow`, `infra`, `orchestrator`, `docs`, `business`: the part the issue changes most;
`workflow` is the loop's own parts, the workflows, the scripts of `.github/`, the hooks, the skills and the briefs; an issue
of any Area that needs a file under `.github/workflows/` says so under "Needs the owner": planner.md "The issue"), and the two difficulty labels
`needs-judgment` and `intricate` (an issue that becomes a pull request carries both, one or neither, by the
rubric of planner.md "The issue"; the orchestrator chooses a run's model and effort level by them; an implementer
adds a missing one before its first push, never removes one: implementer.md "While you work"). A task the
implementer found on the way carries its Area label, and `fast-lane` when it took the fast lane: its first line says
`Found in ABC-12`. A work issue never holds an open choice (the owner's call, 2026-11-23): the planner asks
the owner in the session before it files (planner.md "Before you write" step 3), and an agent with no person there
(work found on the way, a follow-up, a flake) files a `Question:` issue that blocks the work issue (planner.md "The
issue", State). So the owner moves any issue from Triage to Todo without a search for a decision in its text. A follow-up carries `follow-up` and the Area label of the pull request's issue. A pull request carries its issue's Area
label on GitHub too (the same seven names): the owner sends a fix to the session of its Area, and GitHub shows no Area
otherwise (`gh pr list --label frontend`). Every issue that
becomes a pull request also has a priority and a size (Linear's estimate, T-shirt: XS, S, M; L means split), set by the
agent that creates it. Moving it out of Triage is the owner's check of both; a fast-lane issue's are checked by the
fast lane's test (XS or S). After that, no agent changes them (the one exception is the size of a `Question:` issue, set on the owner's answer). A question that
only the owner can answer, with no work for an implementer yet, is an issue titled `Question: ...` in Escalated, assigned
to the owner. When it waits in front of a work issue, it blocks that issue: the orchestrator starts no issue with an open
blocker, and the owner's answer closes it (Done). When the owner answers it with work, the session sets the size and the title that names the work that the owner accepts, with the
harness's Linear connection, before it moves the issue to Todo (`planner.md` "The triage", step 5). A larger feature is a Linear project whose description is its specification (`planner.md`).

On GitHub, the label `escalated` marks each open pull request whose issue is in Escalated (the same name as the
state). The owner's filter for the pull requests that wait on them is `is:pr is:open label:escalated`; the skill `queue`
(`/queue`) reads the same pull requests and the issues in Escalated, and prints each one's reason and its one action. A review request
cannot do this for every pull request: one opened in a session in the owner's own account has the owner as its author,
and GitHub refuses a review request to the author. (One that the agent's user opens has the implementer app as its
author and the owner as the git author of its commits: measured 2026-11-03.) The review workflow adds it when an approved pull request changes a code owner's file, after a
`needs-decision`, on a fourth ready signal, and when GitHub refuses auto-merge on an approve on every try, with "Base branch was
modified" or with a 502, 503 or 504 answer; the implementer adds it when it asks the owner; the
workflow `verdict-rerun` adds it when the owner's rerun (or the rerun of `gates-retry`) of the failed gates passes on an approved head that changes a code
owner's file and the owner's label `owner-approved` does not stand, and when GitHub refuses auto-merge on every try, with the same two causes as above. The review workflow removes it when a review starts a round, the implementer removes it when it
starts from the owner's answer, and the workflow `verdict-rerun` removes it when that rerun passes on an approved head
that changes no code owner's file, or whose code owner's file the owner's label lifts, once auto-merge is on or the pull request merged: after 5 refused tries the label stays on. A
failure to add or remove the label only warns in the workflow: the verdict and the check `verdict` stay as they are, and
the owner then sets the label by hand.


### A flake that did not repeat

Canceled is for work the owner will not do. A flake issue whose test nobody could make fail again is not that: the cause
is still in the suite (four such issues were canceled so, 2026-11-05). The run that cannot make the
test fail stays on the issue: it moves the issue to Backlog, with the line `Take it when: the test is flaky again on CI
(its issue gets a comment)` right after the origin line (`linear.py trigger`), and one comment that names what it tried: the number of runs,
the slowdown (the product's flake hunt settings), and the trace of the failing try if CI kept one. A later failure
of the same test adds a comment to that issue (AGENTS.md "Rules": an open issue for the same check, Backlog included,
gets the comment instead of a new issue), which is the trigger: the owner moves the issue to Todo. A new
occurrence of a test that has no open issue files a new issue as AGENTS.md "Rules" says. For a browser check on CI, the
workflow `flakes` (`.github/flakes.py`) writes that comment, or files that issue, within minutes of the run (a
pull request's run that the flake failed, or a run on main, nightly or on demand, which the flake leaves green): the comment also makes
the issue Urgent, since its test was flaky a second time while the issue was open.

## Who moves an issue

| From | To | Who | When |
|---|---|---|---|
| Triage | Todo | The owner | The owner accepts the issue |
| (new) | Todo | The implementer or the reviewer | It creates an issue that passes the fast lane's test, with the label `fast-lane` ("The fast lane") |
| Todo | Canceled | The owner | The owner drops an issue before it starts, a fast-lane issue included |
| Triage | Backlog | The owner | The owner parks the issue: it waits for a condition or for an open blocker |
| Backlog | Todo | The owner | The condition holds, or the last blocker closed |
| Todo | In Progress | The implementer | At its start, before the first change: the move claims the issue, so no second run takes it (Linear's "Draft PR" automation moves it too, later). Before the move, the orchestrator's run line with its start token holds the issue for 10 minutes against every other starter ("Until then the claims have windows") |
| In Progress | Todo | The implementer | It found a task that must come first (the issue is now blocked by it), or its base is on another issue's open pull request (the run linked the issue as blocked by that issue: implementer.md "While you work"), or it is a draft stack whose blocker went to Escalated or Canceled (implementer.md "A draft stack"), or the issue is harder than its difficulty labels say and it added the missing label before its first push (implementer.md "While you work") |
| In Progress | Backlog | The implementer | A flake issue whose test the hunt cannot make fail again: the trigger line and one comment on what it tried ("A flake that did not repeat") |
| In Progress | In Review | Linear, on the ready mark | The pull request is marked ready for review: always the implementer's last step before the review, after every fix is pushed (Linear's "PR opened" automation; the implementer moves it itself when that did not fire) |
| In Review | In Progress | The reviewer | The verdict is `request-changes` |
| Approved or Escalated | In Progress | The workflow `gates-failed` | The review approved the head (the check `verdict` passed), but a required gate of that head ended with no pass: it failed, was cancelled or timed out, or was skipped (GitHub counts a skipped required check as passed, so a gate job never skips as a whole: "The guards"). The check `verdict` holds only the review, so the approve stands and the ruleset holds the red gate. Nobody watches the pull request after its approve, so the workflow comments once for the head with verdict.py's `FAILED_GATES` words and the head's hash, takes the label `escalated` off a pull request that waited for the owner's merge, and moves the issue. It starts on the end of a gates run that did not pass and on the end of a review, and reads every open pull request: the approve and the failed gate come in any order. A merged or closed pull request keeps its issue |
| In Progress or Escalated | Approved | The workflow `verdict-rerun`, on the owner's rerun or on the rerun of `gates-retry` | The implementer moved the issue to Escalated and waited (`implementer.md` "When a review asks for changes"), or the owner reran the gates before an implementer run escalated it (the issue is still in In Progress). The owner reran the failed gates and they passed on the same head that the reviewer approved, and the last comment that starts the rounds again is the failed gates' one (`gates-failed`'s: the check `verdict` itself holds only the review, so it passed already, and the workflow writes it again). The rerun is the owner's answer (or `gates-retry`'s rerun of a setup failure or of a `web` job with only flaky tests, which needs no answer), which the workflow carries to Linear, like `owner-approved`: the job takes the label `escalated` off the pull request, writes the check `verdict` from that approve, with no new review and no new round, moves the issue to Approved and turns on auto-merge (a code owner's file: the same, when the owner's label `owner-approved` stands; else no auto-merge, the label `escalated` goes on, and the issue goes to Escalated or stays there for the owner's merge, as `report` does). The label stands when GitHub's issue events show its last event is `labeled` by the repository owner and newer than the first gates run of the head: never a review's or a comment's text (verdict.py `labeled`). The workflow exists only once its file `.github/workflows/verdict-rerun.yml` is on main (the implementer's app cannot push a workflow); before that, this row does not happen (a pull request that changes a workflow file and that GitHub can merge at once gets one comment and no merge call: the owner merges it) |
| In Review | Approved | The reviewer | The verdict is `approve` (with the `gates` workflow off, the gates it ran are green: below) |
| In Review | Escalated | The reviewer | The verdict is `needs-decision`, also for a clean review when the issue's "Needs the owner" names a step before the merge that is not done yet (a check of the result, never a choice; after `owner-approved` or the owner's comment, a new round 1 gives its normal verdict; the review workflow adds the label `escalated`) |
| Approved | Escalated | The review workflow | The approved pull request changes a code owner's file: it gets no auto-merge and waits for the owner's merge on GitHub (the workflow adds the label `escalated`) |
| Approved | Escalated | The review workflow's job `report`, and the workflow `verdict-rerun`'s job `rerun` | GitHub refused to turn on auto-merge on every try, with "Base branch was modified" (another pull request merged at the same time) or with a 502, 503 or 504 answer (GitHub's own failure); the job tries 5 times, 10 s apart, and before each new try it reads the pull request: auto-merge on or a merge already (a 5xx answer can come after GitHub did the work) ends the step with success, with no label and no move; after the last of the 5 failed tries the workflow adds the label `escalated` and comments on the issue; `rerun` does the same, with the same 5 tries and the same read before each new try, after it moved the issue to Approved. The owner runs the failed job again, turns on auto-merge or merges on GitHub |
| In Progress | Escalated | The implementer | It has a question only the owner can answer (`implementer.md` "Asking the owner"; it adds the label `escalated` to the pull request when there is one) |
| In Review | Escalated | The implementer | A second ready mark also started no review (`implementer.md` "When you are done" step 6; it adds the label `escalated` to the pull request). The owner's action (the owner's call, the triage of 2026-11-09): look at the Actions tab first, since the review watchdog may have started the review already. When no review runs, start it by hand with the round left empty, `gh workflow run claude-code-review.yml -f pr=N`, so the workflow counts the round from the earlier reviews; then move the issue back to In Review. Never a third ready mark and never `round=1`: both can count the round wrong |
| Escalated | Todo or Canceled | The owner | The owner answered on the issue: Todo to have the answer implemented, Canceled to drop it (the orchestrator closes the open pull request 10 minutes later, `cancel_grace_minutes`, unless the issue is `hands-off`; with no orchestrator running, the owner closes it on GitHub; Done means merged) |
| Escalated | Approved | The review workflow, on the owner's label | The owner accepts the pull request as it is: the owner adds the label `owner-approved` to the pull request on GitHub ("The guards"). The workflow posts an approve for the owner on the labeled head, writes the check `verdict` from it, moves the issue to Approved and turns on auto-merge, also for a code owner's file (the label is the owner's merge too). A move to Approved in Linear alone gives the pull request no check `verdict` (a pull request that changes a workflow file and that GitHub can merge at once gets one comment and no merge call: the owner merges it) |
| Approved or Escalated | In Progress | The workflow `branch-update` | GitHub refused the branch update of a pull request that conflicts with main: the workflow comments on the pull request and turns its auto-merge off, and takes the label `escalated` off a pull request that waits for the owner's merge |
| Approved or Escalated | In Progress, Approved or Escalated | The reviewer | A real commit came after the approve, so `carry` (or `report`, for a push while the review ran) started a new review as round 1: the issue stays in Approved, or in Escalated for a pull request that waits for the owner's merge, while it runs, and the verdict moves it as from In Review |
| In Progress | Escalated | The review workflow | A fourth ready mark without the owner's answer (three rounds at most): no review, the label `escalated`, and a comment on the pull request and the issue with the owner's four actions and what to type for each: move the issue to Todo and, until an orchestrator exists, start the run with `/implement KEY`; run the review by hand with round 1 after a fix of the owner's own; add the label `owner-approved`; or move the issue to Canceled and close the pull request |
| Approved or Escalated | Done | Linear, on the merge | GitHub's auto-merge merged the pull request (one that changes a code owner's file only after the owner's label), or the owner merged it |

No move in this table comes from a review of a pull request that merged or closed: each step that moves an issue
from a review (the reviewer's own move, the review workflow's job `report` and a fourth ready mark, the workflows `gates-failed` and
`verdict-rerun`) reads the pull request's state first, and a merged or closed one keeps its issue as it is (`verdict.py
ended`). The close of a pull request, a merge included, also cancels the review that runs or waits on it. A reviewer that
ends after the merge files its findings as a follow-up issue, the blocking and `fix_now` ones marked so (`reviewer.md`
"Follow-ups").

The required checks are the jobs of the product's `gates` workflow that its AGENTS.md names, and `verdict`, all
required by the ruleset `main` ("The guards"), so GitHub refuses a merge without them. "Green" means that each gate job passed. On a docs-only
change (`.github/code-changed.sh` prints `false` for the pull request's files) the steps of each gate job are skipped and the
job passes; the product's AGENTS.md, or the file that it points to, names any other change whose gate steps skip. A skipped
or cancelled gate job did not run and counts as a failure, though GitHub counts a skipped required check as passed. With the workflow on, the review does not wait
for the checks, before the model or after it: the check `verdict` holds only the review (the owner's call, 2026-11-25:
OWL-30), the ruleset holds a red gate, and the workflow `gates-failed` sends an approved head whose gate did not pass
back to an implementer. The review job ends when the model ends.
A product's gates workflow never skips a gate job as a whole: the job always runs, and on a docs-only change its steps
are skipped and the job passes. So a skipped check never stands for a gate that did not run, and a failed planning job fails
the gate job in its first step instead of skipping it (this repository's `gates.yml` is the example).
The pull request has no required checks only when the `gates` workflow is disabled: then the reviewer runs the gates on
the branch merged with main, and green means that the verdict's `gates` lists every required gate with exit code 0
(`reviewer.md` "The gates"). The required gates come from the file names of the pull request's diff against main:

- When `.github/code-changed.sh` prints `false` for the file names, no gate is required, and `gates` may be empty.
- When it prints `true`, each file that the script calls code requires a gate, as the product's AGENTS.md, or the file that it points to, says.

No agent merges a pull request: the review workflow turns on GitHub's auto-merge after an approve (not for a code owner's
file: the owner merges those), and GitHub merges
(AGENTS.md "Autonomous runs").

## The local review

Before its first ready mark, the implementer runs one local review of its own diff (`implementer.md` "When you
are done", step 2): `.github/local_review.py` gives the brief `local-review.md`, the issue, the author's note on what the
change leaves out, and the diff to one read-only model session, and prints its findings. The implementer fixes the true
ones whose fix is XS and stays in its own lines, and the pull request's text says how many it fixed and left out. The
local review gives no verdict and no check, blocks nothing (the script exits 0 when it did not run), and runs in no fix
round: the formal review, its rounds and the merge bar do not change. Its command (the read-only tools and how the
harness runs them) is a setting, as a run's own model is: the environment variable `LOCAL_REVIEW_COMMAND` holds it, and
every session gets it from `.workflow/agent_user.py` (`keys_env`, so the herdr pane's `claude` alias, Paseo's daemon and
`run` all hold it, whatever starts the session). That file is the one definition of the command's shape and
tools. The orchestrator builds the start command and does not set a local review command. The command follows the session's inference profile
(`PROFILES` in that file, which the orchestrator's `inference_profile` names): on `claude`, Claude Code on the
subscription with `--max-budget-usd 4`; on `pi-ollama`, pi's print mode on Ollama Cloud's OpenAI-compatible API
(`pi -p` with its read-only tools and `--no-approve`); on `pi-chatgpt`, the same print mode on pi's own
provider `openai-codex`, the owner's ChatGPT Plus subscription; on `pi-lithosai`, the same print mode on
LithosAI's API, with the agents' own LithosAI key. The model and the effort level that the
review runs on are the orchestrator's config's, of the profile in force: `review_model` and `review_effort` of its
`[profiles.NAME]` table, one for that profile whatever the issue's labels (the owner's call of 2026-11-21: a
review on another model than the run's, and on another family than the GitHub reviewer's, finds other faults).
`owlcode install` and `restart` write them as JSON beside the installed `agent_user.py`, which reads that
file: the script runs on Python 3.9, which reads no TOML. A review model that the file does not name stops
every start with a refusal that names the command that writes it. The review model and effort must match the catalog.
`agent_user.py user` writes pi's custom provider entries from it. The command reads the profile's key from the agent's
keychain into its own review command, as a Paseo agent's shell may hold none (measured 2026-11-12); a profile
that runs on pi's own sign-in reads no key, because pi holds that sign-in itself. After a change of the command's shape
(`PROFILES`), run `owlcode admin` (it takes the agents' user from `[service] user`; it copies the script to the agent's home;
`owlcode install` updates the service's copy); a change of a profile's review settings needs only
`owlcode restart`, which writes the file (it holds each profile's `cells`, the profiles that its cells run on:
the Paseo daemon also holds their keys). The service restarts Paseo's daemon by itself when no agent runs after
a change of the profile in force, of the profiles that its cells name, or of their review settings: the
daemon's record holds a digest of the profile set's entries in `local-review.json`, and the service compares it with the file
each round. You run nothing as the agents' user. With no command set, the review does not run and the pull request says so.

## Review rounds

Rounds are counted since the owner last answered the issue in Escalated (its move from there to Todo, Approved or
Canceled, or to In Progress when no request-changes came during the stay: `verdict.py answered`), or since the
pull request was first reviewed: the reviewer's earlier verdicts on the pull request after that
moment, plus one. So the owner's answer starts the count again: the next review is round 1, of the whole pull request,
with the answer as part of the issue. Three events on an approved pull request start it again too, each with a comment
on the pull request, and the count starts at that comment: a conflict that the branch update found (the owner's call,
2026-10-28), and failed gates on the approved head (the owner's calls, 2026-10-28), and a real commit after the approved one. The review after the fix, or the new review, is round 1, of the whole pull request.
Only these comments count, each after an approve that is still the reviewer's last verdict: the implementer app's (the
workflow `branch-update`, and the workflow `gates-failed` for gates that did not pass on an approved head), its first
words those of `CONFLICT` or `FAILED_GATES` in `.github/verdict.py`, and the
reviewer app's (the jobs `carry` and `report`), its first words those of `NOT_CARRIED` or `PUSHED`. The review workflow counts the round and gives
it to the reviewer; after the owner's answer the owner runs the workflow by hand with round 1. That run starts the count
again: before the model runs, it posts the reviewer app's comment whose first words are `MANUAL` in
`.github/verdict.py`, with the run's id, and the count starts at that comment. So the run's own verdict is round 1, and
the ready mark after it is round 2. The comment counts only for a `workflow_dispatch` run that the repository owner
started for this pull request with round 1 (the run's title names both: the workflow's `run-name`), posted while that run
ran, once per run: the model posts with the same app's token, so the comment alone
cannot start the rounds again. A run by hand with round 2 or 3 posts none. A fourth ready mark
without an answer gets a comment and no review, and the workflow moves the issue to Escalated (three rounds at most:
`ROUNDS` in `.github/verdict.py`).
Failed gates alone need no new round when the head stays the same. A run that failed only in a setup step (a failed download of
the mise binary, an action, apt or a cache: the branch is not at fault) runs its failed jobs again once, with no person: the workflow
`gates-retry` (the owner's call, 2026-11-08; the rule is `.github/gates_retry.py`, and a failed gate is never rerun, but for
one case). The case: a `web` job that failed in the browser suite with no test failed after its retry (a flaky test, a test that
did not run, an error outside any test, as the suite's counts `run.json` say) runs again once too, and the workflow `flakes`
files the flake from attempt 1, so its issue is the record (the owner's call, 2026-11-16: three pull requests waited
for the owner's rerun alone in one night). A run
that GitHub failed at startup (conclusion failure, no job) runs again whole, once, on the same terms: `gates-retry` reruns it, and
when `gates-retry` failed at startup too, the review watchdog's next pass does. For any
other failure, and for a second failure, the owner reruns the failed job (only the owner can: the
apps have no "Actions: write", the owner's call, 2026-11-04), and when the gates then pass on the head that the reviewer
approved, the workflow `verdict-rerun` writes the check `verdict` from that approve, moves the issue back to
Approved and turns on auto-merge, with no review and no ready mark. That holds only while the file
`.github/workflows/verdict-rerun.yml` exists on main; without it, a ready mark is the way back and the review is round 1. Its
two steps only call `.github/verdict_rerun.py` (a change of the rules needs no workflow patch). A
head that changed after the approve never takes this way: it keeps the rules of `carry` and the new review.
No run has shown yet that `verdict-rerun` starts when the attempt 2 that `gates-retry` or the review watchdog started completes: GitHub starts no
workflow for most events that GitHub Actions' own token causes. So when that attempt 2 passes on an approved head
and no run of `verdict-rerun` follows it within 5 minutes, the owner reruns the gates once more (attempt 3 is the owner's), and
`verdict-rerun` then writes the check from the approve. `gates-retry` reruns a pull request's run only while its head is still
the pull request's head: a rerun keeps its run's concurrency group, and the rerun of an outdated head would cancel the gates of
the new one.

A ready mark can start no run. Within 2 minutes of each mark, whoever made it checks that a run of "Claude Code Review" started for it and that its job `start` did not skip. When none did, no review ran: mark the pull request ready once more, and that mark is no extra round. A mark whose review another run of the same pull request finished or posted after the mark (a second mark, a late `ready_for_review`) gets no second review: the job `start` writes no check, so `review` and `report` skip (OWL-8). A mark after a finished review still gets one. A run by hand is not covered: its payload has no time, so OWL-35 left it out.

- **Round 1** reviews the whole pull request.
- **Round 2** checks that each blocking finding of round 1 is fixed, and reviews only the lines changed since round 1.
  When blocking findings remain, the verdict is `request-changes`, and the implementer runs a second fix round.
- **Round 3** checks only the blocking findings that round 2 left open and the lines changed since round 2: no `fix_now`
  and no new non-blocking finding (the owner's call, 2026-11-16: on 2026-11-15, 1 pull request of 58 was still
  blocked after round 2, and the owner's words were "it could be solved by itself if we allowed a third round").
- When blocking findings remain after round 3, the verdict is `needs-decision`.

## Follow-ups

Non-blocking findings are the loop's leak: fixed in the pull request they bring a new review, which finds a new one.
These rules bound them at both ends.

- **The owner's approve files the lines of the last review** (OWL-19). A review that ends in `needs-decision` or in a
  request for changes writes its `follow_up` findings for the approve that follows. When that approve is the owner's label
  `owner-approved`, no model runs, so the job `report` (`review_report.py owner-approved`) reads the reviewer app's last
  review of its own and files those lines as the reviewer does: `linear.py follow-up`, priority low, in Triage. A review
  with no such line, or an approve of the reviewer itself (it filed its lines), files nothing. A finding that needs the owner's choice has `follow_up` true too and the verdict does not mark it, so it
  goes in as a line: the owner moves it by hand. A failure to file, or an
  issue with no Area, writes one comment on the pull request with the lines. The lines are not filed with the
  `needs-decision` itself: a fix round after the owner's answer can fix the same findings.
- **An approve is final.** The non-blocking findings open at the approve become the follow-up; nothing is pushed to the
  branch after it. A commit after an approve dismisses it (the ruleset) and starts the loop again: do not.
- **Round 1 fixes what is cheap now (`fix_now`).** Round 1 lists every finding. The reviewer marks a non-blocking finding
  `fix_now` when its fix is XS, stays in files the pull request already changes, and needs no choice the issue did not
  make. On a docs-only pull request (`.github/code-changed.sh` prints `false`) the size limit drops: each non-blocking
  finding whose fix stays in those files and needs no new choice is `fix_now`, whatever its size (the owner's call,
  2026-11-03, after a follow-up of a pull request held two small fixes of that pull request's own documents). A round 1 with a
  blocking or a `fix_now` finding asks for changes, and the implementer fixes both kinds in the fix
  round. Round 2 never marks `fix_now`: it checks that each one is fixed, and one still open becomes a line of the
  follow-up, never a block and never `needs-decision`. The implementer leaves the follow-up lines, and the reviewer
  never argues the ones left alone. (Measured 2026-10-28: all 7 depth-1 follow-ups that would have taken the
  fast lane pointed at a file their pull request already changed; each cost a new issue, pull request and review.)
- **One collecting issue per Area** (the owner's call, 2026-11-16: each follow-up issue came with its own pull
  request, review and gates run; on 2026-11-15, 16 of 54 issues Done were follow-ups, with a median of 18 changed lines).
  With the approve, the reviewer puts the findings into the open collecting issue of the pull request's Area:
  `.github/linear.py follow-up TEAM --area AREA --from "PR #N, round R, <head commit>, depth D" --body-file FILE
  --priority P` (with `--fast-lane` when the findings pass the fast lane's test), at every depth. That issue carries
  `follow-up` and the Area label and has not started. It is in Todo with `fast-lane` for findings that pass the fast
  lane's test, else in Triage without it, so no finding enters the fast lane by another's test. It holds at most six
  findings: the command creates a new issue when none is open or the findings do not fit. A new issue is titled
  `Follow-ups: AREA, from PR #N`, is size S, and has the state the fast lane's test gives. Each review adds one line
  `From: PR #N, round R, <head commit>, depth D` under the ones at the top (the first stays the issue's origin), and its
  findings under `## From PR #N`, one checklist line each with its id, the path and line, and one sentence. The issue's
  priority rises to the highest of its reviews. D is 1 when the pull request's issue does not carry `follow-up`; else it
  is that issue's depth plus 1. An issue's depth is the highest D of its `From:` lines, and a line that names no depth
  counts as depth 1. An issue that a run started takes no more findings: it counts as started from its first run line
  (`Run: ...`, which the orchestrator writes when it starts the session), not from its move to In Progress. So follow-ups collect while the queue is busy,
  and they run at once when it is idle, with no timer. The owner accepts it (Todo), splits it, merges it into a
  duplicate, or cancels it. The implementer that takes it fixes the whole list in one pull request and says in a
  comment which lines it left and why.
- **Every finding is acted on, or it is not written.** A finding is blocking, `fix_now` (round 1), or a line of the
  follow-up. Anything else is not written: a finding that nobody acts on is read by nobody (the owner's call,
  2026-10-28). That bucket also hid misjudgements: on 2026-10-28 two round-2 findings went there as "wording" while each
  one said that a document was wrong, which passes the floor.
- **A finding that needs the owner's choice gets its own issue and a `Question:` blocker** (the owner's call,
  2026-11-23: a work issue never holds an open choice). Its fix needs a product, design, cost or security
  choice that nothing records, so it is no line of the collecting issue: one open blocker there would hold the other
  findings. With the approve, the reviewer files it as its own work issue, as any other follow-up: in Triage, or in
  Todo with `fast-lane` when it passes the fast lane's test ("The fast lane"). It has `follow-up`, the Area label,
  a priority and a size, its first line the review's `From:` line, and no open choice in its text. Then it files a
  `Question:` issue that blocks it, in Escalated and assigned to the owner (planner.md "The issue", State;
  reviewer.md "Follow-ups"). The owner answers on the question and moves it to Done. A work issue in Triage leaves it
  by the owner's move as any other; in Todo or not, the orchestrator starts it only after the question is closed.
- **The floor.** A finding becomes a line of the follow-up only when a person would schedule it: a test that is missing,
  a behaviour that is wrong where nothing reaches it yet, a rule a document should hold and does not, a cost that grows
  with use. A wording preference, a name, a comment or a structure the reviewer would write differently is not written.
  A finding about a property of the merge bar (security or privacy, data that can be lost, a documented promise, a
  wrong behaviour that nothing reaches yet) always passes the floor, confirmed or not; an unconfirmed one keeps its "To
  confirm" step in its line. Five lines at most, the most useful first; when more pass the floor, the review says in one
  line how many it dropped.
- **The depth rule.** When the pull request's issue carries `follow-up` (depth 1 or more), a finding becomes a line only
  when it is about a property of the merge bar (the list above). A missing test, an input that no user can reach and a
  preference are not written. (On 2026-10-28 chains reached depth 3 (an issue, its follow-up and the follow-up of that), mostly
  on missing checks and inputs nothing reaches.)
- **Rule 2 and the depth rule bound the chain**. A follow-up of a follow-up is
  filed like any other and its `From:` line shows the depth. It takes the fast lane when it passes the test ("The fast
  lane"), but rule 2 keeps a fast-lane issue one step from an issue that the owner moved to Todo, and the depth rule
  keeps a deeper follow-up small (only a finding about the merge bar is written). The owner cancels a chain at any time
  (Todo to Canceled). (Until 2026-10-26 a follow-up had no
  follow-up; 6 of 10 such pull requests had findings worth scheduling, and the owner took 5 of them by hand.)
- **An unconfirmed finding never blocks.** A finding the reviewer could not confirm in the code or by a run is listed
  under "To confirm", with what would confirm it; the implementer measures it. A block on a memory of a tool's behaviour
  cost a live measurement and a round once.

## The fast lane

An issue that an agent creates goes to Todo, with the label `fast-lane`, when all of these are true (the owner's call,
2026-10-28; widened to any depth and any Area by the owner on 2026-11-09). Otherwise it goes to Triage.

1. It is a follow-up, at any depth, or work found on the way (`Found in KEY`), a task that must come first included.
2. The issue the run works on does not carry `fast-lane`. So every fast-lane issue is one step from an issue the owner
   moved to Todo, and no chain grows while the owner is away.
3. Its size is XS or S.
4. It makes no change that is the owner's, and its "Needs the owner" is empty. The owner's changes are those that
   `.github/verdict.py owned` gives the owner: a file that `.github/CODEOWNERS` names, AGENTS.md's section
   "Autonomous runs" or a `## ` heading line of AGENTS.md added, removed or changed, and in `limits.toml` a
   changed or removed value or a new key of `OWNER_LIMITS` or its prefixes `OWNER_LIMIT_PREFIXES`.
5. It does not carry `hands-off`.

Its Area can be any: rule 4 keeps the owner's changes out.

A collecting follow-up ("Follow-ups") takes the test as one issue of size S: the reviewer's findings join a
fast-lane collecting issue only when they pass the test themselves (`--fast-lane`), and the others join one in Triage.

There is no cap: rule 2 and the depth rule bound the volume. The owner stops the fast lane by stopping the runs, and cancels a fast-lane
issue at any time (Todo to Canceled). A fast-lane issue passes the same review and gates as any other. Measured on
2026-10-28: the agents filed 25 of 29 new issues; the owner accepted 13 of the 14 decided; 13 of the 25 would have taken
the fast lane, and the owner cancelled none of them. Five follow-ups waited 4 to 6 hours in Triage while nothing ran.

## The guards

What keeps an agent from merging its own work, or from changing the rules that would let it.

- **The ruleset `main`** (GitHub Pro; made by the owner's setup script `workflow/github-setup.py`): a pull request for every
  change, a review from the code owners where CODEOWNERS names them (it let three such pull requests through on the
  reviewer app's approve alone; so the workflow turns on no auto-merge for those files), the status checks of the product's gates and `verdict`
  green (the branch need not be up to date: the owner's call, 2026-11-02), merge commits only, no force-push, no deletion. The owner (the repository admin
  role) is on the bypass list: a merge by hand is always possible.
- **The check `verdict`, not an approval.** GitHub counts an approval only from an account with push access, and the
  reviewer's app has none (measured 2026-10-25: its approve left the pull request at REVIEW_REQUIRED). So the
  review workflow reports the verdict as the check `verdict` on the reviewed head (`.github/verdict.py`): success for an
  approve, failure for anything else, a failed run or a dismissed approve included. Only its jobs `start`, `report` and
  `carry`, and the workflow `verdict-rerun`'s job `rerun` (the approve's check after the owner's passed rerun of
  the gates), write a check, with the reviewer app's token, and they run main's code only: no other job and no other
  workflow may write one, and no job's GitHub Actions token may (tests/test_ci.py). Never with GitHub Actions' token:
  GitHub puts that check in a check suite of the head that already exists, and when that is a run whose jobs all skipped
  (a draft's `opened`), the ruleset ignores the check (measured 2026-10-25). An app's check gets its own suite.
  The ruleset requires `verdict` from the reviewer app, so a check of that name from anything else does not count.
  GitHub never lets the author approve, and the owner is the git author of every pull request, so **the owner's Approved
  is the label `owner-approved`** (the owner's call, 2026-10-30). When the repository owner adds it, the job
  `start` posts an approve for the owner with the reviewer app's token, on the head the owner labeled; its verdict JSON
  carries `approved_by`. The review runs no model then, and `report` writes the check from that approve, moves the issue
  to Approved and turns on auto-merge, as for the reviewer's approve. For a code owner's file too: the owner saw the
  head, so the label is also the owner's merge, and the issue stays in Approved until GitHub merges. Only
  `start`'s output `owner` (main's `verdict.py owner` took the label) lifts the guard in `report`, and in `verdict-rerun` main's `verdict.py labeled` does, on GitHub's issue events; a later real commit gets no check
  from the label, and its review's approve waits in Escalated again. Main's
  `verdict.py owner` checks the account: the one that added the label and the one that started the run must both be the
  repository owner. A label from anyone else, an app included, gets a notice in the run's log and nothing more. The
  approve covers the labeled head only: a later push gets the rules of any approve in `carry`. To approve a
  later head, the owner removes the label and adds it again. The review job holds the reviewer app's token, so it runs no file of the
  branch either: main's copies of `linear.py` and `code-changed.sh`, Python isolated (`-I`), and the model without the
  project's settings. The implementer's app can push any file outside `.github/workflows/`, so a branch's script must
  never run beside a token that can post an approve or write a check (a review finding).
- **`.github/CODEOWNERS`** names the owner for the guards: the files that check the agents' work, run beside a key, or
  ship. These are `.github/` (the workflows, `verdict.py`, `code-changed.sh`, `attribution.sh`, and `linear.py`, which
  the review job runs beside the reviewer app's token), `.githooks/`, `.workflow/`, `.claude/`, `.agents/` and `.pi/` (the
  hooks and the settings), each skill's `SKILL.md` (a skill's frontmatter can grant tools or declare hooks in the owner's
  session: the owner's call), `agents/reviewer.md` and `agents/verdict.schema.json`, and the product's own
  files that CODEOWNERS names (its tool and package files, its deploy: the product's AGENTS.md "Autonomous runs"). `orchestrator/deploy/` (the orchestrator's service) and `.workflow/agent_user.py` merge on the verdict like the rest of `orchestrator/`: they run only when the owner runs them, and the owner checks them then, through the launcher `.workflow/approved_run.py` (the owner's), which shows what changed since the owner's last yes and asks one yes (the owner's call, 2026-11-23). A skill's other files check nothing, so
  they merge on the verdict. A new file in `agents/`, `.claude/`, `.agents/` or `.pi/` is the owner's until CODEOWNERS says otherwise. A pull request that touches one needs the owner's review
  (the workflow `branch-update` sends a conflict with main back to its session while it waits: "The loop" step 5),
  not the reviewer app's: the owner merges it, or adds the label `owner-approved`. An issue that needs a file under
  `.github/workflows/` goes to Escalated with the run's patch of that file (implementer.md step 6). The workflow's prose
  (`agents/README.md`, `implementer.md`, `planner.md`) and
  DECISIONS.md check nothing and run nowhere: they merge on the verdict (the owner's call, 2026-10-28: 4 of the
  10 pull requests the owner merged by hand that night changed no guard). `limits.toml` is the owner's only when a change alters or removes the value of a key, or
  adds a key of the owner's list in `.github/verdict.py` (`OWNER_LIMITS` and `OWNER_LIMIT_PREFIXES`: the money, plan and
  sandbox limits): a new key that is not on that list, a change to comments or blank lines, and a line moved with its value
  merge on the verdict (the owner's call, 2026-11-04). AGENTS.md is the owner's only when a change touches its
  section "Autonomous runs" (the merge bar, the rounds, the guards: up to the next `## ` heading) or adds, removes or
  changes a `## ` heading; every other change to it merges on the verdict (`section_owned` in `.github/verdict.py`, which
  finds the section in main's copy; a hunk it cannot place is the owner's; the owner's call, 2026-11-09).
- **The review reads main's rules.** The review job copies main's AGENTS.md, DECISIONS.md and `agents/` to
  `review/rules/`, and the prompt names those copies as the rules. The same files in the checkout are the change under
  review. So a pull request that changes `agents/README.md` or a brief cannot change the rules of its own review.
- **The GitHub Apps.** Each role acts with its own app's token, so a reviewer can approve an implementer's pull request
  and the ruleset counts it. The implementer's app has read and write access to code and pull requests, read access to
  actions, checks, commit statuses and metadata, and no Workflows permission: it cannot push a change under
  `.github/workflows/`, and that refusal is on purpose: a workflow could report the check `verdict`. The reviewer's app has read and write access to pull requests and
  checks, and read access to code, actions, commit statuses and metadata: it cannot push. Its token in the review job
  reads the checks only (the model writes no check). The implementer app's Client ID and key are also in GitHub Actions
  (the variable `IMPLEMENTER_APP_CLIENT_ID`, the secret `IMPLEMENTER_APP_PRIVATE_KEY`), and only five steps that
  run main's code name the key (tests/test_ci.py `KEY_STEPS`): one in the review workflow's `report`, which turns on
  auto-merge with it (GitHub merges as the account that turned auto-merge on, and a merge by GitHub Actions' token starts no
  workflow); the workflow `branch-update`'s job `update`, which asks GitHub to merge main into a branch; the workflow
  `gates-failed`'s job `failed`, which comments on an approved pull request whose gates did not pass and reads the pull
  requests and their checks, with no contents permission; the workflow `attribution`; and the workflow `verdict-rerun`'s job `rerun`, which turns on auto-merge after the
  owner's passed rerun of the gates on an approved head. GitHub, not the app, writes that merge, so
  an update that brings in a workflow change is accepted (measured 2026-10-28). GitHub Actions' own token cannot
  approve a pull request (the repository setting is off). It does rerun failed jobs: the workflow `gates-retry` (the owner's call,
  2026-11-08) reruns the failed jobs of a `gates` run with that token (`actions: write`, no app key), only from main's copy and its
  rule `.github/gates_retry.py`, only in attempt 1, and only when every failed job failed in a setup step (a download: the mise binary,
  an action, apt, a cache) or is `web` failed in the browser suite with no test failed after its retry (a flaky test,
  a test that did not run, an error outside any test; the flake's issue is the record), or the whole run when GitHub failed it
  at startup with no job. Any other failed gate is never rerun, and a second failure is the owner's. Neither app can read Bitwarden or reach the server.
- **The git hooks** stay the guard on every clone (no commit on main, no push to it, the fast checks before a push).
- **The guards of a run**. Two guards stop a tool call before it runs. The edit guard
  (`.agents/guards/edit_in_worktree.py`) refuses a change to a file of the main checkout (the commit hook is the second
  guard). The kill guard (`.agents/guards/kill_by_pid.py`) refuses a kill by name or of a process group, so one run
  cannot stop another run's server (agents/implementer.md "While you work"). Both run from one entry point,
  `.agents/guards/guard.py`, with one contract for every harness. The input is one action as JSON: `{"action": "edit",
  "path": FILE, "cwd": FOLDER}` or `{"action": "shell", "command": COMMAND, "cwd": FOLDER}`. The answer is `{"allow":
  true}` or `{"allow": false, "message": TEXT}`, with the text that the agent sees. An action of another kind passes.
  From a shell, the action goes on stdin and the answer comes on stdout; from Python, it is `guard.decide(action)`.
  Standard library only, on Python 3.9 too (macOS's own `python3`). Each harness needs an adapter that turns its tool
  call into an action and the answer into its own refusal. Claude Code's is `.claude/hooks/adapter.py`, a `PreToolUse`
  hook of `.claude/settings.json` for Edit, Write, NotebookEdit and Bash. pi's is `.agents/guards/pi.ts`, an
  extension that `.pi/settings.json` names, on pi's `tool_call` event for `edit`, `write` and `bash`; pi loads it only in
  a project that it trusts, and `agent_user.py user` trusts the agents' clones. Its tests run with a
  product's Node unit tests, the job that has Node. A harness with no adapter runs without the guards:
  the issue that adds a harness adds its adapter.
- **The review watchdog** (`.github/workflows/review-watchdog.yml`, `.github/review_watchdog.py`). The ruleset
  requires the check `verdict`, and only the review workflow writes it, so a ready mark that starts no review leaves the
  pull request waiting with nobody told (one waited about four hours). Every 15 minutes the watchdog reads the open
  pull requests. It acts on a pull request that is not a draft, whose head has no check `verdict` and no queued or running
  review, 15 minutes or more after its last ready mark or push. When the owner approved that head on GitHub and the label
  `owner-approved` is not there, it comments once that the label is the owner's Approved here; it never adds the label.
  A pull request with `escalated` and no such approve waits on the owner: nothing. Else it starts the review again by
  `workflow_dispatch`, once per head, and says so in a comment; when that review leaves no check either, one comment for
  the owner, and no more starts. Each comment holds a marker for the head, which is the watchdog's only state. It runs
  main's code only, on the runner of `LOOP_WATCH_RUNNER` (bullet "The runners"), writes no check, and comments with the reviewer app's token (pull requests write,
  contents and checks read); GitHub Actions' token reads the review workflow's runs and starts the review. The two steps
  of the workflow only call `review_watchdog.py watch` and `rerun-gates` (a change of the rules needs no workflow
  patch). Each pass also
  reruns, once, the `gates` run of an open pull request's current head that failed in attempt 1 with no job, 5 minutes or
  more after its end (a failure at startup that `gates-retry` missed: one waited two hours), with GitHub
  Actions' token; a run with a job, red or not, is left as it is. A review can give its verdict and then fail in its job
  `report`, which completes the check: the head keeps "Under review" in progress (one waited 4.7 hours).
  So when the reviewer app approved or requested changes on the head 10 minutes ago or more, the head has no completed
  check `verdict`, and no review runs for it, the watchdog reads the pull request's latest review run. When that run
  failed in attempt 1 and `report` is its only failed job, GitHub Actions' token reruns the failed jobs of that run, once
  per head, with a comment: the job `review` passed and does not run again, so there is no second review and no new
  round. Otherwise (attempt 2, no such run, another job failed too, or a rerun that did not start within 10 minutes) it
  writes one comment for the owner, once per head.
- **No agent attribution line** (AGENTS.md "Rules"). A commit with one is refused: by the commit-msg hook
  before the push, and by the gates' job `changes`, which fails `python` and `web`, since a pushed commit cannot change. In the loop's own repository (owlcode) that job's step runs `.github/gates-attribution.sh`, which a product does not copy: there the job turns red and fails `python` in its first step (a gate job never skips as a whole), and the ruleset holds the red gate.
  A pull request's text with one is cleaned: the workflow `attribution` runs at the opening and at each edit (a push keeps the text), reads the text again
  from GitHub, writes it back without the lines with the implementer app's token (pull requests write, checks read), and
  passes (the owner's call, 2026-11-09: the harness adds a session link at `gh pr create`, and 11 gates runs
  failed on it in ten days). The hook, the gates' job `changes` and the workflow `attribution` read one pattern list: `.github/attribution.sh`. The
  hook reads the clone's copy, the job the branch's copy and the workflow main's copy. The workflow `attribution` is a job of this repository. It runs on
  `pull_request_target`, so GitHub runs main's copy. It checks out main's `.github/attribution.sh` with no credentials:
  a pull request cannot change the patterns that check its own text. It makes the implementer app's token only when it
  finds a line, and `.github/attribution-clean.sh` removes the lines.
- **The runners** (DECISIONS.md "Delivery"). Each repository chooses the runners of the review's `review` and the review watchdog's `watch` with
  a repository variable: the review's `review` reads `LOOP_REVIEW_RUNNER` and the review watchdog's `watch` reads
  `LOOP_WATCH_RUNNER`. With no variable, both run on GitHub's `ubuntu-latest`. A product that wants Namespace sets
  each variable to its profile (`gh variable set LOOP_REVIEW_RUNNER -R OWNER/REPO --body PROFILE`) and gives
  Namespace's GitHub app access to the repository: with no access, a job on a Namespace profile waits in `queued`
  until GitHub drops it after 24 hours. The owner's profiles: `namespace-profile-2c4g` (2 vCPU) for `review`, since a
  review mostly waits for the model (four hand reviews of one pull request took 2 min 8 s and 2 min 38 s on 4 vCPU,
  2 min 48 s and 2 min 13 s on 2 vCPU), and `namespace-profile-maurolopes` (4 vCPU) for `watch` and the product's own
  long jobs. The other short jobs run on GitHub's `ubuntu-latest` (the review's
  `start`, `report` and `carry`, branch-update's `update`, gates-failed's `failed`, verdict-rerun's `rerun`, gates-retry's `retry`,
  `attribution`, the gates' `changes`): a short job waited minutes behind the gates for a Namespace machine. The
  watchdog's `watch` runs 96 times a day, and GitHub bills each job as at least a minute: a repository that
  pays for GitHub minutes sets `LOOP_WATCH_RUNNER`. tests/test_ci.py names each job's runner, so a new job chooses one
  on purpose. The product's own jobs and their containers are in the files that its AGENTS.md points to.
  The short jobs run main's `.github/*.py` scripts with the runner's `python3` (3.12), not the project's 3.14:
  a test parses `verdict.py`, `linear.py`, `branch_update.py`, `gates_retry.py`, `gates_failed.py`, `review_steps.py`, `review_report.py`,
  `review_jobs.py`, `verdict_rerun.py` and `review_watchdog.py` as 3.12 syntax, so a 3.13 syntax fails the gates. It
  holds the syntax only, not a library call new in 3.13; the other choice, `actions/setup-python`, costs seconds in every short job.

### Fork pull requests

On a public repository anyone can open a pull request from a fork (OWL-53). The loop does not review such a pull request:
the owner reads it and merges it by hand. Each workflow is made so that a fork's code never runs beside a secret, and the
rules below hold for the seven workflows that run main's copy with the secrets, on `pull_request_target` or `workflow_run`
(tests/test_fork_pull_requests.py). The review workflow and the watchdog hold secrets too: the review's job `review` reads the
head only as text, with main's scripts run in isolated mode ("The review reads main's rules" above), and the watchdog runs on
`schedule`, so it reads no pull request's file.

- **What GitHub gives a fork's event.** On a public repository, a `pull_request` event of a fork runs the fork's copy of the workflow file with
  no secret and a read-only `GITHUB_TOKEN` (GitHub's documentation for `pull_request`; it does not say whether the repository's
  variables reach such a run, so this section holds no rule on them). The gates run so, on GitHub's runner.
  A `pull_request_target` or a `workflow_run` event runs main's copy of the file with the repository's secrets
  (`CLAUDE_CODE_OAUTH_TOKEN`, the apps' keys, `LINEAR_API_KEY`, `WORKFLOW_PUSH_TOKEN`), also when a fork's pull request
  starts it. So these workflows hold the rules, one property each: a step that holds a secret or a write token never runs,
  sources or reads as code a file of the pull request's head; and no value of the pull request (title, body, branch name,
  comment) goes into a shell line: it goes through `env:`.
- **The review does not start.** The job `start` refuses a head in another repository with a notice line: the step
  `access` reads the event's payload (a fork gets no secret anyway, so its key would look unset), and the step `open`
  reads `isCrossRepository` from GitHub for a run by hand and the watchdog's dispatch. No check `verdict` is written and
  no `check` output exists, so the jobs `review` (on `LOOP_REVIEW_RUNNER`, the paid runner) and `report` never start in main's
  copy of the workflow. The owner's label `owner-approved` gets no approve on a fork's pull request either. This skip covers
  main's copy only: a fork's `pull_request` run uses the fork's own workflow files (see "What the owner sets on GitHub").
- **The scripts leave a fork out.** The review watchdog, the branch update, gates-failed, gates-retry and verdict-rerun
  read the open pull requests (or a run's head) and skip a head in another repository (`isCrossRepository`), so they
  start no review, rerun no gates run, comment on no pull request and turn on no auto-merge for a fork. The workflow
  `flakes` files nothing for a run whose head repository is not this one: the fork's file of flaky tests is the fork's
  text, and the issue would go to Todo with the fast lane, where an agent starts it.
- **What stays.** The workflow `attribution` runs main's copy with the implementer app's token (pull requests write) and
  edits a fork's pull request text, as it edits any other: it reads the text as data and takes the script from the base
  commit. The workflow `apply-workflow-patch` runs only for the repository's own owner and for a head in this repository
  (its `if`); it checks out the branch into `branch/` as data, never runs a file of it, and the script is main's.
- **What the owner sets on GitHub.** Settings, Actions, General, "Fork pull request workflows from outside collaborators":
  "Require approval for all external contributors". A fork's workflow run (the gates, which run the fork's code on
  GitHub's runner) then waits for the owner's approval of that run, for every outside account. This approval is the only
  guard of the paid runner against a fork that edits a workflow file: the fork's `pull_request` run takes its workflow
  files from the fork, so it can drop the `start` check and name any runner label, the paid one (`LOOP_REVIEW_RUNNER`'s
  profile) too. Before you approve a run, read the diff for any change under `.github/workflows/`, a `runs-on:` line above
  all, and approve nothing that changes one. The default holds the runs of first-time contributors only. The settings "Send write tokens to workflows from pull
  requests" and "Send secrets to workflows from pull requests" are for private repositories only (GitHub's documentation);
  on a public repository a fork's `pull_request` run gets no secret and a read-only token whatever the owner sets. The
  loop's tokens do not read the approval setting, so it is the owner's step (the issue that publishes the repository
  lists it).

## What can start now

One rule for the orchestrator and for the owner's own start. An issue can start when it is in Todo, carries no
`hands-off` label, has no blocker that is still open, and is not size L (an L issue is more than one pull request: the
planner splits it first). A fast-lane issue in Todo starts like any other. A follow-up (its `From: PR #N` lines at the
top, one for each review it collects: "Follow-ups") starts only when none of those pull requests is open: the reviewer
files it with the approve, before the merge, and main has the change only after it (a follow-up started twice on a main without the change, and then waited for
the owner; of 21 fast-lane follow-ups, 8 had a pull request that merged 15 minutes or more after the filing). A
follow-up of a pull request that was closed with no merge starts too. An issue with the Area `workflow` starts and
resumes like any other, on the implementer app's token (the owner's call, 2026-11-16: 43 of the 80 `workflow`
pull requests merged from 2026-10-26 changed no file under `.github/workflows/`). The app has no Workflows permission
("The guards"): a run that needs such a file does the rest, writes the file's change as a patch on its pull request, and
escalates (implementer.md step 6); the owner's label `apply-workflow-patch` applies the patch (the workflow of that name, main's script `.github/apply_workflow_patch.py`, the secret `WORKFLOW_PUSH_TOKEN`). An issue in In Progress can resume when its pull request is ready for review (not in draft) and its
latest verdict asks for changes or a required check failed, or the branch update found a conflict with main (the workflow
`branch-update`'s comment), or when the issue carries `restart` (implementer.md "Before you change a file", step 4).

**A draft stack**. An issue that meets every other rule above, and whose only open blocker is in In Review, or
in Approved with its pull request open, can start before that blocker merges. Its branch starts from the blocker's branch
(`mise run start NAME --from BLOCKER-BRANCH`), and it opens no pull request until the blocker merges: its gates and its
review then read only its own change against main (implementer.md "A draft stack"). The blocked-by link stays. A blocker
in Escalated or Canceled stops the stack, and the stacked issue goes back to Todo with a comment; a blocker in In Progress
for its fix round does not, and the stack merges the blocker's branch after it. A draft stack is marked by its newest
comment that starts with `Draft stack`, when that comment is the claim `Draft stack on KEY` (the comment `Draft stack ended
on KEY`, written when the stack goes back to Todo, clears the mark), with its branch on origin and no pull request: such an
issue in In Progress resumes when its blocker is Done, when its blocker is in Escalated or Canceled (it then goes back to
Todo), or when its blocker's branch has commits that the stacked branch does not have.

The list for the owner is one table, a row for each issue that can start, sorted by priority (Urgent first, no priority
last), then by the owner's order in Linear (drag the issues in a view ordered by priority; `.github/linear.py` reads it
as `manual_order`, the lower first), then by the issue's number. The orchestrator starts issues in the same order.
The size does not count: the owner puts a small issue first by a drag.

| Key | Title | Priority | Size | Needs | Area | Project | Runs with |
|---|---|---|---|---|---|---|---|

"Needs" holds the issue's difficulty labels (`needs-judgment`, `intricate`, both, or "–" for neither: "The issue states").

Two issues **conflict** when a blocked-by or related link joins them, or when their texts name a file in common (each
issue names the files it expects to change: planner.md "One issue or several"). The Area is no lock: it
says which part an issue changes most. "Runs with" names the other rows that can run beside it, the rows
that do not conflict with it. A row that conflicts with an issue in In Progress or In Review, or in Approved or Escalated
while its pull request is open, runs with nothing yet: its column names that issue instead ("waits for KEY"). A draft
stack's row says "stacks on KEY" (its blocker) and runs with the rows that conflict with neither it nor its blocker. An issue
in In Progress counts before its first push too: the implementer claims it before the draft pull request exists. An
issue in Approved or Escalated keeps its pull request open while it waits for the merge (auto-merge waiting for the
branch update, a code owner's file, a question in a fix round).
The orchestrator's rule differs on purpose: there an issue in Escalated holds no Area ("Until an orchestrator exists"), because no person starts the next issue of that Area in the night; this table is read by the owner, who
sees the waiting pull request and decides.
Under the table, one line each: the issues that can resume, and the issues in Todo that cannot start, with the reason
(the open blocker's key and its state, `hands-off`, or "L: split first"). A follow-up with no Area label (filed before
2026-10-28) takes the area of the pull request's issue it came from.

Under those lines, one line for each Area and pair of difficulty labels, in a code block that the owner pastes into
that Area's session (the owner's call, 2026-11-05): the Area, the labels, then the keys of the issues of that Area and
pair that can start, separated by commas, in the order to take them (`frontend · intricate: ABC-469, ABC-468`; with
neither label, the Area alone: `frontend: ABC-470`). One session runs one model, so a pair gets its own line. The
order is the table's (priority, then the owner's order in Linear): no link puts one Todo issue after another of the line (the owner's call,
2026-11-07). An Area and pair with no issue that can start gets no line.

## Until an orchestrator exists

Since 2026-10-24, with no orchestrator: the owner starts the implementer by hand from an issue in Todo. Since 2026-11-03
the session runs as the agent's macOS user (`.workflow/agent_user.py`, README.md "The agents' macOS user"), in a pane of
the owner's herdr on the SSH machine of that user, and pushes with the implementer app's token. The owner's rule
(2026-11-03): no language model with tool access runs unattended in the owner's own macOS account, which holds the
server's SSH key, Bitwarden and a login that bypasses the ruleset; a plain orchestrator with no model may run there. The
orchestrator to build runs as the agent's user and opens each session as a pane on that user's herdr server, so the owner
sees every session and answers its questions (DECISIONS.md "Delivery"). An issue with the Area `workflow` runs
there too, like any other; the owner's label `apply-workflow-patch` pushes a workflow file from the run's patch, and the owner merges a code owner's file. The reviewer runs on GitHub Actions on the ready mark; GitHub merges on the approve. A pull request that changes a code
owner's file waits for the owner, who merges it with the bypass (on a pull request opened in the owner's own account,
GitHub also refuses the owner's approve: the author cannot approve).
The implementer session ends its turn after the ready mark and the check that a review started; it reads its pull
request no more (the owner's call, 2026-11-16: the reads every minute missed changes, stopped at a 40-minute
limit, and cost a turn each). The orchestrator's message starts each later step: a verdict that asks for changes, a
required check that fails on the head (also after an approve) or a conflict from the branch update starts a
fix round in the same session (below). This holds for a session that the owner started by hand too: the
orchestrator cannot wake it, so its fix goes to the orchestrator's new session after `claim_wait_seconds`. Apart from such a fix it pushes nothing after the approve: nothing merges main into a pull request that is only
behind, and the workflow `branch-update` sends back one whose merge with main conflicts. The orchestrator takes over the start from Todo:
one process, standard library, polling Linear, Linear and GitHub as its only state (and the record of each session's host), restart-safe. Each round it starts the
issues in Todo that can start, in the order of "What can start now", within `max_runs` (3, the owner's call, 2026-11-16; one number for all teams, and a round reads every team that the config lists), as sessions in the agent's
herdr (or in the session host that the config's `session_host` names: herdr, Paseo or a plain process; each developer
picks one, the owner's call, 2026-11-11; a switch of host needs no wait: a session of the old host gets its wakes and its
close on that host). An issue that runs (In Progress, or in In Review or Approved with its
pull request open, or one it started a moment
ago, or one whose session carries the orchestrator's label and is busy in the session host; a failed session list starts nothing that round; or one that another starter started a moment ago, "Until then the claims have windows") holds its Area and its related links: a candidate with a related link to one waits, and while `distinct_areas` is
on, so does a candidate of the same Area or with no Area. An issue in Escalated with its pull request open holds only its
related links, not its Area (the owner's call, 2026-11-10): it waits for the owner, which can take a night, and
its Area's queue goes on. The cost is a possible conflict between its pull request and the next one of the Area: the
workflow `branch-update` finds it after a merge and sends the pull request back for a merge of main (one fix round). A
follow-up whose pull request is open waits for the merge ("What can start now"): the plan names it in its other
waits ("after the merge of PR #N"), and it can start in the first round after the merge. It starts no draft stack: those stay the owner's `/implement`. It also
closes the open pull request of a Canceled issue, with one comment, 10 minutes after the cancel (`cancel_grace_minutes`: the
owner may undo a cancel; the time comes from the issue's history). It deletes no branch. The pull request of a Canceled `hands-off` issue stays open: the plan's row `yours`
lists it (`KEY canceled: close PR N by hand`), and the owner closes it (the owner's call, 2026-11-11). The fix round stays in the session that opened the pull request, with an orchestrator too
(the owner's call, 2026-11-10): the session has the context, and it waits for the orchestrator's message. When an
issue waits for a fix (a request for changes, failed gates, the branch update's conflict), the orchestrator first wakes the
session that opened the pull request: in the round that sees the event it sends that session one message, which names
the pull request, the cause and the step ("When a review asks for changes" of the implementer's brief). The host sends it only
when the session is at rest: Paseo (`paseo send ID --no-wait --prompt-file FILE`, to the newest agent of the key whose status is
idle, closed or error, never a running one, because a send to a running agent steers its turn and stops its tool call at once;
the agent comes from the list without `-a`, so a send never un-archives one), herdr (`herdr pane run` in the root pane of the
key's newest workspace, when the pane's agent is at rest: idle, or done, since a pi pane shows done until viewed), a plain process never. A busy session gets no message: each round
tries again until the session is at rest or the wait ends. One wake for each event (its time is kept in memory: a restart may
wake once more, which is harmless, because the draft claim guards the fix round), and no Linear comment. The plan's events are
`woke`, `would_wake` (a dry run, once for each event) and `wake_failed` (the host raised, once for each wait and error type); a
host that finds no session to wake leaves no event. An exit 0 of the host proves nothing about the fix: the draft mark does.
Since Paseo 0.16 a turn that stops on an API error (the account's usage limit, an invalid key) also ends in `error`
(measured on 0.16.1), and a wake or a new session would stop on the same error. So while the newest Paseo session of
a profile (of the orchestrator's label; the profile is the label `profile=NAME` that each start gives its agent, the profile of
the cell that the start ran) is in `error` and its log's last `[System Error]` line matches a pair of the
`stop_patterns` of the `[harnesses.NAME]` table of the agent's harness (Claude Code's usage limit's words
are not measured yet: the owner's call, 2026-11-17), a round starts no session on that profile (from Todo or for a fix), sends
no wake to a session of it and escalates no fix wait of it. Each profile has its own stop, its own retry and its own
window, so with a mix of providers an empty LithosAI balance stops the cells on LithosAI and the cells on the Claude
subscription go on. An issue's profile is its cell's, from its difficulty labels as they are now; an issue in Todo whose cell
runs on a paused profile waits (`ABC-2 waits: its cell runs on pi-lithosai, which is paused`), and the plan's `pause` has one
row for each profile (`paused (pi-lithosai): no credit left on the provider (the newest session, of ABC-1, stopped on an API
error)`). An agent with no `profile` label (the owner's own, or one that an older start made) counts only when it is the newest
agent of all, and then it stops every profile, as one stop did before. With no cell on another profile the lines name no
profile and one stop holds the whole loop, as before. A provider can refuse one valid request, so the stop gets one retry: `retry_wait_minutes` after the round
saw it, the orchestrator wakes that session with the message `Retry`, and the plan says `retrying KEY after an API error (CAUSE)`.
The retry has the same window again: a matching stop in it pauses the profile, and the plan then says `paused: CAUSE` (with the
profile in it when a cell runs on another one). A pair whose
third field is false gets no retry and pauses at once: a usage limit or a plan limit that resets in hours, an empty balance, a
sign-in that the owner renews. A second retry never comes for the same stop while the process lives (the state is kept in
memory: a restart gives a stop that still sits in a pause one more retry), and a dry run sends nothing. A stop that clears
before the retry, or with no stop in the retry's window, ends the state: the plan's event `resumed` says so and
`resumed_at` sets the fix waits' floor. The owner can end it too: a send to the stopped agent (it is then running or idle), or
its archive (it leaves Paseo's list; the pause goes on when the next newest agent stopped too). The close step runs during a
pause and archives the stopped agent 12 h after its issue reached Done, Canceled or Duplicate, and a setup failure archives its
agent within the claim wait: those end the pause too. A fix wait counts from the pause's end, so the session that the
owner just sent to has its `claim_wait_seconds` (the pause's end is kept in memory: a restart within that time counts
from GitHub's times again). Another error, a crash for example, pauses nothing. herdr and a plain process cannot see a turn's
error, so they never pause.
When the pull request is still ready
5 minutes after the event that began the wait (`claim_wait_seconds`), no session took it: the orchestrator starts one new session for the issue
with the run command, as a start from Todo does (the owner's call, 2026-11-16). That session resumes the issue from its
pull request ("Before you change a file", step 4 of the implementer's brief). The start follows the same slot rule as a start
from Todo (within `max_runs`), never begins beside a session of the key that the host shows busy (or that this process started
and the host does not list), and writes the run line `Run: MODEL, effort EFFORT; review MODEL, effort EFFORT; profile NAME;
pytest workers N (fix round of PR #N)` on the issue (the review's model and the cell's profile as below): that comment
is the record of the start, so a restart of the service starts no second one. A dry run prints `would start a fix session for
KEY`. When that session did not claim the fix (the draft mark) within a second `claim_wait_seconds`, or the same pull request
waits for a fix again after it, the orchestrator adds `escalated` to the pull request, writes one comment (it says that one
session was started), and moves the issue to Escalated. A wait that could not start a session (a session beside it, no free
slot, no session list) escalates after two `claim_wait_seconds`, with a comment that says no session was started.
A launch failure before a session exists does not count as a try. It halts starts and fix escalations for all teams.
Wakes, closes and queue updates continue. A passing preflight lets the service retry; a late confirmation of a session
clears its halt without preflight. A request
for changes with a commit newer than the review is a fix that a session pushed, so it never escalates. A wait for failed gates does nothing while a rerun of the gates runs or can still come (25 minutes for the watchdog's); then the ladder above starts (the owner's call, 2026-11-18). The owner
moves the issue to Todo, and the orchestrator starts the run (`/implement KEY` by hand only until an orchestrator exists; the owner's call, 2026-11-16); an issue in Escalated gets no start from the orchestrator.
A session stays in its herdr workspace when its run ends. The orchestrator closes the workspace 12 hours after its issue
went to Done, Canceled or Duplicate (`close_after_hours`, the owner's call, 2026-11-10), when herdr shows the
agent idle or done (with Paseo: it archives the agent, which keeps its history, when Paseo shows it idle, in error or closed (its process ended, for example after a daemon restart), and the Paseo workspace titled with the issue's key once no agent of that key is left; the orchestrator remembers a workspace whose archive failed, in memory only (a restart forgets it, and the owner archives that workspace), and tries it again; Paseo's agent list names no workspace, so an agent of the owner's inside such a workspace cannot be told, and the orchestrator never archives a workspace that it did not fail on, so the owner's own stay):
the night's runs are still there in the morning, and a closed pane loses nothing (the transcript
stays in Claude Code's files of the agents' user, the decisions in the issue and the pull request). It closes only the
workspaces it labelled, never one that the owner opened.
Until then the claims have windows, so start one run per issue at a time. The move from Todo to In Progress and the
removal of `restart` each read the issue, then write in a second request: two runs that read within one round trip both
pass the claim. The fix round's draft claim starts only when the session takes the orchestrator's message, after the
request-changes: a second `/implement KEY` in that time also resumes. The orchestrator's own
starts have none of these windows; the fix round's window stays, since a session claims its own round.
A start from Todo has a gap of its own before the claim: the session moves the issue about 20 s after the orchestrator
started it, and other starters (another developer's orchestrator, or the owner's `/implement KEY`) see the
issue in Todo until then. The orchestrator's run line closes most of that gap: it writes the line right after
the start, with a start token (`Run: MODEL, effort EFFORT; review MODEL, effort EFFORT; profile NAME; pytest workers N (start TOKEN)`, the start's UTC time
and the host's name), and
gives the same token to the session in its prompt (`{token}` in the `skill_prompt` of the profile's harness). While
that line is the newest run line of an issue in Todo and is younger than `started_elsewhere_seconds` (10 minutes), another
orchestrator does not start the issue (its plan says `started elsewhere at HH:MM UTC, not claimed yet`), and a session whose
token is not on the line stops at its claim (implementer.md step 4); a session started by hand has no token. A session's
own run line comes after its claim and has no token, so an issue that a run claimed and gave back is free again. What
remains: two starters that read within one round trip of the line's write both start (Linear has no compare-and-set), a
start whose run line was not written has no guard, and a dead start elsewhere holds the issue for the 10 minutes. After a
restart the orchestrator knows no token of its own, so its own unclaimed start waits like another's, for the 10 minutes at
most.

In any harness, `/implement` (the skill `implement`) shows the list of "What can start now" and `/implement KEY` runs one issue, and `/issue` writes the issues for an idea (`planner.md`). Several sessions can run at once, one issue each.
What limits the number is not the machine but the merges: two pull requests that change the same files conflict, and
the second one to merge meets a conflict, and a person or an implementer resolves it (the workflow `branch-update` reports it
and sends the pull request back). So run at the same time only issues with no related link between them, three at
most (`max_runs`, the owner's call, 2026-11-16; two, and one per Area, until then). Since pull requests merge in parallel, a merge no longer
costs the other pull requests a new review, so what is left of the reason is two pull requests that change the same
files. A related link is the planner's own statement of that; an Area is a rough guess of it. So the orchestrator keeps
the link as a hard rule and the Area as a setting, `distinct_areas`, off since the owner's call of 2026-11-16: the Area is a label that says which part an issue changes most, and the related link is the only lock between
two issues. It does not compare the files that two issues name. Pull requests that touch other files merge in
parallel with no update of their branches.

## Setting up a product

A product's repository holds a synced copy of the loop's files: the files that `.workflow/loop-files.txt` lists. A change
to one of them is a pull request in the loop's repository, never in the product's: a hand edit in the product fails its
copy check. A run that needs such a change files the issue in the loop's team: the product's AGENTS.md "The teams" has a row for it, the row whose repository is the
loop's. Work
found on the way goes on as "While you work" in `implementer.md` says; a change that the run's own work needs first is a
task that must come first, with the blocked-by link. The product's `mise run loop:bump [REF]` brings the merged change in, as
one command with no issue (the owner's call, 2026-11-26): in the main checkout it makes the worktree and the branch
`loop-bump-SHORT` (SHORT: the new pin's first 12 hex digits), copies the files, commits, pushes and opens the pull request. This is the one exception to "a change has its issue first": a **pure
bump** is a pull request on that branch whose diff against main changes only `.workflow/loop.toml` and the files that the loop's
list (`.workflow/loop-files.txt`) names at the head, or that only main's list names (the bump removes it: the file left the loop), and it needs no issue key. The guards accept it and nothing else without a
key: `.workflow/start.py`, the push hook and the review's start (`review_jobs.py`). A `loop-bump-...` branch whose diff changes
another file gets the failed check `verdict` titled "A bump that changes a product file needs its issue". The review moves
no issue for a pure bump (the report's Linear steps print a notice and skip), and the pin is a code owner's file, so the owner
merges the pull request. A bump that also needs a product change (a test, a workflow of the product) has its issue and a keyed
branch, as before.
The Mac's tools (`orchestrator/`, `.workflow/agent_user.py`, `.workflow/approved_run.py`, `.workflow/model-catalog.json`
and their tests) are not in the copy: the machine's commands run from the loop's checkout only, and a product's
`mise.toml` needs no task for the `owlcode` commands (OWL-46, OWL-54: `bin/owlcode` is this repository's own).

1. **Two GitHub Apps** under the owner's account, implementer and reviewer, with the permissions in "The guards",
   installed on the repository. An app's private key can mint a token for each repository that the app is installed on.
   So **a public repository gets its own app pair**, installed on that repository only: its keys then reach nothing
   else (the owner's call, 2026-11-26, design b2). Private repositories of the same owner may share one pair. Name the
   apps `OWNER-NAME-implementer` and `OWNER-NAME-reviewer` (the skill `measure` counts the reviews of a login that ends
   with `-reviewer`). No file of the loop names an app: the workflows take the reviewer app's login from its token (the
   output `app-slug` of `actions/create-github-app-token`), the implementer app's login from the Actions variable
   `IMPLEMENTER_APP_SLUG` (the app's slug, as in its page's address `github.com/apps/SLUG`; with no variable,
   `OWNER-implementer`, the owner's shared pair), and the orchestrator from the product's `conflict_author`
   (orchestrator/README.md). A repository with its own pair sets `IMPLEMENTER_APP_SLUG`, and its `[[product]]` table
   names its `conflict_author`; `owlcode check` warns when the service's implementer app reaches a public repository
   and other repositories too, or when two products name one app (orchestrator/README.md). The service holds no key of
   the reviewer app: check its installations in the app's settings. The reviewer's Client ID (on the app's page beside the App ID, which GitHub deprecates
   for tokens) as the repository's Actions variable `REVIEWER_APP_CLIENT_ID` (an ID is public, so never a secret: a
   secret's value is masked in every log, and a secret misleads the next reader) and its private key as the Actions
   secret `REVIEWER_APP_PRIVATE_KEY`, set from the owner's terminal from the file, `gh secret set
   REVIEWER_APP_PRIVATE_KEY -R OWNER/REPO < FILE`, never pasted into the prompt (the prompt keeps one line of the many,
   and the workflow then fails on the key's shape; no script sets a secret). The implementer app's go in the same way: its
   Client ID as the variable `IMPLEMENTER_APP_CLIENT_ID` and its key as the secret `IMPLEMENTER_APP_PRIVATE_KEY` (auto-merge and
   the branch update), its slug as the variable `IMPLEMENTER_APP_SLUG` when the pair is the repository's own (`gh
   variable set IMPLEMENTER_APP_SLUG -R OWNER/REPO --body SLUG`), and its key in Bitwarden, for the orchestrator.
2. **Linear:** one team for the repository, and its row in the product's AGENTS.md "The teams". The same table has a row for the loop's team and repository, where a run files the issue for a change to a loop file. `python3 .github/linear.py setup TEAM` makes the rest, reading first and changing only what
   differs (`--dry-run` first; safe to run again; `/usr/local/bin/python3 -I` (python.org 3.11 or newer, its certificates installed) on a Mac whose
   `python3` has no root certificates, AGENTS.md "Traps"): the workflow states Todo, In Progress, In Review, Escalated (named Needs decision before
   2026-10-25: on a team made before, rename that state in Linear first, or setup makes a second one),
   Approved and Done; the labels `follow-up`, `fast-lane`, `hands-off`, `restart`, the difficulty labels `needs-judgment` and `intricate` (team labels outside any group), and the group Area with its seven labels inside it
   (`AREAS`: a label of that name outside the group is reported, never made a second time); the git automations, Draft PR to In Progress, PR
   opened to In Review, PR merged to Done, and Review requested and Ready for merge to nothing (whether "PR opened" fires
   on the ready mark of a draft is what step 5 shows); and the triage inbox on, whose state is Triage. The key reaches
   the command from a hidden prompt, never a file: `read -rs LINEAR_API_KEY && export LINEAR_API_KEY`, the command, then
   `unset LINEAR_API_KEY`. By hand: the seven Area labels on GitHub too, with the same names and Linear's colors (`gh label create NAME -R
   OWNER/REPO --color HEX` for each: a pull request carries its issue's Area label); the GitHub integration installed on the repository (Settings, Integrations, GitHub;
   its issue sync one way, GitHub to Linear, or none: Linear is the record, so nothing of it becomes a GitHub issue);
   the API keys, personal API keys of the owner's Linear account (so `linear.py create --assignee me` assigns an issue to
   the owner), one for each place it is kept, so one can be revoked alone: one as the Actions secret `LINEAR_API_KEY`
   (`gh secret set NAME -R OWNER/REPO`, since run from another checkout it lands in that repository), one in the agent
   user's keychain (`agent_user.py user`); never in a file ("The record"). Each has the permissions Read, Issues and
   Create comments, limited to the team: no Write (a steered run cannot delete or archive issues, edit comments, or
   change the team's labels, workflow states or projects; an issue's own state and labels are Issues, so every run
   still moves its issue and labels a new one) and no Admin (no webhooks, integrations or members). `linear.py setup` needs Write:
   the owner runs it with a key of its own. When a run fails with a permission error at a blocked-by link
   (`issueRelationCreate`) or a new project (`projectCreate`), Write goes on that one key. On the owner's machine,
   Linear's MCP server signed in once.
3. **The ruleset, the merge settings and the variable:** `python3 workflow/github-setup.py OWNER/REPO --check GATE
   (one for each required gate of the product) --check verdict --code-owners --bypass-admin --auto-merge --variable REVIEWER_APP_CLIENT_ID=ID`
   from the owner's setup tools, `--dry-run` first. Then, by hand in the ruleset, the required check `verdict` from the reviewer app
   only (its source, not "any source").
4. **`.github/CODEOWNERS`**, `.github/workflows/claude-code-review.yml`, `.github/review_steps.py` (its job `review`),
   `.github/review_report.py` (its job `report`), `.github/review_jobs.py`, `.github/workflows/branch-update.yml`,
   `.github/branch_update.py`, `.github/linear.py` and `agents/` copied from
   the product that holds the current version, with its commit hash in the copy's pull request.
   Two scripts are not copied: each product writes its own `.github/code-changed.sh` (prints `true` when a changed file is
   code; the loop's hooks and the review call it) and `.github/gates-needed.sh` (prints `none`, `python` or `both`; only the product's own
   gates workflow reads it), and they are not in `.workflow/loop-files.txt`. Nor is `.github/pull_request_template.md`: its
   checklist names the product's own checks, so each product writes its own (a product that took the loop's copy before keeps
   it at its next bump, and owns it from then on).
   `review_steps.py` copies main's `code-changed.sh` with `check=True`, so a product without it fails the review's step.
   The product's own gates workflow never skips a gate job as a whole (the owner's call, 2026-11-25: OWL-30): each job named in
   `GATES_CHECKS` has `if: ${{ !cancelled() }}`, a first step that fails it when the planning job did not pass, and the
   condition of the gates that the change needs on each other step, so a docs-only change skips steps and the job passes.
   The workflow `gates-failed.yml` and `.github/gates_failed.py` come with the loop's files; the product's `GATES_CHECKS` names
   the checks that it reads.
   A product that already uses the loop changes its gates workflow to this rule before the bump that brings OWL-30, or in
   the pull request of that bump. Without it, `gates_failed.py` counts each skipped gate job (a docs-only change, or a gate that
   the plan left out) as a gate that did not run: every approved pull request of that kind gets a `FAILED_GATES` comment and
   goes back to In Progress, and no change to the branch can fix it. A gate that a failed planning job skipped also lets
   auto-merge merge before `gates-failed` reads it.
   `LINEAR_AREAS_FILE` (a JSON object of Area name to text) is no workflow value: only `linear.py` reads it. Set it in the
   environment of `linear.py setup` (step 2, before setup makes the Area group) and of the sessions that run `linear.py`
   (`triage-checks`). The product may set these optional values in the workflows' `env:` (each has a default of this
   product): `IMPLEMENTER_LOGIN` (the implementer app's login; apply-workflow-patch.yml sets it from
   `IMPLEMENTER_APP_SLUG`, step 1),
   `LINEAR_TEAM` (the team of the flake issues; it has no default: with no value, `flakes.py` takes the team whose
   row in the product's AGENTS.md "The teams" names the repository, and with no such row it stops with exit 1),
   `GATES_SUITE_JOB` and `GATES_SUITE_STEP` (the job and the step of the
   browser suite), `GATES_CHECKS` (the gates' checks, separated by spaces: `gates_failed.py` reads it), `GATES_WORKFLOW` (the gates' workflow file),
   `OWNER_LIMITS_FILE` (the owner's limits file), `OWNER_LIMITS`
   (the owner's keys, separated by spaces) and `OWNER_LIMIT_PREFIXES` (their prefixes).
5. **A test pull request** that checks the reviewer's review posts, its check `verdict` satisfies the ruleset, and the Linear
   moves land, and that an approve turns on auto-merge.
6. **The runner variables** (optional): `LOOP_REVIEW_RUNNER` and `LOOP_WATCH_RUNNER`, as the bullet "The runners" says. A product that uses Namespace sets both and installs Namespace's GitHub app on the repository.
7. **The product's own agents' user** (the owner's call, 2026-11-26, design b2): the product's sessions run in a macOS
   user of their own, which holds only the product's things, and the orchestrator runs in a user where no session runs.
   - Once for the machine, the orchestrator's own user: `owlcode admin --orchestrator NAME`. Switch `[service]
     user` to it as the last step of the move: after each product has its own user, its `paseo_host` and its password
     item, and the orchestrator's keychain holds each team's Linear key and the orchestrator's app key. The `[service]`
     lines that it prints are for that step. That user has no harness, no clone, no SSH
     login and no Paseo daemon; its keychain holds only each team's Linear key and each daemon's password
     (`paseo_password_item`), at the hidden prompts of the `security` commands that `admin` prints, and the orchestrator's
     app key (`app_key_item`), which step 8 stores. The project's
     user holds the project's keys: the implementer app's, the Claude token and the team's Linear key. After the
     move, `owlcode check` fails while the orchestrator's user (the user whose full name is `NAME orchestrator`,
     which only `admin --orchestrator` gives) holds a harness config, a clone (but `[service] clone`), a Paseo daemon
     or an SSH login or key (README.md "The agents' macOS user").
   - In the orchestrator's config, the product's table (the top-level keys for the first product, its `[[product]]`
     table for another) names `agent_user`, the new user's name, and `paseo_port`, a port that no other user's Paseo
     daemon takes (`owlcode check` refuses one port for two users). `owlcode admin --project` refuses a table with no
     `agent_user`, or with `[service] user` there: the orchestrator's own user never becomes one product's user.
     `owlcode check` takes `[service] user` for a product with none.
   - `owlcode admin --project TEAM`, from the owner's account. It asks before it makes a new macOS user, then makes the
     user with its home (mode 700), its own sshd file and the owner's key for it (`~/.ssh/NAME-agent`), and writes the
     team, its repository, its port and the orchestrator's install folder (`install_dir`) into the user's
     `~/agent-project.json`. A project file from before OWL-76 has no `install_dir`: `paseo-daemon` then refuses and
     says to run `owlcode admin --project TEAM` again from the owner's account. It prints the `Host NAME` block for the
     owner's `~/.ssh/config`, the line `herdr machine add NAME --label NAME --remote-session default` (the user is its
     own herdr machine), the config's `paseo_host = "ADDRESS:PORT"` (ADDRESS: this Mac's Tailscale address) and
     `paseo_password_item`, and two `security` commands for the orchestrator's user.
   - As the new user: `sudo -iu NAME /usr/bin/python3 -I agent_user.py user`. It clones the product's repository only,
     and asks only for the product's keys: the implementer app of step 1 (installed on that repository alone), a Linear
     key limited to the product's team (step 2), and a Claude token of the user's own. Its git credential helper asks
     GitHub for tokens of that repository only. Then it asks once for the Paseo daemon's own password, at Paseo's hidden
     prompt (`paseo daemon set-password`): Paseo keeps only its hash, so the owner keeps the password in the password
     manager. `paseo-daemon` makes the daemon listen on ADDRESS:PORT at each start.
   - The two `security` commands that `admin` printed, from the owner's account: they store the same password in the
     orchestrator's keychain, at a hidden prompt. Then `paseo_host` and `paseo_password_item` go into the product's
     table, and `owlcode restart`.
   - `owlcode check` gives three lines for each product: `ok   TEAM: the user NAME exists`, `ok   TEAM: the home HOME of
     NAME is mode 700` and `ok   TEAM: the user NAME holds no other team's clone` (else `FAIL TEAM: the user NAME holds a
     clone of OTHER (FOLDER)`). A config whose products share one user fails that last line until each product has its own.
8. **The orchestrator's own GitHub App** (the owner's call, 2026-11-26, design b2: OWL-70), once for all products. The
   service runs in a user of its own, which holds no implementer key. On GitHub it only reads pull requests and their
   checks, adds a label to a pull request and closes a canceled issue's pull request with a comment: it never writes code.
   So it gets an app of its own, with the least reach:
   - Make the app under the owner's account (Settings, Developer settings, GitHub Apps), named `OWNER-orchestrator`.
     Repository permissions: Pull requests read and write, Issues read and write (a pull request's labels go through the
     Issues API), Checks read, Commit statuses read (the service's `gh pr list --json ...statusCheckRollup` can need it), Metadata read, and nothing else. No webhook (clear "Active"). Install it on every
     product's repository.
   - Its Client ID goes into the orchestrator's config, `[service] app_client_id = "CLIENT_ID"` (an ID is public).
   - Its private key goes into the orchestrator's keychain only, never a file that stays: make the key on the app's page
     (a file `KEY.pem`; delete it after both stores below). Unlock the keychain of USER
     (`[service] user`) with `sudo -u USER -H security unlock-keychain /Users/USER/Library/Keychains/agent.keychain-db`,
     then send the key through `security -i` on its standard input (the hidden prompt of `-w` cuts a key of about 2,200
     characters to 119, and the shim then refuses it): `printf 'add-generic-password -U -a USER -s
     USER-agent.orchestrator-app-key -w %s /Users/USER/Library/Keychains/agent.keychain-db\n' "$(base64 -i KEY.pem | tr -d '\n')" | sudo -u USER -H security -i`
     (`printf` is a shell builtin, so no process's arguments hold the key).
     The item's name goes into the config, `[service] app_key_item = "USER-agent.orchestrator-app-key"`. Before the
     switch of `[service] user` (step 7), store the same key again in the keychain of the orchestrator's user NAME: the
     same two commands with NAME for USER (the unlock, then the `printf` line), and `[service]
     app_key_item = "NAME-agent.orchestrator-app-key"` in the lines of the switch. `admin --orchestrator` prints no command for it. Then `owlcode
     restart`: it writes both values and each product's repository as `service-app.json` beside the installed copy, for
     the service's `gh` shim.
   - With both keys set, the service's `gh` mints the token with this app: for each call it finds the installation of
     the call's repository (`--repo`, the `repos/OWNER/NAME` path of `gh api`, else the first product's) by
     `/repos/OWNER/NAME/installation`, and keeps each repository's token in the keychain until five minutes before it
     ends. Without them, it uses the implementer app's key, as before.
   - `owlcode check` gives one line for each product's repository: `ok   github: the orchestrator's app NAME reaches
     OWNER/NAME`, or `FAIL github: the orchestrator's app NAME is not installed on OWNER/NAME` (NAME: the app's slug).

## Model call metrics

For an orchestrator start, pi writes one JSON Lines file per issue key and start token in
`~/.local/state/agent-metrics/KEY-STARTTOKEN.jsonl` in the agents' home. It keeps these files until the owner removes them.
The file contains no secrets, prompts, replies, payloads or file content. It records the role, provider, model, effort,
request and reply times in epoch milliseconds, time to first token (TTFT), tokens per second, input tokens (including
cache reads and writes), cache-read tokens, output tokens, reasoning tokens when available, and cost when pi reports it.
Pi's cost can be an estimate, not a bill. The first nonempty text, thinking or tool-call delta sets TTFT. Throughput is
output tokens divided by the time from that delta to the end. Failed calls have an error type but no throughput. These
are assistant calls as seen by pi, not internal transport retries or background cache warming.

`python3 .agents/metrics/report.py KEY-STARTTOKEN` prints one `Speed:` line per provider and model and separate
`Local review speed:` lines. Median and nearest-rank p90 use only available measurements. The implementer posts these
lines on the issue at handoff. On Claude Code, the report binds the run to its own session transcript by
`CLAUDE_CODE_SESSION_ID`: it opens only that session's file under the harness's `projects` folder, never another
session, and records numbers, names and times only. `--claude-transcript PATH` overrides the path. Its throughput uses
the time from the preceding transcript entry and is approximate (the line shows `~`). It records no TTFT and no cost.
Claude Code's local review keeps no transcript (`--no-session-persistence`), so it reads the numbers of its
`--output-format json` result: one `review` row for each invocation, with the tokens, the reply's duration and the cost,
and no prompt or reply. Its TTFT is `n/a` and its throughput is approximate (`~`). To collect pi local review metrics,
run `owlcode admin` from the merged checkout and restart the session host so it uses the updated review command.
The review loads only the installed metrics extension in addition to its read-only tools; project extensions remain
disabled.

## What to measure

The skill `measure` (`/measure --since DATE --until DATE`) prints these numbers from GitHub and the Linear
issues, each with its definition in a comment of `.agents/skills/measure/measure.py`, so two measurements of one window
agree. The pull requests are the ones created in the window and merged. The numbers: lead time from the first ready mark
(the opening, for a pull request opened ready) to the merge, median and p90, and its active share (gaps of 30 minutes or
less between two events of the pull request); reviews per pull request; blocking rounds; the owner's prompts per pull request; defects found on main
after a merge; follow-up issues filed and how many the owner accepted; fast-lane issues filed and how many the owner
cancelled (more than one in five means the fast lane's test is too wide); the share of pull requests that round 1 sent
back with a request for changes, and the share of `fix_now` findings that are in a document (2026-11-02 to
2026-11-08: 60% and 36%; they show whether the self-check of implementer.md "When you are done" works); the changed lines per pull request (median and 90th percentile) and the round-1 approve share by size band (100 or fewer, 101 to 400, over 400; 2026-11-02 to 2026-11-08: 62%, 26% and 0%, with 19% of the pull requests over 400). The skill also prints
the first verdict's wait, round 2's outcome, the pull requests still blocked after round 2 and after round 3, the merges by the owner and
the owner's wait after the last approve, the hours of the label `escalated`, the gates' and the review's durations, and
the red `gates` runs on main. For the local review (kept on 2026-11-18: DECISIONS.md "Delivery") it splits the pull requests by the
line `Local review: N findings, F fixed, L left out` in their text: the count that ran it and the count that did not
(`did not run`, or no line), the round-1 approve share of each group against the baseline of 51% below, and the findings,
fixed and left out in all, with the findings of each pull request. The owner's prompts and the defects after a merge come
from no artifact: they are counted by hand.

The baseline is the analysis of 2026-11-08, on the 272 pull requests created from 2026-10-29 to 2026-11-08 and
merged: ready mark to merge 16 min median (p90 141 min); ready mark to first verdict 3.3 min; 1.67 verdicts per pull
request; round 1 approve 51%, request-changes 47% (70% of them `fix_now` only), needs-decision 1.5%; round 2 approve
after a round-1 request-changes 96%; 3 pull requests still blocked after round 2; merges by the owner 34% (69 pull
requests with a code owner's file); the owner's wait after the last approve 101 h in all (38% of the ready-to-merge
time); `escalated` 159 h in all; the checks `python` 1.6 min, `web` 4.1 min and the review 3.9 min, in parallel, and
the last green gate to the merge 1.8 min; red `gates` runs on main 13 of 199 (12 of them flakes, by hand); 47
fast-lane issues, 4 cancelled; 88 follow-ups filed, 75 Done; round-1 approve 69% for 100 changed lines or fewer and
31% over 400. The past seven days alone (2026-11-02 to 2026-11-08, 119 pull requests): round-1 approve 38%, the owner's
wait 5.7 min median, over 400 lines approved in round 1: 0 of 23. The earlier baselines are not comparable with it: each
analysis (on 2026-10-24, 2026-10-26 and 2026-10-28) wrote its own scripts and
defined the lead time in its own way. The 2026-10-24 one measured 8.4 h median lead from the opening, 10% active, 2.7
reviews per reviewed pull request, 0.69 round-1 blocks on code, 5 to 11 owner prompts, and 2 defects after a merge in 25.

By model and effort level: each implementer run writes its settings on the issue as the run line `Run: MODEL,
effort EFFORT` (implementer.md "Before you change a file", step 4). The orchestrator writes the line too when it starts a
run, with the model and the effort level it passed (the cells of its config's inference profile): that line comes first, so
it is never `unknown`. Group the issues by the line of their first run,
and compare per group: pull requests, review rounds, blocking rounds, and issues sent to Escalated after the last round (the
skill `measure` prints this table too). A
run with `unknown` counts only where its value is known. The choice of a model by the difficulty labels waits for 30 issues with the line that are Done or
Escalated.

By the local review's model too: the run line that the orchestrator writes names the local review's model and
effort level after the run's (`; review MODEL, effort EFFORT`). It uses the active profile's `review_model` and
`review_effort`. For a pi profile, the orchestrator prefixes the model ID with the connection's `sign_in` value or, if
there is none, the profile name. For example, the `pi-lithosai` profile writes `pi-lithosai/MODEL_ID`, not
`lithosai/MODEL_ID`. Older pi run lines can record the same review model without that prefix. `measure` adds the
provider of the run's model to such a review name, so both spellings group together; it never reads a slash in
the review's catalog ID as a provider, and a run whose model names no provider (a Claude Code run) keeps its review name.
The same table by that group shows which review model finds the faults, beside which model made the change: a
review on another family than the run's finds other ones. An older run line names no review model, and is in no review
group. The same 30 issues hold for this group too.

By profile too: a cell can name another `[profiles.NAME]` table, whose provider and harness then run it, so
the orchestrator's run line names the cell's profile after the review (`; profile NAME`). The review part stays the
review of the profile in force, whatever profile ran the cell (the owner's call, 2026-11-23: one review model for the
whole mix, so the reviews compare across the cells). `measure`'s table by model and effort has a profile column, with
"none" for an older line, which names no profile.

## What lives in this folder

The briefs of the roles that run without a person (AGENTS.md "Autonomous runs"): the implementer, the reviewer and the schema
of the reviewer's verdict, the local review (`local-review.md`, which `.github/local_review.py` runs), the planner (an idea
into issues, and the triage with the owner), and `README.md`, the workflow end to end (the loop, the record in Linear, the
follow-up rules, the guards, how a product is set up). Plain text for any harness: no model, effort or tool of one harness is
named there; those are the orchestrator's settings. `.github/linear.py` is how a run reads and moves an issue, and
`.github/branch_update.py` runs the job of the workflow `branch-update` (the workflow only calls it): it chooses the pull
request to check for a conflict after a merge and reports the conflict. `.github/gates_failed.py` runs the job of the workflow
`gates-failed`: it reports the gates of an approved head that did not pass. The
skills `issue`, `implement` and `triage` are the owner's way into the planner, the implementer and the triage from any
harness: one copy in `.agents/skills/NAME`, linked from `.claude/skills/NAME` (this file: which harness reads which folder).
`reviewer.md`, `verdict.schema.json` and each skill's `SKILL.md` are the owner's (CODEOWNERS; a skill's frontmatter
can grant tools or declare hooks; a skill's other files merge on the verdict); the other briefs merge
on the verdict, and the review reads main's copies of its rules.

A trap of the workflow: `gh pr checks --watch` waits for every check of the head, the watching workflow's own check included:
a workflow that waits for the gates this way waits for itself until its timeout (the review on the ready mark, 2026-10-25).
Poll the gates' checks by name instead.
