---
name: queue
description: List everything that waits on the owner (every team of AGENTS.md "The teams"), each pull request and issue with its reason and its one action, read from GitHub and Linear only. It prints; the owner acts. Only in a session with the owner. Use when the owner says "/queue", "what waits on me?", "what do I have to do?".
---

# What waits on the owner

This skill reads and prints. It moves no issue, adds no label and merges nothing: the owner acts on each row. The rules it
reads the rows with are agents/README.md "Who moves an issue" (who acts on each case), "The guards" (the code owner's
files and the label `owner-approved`) and "The record" (the issue is the record). It changes no file of the repository,
so it needs no worktree.

Reach Linear with `.github/linear.py` (the key in the environment, never in a file) or with the harness's own Linear
connection. Do the same action either way. A session with the Linear keys in its environment (`LINEAR_API_KEY`, and each team's variable of AGENTS.md "The teams"; the agents' macOS user, a runner) uses `.github/linear.py` for reads and writes: it sends each team's own key. In the owner's own account (not the agents' user on the same Mac), reads go through `mise run linear:read -- COMMAND ...` (`list`, `issue`: the team's read-only key from the login keychain, in that one command's environment; any other command is refused), and writes (state, labels, `triage-set`, `create`, `link`, comments) go through the harness's Linear connection, signed in with the owner's account (agents/README.md "The record"). Never a key in a file or in the chat (AGENTS.md "Secrets"). The pull requests
come from `gh`.

## Read

Read steps 1 to 4 for each team of AGENTS.md "The teams": TEAM is the team, and REPO its repository (each `gh`
command gets `-R REPO`). A team whose key is missing (`linear.py: no VARIABLE in the environment`, which `linear:read` prints too) is one line in the report, and the other teams still show (agents/README.md "The teams").

1. The open pull requests with the label `escalated`: `gh pr list -R REPO --state open --label escalated --limit 500 --json
   number,title,headRefName,url` (`gh pr list` reads 30 at most without `--limit`). For each one, read its reviews and comments (`gh pr view N -R REPO --json
   reviews,comments,labels,statusCheckRollup`) and the time the label went on (`gh api
   "repos/REPO/issues/N/events?per_page=100" --paginate`: the newest `labeled` event for `escalated`). The
   issue's key is in the branch's name.
2. The open pull requests with no auto-merge whose newest comment starts "Ready to merge. It changes a workflow file"
   (`gh pr list -R REPO --state open --limit 500 --json number,title,headRefName,url,autoMergeRequest`, then each one's comments). They carry
   no label `escalated` and their issue is in Approved, but they wait for the owner's merge. Their wait counts from that
   comment.
3. The issues in Escalated (`python3 .github/linear.py list TEAM --state Escalated --since`: `--since` gives the time each
   one entered the state), each with its comments. An issue whose pull request is in step 1 or 2 is one row with it, not
   two.
4. For the lines under the table: `python3 .github/linear.py list TEAM --state Triage --since` and `python3
   .github/linear.py list TEAM --state Todo --since`, the second one read for the labels `fast-lane` and `hands-off`. A fast-lane issue has "no run" while it has
   no comment that starts with `Run:` (agents/implementer.md "The run line").

## The rows

One table per team, under the team's key, with a team that has no row left out. One row per pull request or issue, the longest wait first:

| Key | Pull request | Wait | Reason | Action |
|---|---|---|---|---|

- **Key** and **Pull request** as links (AGENTS.md "Links in a reply to the owner"); "none" when the issue has no pull
  request.
- **Wait**: since the label `escalated` went on, else since the "Ready to merge" comment, else since the issue entered
  Escalated, in hours and minutes.
- **Reason** and **Action**: the newest of the artifacts below decides the row. Quote the question in a few words when
  the reason is a question.

| The newest artifact | Reason | Action |
|---|---|---|
| The issue's comment "The reviewer approved PR #N. It changes files that only the owner merges", and the head's check `verdict` passes | An approve on a code owner's file (the files listed in the comment) | Read the change, then add the label `owner-approved` on GitHub (or merge it there) |
| The comment "Ready to merge. It changes a workflow file, which the app cannot merge" on the pull request (step 2) | An approved workflow change | Merge it on GitHub |
| The issue's comment "The gates passed on a rerun at ... changes a file of CODEOWNERS, so it gets no auto-merge" (the workflow `verdict-rerun`) | An approve on a code owner's file, its gates green after a rerun | Merge it on GitHub, or add the label `owner-approved` |
| The issue's comment "GitHub refused to turn on auto-merge" | Auto-merge refused 5 times (its last answer, quoted) | Rerun the failed job of the workflow and run that the comment names (the review workflow or `verdict-rerun`), or merge on GitHub |
| The comment that starts "The gates failed on this approved head" (verdict.py `FAILED_GATES`) on the pull request, and the implementer's comment on the issue that the cause is outside the branch | Failed gates on an approved head, not the branch's fault | Rerun the failed job of the `gates` run on GitHub |
| A review whose verdict is `needs-decision` | The reviewer's question | Answer on the issue, then move it to Todo (or Canceled, and close the pull request). When the question is a step of yours before the merge (a reading or a look at the result; a choice only in an issue filed before 2026-11-23) and nothing asks for changes, do the step and add the label `owner-approved` instead: Todo would start a run with nothing to change |
| The comment "Round N is not reviewed: three rounds is the maximum" (before 2026-11-16 "two rounds is the maximum"; before that, "Review: round N is not reviewed. Two rounds at most") | Still blocked after the last round (the open findings) | Move the issue to Todo and start the run with `/implement KEY`; after a fix of your own, run the review by hand with round 1 (the comment's command); or add `owner-approved` to accept the pull request as it is; or move it to Canceled and close the pull request |
| The implementer's comment on the pull request with the patch of a file under `.github/workflows/` that the app cannot push (agents/implementer.md step 6) | A workflow file to change (the file, quoted) | Read the patch and add the label `apply-workflow-patch`: its workflow pushes the patch to the branch and marks the pull request ready (AGENTS.md "The guards") |
| The implementer's comment on the issue that a second ready mark also started no review (agents/implementer.md "When you are done" step 6) | No review runs on the pull request | Look at the Actions tab first: the review watchdog may have started it. When no review runs, start it by hand with the round left empty, `gh workflow run claude-code-review.yml -R REPO -f pr=N`, then move the issue back to In Review. Never a third ready mark and never `round=1` |
| The implementer's question on the issue (agents/implementer.md "Asking the owner") | The implementer's question | Answer on the issue, then move it to Todo (or Canceled) |
| A `Question:` issue with no pull request | The question in its title | Answer on the issue. A question that blocks a work issue: move it to Done, and the work issue starts after it. Else: work moves it to Todo with a size and a work title, no work to Canceled |
| Nothing that fits | "not clear": name the newest comment | Read the pull request and the issue |

An issue in Escalated whose pull request merged or closed is not a row: say under the table that its state is stale.

## Under the rows

One line each, per team (the team's key first):

- `Triage: N issues, the oldest AGE (KEY)`: the owner's `/triage`.
- `Fast lane with no run: N issues in Todo, the oldest AGE (KEY)`: an Area's session starts them (`/implement`).
- `hands-off in Todo: N issues (KEY, KEY, ...)`: the owner's own steps.

Then stop. The owner acts on GitHub and in Linear; a second `/queue` shows what is left.
