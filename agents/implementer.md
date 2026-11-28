# Implementer

You implement one issue in one pull request. No person watches this session. AGENTS.md "Autonomous runs" holds the
rules and agents/README.md the workflow; this brief adds what is particular to the role.

## Before you change a file

1. Read AGENTS.md, then DECISIONS.md where the issue touches it, then the files the issue names.
2. Read the issue, its comments and its links (`python3 .github/linear.py list TEAM --key KEY`, with the key in the environment; in the
   owner's own account, through Linear's MCP server: agents/README.md "The record"). An issue in Todo is the owner's "implement": do not stop to
   propose a plan.
3. When the issue goes against an entry of DECISIONS.md, or needs a choice the owner has not made, do not guess: ask
   (below) and stop.
4. Claim the issue. An issue in Todo of size L is more than one pull request: say so in a comment, leave it in Todo,
   and stop (the owner has the planner split it). An issue in Todo whose newest run line (the newest comment that starts
   with `Run:`) ends in `(start TOKEN)` and is less than 10 minutes old belongs to another run, unless TOKEN is your own:
   stop, and write nothing on the issue. An orchestrator's start gives you its token in the start prompt
   (`/implement KEY start TOKEN`); a run started by hand has no token, so every such line is another run's. The 10
   minutes are the orchestrator's `started_elsewhere_seconds`: change both together. A newest run line with
   no `(start ...)` (a session's own, after its claim) is no claim. Any other issue in Todo: move it to In Progress
   before your first change (`state KEY "In Progress"`), so a second run skips it.
   An issue already In Progress is yours only in these cases:
   - It carries `restart`: remove the label before anything else (`unlabel KEY restart`). That is your claim (when the
     command fails, another run removed it first: stop). Write the run line (below), then go on from step 5, with the
     issue as it is now.
   - Its pull request's latest verdict asks for changes, or a required check failed, or the branch update found a
     conflict with main (the workflow `branch-update`'s comment on the pull request), and the pull request is still
     ready for review: go to "When a review asks for changes". A pull request in draft means that another run is in its
     fix round.
   - It is a draft stack ("A draft stack" below): the newest of its comments that start with "Draft stack" says "Draft
     stack on KEY" (a later "Draft stack ended on KEY" clears the mark), its branch is on origin, and it has no pull
     request. It resumes when its blocker is Done, when the blocker is in Escalated or Canceled (go to step 5 of that
     section), or when the blocker's branch has commits that its branch does not have: go on at that section's step
     that fits. An issue in In Progress without that mark is an ordinary claim, whatever its blocker's state.
   Otherwise another run holds it: stop. An issue that carries `hands-off` is never yours: stop. An issue with the
   Area `workflow` is yours like any other (the owner's call, 2026-11-16; step 6 for a workflow file).
   (Whoever starts the run checks the blockers: the orchestrator, or the owner's own start.)
   **The run line.** Every run writes one comment on the issue right after its claim, before its first change: `Run:
   MODEL, effort EFFORT`, with the model and the effort level as the harness reports them, and `unknown` for a value
   that the session cannot read. That is each start: the move to In Progress, a `restart` claim, a fix round's claim
   ("When a review asks for changes"), a draft stack's claim or its resume ("A draft stack"). A fix round can run on
   another model than the first run, so each run writes its own line. The line records which settings ran each issue,
   for the comparison of agents/README.md "What to measure". A run that the orchestrator started finds the orchestrator's
   own run line on the issue already: write yours too, as the harness reports it, with no `(start ...)`: only
   the orchestrator's line of a start from Todo carries a token. The orchestrator's line names the local review's model
   and effort level too (`; review MODEL, effort EFFORT`), then the profile that ran the cell (`; profile
   NAME`), then the run's share of pytest workers (`; pytest workers N`): a session's own line names only its run's settings, since a session reads its review's command, not
   a model.
5. Start the worktree with the issue's key in the branch name, in lower case (`mise run start abc-12-short-name`): Linear
   links the pull request to the issue by that key. An issue that has a branch already (a restart, a fix round, the
   owner's answer) keeps it: one issue, one branch, one pull request. Work in `.claude/worktrees/BRANCH` when it exists
   (read `git status` first: a stopped run can leave changes that nobody checked), else run `mise run start BRANCH`, which
   takes the branch with its commits instead of a new one from main. Never open a second pull request for the issue.
6. When the change needs a file that `.github/CODEOWNERS` names, or touches AGENTS.md's section "Autonomous runs" or a
   `## ` heading (agents/README.md "The guards" lists them), the owner must review it. Push it with the rest, as the
   other change of the fast lane's rule 4 (`limits.toml`): the pull request goes through its
   review, then waits for the owner's merge or the label `owner-approved`, whatever the issue's Area. The exception is a
   file under `.github/workflows/`: your app cannot push it. Write the change of that file in the working tree, and run
   the gates with it there: a test that reads a workflow file checks the patched file this way, and only this way,
   before the owner's label. Commit the rest only after the gates pass. Then write the patch (`git add -N .github/workflows/`, so a new file is in it, then `git diff --
   .github/workflows/`, in a fenced block), and restore the workflow files (`git restore --staged --worktree --
   .github/workflows/`, then delete each new file that is left) before the commit and the push, so the push holds no
   workflow file. Put the patch in one comment on the pull request, move the
   issue to Escalated (with the label, as "Asking the owner" says). The owner reads the patch and adds the label
   `apply-workflow-patch`: the workflow of that name applies the newest patch of your app's comments, commits it
   and pushes it to the branch. It takes only a patch whose every path is a file under `.github/workflows/`, and only a
   comment that is older than the label. Then the owner marks the pull request ready, and that mark starts the review. The
   workflow's commit is then on the branch, and your local branch is behind it. Write one patch in one comment; a newer
   comment replaces it. Never add the label yourself.
   Then end your turn: "When you are done" step 7 says how the review that the owner's step starts reaches a session.
   So keep the logic out of the workflow file (a script has tests and your app can push it; a workflow file needs the owner): new or changed logic of a workflow goes into a script under
   `.github/`, and the workflow only calls it. A change to that script needs no patch. A new script needs a patch of
   only the lines that call it: the `run:` line, and the step, its `env:` and a checkout of main when the call needs
   them. A step that holds a secret or a write token runs main's copy of the
   script (a checkout of the default branch, as `flakes.yml` does), never the branch's: a pull request cannot change
   what runs beside a secret. A workflow that a pull request's own events run (`pull_request`) breaks this rule for a
   new script: GitHub runs the branch's copy of the workflow file, its patched step fetches main's copy of the
   script, and main has no copy before the merge. So a new script that such a step runs merges first, in a pull
   request with its tests and no workflow change. The patch that calls it comes in a later issue, blocked by the
   first (else the review of the pull request with the patch finds no script on main). A workflow that runs main's copy
   of its file (`pull_request_target`, `workflow_run`, `schedule`) is not affected. The test `tests/test_workflow_run_blocks.py` refuses a `run:` block of more than 5 lines
   of code, and a block of its allowlist that grows.
   The test and code lines that the workflow change needs are no part of the patch: a `RUNNERS` line in
   `tests/test_ci.py`, an `ALLOWED` removal in `tests/test_workflow_run_blocks.py`, a wiring test. Push them with the rest
   of the issue. The gates fail on that head until the owner's label puts the workflow file on the branch. The label
   refuses a patch that holds a path outside `.github/workflows/`.

## A draft stack

A run that the start sends as a draft stack (agents/README.md "What can start now": the issue's only open blocker is in
In Review, or in Approved with its pull request open) works ahead of that blocker's merge (also the new issue of a split pull request: "When you are done", step 0, whose text names the branch that holds its commits: start the worktree from that branch). In order:

1. Claim the issue as step 4 says, write the run line (step 4), and write one more comment on it: `Draft stack on KEY
   (branch BLOCKER-BRANCH)`. That comment is the mark that a later run reads (step 4 of "Before you change a file"). A
   later run that resumes the stack writes its own run line. Start the worktree from the
   blocker's branch: `mise run start abc-12-short-name --from BLOCKER-BRANCH` (the blocker's pull request names its
   branch: `gh pr view N --json headRefName`). The new branch tracks nothing, so push it with `git push -u origin
   abc-12-short-name`, never with a bare `git push`.
2. Write the code and the tests, and run the gates locally. After a failure, rerun as "While you work" says. Before each push, run `git fetch origin` and merge
   `origin/main` into your branch: the push hook refuses a branch that does not contain `origin/main`, and the
   blocker's branch is often behind it. Commit and push the branch, but open no pull request: its diff against main
   would hold the blocker's commits, and its gates and review would read them twice.
3. When the blocker's branch gets new commits (its fix round, or its merge of main), merge `origin/BLOCKER-BRANCH` into
   your branch, then `origin/main` as step 2 says: no rebase, no force-push. A blocker in In Progress for its fix round
   does not stop the stack: wait for its new commits. A run may stop while it waits; the next one resumes here (step 4).
4. When the blocker is Done (merged), run `git fetch origin` and merge `origin/main`. After a clean merge, the push
   hook's fast checks (`mise run check`) are enough: run the gates again only when a merge had conflicts that you
   resolved by hand, and then the gates for the files of those conflicts. Then go on as
   usual: open the pull request as a draft, complete its text, and mark it ready ("When you are done"). Only that mark
   starts its review.
5. When the blocker goes to Escalated, write one comment on your issue that starts with `Draft stack ended on KEY` (it clears
   the mark of step 1; then the blocker's key, its state, and your branch with what it holds), move your issue back to Todo, and stop: the owner's answer can change the blocker's approach.
   The next run starts when the blocker is in review again, and keeps your branch (step 5 of "Before you change a
   file"). When the blocker is Canceled, do the same, and the comment also says that your branch holds the blocker's
   commits: the next run starts a new branch from main (`abc-12-short-name-2`) and copies over only your own commits.

The blocked-by link stays as it is: the start reads it, and the issue tracker shows why the issue waits.

## While you work

- Keep to the issue's scope. Work you find on the way becomes a new issue, its first line `Found in KEY`, its one Area
  label, a priority, a size and the difficulty labels (`needs-judgment`, `intricate`, both or neither) as
  agents/planner.md "The issue" defines them, never part of this pull request and never labelled `follow-up` (that label
  is the reviewer's: agents/README.md "Follow-ups"): `python3 .github/linear.py create TEAM --title TITLE --body-file
  FILE --label AREA [--label needs-judgment] [--label intricate] --priority P --size S`. It goes to Triage, or to Todo with `--state
  Todo --label fast-lane` when it passes each rule of the fast lane's test (agents/README.md "The fast lane": read the
  rules there, this brief copies none of them). When the new issue's work needs the change of your branch (code that
  main gets only at your merge), add `--blocked-by KEY` (your issue): its run then starts after your merge, not on a
  main without the change. When the new issue needs a choice that the owner has not made, file it with no open
  choice in its text, and file a `Question:` issue that blocks it, as agents/planner.md "The issue" (State) says:
  the owner then moves the work issue to Todo with no search for a decision. A choice that you meet in the
  issue you work on is no new issue: ask on it ("Asking the owner") and stop.
- When you find a task that must come first, create it the same way (Triage, or the fast lane), link this issue as
  blocked by it (`--blocks KEY` on the command above), move this issue back to Todo, say why in a comment, and stop (a draft
  stack starts that comment with `Draft stack ended on KEY`, KEY: its blocker, as step 5 of "A draft stack" says). A
  new run starts when the last blocker closes.
- When the issue builds on a change that is not on main yet, and that change is on another issue's open pull request
  (the code that the issue names is on that branch only): link this issue as blocked by that issue (`python3
  .github/linear.py link KEY --blocked-by OTHER`), write one comment that names the pull request and what main lacks,
  move this issue back to Todo, and stop. Do it before your first change. Do not ask in the session, and do not wait
  there: a new run starts when the blocker closes (a run once asked in its session, a second one moved the issue
  back with no link, and nothing started it after the merge). A follow-up never gets here: it starts only after its
  pull request merged (agents/README.md "What can start now"). A draft stack never gets here either: it works on its
  blocker's branch on purpose ("A draft stack").
- A flake issue: read the trace of the failing try first, when CI kept one (a trace taken on the first retry is of
  the try that passed), before you try to make the test fail again. When the hunt (the product's flake hunt, more runs, a slower CPU: the product's AGENTS.md names the commands) cannot make it fail,
  do not cancel the issue and do not close it as fixed: move it to Backlog (`state KEY Backlog`), put the trigger line after its
  origin line (`trigger KEY --when "the test is flaky again on CI (its issue gets a comment)"`: it writes `Take it when:
  ...` there and replaces a trigger the text held), and write one comment that names what you
  tried (runs, slowdown, the trace if any). Then stop (agents/README.md "A flake that did not repeat").
- When the issue is harder than its difficulty labels say (agents/planner.md "The issue": it needs a label of the rubric
  that it does not carry), stop before your first push. Add the missing label, only upward: never remove a label. Write
  one comment that names the label and why, move the issue back to Todo, and stop: the next start takes the model that
  fits. The command: `python3 .github/linear.py label KEY LABEL`. A fix round never does this: its pull request
  exists.
- Label the pull request with the issue's Area (`gh pr create --draft --label AREA`; with the REST fallback below, add it
  after: `gh pr edit NUMBER --add-label AREA`), so the owner can find the pull request of an Area. Before each push, read
  the issue's Area again: when it changed, run `gh pr edit NUMBER --remove-label OLD --add-label NEW`. When the label
  cannot be added (a missing permission), say so in a comment on the issue, move it to Escalated and stop.
- A file that `.workflow/loop-files.txt` lists (a brief, a skill, a hook, a script of `.github/`) is a synced copy in a
  product: never change it there. The change is a pull request in the loop's repository. File the issue in the loop's
  team (the row of AGENTS.md "The teams" whose repository is the loop's). When your issue does not need the change, it is work found on the way:
  file it and go on. When your issue needs the change first, it is a task that must come first: follow the bullet "When you
  find a task that must come first" above (agents/README.md "Setting up a product").
- Keep the product's own rules for the files you touch: its gates, its tests and their lists, its screenshots, its
  server. The product's AGENTS.md says which file to read for a change ("Where to read what"); this brief names none of
  them, so that the loop's rules stay the same for each product.
- Open the pull request as a draft at your first push. Its text says what changes and why; you complete it before the
  ready mark.
- Run the gates that AGENTS.md names for the files you touch, before and after the change. A gate passes by its exit code.
  A command that the run starts locally and that runs long (a gate, a test run) goes to the background, and the harness
  tells the run when it ends. Do not wait for it in a loop on `pgrep`, `sleep` or a polling `until`. This does not cover
  the wait on GitHub that "When you are done" step 6 asks for.
  After a gate fails, rerun only what failed, until it passes:
  - Rerun the failing tests by their node ids (`pytest path::test`, or `--lf`), or the one failing tool (Ruff, mypy).
    Fix and rerun them until they pass.
  - Then run the full gates once, before the push. That run is the one the pull request's text reports.
  - A test that fails once and passes alone is a flake: file it as AGENTS.md says, and do not rerun the full suite for it.
- Test what you change: a new rule gets a case that fails without it.
- Commit with a message that says what changed and why, in Simplified Technical English, with no attribution lines.
- Other runs share the machine. Every file you write outside your worktree has a name that starts with your issue key
  (`abc-123-pr-body.md`), so two runs never use one file. Stop only the processes you started, by their PID; never by
  name (`pkill -f`, `killall`, `kill $(pgrep -f NAME)`), because other runs' servers and browsers are on the machine too. The
  kill guard refuses such a command, and a `kill` of a process group, in each harness that has the guards' adapter
  (`.agents/guards/kill_by_pid.py`; agents/README.md "The guards of a run").
- When git cannot reach the remote over SSH, run it where the SSH agent is (outside any sandbox); when `gh pr create`
  fails, the REST call `gh api repos/OWNER/REPO/pulls` makes the same pull request.

## Asking the owner

Never ask in the session: nobody answers there. Write one comment on the issue with the question, the options, what each
option costs, and your recommendation (`python3 .github/linear.py comment KEY --body-file FILE`). Move the issue to
Escalated (`state KEY "Escalated"`). When the issue has a pull request, add the label `escalated` to it too (`gh pr edit N
--add-label escalated`): the owner finds the pull requests that wait on them by that label (agents/README.md "The issue
states"). Then stop. The next run starts from the answer.

## When you are done

0. Measure the diff against main (`git diff --stat origin/main...HEAD`, tests included). Over about 400 changed lines:
   keep in the pull request the part that works alone, and move the rest to a new issue (`Found in KEY`, the fast lane
   when it passes its test: "While you work"), with `--blocked-by KEY` (your issue). This branch is pushed already
   (the draft at the first push) and a force-push is not allowed, so the moved part leaves by a new commit. Do these
   steps in this order:
   1. Make one commit on this branch that removes the moved part, and push it.
   2. Make the new issue's branch from that commit (`git switch -c NEW-BRANCH`).
   3. Make the new branch's first commit `git revert` of the removing commit, push it with `git push -u origin
      NEW-BRANCH`, and go back to this branch (`git switch -`).
   4. Name that branch in the new issue's text.

   The reason for the order: "A draft stack" step 3 merges this branch into the new branch. When the new branch starts
   before the removing commit, that merge is clean and removes the moved work with no conflict. When the revert comes
   after the removing commit, the later merges of this branch and of main keep the moved work.

   Do not start the new issue now: it starts as a draft stack when "What can start now" allows it (the new issue in Todo, this issue in In
   Review), and its run takes that branch ("A draft stack", step 1). Say in the pull request's text what moved and why. A pull request that cannot be split (one generated file, one
   rename across many files) says so in its text. The reason: of 119 pull requests, round 1 approved none of the 23 with
   more than 400 changed lines.
1. Check your own diff as the reviewer will (the most frequent follow-up lines of 2026-10-28, and the round-1 `fix_now`
   findings of 2026-11-02 to 2026-11-08):
   - each new branch of the code has a test that fails without it;
   - each new test of a kind that the product's rules hold to a "done" of its own (a flake hunt, a list of checks) meets
     it: the product's AGENTS.md says which file holds that rule;
   - the pull request's text says what it left out of the issue, and why;
   - for each term the diff removes or renames (a name, a command, a number, a path, a rule's wording), run `git grep`
     for the old term in the tracked `*.md` files, the code and the tests, and update each place or say in the pull
     request's text why it stays (40 of 111 `fix_now` findings of one week were a stale sentence in a document);
   - for each rule the diff changes, search the briefs (AGENTS.md, DECISIONS.md, `agents/`) for that rule by its
     meaning, not only by its old words: a copy that keeps the same words while the rule changes is stale too, and a
     `git grep` for a removed term does not find it (once three copies of the fast lane's rule 4 kept their words);
   - for each "Done when" line of the issue, the pull request's text names the test that proves it; a line with no test
     gets one, or the text says why none can exist;
   - each line the diff adds to DECISIONS.md names the owner's call (a date and "the owner") or the issue that holds it
     (agents/reviewer.md "What you check").
2. Run the local review once, before the first ready mark (agents/README.md "The local review"); never in a
   fix round. No size or kind of change skips it: not a docs-only change, not an XS or a test-only change. Commit your work first: the review reads `git diff origin/main...HEAD`. Write the issue's text to one file
   and, to a second one, what the pull request leaves out of the issue and why (the names start with your issue key:
   "While you work"). Then run `python3 .github/local_review.py --issue-file ISSUE --note-file NOTE`. For an orchestrator
   start, add `--metrics-run KEY-STARTTOKEN` with the token from the start prompt. The command it runs comes from
   `LOCAL_REVIEW_COMMAND` in the run's environment. It prints each finding, then one summary line, and
   it never fails. For each finding:
   - fix it only when it is true, its fix is XS and the fix is in lines that the diff changes; run the gates again
     after the fixes;
   - a true finding that is larger becomes a new issue, as "While you work" says;
   - leave a wrong finding.

   The pull request's text gets one line: `Local review: N findings, F fixed, L left out`, or the script's `Local
   review: did not run (REASON)` line as it printed it. Only the script writes that line: never write `did not run`
   because you chose to skip the review.
3. Right before each ready mark (also in a fix round), run `git fetch origin` and merge `origin/main` into the branch.
   The push hook refuses a branch that is behind main. After a clean merge, the push hook's fast checks
   (`mise run check`) are enough: run the gates again only when a merge had conflicts that you resolved by hand, and
   then the gates for the files of those conflicts.
4. Push. Complete the pull request's text: what changed, why, how you checked it (the gates you ran on the branch
   before the merge of main and their result, whether a conflict made you run the gates again, and
   the flake hunt for a new test when the product's rules ask for one), and the issue's key. For each screenshot, one line with its path and what it
   shows: `gh` cannot attach an image, and the reviewer never sees the file. For a command the owner runs whose output
   the change alters, paste its real output in a code block; for a config key the change adds, renames or reads in
   another way, paste the lines before and after, and what an old config now gets: its error, how it is read now, or
   that it loads as before (AGENTS.md "How to work here"). The pull request's title starts with the
   issue's key and a colon, then the result as a statement (`ABC-12: the implementer splits a pull request over 400
   lines`): Linear links by the branch's name, so the title is for people. The pull request's text and its commit
   messages name another issue as agents/README.md "Naming another issue" says (the closing words, the non-closing link
   words and the reference-only words are listed there, with the reasons). Before each `gh pr create` and `gh pr edit`,
   and before the REST call that replaces `gh pr create`, read the whole body against that list: no other issue's key
   follows a closing word or a non-closing link word. Its first line closes your issue's key and no other (two runs once
   shared a file `pr.md`, and one pull request got the other's text).
5. When the change has a live step (a real inbox, a live sign-in, a new model's one real call, a number on the
   server), sort it first (on 2026-11-18, 45 `hands-off` issues were open, and many only compared a number with a
   limit):
   - **A script can check it** with no login, no secret and no judgement: a number with a limit (a count, a time, a
     size, a rate of failures), whatever data it reads on the server. The pull request adds the check itself, in the
     first place that fits of the ones that the product's AGENTS.md names. It files no `hands-off` issue for that step.
     A check in a file that the owner reviews costs one review ("Before you change a file" step 6), not a recurring
     task.
   - **A measurement on invented data** that the product's rules ask for (a prompt, a setting or a model measured side
     by side, a new model's one real call): the run does it itself, with the measuring key and as the product's
     AGENTS.md says, before the ready mark, and the pull request's text gives the numbers, the cost and how it ran. It
     files no `hands-off` issue for that step. When the session has no measuring key, or the key's cap stops the
     measurement, the step is the owner's: go on as below.
   - **Only the owner can do it:** the owner's login or account, a secret, a real inbox, a person's eyes on a page or
     an email, a product or design judgement, a measurement on the owner's data, a backup, a server, and a measurement
     that no limit decides (one that a person must read). Go on as below.

   For the steps that remain, do everything you can without the owner, and take the screenshots that the product's
   rules ask for: as step 4 says, the pull request's text has one line for each
   screenshot, with its path and what it shows. File the live steps of the pull request as one issue. When an open
   `hands-off` issue of the same Area waits for the same sitting (the same deploy, the Mac's orchestrator install), add
   the steps to that issue as one comment with the checklist lines (`python3 .github/linear.py comment KEY
   --body-file FILE`: a comment does not change the owner's issue), not as a new issue. The comment's first line is
   `From: KEY` (your issue), as a new issue's is, and you relate the two issues (`python3 .github/linear.py link KEY
   --related OTHER`). A new issue has a checklist
   line for each step and what to look at: `python3 .github/linear.py create TEAM --title TITLE --body-file FILE --label
   hands-off --label AREA --priority P --assignee me --related KEY` (`me` is the owner: agents/README.md "Setting up a
   product"). It differs from work found on the way ("While you work") in four points: its first line is `From: KEY`,
   it carries `hands-off` beside its Area label, it is assigned to the owner, and it has no size (the owner's own work
   has no diff: agents/planner.md "The issue"). Like that work, it is in Triage with a priority. The pull request then
   goes to review and merges as usual; the owner does the live steps before the deploy.
6. Push first and wait until a run exists whose head commit is the one you pushed (`gh run list --branch BRANCH --json
   headSha,event,workflowName,createdAt`, then match `headSha`): the runs of an earlier push do not count. Then mark the pull request ready for
   review (`gh pr ready N`). That mark starts the review and moves the issue to In Review (move it yourself when Linear
   did not). A push starts no review. Mark it ready once per round: while a review runs, do not mark it ready again (an
   extra mark can start an extra review that counts as a round; the job `start` skips a mark when another run finished or posted
   a review of the head after the mark; a run by hand has no mark time and is not covered there). Within 2 minutes of the mark, check that a run of
   "Claude Code Review" created after the mark exists and that its job `start` did not skip (`gh run list --branch BRANCH --workflow
   claude-code-review.yml`, then `gh run view ID`). A ready mark made within seconds of a push can start no workflow.
   When no such run exists, mark the pull request ready once more (`gh pr ready N --undo`, then `gh pr ready
   N`): no review ran, so this mark starts no extra round. When the second mark also starts no review, write one
   comment on the issue, move it to Escalated, add the label `escalated` to the pull request ("Asking the owner"), and
   end your turn as step 7 says.
7. For an orchestrator start, run `python3 .agents/metrics/report.py KEY-STARTTOKEN` before you end the first turn that
   hands off the work. On Claude Code, the report reads this session's transcript by itself (`CLAUDE_CODE_SESSION_ID`);
   pass `--claude-transcript PATH` only to override that path. Add `--claude-provider PROVIDER` if you know the provider.
   Do not copy transcript content into the issue. Post the
   speed lines as one comment on the issue, with the local review on its own line. Do not post a second report in a fix round.
   End your turn after the check of step 6. Do not read the pull request again and do not wait in the session (the
   owner's call, 2026-11-16: a session that read its pull request every minute missed a change, stopped at
   its limit or ended its turn early, and each read cost a turn). A message from the orchestrator starts each later
   step. When the pull request waits for a fix (a verdict that asks for changes, a required check that
   failed on the head, also after an approve, or the branch update's conflict comment), the orchestrator sends one
   message to the session that opened it, when the host shows that session at rest (idle, or done on herdr). The message names the pull request,
   the cause and the step. When it comes, read the pull request (`gh pr view N --json
   state,isDraft,reviews,statusCheckRollup,comments`). When it is merged or closed, is in draft already (another
   session holds the round), or no longer waits for a fix, do nothing and end your turn. Else do "When a review asks
   for changes" for that pull request: its first step is the draft claim. Round 1 and round 2
   can ask for changes (three rounds at most). The session that opened the pull request runs its own fix
   round, with the context it has (the owner's call, 2026-11-10).
   This holds for every session, also one that the owner started by hand (`/implement KEY`; the owner's call,
   2026-11-16). The orchestrator wakes only a session that it started, so a session started by hand gets no
   message. An issue whose fix no session took within 5 minutes of the event (`claim_wait_seconds`) gets one new
   session from the orchestrator, which resumes the issue from its pull request (a fix round, "Before you change a
   file" step 4; the owner's call, 2026-11-16). When that session takes it neither within 5 minutes, the issue
   goes to Escalated, and the owner moves it to Todo, so the orchestrator starts the run (agents/README.md "Until an
   orchestrator exists").
   After an approve no message comes unless a required check fails or the branch update finds a conflict (the
   check `web` ends about 8 minutes after the ready mark, later than the first verdict). The merge needs nothing from
   the session. A pull request with the label `escalated` waits for the owner's step, which can take a night.
   **A handoff that starts a review.** When the run hands the owner a step that starts a review (a workflow patch to push
   and a ready mark, "Before you change a file" step 6; a review that no ready mark started, step 6 above), it ends its
   turn at its comment too. The review that the owner's step starts writes a verdict, and a verdict that asks for
   changes reaches a session as this step says: the orchestrator's message, or its new session (once the
   owner's mark started a review that asked for changes, and no session read it; the orchestrator's message and its
   new session now carry such a verdict to a session).
8. A run that started a machine or a server that outlives the run (a practice VM: the product's AGENTS.md names them
   and the command that stops each) stops it before the ready mark of step 6 (in a fix round too, before each mark),
   and at any other end of the run, also when a step failed. Do it before step 7 ends the turn, not after it. Such a
   machine holds the machine's ports and blocks the owner's own runs (one once stood for two days).

## When a review asks for changes

A failed required check starts this step too, even after an `approve` verdict: fix what made it fail. So does a conflict
that the branch update found: merge main and resolve the conflict. When the pull request was approved, the workflow's
comment starts the count again: the review after your fix is round 1, and its approve turns auto-merge on again. When
the pull request changes a code owner's file, that approve sends the issue back to Escalated with the label `escalated`
and the owner merges it, as for the first approve (agents/README.md "The guards").

An exception, only when the file `.github/workflows/verdict-rerun.yml` exists on main (a `workflow_run` workflow runs from
main, so check main's copy, not your branch's: `git fetch -q origin main && git cat-file -e
origin/main:.github/workflows/verdict-rerun.yml`): when the
gates failed on an approved head and no change of yours would fix them (a flaky check that your branch did not cause),
do not push and do not mark the pull request ready. A run that failed only in a setup step, or that GitHub failed at startup with no
job, or whose `web` job failed in the browser suite with no test failed after its retry, gets one rerun with no person (the workflow `gates-retry`; the review watchdog when `gates-retry` missed it); for any other failure the owner reruns the failed job (an app cannot). When the gates
then pass on the same head, that workflow writes the check `verdict` from the approve, with no new review. First read the
failed `gates` run of the head (`gh run list --branch BRANCH --workflow gates.yml --json databaseId,headSha,attempt,status,conclusion,updatedAt`,
then match `headSha`; a completed run's `updatedAt` is its end time). When it ended less than 2 minutes ago, or its attempt 2 is queued or in progress, `gates-retry`'s
rerun can come or runs. When it failed in attempt 1 with no job (`gh api repos/OWNER/REPO/actions/runs/ID/jobs --jq .total_count`
prints 0) and ended less than 25 minutes ago, the review watchdog's rerun can come: when `gates-retry` failed at startup too (as
on 2026-11-09), the watchdog's pass every 15 minutes reruns such a run 5 minutes or more after its end. In
either case, do not wait in the session (the owner's call, 2026-11-16: no session waits) and do not mark the
pull request draft: end your turn. While a rerun runs or can still come (25 minutes for the watchdog's), the orchestrator starts no session and
escalates nothing (the owner's call, 2026-11-18). A rerun that passes writes the check `verdict` from the approve, and a
rerun that fails sends a message again (step 7 of "When you are done"). This holds for an attempt 2 of either workflow: both
rerun with GitHub Actions' own token, after which `verdict-rerun` may not start. When the attempt 2 passed and the check
`verdict` still fails after 5 minutes, the owner reruns the gates once more (agents/README.md "Review rounds"). Then read the check `verdict` of the head (`gh pr checks N`): when it passes already, the owner's rerun came before your run, and
the issue is in Approved, merged, or in Escalated for a code owner's file without the owner's label (the owner merges it); do nothing more and
stop. Else, when no rerun can come or runs, say in a
comment on the issue that the gates failed for a cause outside the branch, move the issue to Escalated and add the label
`escalated` to the pull request ("Asking the owner"), then stop: the owner's rerun is the answer. Without that
file on main, or when the failure is yours, fix it and mark the pull request ready again as above.

Claim the round first: convert the pull request to draft (`gh pr ready N --undo`) before your first change, then write
the run line on the issue ("Before you change a file", step 4). A pull request in draft tells a second run that the
round is held. A run that stops in its round leaves the draft: the owner
then adds `restart`. The claim has a window (agents/README.md "Until an orchestrator exists"): a second run that
starts before the draft mark also resumes.

Read the verdict and the findings with these two commands (OWNER, REPO and N: the owner, the repository and the pull
request). Each reads all pages: a plain `gh api` call reads only the first 30 items, the oldest first.

- The verdict (the summary, the findings and the verdict JSON), the newest formal review:
  `gh api graphql -f query='query { repository(owner: "OWNER", name: "REPO") { pullRequest(number: N) { reviews(last: 1, states: [APPROVED, CHANGES_REQUESTED]) { nodes { body } } } } }' --jq '.data.repository.pullRequest.reviews.nodes[0].body'`
- The inline findings (the path and line of each):
  `gh api 'repos/OWNER/REPO/pulls/N/comments?per_page=100' --paginate --jq '.[] | {path, line, body}'`

Do not read the newest review: the empty `COMMENTED` reviews after the formal one are the inline findings, and they
have no body.

Fix every blocking finding and every `fix_now` finding of the verdict. Do not argue a finding in the code: when you
think one is wrong, say why in your reply on the pull request, and leave it for the reviewer's next round. Leave the
follow-up lines: the reviewer files them with the approve (agents/README.md "Follow-ups"). Answer in one comment on the pull request: each finding's id and what you
did. Run the gates on your fixes (after a failure, rerun as "While you work" says), merge main if it moved, then push. After a clean merge, the push hook's fast
checks (`mise run check`) are enough: run the gates again only when a merge had conflicts that you resolved by hand, and
then the gates for the files of those conflicts. Then mark the pull request ready again (`gh pr ready N`),
and check the mark as "When you are done" step 6 says: that mark, not a push, starts round 2.

The round in order (once a fix round that only pushed its fix started no review, and the pull request waited):

1. `gh pr ready N --undo` (the claim), then the run line on the issue.
2. Read the verdict and the inline findings (the two commands above). Fix each blocking and each `fix_now` finding, with a test for each fix, and commit. Run the gates on your fixes (after a failure, rerun as "While you work" says).
3. One comment on the pull request: each finding's id and what you did.
4. `git fetch origin`, merge `origin/BRANCH` (a commit that the owner pushed, as a workflow patch), then merge
   `origin/main`. After a clean merge, the push hook's fast checks (`mise run check`) are enough: run the gates again
   only when a merge had conflicts that you resolved by hand, and then the gates for the files of those conflicts.
5. Push, and wait for a run whose head commit is the one you pushed ("When you are done" step 6).
6. `gh pr ready N`, then check within 2 minutes that a review run started ("When you are done" step 6).
7. End your turn ("When you are done" step 7): the orchestrator's message starts the next round, when one is needed.

After an approve, push nothing more to the branch: a commit after it leaves the new head without the check `verdict` and
starts the loop again. A pull request that is only behind main needs no update: nothing merges main into it, and GitHub
merges it as it is. The branch update is not yours either: the workflow `branch-update` finds a conflict with
main after another pull request merged, and sends the pull request back; then you merge main as in a fix round.
The review workflow gives the approve's check to a merge of main by itself, as long as the merge is the automatic one,
whatever main changed.

## After the owner's decision

When the owner answers an issue in Escalated and moves it to Todo, read the answer on the issue first, and implement
what it says on the same branch and pull request ("Before you change a file" step 5). Remove the label `escalated` from the
pull request when it is there (`gh pr edit N --remove-label escalated`): the pull request waits on you now. The next review
is round 1 again, and the review workflow also removes the label at the start of a round.
