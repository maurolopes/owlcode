---
name: implement
description: Implement one Linear issue, of any team of AGENTS.md "The teams", as agents/implementer.md says, from Todo to an approved pull request, or resume its fix round; with an Area's line of keys, one issue after another. With no key, show the table of the issues that can start now (agents/README.md "What can start now"), one table per team. Use when the owner says "/implement ABC-12", "implement ABC-3", "what can I start?".
---

# Implement an issue

The rules are in `agents/` for any harness; this file only says when to use them. The argument is an issue key such as
`ABC-37`, or an Area's line of keys (`ABC-469, ABC-468`: agents/README.md "What can start now"). With a line, take
the keys one after another, in the line's order, each as "With a key" says, to its end before the next: one issue, one
worktree, one branch, one pull request. A key that cannot start is skipped with its reason; go on with the next.

The orchestrator starts a run with `/implement KEY start TOKEN`. The TOKEN is no part of the key: it is the run's own start
token, which `agents/implementer.md` step 4 compares with the newest run line of the issue (`(start TOKEN)`). A run with
that token owns its start. A run with no token, started by hand, treats such a line as another run's claim only while it
is less than 10 minutes old.

The key's prefix is the issue's team, and AGENTS.md "The teams" gives the team's repository and its checkout.
TEAM and REPO below are that team and that repository: each `linear.py` command gets TEAM, and each `gh` command gets
`-R REPO`.

Reach Linear with `.github/linear.py` (the key in the environment, never in a file) or with the harness's own Linear
connection. Do the same action either way. A session with the Linear keys in its environment (`LINEAR_API_KEY`, and each team's variable of AGENTS.md "The teams"; the agents' macOS user, a runner) uses `.github/linear.py` for reads and writes: it sends each team's own key. In the owner's own account (not the agents' user on the same Mac), reads go through `mise run linear:read -- COMMAND ...` (`list`, `issue`: the team's read-only key from the login keychain, in that one command's environment; any other command is refused), and writes (state, labels, `triage-set`, `create`, `link`, comments) go through the harness's Linear connection, signed in with the owner's account (agents/README.md "The record"). Never a key in a file or in the chat (AGENTS.md "Secrets"). The pull requests
come from `gh`.

## With no key

Do this for each team of AGENTS.md "The teams", and show one table per team, under the team's key. A team whose key is missing (`linear.py: no VARIABLE in the environment`, which `linear:read` prints too) is one line in the report, and the other teams still show (agents/README.md "The teams"). Build the list
that agents/README.md "What can start now" defines. Read the team's issues in the
states Todo, In Progress, In Review, Approved and Escalated, each with its blockers, related links, labels, priority and
size (`python3 .github/linear.py list TEAM --state Todo --state "In Progress" --state "In Review" --state Approved
--state Escalated`). Keep an issue in Approved or Escalated only while its pull request is open (`gh pr list -R REPO --state
open --limit 500`, the key in the branch's name: without `--limit`, gh reads 30 at most): it holds its area like one in In Review ("waits for KEY"). For an issue in In
Progress, read its pull request (`gh pr view N -R REPO --json isDraft,reviews,statusCheckRollup`). A row that can start as a
draft stack says "stacks on KEY" in "Runs with", with the blocker's key. A blocker in another team counts like any
other: an issue waits for it until it is Done. Under each table, show the
line of each Area, in a code block, as "What can start now" defines it. Under the last one, one line: `N pull requests and M
issues wait on you: /queue`, with N the open pull requests that the skill `queue` reads in its steps 1 and 2 (the label
`escalated`, or the comment "Ready to merge. It changes a workflow file" with no auto-merge) and M the issues in
Escalated with no such pull request, all teams counted. Then stop: the owner starts each issue with
`/implement KEY`, or pastes an Area's line into that Area's session.

## With a key

1. Check the issue against agents/README.md "What can start now": read it with its text, comments and relations
   (`python3 .github/linear.py list TEAM --key KEY`; for an issue in In Progress, also `gh pr view N -R REPO --json
   isDraft,reviews,statusCheckRollup`). An issue whose only open blocker is in In Review, or in Approved with its pull
   request open, starts as a draft stack (implementer.md "A draft stack"). When it can neither start nor resume, stop and
   say why: the open blocker's key and state, `hands-off`, size L, or another run that holds it. An issue with the Area
   `workflow` starts like any other (the owner's call, 2026-11-16). This is the owner's own start, so the blocker check is yours
   (implementer.md step 4 leaves it to the start).
2. Read `agents/implementer.md` and follow it from "Before you change a file" to the end. Its step 4 claims the issue.
   Its `.github/linear.py` commands name the actions: read the issue, move it to a state, remove a label, add one
   comment, create an issue, link two issues. Work in REPO's checkout: its worktree, its gates and its pull request.
   While a repository's loop is not complete, an issue of its team follows the interim steps of AGENTS.md "The teams"
   (the worktree by hand, no review).
3. The owner watches this session, but the brief's rule holds: a question goes on the issue as one comment, and the
   issue goes to Escalated. Say so in the session too, in one line.
4. End as "When you are done" step 7 of `agents/implementer.md` says: end the turn after the ready mark and the check
   that a review started, and do not wait in the session. The orchestrator's message starts each later step (a fix
   round, a late gate failure, a branch-update conflict). Also end the turn at a needs-decision (the issue in
   Escalated). End with the pull request's link and the issue's state.
