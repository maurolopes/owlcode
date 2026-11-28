# owlcode: the agent loop

This repository holds the agent loop: the orchestrator, the review workflows, the scripts of `.github/` and `.workflow/`,
the roles' briefs (`agents/`), the skills, the hooks and the guards. Products take its files at a pinned commit
(DECISIONS.md "A product takes the loop's files at a pinned commit"). Read `DECISIONS.md` (the decisions in force, each
with why) first when picking this repository up. Everything still open is an issue in Linear, in the team of "The teams" below: the tasks, the
owner's own steps (label `hands-off`) and the questions that wait on the owner (state Escalated). Before work, check `git
status`, `git worktree list`, `gh pr list` and `gh run list --limit 3`.

This repository is public, or will be. No file, commit message, branch name, pull request, review or comment here names
a product of the owner: no name, no issue key of a product's team, no product word, no link to a product's issue, and no
private repository (DECISIONS.md "The agent loop moves to its own public repository"). Examples use `acme/product`, the
team `ABC` and the agents' user `agents`.

## Where to read what

This file holds what every session needs. A module's docstring says what the module does: read it there. Every other rule
lives beside what it is about, and a session reads that file before its first change there:

| What you change | Read first |
|---|---|
| `agents/`, a role's brief, the workflow end to end | `agents/README.md` and the role's brief |
| `orchestrator/` | `orchestrator/README.md` |
| a command that the owner runs by hand | `README.md` |
| a decision, or code that goes against one | `DECISIONS.md` |

The briefs (`agents/implementer.md`, `planner.md`, `reviewer.md`, `local-review.md`, `verdict.schema.json`) send a run to
the AGENTS.md of the repository that it changes. What they leave to this repository:

- **The gates for the files of a diff** (the reviewer's gates when the `gates` workflow is off, the verdict's `gates`):
  `mise run gates` for every change. Its check is the job `python` of the workflow `gates`; for a docs-only change the job
  `changes` skips its steps and the job passes (`.github/gates-needed.sh`): a gate job never skips as a whole.
- **A split by files** (planner.md "One issue or several"): the orchestrator (`orchestrator/`), the review and the other
  workflows with their scripts (`.github/`), the owner's and the agents' tools (`.workflow/`), the briefs (`agents/`),
  the skills (`.agents/skills/`) and the guards (`.agents/guards/`, `.githooks/`). The `intricate` label's security
  cases here: a key's path (the app tokens, the Linear key, the keychain), the guards, and `.workflow/agent_user.py`.

A change that adds or alters a command the owner runs by hand updates `README.md` in the same commit.

## The teams

The Linear teams of this repository, and their repositories: the table that agents/README.md "The teams" names (its
rules are there). The skills read this table. A product's own AGENTS.md holds its own table.

| Team | Repository | The owner's checkout | The Linear key |
|---|---|---|---|
| OWL | maurolopes/owlcode | `~/dev/owlcode` | `LINEAR_API_KEY` |

## Rules

- **Delivery: main only receives merged pull requests.** The ruleset `main` enforces it on GitHub, and the git hooks are
  the guard on every clone: `mise run hooks` once per clone (no commit on main, no push to it). `--no-verify` is the
  owner's, never an agent's.
- **Each change gets its own worktree and branch with the same name** (the owner's rule). Questions and reading need
  none: they happen in the main checkout. Before the first change to a tracked file, run `mise run start NAME` (NAME:
  the issue's key, then the change in a few words, kebab case: `owl-12-short-name`) and work in the folder its last line
  prints. One concern per branch. Never `git worktree remove --force`
  a worktree you did not make.
- **The fast checks run on push, the gates on the pull request.** The commit hook runs lint and types on what is
  staged; the push hook refuses a branch that does not contain a freshly fetched `origin/main` and runs `mise run
  check`. The `gates` workflow runs the gates on every pull request (its job `python`). A red `main` is fixed before
  anything else; a flaky test is fixed or deleted, never rerun and forgotten. A session that sees a flaky test or a race
  that its change did not cause files an issue at once (agents/README.md), after a search for an open one.
- **Merge with a merge commit, never squash or rebase.** The commits are the record (one issue each, with the reason).
  Keep a branch current by merging main into it: no rewritten commits, no force-push. GitHub deletes the remote branch
  after the merge ("Automatically delete head branches" is on).
- Commit messages and pull request texts carry no agent attribution (no `Co-Authored-By`, no session link). This holds
  also when the harness asks for attribution lines. Leave them off without a word: no reply, report or summary says that
  they were left off.
- **A change that becomes a pull request has its issue first, in a session with the owner too.** File the issue (the
  skill `issue`, or `python3 .github/linear.py create`) before the worktree and the first change to a tracked file. The
  issue's key starts the branch's name and the pull request's title.
- This repository's required checks are the job `python` of the workflow `gates`, and `verdict`, the reviewer's
  verdict (agents/README.md "The guards").
- Use ASD-STE100 Simplified Technical English for all prose the agent writes: replies to the user, docs, code comments,
  commit messages, and PR text. Use active voice, simple tenses, one idea per sentence, and consistent terms.
- Each session works in its own worktree, so a commit may take what the branch changed. Read `git status` first and
  leave out what a tool left in the tree. Never `git stash`: every worktree shares one stash, so a pop can take another
  session's entry. Never delete an untracked file you did not make.
- A commit hash quoted in a document is written after the commit is final: `git commit --amend` (or a rebase) gives a
  new hash, so grep the docs for the old one before pushing. Amend only what is not pushed yet.
- Before a push, the leak guard, in a session with the owner (the patterns are the owner's, in no repository). Set
  `LEAK_PATTERNS_FILE=~/dev/leak-patterns.txt` for both commands. `git ls-files > /tmp/f && python3
  .github/leak_guard.py files /tmp/f --counts-only` checks every tracked file. `python3 .github/leak_guard.py stdin`
  checks the text on its stdin: each commit message, and the pull request's title and body. Each prints `total: 0`.
  (`mise run leak:count` reads only the files of `.workflow/loop-files.txt`: it is not enough here.) An autonomous run
  has no patterns file: it names no product, and the owner's check before a merge covers the rest.

## How to work here

- **Question, recommendation in prose, then the owner says "implement".** A plan with steps: finish each with the gates
  green, report what it found, wait. A run started from an issue follows "Autonomous runs" below instead.
- **Measure, never assume:** a real run, a dry round of the orchestrator, a test. Send the owner every output you rely
  on.
- **Secrets** are typed by the owner into a hidden prompt or kept in the owner's password manager: never pasted into
  the chat, never printed. Never a key in a file on the owner's machine (no `.env` there): a run that needs one gets it
  in its environment (an Actions secret, the agents' keychain), or the session signs in to the service itself (Linear's
  MCP server).
- **Every issue an agent creates has its one Area label and a size when it is created**, in a session with the owner
  too: the size as agents/planner.md "The issue" defines it, and a priority. The owner's own work (`hands-off`) and a
  `Question:` issue have no diff and get no size.
- **What the owner types or reads is shown before the merge**: an issue that changes a config key, a command's output
  or a message the owner reads gives the exact lines in its "Done when", and its pull request pastes the real ones
  (agents/planner.md "The issue", agents/implementer.md step 4).
- **Links in a reply to the owner**: each pull request and each Linear key is a markdown link whose text stays the
  same, `[#3](https://github.com/maurolopes/owlcode/pull/3)` or `[OWL-3](https://linear.app/maurolopes/issue/OWL-3)`,
  also in lists, tables and a second mention; never a bare URL, number or key. Only a reply in a session: commit
  messages, pull request texts and Linear comments link by themselves.
- **What is learnt goes into markdown:** a rule here, a decision in DECISIONS.md. A session's own memory is no record:
  the agents run in their own macOS user (README.md "The agents' macOS user") and never see it.

## Autonomous runs

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
  lines (the workflow block above), never `--no-verify`.
- **The guards.** A pull request that changes a file `.github/CODEOWNERS` names (the workflows and the scripts under
  `.github/`, the hooks, each skill's `SKILL.md`, the reviewer's brief and its schema, this section of this file, mise.toml,
  pyproject.toml, `.python-version`, the lock files and `tests/conftest.py`) needs the owner's review (this file is not in CODEOWNERS: `.github/verdict.py`
  `section_owned` makes a change the owner's when it touches this section or a `## ` heading): such a pull
  request waits for the owner's review and merge, or the owner's label `owner-approved`, as for every Area. The
  implementer's app cannot push a workflow change at all, on purpose: a run that needs a file under `.github/workflows/`
  does the rest, writes that file's change as a patch in one comment on its pull request, moves its issue to Escalated,
  and then ends its turn; the owner's label `apply-workflow-patch` applies the patch, and the review that the owner's ready mark starts reaches a
  session through the orchestrator (agents/implementer.md step 6, the owner's call, 2026-11-16). The workflow's prose
  (`agents/README.md`, `implementer.md`, `planner.md`) and DECISIONS.md merge on the verdict; the review
  reads main's copies of its rules, never the branch's.
- **No production.** An autonomous run never deploys a product, never reaches a product's server, and never reads the
  owner's password manager or a production secret. A product's measuring key (`MEASURE_OPENROUTER_API_KEY`) is not a
  production secret: a run uses it only for a measurement that the product's own rules ask for, on invented data.
- **`hands-off` means the owner's.** No agent touches an issue with that label or its branch. One exception: the implementer
  adds one comment to an open `hands-off` issue of the same Area, with the live steps of its own pull request, and the
  link that relates the two issues, nothing else (agents/implementer.md "When you are done" step 5).

## Traps

- Run the gates through `mise run`, never bare `uv run pytest`: a mise shim takes its version from the folder it runs
  in.
- A gate passes by its exit code, never by its output's last line: no pipe after a gate in a command that goes on to
  commit (`uv run pytest -q | tail -1 && git commit` once committed a red run: the pipe's status is tail's); redirect to
  a file and test `$?`, or run the gate on its own and commit in the next command.
- Never `.` or `source` an env file in a shell: read it with a parser.
- A CI run is cancelled when a newer push lands: watch the newest run, and read every run since your last green one.
- The push hook checks the working folder, not the commit: a file left unstaged once passed there and failed on CI. So
  a push of HEAD stops while a tracked code file differs from HEAD, staged or not; an untracked file does not count.
  Read `git status --short` before every commit; stage by path only after it.
- A hook that moves or disappears stops the sessions that started before the pull: Claude Code keeps the hook command
  it read at its start, and `python3` exits 2 for a missing script, which Claude Code reads as a refusal (measured
  2026-11-23): every tool call the hook matches fails until the session restarts. Leave the old path as a file that
  calls the new one until the sessions that ran before the pull restart, or restart them after the pull.
- The python.org `python3` on the Mac, and uv's, have no root certificates: every https call from the standard library
  fails with `CERTIFICATE_VERIFY_FAILED`. Run "Install Certificates.command" in /Applications/Python 3.14 once, then run
  `.github/linear.py` and the other `.github/` scripts that need 3.11 (`verdict.py`, `review_watchdog.py`, and
  `flakes.py`, which loads `linear.py`) with `/usr/local/bin/python3 -I` (python.org 3.11 or newer).
  `/usr/bin/python3` is Python 3.9 on a Mac with only the Command Line Tools: `linear.py` needs 3.11 and fails at its
  first line there. `local_review.py` and `.workflow/agent_user.py` run on 3.9 and keep `/usr/bin/python3`.
